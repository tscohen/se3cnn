# pylint: disable=C,R,E1101,E1102
'''
Given two representation of SO(3), computes the basis elements of
the vector space of kernels K such that
    integral dy K(x, y) f(y)
is equivariant.

K must satifies
    K(ux, uy) = R_out(u) K(x, y) R_in(u^{-1}) for all u in SE(3)

Therefore
    K(x, y) = K(0, y-x)

    K(0, x) = K(0, g |x| e)  where e is a prefered chosen unit vector and g is in SO(3)
'''
import torch
from se3cnn.SO3 import x_to_alpha_beta, irr_repr, spherical_harmonics, kron, torch_default_dtype
from se3cnn.util.cache_file import cached_dirpklgz
import math


################################################################################
# Solving the constraint coming from the stabilizer of 0 and e
################################################################################

def get_matrix_kernel(A, eps=1e-10):
    '''
    Compute an orthonormal basis of the kernel (x_1, x_2, ...)
    A x_i = 0
    scalar_product(x_i, x_j) = delta_ij

    :param A: matrix
    :return: matrix where each row is a basis vector of the kernel of A
    '''
    _u, s, v = torch.svd(A)

    # A = u @ torch.diag(s) @ v.t()
    kernel = v.t()[s < eps]
    return kernel


def get_matrices_kernel(As, eps=1e-10):
    '''
    Computes the commun kernel of all the As matrices
    '''
    return get_matrix_kernel(torch.cat(As, dim=0), eps)


################################################################################
# Analytically derived basis
################################################################################

@cached_dirpklgz("cache/trans_Q")
def _basis_transformation_Q_J(J, order_in, order_out, version=3):  # pylint: disable=W0613
    """
    :param J: order of the spherical harmonics
    :param order_in: order of the input representation
    :param order_out: order of the output representation
    :return: one part of the Q^-1 matrix of the article
    """
    with torch_default_dtype(torch.float64):
        def _R_tensor(a, b, c): return kron(irr_repr(order_out, a, b, c), irr_repr(order_in, a, b, c))

        def _sylvester_submatrix(J, a, b, c):
            ''' generate Kronecker product matrix for solving the Sylvester equation in subspace J '''
            R_tensor = _R_tensor(a, b, c)  # [m_out * m_in, m_out * m_in]
            R_irrep_J = irr_repr(J, a, b, c)  # [m, m]
            return kron(R_tensor, torch.eye(R_irrep_J.size(0))) - \
                kron(torch.eye(R_tensor.size(0)), R_irrep_J.t())  # [(m_out * m_in) * m, (m_out * m_in) * m]

        random_angles = [
            [4.41301023, 5.56684102, 4.59384642],
            [4.93325116, 6.12697327, 4.14574096],
            [0.53878964, 4.09050444, 5.36539036],
            [2.16017393, 3.48835314, 5.55174441],
            [2.52385107, 0.2908958, 3.90040975]
        ]
        null_space = get_matrices_kernel([_sylvester_submatrix(J, a, b, c) for a, b, c in random_angles])
        assert null_space.size(0) == 1, null_space.size()  # unique subspace solution
        Q_J = null_space[0]  # [(m_out * m_in) * m]
        Q_J = Q_J.view((2 * order_out + 1) * (2 * order_in + 1), 2 * J + 1)  # [m_out * m_in, m]
        assert all(torch.allclose(_R_tensor(a, b, c) @ Q_J, Q_J @ irr_repr(J, a, b, c)) for a, b, c in torch.rand(4, 3))

    assert Q_J.dtype == torch.float64
    return Q_J  # [m_out * m_in, m]


@cached_dirpklgz("cache/sh_cube")
def _sample_sh_cube(size, J, version=3):  # pylint: disable=W0613
    '''
    Sample spherical harmonics in a cube.
    No bandlimiting considered, aliased regions need to be cut by windowing!
    :param size: side length of the kernel
    :param J: order of the spherical harmonics
    '''
    with torch_default_dtype(torch.float64):
        rng = torch.linspace(-((size - 1) / 2), (size - 1) / 2, steps=size)

        Y_J = torch.zeros(2 * J + 1, size, size, size, dtype=torch.float64)
        for idx_x, x in enumerate(rng):
            for idx_y, y in enumerate(rng):
                for idx_z, z in enumerate(rng):
                    if x == y == z == 0:  # angles at origin are nan, special treatment
                        if J == 0:  # Y^0 is angularly independent, choose any angle
                            Y_J[:, idx_x, idx_y, idx_z] = spherical_harmonics(0, 123, 321)  # [m]
                        else:  # insert zeros for Y^J with J!=0
                            Y_J[:, idx_x, idx_y, idx_z] = 0
                    else:  # not at the origin, sample spherical harmonic
                        alpha, beta = x_to_alpha_beta([x, y, z])
                        Y_J[:, idx_x, idx_y, idx_z] = spherical_harmonics(J, alpha, beta)  # [m]

    assert Y_J.dtype == torch.float64
    return Y_J  # [m, x, y, z]


