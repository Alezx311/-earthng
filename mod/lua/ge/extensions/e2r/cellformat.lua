-- Binary source cells of native country packages (earthng-features-v2). The format and its
-- reference encoder live in earthng/country_format.py: LEB128 varints, zigzag for signed
-- values, centimetre coordinates as deltas from the cell origin, decimetre terrain heights.
-- The decoder builds exactly the tables the v1 JSON cells held, so the generator is shared.
local M = {}
local byte, sub, floor = string.byte, string.sub, math.floor
local KINDS = {'roads', 'junctions', 'buildings', 'areas', 'lines', 'points'}
M.BLOCK = 8
M.KINDS = KINDS

-- Byte ranges {slot = {first, last}} of the cells in a block file (1-based, inclusive).
function M.block(data)
  if type(data) ~= 'string' or sub(data, 1, 4) ~= 'EGB2' then error('not an EarthNG source block') end
  local pos, size = 5, #data
  local function u()
    local value, mul = 0, 1
    while true do
      if pos > size or mul > 2 ^ 49 then error('truncated source block') end
      local b = byte(data, pos)
      pos = pos + 1
      if b < 128 then return value + b * mul end
      value, mul = value + (b - 128) * mul, mul * 128
    end
  end
  local count, entries = u(), {}
  if count > M.BLOCK * M.BLOCK then error('too many cells in a source block') end
  for k = 1, count do entries[k] = {u(), u()} end
  local out, at = {}, pos
  for _, e in ipairs(entries) do
    if e[1] >= M.BLOCK * M.BLOCK or out[e[1]] then error('invalid source block slot') end
    out[e[1]] = {at, at + e[2] - 1}
    at = at + e[2]
  end
  if at ~= size + 1 then error('source block size mismatch') end
  return out
end

-- Margins round a tile for M.cell's keep filter: a road shapes the ground up to 70 m from its
-- edge (generator PULL), so roads are kept 120 m out (half of an 80 m road + 70 m + slack);
-- everything else only matters inside the tile (parked cars reach 3 m past it).
M.ROAD_MARGIN, M.MARGIN = 120, 5

