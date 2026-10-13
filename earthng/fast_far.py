"""The far layer of a tile: the city on the horizon, not to drive in.

Within ~3 km of the car and beyond the tiles that have their scene, a far tile draws simplified
buildings (the same facade materials, no parapets, gables or end walls), the major roads as flat
ribbons, water, and a coarse ground grid. Everything is decoration: no collision, so the far
city adds nothing to be:reloadCollision. The game drops a far tile when the tile's own scene has
loaded.
"""
import math

import numpy as np
import shapely
from shapely.geometry import box

import fast
import fast_sources as sources
import fast_style as style

FAR_GRID = 100.0         # ground cells, m
FAR_ROADS = {'motorway', 'trunk', 'primary', 'secondary', 'tertiary'}
FAR_MIN_HEIGHT = 6.0     # lower buildings vanish at this distance anyway (unless large)
SIMPLIFY = 1.0           # m


def build_far(store, rect):
    mesh = fast.Mesh(rect)
    tile = box(*rect)
    cx, cy = store.frame.local(*fast.CENTRE)
    centre = math.hypot((rect[0] + rect[2]) / 2 - float(cx), (rect[1] + rect[3]) / 2 - float(cy)) < fast.CENTRE_RADIUS
    holes = []
    for fid, kind, data, geom in store.query(rect, {'road'}):
        tags = data['tags']
        if tags.get('highway', '').replace('_link', '') not in FAR_ROADS:
            continue
        major = tags.get('highway', '').replace('_link', '') in fast.MAJOR
        mesh.add_many('road_major' if major else 'road', far_road(data))
    for fid, kind, data, geom in store.scenery(rect):
        if kind == 'water':
            p = geom.representative_point()
            h = float(store.terrain.sample([[p.x, p.y]])[0]) - .5
            part = geom.intersection(tile)
            if not part.is_empty:
                mesh.polygon('water', part, h)
                holes.append(part)
        elif kind == 'building':
            tags = data['tags']
            height = max(2.5, min(250, sources.number(tags.get('height'), sources.number(tags.get('building:levels'), 3) * 3)))
            if height < FAR_MIN_HEIGHT and geom.area < 400:
                continue
            p = geom.representative_point()
            # a building belongs to the far tile holding its point: no clipping, no doubles
            if not (rect[0] <= p.x < rect[2] and rect[1] <= p.y < rect[3]):
                continue
            base = float(store.terrain.sample([[p.x, p.y]])[0])
            simple = geom.simplify(SIMPLIFY)
            if simple.is_empty:
                continue
            kind_ = style.wall(tags, height, fid, centre)
            scale = style.KINDS[kind_][3]
            walls = []
            for poly in shapely.get_parts(shapely.orient_polygons(simple)):
                if poly.geom_type != 'Polygon':
                    continue
                quads, _ = fast.wall_quads(list(poly.exterior.coords), base - 2, base + height, base, scale)
                walls.extend(t for q in quads for t in q)
            mesh.faces.setdefault(kind_, [])
            add_unclipped(mesh, kind_, walls)
            roof = [np.array([[*t[0], base + height], [*t[1], base + height], [*t[2], base + height]])
                    for t in fast.triangles(simple)]
            add_unclipped(mesh, 'roof', roof)
    # coarse ground under it all, a little below the scene's own ground
    xs = np.arange(rect[0], rect[2] + 1, FAR_GRID); ys = np.arange(rect[1], rect[3] + 1, FAR_GRID)
    pts = np.array([(x, y) for y in ys for x in xs])
    hz = store.terrain.sample(pts).reshape(len(ys), len(xs)) - .6
    water = shapely.union_all(holes) if holes else None
    tris = []
    for j in range(len(ys) - 1):
        for i in range(len(xs) - 1):
            if water is not None and water.contains(box(xs[i], ys[j], xs[i + 1], ys[j + 1])):
                continue
            a = [xs[i], ys[j], hz[j, i]]; b = [xs[i + 1], ys[j], hz[j, i + 1]]
            c = [xs[i + 1], ys[j + 1], hz[j + 1, i + 1]]; d = [xs[i], ys[j + 1], hz[j + 1, i]]
            tris += [np.array([a, b, c]), np.array([a, c, d])]
    mesh.add_many('ground', tris)
    return mesh


def far_road(data):
    """A road as a coarse ribbon: the line simplified to 2.5 m, heights from its solved profile.
    (The scene's strips sample every 8 m: up to 490 triangles a far tile, vis-replay-01.)"""
    line = shapely.LineString(data['xy'])
    a, b = data['cuts']
    if line.length - a - b < 1:
        return []
    from shapely.ops import substring
    part = substring(line, a, line.length - b).simplify(2.5)
    xy = np.asarray(part.coords)[:, :2]
    if len(xy) < 2:
        return []
    s = np.array([line.project(shapely.Point(p)) for p in xy])
    ps, ph = data['prof']
    z = np.interp(s, ps, ph) + sources.ROAD_LIFT
    t = np.gradient(xy, axis=0)
    t /= np.maximum(np.linalg.norm(t, axis=1)[:, None], 1e-9)
    n = np.column_stack([-t[:, 1], t[:, 0]]) * data['width'] / 2
    return fast.strip(np.column_stack([xy + n, z]), np.column_stack([xy - n, z]))


def add_unclipped(mesh, kind, tris):
    """Faces of a building wholly in this far tile (it owns the building by its point)."""
    if not len(tris):
        return
    t = np.asarray(tris, dtype=float)
    a, b = t[:, 1, :3] - t[:, 0, :3], t[:, 2, :3] - t[:, 0, :3]
    n = np.cross(a, b)
    norm = np.linalg.norm(n, axis=1)
    keep = norm >= 1e-8
    t, n, norm = t[keep], n[keep], norm[keep]
    if not fast.is_wall(kind):
        down = n[:, 2] < 0
        t[down] = t[down][:, [0, 2, 1]]; n[down] *= -1
    mesh.faces.setdefault(kind, []).extend(zip(t, n / norm[:, None]))
