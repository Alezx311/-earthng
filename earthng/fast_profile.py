"""Road heights for the whole graph at once (fast generator, index time).

Per-segment profiles from a 24 m DEM gave 112 % ramps and 3.5 m pits on short pieces
(docs/HANDOFF_FAST_STREAMING.md). Here every road is sampled into stations (shared at graph
nodes) and the heights minimise

    sum w*m*(h - target)^2 + ALPHA * sum (dh)^2/l + BETA * sum (d2h)^2/l^3

(data, grade, change of grade), solved by Jacobi-preconditioned conjugate gradients. A node
has one height for every road that meets there, so tiles and junction mouths agree by
construction. Bridges pull up and real tunnels pull down only softly; their approaches become
ramps. tunnel=building_passage is an arch through a house, at ground level.
"""
import math

import numpy as np

STEP = 20.0            # station spacing, m
ALPHA = 40.0 ** 2      # grade smoothing length 40 m
BETA = 50.0 ** 4       # grade-change smoothing length 50 m
BRIDGE_CLEAR, TUNNEL_DEPTH, SOFT = 6.5, 7.0, 0.5
# Grade weight on the first station gap out of a junction (3+ roads): roads leave it level, so
# roads that overlap near it in plan are at one height. With free grades a link and the main
# road diverged by 0.78 m over the main road's sidewalk, and junction polygons tilted 24 %.
JUNCTION_FLAT = 10.0
# Ground roads whose surfaces touch in plan (two carriageways of a divided road, a lane beside
# a street) are tied to one height: on Khreshchatyk the carriageways overlapped by 1 m with
# profiles up to 1.02 m apart.
COUPLE = 1000.0


def close_pairs(pos, seg_of, width, ground, same):
    """Station -> nearby edge of another ground segment whose strip touches it: lateral
    distance to the edge < half widths + 0.5 m. Returns station rows a, edge start rows e
    (edge = e, e+1) and the parameter t of the closest point. Grid hashing, no SciPy."""
    cell = 24.0
    e0 = np.flatnonzero(same)                     # edge e0 -> e0+1, both of one segment
    mid = (pos[e0] + pos[e0 + 1]) / 2
    M = 1 << 21
    def keys(xy, dx=0, dy=0):
        return (np.floor(xy[:, 0] / cell).astype(np.int64) + dx + M) * (2 * M) + np.floor(xy[:, 1] / cell).astype(np.int64) + dy + M
    ekey = keys(mid); order = np.argsort(ekey, kind='stable'); skey = ekey[order]
    out = ([], [], [])
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            nk = keys(pos, dx, dy)
            lo = np.searchsorted(skey, nk, 'left'); hi = np.searchsorted(skey, nk, 'right')
            cnt = hi - lo
            if not cnt.sum():
                continue
            ia = np.repeat(np.arange(len(pos)), cnt)
            ie = e0[order[lo[ia] + (np.arange(cnt.sum()) - np.repeat(np.cumsum(cnt) - cnt, cnt))]]
            keep = (seg_of[ia] != seg_of[ie]) & ground[ia] & ground[ie]
            ia, ie = ia[keep], ie[keep]
            a, b = pos[ie], pos[ie + 1]
            ab = b - a; L2 = np.maximum((ab ** 2).sum(1), 1e-9)
            t = np.clip(((pos[ia] - a) * ab).sum(1) / L2, 0, 1)
            d = np.linalg.norm(pos[ia] - (a + ab * t[:, None]), axis=1)
            keep = d < (width[ia] + width[ie]) / 2 + .5
            out[0].append(ia[keep]); out[1].append(ie[keep]); out[2].append(t[keep])
    if not out[0]:
        return np.zeros(0, int), np.zeros(0, int), np.zeros(0)
    return tuple(np.concatenate(v) for v in out)


def layer(tags):
    try:
        return abs(float(str(tags.get('layer', 1)).split(';')[0]))
    except ValueError:
        return 1.


def kind(tags):
    if tags.get('bridge') not in (None, 'no'):
        return 'bridge'
    if tags.get('tunnel') not in (None, 'no', 'building_passage'):
        return 'tunnel'
    return 'road'


