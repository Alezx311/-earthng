"""Facade textures and texture packs for the fast generator.

A facade texture is one window cell, 3 m wide and one 3 m floor high; walls carry their own
UVs (u along the building outline, v up from its base), so the windows sit on floors. The
'real' pack draws the windows over the game's own wall textures (read from its asset zips,
nothing shipped); 'toon' and 'model' are flat stylisations. Written once per user folder:

    <user>/e2r_stream/fast-art/<VERSION>/<pack>/<style>.color.png
    <user>/e2r_stream/fast-art/<VERSION>/packs.json     material fields per pack (in-game switch)
"""
import io
import json
import os
from pathlib import Path
import zipfile

from PIL import Image, ImageDraw

import fast_packs
import fast_photo as photo
import fast_style as style

VERSION = 'v8'
CELL = 3.0          # m per texture repeat on walls: one window, one floor
PX = 1024           # texture size: as the game's own (a 256 px swap kept its 1024 mip chain and failed)


def game_dir():
    """The BeamNG.drive install: BEAMNG_DIR, else the Steam libraries (registry and
    libraryfolders.vdf), else the default Steam folder. None when not found."""
    import os, re
    candidates = []
    if os.environ.get('BEAMNG_DIR'):
        candidates.append(Path(os.environ['BEAMNG_DIR']))
    steam = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as k:
            steam.append(Path(winreg.QueryValueEx(k, 'SteamPath')[0]))
    except OSError:
        pass
    steam.append(Path(r'C:/Program Files (x86)/Steam'))
    for root in steam:
        vdf = root / 'steamapps' / 'libraryfolders.vdf'
        libs = [root]
        if vdf.exists():
            libs += [Path(m.replace('\\', '/')) for m in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(errors='ignore'))]
        candidates += [lib / 'steamapps' / 'common' / 'BeamNG.drive' for lib in libs]
    for c in candidates:
        if (c / 'content' / 'assets' / 'materials').exists():
            return c
    return None


GAME = game_dir()
# the game's texture zips; without them the 'real' facades fall back to flat colours
ASSETS = (GAME / 'content' / 'assets' / 'materials') if GAME else Path('missing')

# wall style: (flat colour for toon, window kind) -- 'band' = office strip glazing
FACADES = {
    'panel':       ((222, 214, 196), 'panel'),
    'precast':     ((205, 200, 190), 'panel'),
    'brick':       ((176, 112, 86), 'window'),
    'brick_red':   ((160, 82, 64), 'window'),
    'brick_white': ((236, 232, 222), 'window'),
    'plaster':     ((238, 214, 170), 'window'),
    'stucco':      ((244, 240, 228), 'window'),
    'glass':       ((120, 160, 190), 'band'),
    'windows':     ((150, 170, 180), 'band'),
    'block':       ((170, 170, 165), 'small'),
}
ROOF = {'toon': (120, 96, 92), 'model': (210, 210, 210)}
FLAT = {   # kind: (toon colour, model colour)
    'road': ((92, 96, 110), (120, 120, 124)), 'road_major': ((80, 84, 98), (110, 110, 114)),
    'sidewalk': ((214, 204, 190), (236, 236, 236)), 'mark': ((250, 250, 240), (255, 255, 255)),
    'ground': ((126, 196, 112), (226, 228, 222)), 'forest': ((84, 150, 90), (200, 204, 198)),
    'paved': ((190, 184, 176), (220, 220, 220)), 'water': ((96, 176, 220), (180, 200, 214)),
    'roof': ((120, 96, 92), (210, 210, 210)), 'curb': ((240, 240, 240), (250, 250, 250)),
    'park': ((126, 196, 112), (226, 228, 222)), 'roof_tile': ((176, 84, 62), (200, 200, 200)),
    'roof_metal': ((96, 120, 116), (205, 205, 205)), 'parapet': ((200, 196, 190), (236, 236, 236)),
}
BASE_PACKS = ('real', 'toon', 'model', 'synth')
PACKS = BASE_PACKS + fast_packs.PACKS
LABELS = {'real': 'Реальний', 'toon': 'Мультяшний', 'model': 'Макет', 'synth': 'Синтвейв', **fast_packs.LABELS}
MAP_FIELDS = ('baseColorMap', 'normalMap', 'roughnessMap', 'ambientOcclusionMap', 'emissiveMap', 'opacityMap')
# synthwave: dark violet surfaces, neon lines glowing (emissive), a striped sun at the horizon
SYNTH = {
    'road': (22, 16, 44), 'road_major': (26, 18, 52), 'sidewalk': (44, 30, 78), 'paved': (30, 20, 60),
    'water': (14, 10, 46), 'roof': (34, 22, 62), 'ground': (20, 8, 40), 'forest': (16, 6, 34),
    'roof_tile': (40, 20, 60), 'roof_metal': (30, 22, 60), 'parapet': (36, 22, 64),
}
SYNTH_GLOW = {'mark': (0.0, 1.0, 1.0), 'curb': (1.0, 0.1, 0.8)}   # emissive lines on the roads
NEON = (255, 40, 200)       # grid magenta
CYAN = (0, 240, 255)


