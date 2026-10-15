-- Engine-native EarthNG provider. Country ZIPs contain source cells; the Lua
-- generator creates the same road/scene manifests as the streaming renderer.
-- Nothing is written to the user's filesystem and no companion process is used.
local M = {}
local catalog = require('ge/extensions/e2r/countries')
local generator = require('ge/extensions/e2r/generator')
local coordinates = require('ge/extensions/e2r/coordinates')
local CELL_LIMIT, TILE_LIMIT, TRIANGLE_LIMIT, VERTEX_LIMIT, LAND_RADIUS = 32, 32, 80000, 240000, 500
local state, regions, active

local function finite(v)
  return type(v) == 'number' and v == v and v ~= math.huge and v ~= -math.huge
end

local function array(v)
  if type(v) ~= 'table' then return false end
  local n = 0
  for k in pairs(v) do
    if type(k) ~= 'number' or k < 1 or k % 1 ~= 0 then return false end
    n = n + 1
  end
  return n == #v
end

local function vector(v, n)
  if not array(v) or #v ~= n then return false end
  for _, x in ipairs(v) do if not finite(x) then return false end end
  return true
end

local function rect(v)
  return vector(v, 4) and v[1] < v[3] and v[2] < v[4]
end

local function geoBox(v)
  return rect(v) and v[1] >= -180 and v[3] <= 180 and v[2] >= -90 and v[4] <= 90
end

local function inside(b, x, y)
  return x >= b[1] and x <= b[3] and y >= b[2] and y <= b[4]
end

local function insideAny(boxes, x, y)
  for _, b in ipairs(boxes or {}) do if inside(b, x, y) then return true end end
  return false
end

