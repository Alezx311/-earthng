"""Offline base map of the planet picker: Natural Earth 1:10m (public domain) as 256 px tiles.

    python earthng/build_basemap.py            # builds out/basemap/<VERSION>/ once
    python earthng/build_basemap.py --force    # again

Web Mercator tiles z0..MAX_ZOOM with land, lakes, rivers, urban areas and borders in the
OpenStreetMap palette. Labels are not baked: the widget draws them (crisp, in the UI's
language) from labels.json. Open-sea tiles are not written (the widget fills the sea colour)
and identical tiles share one file: index.json maps "z/x/y" to a file name.

Sources (downloaded once to data/naturalearth/):
https://github.com/nvkelso/natural-earth-vector/tree/master/geojson
"""
import argparse
import hashlib
import io
import json
import math
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import pickle
import shutil
import urllib.request

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / 'data/naturalearth'
OUT = ROOT / 'out/basemap'
VERSION = '3'
MAX_ZOOM = 8
TILE = 256
SUPER = 8      # tiles per side rendered together (one polygon pass for 64 tiles)
SS = 2         # supersampling: polygons and lines are drawn at twice the size, then reduced
URL = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/{}.geojson'
LAYERS = ['ne_10m_land', 'ne_10m_lakes', 'ne_10m_rivers_lake_centerlines', 'ne_10m_urban_areas',
          'ne_10m_admin_0_boundary_lines_land', 'ne_10m_admin_1_states_provinces_lines',
          'ne_10m_populated_places', 'ne_10m_admin_0_countries']

# OpenStreetMap Carto-like colours; SEA must match the widget's fill (planetMap.SEA)
SEA = (170, 211, 223)
LAND = (242, 239, 233)
URBAN = (224, 219, 214)
RIVER = (150, 196, 214)
ADMIN0 = (160, 128, 170)
ADMIN1 = (190, 175, 196)
MAX_LAT = 85.0511


def fetch():
    SRC.mkdir(parents=True, exist_ok=True)
    for name in LAYERS:
        path = SRC / f'{name}.geojson'
        if not path.exists():
            print('download', name)
            tmp = path.with_suffix('.tmp')
            with urllib.request.urlopen(URL.format(name), timeout=120) as r, open(tmp, 'wb') as f:
                shutil.copyfileobj(r, f)
            tmp.replace(path)


def read(name):
    return json.loads((SRC / f'{name}.geojson').read_text(encoding='utf8'))['features']


def rings(geometry):
    """Outer and inner rings (polygons) or parts (lines) as lon/lat float arrays."""
    if not geometry:
        return []
    kind, c = geometry['type'], geometry['coordinates']
    if kind == 'Polygon':
        return [c]
    if kind == 'MultiPolygon':
        return c
    if kind == 'LineString':
        return [[c]]
    if kind == 'MultiLineString':
        return [[part] for part in c]
    return []


def shapes(features, keep=lambda p: True, minzoom=lambda p: 0):
    """[(minzoom, box (w, s, e, n), [ring arrays])] - one entry per polygon or line part."""
    out = []
    for f in features:
        p = f['properties']
        if not keep(p):
            continue
        z = minzoom(p)
        for parts in rings(f['geometry']):
            arrays = [np.asarray(r, dtype=np.float64)[:, :2] for r in parts if len(r) >= 2]
            if not arrays:
                continue
            a = arrays[0]
            out.append((z, (a[:, 0].min(), a[:, 1].min(), a[:, 0].max(), a[:, 1].max()), arrays))
    return out


def number(p, *keys, default=0.0):
    for k in keys:
        v = p.get(k)
        if v is not None:
            return float(v)
    return default