-- One cell from data[first..last]; ox, oy: its south-west corner in region metres.
-- keep (optional {x0, y0, x1, y1}): only features whose bounds reach it (with the margins above)
-- become tables; the rest are read past. A tile needs a quarter of a 1 km cell, and decoded
-- points are what the game's collector walks, so a tile's cell is not decoded whole.
function M.cell(data, first, last, ox, oy, rect, pause, keep)
  pause = pause or function() end
  local pos = first
  local function u()
    local value, mul = 0, 1
    while true do
      if pos > last or mul > 2 ^ 49 then error('truncated source cell') end
      local b = byte(data, pos)
      pos = pos + 1
      if b < 128 then return value + b * mul end
      value, mul = value + (b - 128) * mul, mul * 128
    end
  end
  local function s()
    local v = u()
    if v % 2 == 0 then return v / 2 end
    return -(v + 1) / 2
  end
  local strings = {}
  for k = 1, u() do
    local n = u()
    if pos + n - 1 > last then error('truncated source string') end
    strings[k] = sub(data, pos, pos + n - 1)
    pos = pos + n
  end
  local function ref()
    local v = strings[u() + 1]
    if v == nil then error('invalid source string reference') end
    return v
  end
  local function tags()
    local out = {}
    for _ = 1, u() do local key = ref(); out[key] = ref() end
    return out
  end
  local ocx, ocy = floor(ox * 100 + .5), floor(oy * 100 + .5)
  -- Coordinates go to a scratch buffer first (bounds while reading); tables only when kept.
  local buf, nbuf, bx0, by0, bx1, by1, work = {}, 0, 0, 0, 0, 0, 0
  local function read(minimum)
    local n = u()
    if n < minimum then error('too few source coordinates') end
    local px, py, pz = ocx, ocy, 0
    for _ = 1, n do
      px, py, pz = px + s(), py + s(), pz + s()
      local x, y = px / 100, py / 100
      buf[nbuf + 1], buf[nbuf + 2], buf[nbuf + 3] = x, y, pz / 100
      nbuf = nbuf + 3
      work = work + 1
      if work >= 1024 then work = 0; pause() end
      if x < bx0 then bx0 = x end
      if x > bx1 then bx1 = x end
      if y < by0 then by0 = y end
      if y > by1 then by1 = y end
    end
    return n
  end
  local function start() nbuf, bx0, by0, bx1, by1 = 0, math.huge, math.huge, -math.huge, -math.huge end
  local function wanted(margin)
    return not keep or (bx0 <= keep[3] + margin and bx1 >= keep[1] - margin and by0 <= keep[4] + margin and by1 >= keep[2] - margin)
  end
  local function points(from, n)
    local out = {}
    for k = 1, n do
      local at = from + 3 * (k - 1)
      out[k] = {buf[at + 1], buf[at + 2], buf[at + 3]}
    end
    return out
  end
  local function rings()
    local n = u()
    if n < 1 then error('source polygon without rings') end
    local counts = {}
    for k = 1, n do counts[k] = read(3) end
    return counts
  end
  local function ringTables(counts)
    local out, at = {}, 0
    for k = 1, #counts do out[k] = points(at, counts[k]); at = at + 3 * counts[k] end
    return out
  end
  local nx, ny = u(), u()
  if nx < 2 or ny < 2 or nx > 257 or ny > 257 then error('invalid source elevation grid') end
  local heights, h = {}, 0
  for k = 1, nx * ny do h = h + s(); heights[k] = h / 10 end
  local cell = {terrain = {rect = rect, nx = nx, ny = ny, heights = heights}}
  local done = 0
  for _, kind in ipairs(KINDS) do
    local list = {}
    for _ = 1, u() do
      local f = {id = ref()}
      start()
      local keepIt
      if kind == 'roads' then
        f.width = u() / 100; f.tags = tags()
        local nodes = {}
        for n = 1, u() do nodes[n] = ref() end
        f.nodes = nodes
        local n = read(2)
        keepIt = wanted(M.ROAD_MARGIN)
        if keepIt then f.points = points(0, n) end
      elseif kind == 'junctions' then
        f.height = s() / 100
        local counts = rings()
        keepIt = wanted(M.MARGIN)
        if keepIt then f.rings = ringTables(counts) end
      elseif kind == 'buildings' then
        f.kind = ref(); f.height = u() / 100; f.minHeight = u() / 100
        f.tags = tags()
        local counts = rings()
        keepIt = wanted(M.MARGIN)
        if keepIt then f.rings = ringTables(counts) end
      elseif kind == 'areas' then
        f.kind = ref(); f.tags = tags()
        local counts = rings()
        keepIt = wanted(M.MARGIN)
        if keepIt then f.rings = ringTables(counts) end
      elseif kind == 'lines' then
        f.kind = ref(); f.tags = tags()
        local n = read(2)
        keepIt = wanted(M.MARGIN)
        if keepIt then f.points = points(0, n) end
      else
        f.kind = ref(); f.tags = tags()
        read(1)
        keepIt = wanted(M.MARGIN)
        if keepIt then f.pos = points(0, 1)[1] end
      end
      if keepIt then list[#list + 1] = f end
      done = done + 1
      if done % 128 == 0 then pause() end
    end
    cell[kind] = list
  end
  if pos ~= last + 1 then error('trailing bytes after source cell') end
  return cell
end

-- Only the road centre lines of a cell, for the map: {id = .., highway = .., xy = {x1, y1, ...}}.
-- Terrain and every later section are skipped; nothing else is built.
function M.roads(data, first, last, ox, oy)
  local pos = first
  local function u()
    local value, mul = 0, 1
    while true do
      if pos > last or mul > 2 ^ 49 then error('truncated source cell') end
      local b = byte(data, pos)
      pos = pos + 1
      if b < 128 then return value + b * mul end
      value, mul = value + (b - 128) * mul, mul * 128
    end
  end
  local function s()
    local v = u()
    if v % 2 == 0 then return v / 2 end
    return -(v + 1) / 2
  end
  local strings = {}
  for k = 1, u() do
    local n = u()
    strings[k] = sub(data, pos, pos + n - 1)
    pos = pos + n
  end
  local nx, ny = u(), u()
  for _ = 1, nx * ny do u() end
  local out, ocx, ocy = {}, floor(ox * 100 + .5), floor(oy * 100 + .5)
  for k = 1, u() do
    local road = {id = strings[u() + 1]}
    u()
    for _ = 1, u() do
      local key, value = strings[u() + 1], strings[u() + 1]
      if key == 'highway' then road.highway = value end
    end
    for _ = 1, u() do u() end
    local xy, px, py = {}, ocx, ocy
    for n = 1, u() do
      px, py = px + s(), py + s()
      s()
      xy[2 * n - 1], xy[2 * n] = px / 100, py / 100
    end
    road.xy = xy
    out[k] = road
  end
  return out
end

-- Is slot k (0..63) present in a 16-hex-digit block mask? Digit d holds slots 4d..4d+3.
function M.present(mask, k)
  local digit = tonumber(sub(mask, floor(k / 4) + 1, floor(k / 4) + 1), 16)
  return digit ~= nil and floor(digit / 2 ^ (k % 4)) % 2 == 1
end

return M
