"""Road quality bench: the native Lua generator over a test area, outside the game.

  python earthng/road_quality.py --pack out/polygon/earthng_kyiv10.zip --label baseline
  python earthng/road_quality.py --pack ... --label try --set groundDrop=0.3 --compare baseline

Every tile of the area (or every --step-th) is built by generator.lua in LuaJIT (lupa), its road
layer split into carriageway, sidewalk/curb and ground, each rasterised as a top-surface height map
(RES m per pixel). Against the source roads' own footprint (centre line +- width/2, minus EDGE) the
bench counts the defects one sees while driving:

  curb_on_road   m2 of sidewalk or curb on top of a carriageway (another street's sidewalk)
  ground_on_road m2 of ground at or above the asphalt; ground_max: the worst penetration, m
  holes          m2 of footprint with no asphalt at all
  road_step      m2 of asphalt next to a jump of more than STEP within the asphalt (mouths, plates)
  edge_*         across each road edge every 2 m, the largest jump of the visible surface from
                 0.5 m inside to 4 m outside: p50/p95 and counts over 0.15 and 0.3 m per km of edge
  ms, tris       generation cost per tile

Bridges, tunnels and the ground under them are left out. The report goes to
out/polygon/runs/<label>/report.json with a defect map (map.png) and the worst places (lat, lon).
"""
import argparse
import json
import math
from pathlib import Path
import sys
import time
import zipfile

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'out/test-tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lupa.luajit21 import LuaRuntime
import country_format as binary

LUA = ROOT / 'mod/lua/ge/extensions/e2r'
RES = .25          # m per raster pixel
EDGE = .3          # footprint inset: rounding at the very edge is not a defect
STEP = .05         # m: a jump inside the asphalt between neighbouring pixels
CARRIAGEWAY = {'e2rf_road', 'e2rf_road_major'}
WALK = {'e2rf_sidewalk', 'e2rf_curb'}
GROUND = {'e2rf_ground'}
# ground cover from the scene layer: it lies on the ground and shows through the asphalt as well
COVER = {'e2rf_paved', 'e2rf_park', 'e2rf_forest', 'e2rf_water', 'e2rf_footway', 'e2rf_footway_asphalt', 'e2rf_trodden'}

BUILD = '''
local generator, cellformat, data, slots, i, j, size, cellSize, sea, tune, cover, repeats = ...
local x0, y0 = i * size, j * size
local cells = {}
for ci = math.floor(x0 / cellSize), math.ceil((x0 + size) / cellSize) - 1 do
  for cj = math.floor(y0 / cellSize), math.ceil((y0 + size) / cellSize) - 1 do
    local range = slots(ci, cj)
    if range then
      cells[#cells + 1] = cellformat.cell(data(ci, cj), range[1], range[2], ci * cellSize, cj * cellSize,
        {ci * cellSize, cj * cellSize, (ci + 1) * cellSize, (cj + 1) * cellSize}, nil, {x0, y0, x0 + size, y0 + size})
    end
  end
end
if #cells == 0 then return nil end
local road, scene, ms, stages
for run = 1, repeats do
  -- the fastest of a few runs, each after a full collection: the machine is not idle
  collectgarbage('collect')
  local marks, last = {}, os.clock()
  local options = {tileSize = size, seaLevel = sea, pause = function() end, tune = tune,
    mark = function(name) local now = os.clock(); marks[name] = (marks[name] or 0) + (now - last) * 1000; last = now end}
  local t = os.clock()
  last = t
  local r, s = generator.build(cells, i, j, options)
  local took = (os.clock() - t) * 1000
  if not ms or took < ms then road, scene, ms, stages = r, s, took, marks end
end
local out, fmt, tris = {}, string.format, 0
local items = {}
for _, item in ipairs(road.items) do items[#items + 1] = item end
for _, item in ipairs(scene.items) do
  if item.kind == 'proc' then
    for _, mesh in ipairs(item.meshes or {}) do if cover[mesh.material] then items[#items + 1] = item; break end end
  end
end
for _, item in ipairs(items) do
  local p = item.pos
  for _, mesh in ipairs(item.meshes or {}) do
    if item.layer ~= 'scene' or cover[mesh.material] then
    local parts = out[mesh.material] or {}
    out[mesh.material] = parts
    local verts, faces = mesh.verts, mesh.faces
    for k = 1, #faces do
      local v = verts[faces[k].v + 1]
      parts[#parts + 1] = fmt('%.3f %.3f %.3f', v.x + p[1], v.y + p[2], v.z + p[3])
    end
    tris = tris + #faces / 3
    end
  end
end
local result = {ms = ms, tris = tris, mats = {}, stages = stages}
for material, parts in pairs(out) do result.mats[material] = table.concat(parts, ' ') end
return result
'''