def root(user):
    return Path(user) / 'e2r_stream' / 'fast-art' / VERSION


def game_texture(path):
    """A /assets/... .png map of the game, decoded from its .dds in the asset zips."""
    rel = path.lstrip('/')[:-4] + '.dds'
    for zip_name in ('tileable.zip', 'terrain.zip'):
        z = ASSETS / zip_name
        if z.exists():
            with zipfile.ZipFile(z) as zf:
                if rel in zf.namelist():
                    return Image.open(io.BytesIO(zf.read(rel))).convert('RGB')
    return None


def draw_windows(im, kind, pack):
    """Windows in world orientation (floor at the bottom of the picture)."""
    d = ImageDraw.Draw(im)
    s = PX / CELL
    glass = {'real': (52, 66, 82), 'toon': (150, 205, 235), 'model': (190, 190, 192), 'synth': (40, 10, 70)}[pack]
    frame = {'real': (225, 225, 220), 'toon': (60, 60, 70), 'model': (245, 245, 245), 'synth': CYAN}[pack]
    if kind == 'band':
        d.rectangle([0, s * .55, PX, s * 2.75], fill=glass)
        for x in range(0, PX, int(s * 1.5)):
            d.rectangle([x, s * .55, x + 2, s * 2.75], fill=frame)
        d.rectangle([0, s * .5, PX, s * .6], fill=frame)
        return
    if kind == 'small':
        x0, x1, y0, y1 = .9, 2.1, .6, 1.4
    else:
        x0, x1, y0, y1 = .8, 2.2, .6, 2.1         # 1.4 x 1.5 m window, sill 0.9 m up
    box = [s * x0, PX - s * y1, s * x1, PX - s * y0]
    d.rectangle([box[0] - 4, box[1] - 4, box[2] + 4, box[3] + 4], fill=frame)
    d.rectangle(box, fill=glass)
    mid = (box[0] + box[2]) / 2
    d.rectangle([mid - 2, box[1], mid + 2, box[3]], fill=frame)          # mullion
    d.rectangle([box[0] - 8, box[3] + 4, box[2] + 8, box[3] + 10], fill=frame)   # sill
    if kind == 'panel':                          # precast panel joints at the cell edges
        line = tuple(max(0, c - 40) for c in im.getpixel((2, 2)))
        d.rectangle([0, PX - 3, PX, PX], fill=line)
        d.rectangle([0, 0, 2, PX], fill=line)


def facade(name, pack):
    colour, kind = FACADES[name]
    if pack == 'real':
        tex = game_texture(style.TEXTURES[name]['baseColorMap'])
        if tex is None:
            im = Image.new('RGB', (PX, PX), colour)
        else:
            # the game texture spans 4 m; this cell is 3 m of it
            w, h = tex.size
            im = tex.crop((0, 0, int(w * CELL / 4), int(h * CELL / 4))).resize((PX, PX), Image.LANCZOS)
    elif pack == 'synth':
        im = Image.new('RGB', (PX, PX), (24, 14, 46))
    else:
        im = Image.new('RGB', (PX, PX), colour if pack == 'toon' else (238, 238, 236))
    draw_windows(im, kind, pack)
    # UV v grows with height and image rows grow downward: store it upside down
    return im.transpose(Image.FLIP_TOP_BOTTOM)


def stylised(name, pack, glow=False):
    """A photo wall's stand-in in a flat pack: its window cell tiled over the wall's repeat
    (2 x 2 cells for 6 m, 11 x 11 for the 33 m loggia sheet), upside down like facade()."""
    family, colour, metres = photo.PHOTO_WALLS[name]
    kind = None if family.startswith('end_') else ('panel' if family in ('panel', 'loggia') else 'window')
    if glow:
        cell = Image.new('RGB', (PX, PX), (0, 0, 0))
    elif pack == 'toon':
        cell = Image.new('RGB', (PX, PX), tuple(round(c * 255) for c in colour))
    elif pack == 'synth':
        cell = Image.new('RGB', (PX, PX), (24, 14, 46))
    else:
        cell = Image.new('RGB', (PX, PX), (238, 238, 236))
    if kind:
        draw_windows(cell, kind, 'synth' if glow else pack)
    if glow:
        mask = cell.point(lambda v: 255 if v > 200 else 0).convert('L')
        cell = Image.new('RGB', (PX, PX), (0, 0, 0))
        cell.paste(Image.new('RGB', (PX, PX), CYAN), mask=mask)
    n = max(1, round(metres / CELL))
    size = 2048 if n > 4 else PX
    step = size // n
    small = cell.resize((step, step), Image.LANCZOS)
    out = Image.new('RGB', (size, size))
    for i in range(n):
        for j in range(n):
            out.paste(small, (i * step, j * step))
    return out.resize((size, size)).transpose(Image.FLIP_TOP_BOTTOM)


