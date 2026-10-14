"""Five packs built on the 'real' one: night, rain, cyberpunk Kyiv 2077, winter and autumn.

Each starts from the real pack's material fields (photo facades and ground) and overrides some:
lit windows as emissive maps drawn over the very windows of the photo facades (night, cyber),
wet roads (rain, cyber, winter), snow and fallen leaves on the ground sheets (CC0 ambientCG sets
from earthng/fetch_textures.py), the game's tree leaves recoloured or hidden. Sky, fog, clouds,
precipitation and street lights are the pack's environment (mod: world.lua applyPack).

Images are written as PNG into <art>/<pack>/; fast_art.build turns every PNG into a DDS.
"""
import io
import zipfile

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import fast_photo as photo
import fast_style as style

PACKS = ('night', 'rain', 'cyber', 'winter', 'autumn')
LABELS = {'night': 'Ніч', 'rain': 'Дощ', 'cyber': 'Кіберпанк', 'winter': 'Зима', 'autumn': 'Осінь'}

# --- lit windows ---------------------------------------------------------------------------------
# glass of the Panelka panel layers (fast_photo.panel_atlas), in parts of a 3 m cell, row 0 at top
LAYER_GLASS = {0: (.245, .165, .75, .69), 1: (.225, .17, .76, .71), 3: (.245, .13, .76, .665), 4: (.245, .145, .76, .695)}
# (colour, weight): incandescent, warm LED, cold LED, a television
HOME = [((255, 172, 92), .42), ((255, 212, 150), .3), ((196, 214, 255), .16), ((96, 128, 255), .12)]
NEON = [((255, 30, 190), .32), ((0, 225, 255), .3), ((150, 50, 255), .22), ((255, 110, 60), .16)]
OFFICE = (215, 230, 255)
SHOP = (255, 214, 160)


def _pick(palette, u):
    for colour, w in palette:
        if u < w:
            return colour
        u -= w
    return palette[-1][0]


def windows_of(name):
    """(box px, role) of every window of a photo wall, row 0 at the top of the repeat; role is
    'home', 'office', 'shop', 'sign' or 'plate'."""
    family, _, _ = photo.PHOTO_WALLS[name]
    C, T = photo.CELL, photo.TILE_PX
    out = []
    if family == 'panel':
        own = photo.WINDOW_LAYERS[int(photo.unit('own', name) * len(photo.WINDOW_LAYERS))]
        for bx in range(photo.PANEL_N):
            for sy in range(photo.PANEL_N):
                swap = photo.WINDOW_LAYERS[int(photo.unit('flat', name, bx, sy) * len(photo.WINDOW_LAYERS))]
                layer = swap if photo.unit('swap', name, bx, sy) < .2 else own
                x0, y0, x1, y1 = LAYER_GLASS[layer]
                out.append(((bx * C + x0 * C, sy * C + y0 * C, bx * C + x1 * C, sy * C + y1 * C), 'home'))
    elif family == 'loggia':
        S = photo.SHEET_PX
        fh, cw = S / 11, S / 5
        for k in range(11):
            for c in range(5):
                out.append(((c * cw + .05 * cw, k * fh + .04 * fh, c * cw + .95 * cw, k * fh + .5 * fh), 'home'))
    elif family in ('brick', 'plaster'):
        for bx in range(2):
            for sy in range(2):
                x0, y0 = bx * C, sy * C
                out.append(((x0 + .265 * C, y0 + .225 * C, x0 + .735 * C, y0 + .655 * C), 'home'))
    elif family == 'curtain':
        m = T / 6.0
        for fx in range(4):
            for fy in range(2):
                out.append(((fx * 1.5 * m + 4, fy * 3 * m + 5, (fx + 1) * 1.5 * m - 4, fy * 3 * m + 2.45 * m), 'office'))
    elif family == 'ribbon':
        m = T / 6.0
        for fy in range(2):
            top = fy * 3 * m + .55 * m
            for fx in range(4):
                out.append(((fx * 1.5 * m + 4, top + 4, (fx + 1) * 1.5 * m - 4, top + 1.55 * m), 'office'))
    elif family == 'shop':
        m, floor = T / 6.0, T
        for fx in range(3):
            x0 = fx * 2 * m + .15 * m
            out.append(((x0 + 6, floor - 3.15 * m, x0 + 1.7 * m - 6, floor - .45 * m), 'shop'))
            out.append(((fx * 2 * m + .5 * m, floor - 3.95 * m, fx * 2 * m + 1.5 * m, floor - 3.55 * m), 'plate'))
        out.append(((0, floor - 4.2 * m, T, floor - 3.3 * m), 'sign'))
    return out


