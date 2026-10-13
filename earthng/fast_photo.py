"""Photo facades and photo ground for the fast generator's 'real' pack.

Sources are CC0 photographs: the Panelka pack (https://kureca.itch.io/panelka-pack, facades of
Soviet blocks) and ambientCG (lawn, meadow, worn ground), imported by Earth2Road's
tools/import_panelka.py and tools/fetch_textures.py into data/textures/<set>/albedo.jpg.
The same composition as Earth2Road's BeamNG photo style (texture_styles.py): a facade repeat is
6 m (two 3 m storeys by two 3 m bays), the II-68 loggia sheet 33 m; ground sheets cover 24 m.

A wall kind is 'wall_<name>' (fast_style.KINDS); its repeat in metres is PHOTO_WALLS[name][2].
"""
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import paths

TEXTURES = next((p for p in (paths.PROJECT / 'data/textures', paths.EARTH2ROAD / 'game/assets/textures')
                 if (p / 'panelka_panels/albedo.jpg').exists()), paths.PROJECT / 'data/textures')
TILE_PX = 1024          # one 6 m repeat: the panel photos' own 512 px per 3 m cell
CELL = TILE_PX // 2
SHEET_PX = 2048         # the whole II-68 loggia photo, 33 m
LOGGIA_M = 33.0
PANEL_LAYERS = 6        # atlas: five windowed panels, then a blank one
WINDOW_LAYERS = (0, 1, 3, 4)   # layer 2 has a window grille: not in a repeat
PANEL_GAIN = 1.32

# name: (family, colour 0..1, metres per repeat). Colours are the city's: grey and beige panels,
# the pinkish and pale blue series, painted plaster of the old centre.
PHOTO_WALLS = {
    'pnl_grey':   ('panel',   (.80, .80, .78), 6.0),
    'pnl_beige':  ('panel',   (.86, .80, .70), 6.0),
    'pnl_pink':   ('panel',   (.86, .75, .71), 6.0),
    'pnl_blue':   ('panel',   (.74, .80, .86), 6.0),
    'pnl_white':  ('panel',   (.90, .90, .88), 6.0),
    'lgg_cream':  ('loggia',  (.87, .84, .77), LOGGIA_M),
    'lgg_grey':   ('loggia',  (.80, .80, .78), LOGGIA_M),
    'lgg_pink':   ('loggia',  (.86, .76, .72), LOGGIA_M),
    'brk_red':    ('brick',   (.65, .40, .32), 6.0),
    'brk_white':  ('brick',   (.88, .87, .83), 6.0),
    'pls_yellow': ('plaster', (.86, .74, .50), 6.0),
    'pls_cream':  ('plaster', (.89, .85, .76), 6.0),
    'pls_peach':  ('plaster', (.86, .75, .66), 6.0),
    'pls_green':  ('plaster', (.70, .78, .68), 6.0),
    'pls_blue':   ('plaster', (.70, .78, .86), 6.0),
    'pls_grey':   ('plaster', (.78, .77, .74), 6.0),
    # offices: drawn curtain walls (glass that reflects the sky: see fast_style.real_stage),
    # ribbon windows in light concrete; storefronts for the ground floors of the old town
    'off_blue':   ('curtain', (.50, .64, .76), 6.0),
    'off_green':  ('curtain', (.50, .66, .64), 6.0),
    'off_grey':   ('curtain', (.60, .64, .68), 6.0),
    'off_ribbon': ('ribbon',  (.86, .85, .81), 6.0),
    'off_ribbon_dark': ('ribbon', (.56, .55, .53), 6.0),
    'shop_dark':  ('shop',    (.20, .21, .22), 6.0),
    'shop_red':   ('shop',    (.55, .14, .12), 6.0),
    'shop_green': ('shop',    (.12, .36, .24), 6.0),
    'shop_blue':  ('shop',    (.12, .24, .45), 6.0),
}
# blank end walls of elongated panel and brick blocks (no windows across the short side)
for _n, (_f, _c, _m) in list(PHOTO_WALLS.items()):
    if _f in ('panel', 'loggia', 'brick'):
        PHOTO_WALLS[_n + '_end'] = ('end_brick' if _f == 'brick' else 'end_panel', _c, 6.0)

END_WALL_MAX_M = 16.0   # deeper walls are a wing's long side (Soviet blocks are 11-14 m deep)