def prepare():
    """Geometry for the renderers, pickled once (each worker process loads the pickle)."""
    path = OUT / f'geometry-{VERSION}.pickle'
    if path.exists():
        return path
    fetch()
    print('preparing geometry')
    geo = {
        'land': shapes(read('ne_10m_land')),
        'lakes': shapes(read('ne_10m_lakes'), minzoom=lambda p: number(p, 'min_zoom') - 1),
        'urban': shapes(read('ne_10m_urban_areas'), minzoom=lambda p: 4),
        'rivers': shapes(read('ne_10m_rivers_lake_centerlines'), keep=lambda p: p.get('featurecla') != 'Lake Centerline',
                         minzoom=lambda p: number(p, 'min_zoom') - 1),
        'admin1': shapes(read('ne_10m_admin_1_states_provinces_lines'), minzoom=lambda p: max(4, number(p, 'MIN_ZOOM') - 1)),
        'admin0': shapes(read('ne_10m_admin_0_boundary_lines_land')),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_bytes(pickle.dumps(geo, protocol=pickle.HIGHEST_PROTOCOL))
    tmp.replace(path)
    return path


def merc(lon, lat, scale):
    lat = np.clip(lat, -MAX_LAT, MAX_LAT)
    s = np.sin(np.radians(lat))
    return (lon + 180) / 360 * scale, (0.5 - np.log((1 + s) / (1 - s)) / (4 * math.pi)) * scale


def unmerc_lat(y, scale):
    m = math.pi * (1 - 2 * y / scale)
    return math.degrees(math.atan(math.sinh(m)))


GEO = None


def load(path):
    global GEO
    GEO = pickle.loads(Path(path).read_bytes())


def render(job):
    """One SUPER x SUPER block of tiles at zoom z -> {(x, y): png bytes or None (open sea)}."""
    z, bx, by = job
    n = 2 ** z
    side = min(SUPER, n)
    scale = TILE * n * SS
    px0, py0 = bx * side * TILE * SS, by * side * TILE * SS
    size = side * TILE * SS
    west, east = px0 / scale * 360 - 180, (px0 + size) / scale * 360 - 180
    north, south = unmerc_lat(py0, scale), unmerc_lat(py0 + size, scale)
    pad = (east - west) * 0.02
    image = Image.new('RGB', (size, size), SEA)
    draw = ImageDraw.Draw(image)
    land = np.zeros((side, side), bool)

    def visible(entry):
        mz, (w, s, e, nn), _ = entry
        return mz <= z and w <= east + pad and e >= west - pad and s <= north + pad and nn >= south - pad

    def points(a):
        x, y = merc(a[:, 0], a[:, 1], scale)
        # drop points under half a pixel apart: PIL cost is per point
        xy = np.round(np.stack([x - px0, y - py0], 1) * 2) / 2
        keep = np.ones(len(xy), bool)
        keep[1:] = np.any(xy[1:] != xy[:-1], 1)
        return [tuple(p) for p in xy[keep]]

    def fill(name, colour, holes=None):
        for entry in GEO[name]:
            if visible(entry):
                arrays = entry[2]
                outer = points(arrays[0])
                if len(outer) >= 3:
                    draw.polygon(outer, fill=colour)
                    for hole in arrays[1:]:
                        pts = points(hole)
                        if len(pts) >= 3:
                            draw.polygon(pts, fill=holes)

    def stroke(name, colour, width):
        for entry in GEO[name]:
            if visible(entry):
                for a in entry[2]:
                    pts = points(a)
                    if len(pts) >= 2:
                        draw.line(pts, fill=colour, width=max(1, round(width * SS)), joint='curve')

    fill('land', LAND, SEA)
    # land at all? (tiles of open sea are not written)
    small = np.asarray(image.resize((side * 8, side * 8), Image.NEAREST))
    land = np.any((small != SEA).any(2).reshape(side, 8, side, 8), axis=(1, 3))
    if z >= 4:
        fill('urban', URBAN)
    fill('lakes', SEA, LAND)
    # thin, as OpenStreetMap draws them: past MAX_ZOOM the tiles are stretched, lines with them
    stroke('rivers', RIVER, 0.5 + 0.2 * max(0, z - 3))
    if z >= 4:
        stroke('admin1', ADMIN1, 0.6)
    stroke('admin0', ADMIN0, 0.6 + 0.12 * z)
    image = image.reduce(SS)
    out = {}
    for ty in range(side):
        for tx in range(side):
            if not land[ty, tx]:
                out[(bx * side + tx, by * side + ty)] = None
                continue
            tile = image.crop((tx * TILE, ty * TILE, (tx + 1) * TILE, (ty + 1) * TILE))
            tile = tile.quantize(colors=48, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
            buf = io.BytesIO()
            tile.save(buf, 'PNG', optimize=True)
            out[(bx * side + tx, by * side + ty)] = buf.getvalue()
    return z, out


def labels():
    """Cities and countries for the widget: [lat, lon, min zoom, English name, Ukrainian name]."""
    places = []
    for f in read('ne_10m_populated_places'):
        p = f['properties']
        lon, lat = f['geometry']['coordinates'][:2]
        en = p.get('NAME_EN') or p.get('NAME')
        uk = p.get('NAME_UK') or en
        capital = 1 if p.get('ADM0CAP') else 0
        places.append([round(lat, 4), round(lon, 4), round(number(p, 'MIN_ZOOM', default=8), 1), en, uk,
                       capital, int(number(p, 'POP_MAX'))])
    places.sort(key=lambda r: (r[2], -r[6]))
    countries = []
    for f in read('ne_10m_admin_0_countries'):
        p = f['properties']
        en = p.get('NAME_EN') or p.get('NAME')
        countries.append([round(number(p, 'LABEL_Y'), 4), round(number(p, 'LABEL_X'), 4),
                          round(number(p, 'MIN_LABEL', default=3), 1), en, p.get('NAME_UK') or en])
    countries.sort(key=lambda r: r[2])
    return {'places': places, 'countries': countries}


def build(force=False, jobs=6):
    target = OUT / VERSION
    if (target / 'index.json').exists() and not force:
        return target
    geometry = prepare()
    if target.exists():
        shutil.rmtree(target)
    (target / 't').mkdir(parents=True)
    work = []
    for z in range(MAX_ZOOM + 1):
        blocks = max(1, 2 ** z // SUPER)
        work += [(z, bx, by) for by in range(blocks) for bx in range(blocks)]
    # the heavy (deep) blocks first, so the pool ends together
    work.sort(key=lambda j: -j[0])
    index, files, written = {}, set(), 0
    with ProcessPoolExecutor(jobs, initializer=load, initargs=(str(geometry),)) as pool:
        for done, (z, tiles) in enumerate(pool.map(render, work, chunksize=1), 1):
            for (x, y), png in tiles.items():
                if png is None:
                    continue
                name = hashlib.sha1(png).hexdigest()[:16]
                if name not in files:
                    files.add(name)
                    (target / 't' / f'{name}.png').write_bytes(png)
                    written += len(png)
                index[f'{z}/{x}/{y}'] = name
            if done % 64 == 0 or done == len(work):
                print(f'{done}/{len(work)} blocks, {len(index)} tiles, {len(files)} files, {written / 2**20:.1f} MB')
    (target / 'labels.json').write_text(json.dumps(labels(), ensure_ascii=False, separators=(',', ':')), encoding='utf8')
    (target / 'index.json').write_text(json.dumps({'maxZoom': MAX_ZOOM, 'sea': SEA, 'tiles': index},
                                                  separators=(',', ':')), encoding='utf8')
    return target


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--force', action='store_true')
    p.add_argument('--jobs', type=int, default=6)
    a = p.parse_args()
    print(build(a.force, a.jobs))


if __name__ == '__main__':
    main()
