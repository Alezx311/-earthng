"""Retro City: original pixel drawings on the existing world's metre-scale UVs.

Draw on a 32 px / floor grid, then enlarge without interpolation. Full-sized DDS
maps keep runtime texture swaps compatible; normal mipmaps keep distant roads calm.
No game images are sampled or redistributed.
"""
import random
import zlib
from PIL import Image, ImageDraw

import fast_photo as photo
import fast_style as style

INK = (43, 53, 73)
SLATE = (72, 91, 115)
BLUE = (111, 145, 160)
CREAM = (220, 207, 171)
SAND = (181, 165, 138)
CLAY = (166, 99, 78)
SAGE = (114, 143, 111)
MOSS = (76, 107, 86)
AMBER = (221, 169, 94)
PALETTE = (INK, SLATE, BLUE, CREAM, SAND, CLAY, SAGE, MOSS, AMBER)
SURFACES = {
    'road': SLATE, 'road_major': INK, 'footway_asphalt': SLATE,
    'sidewalk': SAND, 'footway': SAND, 'paved': SAND, 'mark': CREAM,
    'curb': CREAM, 'ground': SAGE, 'park': SAGE, 'forest': MOSS,
    'water': BLUE, 'roof': SLATE, 'roof_tile': CLAY, 'roof_metal': MOSS,
    'parapet': SAND, 'trodden': SAND, 'fence_wall': SAND, 'fence_mesh': SLATE,
}


def rng_for(name):
    return random.Random(zlib.crc32(name.encode()))


def wall_colour(name):
    if 'red' in name or 'peach' in name or 'pink' in name or name == 'brick':
        return CLAY
    if 'blue' in name or name in ('glass', 'windows'):
        return BLUE
    if 'green' in name:
        return SAGE
    if 'grey' in name or 'dark' in name or name == 'block':
        return SAND
    return CREAM


