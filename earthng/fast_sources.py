"""Cold-start OSM index for the streaming generator. No SUMO or generated meshes.

The index owns complete OSM ways and junction mouths, not tile-local decisions.
SQLite/RTree readers are independent and bounded; workers never copy a city graph.
"""
from collections import Counter, defaultdict, OrderedDict
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time

import numpy as np
from PIL import Image
import pyproj
import shapely
from shapely.geometry import LineString, MultiPoint, Point, shape
from shapely.ops import transform

VERSION = 'fast-9'          # source index format (keys fast-index/<id>); 9: frame per landing
GEOMETRY = 'fast-33'         # mesh/material semantics; bump to reject tiles built by older code
TILE = 500.0
ROAD_LIFT = 0.12            # road surface over its profile (the ground mesh sits 0.25 under the DEM)
LIFT = 20.0                 # absolute DEM heights + lift; never the carrier plane
CRS = 'EPSG:32636'          # the frame before fast-9 (Kyiv): old replay routes are in it
ORIGIN = (324000., 5590000.)
DRIVABLE = {'motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'residential',
            'unclassified', 'service', 'living_street', 'road'}


def number(value, default):
    try:
        return float(str(value).split(';')[0].replace(' m', '').replace(',', '.'))
    except (ValueError, TypeError):
        return default


def road_width(tags):
    klass = tags.get('highway', '').replace('_link', '')
    lanes = number(tags.get('lanes'), 1 if tags.get('oneway') in ('yes', '1', '-1') else 2)
    fallback = 4 if klass in ('service', 'living_street') else max(3.2, lanes * 3.3)
    return min(35., max(2.5, number(tags.get('width'), fallback)))


