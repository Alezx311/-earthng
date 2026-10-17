"""Native generator bench outside the game: LuaJIT (lupa) over a real country ZIP.

  python earthng/bench_native.py --pack out/native-packs/earthng_zanzibar.zip [--lat .. --lon ..]

Two parts. Tiles: generator.build of the tiles round the landing, one at a time and without
pauses (ms, triangles, vertices, Lua garbage). Stream: native.update frame by frame with the
game's budget until the landing is ready and the radius is built (per-frame ms, the longest
single resume, resumes per frame, garbage per frame).
"""
import argparse
import json
from pathlib import Path
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'out/test-tools'))
from lupa.luajit21 import LuaRuntime

LUA = ROOT / 'mod/lua/ge/extensions/e2r'


def runtime(pack):
    archive = zipfile.ZipFile(pack)
    names = {'/' + n for n in archive.namelist()}
    lua = LuaRuntime(unpack_returned_tuples=True)

    def convert(value):
        if isinstance(value, dict):
            return lua.table_from({k: convert(v) for k, v in value.items()})
        if isinstance(value, list):
            return lua.table_from([convert(v) for v in value])
        return value

    stats = {'readMs': 0.0, 'reads': 0}

    def read_bytes(path):
        if path not in names:
            return None
        t = time.perf_counter()
        data = archive.read(path[1:])
        stats['readMs'] += (time.perf_counter() - t) * 1000
        stats['reads'] += 1
        return data

    g = lua.globals()
    g.py_exists = lambda p: p in names
    g.clock = time.perf_counter
    g.py_json = lambda p: convert(json.loads(archive.read(p[1:]))) if p in names else None
    g.py_bytes = read_bytes
    g.py_manifests = lambda: convert(sorted(n for n in names if n.count('/') == 4 and n.endswith('/manifest.json')))
    lua.execute('''
      FS={fileExists=function(_,p) return py_exists(p) end, findFiles=function() return py_manifests() end}
      jsonReadFile=function(p) return py_json(p) end
      io.open=function(p) local data=py_bytes(p); if not data then return nil end
        return {read=function() return data end, close=function() end} end
      hptimer=function() local t0=clock()
        return {stop=function() return (clock()-t0)*1000 end,
                stopAndReset=function() local n=clock(); local d=(n-t0)*1000; t0=n; return d end} end
    ''')
    mods = {}
    load = lua.eval('function(source, name) return assert(load(source, "@" .. name))() end')
    for name in ('countries', 'coordinates', 'cellformat', 'generator'):
        mods[name] = load((LUA / f'{name}.lua').read_text(encoding='utf8'), f'{name}.lua')
    g.mods = convert({})
    for name, mod in mods.items():
        g.mods[name] = mod
    lua.execute("require=function(name) return mods[name:match('[^/]+$')] end")
    mods['native'] = load((LUA / 'native.lua').read_text(encoding='utf8'), 'native.lua')
    return lua, mods, stats


