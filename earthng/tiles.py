"""Streamed tiles of a prepared region (region.py): one square of the region grid built with
roadgen v2 from the region's network and elevation, delivered as ProceduralMesh pieces.

    C:/js/Earth2Road/.venv/Scripts/python.exe earthng/tiles.py out/region/kyiv-ns --tile 0 0 --out out/tiles

A tile owns the square [i*T, (i+1)*T) x [j*T, (j+1)*T) of the region frame (metres east/north
of the region centre, = BeamNG x/y). It is built with a halo of context around it; only the
objects whose 250 m export cell (or own position) lies in the owned square are kept, so each
place is delivered by exactly one tile. Because the network is cut from the region network
without renormalising and the elevation comes from the region, the halo sees the same inputs
in every tile and the borders of neighbouring tiles meet.
"""
import argparse
import copy
import json
import math
import os
from pathlib import Path
import pickle
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402,F401  (puts Earth2Road on sys.path)
import osm_tiles  # noqa: E402
import s1  # noqa: E402
import direct  # noqa: E402

TILE = 500.0
HALO = 150.0    # context around the owned square: 80 m left 0.23% of border probes > 5 cm (route-07), 150 m: see docs
# compact kerbs (no 8 mm bevel; the visible kerb is its collider): sidewalks 25.5 -> 9.6 s on 1 km2
OPTIMIZATION = 'balanced+kerbs'
LEVEL_ID = 'e2r_stream'   # one material namespace for all tiles: textures and warm-up are shared


class Frame:
    """The region's projection: lon/lat <-> network x/y (as sumolib) and the region frame."""

    def __init__(self, rdir):
        import pyproj
        self.dir = Path(rdir)
        self.meta = json.loads((self.dir / 'region.json').read_text(encoding='utf8'))
        head = (self.dir / self.meta['net']).open('rb').read(1 << 16).decode('utf8', 'replace')
        loc = head[head.index('<location'):]
        loc = loc[:loc.index('/>')]
        attr = dict(part.split('="', 1) for part in loc.replace('<location', '').strip().split('" ') if '="' in part)
        attr = {k.strip(): v.rstrip('"') for k, v in attr.items()}
        self.offset = tuple(float(v) for v in attr['netOffset'].split(','))
        self.proj = pyproj.Proj(attr['projParameter'])
        self.cx, self.cy = self.xy(*self.meta['center'])

    def xy(self, lon, lat):
        x, y = self.proj(lon, lat)
        return x + self.offset[0], y + self.offset[1]

    def lonlat(self, x, y):
        return self.proj(x - self.offset[0], y - self.offset[1], inverse=True)

    def local(self, lon, lat):
        x, y = self.xy(lon, lat)
        return x - self.cx, y - self.cy

    def tile_of(self, lon, lat):
        x, y = self.local(lon, lat)
        return math.floor(x / TILE), math.floor(y / TILE)

    def rect(self, i, j, pad=0.0):
        return (i * TILE - pad, j * TILE - pad, (i + 1) * TILE + pad, (j + 1) * TILE + pad)

    def geo_bbox(self, rect):
        corners = [self.lonlat(self.cx + x, self.cy + y) for x in rect[0::2] for y in rect[1::2]]
        return [round(min(c[0] for c in corners), 6), round(min(c[1] for c in corners), 6),
                round(max(c[0] for c in corners), 6), round(max(c[1] for c in corners), 6)]


_ELEVATION = {}


def elevation(rdir):
    """The region elevation (loaded once per worker process)."""
    key = str(rdir)
    if key not in _ELEVATION:
        with open(Path(rdir) / 'elevation.pkl', 'rb') as f:
            _ELEVATION[key] = pickle.load(f)
    return _ELEVATION[key]


def region_lift(rdir):
    """One vertical offset for the whole region: the carrier level has a collision plane at z=0."""
    e = elevation(rdir)
    return round(12.0 - min(e['node_heights'].values()), 1)


