import numpy as np
from skimage.transform import resize
from skimage import io as img_io
from skimage.color import rgb2gray
import torch
import torch.nn.functional as F


def grd_mgn(img):

    sobel = torch.tensor([[1, 2, 1], [0, 0, 0], [-1, -2, -1]]).float().to(img.device)
    gy = F.conv2d(img, sobel.view(1, 1, 3, 3), padding=1)
    gx = F.conv2d(img, sobel.t().view(1, 1, 3, 3), padding=1)
    mgn = torch.sqrt(gx ** 2 + gy ** 2)

    return mgn


# morphological operations suppurted by cuda
def torch_morphological(img, kernel_size, mode='dilation'):

    # img : batches x 1 x h x w
    if mode == 'dilation':
        img = F.max_pool2d(img, kernel_size=kernel_size, stride=1, padding=kernel_size/2)
    if mode == 'erosion':
        img = -F.max_pool2d(-img, kernel_size=kernel_size, stride=1, padding=kernel_size/2)
    if mode == 'closing':
        img = F.max_pool2d(img, kernel_size=kernel_size, stride=1, padding=kernel_size/2)
        img = -F.max_pool2d(-img, kernel_size=kernel_size, stride=1, padding=kernel_size/2)
    if mode == 'opening':
        img = -F.max_pool2d(-img, kernel_size=kernel_size, stride=1, padding=kernel_size/2)
        img = F.max_pool2d(img, kernel_size=kernel_size, stride=1, padding=kernel_size/2)

    return img

def pairwise_radial_basis(x, c, eps=.00001):

    dd2 = (x.view(-1, 1, 2) - c.view(1, -1, 2)) ** 2
    D = dd2[:, :, 0] + dd2[:, :, 1]
    #D = F.pairwise_distance(x, c)
    # > 1
    #Phi = (D ** 2) * (D+eps).log()
    # < 1
    #Phi = D * (D ** D + eps).log()

    Phi = .5 * D * (D + eps).log()

    return Phi


def tps_parameters(X, Y, l=0.01):

    # X : control points, n x 2
    # Y : target points, n x 2

    dev = X.device
    n = X.size(0)
    d = X.size(1)

    # A: n x n
    A = pairwise_radial_basis(X, X) + l * torch.eye(n, n).to(dev)


    # Xa : n x 3

    aux_ones = torch.ones(n, 1).to(dev)
    Xa = torch.cat([X, aux_ones], 1)

    # M = [A Xa ; Xa^T 0] : (n+3) x (n+3)
    M = torch.cat([torch.cat([A, Xa], 1), torch.cat([Xa.t(), torch.zeros(d+1, d+1).to(dev)],1)], 0)


    # Ya : (n+3) x 3
    aux_zeros = torch.zeros(d + 1, d + 1).to(dev)
    Ya = torch.cat([torch.cat([Y, aux_ones], 1), aux_zeros], 0)

    # w : (n+3) x 3
    w = torch.mm(torch.inverse(M + .1 * torch.eye(n+d+1, n+d+1).to(dev)), Ya)

    #M_LU = torch.btrifact(M.unsqueeze(0))
    #w = torch.btrisolve(Ya.unsqueeze(0), *M_LU)[0]

    return w


def tps_deform(grid, cpoints, tps_w):

    # A : m x n
    A = pairwise_radial_basis(grid, cpoints)

    # Xa : m x 3
    aux_ones = torch.ones(grid.size(0), 1).to(grid.device)
    Xa = torch.cat([grid, aux_ones], 1)

    ngrid = torch.mm(torch.cat([A, Xa], 1), tps_w)[:, :-1]

    return ngrid

def affine(img):

    h, w = img.size(2), img.size(3)
    g = torch.stack([
        torch.linspace(-w/2, w/2, w).view(1, -1).repeat(h, 1),
        torch.linspace(-h/2, h/2, h).view(-1, 1).repeat(1, w),
    ], 2)

    scale = 1 #np.random.uniform(.9, 1.1)
    x_prop = 1 #np.random.uniform(.9, 1.1)
    rotate = np.deg2rad(2 * np.random.randn())
    slant = np.random.randn()

    # ng = ng + .05 * torch.randn(ng.size())
    ng = scale * g
    ng[:, :, 0] = x_prop * ng[:, :, 0]
    ng[:, :, 0] = ng[:, :, 0] + slant * 40 * ng[:, :, 1] / h

    R = torch.from_numpy(
        np.asarray([np.cos(rotate), -np.sin(rotate), np.sin(rotate), np.cos(rotate)])).float().view(2, 2)
    ng = torch.mm(ng.view(-1, 2), R).view_as(g)
    ng[:, :, 0] = 2 * ng[:, :, 0] / w
    ng[:, :, 1] = 2 * ng[:, :, 1] / h

    nimg = F.grid_sample(img, ng.unsqueeze(0).to(img.device), padding_mode='border')

    return nimg


def torch_augm(img):

    # kernel radius
    r = np.random.randint(0, 2)
    if r > 0:
        mode = np.random.choice(['dilation', 'erosion', 'opening', 'closing'])
        img = torch_morphological(img, 2*r+1, mode)

    img = affine(img)

    return img.detach()


def image_resize(img, height=None, width=None):

    if height is not None and width is None:
        scale = float(height) / float(img.shape[0])
        width = int(scale*img.shape[1])

    if width is not None and height is None:
        scale = float(width) / float(img.shape[1])
        height = int(scale*img.shape[0])

    img = resize(image=img, output_shape=(height, width)).astype(np.float32)

    return img


def centered(word_img, tsize):

    height = tsize[0]
    width = tsize[1]

    xs, ys, xe, ye = 0, 0, width, height
    diff_h = height-word_img.shape[0]
    if diff_h >= 0:
        pv = diff_h/2
        padh = (pv, diff_h-pv)
    else:
        diff_h = abs(diff_h)
        ys, ye = diff_h/2, word_img.shape[0] - (diff_h - diff_h/2)
        padh = (0, 0)
    diff_w = width - word_img.shape[1]
    if diff_w >= 0:
        pv = diff_w / 2
        padw = (pv, diff_w - pv)
    else:
        diff_w = abs(diff_w)
        xs, xe = diff_w / 2, word_img.shape[1] - (diff_w - diff_w / 2)
        padw = (0, 0)

    mv = np.median(word_img)
    word_img = np.pad(word_img[ys:ye, xs:xe], (padh, padw), 'constant', constant_values=mv)
    return word_img