def facade(name):
    """A complete repeat, including windowless ends and distinct shops/loggias."""
    family, _, metres = photo.PHOTO_WALLS.get(name, (name, None, 3.0))
    n = max(1, round(metres / 3))
    size = n * 32
    base = wall_colour(name)
    im = Image.new('RGB', (size, size), base)
    d, rng = ImageDraw.Draw(im), rng_for(name)
    brick = 'brick' in family or 'brk' in name
    panel = family in ('panel', 'precast', 'loggia', 'end_panel')
    if brick:
        for y in range(0, size, 4):
            d.line((0, y, size, y), fill=SAND)
            for x in range(-8 + (y // 4 % 2) * 4, size, 8):
                d.line((x, y, x, y + 3), fill=SAND)
    for row in range(n):
        for col in range(n):
            x, y = col * 32, row * 32
            if panel:
                d.line((x, y, x + 31, y), fill=SAND if base != SAND else SLATE)
                d.line((x, y, x, y + 31), fill=SAND if base != SAND else SLATE)
            if family.startswith('end_'):
                continue
            band = family in ('curtain', 'ribbon', 'glass', 'windows')
            shop = family == 'shop'
            small = family == 'block'
            left, right = (2, 29) if band or shop else (8, 23)
            top, bottom = (7, 17) if small else (7, 23)
            if shop:
                top, bottom = 12, 30
                d.rectangle((x, y + 4, x + 31, y + 9), fill=CLAY if base != CLAY else MOSS)
                for gx in range(5, 27, 5):
                    d.rectangle((x + gx, y + 6, x + gx + 2, y + 7), fill=CREAM)
            d.rectangle((x + left - 1, y + top - 1, x + right + 2, y + bottom + 2), fill=SLATE)
            d.rectangle((x + left, y + top, x + right, y + bottom), fill=INK)
            glass = BLUE if rng.random() > .18 else AMBER
            d.rectangle((x + left + 1, y + top + 1, x + right - 1, y + bottom - 1), fill=glass)
            d.rectangle((x + left + 1, y + bottom - 5, x + right - 1, y + bottom - 1), fill=SLATE)
            # Stepped reflection, deep blue recess, cream mullion and projecting sill.
            d.line((x + left + 2, y + top + 2, x + right - 3, y + top + 2), fill=CREAM)
            d.line((x + 16, y + top, x + 16, y + bottom), fill=SAND)
            d.line((x + left - 2, y + bottom + 1, x + right + 1, y + bottom + 1), fill=CREAM)
            if family == 'loggia':
                d.rectangle((x + 4, y + 24, x + 28, y + 28), fill=SAND)
                d.line((x + 4, y + 24, x + 28, y + 24), fill=CREAM)
    # Match the large loggia's existing streaming slot. Other maps are 1024.
    target = 2048 if n > 4 else 1024
    return im.transpose(Image.Transpose.FLIP_TOP_BOTTOM).resize((target, target), Image.Resampling.NEAREST)


def surface(kind):
    metres = style.KINDS[kind][3]
    size = max(16, round(metres * 8))
    base = SURFACES[kind]
    im = Image.new('RGB', (size, size), base)
    d, rng = ImageDraw.Draw(im), rng_for(kind)
    if kind in ('mark', 'curb', 'fence_mesh', 'parapet'):
        return im.resize((1024, 1024), Image.Resampling.NEAREST)
    for _ in range(size * size // 30):
        x, y = rng.randrange(size), rng.randrange(size)
        if kind in ('ground', 'park', 'forest'):
            c = MOSS if base == SAGE else SAGE
            d.line((x, y, x + 2, y), fill=c)
            d.point((x + 1, y - 1), fill=c)
        elif kind == 'water':
            d.line((x, y, x + 4, y), fill=SLATE)
        elif kind not in ('sidewalk', 'footway', 'paved', 'fence_wall'):
            d.point((x, y), fill=SLATE if base == INK else INK if base == SLATE else SAND)
    if kind in ('sidewalk', 'footway', 'paved', 'fence_wall', 'roof_tile', 'roof_metal'):
        for y in range(0, size, 8):
            d.line((0, y, size, y), fill=SLATE if base == SAND else SAND)
            for x in range(-8 + (y // 8 % 2) * 8, size, 16):
                d.line((x, y, x, y + 7), fill=SLATE if base == SAND else SAND)
                d.line((x + 1, y + 1, x + 14, y + 1), fill=CREAM if base == SAND else CLAY)
    return im.resize((1024, 1024), Image.Resampling.NEAREST)


def build(root):
    import fast
    out = root / 'pixel'
    out.mkdir(parents=True, exist_ok=True)
    for kind in style.KINDS:
        if kind.startswith('wall_'):
            facade(kind[5:]).save(out / f'{kind}.color.png')
        elif kind in SURFACES:
            surface(kind).save(out / f'{kind}.color.png')
    # The decorative cars share an atlas: preserve its cells and glass/tyre indices.
    cars = Image.new('RGB', (32 * fast.CAR_ATLAS, 32), INK)
    d = ImageDraw.Draw(cars)
    for k in range(len(fast.CAR_COLOURS)):
        d.rectangle((32 * k, 0, 32 * k + 31, 31), fill=PALETTE[2 + k % 7])
    k = len(fast.CAR_COLOURS)
    d.rectangle((32 * k, 0, 32 * k + 31, 31), fill=SLATE)
    cars.save(out / 'car.color.png')


def fields(base):
    import fast_packs
    out = {}
    for kind in style.KINDS:
        f = {key: '' for key in ('baseColorMap', 'normalMap', 'roughnessMap', 'ambientOcclusionMap', 'emissiveMap', 'opacityMap')}
        f.update(baseColorMap=f'{base}/pixel/{kind}.color.png', baseColorFactor='1 1 1 1',
                 emissiveFactor='0 0 0', roughnessFactor=.9, metallicFactor=0)
        if kind == 'fence_mesh':
            f['opacityMap'] = f'{base}/real/chainlink_o.data.png'
        out['e2rf_' + kind] = f
    for mat in fast_packs.LEAF_NAMES:
        out[mat] = {'baseColorFactor': '0.68 0.82 0.64 1', 'roughnessFactor': .95}
    return style.dds(out)


def environment(real):
    import copy
    env = copy.deepcopy(real)
    env.update(sun=False, lights=False, precip=False, authoredSun=True)
    env['objects']['sunsky'].update(elevation='32', azimuth='225', brightness='0.65', skyBrightness='18',
        sunScale='1 0.86 0.68 1', ambientScale='0.48 0.58 0.72 1',
        colorize='0.58 0.72 0.82 1', colorizeAmount='0.35', fogScale='0.65 0.72 0.78 1')
    env['objects']['clouds'].update(coverage='0.25', exposure='0.4')
    env['objects']['theLevelInfo'].update(fogColor='0.55 0.65 0.72 1', fogDensity='0.00022')
    return env