BLOCK, BLOCK_PAD = 2000.0, 500.0


def _netconvert_cut(src, frame, rect, out):
    """Edges of src inside rect (region frame), coordinates unchanged. netconvert recomputes the
    junction shapes of a loaded network; with the build's junction options they match the region
    network to ~1 cm, and two cuts agree exactly where both contain a junction's edges."""
    from akadem_maps import runtime
    x0, y0, x1, y1 = rect
    tmp = Path(str(out) + f'.{os.getpid()}.tmp')
    cmd = [str(runtime.sumo_binary('netconvert')), '--sumo-net-file', str(src),
           '--keep-edges.in-boundary', f'{frame.cx + x0},{frame.cy + y0},{frame.cx + x1},{frame.cy + y1}',
           '--offset.disable-normalization', 'true', '--junctions.corner-detail', '5',
           '--default.connection.cont-pos', '0', '--no-turnarounds.except-deadend', 'true',
           '--output-file', str(tmp), '--no-warnings', 'true']
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors='replace')
    if res.returncode != 0:
        if 'No edges loaded' in res.stderr:
            raise NoRoads(f'no roads in {rect}')
        raise RuntimeError(f'netconvert cut failed: {res.stderr[-800:]}')
    os.replace(tmp, out)
    return out


class NoRoads(Exception):
    """A tile without any road (river, lake, forest): delivered empty, nothing to drive on."""


def cut_network(frame, rect, out):
    """The region network inside rect. Loading the whole region network costs ~20 s, so it is
    first cut into cached 2 km blocks (+500 m) and the tile is cut from its block."""
    bi, bj = math.floor((rect[0] + rect[2]) / 2 / BLOCK), math.floor((rect[1] + rect[3]) / 2 / BLOCK)
    brect = (bi * BLOCK - BLOCK_PAD, bj * BLOCK - BLOCK_PAD, (bi + 1) * BLOCK + BLOCK_PAD, (bj + 1) * BLOCK + BLOCK_PAD)
    assert brect[0] <= rect[0] and brect[1] <= rect[1] and rect[2] <= brect[2] and rect[3] <= brect[3]
    block = frame.dir / 'blocks' / f'{bi}_{bj}.net.xml'
    if not block.exists():
        block.parent.mkdir(exist_ok=True)
        _netconvert_cut(frame.dir / frame.meta['net'], frame, brect, block)
    return _netconvert_cut(block, frame, rect, out)