def stations(segments, node_index):
    """All segments' stations at once (162k segments: a Python loop cost 12 s).

    Returns per-station arc length s, variable id, position, owning segment and the start of
    each segment's run; ends map to their graph node, interior stations get fresh ids."""
    n_seg = len(segments)
    lens = np.array([len(seg['xy']) for seg in segments])
    xy = np.concatenate([np.asarray(seg['xy'], dtype=float) for seg in segments])
    first = np.r_[0, np.cumsum(lens)[:-1]]
    step = np.r_[0., np.linalg.norm(np.diff(xy, axis=0), axis=1)]
    step[first] = 1.                       # a 1 m gap keeps the global arc length increasing
    cum = np.cumsum(step)
    base = cum[first]                      # each segment's start on the global arc length
    length = cum[first + lens - 1] - base
    n = np.maximum(1, np.ceil(length / STEP)).astype(np.int64)
    count = n + 1
    run = np.r_[0, np.cumsum(count)[:-1]]
    seg_of = np.repeat(np.arange(n_seg), count)
    k = np.arange(count.sum()) - run[seg_of]
    s = length[seg_of] * k / n[seg_of]
    g = base[seg_of] + s
    pos = np.column_stack([np.interp(g, cum, xy[:, 0]), np.interp(g, cum, xy[:, 1])])
    a = np.array([node_index[seg['nodes'][0]] for seg in segments])
    b = np.array([node_index[seg['nodes'][1]] for seg in segments])
    inner_run = len(node_index) + np.r_[0, np.cumsum(n - 1)[:-1]]
    ids = inner_run[seg_of] + k - 1
    ids[k == 0] = a[seg_of[k == 0]]
    ends = k == n[seg_of]
    ids[ends] = b[seg_of[ends]]
    nvar = len(node_index) + int((n - 1).sum())
    return s, ids, pos, seg_of, run, nvar


def csr(rows, cols, vals, n):
    """COO -> CSR with duplicates summed (no SciPy here)."""
    key = rows * n + cols
    order = np.argsort(key, kind='stable')
    key, vals = key[order], vals[order]
    uniq, start = np.unique(key, return_index=True)
    data = np.add.reduceat(vals, start)
    r, c = uniq // n, uniq % n
    ptr = np.r_[0, np.cumsum(np.bincount(r, minlength=n))]
    return ptr, c, data


def solve(segments, sample, iterations=400, tol=1e-3, emit=None):
    """Heights for every segment's stations. sample(xy) -> DEM height (vectorised, NaN outside).

    Returns a list of (s, h) per segment and {node id: h}."""
    nodes = sorted({n for seg in segments for n in seg['nodes']})
    node_index = {n: k for k, n in enumerate(nodes)}
    s, ids, pos_st, seg_of, run, nvar = stations(segments, node_index)
    pos = np.zeros((nvar, 2)); pos[ids] = pos_st
    same = seg_of[1:] == seg_of[:-1]                       # consecutive stations of one segment
    ds = np.diff(s)
    ea, eb, el = ids[:-1][same], ids[1:][same], np.maximum(ds[same], .5)
    mass = np.bincount(ea, el / 2, nvar) + np.bincount(eb, el / 2, nvar)
    tri = same[:-1] & same[1:]
    ta, tb, tc = ids[:-2][tri], ids[1:-1][tri], ids[2:][tri]
    tl = np.maximum((ds[:-1][tri] + ds[1:][tri]) / 2, .5)
    dem = sample(pos)
    # whole OSM ways reach past the downloaded DEM (their tiles fail no_dem anyway): there the
    # height only follows the neighbours
    known = np.isfinite(dem)
    dem[~known] = np.median(dem[known]) if known.any() else 0.
    target = dem.copy(); weight = np.where(known, 1., 1e-3)
    kinds = np.array([{'road': 0, 'bridge': 1, 'tunnel': -1}[kind(seg['tags'])] for seg in segments])
    layers = np.array([max(1., layer(seg['tags'])) for seg in segments])
    k_st = kinds[seg_of]
    inner = (k_st != 0) & (ids >= len(node_index))
    off = np.where(k_st > 0, BRIDGE_CLEAR, -TUNNEL_DEPTH) * layers[seg_of]
    target[ids[inner]] = dem[ids[inner]] + off[inner]
    weight[ids[inner]] = np.where(known[ids[inner]], SOFT, 1e-3)
    mw = weight * np.maximum(mass, .5)
    degree = np.bincount(np.array([node_index[n] for seg in segments for n in seg['nodes']]), minlength=nvar)
    flat = np.where((degree[ea] >= 3) | (degree[eb] >= 3), JUNCTION_FLAT, 1.)
    ce, ct = ALPHA * flat / el, BETA / tl ** 3
    widths = np.array([seg.get('width', 4.) for seg in segments])
    pa, pe, pt = close_pairs(pos_st, seg_of, widths[seg_of], k_st == 0, same)
    va, vb1, vb2 = ids[pa], ids[pe], ids[pe + 1]
    keep = (va != vb1) & (va != vb2)
    va, vb1, vb2, pt = va[keep], vb1[keep], vb2[keep], pt[keep]
    rows = [np.arange(nvar), ea, eb, ea, eb]
    cols = [np.arange(nvar), ea, eb, eb, ea]
    vals = [mw, ce, ce, -ce, -ce]
    # residual h_a - (1-t) h_b1 - t h_b2, weight COUPLE
    cv, cw = (va, vb1, vb2), (np.ones(len(va)), -(1 - pt), -pt)
    for i in range(3):
        for j in range(3):
            rows.append(cv[i]); cols.append(cv[j]); vals.append(COUPLE * cw[i] * cw[j])
    trip, coef = (ta, tb, tc), (1., -2., 1.)
    for i in range(3):
        for j in range(3):
            rows.append(trip[i]); cols.append(trip[j]); vals.append(ct * coef[i] * coef[j])
    ptr, col, data = csr(np.concatenate(rows), np.concatenate(cols), np.concatenate(vals), nvar)
    diag = np.bincount(np.repeat(np.arange(nvar), np.diff(ptr)), np.where(
        col == np.repeat(np.arange(nvar), np.diff(ptr)), data, 0.), nvar)

    def matvec(h):
        return np.add.reduceat(data * h[col], ptr[:-1])

    # solve for the offset from the DEM: the start residual is small and well scaled
    b = mw * target - matvec(dem)
    x = np.zeros(nvar); r = b.copy(); z = r / diag; p = z.copy(); rz = r @ z
    norm0 = math.sqrt(b @ b) or 1.
    it = 0
    for it in range(iterations):
        if math.sqrt(r @ r) < tol * norm0 or rz <= 0:
            break                                  # e.g. a flat DEM is already the answer
        ap = matvec(p)
        alpha = rz / (p @ ap)
        x += alpha * p; r -= alpha * ap
        if math.sqrt(r @ r) < tol * norm0:
            break
        z = r / diag; rz2 = r @ z
        p = z + (rz2 / rz) * p; rz = rz2
    if emit:
        emit(f'profile: {nvar} stations, {len(va)} close station-edge pairs, {it + 1} CG iterations, residual {math.sqrt(r @ r) / norm0:.1e}')
    h = dem + x
    hs = h[ids]
    bounds = np.r_[run, len(s)]
    profiles = [(s[bounds[k]:bounds[k + 1]], hs[bounds[k]:bounds[k + 1]]) for k in range(len(segments))]
    return profiles, {n: float(h[k]) for n, k in node_index.items()}