TILES = '''
local native, generator, cellformat, i0, j0, radius = ...
local st = native.status()
local out = {}
local r
-- the active region after land(): reach it through the module's upvalues
local function upvalue(f, name)
  for k = 1, 200 do local n, v = debug.getupvalue(f, k); if not n then return end; if n == name then return v end end
end
local active = upvalue(native.update, 'active')
r = active.region
local d = r.data
-- the module's own cell loader (v1 JSON or v2 blocks), run to the end in a coroutine
local loadCell = upvalue(upvalue(native.update, 'buildTile'), 'loadCell')
local function cellOf(i, j, keep)
  local co = coroutine.create(function() return loadCell(string.format('%d_%d', i, j), i, j, keep) end)
  while true do
    local ok, cell = coroutine.resume(co)
    if not ok then return nil end
    if coroutine.status(co) == 'dead' then return cell end
  end
end
local cells = {}
for di = -radius, radius do
  for dj = -radius, radius do
    local i, j = i0 + di, j0 + dj
    local ci, cj = math.floor(i * 500 / d.cellSize), math.floor(j * 500 / d.cellSize)
    local ck = i .. '_' .. j
    cellOf(ci, cj, {0, 0, 1, 1})  -- the block into the module's cache: decode time alone below
    collectgarbage('collect')
    local kbDecode = collectgarbage('count')
    local t = clock()
    cells[ck] = cellOf(ci, cj, {i * 500, j * 500, (i + 1) * 500, (j + 1) * 500}) or false
    local decodeMs = (clock() - t) * 1000
    local decodeKb = collectgarbage('count') - kbDecode
    if cells[ck] then
      for _, far in ipairs({false, true}) do
        collectgarbage('collect')
        local kb0 = collectgarbage('count')
        collectgarbage('stop')
        local pauses = 0
        local t1 = clock()
        local road, scene, farM = generator.build({cells[ck]}, i, j, {tileSize = 500, far = far,
          pause = function() pauses = pauses + 1 end, seaLevel = d.seaLevel})
        local ms = (clock() - t1) * 1000
        local kb = collectgarbage('count') - kb0
        collectgarbage('restart')
        local tris, verts, items, statics, tables = 0, 0, 0, 0, 0
        for _, m in ipairs({road, scene, farM}) do
          for _, it in ipairs(m and m.items or {}) do
            items = items + 1
            if it.kind == 'static' then statics = statics + 1 end
            for _, mesh in ipairs(it.meshes or {}) do
              verts = verts + #mesh.verts; tris = tris + #mesh.faces / 3
              local seen, n = {}, 0
              for _, f in ipairs(mesh.faces) do if not seen[f] then seen[f] = true; n = n + 1 end end
              tables = tables + #mesh.verts + #mesh.normals + #mesh.uvs + n
            end
          end
        end
        out[#out + 1] = {key = i .. '_' .. j, far = far, ms = ms, decodeMs = far and 0 or decodeMs, decodeKb = far and 0 or decodeKb, kb = kb,
          tris = tris, verts = verts, tables = tables, items = items, statics = statics, pauses = pauses}
      end
    end
  end
end
return out
'''

STREAM = '''
local native, budget, frames, speed = ...
local log = {}
local function upvalue(f, name)
  for k = 1, 200 do local n, v = debug.getupvalue(f, k); if not n then return end; if n == name then return v end end
end
local resumeMax, resumes, long = 0, 0, {}
local realResume = coroutine.resume
local function where(co)
  local tb = debug.traceback(co, '', 0)
  local out = {}
  for line in tb:gmatch('[^' .. string.char(10) .. ']+') do
    local f = line:match('(%w+%.lua:%d+)')
    if f and not f:find('^bench') then out[#out + 1] = f end
    if #out >= 3 then break end
  end
  return table.concat(out, ' < ')
end
coroutine.resume = function(co, ...)
  local before = where(co)
  local t = clock()
  local r = {realResume(co, ...)}
  local ms = (clock() - t) * 1000
  resumes = resumes + 1
  if ms > resumeMax then resumeMax = ms end
  if ms > 12 then long[#long + 1] = string.format('%6.1f ms  from %s  to %s', ms, before,
    coroutine.status(co) == 'dead' and 'end' or where(co)) end
  return unpack(r)
end
local readyFrame
local x, y
for f = 1, frames do
  resumeMax, resumes = 0, 0
  local kb0 = collectgarbage('count')
  local t = clock()
  native.update(1 / 60, budget)
  local ms = (clock() - t) * 1000
  local st = native.status()
  if st.job.state == 'error' then error(st.job.message) end
  if st.job.state == 'ready' and not readyFrame then
    readyFrame = f
    local p = st.job.manifest.spawn.pos
    x, y = p[1], p[2]
  end
  if readyFrame then
    if f == readyFrame + 1 then native.pose({x = x, y = y, vx = 0, vy = 0, state = 'driving'}) end
    if speed > 0 and f > readyFrame + 1 then
      x = x + speed / 60
      native.pose({x = x, y = y, vx = speed, vy = 0, state = 'driving'})
    end
  end
  log[f] = {ms = ms, resumeMax = resumeMax, resumes = resumes, kb = collectgarbage('count') - kb0,
    built = st.stream.built, queued = st.stream.queued, ready = st.stream.readyCount}
end
coroutine.resume = realResume
return {frames = log, readyFrame = readyFrame, long = long}
'''