def build(rdir, i, j, jobdir, cache, tiles_src=paths.TILES, center=None, emit=lambda *a, **k: None):
    """Build tile (i, j) into jobdir. Returns (level_dir, timings)."""
    from akadem_maps.context import BuildContext, RESOURCES, write_json
    from akadem_maps.world import validate_config
    from akadem_maps.core import prepare, osm_extract
    from akadem_maps import sources
    from akadem_maps.adapters.beamng.export_beamng import export_map

    timings = {}
    t0 = time.perf_counter()
    frame = Frame(rdir)
    jobdir = Path(jobdir)
    jobdir.mkdir(parents=True, exist_ok=True)
    halo = frame.rect(i, j, HALO)
    bbox = frame.geo_bbox(halo)
    net = cut_network(frame, halo, jobdir / 'tile.net.xml')
    timings['network'] = round(time.perf_counter() - t0, 2)
    t = time.perf_counter()
    index = json.loads((Path(tiles_src) / 'index.json').read_text(encoding='utf8'))
    hit = osm_tiles.tile_for(index, bbox)
    if not hit:
        raise RuntimeError(f'no_data: no OSM source tile covers {bbox}')
    cache = Path(cache).resolve()
    raw = cache
    osm = jobdir / 'source.osm'
    osm_extract.extract(Path(tiles_src) / hit[1]['file'], osm, bbox)
    timings['osm'] = round(time.perf_counter() - t, 2)
    lon, lat = center or frame.lonlat(frame.cx + (i + .5) * TILE, frame.cy + (j + .5) * TILE)
    cfg = validate_config({'id': LEVEL_ID, 'name': f'{frame.meta["id"]} {i} {j}', 'bbox': bbox,
                           'center': [round(lon, 6), round(lat, 6)], 'region_profile': 'ukraine',
                           'road_geometry': {'mode': 'v2'}})
    world = jobdir / 'world'
    world.mkdir(exist_ok=True)
    context = BuildContext(world, raw, jobdir, False, RESOURCES, emit=emit, diagnostics=jobdir / 'logs')
    context.skip_surface_audit = True
    region_elev = elevation(rdir)

    def elevation_for(ways):
        profiles = {w: p for w, p in region_elev['profiles'].items() if w in ways}
        way_refs = {w: r for w, r in region_elev['way_refs'].items() if w in ways}
        return (profiles, region_elev['ground_nodes'], region_elev['node_heights'], way_refs,
                copy.deepcopy(region_elev['profile_report']), copy.deepcopy(region_elev['elevation_report']))
    context.region = {'net': net, 'center': frame.meta['center'], 'elevation': elevation_for}
    t = time.perf_counter()
    root = sources.validate_osm(osm)
    context.build.mkdir(parents=True, exist_ok=True)
    write_json(context.build / 'sources.json', {'source': 'earthng tile', 'bbox': bbox, 'config': cfg,
                                                'license': 'ODbL-1.0; © OpenStreetMap contributors'})
    prepare.build(cfg, root, context)
    write_json(world / 'config.json', cfg)
    timings['build'] = round(time.perf_counter() - t, 2)
    t = time.perf_counter()
    sink = direct.StreamSink(cfg['id'])
    export_map(cfg['id'], jobdir / 'level', level_id=cfg['id'], world_dir=world, namespace=True,
               package_zip=False, emit=emit, sink=sink, optimization=OPTIMIZATION)
    timings['export'] = round(time.perf_counter() - t, 2)
    timings['export_pieces'] = round(sink.seconds, 2)
    return jobdir / 'level/levels' / cfg['id'], timings


def owned(pos, rect):
    return rect[0] <= pos[0] < rect[2] and rect[1] <= pos[1] < rect[3]


def deliver(rdir, i, j, level_dir, dest_root, lift):
    """Owned objects of a built tile -> <dest_root>/<region>/: pieces, shared art, tiles/<i>_<j>.json.
    Paths inside pieces and materials point at /e2r_stream/<region>/."""
    frame = Frame(rdir)
    rid = frame.meta['id']
    rect = frame.rect(i, j)
    s1.STREAM = f'e2r_stream/{rid}'
    work = Path(level_dir).parent.parent.parent / 'convert'
    work.mkdir(exist_ok=True)
    spawn, items, files, materials = s1.tileset(Path(level_dir), 'proc', work, direct.MAX_TRIS, 'bin')
    keep = [it for it in items if owned(it['pos'], rect)]
    dest = Path(dest_root) / rid
    used = set()
    for it in keep:
        it['name'] = f't{i}_{j}_{it["name"]}'
        it['pos'] = [it['pos'][0], it['pos'][1], it['pos'][2] + lift]
        it.pop('zmin', None)
        if it['kind'] == 'proc':
            used.add(it['shape'].split(f'/{s1.STREAM}/', 1)[1])
    for rel, src in files.items():
        if rel.startswith('art/'):
            target = dest / rel
            if target.exists() and target.stat().st_size == Path(src).stat().st_size:
                continue
        elif rel not in used:
            continue
        else:
            target = dest / f'tiles/{i}_{j}' / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    for it in keep:
        if it['kind'] == 'proc':
            rel = it['shape'].split(f'/{s1.STREAM}/', 1)[1]
            it['shape'] = f'/{s1.STREAM}/tiles/{i}_{j}/{rel}'
    (dest / 'tiles').mkdir(parents=True, exist_ok=True)
    # not *.materials.json: the game's util_materialHotReload re-parses every such file that appears
    # in its file system and re-creates ~80 existing materials per delivered tile (1-3 s frames,
    # route-05/06). The extension loads only the definitions it has not seen (world.lua).
    mats = dest / f'tiles/{i}_{j}.mats.json'
    mats.write_text(materials, encoding='utf8')
    sp = spawn['position']
    manifest = {'tile': [i, j], 'rect': rect, 'lift': lift, 'materials': f'/{s1.STREAM}/tiles/{i}_{j}.mats.json',
                'mats': sorted({m for it in keep for m in it.get('mats', [])}), 'items': keep,
                'triangles': sum(it['triangles'] for it in keep),
                'spawn': {'pos': [sp[0], sp[1], sp[2] + lift + 0.5], 'yaw': s1.yaw(spawn.get('rotationMatrix'))}}
    tmp = dest / f'tiles/{i}_{j}.json.tmp'
    tmp.write_text(json.dumps(manifest), encoding='utf8')
    os.replace(tmp, dest / f'tiles/{i}_{j}.json')
    return manifest