def _glyphs(d, box, colour, rng):
    """Neon letters: strokes of a made-up Cyrillic-ish script in a sign box."""
    x0, y0, x1, y1 = box
    h = y1 - y0
    w = h * .62
    x = x0 + h * .3
    stroke = max(3, int(h * .09))
    while x + w < x1 - h * .3:
        if rng.random() < .18:
            x += w * .7
            continue
        for _ in range(rng.integers(2, 4)):
            kind = rng.integers(0, 4)
            if kind == 0:
                d.line([(x, y0 + h * .15), (x, y1 - h * .15)], fill=colour, width=stroke)
            elif kind == 1:
                d.line([(x + w * .8, y0 + h * .15), (x + w * .8, y1 - h * .15)], fill=colour, width=stroke)
            elif kind == 2:
                yy = y0 + h * rng.choice([.15, .5, .85])
                d.line([(x, yy), (x + w * .8, yy)], fill=colour, width=stroke)
            else:
                d.line([(x, y1 - h * .15), (x + w * .8, y0 + h * .15)], fill=colour, width=stroke)
        x += w * 1.1


def wall_glow(name, mood, colour_image=None):
    """Emissive map of a photo wall (same size and orientation as its colour map in fast_art,
    i.e. upside down): some windows lit. mood 'night' (homes warm, offices white) or 'cyber'
    (neon, shop signs with letters, a neon line along every office floor)."""
    family = photo.PHOTO_WALLS[name][0]
    size = photo.SHEET_PX if family == 'loggia' else photo.CELL * photo.PANEL_N if family == 'panel' else photo.TILE_PX
    im = Image.new('RGB', (size, size), (0, 0, 0))
    if family.startswith('end_'):
        return im
    d = ImageDraw.Draw(im)
    rng = np.random.default_rng(int(photo.unit('glow', name, mood) * 2**31))
    home = NEON if mood == 'cyber' else HOME
    share = .42 if mood == 'cyber' else .36
    floor_lit = {}
    wins = windows_of(name)
    for k, (box, role) in enumerate(wins):
        u = photo.unit('lit', name, mood, k)
        if role == 'home':
            if family in ('brick', 'plaster'):
                # four windows a repeat: never all dark, never all lit
                lit = [0, 3] if photo.unit('pat', name) < .5 else [1, 2]
                if photo.unit('third', name, mood) < .5:
                    lit = lit + [(lit[0] + 1) % 4]
                on = k in lit
            else:
                on = u < share
            if not on:
                continue
            c = _pick(home, photo.unit('hue', name, mood, k))
            gain = .55 + .45 * photo.unit('gain', name, mood, k)
        elif role == 'office':
            row = int(box[1] // (size / 2))
            if row not in floor_lit:
                floor_lit[row] = photo.unit('floor', name, mood, row) < .6
            if not floor_lit[row] or u < .35:
                continue
            # cold and warm panels, dimmer than a home window: an office floor is a broad surface
            c = _pick(NEON, u) if mood == 'cyber' and u > .8 else (OFFICE if u < .7 else (255, 232, 196))
            gain = .42 + .25 * photo.unit('gain', name, mood, k)
        elif role == 'shop':
            c, gain = (_pick(NEON, u) if mood == 'cyber' else SHOP), .9
        elif role == 'plate':
            if mood == 'cyber':
                continue
            c, gain = (255, 246, 226), 1.0
        else:  # sign band
            if mood == 'cyber':
                _glyphs(d, box, _pick(NEON, photo.unit('sign', name)), rng)
            else:
                rgb = tuple(round(v * 255) for v in photo.PHOTO_WALLS[name][1])
                d.rectangle(box, fill=tuple(min(255, int(v * 1.6) + 30) for v in rgb))
            continue
        c = tuple(int(v * gain) for v in c)
        x0, y0, x1, y1 = box
        # a curtain's soft gradient: brighter at the top, darker toward the sill
        hh = max(1, int(y1 - y0))
        for r in range(hh):
            t = r / hh
            k2 = 1.0 - .35 * t
            d.line([(x0, y0 + r), (x1, y0 + r)], fill=tuple(int(v * k2) for v in c))
        if role == 'home' and family != 'loggia':
            mid = (x0 + x1) / 2
            d.rectangle([mid - size * .004, y0, mid + size * .004, y1], fill=(0, 0, 0))
    if mood == 'cyber' and family in ('curtain', 'ribbon'):
        m = size / 6.0
        for fy in range(3):
            d.rectangle([0, fy * 3 * m - 5, size, fy * 3 * m + 5], fill=(0, 230, 255) if fy % 2 == 0 else (255, 30, 190))
    if colour_image is not None and family in ('panel', 'loggia', 'brick', 'plaster'):
        # the photo's own curtains and frames show through the light
        lum = np.asarray(colour_image.convert('L').resize((size, size)), dtype=np.float32) / 255
        lum = np.flipud(lum)        # the colour image is stored upside down already
        a = np.asarray(im, dtype=np.float32) * (.55 + .45 * np.clip(lum * 1.6, 0, 1))[..., None]
        im = Image.fromarray(np.clip(a, 0, 255).astype('uint8'))
        return _flip(im)
    return _flip(im)


def _flip(im):
    return im.transpose(Image.FLIP_TOP_BOTTOM)


def facade_glow(kind, mood):
    """Emissive map of a drawn 3 m facade cell (fast_art.facade: one window, or an office band)."""
    import fast_art
    PX, s = fast_art.PX, fast_art.PX / fast_art.CELL
    im = Image.new('RGB', (PX, PX), (0, 0, 0))
    d = ImageDraw.Draw(im)
    c = (_pick(NEON, photo.unit('fac', kind)) if mood == 'cyber' else
         (OFFICE if kind == 'band' else (255, 190, 110)))
    c = tuple(int(v * .4) for v in c)       # every cell alike: dim, or the far city turns to a grid
    if kind == 'band':
        d.rectangle([0, s * .6, PX, s * 2.7], fill=c)
        if mood == 'cyber':
            d.rectangle([0, s * .5, PX, s * .56], fill=(0, 230, 255))
    else:
        x0, x1, y0, y1 = (.9, 2.1, .6, 1.4) if kind == 'small' else (.8, 2.2, .6, 2.1)
        d.rectangle([s * x0, PX - s * y1, s * x1, PX - s * y0], fill=c)
    return im.transpose(Image.FLIP_TOP_BOTTOM)


# --- ground: snow and leaves -------------------------------------------------------------------
def _img(a):
    return Image.fromarray(np.clip(a * 255 + .5, 0, 255).astype('uint8'))


def _arr(im):
    return np.asarray(im.convert('RGB'), dtype=np.float32) / 255


def snow_sheet(kind):
    """(colour, normal) of a GROUND_M sheet under snow: fresh snow, trodden patches, and on
    'forest' and 'trodden' grey thaw. Normal green flipped for BeamNG like fast_photo."""
    key = {'ground': 11, 'park': 12, 'forest': 13, 'paved': 14}[kind]
    base_c, base_n = photo._layer('snow_fresh', 3.0, 'albedo'), photo._layer('snow_fresh', 3.0, 'normal')
    trod_c, trod_n = photo._layer('snow_trodden', 3.0, 'albedo'), photo._layer('snow_trodden', 3.0, 'normal')
    dirt_c, dirt_n = photo._layer('snow_dirty', 3.0, 'albedo'), photo._layer('snow_dirty', 3.0, 'normal')
    # Snow005 photographs grey: lift it to a bright, slightly blue white
    base_c = base_c / max(base_c.mean(), .05) * np.array([.86, .89, .95], np.float32)
    trod_c = trod_c / max(trod_c.mean(), .05) * np.array([.80, .84, .91], np.float32)
    flat = np.full((photo.GROUND_PX, photo.GROUND_PX), .4, np.float32)
    cover_t, cover_d = {'ground': (.3, .06), 'park': (.22, .03), 'forest': (.15, .2), 'paved': (.5, .35)}[kind]
    m1 = photo._cover(photo._noise(key, 3) * .8 + photo._noise(key + 5, 12) * .2, cover_t, flat)
    m2 = photo._cover(photo._noise(key + 9, 5) * .7 + photo._noise(key + 3, 24) * .3, cover_d, flat)
    colour = base_c * (1 - m1) + trod_c * m1
    normal = base_n * (1 - m1) + trod_n * m1
    colour = colour * (1 - m2) + dirt_c * m2
    normal = normal * (1 - m2) + dirt_n * m2
    normal[..., 1] = 1 - normal[..., 1]
    return _img(colour), _img(normal)


def snow_tile(src, metres, span, tone=(1, 1, 1), size=1024):
    """A plain snow repeat for roofs, sidewalks and paths: the photo `src` repeated over `span` m."""
    c = photo.tiled(photo.TEXTURES / src / 'albedo.jpg', (metres, metres), size, span)
    n = photo.tiled(photo.TEXTURES / src / 'normal.jpg', (metres, metres), size, span)
    a = _arr(c)
    a = a / max(a.mean(), .05) * np.array(tone, np.float32)
    r, g, b = n.split()
    return _img(a), Image.merge('RGB', (r, g.point(lambda v: 255 - v), b))


def leaves_over(colour, normal, cover, key, metres=2.0, size=None, autumn_grass=True):
    """Fallen leaves (ScatteredLeaves007: leaves on black) over a colour/normal pair in the
    repeat of a GROUND_M sheet (or `size` px of `metres` m repeats), on a noise mask."""
    size = size or colour.size[0]
    reps = max(1, round((photo.GROUND_M if size == photo.GROUND_PX else 6.0) / metres))
    cell = size // reps
    lc = Image.open(photo.TEXTURES / 'leaves_orange/albedo.jpg').convert('RGB').resize((cell, cell), Image.LANCZOS)
    ln = Image.open(photo.TEXTURES / 'leaves_orange/normal.jpg').convert('RGB').resize((cell, cell), Image.LANCZOS)
    fit = lambda a: _arr(_img(np.tile(a, (reps, reps, 1))).resize((size, size), Image.LANCZOS))
    lc, ln = fit(_arr(lc)), fit(_arr(ln))
    ln[..., 1] = 1 - ln[..., 1]
    # warmer and brighter than the photo: Kyiv's chestnuts and maples go yellow and orange
    lc = np.clip(lc * np.array([1.22, 1.0, .62], np.float32) + np.array([.03, .015, 0], np.float32), 0, 1)
    leaf = np.clip((lc.max(2) - .09) / .12, 0, 1)[..., None]
    c, n = _arr(colour.resize((size, size))), _arr(normal.resize((size, size)))
    if autumn_grass:
        grey = (c @ np.array([.3, .59, .11], np.float32))[..., None]
        c = (grey + (c - grey) * .7) * np.array([1.08, 1.0, .78], np.float32)
    if size == photo.GROUND_PX:
        noise = photo._noise(key, 4) * .75 + photo._noise(key + 1, 16) * .25
    else:
        noise = np.asarray(Image.fromarray(photo._noise(key, 5) * .7 + photo._noise(key + 1, 14) * .3).resize((size, size)), np.float32)
    mask = photo._cover(noise, cover, np.full((size, size), .4, np.float32)) * leaf
    return _img(c * (1 - mask) + lc * mask), _img(n * (1 - mask) + ln * mask)


# --- trees ----------------------------------------------------------------------------------------
LEAF_NAMES = ('beech_leaves', 'm_ind_beech_leaves', 'beech_blocker', 'm_ind_beech_blocker', 'm_ind_birch_leaves_01',
              'm_oak_leaves_01', 'oak_leaves_01', 'm_oak_leaves_02', 'oak_leaves_02', 'Moss_01',
              'Oak_branch_01', 'Oak_branch_02', 'beech_branch_01', 'Birch_Branch_01')
# material -> its colour map in the game's tree.zip (as fast_tree_materials.json has it)
LEAF_MATS = {m: style.MATERIALS[m]['Stages'][0]['baseColorMap'].split('/materials/tree/')[1].replace('.color.png', '')
             for m in LEAF_NAMES}
BRANCHES = ('Oak_branch_01', 'Oak_branch_02', 'beech_branch_01', 'Birch_Branch_01')
# autumn hue per tree: birches go yellow, beeches copper, oaks rust
AUTUMN = {'beech': ((1.0, .55, .16), (.86, .32, .08)), 'birch': ((1.0, .82, .2), (.95, .62, .1)),
          'oak': ((.9, .45, .12), (.62, .22, .06)), 'Moss': ((.75, .55, .2), (.5, .35, .12))}


def tree_texture(rel):
    import fast_art
    with zipfile.ZipFile(fast_art.ASSETS / 'tree.zip') as z:
        im = Image.open(io.BytesIO(z.read(f'assets/materials/tree/{rel}.color.dds'))).convert('RGB')
    w, h = im.size
    if max(w, h) > 1024:
        im = im.resize((w * 1024 // max(w, h), h * 1024 // max(w, h)), Image.LANCZOS)
    return im


def autumn_leaves(rel):
    """The leaf texture with its green turned to the species' autumn colours (two tones mixed
    on a noise, so a crown is not one flat colour); bark and twigs (not green) stay."""
    im = tree_texture(rel)
    a = _arr(im)
    hsv = np.asarray(im.convert('HSV'), dtype=np.float32) / 255
    hue, sat = hsv[..., 0] * 360, hsv[..., 1]
    # green to olive leaves (hue 55-170); grey bark and twigs keep their colour
    green = (np.clip((hue - 50) / 12, 0, 1) * np.clip((175 - hue) / 12, 0, 1) * np.clip((sat - .08) / .1, 0, 1))[..., None]
    lum = (a @ np.array([.3, .59, .11], np.float32))[..., None]
    tones = AUTUMN[next(k for k in AUTUMN if k.lower() in rel.lower())]
    h, w = a.shape[:2]
    rng = np.random.default_rng(len(rel))
    small = rng.random((max(2, h // 64), max(2, w // 64))).astype(np.float32)
    mix = np.asarray(Image.fromarray(small).resize((w, h), Image.BICUBIC), np.float32)[..., None]
    tone = np.array(tones[0], np.float32) * (1 - mix) + np.array(tones[1], np.float32) * mix
    leaf = np.clip(lum * 2.6 + .08, 0, 1) * tone
    return _img(a * (1 - green) + leaf * green)


# --- the packs --------------------------------------------------------------------------------------
GROUND_KINDS = ('ground', 'park', 'forest')
GLOW = {'night': '1.8 1.8 1.8', 'cyber': '2.2 2.2 2.2', 'rain': '1.4 1.4 1.4'}      # emissive gain of lit windows
# Moonlight fill: in the dark packs every surface also glows faintly in its own colour (emissive =
# its colour map x FILL x TINT). The sky lights a night in two ways between sessions — a blue
# "moonlit" one or, with a stale dark capture, nothing — and with nothing every wall away from a
# lamp was black (tester, 2026-10-14). A sun above the horizon, however dim, blew the frame out
# at a night exposure (out/fix-packs-02), and nightColor/ambientScale did not reach the dark case.
FILL = {'night': .25, 'cyber': .2, 'rain': .3}
TINT = {'night': (.7, .8, 1.), 'cyber': (.9, .6, 1.), 'rain': (.85, .9, 1.)}


def with_fill(glow, colour, mood):
    """A wall's glow map with the moonlight fill baked in (its emissive map is the windows'):
    the colour map in linear light x FILL x TINT / the glow gain, added under the windows."""
    if colour is None:
        return glow
    gain = float(GLOW[mood].split()[0])
    c = _arr(colour.resize(glow.size, Image.BILINEAR)) ** 2.2 * np.array(TINT[mood], np.float32) * (FILL[mood] / gain)
    g = _arr(glow) ** 2.2
    return _img(np.clip(g + c, 0, 1) ** (1 / 2.2))


def build(r, base):
    """Write every new pack's images (PNG) under r/<pack>/."""
    import fast_art
    for p in PACKS:
        (r / p).mkdir(parents=True, exist_ok=True)
    layers = photo.panel_atlas()
    for name in photo.PHOTO_WALLS:
        colour = Image.open(r / 'real' / f'{name}.color.png') if (r / 'real' / f'{name}.color.png').exists() else None
        if photo.PHOTO_WALLS[name][0].startswith('end_'):
            continue
        for mood in ('night', 'cyber'):
            with_fill(wall_glow(name, mood, colour), colour, mood).save(r / mood / f'{name}.glow.png')
    for name, (_, kind) in fast_art.FACADES.items():
        colour = Image.open(r / 'real' / f'{name}.color.png') if (r / 'real' / f'{name}.color.png').exists() else None
        for mood in ('night', 'cyber'):
            with_fill(facade_glow(kind, mood), colour, mood).save(r / mood / f'{name}.glow.png')
    # wet roads: the game's asphalt roughness, smoother overall and near mirror-smooth in puddles
    for tex, out in (('asphalt', 'road'), ('sidewalk', 'sidewalk')):
        rough = fast_art.game_texture(style.TEXTURES[tex]['roughnessMap'])
        rough = rough.convert('L').resize((1024, 1024)) if rough else Image.new('L', (1024, 1024), 180)
        ra = np.asarray(rough, np.float32) / 255
        pud = np.asarray(Image.fromarray(photo._noise(31 if out == 'road' else 37, 7) * .6 + photo._noise(5, 18) * .4)
                         .resize((1024, 1024)), np.float32)
        puddle = np.clip((pud - .7) / .05, 0, 1)
        wet = ra * .3 * (1 - puddle) + .03 * puddle
        Image.fromarray(np.clip(wet * 255, 0, 255).astype('uint8')).convert('RGB').save(r / 'rain' / f'wet_{out}.data.png')
        if out == 'road':
            Image.fromarray(np.clip(ra * .55 * 255, 0, 255).astype('uint8')).convert('RGB').save(r / 'winter' / 'slush.data.png')
    # winter ground
    for kind in GROUND_KINDS + ('paved',):
        c, n = snow_sheet(kind)
        c.save(r / 'winter' / f'ground_{kind}.color.png'); n.save(r / 'winter' / f'ground_{kind}.normal.png')
    for name, src, metres, span, tone in (('roof', 'snow_fresh', 3.0, 6.0, (.9, .92, .97)),
                                          ('sidewalk', 'snow_trodden', 1.5, 3.0, (.74, .77, .82)),
                                          ('path', 'snow_trodden', 2.0, 2.0, (.8, .83, .88)),
                                          ('trodden', 'snow_dirty', 3.0, 3.0, (.62, .6, .6))):
        c, n = snow_tile(src, metres, span, tone)
        c.save(r / 'winter' / f'{name}.color.png'); n.save(r / 'winter' / f'{name}.normal.png')
    # autumn ground: the real sheets under fallen leaves; paths with a few
    for kind, cover in (('ground', .5), ('park', .62), ('forest', .8)):
        c, n = leaves_over(Image.open(r / 'real' / f'ground_{kind}.color.png'), Image.open(r / 'real' / f'ground_{kind}.normal.png'),
                           cover, 40 + len(kind))
        c.save(r / 'autumn' / f'ground_{kind}.color.png'); n.save(r / 'autumn' / f'ground_{kind}.normal.png')
    c, n = leaves_over(Image.open(r / 'real' / 'paving.color.png'), Image.open(r / 'real' / 'paving.normal.png'), .3, 77,
                       metres=1.0, size=1024, autumn_grass=False)
    c.save(r / 'autumn' / 'paving.color.png'); n.save(r / 'autumn' / 'paving.normal.png')
    for mat, rel in LEAF_MATS.items():
        out = r / 'autumn' / f'tree_{rel.split("/")[-1]}.color.png'
        if not out.exists():
            autumn_leaves(rel).save(out)


def _colour(rgb):
    return ' '.join(f'{c:.3f}' for c in rgb) + ' 1'


def fields(pack, base, real):
    """Material fields of a new pack: a copy of the real pack's (`real`) with this pack's changes."""
    import copy
    import fast_art
    out = copy.deepcopy(real)
    art = f'{base}/{pack}'

    def put(kind, **kw):
        f = out['e2rf_' + kind]
        for k, v in kw.items():
            f[k] = v

    walls = [k[5:] for k in style.KINDS if k.startswith('wall_')]
    if pack in ('night', 'cyber', 'rain'):
        # the rainy evening shares the night's windows
        lit = f'{base}/{"cyber" if pack == "cyber" else "night"}'
        for w in walls:
            if w in photo.PHOTO_WALLS and photo.PHOTO_WALLS[w][0].startswith('end_'):
                continue
            put('wall_' + w, emissiveMap=f'{lit}/{w}.glow.png', emissiveFactor=GLOW[pack])
    if pack == 'cyber':
        for w in walls:
            put('wall_' + w, baseColorFactor='0.45 0.42 0.55 1')
        put('mark', baseColorFactor=_colour((0, 1, 1)), emissiveFactor='0 0.9 1')
        put('curb', baseColorFactor=_colour((1, .1, .8)), emissiveFactor='1 0.08 0.75')
        # the parapets glow: every flat roof outlined in neon over the dark city
        put('parapet', baseColorMap='', normalMap='', roughnessMap='', ambientOcclusionMap='',
            baseColorFactor=_colour((.6, .1, 1)), emissiveFactor='0.55 0.08 1.2')
        put('water', baseColorFactor=_colour((.04, .03, .1)), roughnessFactor=.05)
    if pack in ('rain', 'cyber'):
        put('road', roughnessMap=f'{base}/rain/wet_road.data.png', baseColorFactor='0.55 0.55 0.58 1')
        put('road_major', roughnessMap=f'{base}/rain/wet_road.data.png', baseColorFactor='0.55 0.55 0.58 1')
        put('footway_asphalt', roughnessMap=f'{base}/rain/wet_road.data.png', baseColorFactor='0.55 0.55 0.58 1')
        put('sidewalk', roughnessMap=f'{base}/rain/wet_sidewalk.data.png', baseColorFactor='0.62 0.62 0.64 1')
        put('paved', roughnessMap=f'{base}/rain/wet_sidewalk.data.png')
        for k in ('footway', 'roof', 'roof_metal', 'roof_tile', 'parapet', 'car', 'curb', 'fence_wall'):
            put(k, roughnessFactor=.18)
        if pack == 'rain':
            for w in walls:
                put('wall_' + w, baseColorFactor='0.68 0.68 0.7 1')
            put('water', baseColorFactor=_colour((.1, .14, .16)), roughnessFactor=.05)
            for kind in GROUND_KINDS:
                put(kind, baseColorFactor='0.62 0.66 0.6 1')
    if pack == 'winter':
        for kind in GROUND_KINDS + ('paved',):
            put(kind, baseColorMap=f'{art}/ground_{kind}.color.png', normalMap=f'{art}/ground_{kind}.normal.png',
                roughnessMap='', ambientOcclusionMap='', baseColorFactor='1 1 1 1')
        for kind in ('roof', 'roof_tile', 'roof_metal'):
            put(kind, baseColorMap=f'{art}/roof.color.png', normalMap=f'{art}/roof.normal.png', roughnessMap='',
                ambientOcclusionMap='', baseColorFactor='1 1 1 1')
        put('sidewalk', baseColorMap=f'{art}/sidewalk.color.png', normalMap=f'{art}/sidewalk.normal.png', roughnessMap='',
            baseColorFactor='1 1 1 1')
        put('footway', baseColorMap=f'{art}/path.color.png', normalMap=f'{art}/path.normal.png', baseColorFactor='1 1 1 1')
        put('trodden', baseColorMap=f'{art}/trodden.color.png', normalMap=f'{art}/trodden.normal.png', baseColorFactor='1 1 1 1')
        for k in ('road', 'road_major', 'footway_asphalt'):
            put(k, roughnessMap=f'{art}/slush.data.png', baseColorFactor='0.95 0.96 1 1')
        put('curb', baseColorFactor=_colour((.93, .95, 1)))
        put('parapet', baseColorFactor=_colour((.88, .9, .95)))
        put('water', baseColorFactor=_colour((.72, .8, .86)), roughnessFactor=.12)
        for w in walls:
            put('wall_' + w, baseColorFactor='0.92 0.94 1 1')
    if pack == 'autumn':
        for kind in GROUND_KINDS:
            put(kind, baseColorMap=f'{art}/ground_{kind}.color.png', normalMap=f'{art}/ground_{kind}.normal.png')
        put('footway', baseColorMap=f'{art}/paving.color.png', normalMap=f'{art}/paving.normal.png')
        for w in walls:
            put('wall_' + w, baseColorFactor='1 0.97 0.92 1')
    if pack in FILL:
        # the moonlight fill of everything else: its colour map (or colour) as a faint emissive;
        # walls have it baked into their glow maps (with_fill), the neon of cyber stays as it is
        for mat, f in out.items():
            if not mat.startswith('e2rf_') or f.get('emissiveMap') or f.get('emissiveFactor', '0 0 0') != '0 0 0':
                continue
            bc = [float(v) for v in str(f.get('baseColorFactor', '1 1 1 1')).split()[:3]]
            if f.get('baseColorMap'):
                f['emissiveMap'] = f['baseColorMap']
            f['emissiveFactor'] = ' '.join(f'{FILL[pack] * c * t:.4f}' for c, t in zip(bc, TINT[pack]))
    # the trees: their own materials (fast_tree_materials.json)
    for mat, rel in LEAF_MATS.items():
        f = {}
        if pack == 'winter':
            if mat in BRANCHES:
                continue
            f = {'opacityFactor': '0'}          # bare: only trunks and twigs stay
        elif pack == 'autumn':
            f = {'baseColorMap': f'{art}/tree_{rel.split("/")[-1]}.color.png'}
        elif pack == 'cyber':
            f = {'baseColorFactor': '0.5 0.4 0.75 1'}
        if f:
            out[mat] = f
    return style.dds(out)


def environment(pack, real):
    """Sky, fog, clouds, rain or snow, street lights per pack ('real' is the level's own sky)."""
    import copy
    env = copy.deepcopy(real)
    sun, info = env['objects']['sunsky'], env['objects']['theLevelInfo']
    clouds = env['objects'].setdefault('clouds', {})
    env.update(precip=False, lights=False, sun=False)
    if pack in ('night', 'cyber'):
        sun.update(elevation='-25', brightness='0.02', skyBrightness='1', ambientScale='0.05 0.055 0.1 1',
                   nightColor='0.008 0.011 0.026 1', nightFogColor='0.008 0.011 0.026 1', colorizeAmount='0')
        info.update(fogColor='0.016 0.022 0.048 1', fogDensity='0.00045', canvasClearColor='3 5 12 255')
        clouds.update(coverage='0.25', exposure='0.15')
        env['lights'] = {'colors': [[1, .55, .2]], 'radius': 34, 'brightness': 7, 'height': 7.3, 'count': 36}
        # a fixed exposure (EV): the automatic one brightens the night back to dusk. (A night
        # cubemap, BNG_Sky_Space, took in some sessions and not in others: vis-packs-podil-06/07.)
        env['exposure'] = 4.5
    if pack == 'cyber':
        sun.update(nightColor='0.04 0.012 0.07 1', nightFogColor='0.07 0.015 0.11 1', ambientScale='0.12 0.06 0.18 1')
        info.update(fogColor='0.08 0.015 0.13 1', fogDensity='0.0008', canvasClearColor='12 3 22 255')
        env['lights'] = {'colors': [[1, .1, .75], [0, .85, 1], [.55, .2, 1]], 'radius': 26, 'brightness': 6,
                         'height': 7.3, 'count': 28}
        env['precip'] = {'dataBlock': 'rain_medium', 'numDrops': 2500, 'minSpeed': 1.4, 'maxSpeed': 1.8}
        env['exposure'] = 5
    if pack == 'rain':
        # a rainy evening: dusk under low cloud, the street lights on, wet asphalt mirroring them
        sun.update(elevation='-5', brightness='0.1', skyBrightness='7', colorize='0.5 0.55 0.62 1', colorizeAmount='1',
                   ambientScale='0.3 0.33 0.4 1', nightColor='0.13 0.15 0.19 1', nightFogColor='0.16 0.18 0.22 1',
                   fogScale='0.5 0.54 0.6 1')
        info.update(fogColor='0.2 0.22 0.26 1', fogDensity='0.0011', canvasClearColor='46 52 62 255')
        clouds.update(coverage='1', exposure='0.2', baseColor='0.3 0.32 0.36 1')
        env['precip'] = {'dataBlock': 'rain_medium', 'numDrops': 7000, 'minSpeed': 1.5, 'maxSpeed': 2.0}
        env['lights'] = {'colors': [[1, .62, .32]], 'radius': 26, 'brightness': 5, 'height': 7.3, 'count': 28}
        env['exposure'] = 5
    if pack == 'winter':
        sun.update(elevation='13', azimuth='195', brightness='0.75', skyBrightness='16', colorize='0.78 0.85 0.98 1',
                   colorizeAmount='0.7', ambientScale='0.52 0.56 0.66 1', sunScale='1 0.9 0.8 1', fogScale='0.75 0.8 0.9 1')
        info.update(fogColor='0.8 0.83 0.88 1', fogDensity='0.0006', canvasClearColor='196 204 216 255')
        clouds.update(coverage='0.75', exposure='0.7', baseColor='0.9 0.92 0.96 1')
        env['precip'] = {'dataBlock': 'Snow_menu', 'numDrops': 9000, 'minSpeed': .2, 'maxSpeed': .35, 'dropSize': .6,
                         'turbulence': True}
    if pack == 'autumn':
        sun.update(elevation='11', azimuth='235', brightness='1.05', skyBrightness='20', colorize='0.98 0.7 0.42 1',
                   colorizeAmount='0.9', ambientScale='0.78 0.64 0.52 1', sunScale='1 0.74 0.44 1', fogScale='0.95 0.72 0.5 1')
        info.update(fogColor='0.86 0.7 0.52 1', fogDensity='0.00032', canvasClearColor='214 176 132 255')
        clouds.update(coverage='0.45', exposure='0.8', baseColor='1 0.86 0.72 1')
    return env
