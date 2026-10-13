"""Cold-start OSM index for the streaming generator. No SUMO or generated meshes.

The index owns complete OSM ways and junction mouths, not tile-local decisions.
SQLite/RTree readers are independent and bounded; workers never copy a city graph.
"""
from collections import Counter, defaultdict, OrderedDict
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
from shapely.geometry import LineString, MultiPoint, shape
from shapely.ops import transform

VERSION = 'fast-8'          # source index format (keys fast-index/<id>)
GEOMETRY = 'fast-18'         # mesh/material semantics; bump to reject tiles built by older code
TILE = 500.0
ROAD_LIFT = 0.12            # road surface over its profile (the ground mesh sits 0.25 under the DEM)
LIFT = 20.0                 # absolute DEM heights + lift; never the carrier plane
CRS = 'EPSG:32636'
ORIGIN = (324000., 5590000.) # fixed UTM anchor, independent of landing and source bounds
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


class Frame:
    def __init__(self):
        self.forward = pyproj.Transformer.from_crs('EPSG:4326', CRS, always_xy=True)
        self.inverse = pyproj.Transformer.from_crs(CRS, 'EPSG:4326', always_xy=True)
        self.cx, self.cy = ORIGIN

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
WINDOW = (0.4, 0.25)        # half size, degrees lon/lat (~56 x 55 km)
WINDOW_SNAP = 0.2           # window centres on this grid: nearby landings share an index


def window_of(index, point):
    """The tiles of a large package that a landing at point indexes, and the window box."""
    if point is None or len(index['tiles']) <= WINDOW_TILES:
        return index, None
    lon, lat = point
    cx, cy = round(lon / WINDOW_SNAP) * WINDOW_SNAP, round(lat / WINDOW_SNAP) * WINDOW_SNAP
    box = [round(v, 6) for v in (cx - WINDOW[0], cy - WINDOW[1], cx + WINDOW[0], cy + WINDOW[1])]
    tiles = {k: v for k, v in index['tiles'].items()
             if box[0] <= (v['cell'][0] + v['cell'][2]) / 2 <= box[2] and box[1] <= (v['cell'][1] + v['cell'][3]) / 2 <= box[3]}
    if not tiles:
        raise ValueError(f'no_osm: {lon:.4f}, {lat:.4f} is outside the source package')
    pb = index['package_box']
    window = [max(pb[0], box[0]), max(pb[1], box[1]), min(pb[2], box[2]), min(pb[3], box[3])]
    return dict(index, tiles=tiles, package_box=window, window=window), window


def dem_tiles(box):
    """z12 Terrarium tiles under a lon/lat box, one tile of margin (fast_dem.audit's rule)."""
    w, s, e, n = box
    tile = lambda lon, lat: ((lon + 180) / 360 * 4096, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 4096)
    x0, y0 = tile(w, n); x1, y1 = tile(e, s)
    return [(x, y) for x in range(math.floor(x0) - 1, math.floor(x1) + 2) for y in range(math.floor(y0) - 1, math.floor(y1) + 2)]


def source_identity(source, dem, point=None):
    """Index checksum (of the window's tiles) plus the DEM files under it (name, size, mtime):
    old geometry cannot survive source replacement. Contents were hashed before: 200 MB of
    PNG per landing for the oblast."""
    source, dem = Path(source), Path(dem)
    index, window = window_of(json.loads((source / 'index.json').read_bytes()), point)
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

    def pixels(self, x, y):
        out = np.empty(len(x), dtype=float)
        tx, ty = x // 256, y // 256
        for a, b in set(zip(tx.tolist(), ty.tolist())):
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
            mask = (tx == a) & (ty == b)
            out[mask] = self.images[key][y[mask] % 256, x[mask] % 256]
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
        for dx, dy, weight in offsets:
            z += weight * ((self.pixels(ix+dx,iy+dy)*(1-u) + self.pixels(ix+dx+1,iy+dy)*u)*(1-v)
                           + (self.pixels(ix+dx,iy+dy+1)*(1-u) + self.pixels(ix+dx+1,iy+dy+1)*u)*v)
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