def pct(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(q * (len(v) - 1) + .5))] if v else 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--pack', type=Path, default=ROOT / 'out/native-packs/earthng_zanzibar.zip')
    p.add_argument('--lat', type=float)
    p.add_argument('--lon', type=float)
    p.add_argument('--radius', type=int, default=2, help='tiles round the landing tile for the tile bench')
    p.add_argument('--frames', type=int, default=1200)
    p.add_argument('--budget', type=float, default=4)
    p.add_argument('--speed', type=float, default=0, help='m/s along +x after the landing (stream part)')
    p.add_argument('--skip-tiles', action='store_true')
    p.add_argument('--json', type=Path)
    a = p.parse_args()
    lua, mods, io_stats = runtime(a.pack)
    native = mods['native']
    st = native.refresh()
    country = next(c for c in st.countries.values() if c.available)
    lat = a.lat if a.lat is not None else country.landing.lat
    lon = a.lon if a.lon is not None else country.landing.lon
    report = {'pack': str(a.pack), 'lat': lat, 'lon': lon}
    job = native.land(lua.table_from({'seq': 1, 'lat': lat, 'lon': lon}))
    if job.state == 'error':
        sys.exit(job.message)
    if not a.skip_tiles:
        land = lua.eval('function(native) local n,v; for k=1,200 do n,v=debug.getupvalue(native.update,k); if n=="active" then return v.landing.x, v.landing.y end end end')(native)
        i0, j0 = int(land[0] // 500), int(land[1] // 500)
        rows = [dict(r) for r in lua.execute(TILES, native, mods['generator'], mods['cellformat'], i0, j0, a.radius).values()]
        rows = [{k: v for k, v in r.items()} for r in rows]
        near = [r for r in rows if not r['far']]
        far = [r for r in rows if r['far']]
        print(f'{"tile":>10} {"layer":>5} {"ms":>8} {"decode":>7} {"tris":>7} {"verts":>7} {"tables":>7} {"items":>5} {"stat":>4} {"KB":>8} {"pause":>6}')
        for r in rows:
            print(f'{r["key"]:>10} {"far" if r["far"] else "near":>5} {r["ms"]:8.1f} {r["decodeMs"]:7.1f} {int(r["tris"]):7d} '
                  f'{int(r["verts"]):7d} {int(r["tables"]):7d} {int(r["items"]):5d} {int(r["statics"]):4d} {r["kb"]:8.0f} {int(r["pauses"]):6d}')
        for name, group in (('near', near), ('far', far)):
            if group:
                print(f'{name}: tiles {len(group)}, ms mean {sum(r["ms"] for r in group) / len(group):.1f} '
                      f'max {max(r["ms"] for r in group):.1f}, KB mean {sum(r["kb"] for r in group) / len(group):.0f}, '
                      f'verts/tri {sum(r["verts"] for r in group) / max(1, sum(r["tris"] for r in group)):.2f}, '
                      f'tables/tri {sum(r["tables"] for r in group) / max(1, sum(r["tris"] for r in group)):.2f}')
        report['tiles'] = rows
        # land again: the tile bench left the module's caches warm
        native.refresh()
        native.land(lua.table_from({'seq': 2, 'lat': lat, 'lon': lon}))
    io_stats['readMs'], io_stats['reads'] = 0.0, 0
    started = time.perf_counter()
    out = lua.execute(STREAM, native, a.budget, a.frames, a.speed)
    wall = time.perf_counter() - started
    frames = [dict(f) for f in out.frames.values()]
    ms = [f['ms'] for f in frames]
    resume = [f['resumeMax'] for f in frames]
    kb = [f['kb'] for f in frames if f['kb'] > 0]
    ready = out.readyFrame
    built = frames[-1]['built']
    print(f'stream: budget {a.budget} ms, ready at frame {ready}, built {built} tiles in {len(frames)} frames '
          f'({wall:.1f} s wall), queued at end {frames[-1]["queued"]}')
    print(f'  update ms: mean {sum(ms) / len(ms):.2f} p99 {pct(ms, .99):.1f} max {max(ms):.1f}; '
          f'longest resume max {max(resume):.1f}; resumes/frame mean {sum(f["resumes"] for f in frames) / len(frames):.0f}; '
          f'frames over 2x budget {sum(m > 2 * a.budget for m in ms)}; garbage KB/frame mean {sum(kb) / max(1, len(kb)):.0f}')
    print(f'  zip reads {io_stats["reads"]}, {io_stats["readMs"]:.0f} ms')
    for line in list(out.long.values())[:25]:
        print('  long:', line)
    report['stream'] = {'ready': ready, 'built': built, 'frames': len(frames), 'meanMs': sum(ms) / len(ms),
                        'p99Ms': pct(ms, .99), 'maxMs': max(ms), 'resumeMax': max(resume),
                        'over2x': sum(m > 2 * a.budget for m in ms), 'reads': io_stats['reads']}
    if a.json:
        a.json.write_text(json.dumps(report, indent=1), encoding='utf8')


if __name__ == '__main__':
    main()