def _sample_cube(size, order_in, order_out):
    '''
    :param size: side length of the kernel
    :param order_in: order of the input representation
    :param order_out: order of the output representation
    :return: sampled equivariant kernel basis of shape (N_basis, 2*order_out+1, 2*order_in+1, size, size, size)
    '''

    rng = torch.linspace(-((size - 1) / 2), (size - 1) / 2, steps=size, dtype=torch.float64)

    order_irreps = list(range(abs(order_in - order_out), order_in + order_out + 1))
    sh_cubes = []
    for J in order_irreps:
        Y_J = _sample_sh_cube(size, J)  # [m, x, y, z]

        # compute basis transformation matrix Q_J
        Q_J = _basis_transformation_Q_J(J, order_in, order_out)  # [m_out * m_in, m]
        K_J = torch.einsum('mn,nxyz->mxyz', (Q_J, Y_J))  # [m_out * m_in, x, y, z]
        K_J = K_J.reshape(2 * order_out + 1, 2 * order_in + 1, size, size, size)  # [m_out, m_in, x, y, z]
        sh_cubes.append(K_J)

        # check that  rho_out(u) K(u^-1 x) rho_in(u^-1) = K(x) with u = rotation of +pi/2 around y axis
        inv_idx = torch.arange(size - 1, -1, -1, device=K_J.device).long()
        tmp = K_J.transpose(2, 4)[:, :, :, :, inv_idx]  # K(u^-1 x)
        tmp = torch.einsum(
            "ij,jkxyz,kl->ilxyz",
            (
                irr_repr(order_out, 0, math.pi / 2, 0, dtype=K_J.dtype),
                tmp,
                irr_repr(order_in, 0, -math.pi / 2, 0, dtype=K_J.dtype)
            )
        )  # rho_out(u) K(u^-1 x) rho_in(u^-1)
        assert torch.allclose(tmp, K_J)

    r_field = (rng.view(-1, 1, 1).pow(2) + rng.view(1, -1, 1).pow(2) + rng.view(1, 1, -1).pow(2)).sqrt()  # [x, y, z]
    return sh_cubes, r_field, order_irreps


def cube_basis_kernels(size, order_in, order_out, radial_window):
    '''
    Generate equivariant kernel basis mapping between capsules transforming under order_in and order_out
    :param size: side length of the filter kernel
    :param order_in: input representation order
    :param order_out: output representation order
    :param radial_window: callable for windowing out radial parts, taking mandatory parameters 'sh_cubes', 'r_field' and 'order_irreps'
    :return: basis of equivariant kernels of shape (N_basis, 2 * order_out + 1, 2 * order_in + 1, size, size, size)
    '''
    basis = radial_window(*_sample_cube(size, order_in, order_out))
    if basis is not None:
        # normalize filter energy (not over axis 0, i.e. different filters are normalized independently)
        basis = basis / basis.view(basis.size(0), -1).norm(dim=1).view(-1, 1, 1, 1, 1, 1)
    return basis


################################################################################
# Radial distribution functions
################################################################################

def gaussian_window_fct(sh_cubes, r_field, order_irreps, radii, J_max_list, sigma=.6):
    '''
    gaussian windowing function with manual handling of shell radii, shell bandlimits and shell width
    :param sh_cubes: list of spherical harmonic basis cubes which are np.ndarrays of shape (2*l+1, 2*j+1, size, size, size)
    :param r_field: np.ndarray containing radial coordinates in the cube, shape (size,size,size)
    :param order_irreps: np.ndarray with the order J of the irreps in sh_cubes with |j-l|<=J<=j+l
    :param radii: np.ndarray with radii of the shells, sets mean of the radial gaussians
    :param J_max_list: np.ndarray with bandlimits of the shells, same length as radii
    :param sigma: width of the shells, corresponds to standard deviation of radial gaussians
    '''
    # run over radial parts and window out non-aliased basis functions
    assert len(radii) == len(J_max_list)

    basis = []
    for r, J_max in zip(radii, J_max_list):
        window = torch.exp(-.5 * ((r_field - r) / sigma)**2) / (math.sqrt(2 * math.pi) * sigma)
        window = window.unsqueeze(0).unsqueeze(0)
        # for each spherical shell at radius r window sh_cube if J does not exceed the bandlimit J_max
        for idx_J, J in enumerate(order_irreps):
            if J > J_max:
                break
            else:
                basis.append(sh_cubes[idx_J] * window)
    if len(basis) > 0:
        basis = torch.stack(basis, dim=0)
    else:
        basis = None
    return basis