def photo_real(name):
    """(colour, normal or None) of a photo wall for the 'real' pack, upside down like facade():
    texture v grows with height. A flipped normal map's green (tangent y) is inverted too."""
    im, nm = photo.wall_image(name, LAYERS[0] if LAYERS else None)
    im = im.transpose(Image.FLIP_TOP_BOTTOM)
    if nm is not None:
        r, g, b = nm.transpose(Image.FLIP_TOP_BOTTOM).split()
        nm = Image.merge('RGB', (r, g.point(lambda v: 255 - v), b))
    return im, nm


LAYERS = []


def synth_glow(name):
    """Emissive map for a synthwave facade: only the neon window frames light up."""
    im = Image.new('RGB', (PX, PX), (0, 0, 0))
    draw_windows(im, FACADES[name][1], 'synth')
    mask = im.convert('RGB').point(lambda v: 255 if v > 200 else 0).convert('L')
    out = Image.new('RGB', (PX, PX), (0, 0, 0))
    out.paste(Image.new('RGB', (PX, PX), CYAN), mask=mask)
    return out.transpose(Image.FLIP_TOP_BOTTOM)


def synth_grid(base, glow=False):
    """One ground repeat with neon lines on its edges (the Outrun grid)."""
    im = Image.new('RGB', (PX, PX), (0, 0, 0) if glow else base)
    d = ImageDraw.Draw(im)
    w = PX // 64
    d.rectangle([0, 0, PX, w], fill=NEON); d.rectangle([0, 0, w, PX], fill=NEON)
    return im