def prepare(source, output, dem, emit=print, point=None):
    """Parse source tiles once, split roads at shared nodes, publish a read-only index.
    A large package is indexed as a window round point (the landing)."""
    import osmium
    started = time.perf_counter()
    identity, index = source_identity(source, dem, point)
    target = Path(output) / identity
    final = target / 'sources.sqlite'
    if final.exists():
        return target, json.loads((target / 'region.json').read_text())
    target.mkdir(parents=True, exist_ok=True)
    frame = Frame()
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
    tmp = target / f'sources.{os.getpid()}.tmp'
    db = sqlite3.connect(tmp)
    db.executescript('''CREATE TABLE features(id INTEGER PRIMARY KEY, kind TEXT, data TEXT, geom BLOB);
        CREATE VIRTUAL TABLE bounds USING rtree(id,x0,x1,y0,y1);
        CREATE TABLE nodes(id INTEGER PRIMARY KEY, data TEXT);
        CREATE TABLE links(node INTEGER, segment INTEGER);
        CREATE INDEX links_node ON links(node);''')
    # rows collected and written with executemany; geometry made in shapely's vector calls
    # (one call per mouth/segment cost ~60 s of the cold start)
    def along(sid, at):
        pts, cum = arcs[sid-1]
        return np.array([np.interp(at, cum, pts[:,0]), np.interp(at, cum, pts[:,1])])
    cutbacks, node_rows, link_rows, junctions = {}, [], [], []
    for nid, entries in mouths.items():
        # Complete shared node ownership; endpoints use square caps, junctions shared mouths.
        radius = max(e[4] for e in entries)*.8 if len(entries)>1 else 0.
        corners, cz = [], []
        level = 0.
        for sid, end, pos, direction, width, length in entries:
            tags = segments[sid-1]['tags']
            if tags.get('bridge') not in (None,'no'):
                level = max(level, max(1,number(tags.get('layer'),1))*6.5)
            cut = min(radius, length*.25)
            cutbacks[sid,end] = cut
            s = cut if end == 0 else length-cut
            q = along(sid, [s, min(length,s+.25), max(0,s-.25)])
            p, d = q[:,0], q[:,1]-q[:,2]
            d /= max(1e-9,np.linalg.norm(d))
            normal = np.array([-d[1],d[0]])*width/2
            corners.extend([np.round(p+normal,3).tolist(),np.round(p-normal,3).tolist()])
            # the corner sits on its own road's profile at the mouth: the strip meets it exactly
            ps, ph = segments[sid-1]['prof']
            cz.extend([float(np.interp(s, ps, ph)) + ROAD_LIFT]*2)
            link_rows.append((nid,sid))
        data = {'xy': entries[0][2], 'raise': level, 'segments': [e[0] for e in entries],
                'h': node_h[nid] + ROAD_LIFT}
        node_rows.append((nid,json.dumps(data)))
        if len(entries)>1:
            junctions.append((nid, data, corners, cz))
    feature_rows, bound_rows = [], []
    if junctions:
        counts = [len(j[2]) for j in junctions]
        hulls = shapely.convex_hull(shapely.multipoints(np.concatenate([j[2] for j in junctions]),
                                                        indices=np.repeat(np.arange(len(junctions)), counts)))
        areas, wkbs, bounds = shapely.area(hulls), shapely.to_wkb(hulls), shapely.bounds(hulls)
        for (nid, data, corners, cz), area, wkb, (x0,y0,x1,y1) in zip(junctions, areas, wkbs, bounds):
            if area > .01:
                feature_rows.append((-nid,'junction',json.dumps(dict(data,node=nid,corners=corners,cz=cz),separators=(',',':')),wkb))
                bound_rows.append((-nid,x0,x1,y0,y1))
    lines = shapely.linestrings(np.concatenate([a[0] for a in arcs]),
                                indices=np.repeat(np.arange(len(arcs)), [len(a[0]) for a in arcs]))
    for seg, wkb, (x0,y0,x1,y1) in zip(segments, shapely.to_wkb(lines), shapely.bounds(lines)):
        seg['cuts'] = [cutbacks[seg['id'],0],cutbacks[seg['id'],1]]
        # Bounds include end mouths so a query always includes the owning segment.
        pad = seg['width']/2+1
        feature_rows.append((seg['id'],'road',json.dumps(seg,separators=(',',':')),wkb))
        bound_rows.append((seg['id'],x0-pad,x1+pad,y0-pad,y1+pad))
    db.executemany('INSERT INTO features VALUES(?,?,?,?)', feature_rows)
    db.executemany('INSERT INTO bounds VALUES(?,?,?,?,?)', bound_rows)
    db.executemany('INSERT INTO nodes VALUES(?,?)', node_rows)
    db.executemany('INSERT INTO links VALUES(?,?)', link_rows)
    db.commit(); db.close()
    meta = {'id':identity,'generator':VERSION,'bbox':index['package_box'],'center':[30.52,50.45],
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
        self.frame = Frame()
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
            found={}; factory=osmium.geom.GeoJSONFactory()
            class Reader(osmium.SimpleHandler):
                def area(self,a):
                    kind='building' if a.tags.get('building') not in (None,'no') else (
                        'water' if a.tags.get('natural')=='water' or a.tags.get('waterway')=='riverbank' else None)
                    if kind is None:
                        import fast_style
                        kind=fast_style.ground_kind(a.tags)
                    if kind and a.id not in found:
                        found[a.id]=(kind,dict(a.tags),shape(json.loads(factory.create_multipolygon(a))))
            Reader().apply_file(str(Path(self.meta['source'])/entry['file']),locations=True)
            ordered=sorted(found.items())
            geoms=shapely.make_valid(shapely.transform([v[1][2] for v in ordered],self.frame.local,interleaved=False))
            records=[(fid,kind,{'tags':tags},geom) for (fid,(kind,tags,_)),geom in zip(ordered,geoms)]
            self.scenes[key]=(records,shapely.STRtree(geoms))
            if len(self.scenes)>4: self.scenes.popitem(last=False)
        self.scenes.move_to_end(key)
        records,tree=self.scenes[key]
        from shapely.geometry import box
        return [records[int(k)] for k in sorted(tree.query(box(*rect)))]