def utm_crs(lon, lat):
    zone = int((lon + 180) // 6) % 60 + 1
    return f'EPSG:{(32600 if lat >= 0 else 32700) + zone}'


class Frame:
    """World metres = UTM metres minus an origin. Every index has its own frame, at the landing
    (Frame.at): BeamNG keeps positions in float32, and with one origin for the country Uzhhorod
    lay 613 km from Kyiv's (steps of 6 cm); the UTM zone of the landing keeps the scale true in
    the west as well."""
    def __init__(self, crs=CRS, origin=ORIGIN):
        self.crs = crs
        self.forward = pyproj.Transformer.from_crs('EPSG:4326', crs, always_xy=True)
        self.inverse = pyproj.Transformer.from_crs(crs, 'EPSG:4326', always_xy=True)
        self.cx, self.cy = float(origin[0]), float(origin[1])

    @classmethod
    def at(cls, lon, lat):
        """The frame centred on a point (whole metres), in the point's UTM zone."""
        crs = utm_crs(lon, lat)
        x, y = pyproj.Transformer.from_crs('EPSG:4326', crs, always_xy=True).transform(lon, lat)
        return cls(crs, (round(x), round(y)))

    @classmethod
    def of(cls, meta):
        """The frame of an index (region.json, or its source index)."""
        spec = meta.get('frame')
        return cls(spec['crs'], spec['origin']) if spec else cls()

    def spec(self):
        return {'crs': self.crs, 'origin': [self.cx, self.cy]}

    def local(self, lon, lat):
        x, y = self.forward.transform(lon, lat)
        return np.asarray(x) - self.cx, np.asarray(y) - self.cy

    def lonlat(self, x, y):
        return self.inverse.transform(x, y)

    def geographic(self, x, y):
        return self.inverse.transform(np.asarray(x) + self.cx, np.asarray(y) + self.cy)

    def tile_of(self, lon, lat):
        x, y = self.local(lon, lat)
        return math.floor(x / TILE), math.floor(y / TILE)

    def rect(self, i, j, pad=0.):
        return i*TILE-pad, j*TILE-pad, (i+1)*TILE+pad, (j+1)*TILE+pad

    def geo_bbox(self, rect):
        x0, y0, x1, y1 = rect
        lon, lat = self.geographic([x0,x0,x1,x1], [y0,y1,y0,y1])
        return [float(min(lon)), float(min(lat)), float(max(lon)), float(max(lat))]


# A package larger than this is indexed as a window round the landing (Kyiv oblast: ~2900
# source tiles; the whole of it would take minutes against the 120 s start).
WINDOW_TILES = 300
WINDOW_SNAP = 0.2           # window centres on this grid: nearby landings share an index
WINDOW_WAYS = 1_200_000     # OSM ways a window may hold: the Kyiv package (162 tiles) has 1.17 M
WINDOW_RINGS = (5, 20)      # rings of tiles round the centre: at least ~56 x 55 km, at most ~210 x 220


def window_of(index, point):
    """The tiles of a large package that a landing at point indexes, and the window box. The
    window grows ring by ring (1.5 cells of longitude per cell of latitude: about square in km)
    while it holds fewer ways than the Kyiv package, so a rural window covers far more ground
    for the same indexing time."""
    if point is None or len(index['tiles']) <= WINDOW_TILES:
        return index, None
    lon, lat = point
    cell = index.get('cell', .05)
    centre = [round(round(lon / WINDOW_SNAP) * WINDOW_SNAP, 6), round(round(lat / WINDOW_SNAP) * WINDOW_SNAP, 6)]
    cx, cy = centre[0] / cell, centre[1] / cell
    entries = []
    for k, v in index['tiles'].items():
        x, y = (v['cell'][0] + v['cell'][2]) / 2 / cell, (v['cell'][1] + v['cell'][3]) / 2 / cell
        entries.append((max(abs(x - cx) / 1.5, abs(y - cy)), k, v))
    tiles, ways = {}, 0
    for r in range(WINDOW_RINGS[0], WINDOW_RINGS[1] + 1):
        ring = {k: v for d, k, v in entries if d <= r}
        total = sum(v.get('ways', WINDOW_WAYS) for v in ring.values())
        if tiles and total > WINDOW_WAYS:
            break
        tiles, ways = ring, total
        if total >= WINDOW_WAYS:
            break
    if not tiles:
        raise ValueError(f'no_osm: {lon:.4f}, {lat:.4f} is outside the source package')
    xs = [c for v in tiles.values() for c in (v['cell'][0], v['cell'][2])]
    ys = [c for v in tiles.values() for c in (v['cell'][1], v['cell'][3])]
    window = [round(min(xs), 6), round(min(ys), 6), round(max(xs), 6), round(max(ys), 6)]
    return dict(index, tiles=tiles, package_box=window, window=window, centre=centre), window


def window_box(index, box):
    """The tiles of a package under a lon/lat box (an authoring grid region), centred on it."""
    tiles = {k: v for k, v in index['tiles'].items()
             if v['cell'][0] < box[2] and v['cell'][2] > box[0] and v['cell'][1] < box[3] and v['cell'][3] > box[1]}
    if not tiles:
        raise ValueError(f'no_osm: {box} is outside the source package')
    xs = [c for v in tiles.values() for c in (v['cell'][0], v['cell'][2])]
    ys = [c for v in tiles.values() for c in (v['cell'][1], v['cell'][3])]
    window = [round(min(xs), 6), round(min(ys), 6), round(max(xs), 6), round(max(ys), 6)]
    centre = [round((box[0] + box[2]) / 2, 6), round((box[1] + box[3]) / 2, 6)]
    return dict(index, tiles=tiles, package_box=window, window=window, centre=centre), window


def dem_tiles(box):
    """z12 Terrarium tiles under a lon/lat box, one tile of margin (fast_dem.audit's rule)."""
    w, s, e, n = box
    tile = lambda lon, lat: ((lon + 180) / 360 * 4096, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 4096)
    x0, y0 = tile(w, n); x1, y1 = tile(e, s)
    return [(x, y) for x in range(math.floor(x0) - 1, math.floor(x1) + 2) for y in range(math.floor(y0) - 1, math.floor(y1) + 2)]


def source_identity(source, dem, point=None, box=None):
    """Index checksum (of the window's tiles) plus the DEM files under it (name, size, mtime):
    old geometry cannot survive source replacement. Contents were hashed before: 200 MB of
    PNG per landing for the oblast."""
    source, dem = Path(source), Path(dem)
    index = json.loads((source / 'index.json').read_bytes())
    index, window = window_box(index, box) if box is not None else window_of(index, point)
    # the frame's origin: the window's centre (the landing snapped to WINDOW_SNAP, so nearby
    # landings share the index and its tiles; at most ~11 km from the car), else the package's
    if 'centre' not in index:
        w, s, e, n = index['package_box']
        index = dict(index, centre=[round((w + e) / 2, 6), round((s + n) / 2, 6)])
    index = dict(index, frame=Frame.at(*index['centre']).spec())
    h = hashlib.sha256(VERSION.encode() + json.dumps(index, sort_keys=True).encode())
    for x, y in dem_tiles(index['package_box']):
        p = dem / 'terrain' / '12' / str(x) / f'{y}.png'
        if p.exists():
            st = p.stat()
            h.update(f'{x}/{y}:{st.st_size}:{st.st_mtime_ns}'.encode())
    return VERSION + '-' + h.hexdigest()[:16], index


class Terrain:
    """Vectorized, offline Terrarium samples with a tile-independent pixel grid.

    Bilinear sampling crosses PNG boundaries. Missing tiles fail explicitly.
    Roads use a symmetric 5-tap spatial filter; no optional network downloads.
    """
    def __init__(self, cache, frame=None):
        self.cache, self.frame = Path(cache), frame or Frame()
        self.images = OrderedDict()

    def image(self, a, b):
        key = (a, b)
        if key not in self.images:
            path = self.cache / 'terrain' / '12' / str(a) / f'{b}.png'
            if not path.exists():
                raise FileNotFoundError(f'no_dem: {path}')
            with Image.open(path) as im:
                rgb = np.asarray(im.convert('RGB'), dtype=float)
            self.images[key] = rgb[:,:,0]*256 + rgb[:,:,1] + rgb[:,:,2]/256 - 32768
            if len(self.images) > 24:
                self.images.popitem(last=False)
        self.images.move_to_end(key)
        return self.images[key]

    def pixels(self, x, y):
        tx, ty = x // 256, y // 256
        a0, b0 = int(tx[0]), int(ty[0])
        if (tx == a0).all() and (ty == b0).all():     # the usual case: one PNG
            return self.image(a0, b0)[y % 256, x % 256]
        out = np.empty(len(x), dtype=float)
        keys = tx * 65536 + ty                          # z12 tile indices are 0..4095
        for key in np.unique(keys).tolist():
            mask = keys == key
            a, b = divmod(key, 65536)
            out[mask] = self.image(a, b)[y[mask] % 256, x[mask] % 256]
        return out

    def sample(self, xy, smooth=False):
        xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        if not len(xy):
            return np.array([])
        lon, lat = self.frame.geographic(xy[:,0], xy[:,1])
        scale = 256 * 2**12
        x = (lon+180)/360*scale
        y = (1-np.arcsinh(np.tan(np.radians(lat)))/np.pi)/2*scale
        ix, iy = np.floor(x).astype(int), np.floor(y).astype(int)
        u, v = x-ix, y-iy
        offsets = [(0,0,1.)] if not smooth else [(0,0,.5),(-1,0,.125),(1,0,.125),(0,-1,.125),(0,1,.125)]
        z = np.zeros(len(x))
        n = len(x)
        for dx, dy, weight in offsets:
            # the four corners in one lookup (one pass over the PNG keys)
            p = self.pixels(np.concatenate([ix+dx, ix+dx+1, ix+dx, ix+dx+1]),
                            np.concatenate([iy+dy, iy+dy, iy+dy+1, iy+dy+1]))
            z += weight * ((p[:n]*(1-u) + p[n:2*n]*u)*(1-v) + (p[2*n:3*n]*(1-u) + p[3*n:]*u)*v)
        if not np.isfinite(z).all():
            raise ValueError('non-finite DEM')
        return z + LIFT


def read_roads(path, sha256):
    """Drivable ways of one source tile: {way id: (tags, [(node, lon, lat), ...])}."""
    import osmium
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != sha256:
        raise ValueError(f'OSM checksum mismatch: {path}')
    roads = {}

    class Reader(osmium.SimpleHandler):
        def way(self, w):
            h = w.tags.get('highway', '')
            if not (h in DRIVABLE or h.endswith('_link')) or w.id in roads:
                return
            if w.tags.get('area') == 'yes' or w.tags.get('access') == 'no':
                return
            nodes = [(n.ref, n.lon, n.lat) for n in w.nodes if n.location.valid()]
            if len(nodes) != len(w.nodes):
                raise ValueError(f'incomplete OSM way {w.id}')
            if len(nodes) >= 2:
                roads[w.id] = (dict(w.tags), nodes)

    Reader().apply_file(str(path), locations=True)
    return roads


class Polylines:
    """Many 1-D functions (xp[k], fp[k]) evaluated at once: value(x, k) == np.interp(x, xp[k], fp[k])
    bit for bit (the same slope formula), without a Python step per call."""
    def __init__(self, xp):
        self.n = np.array([len(a) for a in xp])
        self.start = np.concatenate([[0], np.cumsum(self.n)[:-1]])
        self.X = np.concatenate(xp)
        first, last = self.X[self.start], self.X[self.start + self.n - 1]
        # the functions laid end to end (1 apart) only to find the bracketing pair
        self.offset = np.concatenate([[0.], np.cumsum(last - first + 1.)[:-1]]) - first
        self.shifted = self.X + np.repeat(self.offset, self.n)

    def locate(self, x, k):
        lo, hi = self.start[k], self.start[k] + self.n[k] - 1
        j = np.clip(np.searchsorted(self.shifted, x + self.offset[k], side='right') - 1, lo, np.maximum(lo, hi - 1))
        return j, np.minimum(j + 1, hi), lo, hi

    def value(self, x, where, fp):
        j, j1, lo, hi = where
        X, F = self.X, fp
        with np.errstate(divide='ignore', invalid='ignore'):
            out = (F[j1] - F[j]) / (X[j1] - X[j]) * (x - X[j]) + F[j]
        out = np.where(x <= X[lo], F[lo], out)
        return np.where(x >= X[hi], F[hi], out)


def _dumps(objs, compact):
    return [json.dumps(o, separators=(',', ':')) if compact else json.dumps(o) for o in objs]


def write_index(segments, arcs, mouths, node_h, path, emit=print):
    """The read-only index (features, their bounds, nodes, links) into a new SQLite file at path.
    Mouths are done as arrays (a Python step per mouth was ~90 s of London's cold index), the JSON
    of the rows is encoded in parallel (~60 s in one process), and the rows go in key / spatial
    order into a database without a journal: a temporary file, renamed only once complete."""
    from concurrent.futures import ProcessPoolExecutor
    started = time.perf_counter()
    pool = ProcessPoolExecutor(min(6, os.cpu_count() or 2)) if len(segments) > 50_000 else None
    try:
        # every mouth, node by node in mouths order
        nids = list(mouths)
        counts = np.array([len(mouths[n]) for n in nids])
        flat = [e for n in nids for e in mouths[n]]
        sid = np.array([e[0] for e in flat]); end = np.array([e[1] for e in flat])
        width = np.array([e[4] for e in flat], dtype=float); length = np.array([e[5] for e in flat], dtype=float)
        del flat
        first = np.concatenate([[0], np.cumsum(counts)[:-1]])
        radius = np.where(counts > 1, np.maximum.reduceat(width, first) * .8, 0.)
        cut = np.minimum(np.repeat(radius, counts), length * .25)
        s = np.where(end == 0, cut, length - cut)
        k = sid - 1
        arc = Polylines([a[1] for a in arcs])
        ax_, ay_ = np.concatenate([a[0][:, 0] for a in arcs]), np.concatenate([a[0][:, 1] for a in arcs])
        def at(x):
            w = arc.locate(x, k)
            return arc.value(x, w, ax_), arc.value(x, w, ay_)
        px, py = at(s)
        fx, fy = at(np.minimum(length, s + .25))
        bx, by = at(np.maximum(0, s - .25))
        dx, dy = fx - bx, fy - by
        norm = np.maximum(1e-9, np.sqrt(dx * dx + dy * dy))
        dx, dy = dx / norm, dy / norm
        nx, ny = -dy * width / 2, dx * width / 2
        plus = np.round(np.column_stack([px + nx, py + ny]), 3).tolist()
        minus = np.round(np.column_stack([px - nx, py - ny]), 3).tolist()
        prof = Polylines([np.asarray(seg['prof'][0], dtype=float) for seg in segments])
        cz = (prof.value(s, prof.locate(s, k), np.concatenate([np.asarray(seg['prof'][1], dtype=float) for seg in segments]))
              + ROAD_LIFT).tolist()
        bridge = np.array([max(1, number(seg['tags'].get('layer'), 1)) * 6.5
                           if seg['tags'].get('bridge') not in (None, 'no') else 0. for seg in segments])
        level = np.maximum(np.maximum.reduceat(bridge[k], first), 0.).tolist()
        cuts = np.zeros((len(segments) + 1, 2)); cuts[sid, end] = cut
        cuts = cuts.tolist()
        emit(f'mouths: {time.perf_counter()-started:.1f}s')
        nodes, junctions = [], []
        for n, nid, a, lev in zip(counts.tolist(), nids, first.tolist(), level):
            entries = mouths[nid]
            data = {'xy': entries[0][2], 'raise': lev, 'segments': [e[0] for e in entries], 'h': node_h[nid] + ROAD_LIFT}
            nodes.append(data)
            if n > 1:
                junctions.append((nid, data, [c for i in range(a, a + n) for c in (plus[i], minus[i])],
                                  [z for i in range(a, a + n) for z in (cz[i], cz[i])]))
        del plus, minus, cz
        bound_rows, kept = [], []
        if junctions:
            jc = [len(j[2]) for j in junctions]
            hulls = shapely.convex_hull(shapely.multipoints(np.concatenate([j[2] for j in junctions]),
                                                            indices=np.repeat(np.arange(len(junctions)), jc)))
            areas, wkbs, bounds = shapely.area(hulls), shapely.to_wkb(hulls), shapely.bounds(hulls)
            for (nid, data, corners, z), area, wkb, (x0, y0, x1, y1) in zip(junctions, areas, wkbs, bounds):
                if area > .01:
                    kept.append((-nid, dict(data, node=nid, corners=corners, cz=z), wkb))
                    bound_rows.append((-nid, x0, x1, y0, y1))
        del junctions
        lines = shapely.linestrings(np.concatenate([a[0] for a in arcs]),
                                    indices=np.repeat(np.arange(len(arcs)), [len(a[0]) for a in arcs]))
        seg_wkb = shapely.to_wkb(lines)
        for seg, (x0, y0, x1, y1) in zip(segments, shapely.bounds(lines)):
            seg['cuts'] = cuts[seg['id']]
            # Bounds include end mouths so a query always includes the owning segment.
            pad = seg['width'] / 2 + 1
            bound_rows.append((seg['id'], x0 - pad, x1 + pad, y0 - pad, y1 + pad))
        del lines
        emit(f'rows: {time.perf_counter()-started:.1f}s')
        def dumps(objs, compact):
            if pool is None:
                out = _dumps(objs, compact)
                return lambda: out
            step = max(1, len(objs) // 48 + 1)
            futures = [pool.submit(_dumps, objs[i:i + step], compact) for i in range(0, len(objs), step)]
            return lambda: [t for f in futures for t in f.result()]
        node_json = dumps(nodes, False)
        junction_json = dumps([j[1] for j in kept], True)
        segment_json = dumps(segments, True)
        # the R-tree fills far faster in spatial order: 1 km cells along a Morton curve
        b = np.array([r[1:] for r in bound_rows])
        code = np.zeros(len(b), dtype=np.int64)
        cx, cy = ((b[:, 0] + b[:, 1]) / 2000).astype(np.int64) & 0xffff, ((b[:, 2] + b[:, 3]) / 2000).astype(np.int64) & 0xffff
        for bit in range(16):
            code |= ((cx >> bit) & 1) << (2 * bit) | ((cy >> bit) & 1) << (2 * bit + 1)
        bound_rows = [bound_rows[i] for i in np.argsort(code, kind='stable')]
        link_rows = sorted(zip(np.repeat(nids, counts).tolist(), sid.tolist()), key=lambda r: r[0])
        db = sqlite3.connect(path)
        db.executescript('''PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-262144;
            PRAGMA temp_store=MEMORY;
            CREATE TABLE features(id INTEGER PRIMARY KEY, kind TEXT, data TEXT, geom BLOB);
            CREATE VIRTUAL TABLE bounds USING rtree(id,x0,x1,y0,y1);
            CREATE TABLE nodes(id INTEGER PRIMARY KEY, data TEXT);
            CREATE TABLE links(node INTEGER, segment INTEGER);''')
        db.executemany('INSERT INTO bounds VALUES(?,?,?,?,?)', bound_rows)
        db.executemany('INSERT INTO links VALUES(?,?)', link_rows)
        db.execute('CREATE INDEX links_node ON links(node)')
        db.executemany('INSERT INTO nodes VALUES(?,?)', sorted(zip(nids, node_json())))
        # junctions (negative ids) before roads: features in key order
        db.executemany('INSERT INTO features VALUES(?,?,?,?)',
                       [(fid, 'junction', text, wkb) for (fid, _, wkb), text in sorted(zip(kept, junction_json()), key=lambda r: r[0][0])])
        db.executemany('INSERT INTO features VALUES(?,?,?,?)',
                       ((seg['id'], 'road', text, wkb) for seg, text, wkb in zip(segments, segment_json(), seg_wkb)))
        db.commit(); db.close()
        emit(f'written: {time.perf_counter()-started:.1f}s')
    finally:
        if pool:
            pool.shutdown(cancel_futures=True)


class Cancelled(Exception):
    """A newer landing replaced the one this index was being built for."""


def prepare(*args, **kw):
    """_prepare without the cyclic garbage collector: with millions of live lists and dicts
    (London's window: 650 k roads) each collection walked them all again; the index was built
    ~2x slower and a pickle of it loaded 10x slower. Reference counting still frees them.
    The collector is process-wide, so the companion's other threads run without it for the
    build too; they make no cycles worth collecting meanwhile."""
    enabled = gc.isenabled()
    gc.disable()
    try:
        return _prepare(*args, **kw)
    finally:
        if enabled:
            gc.enable()


def _prepare(source, output, dem, emit=print, point=None, cancelled=None, box=None):
    """Parse source tiles once, split roads at shared nodes, publish a read-only index.
    A large package is indexed as a window round point (the landing). cancelled() is asked
    between the stages: a window of a big city takes minutes, and the single preparer must not
    finish it for a landing the player has already left (the next landing waited behind it)."""
    import osmium
    started = time.perf_counter()
    def check():
        if cancelled and cancelled():
            raise Cancelled(point)
    identity, index = source_identity(source, dem, point, box)
    target = Path(output) / identity
    final = target / 'sources.sqlite'
    if final.exists():
        return target, json.loads((target / 'region.json').read_text())
    check()
    target.mkdir(parents=True, exist_ok=True)
    frame = Frame.of(index)
    roads = {}

    files = [Path(source) / entry['file'] for entry in index['tiles'].values()]
    shas = [entry['sha256'] for entry in index['tiles'].values()]
    if len(files) > 24:
        # tiles parse in parallel; merged in tile order, first copy of a way wins (as before)
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(min(6, os.cpu_count() or 2)) as pool:
            parts = list(pool.map(read_roads, files, shas, chunksize=4))
    else:
        parts = [read_roads(f, h) for f, h in zip(files, shas)]
    roads = {}
    for part in parts:
        for wid, road in part.items():
            roads.setdefault(wid, road)
    del parts
    emit(f'roads parsed in {time.perf_counter()-started:.1f}s')
    check()
    refs = Counter(n[0] for _, nodes in roads.values() for n in nodes)
    order = sorted(roads)
    lens = [len(roads[w][1]) for w in order]
    lon = [n[1] for w in order for n in roads[w][1]]; lat = [n[2] for w in order for n in roads[w][1]]
    X, Y = frame.local(lon, lat)            # one transform for every node (was one per way)
    X, Y = X.tolist(), Y.tolist(); del lon, lat
    segments, arcs, mouths = [], [], defaultdict(list)
    at = 0
    hypot = math.hypot
    for wid, count in zip(order, lens):
        tags, nodes = roads[wid]
        xy = list(zip(X[at:at + count], Y[at:at + count])); at += count
        cuts = [0] + [k for k in range(1,len(nodes)-1) if refs[nodes[k][0]] > 1] + [len(nodes)-1]
        width = None
        for a, b in zip(cuts, cuts[1:]):
            # points closer than 1 cm to the previous raw point dropped; arc length after that
            pts = [xy[a]] + [xy[k] for k in range(a + 1, b + 1)
                             if hypot(xy[k][0] - xy[k-1][0], xy[k][1] - xy[k-1][1]) > 0.01]
            if len(pts)<2:
                continue
            cum = [0.]
            for k in range(1, len(pts)):
                cum.append(cum[-1] + hypot(pts[k][0] - pts[k-1][0], pts[k][1] - pts[k-1][1]))
            if cum[-1] < .1:
                continue
            if width is None:
                width = road_width(tags)
            # millimetres are enough (seams <= 1 cm) and keep the JSON a third the size
            seg = {'id': len(segments)+1, 'way': wid, 'tags': tags, 'xy': [[round(q[0], 3), round(q[1], 3)] for q in pts],
                   'nodes': [nodes[a][0],nodes[b][0]], 'width': width}
            segments.append(seg); arcs.append((np.array(pts), np.array(cum)))
            mouths[seg['nodes'][0]].append((seg['id'],0,list(pts[0]),None,width,cum[-1]))
            mouths[seg['nodes'][1]].append((seg['id'],1,list(pts[-1]),None,width,cum[-1]))
    del roads, refs
    # one height solve for the whole graph: shared nodes, smooth grades, soft bridges/tunnels
    import fast_profile
    terrain = Terrain(dem, frame)
    mosaic = fast_profile.Mosaic(terrain, np.concatenate([np.asarray(seg['xy']) for seg in segments]))
    profiles, node_h = fast_profile.solve(segments, lambda q: mosaic.sample(q, missing=True), emit=emit)
    for seg, (ps, ph) in zip(segments, profiles):
        seg['prof'] = [np.round(ps, 2).tolist(), np.round(ph, 3).tolist()]
    del mosaic, profiles
    emit(f'profile ready: {time.perf_counter()-started:.1f}s')
    check()
    tmp = target / f'sources.{os.getpid()}.tmp'
    tmp.unlink(missing_ok=True)         # left by an earlier run of this process
    write_index(segments, arcs, mouths, node_h, tmp, emit)
    meta = {'id':identity,'generator':VERSION,'bbox':index['package_box'],'center':index['centre'],'frame':index['frame'],
            'dem':str(Path(dem).resolve()), 'source':str(Path(source).resolve()),
            'coverage':[v['coverage'] for v in index['tiles'].values()],
            'seconds':time.perf_counter()-started,'roads':len(segments),'scenery':'lazy from raw OSM'}
    (target/'region.json').write_text(json.dumps(meta),encoding='utf8')
    os.replace(tmp,final)
    emit(f'index ready: {meta["seconds"]:.1f}s, {len(segments)} segments')
    return target, meta


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.meta = json.loads((self.directory/'region.json').read_text())
        self.db = sqlite3.connect((self.directory/'sources.sqlite').as_uri()+'?mode=ro',uri=True)
        self.frame = Frame.of(self.meta)
        self.terrain = Terrain(self.meta['dem'],self.frame)
        from shapely.geometry import box
        self.coverage = shapely.union_all([box(*r) for r in self.meta['coverage']])
        self.heights = OrderedDict()
        self.scenes = OrderedDict()
        self.source_index = json.loads((Path(self.meta['source'])/'index.json').read_text())

    def query(self, rect, kinds=None):
        x0,y0,x1,y1=rect
        rows = self.db.execute('''SELECT f.id,f.kind,f.data,f.geom FROM bounds b JOIN features f ON f.id=b.id
            WHERE b.x1>=? AND b.x0<=? AND b.y1>=? AND b.y0<=? ORDER BY f.id''',(x0,x1,y0,y1))
        return [(fid,kind,json.loads(data),shapely.from_wkb(geom)) for fid,kind,data,geom in rows
                if kinds is None or kind in kinds]

    def node(self, nid):
        return json.loads(self.db.execute('SELECT data FROM nodes WHERE id=?',(nid,)).fetchone()[0])

    def height(self, nid):
        if nid in self.heights:
            self.heights.move_to_end(nid)
            return self.heights[nid]
        h = float(self.node(nid)['h'])     # solved with the whole graph (fast_profile)
        self.heights[nid] = h
        if len(self.heights)>8192:
            self.heights.popitem(last=False)
        return h

    def covered(self, rect):
        from shapely.geometry import box
        return self.coverage.covers(box(*self.frame.geo_bbox(rect)))

    def scenery(self, rect):
        """Load only the raw OSM block needed now, including multipolygon holes.

        This work is timed in scene jobs, never hidden in a prebuilt city asset.
        """
        import osmium
        import osm_tiles
        bbox=self.frame.geo_bbox(rect)
        hit=osm_tiles.tile_for(self.source_index,bbox)
        if not hit:
            raise ValueError(f'no_osm scenery coverage: {bbox}')
        key,entry=hit
        if key not in self.scenes:
            found={}; lines={}; factory=osmium.geom.GeoJSONFactory()
            import fast_style
            class Reader(osmium.SimpleHandler):
                def area(self,a):
                    kind='building' if a.tags.get('building') not in (None,'no') else (
                        'water' if a.tags.get('natural')=='water' or a.tags.get('waterway')=='riverbank' else None)
                    if kind is None:
                        kind=fast_style.ground_kind(a.tags)
                    if kind and a.id not in found:
                        try:
                            geometry=factory.create_multipolygon(a)
                        except RuntimeError:
                            return      # broken OSM ring (e.g. area 29961653): skip that area alone
                        found[a.id]=(kind,dict(a.tags),shape(json.loads(geometry)))
                def node(self,n):
                    # bus stops and traffic signals where OSM has them (way nodes keep their tags
                    # in the source tiles); keyed apart from areas and ways
                    kind=fast_style.point_kind(n.tags)
                    if kind and n.location.valid():
                        lines[-(10**12+n.id)]=(kind,dict(n.tags),Point(n.location.lon,n.location.lat))
                def way(self,w):
                    # footpaths, fences, tree rows: lines, not areas (an area id is 2 x way id,
                    # so a line keys as -way id to stay apart)
                    kind=fast_style.line_kind(w.tags)
                    if kind and -w.id not in lines:
                        try:
                            coords=[(n.lon,n.lat) for n in w.nodes]
                        except osmium.InvalidLocationError:
                            return
                        if len(coords)>=2:
                            lines[-w.id]=(kind,dict(w.tags),LineString(coords))
            Reader().apply_file(str(Path(self.meta['source'])/entry['file']),locations=True)
            found.update(lines)
            ordered=sorted(found.items())
            geoms=shapely.make_valid(shapely.transform([v[1][2] for v in ordered],self.frame.local,interleaved=False))
            records=[(fid,kind,{'tags':tags},geom) for (fid,(kind,tags,_)),geom in zip(ordered,geoms)]
            self.scenes[key]=(records,shapely.STRtree(geoms))
            if len(self.scenes)>4: self.scenes.popitem(last=False)
        self.scenes.move_to_end(key)
        records,tree=self.scenes[key]
        from shapely.geometry import box
        return [records[int(k)] for k in sorted(tree.query(box(*rect)))]