def synth_sun():
    """The sun: yellow to magenta from the top, cut by widening gaps in its lower half."""
    im = Image.new('RGBA', (PX, PX), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for y in range(PX):
        t = y / PX
        if t > .5 and (t * 18) % 1 < .15 + .45 * (t - .5) / .5:
            continue
        half = (max(0., (PX / 2) ** 2 - (y - PX / 2) ** 2)) ** .5
        if half > 0:
            d.line([(PX / 2 - half, y), (PX / 2 + half, y)], fill=(255, int(230 - 200 * t), int(60 + 140 * t), 255))
    return im


def colour(rgb):
    return ' '.join(f'{c / 255:.3f}' for c in rgb) + ' 1'


def pack_fields(pack, base):
    """Material name -> stage-0 fields for one pack (all map fields present, '' clears)."""
    if pack in fast_packs.PACKS:
        return fast_packs.fields(pack, base, pack_fields('real', base))
    out = {}
    for kind, (tex, fallback, _, _, mode) in style.KINDS.items():
        f = {k: '' for k in MAP_FIELDS}
        f['emissiveFactor'] = '0 0 0'
        if kind.startswith('wall_'):
            w = kind[5:]
            f['baseColorMap'] = f'{base}/{pack}/{w}.color.png'
            if pack == 'real':
                f.update(style.real_stage(kind))
            if pack == 'synth':
                f['emissiveMap'] = f'{base}/synth/{w}.glow.png'; f['emissiveFactor'] = '1 1 1'
            f['baseColorFactor'] = '1 1 1 1'
        elif pack == 'real':
            if kind in style.PHOTO_GROUND:
                f.update(style.real_stage(kind))
                f['baseColorFactor'] = '1 1 1 1'
            elif tex:
                f.update(style.TEXTURES[tex])
                f['baseColorFactor'] = colour([c * 255 for c in style.TINT.get(kind, [1, 1, 1])[:3]])
            else:
                f['baseColorFactor'] = colour([c * 255 for c in fallback[:3]])
        elif pack == 'synth':
            if kind in ('ground', 'forest', 'paved', 'park'):
                f['baseColorMap'] = f'{base}/synth/grid_{"ground" if kind == "park" else kind}.color.png'
                f['emissiveMap'] = f'{base}/synth/grid.glow.png'; f['emissiveFactor'] = '1 1 1'
                f['baseColorFactor'] = '1 1 1 1'
            elif kind in SYNTH_GLOW:
                g = SYNTH_GLOW[kind]
                f['baseColorFactor'] = colour([c * 255 for c in g]); f['emissiveFactor'] = ' '.join(map(str, g))
            else:
                f['baseColorFactor'] = colour(SYNTH.get(kind, (30, 20, 50)))
        else:
            f['baseColorFactor'] = colour(FLAT.get(kind, ((200, 200, 200), (230, 230, 230)))[0 if pack == 'toon' else 1])
        out['e2rf_' + kind] = f
    return out


def environment(pack):
    """Sky and fog per pack: the level's own (earthng/build_mod.SKY) or a synthwave dusk."""
    import build_mod
    sky = {o['name']: o for o in build_mod.SKY}

    def fields(o, keys):
        return {k: (' '.join(map(str, o[k])) if isinstance(o[k], list) else str(o[k])) for k in keys if k in o}
    sun_keys = ('azimuth', 'elevation', 'brightness', 'skyBrightness', 'colorize', 'colorizeAmount', 'fogScale',
                'sunScale', 'ambientScale', 'nightColor', 'nightFogColor', 'nightCubemap')
    info_keys = ('fogColor', 'fogDensity', 'canvasClearColor')
    if pack != 'synth':
        real = {'objects': {'sunsky': {**fields(sky['sunsky'], sun_keys), 'useNightCubemap': '0'}, 'theLevelInfo': fields(sky['theLevelInfo'], info_keys),
                            'clouds': fields(sky['clouds'], ('coverage', 'exposure', 'baseColor'))},
                'sun': False}
        return fast_packs.environment(pack, real) if pack in fast_packs.PACKS else real
    return {'objects': {
        'sunsky': {'azimuth': '250', 'elevation': '3', 'brightness': '0.45', 'skyBrightness': '6',
                   'colorize': '0.75 0.1 0.95 1', 'colorizeAmount': '4', 'fogScale': '0.55 0.08 0.75 1',
                   'sunScale': '1 0.3 0.8 1', 'ambientScale': '0.45 0.25 0.7 1'},
        'theLevelInfo': {'fogColor': '0.25 0.04 0.38 1', 'fogDensity': '0.00004', 'canvasClearColor': '40 10 60 255'}},
        'sun': {'azimuth': 250, 'distance': 3200, 'size': 1500, 'height': 420, 'material': 'e2rf_synth_sun'}}


def warm_defs(pack, fields):
    """One material per pack and texture, loaded at the landing on a hidden triangle: a texture
    the engine first meets through a runtime map swap streams into a slot sized for the old one
    and breaks (\"dds mip chain (11) does not match texture (9)\", fast-game-packs-02)."""
    out = {}
    for mat, f in fields.items():
        stage = {k: v for k, v in f.items() if k in MAP_FIELDS and v}
        if not stage:
            continue
        name = f'e2rf_pk_{pack}_{mat.removeprefix("e2rf_")}'
        out[name] = {'name': name, 'mapTo': name, 'class': 'Material', 'version': 1.5,
                     'Stages': [stage, {}, {}, {}]}
    return out


def ensure(user):
    """The art in the user folder (the game reads it from there): copied from the shared
    build in data/art/<VERSION>, which is written once (~20 s: photo facades and ground
    sheets). Returns the art root path in the user folder."""
    import shutil
    import paths
    r = root(user)
    if (r / 'packs.json').exists():
        return r
    shared = prebuild()
    # file by file, packs.json last (the marker): renaming a whole folder inside the profile is
    # refused while the game watches it (WinError 5, vis-02-akadem)
    for src in sorted(shared.rglob('*')):
        if src.is_file() and src.name != 'packs.json':
            dst = r / src.relative_to(shared)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    tmp = r / 'packs.json.tmp'
    shutil.copyfile(shared / 'packs.json', tmp)
    os.replace(tmp, r / 'packs.json')
    return r


def prebuild():
    """The shared art build (data/art/<VERSION>), written when missing; returns its path."""
    import paths
    shared = paths.PROJECT / 'data' / 'art' / VERSION
    if not (shared / 'packs.json').exists():
        build(shared)
    return shared


def build(r):
    """Write the facade textures, ground sheets and packs.json into r."""
    base = '/e2r_stream/fast-art/' + VERSION
    done = r / 'packs.json'
    for pack in BASE_PACKS:
        (r / pack).mkdir(parents=True, exist_ok=True)
        for name in FACADES:
            path = r / pack / f'{name}.color.png'
            facade(name, pack).save(path)
            if pack == 'synth':
                synth_glow(name).save(r / pack / f'{name}.glow.png')
        for name in photo.PHOTO_WALLS:
            if pack == 'real':
                if not LAYERS:
                    LAYERS.append(photo.panel_atlas())
                im, nm = photo_real(name)
                im.save(r / pack / f'{name}.color.png')
                if nm is not None:
                    nm.save(r / pack / f'{name}.normal.png')
            else:
                stylised(name, pack).save(r / pack / f'{name}.color.png')
                if pack == 'synth':
                    stylised(name, pack, glow=True).save(r / pack / f'{name}.glow.png')
    for kind in style.PHOTO_GROUND:
        im, nm = photo.ground_sheet(kind)
        im.save(r / 'real' / f'ground_{kind}.color.png'); nm.save(r / 'real' / f'ground_{kind}.normal.png')
    # footpaths: ambientCG photos as they are (normal green flipped for BeamNG)
    for name, src in (('paving', 'paving'), ('trodden', 'photo_trodden')):
        Image.open(photo.TEXTURES / src / 'albedo.jpg').convert('RGB').resize((1024, 1024), Image.LANCZOS).save(r / 'real' / f'{name}.color.png')
        rr, g, b = Image.open(photo.TEXTURES / src / 'normal.jpg').convert('RGB').resize((1024, 1024), Image.LANCZOS).split()
        Image.merge('RGB', (rr, g.point(lambda v: 255 - v), b)).save(r / 'real' / f'{name}.normal.png')
    # parked cars: one cell per paint colour, then glass and tyre (fast.CAR_COLOURS)
    import fast
    cars = Image.new('RGB', (32 * fast.CAR_ATLAS, 32))
    d = ImageDraw.Draw(cars)
    for k, rgb in enumerate(fast.CAR_COLOURS + [(36, 44, 52), (22, 22, 22)]):
        d.rectangle([32 * k, 0, 32 * k + 31, 31], fill=rgb)
    cars.save(r / 'real' / 'cars.color.png')
    # the PO-2 concrete fence: one 2.5 m x 2 m slab... laid over a 2.5 m repeat, the slab in the
    # lower 0.8 of it (fences are 2 m tall), upside down like the facades
    slab = Image.open(photo.TEXTURES / 'panelka_fence/albedo.jpg').convert('RGB')
    fence = Image.new('RGB', (1024, 1024), (150, 150, 146))
    fence.paste(slab.resize((1024, 820), Image.LANCZOS), (0, 1024 - 820))
    fence.transpose(Image.FLIP_TOP_BOTTOM).save(r / 'real' / 'fence_po2.color.png')
    for kind in ('ground', 'forest', 'paved'):
        synth_grid(SYNTH[kind]).save(r / 'synth' / f'grid_{kind}.color.png')
    synth_grid(None, glow=True).save(r / 'synth' / 'grid.glow.png')
    synth_sun().save(r / 'synth' / 'sun.color.png')
    fast_packs.build(r, base)
    # every map as a BC1 DDS with its mips, the PNG removed: the game reads '<name>.png' from
    # '<name>.dds' as it does its own assets, and does not cook (vis-08-centre showed checker
    # placeholders for ~30 s while ~70 PNGs cooked one by one)
    import fast_dds
    from concurrent.futures import ThreadPoolExecutor
    def to_dds(png):
        fast_dds.save(Image.open(png), png.with_suffix('.dds'))
        png.unlink()
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(to_dds, sorted(r.rglob('*.png'))))
    fields = {p: pack_fields(p, base) for p in PACKS}
    tmp = r / 'packs.json.tmp'
    sun = {'e2rf_synth_sun': {'name': 'e2rf_synth_sun', 'mapTo': 'e2rf_synth_sun', 'class': 'Material', 'version': 1.5,
                              # additive: the black around the disc adds nothing (an alpha test left a square)
                              'translucent': True, 'translucentBlendOp': 'Add', 'translucentZWrite': False,
                              'doubleSided': True,
                              'Stages': [{'baseColorMap': f'{base}/synth/sun.color.png', 'emissiveMap': f'{base}/synth/sun.color.png',
                                          'emissiveFactor': [1, 1, 1]}, {}, {}, {}]}}
    tmp.write_text(json.dumps(style.dds({'order': list(PACKS), 'labels': LABELS, 'fields': fields, 'warm': {p: warm_defs(p, fields[p]) for p in PACKS},
                               'environment': {p: environment(p) for p in PACKS}, 'extra': sun})), encoding='utf8')
    os.replace(tmp, done)
    return r