class Pack:
    """The region of a country ZIP and its source cells (Python and Lua views of the same bytes)."""

    def __init__(self, path):
        self.zip = zipfile.ZipFile(path)
        names = self.zip.namelist()
        country = json.loads(self.zip.read(next(n for n in names if n.count('/') == 3 and n.endswith('manifest.json'))))
        self.country = country
        self.region = json.loads(self.zip.read(country['regions'][0]['manifest'].lstrip('/')))
        r = self.region
        self.size, self.cell = r['tileSize'], r['cellSize']
        self.blocks = {}
        for key, block in r['blocks'].items():
            data = self.zip.read(block['path'].lstrip('/'))
            bi, bj = map(int, key.split('_'))
            self.blocks[(bi, bj)] = (data, binary.decode_block(data))

    def range(self, i, j):
        b = binary.BLOCK
        block = self.blocks.get((i // b, j // b))
        if not block:
            return None
        hit = block[1].get(binary.slot(i, j))
        return hit and (block[0], hit)

    def cells(self):
        b = binary.BLOCK
        return sorted((bi * b + k % b, bj * b + k // b) for (bi, bj), (_, slots) in self.blocks.items() for k in slots)

    def source(self, i, j):
        hit = self.range(i, j)
        if not hit:
            return None
        data, (at, end) = hit
        rect = [i * self.cell, j * self.cell, (i + 1) * self.cell, (j + 1) * self.cell]
        return binary.decode_cell(data, rect[:2], rect, at, end)

    def geo(self, x, y):
        g = self.region['geo']
        return g[3] + g[6] * x + g[7] * y, g[2] + g[4] * x + g[5] * y


def lua_runtime(pack, generator=None):
    lua = LuaRuntime(unpack_returned_tuples=True)
    load = lua.eval('function(source, name) return assert(load(source, "@" .. name))() end')
    sources = {'cellformat': LUA / 'cellformat.lua', 'generator': Path(generator or LUA / 'generator.lua')}
    mods = {name: load(path.read_text(encoding='utf8'), f'{name}.lua') for name, path in sources.items()}
    build = lua.eval('function(src) return assert(load(src, "@road_quality")) end')(BUILD)

    def data(ci, cj):
        hit = pack.range(ci, cj)
        return hit and bytes(hit[0])

    def slots(ci, cj):
        hit = pack.range(ci, cj)
        # Lua ranges: 1-based first..last bytes of the cell inside the block string
        return hit and lua.table_from([hit[1][0] + 1, hit[1][1]])
    return lua, mods, build, data, slots


def raster(text, rect, shape):
    """Top surface (max z) of triangles given as 'x y z ...' text; NaN where none."""
    out = np.full(shape, np.nan)
    if not text:
        return out
    v = np.array(text.split(), dtype=float).reshape(-1, 3, 3)
    h, w = shape
    for tri in v:
        xs, ys = (tri[:, 0] - rect[0]) / RES - .5, (tri[:, 1] - rect[1]) / RES - .5
        c0, c1 = max(0, math.ceil(xs.min())), min(w - 1, math.floor(xs.max()))
        r0, r1 = max(0, math.ceil(ys.min())), min(h - 1, math.floor(ys.max()))
        if c0 > c1 or r0 > r1:
            continue
        den = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
        if abs(den) < 1e-12:
            continue
        gx, gy = np.meshgrid(np.arange(c0, c1 + 1), np.arange(r0, r1 + 1))
        a = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / den
        b = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / den
        c = 1 - a - b
        inside = (a >= -1e-9) & (b >= -1e-9) & (c >= -1e-9)
        if not inside.any():
            continue
        z = a * tri[0, 2] + b * tri[1, 2] + c * tri[2, 2]
        z = np.where(inside, z, np.nan)
        win = out[r0:r1 + 1, c0:c1 + 1]
        out[r0:r1 + 1, c0:c1 + 1] = np.fmax(win, z)
    return out


def surface(tags):
    return tags.get('bridge') in (None, 'no') and tags.get('tunnel') in (None, 'no') and tags.get('layer') != '-1'


def footprints(roads, rect, shape, inset):
    """Masks of the ground-level carriageways (shrunk by inset) and of bridges/tunnels (grown 6 m)."""
    h, w = shape
    road, skip = Image.new('L', (w, h)), Image.new('L', (w, h))
    dr, ds = ImageDraw.Draw(road), ImageDraw.Draw(skip)

    def px(x, y):
        return ((x - rect[0]) / RES - .5, (y - rect[1]) / RES - .5)
    for r in roads:
        pts = r['points']
        level = surface(r['tags'])
        half = r['width'] / 2 - inset if level else r['width'] / 2 + 6
        if half <= 0:
            continue
        for a, b in zip(pts, pts[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            n = math.hypot(dx, dy)
            if n < 1e-6:
                continue
            nx, ny = -dy / n * half, dx / n * half
            ex, ey = 0, 0
            quad = [px(a[0] + nx + ex, a[1] + ny + ey), px(b[0] + nx - ex, b[1] + ny - ey),
                    px(b[0] - nx - ex, b[1] - ny - ey), px(a[0] - nx + ex, a[1] - ny + ey)]
            (dr if level else ds).polygon(quad, fill=1)
    road, skip = np.array(road, dtype=bool), np.array(skip, dtype=bool)
    return road & ~skip, skip


def edge_steps(roads, rect, top, skip, foot):
    """Per 2 m along each road edge, the largest jump of the visible surface across it."""
    h, w = top.shape
    steps, length = [], 0.
    offsets = np.arange(-.5, 4.001, RES)
    for r in roads:
        if not surface(r['tags']):
            continue
        pts = np.asarray(r['points'], dtype=float)[:, :2]
        half = r['width'] / 2
        for a, b in zip(pts, pts[1:]):
            d = b - a
            n = float(np.hypot(*d))
            if n < 1e-6:
                continue
            t, nrm = d / n, np.array([-d[1], d[0]]) / n
            for s in np.arange(1., n - .999, 2.):
                p = a + t * s
                if not (rect[0] <= p[0] < rect[2] and rect[1] <= p[1] < rect[3]):
                    continue
                for side in (1, -1):
                    q = p[None, :] + nrm[None, :] * side * (half + offsets[:, None])
                    c = np.floor((q[:, 0] - rect[0]) / RES).astype(int)
                    rr = np.floor((q[:, 1] - rect[1]) / RES).astype(int)
                    ok = (c >= 0) & (c < w) & (rr >= 0) & (rr < h)
                    if not ok.all():
                        continue
                    # out past the edge into another street or a bridge: not this edge's business
                    outside = offsets > .3
                    if (foot[rr, c] & outside).any() or skip[rr, c].any():
                        continue
                    z = top[rr, c]
                    z = z[~np.isnan(z)]
                    length += 1
                    steps.append(float(np.abs(np.diff(z)).max()) if len(z) > 1 else 9.)
    return steps, length * 2


def measure(result, src_roads, rect):
    n = int(round((rect[2] - rect[0]) / RES))
    shape = (n, n)
    mats = {k: v for k, v in result['mats'].items()}
    zc = raster(' '.join(mats.get(m, '') for m in CARRIAGEWAY).strip(), rect, shape)
    zw = raster(' '.join(mats.get(m, '') for m in WALK).strip(), rect, shape)
    zg = raster(' '.join(mats.get(m, '') for m in GROUND).strip(), rect, shape)
    zv = raster(' '.join(mats.get(m, '') for m in COVER).strip(), rect, shape)
    foot, skip = footprints(src_roads, rect, shape, EDGE)
    full, _ = footprints(src_roads, rect, shape, 0.)
    px = RES * RES
    curb = foot & ~np.isnan(zw) & (np.isnan(zc) | (zw > zc - .005))
    ground = foot & ~np.isnan(zg) & (np.isnan(zc) | (zg > zc - .005))
    cover = foot & ~np.isnan(zv) & (np.isnan(zc) | (zv > zc - .005)) & ~ground
    pen = np.where(foot & ~np.isnan(zg) & ~np.isnan(zc), zg - zc, -np.inf)
    holes = foot & np.isnan(zc)
    jump = np.zeros(shape, dtype=bool)
    for axis in (0, 1):
        dz = np.abs(np.diff(zc, axis=axis))
        both = (foot[1:, :] & foot[:-1, :]) if axis == 0 else (foot[:, 1:] & foot[:, :-1])
        bad = both & (dz > STEP)
        if axis == 0:
            jump[1:, :] |= bad; jump[:-1, :] |= bad
        else:
            jump[:, 1:] |= bad; jump[:, :-1] |= bad
    top = np.fmax(np.fmax(np.fmax(zc, zw), zg), zv)
    steps, edge_m = edge_steps(src_roads, rect, top, skip, full)
    road_m = 0.
    for r in src_roads:
        if surface(r['tags']):
            p = np.asarray(r['points'], dtype=float)[:, :2]
            # the length inside the tile, roughly: segments whose midpoint is in it
            mid = (p[1:] + p[:-1]) / 2
            inside = (mid[:, 0] >= rect[0]) & (mid[:, 0] < rect[2]) & (mid[:, 1] >= rect[1]) & (mid[:, 1] < rect[3])
            road_m += float(np.linalg.norm(np.diff(p, axis=0), axis=1)[inside].sum())
    defects = {'curb': curb, 'ground': ground | cover, 'holes': holes, 'jump': jump}
    stats = {'road_km': road_m / 1000, 'edge_km': edge_m / 1000, 'foot_m2': float(foot.sum() * px),
             'curb_on_road': float(curb.sum() * px), 'ground_on_road': float(ground.sum() * px), 'cover_on_road': float(cover.sum() * px),
             'ground_max': float(max(0., pen.max())) if foot.any() else 0., 'holes': float(holes.sum() * px),
             'road_step': float(jump.sum() * px), 'edge_steps': steps}
    return stats, defects


def cell_roads(pack, i, j, rect):
    """Source roads reaching the tile, from the cells under it (deduplicated by id)."""
    seen, roads = set(), []
    for ci in range(math.floor(rect[0] / pack.cell), math.ceil(rect[2] / pack.cell)):
        for cj in range(math.floor(rect[1] / pack.cell), math.ceil(rect[3] / pack.cell)):
            cell = pack.source(ci, cj)
            for r in (cell or {}).get('roads', []):
                if r['id'] not in seen:
                    seen.add(r['id']); roads.append(r)
    return roads


SUMS = ('road_km', 'edge_km', 'foot_m2', 'curb_on_road', 'ground_on_road', 'cover_on_road', 'holes', 'road_step', 'ms', 'tris')


def summary(rows):
    total = {k: sum(r[k] for r in rows) for k in SUMS}
    steps = np.array([s for r in rows for s in r['edge_steps']])
    km = max(total['road_km'], 1e-9)
    ekm = max(total['edge_km'], 1e-9)
    return {
        'tiles': len(rows), 'road_km': round(total['road_km'], 1),
        'curb_on_road_m2_km': round(total['curb_on_road'] / km, 2),
        'ground_on_road_m2_km': round(total['ground_on_road'] / km, 2),
        'cover_on_road_m2_km': round(total['cover_on_road'] / km, 2),
        'ground_max_m': round(max(r['ground_max'] for r in rows), 2),
        'holes_m2_km': round(total['holes'] / km, 2),
        'road_step_m2_km': round(total['road_step'] / km, 2),
        'edge_p50_m': round(float(np.percentile(steps, 50)), 3) if len(steps) else 0,
        'edge_p95_m': round(float(np.percentile(steps, 95)), 3) if len(steps) else 0,
        'edge_over_15cm_per_km': round(float((steps > .15).sum()) / ekm, 1),
        'edge_over_30cm_per_km': round(float((steps > .3).sum()) / ekm, 1),
        'ms_per_tile': round(total['ms'] / max(1, len(rows)), 1),
        'tris_per_tile': round(total['tris'] / max(1, len(rows))),
    }


def parse_tune(items):
    tune = {}
    for item in items or []:
        key, value = item.split('=', 1)
        tune[key] = float(value)
    return tune


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--pack', type=Path, default=ROOT / 'out/polygon/earthng_kyiv10.zip')
    p.add_argument('--label', default='run')
    p.add_argument('--step', type=int, default=1, help='every n-th tile in x and y (a quick loop)')
    p.add_argument('--tiles', nargs='*', default=None, help='only these tiles, i_j')
    p.add_argument('--set', nargs='*', help='generator tuning, key=value (options.tune)')
    p.add_argument('--compare', nargs='*', help='labels of earlier runs to print beside this one')
    p.add_argument('--worst', type=int, default=10)
    p.add_argument('--repeat', type=int, default=1, help='build each tile n times, time the fastest')
    p.add_argument('--generator', type=Path, help='another generator.lua (an A/B against an older version)')
    p.add_argument('--out', type=Path, default=ROOT / 'out/polygon/runs')
    a = p.parse_args()
    pack = Pack(a.pack)
    lua, mods, build, data, slots = lua_runtime(pack, a.generator)
    tune = parse_tune(a.set)
    size = pack.size
    per = int(pack.cell // size)
    tiles = sorted({(ci * per + di, cj * per + dj) for ci, cj in pack.cells() for di in range(per) for dj in range(per)})
    if a.tiles:
        wanted = {tuple(map(int, t.split('_'))) for t in a.tiles}
        tiles = [t for t in tiles if t in wanted]
    elif a.step > 1:
        tiles = [t for t in tiles if t[0] % a.step == 0 and t[1] % a.step == 0]
    i0, j0 = min(t[0] for t in tiles), min(t[1] for t in tiles)
    i1, j1 = max(t[0] for t in tiles), max(t[1] for t in tiles)
    thumb = 125           # px per tile in the map: 4 m per pixel
    image = Image.new('RGB', ((i1 - i0 + 1) * thumb, (j1 - j0 + 1) * thumb), (24, 24, 24))
    rows, started, stages = [], time.perf_counter(), {}
    lua_tune = lua.table_from(tune)
    lua_cover = lua.table_from({m: True for m in COVER})
    for n, (i, j) in enumerate(tiles):
        rect = [i * size, j * size, (i + 1) * size, (j + 1) * size]
        result = build(mods['generator'], mods['cellformat'], data, slots, i, j, size, pack.cell,
                       pack.region.get('seaLevel'), lua_tune, lua_cover, a.repeat)
        if result is None:
            continue
        res = {'ms': result.ms, 'tris': result.tris, 'mats': dict(result.mats.items())}
        for name, ms in result.stages.items():
            stages[name] = stages.get(name, 0.) + ms
        stats, defects = measure(res, cell_roads(pack, i, j, rect), rect)
        stats.update(tile=f'{i}_{j}', ms=res['ms'], tris=res['tris'])
        rows.append(stats)
        # map: footprint grey, holes magenta, ground on road green, curb red, jumps yellow
        rgb = np.zeros(defects['curb'].shape + (3,), dtype=np.uint8)
        rgb[defects['holes']] = (200, 0, 200)
        rgb[defects['ground']] = (40, 200, 40)
        rgb[defects['jump']] = (240, 220, 0)
        rgb[defects['curb']] = (255, 40, 40)
        tile = Image.fromarray(rgb[::-1]).resize((thumb, thumb), Image.BOX)
        image.paste(tile, ((i - i0) * thumb, (j1 - j) * thumb))
        if (n + 1) % 20 == 0 or n + 1 == len(tiles):
            print(f'{n + 1}/{len(tiles)} tiles, {time.perf_counter() - started:.0f} s', flush=True)
    summ = summary(rows)
    for r in rows:
        r['score'] = r['curb_on_road'] + r['ground_on_road'] + r['cover_on_road'] + r['holes'] + r['road_step'] + 2 * sum(s > .3 for s in r['edge_steps'])
    worst = []
    for r in sorted(rows, key=lambda r: -r['score'])[:a.worst]:
        i, j = map(int, r['tile'].split('_'))
        lat, lon = pack.geo((i + .5) * size, (j + .5) * size)
        worst.append({'tile': r['tile'], 'lat': round(lat, 5), 'lon': round(lon, 5), 'score': round(r['score'], 1),
                      'curb': round(r['curb_on_road'], 1), 'ground': round(r['ground_on_road'], 1), 'cover': round(r['cover_on_road'], 1),
                      'holes': round(r['holes'], 1), 'step': round(r['road_step'], 1)})
    out = a.out / a.label
    out.mkdir(parents=True, exist_ok=True)
    image.save(out / 'map.png')
    for r in rows:
        steps = r.pop('edge_steps')
        r['edge_over_30cm'] = int(sum(s > .3 for s in steps))
    (out / 'report.json').write_text(json.dumps({'pack': str(a.pack), 'tune': tune, 'summary': summ,
                                                 'worst': worst, 'tiles': rows}, indent=1), encoding='utf8')
    labels = [a.label] + [c for c in (a.compare or []) if c != a.label]
    table = {a.label: summ}
    for c in labels[1:]:
        path = a.out / c / 'report.json'
        if path.exists():
            table[c] = json.loads(path.read_text(encoding='utf8'))['summary']
    keys = list(summ)
    print(f'{"":>24}' + ''.join(f'{c:>14}' for c in table))
    for k in keys:
        print(f'{k:>24}' + ''.join(f'{str(table[c].get(k, "")):>14}' for c in table))
    if stages:
        print('ms/tile by stage: ' + ', '.join(f'{k} {v / max(1, len(rows)):.1f}' for k, v in stages.items()))
    print('worst:')
    for w in worst:
        print('  ', w)
    print(f'report: {out / "report.json"}')


if __name__ == '__main__':
    main()
