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

import fast_style as style

VERSION = 'v3'
CELL = 3.0          # m per texture repeat on walls: one window, one floor
PX = 1024           # texture size: as the game's own (a 256 px swap kept its 1024 mip chain and failed)
ASSETS = Path(r'C:/Program Files (x86)/Steam/steamapps/common/BeamNG.drive/content/assets/materials')

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
}
PACKS = ('real', 'toon', 'model')
MAP_FIELDS = ('baseColorMap', 'normalMap', 'roughnessMap', 'ambientOcclusionMap')


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
    glass = {'real': (52, 66, 82), 'toon': (150, 205, 235), 'model': (190, 190, 192)}[pack]
    frame = {'real': (225, 225, 220), 'toon': (60, 60, 70), 'model': (245, 245, 245)}[pack]
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
    else:
        im = Image.new('RGB', (PX, PX), colour if pack == 'toon' else (238, 238, 236))
    draw_windows(im, kind, pack)
    # UV v grows with height and image rows grow downward: store it upside down
    return im.transpose(Image.FLIP_TOP_BOTTOM)


def colour(rgb):
    return ' '.join(f'{c / 255:.3f}' for c in rgb) + ' 1'


def pack_fields(pack, base):
    """Material name -> stage-0 fields for one pack (all map fields present, '' clears)."""
    out = {}
    for kind, (tex, fallback, _, _, mode) in style.KINDS.items():
        f = {k: '' for k in MAP_FIELDS}
        if kind.startswith('wall_'):
            w = kind[5:]
            f['baseColorMap'] = f'{base}/{pack}/{w}.color.png'
            if pack == 'real':
                f['normalMap'] = style.TEXTURES[w].get('normalMap', '')
            f['baseColorFactor'] = '1 1 1 1'
        elif pack == 'real':
            if tex:
                f.update(style.TEXTURES[tex])
                f['baseColorFactor'] = colour([c * 255 for c in style.TINT.get(kind, [1, 1, 1])[:3]])
            else:
                f['baseColorFactor'] = colour([c * 255 for c in fallback[:3]])
        else:
            f['baseColorFactor'] = colour(FLAT.get(kind, ((200, 200, 200), (230, 230, 230)))[0 if pack == 'toon' else 1])
        out['e2rf_' + kind] = f
    return out


def warm_defs(pack, fields):
    """One material per pack and texture, loaded at the landing on a hidden triangle: a texture
    the engine first meets through a runtime map swap streams into a slot sized for the old one
    and breaks (\"dds mip chain (11) does not match texture (9)\", fast-game-packs-02)."""
    out = {}
    for mat, f in fields.items():
        stage = {k: v for k, v in f.items() if k in MAP_FIELDS and v}
        if not stage:
            continue
        name = f'e2rf_pk_{pack}_{mat[5:]}'
        out[name] = {'name': name, 'mapTo': name, 'class': 'Material', 'version': 1.5,
                     'Stages': [stage, {}, {}, {}]}
    return out


def ensure(user):
    """Write the facade textures and packs.json once (~1 s); returns the art root path."""
    r = root(user)
    done = r / 'packs.json'
    if done.exists():
        return r
    base = '/e2r_stream/fast-art/' + VERSION
    for pack in PACKS:
        (r / pack).mkdir(parents=True, exist_ok=True)
        for name in FACADES:
            path = r / pack / f'{name}.color.png'
            facade(name, pack).save(path)
    fields = {p: pack_fields(p, base) for p in PACKS}
    tmp = r / 'packs.json.tmp'
    tmp.write_text(json.dumps({'fields': fields, 'warm': {p: warm_defs(p, fields[p]) for p in PACKS}}), encoding='utf8')
    os.replace(tmp, done)
    return r
