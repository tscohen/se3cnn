# pylint: disable=C,R,E1101
'''
Minimalist example of usage of SE(3) CNN
'''
import torch
import numpy as np

from se3_cnn.blocks.highway import HighwayBlock


class CNN(torch.nn.Module):

    def __init__(self):
        super(CNN, self).__init__()

        features = [
            (1, 0, 0),
            (10, 10, 2),
            (2, 0, 0)
        ]
        block_params = [
            {'stride': 2, 'non_linearities': True},
            {'stride': 2, 'non_linearities': False},
        ]

        assert len(block_params) + 1 == len(features)

        blocks = [HighwayBlock(features[i], features[i + 1], **block_params[i]) for i in range(len(block_params))]
        self.blocks = torch.nn.Sequential(*blocks)

    def forward(self, inp):  # pylint: disable=W
        '''
        :param inp: [batch, features, x, y, z]
        '''
        x = self.blocks(inp)  # [batch, features, x, y, z]
        x = x.view(x.size(0), x.size(1), -1)  # [batch, features, x*y*z]
        x = x.mean(-1)  # [batch, features]

        return x


def main():
    cnn = CNN()

    if torch.cuda.is_available():
        cnn.cuda()

    criterion = torch.nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(cnn.parameters(), lr=1e-2)

    batch_size = 64
    sample_size = 24

    mesh = np.linspace(-1, 1, sample_size)
    mx, my, mz = np.meshgrid(mesh, mesh, mesh)

    def step(i):
        x = 0.1 * np.random.randn(batch_size, 1, sample_size, sample_size, sample_size)
        y = np.random.randint(0, 2, size=(batch_size,))

        for j, label in enumerate(y):
            radius = 0.6 + np.random.rand() * (0.9 - 0.6)

            if label == 0:
                # ball
                mask = mx ** 2 + my ** 2 + mz ** 2 < radius ** 2
            if label == 1:
                # cube
                mask = abs(mx) + abs(my) + abs(mz) < radius

            x[j, 0, mask] += np.random.randint(2) * 2 - 1

        x = torch.FloatTensor(x)
        y = torch.LongTensor(y)

        if torch.cuda.is_available():
            x = x.cuda()
            y = y.cuda()

        x = torch.autograd.Variable(x)
        y = torch.autograd.Variable(y)

        optimizer.zero_grad()
        out = cnn(x)
        loss = criterion(out, y)
        loss.backward()
        optimizer.step()

        loss = loss.data.cpu().numpy()
        out = out.data.cpu().numpy()
        y = y.data.cpu().numpy()

        acc = np.sum(out.argmax(-1) == y) / batch_size

        print("{}: acc={}% loss={}".format(i, 100 * acc, float(loss)))

    for i in range(1000):
        step(i)


if __name__ == '__main__':
    main()