local function pathIn(path, prefix)
  if type(path) ~= 'string' or path:sub(1, #prefix) ~= prefix
    or path:find('[%z\1-\31\\:%?%#%%]') or path:find('//', 1, true) then return false end
  for p in path:gmatch('[^/]+') do if p == '.' or p == '..' then return false end end
  return path:sub(-5) == '.json'
end

local function exists(path)
  local ok, found = pcall(function() return FS:fileExists(path) end)
  return ok and found == true
end

local function read(path)
  if not exists(path) then error('missing mounted source: ' .. path) end
  local ok, value = pcall(jsonReadFile, path)
  if not ok or type(value) ~= 'table' then error('invalid source JSON: ' .. path) end
  return value
end

local function keyOf(i, j) return string.format('%d_%d', i, j) end

local function geographic(r, x, y)
  if r.projection then return r.projection.geographic(x, y) end
  local g = r.data.geo
  local dx, dy = x - g[1], y - g[2]
  return g[3] + g[5] * dx + g[6] * dy, g[4] + g[7] * dx + g[8] * dy
end

local function localPoint(r, lat, lon)
  if r.projection then return r.projection.localPoint(lat, lon) end
  local g = r.data.geo
  local dx, dy = lon - g[3], lat - g[4]
  local determinant = g[5] * g[8] - g[6] * g[7]
  return g[1] + (dx * g[8] - dy * g[6]) / determinant,
    g[2] + (dy * g[5] - dx * g[7]) / determinant
end

local function affineAt(r, x, y)
  return r.projection and r.projection.affineAt(x, y) or r.data.geo
end

-- Source metadata is an exact metric grid. Geographic bounding boxes alone are
-- insufficient: projected neighbouring cells have overlapping geographic boxes.
local function covered(r, x0, y0, x1, y1)
  local s = r.data.cellSize
  local i0, j0 = math.floor((x0 + 0.000001) / s), math.floor((y0 + 0.000001) / s)
  local i1, j1 = math.ceil((x1 - 0.000001) / s) - 1, math.ceil((y1 - 0.000001) / s) - 1
  for i = i0, i1 do
    for j = j0, j1 do if not r.data.cells[keyOf(i, j)] then return false end end
  end
  return true
end

local function geoRect(r, b)
  local w, s, e, n = math.huge, math.huge, -math.huge, -math.huge
  for _, p in ipairs({{b[1], b[2]}, {b[3], b[2]}, {b[1], b[4]}, {b[3], b[4]}}) do
    local lon, lat = geographic(r, p[1], p[2])
    w, s, e, n = math.min(w, lon), math.min(s, lat), math.max(e, lon), math.max(n, lat)
  end
  return {w, s, e, n}
end

local function addIssue(country, region, message)
  state.errors[#state.errors + 1] = {id = country.id, region = region and region.id,
    path = region and region.manifest or country.manifest, code = 'native-source', message = tostring(message)}
end

local function validateRegion(country, descriptor)
  local d = read(descriptor.manifest)
  if d.schemaVersion ~= 1 or d.id ~= descriptor.id or d.runtime ~= 'lua' then
    error('region must use schemaVersion 1, its declared id and runtime lua')
  end
  if d.tileSize ~= 500 or not finite(d.cellSize) or d.cellSize < d.tileSize
    or d.cellSize > 10000 or d.cellSize % d.tileSize ~= 0 then error('invalid tileSize/cellSize grid') end
  if not geoBox(d.bounds) or not vector(d.geo, 8) then error('invalid region bounds/geo affine') end
  if math.abs(d.geo[5] * d.geo[8] - d.geo[6] * d.geo[7]) < 1e-18 then error('singular geo affine') end
  if type(d.cells) ~= 'table' then error('region cells must be an object') end
  local prefix, count = country.namespace, 0
  for key, cell in pairs(d.cells) do
    local i, j
    if type(key) == 'string' then i, j = key:match('^(-?%d+)_(-?%d+)$') end
    i, j = tonumber(i), tonumber(j)
    if not i or not j or math.abs(i) > 1000000 or math.abs(j) > 1000000 or key ~= keyOf(i, j) or type(cell) ~= 'table' or not rect(cell.bounds)
      or not pathIn(cell.path, prefix) or not exists(cell.path) then error('invalid or missing source cell: ' .. tostring(key)) end
    local expected = {i * d.cellSize, j * d.cellSize, (i + 1) * d.cellSize, (j + 1) * d.cellSize}
    for k = 1, 4 do if math.abs(cell.bounds[k] - expected[k]) > 0.001 then error('source cell bounds do not match grid: ' .. key) end end
    count = count + 1
  end
  if count == 0 then error('region has no mounted source cells') end
  local r = {country = country, descriptor = descriptor, data = d, safe = {}, coverage = {},
    projection = type(coordinates.frame) == 'function' and coordinates.frame(d.frame) or nil}
  -- Erode the exported cell union by the startup radius. Each advertised rectangle
  -- can land with the full 1 km neighbourhood, including at holes and outer edges.
  for _, cell in pairs(d.cells) do
    local b, t = cell.bounds, d.tileSize
    for x = b[1], b[3] - t, t do
      for y = b[2], b[4] - t, t do
        if covered(r, x - LAND_RADIUS, y - LAND_RADIUS, x + t + LAND_RADIUS, y + t + LAND_RADIUS) then
          local safe = {x, y, x + t, y + t}
          r.safe[#r.safe + 1] = safe
          r.coverage[#r.coverage + 1] = geoRect(r, safe)
        end
      end
    end
  end
  table.sort(r.safe, function(a, b) return a[1] == b[1] and a[2] < b[2] or a[1] < b[1] end)
  return r
end

local function refresh()
  local countries, errors = catalog.refresh()
  regions, active = {}, nil
  state = {provider = 'native', native = true, countries = countries, errors = {}, coverage = {}, regions = {},
    countryCount = 0, job = nil, stream = nil}
  for _, issue in ipairs(errors or {}) do state.errors[#state.errors + 1] = issue end
  for _, country in ipairs(countries) do
    local accepted = 0
    if country.runtime == 'native' and country.sourceFormat == 'earthng-features-v1' then
      for _, descriptor in ipairs(country.regions) do
        local ok, r = pcall(validateRegion, country, descriptor)
        if ok and #r.safe > 0 then
          regions[#regions + 1] = r
          accepted = accepted + 1
          if not country.landing then
            local first = r.safe[1]
            local lon, lat = geographic(r, (first[1] + first[3]) / 2, (first[2] + first[4]) / 2)
            country.landing = {lat = lat, lon = lon}
          end
          state.regions[#state.regions + 1] = {id = descriptor.id, country = country.id, title = descriptor.title or country.title,
            bounds = r.data.bounds, coverage = r.coverage}
          for _, box in ipairs(r.coverage) do
            state.coverage[#state.coverage + 1] = box
            local b = state.packageBox
            state.packageBox = b and {math.min(b[1], box[1]), math.min(b[2], box[2]), math.max(b[3], box[3]), math.max(b[4], box[4])}
              or {box[1], box[2], box[3], box[4]}
          end
        else
          addIssue(country, descriptor, ok and 'no complete landing neighbourhood in exported source cells' or r)
        end
      end
    elseif country.runtime ~= 'source' then
      addIssue(country, nil, 'country requires runtime native and sourceFormat earthng-features-v1')
    end
    country.available = accepted > 0
    country.reason = country.available and nil or (country.runtime == 'source' and 'source-only' or 'no-playable-source')
    if country.available then state.countryCount = state.countryCount + 1 end
  end
  state.catalog = {countries = state.countries, errors = state.errors}
  return state
end

local function ensure() if not state then refresh() end end

local function at(lat, lon)
  ensure()
  if not finite(lat) or not finite(lon) or lat < -90 or lat > 90 or lon < -180 or lon > 180 then return nil end
  for _, r in ipairs(regions) do
    if insideAny(r.country.coverage, lon, lat) and insideAny(r.descriptor.coverage, lon, lat) then
      local x, y = localPoint(r, lat, lon)
      if insideAny(r.safe, x, y) and covered(r, x - LAND_RADIUS, y - LAND_RADIUS, x + LAND_RADIUS, y + LAND_RADIUS) then
        return r.country, r.descriptor, x, y, r
      end
    end
  end
end

local function touch() active.clock = active.clock + 1; return active.clock end

local function evict(cache, limit, protected, onRemove)
  local count = 0
  for _ in pairs(cache) do count = count + 1 end
  while count > limit do
    local oldest, oldestAt
    for key, entry in pairs(cache) do
      if not (protected and protected[key]) and (not oldestAt or entry.used < oldestAt) then oldest, oldestAt = key, entry.used end
    end
    if not oldest then break end
    cache[oldest] = nil
    if onRemove then onRemove(oldest) end
    count = count - 1
  end
end

local function loadCell(key, descriptor)
  local hit = active.cells[key]
  if hit then hit.used = touch(); return hit.data end
  local cell = read(descriptor.path)
  for _, field in ipairs({'roads', 'junctions', 'buildings', 'areas', 'lines', 'points'}) do
    if cell[field] ~= nil and not array(cell[field]) then error('source cell ' .. field .. ' must be an array') end
  end
  local terrain = cell.terrain
  if type(terrain) ~= 'table' or not rect(terrain.rect) or not finite(terrain.nx) or not finite(terrain.ny)
    or terrain.nx % 1 ~= 0 or terrain.ny % 1 ~= 0 or terrain.nx < 2 or terrain.ny < 2
    or terrain.nx > 257 or terrain.ny > 257 or not array(terrain.heights)
    or #terrain.heights ~= terrain.nx * terrain.ny then error('invalid source elevation grid') end
  for _, height in ipairs(terrain.heights) do if not finite(height) then error('invalid source elevation sample') end end
  local checked = 0
  local function coordinates(points, minimum)
    if not array(points) or #points < minimum then error('invalid source geometry coordinates') end
    for _, p in ipairs(points) do
      if not array(p) or (#p ~= 2 and #p ~= 3) then error('invalid source geometry point') end
      for _, v in ipairs(p) do if not finite(v) or math.abs(v) > 1e12 then error('non-finite source geometry point') end end
      checked = checked + 1
      if checked % 128 == 0 then coroutine.yield() end
    end
  end
  for _, field in ipairs({'roads', 'junctions', 'buildings', 'areas', 'lines', 'points'}) do
    for _, feature in ipairs(cell[field] or {}) do
      if type(feature) ~= 'table' or (feature.tags ~= nil and type(feature.tags) ~= 'table') then error('invalid source feature') end
      for _, n in ipairs({'width', 'height', 'minHeight'}) do
        if feature[n] ~= nil and not finite(feature[n]) then error('non-finite source feature ' .. n) end
      end
      if field == 'roads' or field == 'lines' then
        coordinates(feature.points, 2)
      elseif field == 'points' then
        coordinates({feature.pos}, 1)
      else
        if not array(feature.rings) or #feature.rings == 0 then error('invalid source polygon rings') end
        for _, ring in ipairs(feature.rings) do coordinates(ring, 3) end
      end
    end
  end
  active.cells[key] = {data = cell, used = touch()}
  evict(active.cells, CELL_LIMIT)
  return cell
end

local function tileCovered(r, i, j)
  local t = r.data.tileSize
  return covered(r, i * t, j * t, (i + 1) * t, (j + 1) * t)
end

local function needSquare(r, x, y)
  local out, t = {}, r.data.tileSize
  for i = math.floor((x - LAND_RADIUS) / t), math.ceil((x + LAND_RADIUS) / t) - 1 do
    for j = math.floor((y - LAND_RADIUS) / t), math.ceil((y + LAND_RADIUS) / t) - 1 do
      if not tileCovered(r, i, j) then return nil end
      out[keyOf(i, j)] = {i, j}
    end
  end
  return out
end

local function fail(message)
  if state.job then state.job.state, state.job.stage, state.job.message = 'error', 'source', tostring(message) end
end

local function spawnValid(manifest)
  local sp = manifest and manifest.spawn
  return manifest and manifest.drivable == true and type(sp) == 'table' and vector(sp.pos, 3) and finite(sp.yaw)
end

local function completeLanding()
  local land = active and active.landing
  if not land or state.job.state ~= 'working' then return end
  local done, total = 0, 0
  for key in pairs(land.required) do total = total + 1; if active.tiles[key] then done = done + 1 end end
  state.job.progress = total > 0 and done / total or 0
  if done < total then return end
  if not land.selected then
    local best, distance
    for key in pairs(land.required) do
      local entry = active.tiles[key]
      if entry and spawnValid(entry.road) then
        local p = entry.road.spawn.pos
        local sq = needSquare(active.region, p[1], p[2])
        if sq then
          local d = (p[1] - land.x) ^ 2 + (p[2] - land.y) ^ 2
          if not distance or d < distance then best, distance = {key = key, square = sq}, d end
        end
      end
    end
    if not best then fail('no_drivable_spawn: no road with a complete source neighbourhood'); return end
    land.selected = best.key
    for key, ij in pairs(best.square) do land.required[key] = ij end
    active.prioritiesDirty = true
    completeLanding()
    return
  end
  local entry = active.tiles[land.selected]
  local job, p = state.job, entry.road.spawn.pos
  job.state, job.stage, job.progress, job.manifest = 'ready', 'ready', 1, entry.road
  job.tile = entry.road.tile
  job.horizonZ = finite(active.region.data.horizonZ) and active.region.data.horizonZ or p[3] - 40
  job.timings = {native = true}
  active.pose = {x = p[1], y = p[2], vx = 0, vy = 0}
  active.prioritiesDirty = true
end

local function land(req)
  ensure()
  req = type(req) == 'table' and req or {}
  state.job = {seq = req.seq, lat = req.lat, lon = req.lon, state = 'working', stage = 'source', progress = 0}
  local country, descriptor, x, y, r = at(req.lat, req.lon)
  if not r then fail('no_data: this point has no complete mounted source neighbourhood'); return state.job end
  active = {region = r, cells = {}, tiles = {}, failed = {}, clock = 0, desired = {}, evicted = {}, epoch = 1,
    epochPoint = {x, y}, prioritiesDirty = true, startupPinned = true,
    manifests = setmetatable({}, {__mode = 'v'}),
    landing = {x = x, y = y, required = needSquare(r, x, y)}, pose = {x = x, y = y, vx = 0, vy = 0}}
  state.stream = {region = descriptor.id, country = country.id, tile = r.data.tileSize, generator = 'earthng-lua-v1',
    geo = affineAt(r, x, y), bounds = r.data.bounds, ready = {}, scenes = {}, far = {}, wanted = {}, readyCount = 0,
    building = 0, queued = 0, failed = active.failed, built = 0, meanBuildSeconds = 0, loadRadius = 1000, farRadius = 3000, farFast = 25}
  state.job.region, state.job.country, state.job.tileSize = descriptor.id, country.id, r.data.tileSize
  state.job.generator = state.stream.generator
  return state.job
end

local function priorities()
  local a, out = active, {}
  if not a then return out end
  local p, t, st = a.pose, a.region.data.tileSize, state.stream
  local speed = math.sqrt((p.vx or 0) ^ 2 + (p.vy or 0) ^ 2)
  local ahead = math.min(speed * 3, 2000)
  local ax, ay = p.x + (speed > 0 and (p.vx or 0) / speed * ahead or 0),
    p.y + (speed > 0 and (p.vy or 0) / speed * ahead or 0)
  local radius, farRadius = 1000, 3000
  st.loadRadius = radius
  for i = math.floor(math.min(p.x - farRadius, ax - radius) / t), math.floor(math.max(p.x + farRadius, ax + radius) / t) do
    for j = math.floor(math.min(p.y - farRadius, ay - radius) / t), math.floor(math.max(p.y + farRadius, ay + radius) / t) do
      local cx, cy = (i + 0.5) * t, (j + 0.5) * t
      local d = math.min(math.sqrt((cx - p.x) ^ 2 + (cy - p.y) ^ 2), math.sqrt((cx - ax) ^ 2 + (cy - ay) ^ 2))
      local farD = math.sqrt((cx - p.x) ^ 2 + (cy - p.y) ^ 2)
      if tileCovered(a.region, i, j) then
        if d <= radius then
          out[keyOf(i, j)] = {i = i, j = j, priority = d / math.max(speed, 5)}
        elseif farD <= farRadius and speed <= st.farFast then
          out[keyOf(i, j)] = {i = i, j = j, far = true, priority = 1e8 + farD}
        end
      end
    end
  end
  if a.landing and state.job.state == 'working' then
    for key, ij in pairs(a.landing.required) do
      local dx, dy = (ij[1] + 0.5) * t - a.landing.x, (ij[2] + 0.5) * t - a.landing.y
      out[key] = {i = ij[1], j = ij[2], priority = -1e12 + dx * dx + dy * dy}
    end
  end
  st.wanted = {}
  for key, wanted in pairs(out) do st.wanted[key] = wanted.priority end
  a.desired = out
  a.prioritiesDirty = false
  return out
end

local function geometrySize(manifest)
  local triangles, vertices = 0, 0
  if not manifest then return triangles, vertices end
  for _, item in ipairs(manifest.items or {}) do
    if type(item.meshes) == 'table' then
      triangles = triangles + (finite(item.triangles) and item.triangles or 0)
      for _, mesh in ipairs(item.meshes) do vertices = vertices + #(mesh.verts or {}) end
    end
  end
  return triangles, vertices
end

local function trimTiles()
  local a, st = active, state.stream
  local count, triangles, vertices, cellCount = 0, 0, 0, 0
  for _ in pairs(a.cells) do cellCount = cellCount + 1 end
  for _, entry in pairs(a.tiles) do
    count, triangles, vertices = count + 1, triangles + entry.triangles, vertices + entry.vertices
  end
  while count > TILE_LIMIT or triangles > TRIANGLE_LIMIT or vertices > VERTEX_LIMIT do
    local worst, worstPriority, oldest
    for key, entry in pairs(a.tiles) do
      local required = a.landing and a.startupPinned and a.landing.required[key]
      local selected = a.startupPinned and a.landing and a.landing.selected == key
      local priority = a.desired[key] and a.desired[key].priority or 1e20
      if not required and not selected and (not worst or priority > worstPriority or (priority == worstPriority and entry.used < oldest)) then
        worst, worstPriority, oldest = key, priority, entry.used
      end
    end
    if not worst then break end -- The startup neighbourhood is the minimum working set.
    local removed = a.tiles[worst]
    count, triangles, vertices = count - 1, triangles - removed.triangles, vertices - removed.vertices
    a.tiles[worst] = nil
    if removed.road then a.evicted[worst .. ':road'] = a.epoch end
    if removed.far then a.evicted[worst .. ':far'] = a.epoch end
    st.ready[worst], st.scenes[worst], st.far[worst] = nil, nil, nil
  end
  local readyCount = 0
  for _, entry in pairs(a.tiles) do if entry.road then readyCount = readyCount + 1 end end
  st.readyCount, st.cachedTiles, st.cachedCells = readyCount, count, cellCount
  st.cachedTriangles, st.cachedVertices = triangles, vertices
  st.cacheLimits = {tiles = TILE_LIMIT, cells = CELL_LIMIT, triangles = TRIANGLE_LIMIT, vertices = VERTEX_LIMIT}
  st.cacheOverBudget = count > TILE_LIMIT or triangles > TRIANGLE_LIMIT or vertices > VERTEX_LIMIT
end

local function buildTile(key, wanted)
  local r, t, cells = active.region, active.region.data.tileSize, {}
  local x0, y0 = wanted.i * t, wanted.j * t
  local s = r.data.cellSize
  for i = math.floor(x0 / s), math.ceil((x0 + t) / s) - 1 do
    for j = math.floor(y0 / s), math.ceil((y0 + t) / s) - 1 do
      local cellKey = keyOf(i, j)
      cells[#cells + 1] = loadCell(cellKey, r.data.cells[cellKey])
      coroutine.yield()
    end
  end
  local center = active.landing and {active.landing.x, active.landing.y} or nil
  local road, scene, far, nav = generator.build(cells, wanted.i, wanted.j,
    {tileSize = t, regionId = r.descriptor.id, center = center, pause = function() coroutine.yield() end,
      materials = '/earthng/planet/materials.json', packs = '/e2r_stream/fast-art/v9/packs.json', far = wanted.far})
  if type(road) ~= 'table' or type(road.items) ~= 'table' or type(road.mats) ~= 'table'
    or type(scene) ~= 'table' or type(scene.items) ~= 'table' then error('generator returned an invalid tile manifest') end
  if wanted.far and (type(far) ~= 'table' or type(far.items) ~= 'table') then error('generator returned an invalid far manifest') end
  if road.spawn and not finite(road.spawn.yaw) and vector(road.spawn.dir, 3) then
    road.spawn.yaw = math.atan2(-road.spawn.dir[1], road.spawn.dir[2])
  end
  road.tile, road.source, scene.source = {wanted.i, wanted.j}, r.descriptor.id, r.descriptor.id
  road.materials, scene.materials = '/earthng/planet/materials.json', '/earthng/planet/materials.json'
  road.packs = '/e2r_stream/fast-art/v9/packs.json'
  road.navSegments = type(nav) == 'table' and nav.segments or {}
  local entry = active.tiles[key] or {}
  if wanted.far then
    far.source, far.materials = r.descriptor.id, '/earthng/planet/materials.json'
    entry.far = far
    active.manifests[key .. ':far'] = far
  else
    entry.road, entry.scene, entry.nav = road, scene, nav
    active.manifests[key .. ':road'], active.manifests[key .. ':scene'] = road, scene
  end
  local rt, rv = geometrySize(entry.road)
  local stt, sv = geometrySize(entry.scene)
  local ft, fv = geometrySize(entry.far)
  entry.used, entry.triangles, entry.vertices = touch(), rt + stt + ft, rv + sv + fv
  active.tiles[key] = entry
  local st = state.stream
  st.ready[key], st.scenes[key], st.far[key] = entry.road and true or nil, entry.scene and true or nil, entry.far and true or nil
  st.built = st.built + 1
  trimTiles()
end

local function update(dt, budgetMs)
  ensure()
  if not active then return end
  local wanted, best, bestKey = active.prioritiesDirty and priorities() or active.desired, nil, nil
  local timer = hptimer()
  budgetMs = finite(budgetMs) and math.max(0.1, math.min(8, budgetMs)) or 2
  local steps = 0
  repeat
    if not active.work then
      best, bestKey = nil, nil
      local queued = 0
      for key, entry in pairs(wanted) do
        local cached = active.tiles[key]
        local ready = cached and (entry.far and cached.far or not entry.far and cached.road)
        local layerKey = key .. (entry.far and ':far' or ':road')
        if not ready and not active.failed[key] and active.evicted[layerKey] ~= active.epoch then
          queued = queued + 1
          if not best or entry.priority < best.priority or (entry.priority == best.priority and key < bestKey) then best, bestKey = entry, key end
        end
      end
      state.stream.queued = queued
      if not best then break end
      local key, entry = bestKey, best
      active.workKey = key
      active.workFar = entry.far
      active.work = coroutine.create(function() buildTile(key, entry) end)
      state.stream.building = 1
      if state.job and state.job.state == 'working' then state.job.stage = 'Generating roads and scenery in Lua' end
    end
    local ok, message = coroutine.resume(active.work)
    steps = steps + 1
    if not ok then
      active.failed[active.workKey] = tostring(message)
      if active.landing and active.landing.required[active.workKey] and state.job.state == 'working' then fail(message) end
      active.work = nil
    elseif coroutine.status(active.work) == 'dead' then
      active.work = nil
      completeLanding()
      trimTiles()
    end
    state.stream.building = active.work and 1 or 0
  until steps >= 256 or timer:stop() >= budgetMs
end

local function pose(p)
  if not active or type(p) ~= 'table' or not finite(p.x) or not finite(p.y) then return end
  if math.abs(p.x) > 1e12 or math.abs(p.y) > 1e12 then return end
  if p.region and p.region ~= active.region.descriptor.id then return end
  if p.country and p.country ~= active.region.country.id then return end
  local old = active.pose
  active.pose = {x = p.x, y = p.y,
    vx = finite(p.vx) and math.max(-10000, math.min(10000, p.vx)) or 0,
    vy = finite(p.vy) and math.max(-10000, math.min(10000, p.vy)) or 0}
  if p.x ~= old.x or p.y ~= old.y or active.pose.vx ~= old.vx or active.pose.vy ~= old.vy then active.prioritiesDirty = true end
  local ep = active.epochPoint
  if (p.x - ep[1]) ^ 2 + (p.y - ep[2]) ^ 2 >= 250 * 250 then
    active.epoch, active.epochPoint = active.epoch + 1, {p.x, p.y}
  end
  -- A ready job starts the renderer's warm-up and descent. Keep every startup
  -- road/scene until the renderer confirms that the car has reached the ground.
  if p.state == 'driving' and active.startupPinned then
    active.startupPinned, active.prioritiesDirty = false, true
    active.epoch = active.epoch + 1
    trimTiles()
  end
  local lon, lat = geographic(active.region, p.x, p.y)
  state.stream.car = {lon, lat}
  state.stream.geo = affineAt(active.region, p.x, p.y)
  local b = active.region.data.bounds
  state.stream.edgeKm = math.max(0, math.min((lon - b[1]) * 111.2 * math.cos(math.rad(lat)),
    (b[3] - lon) * 111.2 * math.cos(math.rad(lat)), (lat - b[2]) * 111.2, (b[4] - lat) * 111.2))
end

local function manifest(key, layer)
  local entry = active and active.tiles[key]
  if entry then entry.used = touch(); return entry[layer or 'road'] end
end

local function recount(entry)
  local rt, rv = geometrySize(entry.road)
  local stt, sv = geometrySize(entry.scene)
  local ft, fv = geometrySize(entry.far)
  entry.triangles, entry.vertices = rt + stt + ft, rv + sv + fv
end

-- createMesh has copied these arrays to the engine. The renderer calls this only
-- after the entire layer has spawned, so its queue can no longer need the tables.
local function release(key, layer)
  layer = layer or 'road'
  if layer ~= 'road' and layer ~= 'scene' and layer ~= 'far' then return false end
  local entry = active and active.tiles[key]
  local data = entry and entry[layer] or active and active.manifests[key .. ':' .. layer]
  if not data then return false end
  for _, item in ipairs(data.items or {}) do item.meshes = nil end
  if entry then
    entry[layer .. 'Delivered'] = true
    entry.used = touch()
    recount(entry)
  end
  trimTiles()
  return true
end

-- Resident GPU meshes have gone. Removing the corresponding ready bit makes the
-- next visit rebuild from the compact source cells instead of reusing empty data.
local function unload(key, layer)
  if not active or (layer ~= nil and layer ~= 'road' and layer ~= 'scene' and layer ~= 'far') then return false end
  local entry, st = active.tiles[key], state.stream
  local farOnly = layer == 'far'
  if active.workKey == key and (farOnly == (active.workFar == true)) then
    active.work, active.workKey, st.building = nil, nil, 0
  end
  if farOnly then
    if entry then entry.far, entry.farDelivered = nil, nil end
    st.far[key], active.evicted[key .. ':far'] = nil, nil
    active.manifests[key .. ':far'] = nil
  else
    if entry then
      entry.road, entry.scene, entry.nav = nil, nil, nil
      entry.roadDelivered, entry.sceneDelivered = nil, nil
    end
    st.ready[key], st.scenes[key], active.evicted[key .. ':road'] = nil, nil, nil
    active.manifests[key .. ':road'], active.manifests[key .. ':scene'] = nil, nil
  end
  active.failed[key] = nil
  if entry then
    if not entry.road and not entry.scene and not entry.far then active.tiles[key] = nil else recount(entry) end
  end
  active.prioritiesDirty = true
  trimTiles()
  return entry ~= nil
end

M.refresh = refresh
M.at = at
M.land = land
M.update = update
M.pose = pose
M.manifest = manifest
M.release = release
M.unload = unload
M.nav = function(key) local entry = active and active.tiles[key]; return entry and entry.nav end
M.status = function() ensure(); state.heartbeat = os.time(); return state end
return M