def gaussian_window_fct_convenience_wrapper(sh_cubes, r_field, order_irreps, mode='compromise', border_dist=0., sigma=.6):
    '''
    convenience wrapper for windowing function with three different predefined modes for radii and bandlimits
    :param sh_cubes: list of spherical harmonic basis cubes which are np.ndarrays of shape (2*l+1, 2*j+1, size, size, size)
    :param r_field: np.ndarray containing radial coordinates in the cube, shape (size,size,size)
    :param order_irreps: np.ndarray with the order J of the irreps in sh_cubes with |j-l|<=J<=j+l
    :param mode: string in ['sfcnn', 'conservative', '']
                 'conservative': radial bandlimits such that equivariance value stays over 90% (for border_dist=0 and except for outer shell)
                          [0,2,4,6,8,10,...]
                 'compromise' something inbetween
                          [0,3,5,7,9,11,...]
                 'sfcnn': same radial bandlimits as used in https://arxiv.org/abs/1711.07289
                          [0,4,6,8,10,12,...]
    :param border_dist: distance of mean of outermost shell from outermost pixel center
    :param sigma: width of the shell
    '''
    assert mode in ['conservative', 'compromise', 'sfcnn']
    size = r_field.size(0)

    n_radial = size // 2 + 1
    radii = torch.linspace(0, size // 2 - border_dist, steps=n_radial, dtype=torch.float64)

    if mode == 'conservative':
        J_max_list = [0, 2, 4, 6, 8, 10, 12, 14][:n_radial]
    if mode == 'compromise':
        J_max_list = [0, 3, 5, 7, 9, 11, 13, 15][:n_radial]
    if mode == 'sfcnn':
        J_max_list = [0, 4, 6, 8, 10, 12, 14, 16][:n_radial]

    return gaussian_window_fct(sh_cubes, r_field, order_irreps, radii, J_max_list, sigma)


################################################################################
# Measure equivariance
################################################################################

def check_basis_equivariance(basis, order_in, order_out, alpha, beta, gamma):
    from se3cnn import SO3
    from scipy.ndimage import affine_transform
    import numpy as np

    n = basis.size(0)
    dim_in = 2 * order_in + 1
    dim_out = 2 * order_out + 1
    size = basis.size(-1)
    assert basis.size() == (n, dim_out, dim_in, size, size, size), basis.size()

    basis = basis / basis.view(n, -1).norm(dim=1).view(-1, 1, 1, 1, 1, 1)

    x = basis.view(-1, size, size, size)
    y = torch.empty_like(x)

    invrot = SO3.rot(-gamma, -beta, -alpha).numpy()
    center = (np.array(x.size()[1:]) - 1) / 2

    for k in range(y.size(0)):
        y[k] = torch.tensor(affine_transform(x[k].numpy(), matrix=invrot, offset=center - np.dot(invrot, center)))

    y = y.view(*basis.size())

    y = torch.einsum(
        "ij,bjkxyz,kl->bilxyz",
        (
            irr_repr(order_out, alpha, beta, gamma, dtype=y.dtype),
            y,
            irr_repr(order_in, -gamma, -beta, -alpha, dtype=y.dtype)
        )
    )

    return torch.tensor([(basis[i] * y[i]).sum() for i in range(n)])


################################################################################
# Testing
################################################################################

def _test_basis_equivariance():
    from functools import partial
    with torch_default_dtype(torch.float64):
        basis = cube_basis_kernels(4 * 5, 2, 2, partial(gaussian_window_fct, radii=[5], J_max_list=[999], sigma=2))
        overlaps = check_basis_equivariance(basis, 2, 2, *torch.rand(3))
        assert overlaps.gt(0.98).all(), overlaps


if __name__ == '__main__':
    _test_basis_equivariance()