class Mosaic:
    """Terrain.sample for millions of points: the needed PNG tiles stitched once."""
    def __init__(self, terrain, xy):
        lon, lat = terrain.frame.geographic(xy[:, 0], xy[:, 1])
        x, y = self._px(lon, lat)
        self.tx0, self.ty0 = int(x.min() // 256) - 1, int(y.min() // 256) - 1
        tx1, ty1 = int(x.max() // 256) + 1, int(y.max() // 256) + 1
        self.terrain = terrain
        self.grid = np.full(((ty1 - self.ty0 + 1) * 256, (tx1 - self.tx0 + 1) * 256), np.nan)
        for a in range(self.tx0, tx1 + 1):
            for b in range(self.ty0, ty1 + 1):
                path = terrain.cache / 'terrain' / '12' / str(a) / f'{b}.png'
                if path.exists():
                    ix = np.arange(a * 256, a * 256 + 256); iy = np.arange(b * 256, b * 256 + 256)
                    gx, gy = np.meshgrid(ix, iy)
                    self.grid[(b - self.ty0) * 256:(b - self.ty0 + 1) * 256,
                              (a - self.tx0) * 256:(a - self.tx0 + 1) * 256] = terrain.pixels(gx.ravel(), gy.ravel()).reshape(256, 256)

    @staticmethod
    def _px(lon, lat):
        scale = 256 * 2 ** 12
        return (np.asarray(lon) + 180) / 360 * scale, (1 - np.arcsinh(np.tan(np.radians(lat))) / np.pi) / 2 * scale

    def sample(self, xy, smooth=True, missing=False):
        """Same filter and result as Terrain.sample (smooth 5-tap bilinear)."""
        from fast_sources import LIFT
        xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        lon, lat = self.terrain.frame.geographic(xy[:, 0], xy[:, 1])
        x, y = self._px(lon, lat)
        ix, iy = np.floor(x).astype(int), np.floor(y).astype(int)
        u, v = x - ix, y - iy
        gx, gy = ix - self.tx0 * 256, iy - self.ty0 * 256
        g = self.grid
        offsets = [(0, 0, 1.)] if not smooth else [(0, 0, .5), (-1, 0, .125), (1, 0, .125), (0, -1, .125), (0, 1, .125)]
        z = np.zeros(len(x))
        for dx, dy, w in offsets:
            a, b = gx + dx, gy + dy
            z += w * ((g[b, a] * (1 - u) + g[b, a + 1] * u) * (1 - v) + (g[b + 1, a] * (1 - u) + g[b + 1, a + 1] * u) * v)
        if not missing and not np.isfinite(z).all():
            raise FileNotFoundError('no_dem: profile stations outside the downloaded DEM')
        return z + LIFT