def deliver_empty(rdir, i, j, dest_root, lift):
    """A road-less tile: a manifest without items, so the game counts it as loaded.
    (No ground either yet: v2 builds the ground around roads.)"""
    frame = Frame(rdir)
    rid = frame.meta['id']
    dest = Path(dest_root) / rid / 'tiles'
    dest.mkdir(parents=True, exist_ok=True)
    rect = frame.rect(i, j)
    manifest = {'tile': [i, j], 'rect': rect, 'lift': lift, 'empty': True, 'materials': None, 'mats': [],
                'items': [], 'triangles': 0,
                'spawn': {'pos': [(rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2, lift], 'yaw': 0}}
    tmp = dest / f'{i}_{j}.json.tmp'
    tmp.write_text(json.dumps(manifest), encoding='utf8')
    os.replace(tmp, dest / f'{i}_{j}.json')
    return manifest


def run(rdir, i, j, work, dest_root, cache, center=None):
    """Build + deliver one tile (a worker job). Returns a small summary."""
    started = time.perf_counter()
    jobdir = Path(work) / f'{i}_{j}'
    if jobdir.exists():
        shutil.rmtree(jobdir, ignore_errors=True)
    try:
        level_dir, timings = build(rdir, i, j, jobdir, cache, center=center)
    except NoRoads:
        manifest = deliver_empty(rdir, i, j, dest_root, region_lift(rdir))
        return {'tile': [i, j], 'items': 0, 'triangles': 0, 'empty': True,
                'timings': {'total': round(time.perf_counter() - started, 2)}}
    t = time.perf_counter()
    manifest = deliver(rdir, i, j, level_dir, dest_root, region_lift(rdir))
    timings['deliver'] = round(time.perf_counter() - t, 2)
    timings['total'] = round(time.perf_counter() - started, 2)
    (jobdir / 'timings.json').write_text(json.dumps(timings), encoding='utf8')
    return {'tile': [i, j], 'items': len(manifest['items']), 'triangles': manifest['triangles'], 'timings': timings}


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('region')
    p.add_argument('--tile', type=int, nargs=2, action='append', required=True, metavar=('I', 'J'))
    p.add_argument('--out', default=str(paths.OUT / 'tiles'))
    p.add_argument('--dest', default=None, help='delivery root (default <out>/stream)')
    p.add_argument('--cache', default=str(paths.OUT / 'world' / 'cache'))
    p.add_argument('--halo', type=float, default=HALO, help='context around the owned square, m (experiments)')
    a = p.parse_args()
    globals()['HALO'] = a.halo
    for i, j in a.tile:
        print(json.dumps(run(a.region, i, j, a.out, a.dest or Path(a.out) / 'stream', a.cache)), flush=True)


if __name__ == '__main__':
    main()