def unit(*key):
    return int.from_bytes(hashlib.sha256(repr(key).encode()).digest()[:4], 'big') / 2**32


def tint(image, colour, strength=.5, gain=1.):
    """Shift a photo toward colour (0..1 RGB) keeping its brightness (times gain)."""
    a = np.asarray(image, dtype=np.float32)
    mean = sum(colour) / 3 or .05
    k = np.array([gain * (1 + strength * (c / mean - 1)) for c in colour], dtype=np.float32)
    return Image.fromarray(np.clip(a * k, 0, 255).astype('uint8'))


def tiled(path, metres, size=TILE_PX, span=6.0):
    """A tileable photo repeated over `span` m at its real size (whole repeats)."""
    src = Image.open(path).convert('RGB')
    nx, ny = max(1, round(span / metres[0])), max(1, round(span / metres[1]))
    cell = src.resize((size // nx, size // ny), Image.LANCZOS)
    out = Image.new('RGB', (size, size))
    for i in range(nx):
        for j in range(ny):
            out.paste(cell, (i * cell.width, j * cell.height))
    return out.resize((size, size))


def windows(image, glass=(52, 62, 70)):
    """Windows over a plain wall on the 3 m grid: frame, two panes, sill."""
    d = ImageDraw.Draw(image)
    frame = (214, 214, 206)
    for bx in range(2):
        for sy in range(2):
            x0, y0 = bx * CELL, sy * CELL
            box = (x0 + .24 * CELL, y0 + (1 - .80) * CELL, x0 + .76 * CELL, y0 + (1 - .32) * CELL)
            d.rectangle(box, fill=frame)
            inner = (box[0] + .025 * CELL, box[1] + .025 * CELL, box[2] - .025 * CELL, box[3] - .025 * CELL)
            d.rectangle(inner, fill=glass)
            # a lighter upper pane: the sky in the glass
            d.rectangle((inner[0], inner[1], inner[2], inner[1] + (inner[3] - inner[1]) * .35),
                        fill=tuple(min(255, c + 34) for c in glass))
            mid = (box[0] + box[2]) / 2
            d.rectangle((mid - .014 * CELL, inner[1], mid + .014 * CELL, inner[3]), fill=frame)
            d.rectangle((box[0] - .03 * CELL, box[3], box[2] + .03 * CELL, box[3] + .035 * CELL), fill=(176, 176, 170))
    return image


def panel_atlas():
    a = Image.open(TEXTURES / 'panelka_panels/albedo.jpg').convert('RGB')
    s = a.height
    return [a.crop((k * s, 0, (k + 1) * s, s)).resize((CELL, CELL), Image.LANCZOS) for k in range(PANEL_LAYERS)]


def panel(colour, key, layers):
    """2 x 2 photo panels: one window style per facade, an occasional different one."""
    own = WINDOW_LAYERS[int(unit('own', key) * len(WINDOW_LAYERS))]
    out = Image.new('RGB', (TILE_PX, TILE_PX))
    for bx in range(2):
        for sy in range(2):
            swap = WINDOW_LAYERS[int(unit('flat', key, bx, sy) * len(WINDOW_LAYERS))]
            out.paste(layers[swap if unit('swap', key, bx, sy) < .2 else own], (bx * CELL, sy * CELL))
    return tint(out, colour, gain=PANEL_GAIN)


def wall_image(name, layers=None):
    """(colour image, normal image or None) of one repeat; row 0 at the top of the wall."""
    family, colour, _ = PHOTO_WALLS[name]
    layers = layers or panel_atlas()
    if family == 'panel':
        return panel(colour, name, layers), None
    if family == 'loggia':
        a = Image.open(TEXTURES / 'panelka_loggias/albedo.jpg').convert('RGB')
        return tint(a.resize((SHEET_PX, SHEET_PX), Image.LANCZOS), colour, .3, 1.12), None
    if family == 'end_panel':
        out = Image.new('RGB', (TILE_PX, TILE_PX))
        for bx in range(2):
            for sy in range(2):
                out.paste(layers[PANEL_LAYERS - 1], (bx * CELL, sy * CELL))
        return tint(out, colour, gain=PANEL_GAIN), None
    if family in ('curtain', 'ribbon', 'shop'):
        return drawn(family, colour, name), None
    white = colour[0] > .8
    src = TEXTURES / ('panelka_brick_white' if white else 'panelka_brick_red')
    if family in ('brick', 'end_brick'):
        im = tiled(src / 'albedo.jpg', (2.0, 1.8))
        if not white:      # the pack's red brick photographs pink: toward fired clay
            im = tint(im, (.60, .38, .31), .6, .8)
        nm = tiled(src / 'normal.jpg', (2.0, 1.8)) if (src / 'normal.jpg').exists() else None
        return (windows(im) if family == 'brick' else im), nm
    # plaster: the photo tinted strongly toward the paint colour
    im = tint(tiled(TEXTURES / 'panelka_plaster/albedo.jpg', (4.0, 4.0)), colour, .8)
    # the photo's cracks repeat every 4 m: mostly the paint, a little of the photo
    flat = Image.new('RGB', im.size, tuple(round(c * 238) for c in colour))
    return windows(Image.blend(flat, im, .4)), None


def drawn(family, colour, key):
    """Office and shop walls drawn here (row 0 at the top of a 6 m x 6 m repeat, two 3 m floors)."""
    rgb = tuple(round(c * 255) for c in colour)
    rng = np.random.default_rng(int(unit('drawn', key) * 2**31))
    im = Image.new('RGB', (TILE_PX, TILE_PX), rgb)
    d = ImageDraw.Draw(im)
    m = TILE_PX / 6.0                       # px per metre
    if family == 'curtain':
        # panes 1.5 m x 3 m: each a vertical sky gradient, slightly different; aluminium mullions,
        # a dark spandrel at every floor slab
        for fx in range(4):
            for fy in range(2):
                x0, y0 = fx * 1.5 * m, fy * 3 * m
                k = rng.uniform(.88, 1.1)
                for row in range(int(3 * m)):
                    t = row / (3 * m)
                    c = tuple(min(255, int(v * k * (1.18 - .38 * t))) for v in rgb)
                    d.line([(x0, y0 + row), (x0 + 1.5 * m, y0 + row)], fill=c)
                d.rectangle([x0, y0 + 2.45 * m, x0 + 1.5 * m, y0 + 3 * m], fill=tuple(int(v * .42) for v in rgb))
        for fx in range(5):
            d.rectangle([fx * 1.5 * m - 3, 0, fx * 1.5 * m + 3, TILE_PX], fill=(196, 200, 204))
        for fy in range(3):
            d.rectangle([0, fy * 3 * m - 4, TILE_PX, fy * 3 * m + 4], fill=(180, 184, 188))
        return im
    if family == 'ribbon':
        # a light concrete wall with a continuous window band per floor
        glass = (70, 88, 104)
        for fy in range(2):
            top = fy * 3 * m + .55 * m
            d.rectangle([0, top, TILE_PX, top + 1.55 * m], fill=glass)
            d.rectangle([0, top, TILE_PX, top + .35 * m], fill=(110, 130, 148))
            for fx in range(5):
                d.rectangle([fx * 1.5 * m - 3, top, fx * 1.5 * m + 3, top + 1.55 * m], fill=(200, 200, 196))
        return im
    # shop: on the lower 4.2 m: a granite plinth, shop windows with frames, a sign band (the
    # family colour) under the first floor; the upper 1.8 m stays plain (never shown)
    granite = (58, 56, 54)
    floor = TILE_PX                          # bottom of the image = street level
    d.rectangle([0, 0, TILE_PX, TILE_PX], fill=(150, 146, 140))
    d.rectangle([0, floor - .45 * m, TILE_PX, floor], fill=granite)
    for fx in range(3):
        x0 = fx * 2 * m + .15 * m
        x1 = x0 + 1.7 * m
        d.rectangle([x0, floor - 3.2 * m, x1, floor - .45 * m], fill=(214, 214, 210))
        for row in range(int(2.65 * m)):
            t = row / (2.65 * m)
            c = (int(60 + 70 * (1 - t)), int(72 + 74 * (1 - t)), int(84 + 76 * (1 - t)))
            d.line([(x0 + 5, floor - 3.15 * m + row), (x1 - 5, floor - 3.15 * m + row)], fill=c)
        d.rectangle([(x0 + x1) / 2 - 3, floor - 3.2 * m, (x0 + x1) / 2 + 3, floor - .45 * m], fill=(214, 214, 210))
    d.rectangle([0, floor - 4.2 * m, TILE_PX, floor - 3.3 * m], fill=rgb)
    for fx in range(3):
        x0 = fx * 2 * m + .5 * m
        d.rectangle([x0, floor - 3.95 * m, x0 + 1.0 * m, floor - 3.55 * m], fill=(236, 232, 214))
    return im


# --- ground --------------------------------------------------------------------------------------
GROUND_PX = 2048
GROUND_M = 24.0          # one sheet: 8 repeats of a 3 m photo, 6 of a 4 m one
# kind -> (base, metres), (patch, metres, cover), (wear, metres, cover), tint, saturation
GROUND = {
    'ground': (('photo_lawn', 3.0), ('photo_meadow', 4.0, .35), ('photo_worn', 3.0, .12), (.88, .88, .84), .58),
    'park':   (('photo_lawn', 3.0), ('photo_meadow', 4.0, .22), ('photo_worn', 3.0, .05), (.86, .90, .82), .66),
    'forest': (('photo_meadow', 4.0), ('photo_worn', 3.0, .35), ('photo_trodden', 3.0, .15), (.72, .74, .66), .6),
    'verge':  (('photo_verge', 3.0), ('photo_lawn', 3.0, .35), ('photo_worn', 3.0, .15), (.86, .88, .76), .85),
}


def _layer(name, metres, which):
    repeats = round(GROUND_M / metres)
    cell = GROUND_PX // repeats
    im = Image.open(TEXTURES / name / f'{which}.jpg').convert('RGB')
    tile = np.asarray(im.resize((cell, cell), Image.LANCZOS), dtype=np.float32) / 255
    sheet = np.tile(tile, (repeats, repeats, 1))
    if sheet.shape[0] != GROUND_PX:
        sheet = np.asarray(Image.fromarray((sheet * 255).astype('uint8')).resize((GROUND_PX, GROUND_PX), Image.LANCZOS),
                           dtype=np.float32) / 255
    return sheet


NOISE_PX = 512


def _noise(seed, cycles):
    """Smooth 0..1 noise that repeats with the sheet: waves of whole cycles, summed at NOISE_PX
    (at most 24 cycles: 21 px each) and scaled up; 2048 px direct took 18 s a sheet."""
    rng = np.random.default_rng(seed)
    axis = np.linspace(0, 2 * np.pi, NOISE_PX, endpoint=False, dtype=np.float32)
    u, v = np.meshgrid(axis, axis)
    total = np.zeros_like(u)
    for _ in range(28):
        fx, fy = rng.integers(-cycles, cycles + 1, 2)
        if fx == 0 and fy == 0:
            continue
        total += np.cos(fx * u + fy * v + rng.uniform(0, 2 * np.pi)) / (fx * fx + fy * fy) ** .35
    total = (total - total.min()) / (total.max() - total.min())
    # wrap a border before scaling so the sheet still repeats without a seam
    pad = np.pad(total, 2, mode='wrap')
    big = Image.fromarray(pad.astype(np.float32), mode='F').resize(((NOISE_PX + 4) * GROUND_PX // NOISE_PX,) * 2, Image.BICUBIC)
    k = 2 * GROUND_PX // NOISE_PX
    return np.asarray(big, dtype=np.float32)[k:k + GROUND_PX, k:k + GROUND_PX]


def _cover(noise, cover, detail):
    edge = 1 - cover + (detail - .4) * .5
    t = np.clip((noise - (edge - .03)) / .06, 0, 1)
    return (t * t * (3 - 2 * t))[..., None]


def ground_sheet(kind):
    """(colour, normal) images of one GROUND_M sheet: base photo, a turned copy to hide its
    grid, patches and worn spots on noise masks that repeat with the sheet. Normal green is
    flipped for BeamNG (ambientCG ships OpenGL normals)."""
    (base, bm), (patch, pm, pc), (wear, wm, wc), tone, saturation = GROUND[kind]
    key = int.from_bytes(hashlib.sha256(kind.encode()).digest()[:4], 'big')
    layers = {n: (_layer(n, m, 'albedo'), _layer(n, m, 'normal')) for n, m in ((base, bm), (patch, pm), (wear, wm))}
    shift = GROUND_PX // round(GROUND_M / bm) // 2
    turned_c = np.roll(np.rot90(layers[base][0]), (shift, shift), (0, 1))
    turned_n = np.roll(np.rot90(layers[base][1]), (shift, shift), (0, 1))
    turned_n = np.stack([1 - turned_n[..., 1], turned_n[..., 0], turned_n[..., 2]], -1)
    k = _cover(_noise(key + 99, 6), .5, np.full((GROUND_PX, GROUND_PX), .4, np.float32))
    colour = layers[base][0] * (1 - k) + turned_c * k
    normal = layers[base][1] * (1 - k) + turned_n * k
    target = colour.reshape(-1, 3).mean(0)
    for name, cover, amount, cycles in ((patch, pc, .6, 2), (wear, wc, .3, 4)):
        own_c, own_n = layers[name]
        own_c = own_c * (1 + amount * (target / np.maximum(own_c.reshape(-1, 3).mean(0), .02) - 1))
        noise = _noise(key + cycles, cycles) * .7 + _noise(key + 7 * cycles, 3 * cycles) * .2 + _noise(key + 13 * cycles, 24) * .1
        mask = _cover(noise, cover, own_c.mean(2))
        colour = colour * (1 - mask) + own_c * mask
        normal = normal * (1 - mask) + own_n * mask
    grey = (colour @ np.array([.3, .59, .11], dtype=np.float32))[..., None]
    colour = (grey + (colour - grey) * saturation) * np.array(tone, dtype=np.float32)
    normal[..., 1] = 1 - normal[..., 1]
    img = lambda a: Image.fromarray(np.clip(a * 255 + .5, 0, 255).astype('uint8'))
    return img(colour), img(normal)


def available():
    return (TEXTURES / 'panelka_panels/albedo.jpg').exists() and (TEXTURES / 'photo_lawn/albedo.jpg').exists()


# --- which wall --------------------------------------------------------------------------------
NAMED = {   # OSM building:colour words -> tint family hint
    'white': 'white', 'grey': 'grey', 'gray': 'grey', 'beige': 'beige', 'cream': 'cream', 'yellow': 'yellow',
    'pink': 'pink', 'red': 'red', 'brown': 'red', 'blue': 'blue', 'lightblue': 'blue', 'green': 'green',
    'orange': 'peach', 'peach': 'peach', 'silver': 'grey', 'tan': 'beige', 'ivory': 'cream',
}


def colour_word(value):
    """An OSM colour (name or #rrggbb) as one of the tint words above, or None."""
    if not value:
        return None
    v = str(value).strip().lower()
    if v in NAMED:
        return NAMED[v]
    if v.startswith('#') and len(v) == 7:
        try:
            r, g, b = (int(v[i:i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            return None
        hi, lo = max(r, g, b), min(r, g, b)
        if hi - lo < 24:
            return 'white' if hi > 200 else 'grey'
        if r >= g and r >= b:
            return 'pink' if b > g * .9 else ('yellow' if g > r * .8 else ('peach' if g > r * .6 else 'red'))
        if g >= r and g >= b:
            return 'green'
        return 'blue'
    return None


def pick(options, word, h):
    """An option whose name has the colour word, else a stable choice by hash."""
    if word:
        hit = [o for o in options if o.split('_')[1] == word]
        if hit:
            return hit[h % len(hit)]
    return options[h % len(options)]


def end_walls(points):
    """Per edge of a footprint ring (x, y pairs, not closed): True for the short walls across
    the long axis of an elongated block (length >= 1.6 x width), as Earth2Road's end_walls."""
    n = len(points)
    mask = [False] * n
    if n < 3:
        return mask
    edges = [(points[(i + 1) % n][0] - points[i][0], points[(i + 1) % n][1] - points[i][1]) for i in range(n)]
    longest = max(edges, key=lambda d: d[0] ** 2 + d[1] ** 2)
    norm = (longest[0] ** 2 + longest[1] ** 2) ** .5
    if norm < 1e-6:
        return mask
    ax, ay = longest[0] / norm, longest[1] / norm
    along = [p[0] * ax + p[1] * ay for p in points]
    across = [-p[0] * ay + p[1] * ax for p in points]
    length, width = max(along) - min(along), max(across) - min(across)
    if length < 1.6 * width:
        return mask
    for i, (dx, dy) in enumerate(edges):
        d = (dx * dx + dy * dy) ** .5
        if d > 1e-6 and abs((dx * ax + dy * ay) / d) < .5 and d <= min(width * 1.05 + .5, END_WALL_MAX_M):
            mask[i] = True
    return mask
