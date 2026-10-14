"""BC1 (DXT1) DDS with a full mip chain, in NumPy.

BeamNG loads a material's '<name>.color.png' from '<name>.color.dds' when that is there; a PNG
it first cooks into a DDS under /temp (0.2-0.5 s a texture, the material showing a checker
placeholder meanwhile: vis-08-centre). Writing the DDS ourselves skips both. The engine checks
the chain against the size ("dds mip chain (11) does not match texture (9)"), so every level
down to 1 x 1 is written.

The encoder is a plain range fit: per 4 x 4 block the endpoints are the colours of least and
most luminance (pulled in 1/16 toward each other), every texel takes the nearest of the four.
"""
import struct

import numpy as np
from PIL import Image


def _565(c):
    c = np.clip(np.rint(c), 0, 255).astype(np.uint32)
    return ((c[..., 0] >> 3) << 11) | ((c[..., 1] >> 2) << 5) | (c[..., 2] >> 3)


def _unpack(v):
    r = ((v >> 11) & 31) * 255 / 31
    g = ((v >> 5) & 63) * 255 / 63
    b = (v & 31) * 255 / 31
    return np.stack([r, g, b], -1).astype(np.float32)


def bc1(rgb):
    """BC1 blocks of an H x W x 3 uint8 array (H, W multiples of 4) -> bytes."""
    h, w, _ = rgb.shape
    blocks = rgb.reshape(h // 4, 4, w // 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 3).astype(np.float32)
    lum = blocks @ np.array([.299, .587, .114], dtype=np.float32)
    lo = blocks[np.arange(len(blocks)), lum.argmin(1)]
    hi = blocks[np.arange(len(blocks)), lum.argmax(1)]
    lo, hi = lo + (hi - lo) / 16, hi - (hi - lo) / 16
    c0, c1 = _565(hi), _565(lo)
    # four-colour mode needs c0 > c1; equal endpoints: all texels index 0
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    p0, p1 = _unpack(c0), _unpack(c1)
    palette = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], 1)          # n x 4 x 3
    d = ((blocks[:, :, None, :] - palette[:, None, :, :]) ** 2).sum(-1)             # n x 16 x 4
    idx = d.argmin(-1).astype(np.uint32)
    idx[c0 == c1] = 0
    bits = (idx << (2 * np.arange(16, dtype=np.uint32))).sum(1).astype(np.uint32)
    out = np.empty((len(blocks), 2), dtype=np.uint32)
    out[:, 0] = c0 | (c1 << 16)
    out[:, 1] = bits
    return out.astype('<u4').tobytes()


BC1_UNORM, BC1_UNORM_SRGB = 71, 72


def keep_coverage(a, ref, coverage):
    """A mip of an alpha-tested map scaled so the share of texels >= ref stays the level-0 one.
    A plain box filter thins thin wire to nothing: the game's chain link (16 % of texels over
    alphaRef 80) had none left from the 64 px level, so fences vanished ~30 m away."""
    v = a[..., 0].astype(np.float32)
    if coverage <= 0 or v.max() <= 0:
        return a
    t = np.quantile(v, 1 - coverage)
    if t <= 0:
        return a
    return np.clip(v * (ref / t), 0, 255).astype(np.uint8)[..., None].repeat(3, -1)


def save(image, path, srgb=None, alpha_ref=None):
    """Write image (PIL, RGB) as a BC1 DDS with mips; sides must be powers of two. A DX10 header
    carries the format as the game's own textures do: colour maps ('.color.' in the name, or
    srgb=True) are sRGB; read as linear, a plain DXT1 lawn came out pale (vis-10-akadem).
    alpha_ref: an opacity map (grey) for that alpha test; every mip keeps the coverage."""
    if srgb is None:
        srgb = '.color.' in str(path) or '.glow.' in str(path)
    im = image.convert('RGB')
    w, h = im.size
    levels = []
    coverage = None
    while True:
        a = np.asarray(im, dtype=np.uint8)
        if alpha_ref is not None:
            if coverage is None:
                coverage = float((a[..., 0] >= alpha_ref).mean())
            else:
                a = keep_coverage(a, alpha_ref, coverage)
        if a.shape[0] < 4 or a.shape[1] < 4:
            # BC1 stores at least one 4 x 4 block: pad the smallest levels
            pad = np.zeros((max(4, a.shape[0]), max(4, a.shape[1]), 3), np.uint8)
            pad[:a.shape[0], :a.shape[1]] = a
            pad[a.shape[0]:, :] = pad[a.shape[0] - 1:a.shape[0], :] if a.shape[0] else 0
            pad[:, a.shape[1]:] = pad[:, a.shape[1] - 1:a.shape[1]] if a.shape[1] else 0
            a = pad
        levels.append(bc1(a))
        if im.size == (1, 1):
            break
        im = im.resize((max(1, im.size[0] // 2), max(1, im.size[1] // 2)), Image.BOX)
    linear = max(1, w // 4) * max(1, h // 4) * 8
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000     # caps height width pixelformat mipmapcount linearsize
    pf = struct.pack('<II4sIIIII', 32, 0x4, b'DX10', 0, 0, 0, 0, 0)
    caps = 0x1000 | 0x8 | 0x400000                            # texture, complex, mipmap
    header = struct.pack('<4sIIIIIII44s', b'DDS ', 124, flags, h, w, linear, 0, len(levels), b'\0' * 44) + pf + \
        struct.pack('<IIIII', caps, 0, 0, 0, 0)
    # DX10 extension: format, 2D texture, no flags, one array slice
    header += struct.pack('<IIIII', BC1_UNORM_SRGB if srgb else BC1_UNORM, 3, 0, 1, 0)
    with open(path, 'wb') as f:
        f.write(header)
        for level in levels:
            f.write(level)
