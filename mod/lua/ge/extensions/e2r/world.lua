-- EarthNG planet level; active only in e2r_world. Native mode discovers mounted
-- country source packs, generates 500m tiles in Lua and streams them over several
-- frames. See docs/NATIVE_MODS.md. The explicit --legacy build retains the Python
-- companion protocol (request/status/pose files) for developer comparisons.
local M = {}
local loadNewMaterials
local AP = nil   -- autopilot state (route test); read by writePose for the route ahead
local im = ui_imgui
local imguiUtils = require('ui/imguiUtils')
local ffi = require('ffi')
local logTag = 'e2r_world'
local LANG = 'uk'   -- build_mod.py --lang en rewrites this line (the English packages: UK, California, New York)
local NATIVE = true   -- build_mod.py --legacy keeps the companion development workflow
local native = NATIVE and require('ge/extensions/e2r/native') or nil
local planetMap = NATIVE and require('ge/extensions/e2r/planetMap') or nil
local function L(uk, en) return LANG == 'en' and en or uk end

local W, H = 720, 440          -- map picture size, px
local BUDGET_MS = 4            -- spawn work per frame
local CAM_HIGH, CAM_LOW = 350, 28
local LOAD_R, UNLOAD_R = 800, 1250   -- tile centre distance from the car, m
local SCENE_LAG = 3            -- s: a scene item queues as the road of a tile reached this much later
local COLLISION_EVERY = 2.0          -- s between batched reloadCollision calls
local LAMP_SHAPE = '/art/shapes/objects/lamp1.dae'   -- fast_style.LAMP
local PRESETS = {
  {'Оболонь', 50.5070, 30.4990, 'ukraine'}, {'Поділ', 50.4655, 30.5170, 'ukraine'},
  {'Хрещатик', 50.4475, 30.5225, 'ukraine'}, {'Печерськ', 50.4280, 30.5410, 'ukraine'},
  {'Голосіїв', 50.3920, 30.5080, 'ukraine'}, {'Біличі', 50.4775, 30.3600, 'ukraine'},
  -- Kyiv oblast towns (data/osm_tiles/ukraine-260918)
  {'Ірпінь', 50.5210, 30.2500, 'ukraine'}, {'Бровари', 50.5110, 30.7900, 'ukraine'},
  {'Бориспіль', 50.3520, 30.9550, 'ukraine'}, {'Фастів', 50.0780, 29.9180, 'ukraine'},
  {'Біла Церква', 49.7960, 30.1150, 'ukraine'},
  -- oblast centres (data/osm_tiles/ukraine-260918)
  {'Львів', 49.8400, 24.0280, 'ukraine'}, {'Одеса', 46.4800, 30.7300, 'ukraine'},
  {'Харків', 49.9900, 36.2300, 'ukraine'}, {'Дніпро', 48.4650, 35.0460, 'ukraine'},
  {'Запоріжжя', 47.8390, 35.1400, 'ukraine'}, {'Вінниця', 49.2330, 28.4680, 'ukraine'},
  {'Житомир', 50.2550, 28.6590, 'ukraine'}, {'Чернігів', 51.4980, 31.2890, 'ukraine'},
  {'Полтава', 49.5880, 34.5510, 'ukraine'}, {'Черкаси', 49.4440, 32.0600, 'ukraine'},
  {'Ужгород', 48.6210, 22.2880, 'ukraine'}, {'Чернівці', 48.2920, 25.9400, 'ukraine'},
  {'Івано-Франківськ', 48.9230, 24.7110, 'ukraine'}, {'Луцьк', 50.7470, 25.3250, 'ukraine'},
  -- California (data/osm_tiles/california-261013; an English package)
  {'Los Angeles', 34.0505, -118.2475, 'california'}, {'Hollywood', 34.1015, -118.3265, 'california'},
  {'Santa Monica', 34.0160, -118.4925, 'california'}, {'San Francisco', 37.7880, -122.4075, 'california'},
  {'San Diego', 32.7150, -117.1610, 'california'}, {'Sacramento', 38.5780, -121.4920, 'california'},
  {'San Jose', 37.3360, -121.8900, 'california'}, {'Fresno', 36.7390, -119.7860, 'california'},
  {'Palm Springs', 33.8250, -116.5420, 'california'}, {'Big Sur', 36.2700, -121.8070, 'california'},
  {'Lake Tahoe', 38.9350, -119.9800, 'california'}, {'Death Valley', 36.4600, -116.8700, 'california'},
  -- United Kingdom: the whole country (united-kingdom-*) or a regional package (uk-<region>-*, its
  -- status.area); the 4th field is the region, one row each
  {'London', 51.5080, -0.1281, 'uk-london-south-east'}, {'Brighton', 50.8225, -0.1372, 'uk-london-south-east'},
  {'Oxford', 51.7520, -1.2577, 'uk-london-south-east'}, {'Cambridge', 52.2053, 0.1218, 'uk-london-south-east'},
  {'Canterbury', 51.2802, 1.0789, 'uk-london-south-east'}, {'Dover', 51.1279, 1.3134, 'uk-london-south-east'},
  {'Windsor', 51.4839, -0.6044, 'uk-london-south-east'}, {'Portsmouth', 50.7989, -1.0912, 'uk-london-south-east'},
  {'Bristol', 51.4545, -2.5879, 'uk-south-west'}, {'Bath', 51.3811, -2.3590, 'uk-south-west'},
  {'Exeter', 50.7184, -3.5339, 'uk-south-west'}, {'Plymouth', 50.3714, -4.1422, 'uk-south-west'},
  {'St Ives', 50.2110, -5.4800, 'uk-south-west'}, {'Cheddar Gorge', 51.2830, -2.7600, 'uk-south-west'},
  {'Stonehenge', 51.1789, -1.8262, 'uk-south-west'}, {'Birmingham', 52.4796, -1.9026, 'uk-central-england'},
  {'Manchester', 53.4794, -2.2453, 'uk-central-england'}, {'Liverpool', 53.4058, -2.9800, 'uk-central-england'},
  {'Nottingham', 52.9536, -1.1505, 'uk-central-england'}, {'Sheffield', 53.3811, -1.4701, 'uk-central-england'},
  {'Peak District', 53.3440, -1.7770, 'uk-central-england'}, {'Chester', 53.1905, -2.8900, 'uk-central-england'},
  {'Leicester', 52.6369, -1.1398, 'uk-central-england'}, {'Leeds', 53.7965, -1.5478, 'uk-north-england'},
  {'Newcastle', 54.9738, -1.6132, 'uk-north-england'}, {'York', 53.9590, -1.0815, 'uk-north-england'},
  {'Durham', 54.7761, -1.5733, 'uk-north-england'}, {'Lake District', 54.6013, -3.1347, 'uk-north-england'},
  {'Whitby', 54.4858, -0.6206, 'uk-north-england'}, {'Cardiff', 51.4816, -3.1791, 'uk-wales'},
  {'Swansea', 51.6214, -3.9436, 'uk-wales'}, {'Snowdonia', 53.1190, -4.1290, 'uk-wales'},
  {'Aberystwyth', 52.4153, -4.0829, 'uk-wales'}, {'Conwy', 53.2800, -3.8300, 'uk-wales'},
  {'Brecon Beacons', 51.9460, -3.3920, 'uk-wales'}, {'Belfast', 54.5973, -5.9301, 'uk-northern-ireland'},
  {'Derry', 54.9966, -7.3086, 'uk-northern-ireland'}, {"Giant's Causeway", 55.2408, -6.5116, 'uk-northern-ireland'},
  {'Enniskillen', 54.3438, -7.6315, 'uk-northern-ireland'},
  {'Newcastle (NI)', 54.2180, -5.8880, 'uk-northern-ireland'}, {'Glasgow', 55.8609, -4.2514, 'uk-south-scotland'},
  {'Edinburgh', 55.9520, -3.1960, 'uk-south-scotland'}, {'Stirling', 56.1165, -3.9369, 'uk-south-scotland'},
  {'Ayr', 55.4586, -4.6292, 'uk-south-scotland'}, {'Dumfries', 55.0700, -3.6050, 'uk-south-scotland'},
  {'Loch Lomond', 56.1010, -4.6380, 'uk-south-scotland'}, {'Aberdeen', 57.1497, -2.0943, 'uk-scotland-east'},
  {'Dundee', 56.4620, -2.9707, 'uk-scotland-east'}, {'Inverness', 57.4778, -4.2247, 'uk-scotland-east'},
  {'Perth', 56.3950, -3.4310, 'uk-scotland-east'}, {'Cairngorms', 57.1950, -3.8250, 'uk-scotland-east'},
  {"John o' Groats", 58.6373, -3.0689, 'uk-scotland-east'}, {'Glencoe', 56.6826, -5.1023, 'uk-scotland-west'},
  {'Fort William', 56.8198, -5.1052, 'uk-scotland-west'}, {'Oban', 56.4150, -5.4720, 'uk-scotland-west'},
  {'Isle of Skye', 57.4125, -6.1946, 'uk-scotland-west'}, {'Ullapool', 57.8960, -5.1590, 'uk-scotland-west'},
  {'Stornoway', 58.2090, -6.3880, 'uk-scotland-west'},
  -- New York State (data/osm_tiles/new-york-*; an English package): the city, then the state
  {'Times Square', 40.7580, -73.9855, 'new-york'}, {'Wall Street', 40.7069, -74.0113, 'new-york'},
  {'Central Park', 40.7680, -73.9819, 'new-york'}, {'Brooklyn', 40.6928, -73.9903, 'new-york'},
  {'Queens', 40.7447, -73.9485, 'new-york'}, {'The Bronx', 40.8270, -73.9260, 'new-york'},
  {'Staten Island', 40.6437, -74.0736, 'new-york'}, {'Montauk', 41.0359, -71.9545, 'new-york'},
  {'Albany', 42.6526, -73.7562, 'new-york'}, {'Buffalo', 42.8864, -78.8784, 'new-york'},
  {'Niagara Falls', 43.0896, -79.0636, 'new-york'}, {'Rochester', 43.1566, -77.6088, 'new-york'},
  {'Syracuse', 43.0481, -76.1474, 'new-york'}, {'Lake Placid', 44.2795, -73.9799, 'new-york'},
  -- Zanzibar (data/osm_tiles/zanzibar-*; an English package): Unguja, then Pemba
  {'Stone Town', -6.1630, 39.1910, 'zanzibar'}, {'Nungwi', -5.7266, 39.2970, 'zanzibar'},
  {'Kendwa', -5.7530, 39.2930, 'zanzibar'}, {'Paje', -6.2650, 39.5350, 'zanzibar'},
  {'Jambiani', -6.3150, 39.5450, 'zanzibar'}, {'Jozani Forest', -6.2610, 39.4130, 'zanzibar'},
  {'Chake Chake', -5.2460, 39.7690, 'zanzibar'}, {'Wete', -5.0570, 39.7280, 'zanzibar'},
  {'Mkoani', -5.3700, 39.6440, 'zanzibar'},
  -- Romania (data/osm_tiles/romania-*; an English package)
  {'Bucharest', 44.4355, 26.1025, 'romania'}, {'Cluj-Napoca', 46.7695, 23.5899, 'romania'},
  {'Timisoara', 45.7537, 21.2257, 'romania'}, {'Iasi', 47.1620, 27.5890, 'romania'},
  {'Brasov', 45.6427, 25.5887, 'romania'}, {'Constanta', 44.1733, 28.6383, 'romania'},
  {'Sibiu', 45.7975, 24.1522, 'romania'}, {'Sighisoara', 46.2190, 24.7920, 'romania'},
  {'Oradea', 47.0570, 21.9290, 'romania'}, {'Craiova', 44.3190, 23.7967, 'romania'},
  {'Transfagarasan', 45.6030, 24.6170, 'romania'}, {'Bran Castle', 45.5150, 25.3672, 'romania'},
  {'Tulcea', 45.1790, 28.8050, 'romania'},
}
-- Kyiv, its oblast, the country (two rows), California (two rows), the UK (a row per region), New York
-- (two rows), Zanzibar (Unguja, Pemba), Romania (two rows)
local PRESET_ROWS = {[7] = true, [12] = true, [19] = true, [26] = true, [32] = true, [38] = true, [46] = true,
  [53] = true, [61] = true, [67] = true, [73] = true, [78] = true, [84] = true, [90] = true,
  [96] = true, [103] = true, [110] = true, [116] = true, [119] = true, [126] = true}

local S = {
  widgetHidden = false, pickerOpen = false,
  state = 'off', center = {lat = 50.4501, lon = 30.5234}, zoom = 11, marker = nil,
  seq = 0, viewDirty = true, lastViewAt = -1, tex = nil, texView = nil, status = nil,
  statusReadAt = -1, landSeq = nil, landAt = nil, now = 0, frames = {},
  tiles = {}, far = {}, queue = {}, nav = {}, navPending = {}, poseAt = -1, collisionDirty = false, collisionAt = -1, knownMats = {},
}
local coordBuf = im.ArrayChar(64)
local test, result = nil, {status = 'waiting', shots = {}}
local frameTimer = hptimer()

-- the 4th field of a preset: the tile package area it belongs to (status.area, the tile folder's
-- name without the date); a UK region also belongs to the whole united-kingdom package
local function presetArea(tag)
  return tag:match('^uk%-') and 'united-kingdom' or tag
end
local KNOWN_AREAS = {}
for _, p in ipairs(PRESETS) do KNOWN_AREAS[p[4]] = true; KNOWN_AREAS[presetArea(p[4])] = true end

-- only the presets of the companion's OSM package: by its area when the buttons know it (the
-- Ukraine box holds Bucharest, the Wales box Bristol), else inside status.packageBox, so a
-- California package does not offer Kyiv
local function presetInPackage(p)
  if NATIVE then return native.at(p[2], p[3]) ~= nil end
  local area = S.status and S.status.area
  if p[4] and area and KNOWN_AREAS[area] then return p[4] == area or presetArea(p[4]) == area end
  local box = S.status and S.status.packageBox
  if NATIVE and not box and not S.centredOnPackage then
    S.center, S.zoom, S.centredOnPackage = {lat = 20, lon = 0}, 2, true
  end
  return not box or (p[3] >= box[1] and p[2] >= box[2] and p[3] <= box[3] and p[2] <= box[4])
end

-- --- helpers -------------------------------------------------------------------------------------
local function merc(lat, lon, z)
  local n = 256 * 2 ^ z
  local s = math.sin(math.rad(math.max(-85.05, math.min(85.05, lat))))
  return (lon + 180) / 360 * n, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
end

local function unmerc(x, y, z)
  local n = 256 * 2 ^ z
  local m = math.pi * (1 - 2 * y / n)
  return math.deg(math.atan(0.5 * (math.exp(m) - math.exp(-m)))), x / n * 360 - 180
end

local function saveResult()
  if test then jsonWriteFile('e2r-world-result.json', result, true) end
end

local function shot(name)
  if not test then return end
  createScreenshot2({filename = 'screenshots/e2r-' .. name, writeJPG = false, superSampling = 1})
  table.insert(result.shots, name)
  -- what the picture was taken under (pack looks vary run to run: exposure, night sky)
  pcall(function()
    local lx, sky = scenetree.findObject('PostEffectLocalExposureObject'), scenetree.findObject('sunsky')
    result.shotState = result.shotState or {}
    result.shotState[name] = {lx and tostring(lx.autoExposure), lx and lx.manualEV,
                              sky and sky:getField('nightCubemap', 0), sky and sky:getField('useNightCubemap', 0),
                              sky and sky:getField('elevation', 0)}
  end)
end

local function request(tbl)
  S.seq = S.seq + 1
  tbl.seq = S.seq
  if NATIVE then
    if tbl.type == 'land' then native.land(tbl) end
    return S.seq
  end
  jsonWriteFile(string.format('e2r_world/request_%d.json', S.seq), tbl, false)
  return S.seq
end

local function readStatus()
  if S.now - S.statusReadAt < 0.25 then return end
  S.statusReadAt = S.now
  if NATIVE then
    S.status = native.status()
    S.coverage = S.status.coverage
  else
    if not FS:fileExists('/e2r_world/status.json') then return end
    local ok, st = pcall(jsonReadFile, '/e2r_world/status.json')
    if ok and type(st) == 'table' then S.status = st end
  end
  -- the map opens over the package's own land (the default centre is Kyiv)
  local box = S.status and S.status.packageBox
  if box and not S.centredOnPackage then
    S.centredOnPackage = true
    if not presetInPackage({'', S.center.lat, S.center.lon}) then
      S.center, S.viewDirty = {lat = (box[2] + box[4]) / 2, lon = (box[1] + box[3]) / 2}, true
      for _, p in ipairs(PRESETS) do
        if presetInPackage(p) then S.center = {lat = p[2], lon = p[3]}; break end
      end
    end
  end
end

local function serverUp()
  if NATIVE then return true end
  return S.status and S.status.heartbeat and (os.time() - S.status.heartbeat) < 6
end

local function setMarker(lat, lon)
  S.marker = {lat = lat, lon = lon}
  S.viewDirty = true
end

local function goTo(lat, lon, zoom)
  S.center = {lat = lat, lon = lon}
  S.zoom = zoom or S.zoom
  setMarker(lat, lon)
end

local function land()
  if not S.marker then return end
  if NATIVE and not native.at(S.marker.lat, S.marker.lon) then return end
  S.landSeq = request({type = 'land', lat = S.marker.lat, lon = S.marker.lon})
  S.landAt = S.now
  S.landTimer = hptimer()
  S.pickerOpen = false
  S.state = 'generating'
  result.landRequestedAt = S.now
  saveResult()
end

-- --- pieces (as measured in EarthNG earthng/e2rstream.lua, S2) ---------------------------------
-- Pieces: u32 subCount; per submesh u32 nameLen (padded to 4), name, u32 verts, u32 indices,
-- f32 pos[3v], f32 normal[3v], f32 uv[2v], u32 index[i]. ffi.cast is a no-op in game Lua, so the
-- bytes are copied into a uint32 and a float buffer and indexed by 4-byte word.
local function binMeshes(path, pause)
  local t = hptimer()
  local f = io.open(path, 'rb')
  if not f then return nil end
  local data = f:read('*all')
  f:close()
  local ms = t:stop()
  if ms > (result.readMs or 0) then result.readMs, result.readItem = ms, path end
  local words = #data / 4
  local u = ffi.new('uint32_t[?]', words)
  ffi.copy(u, data, #data)
  local fl = ffi.new('float[?]', words)
  ffi.copy(fl, data, #data)
  pause()
  local meshes, w = {}, 1
  for s = 1, u[0] do
    local len = u[w]
    local mat = data:sub(4 * w + 5, 4 * w + 4 + len):gsub('%z+$', '')
    w = w + 1 + len / 4
    local nv, ni = u[w], u[w + 1]
    w = w + 2
    local pw, nw, tw, iw = w, w + 3 * nv, w + 6 * nv, w + 8 * nv
    local verts, normals, uvs, faces = {}, {}, {}, {}
    for k = 0, nv - 1 do
      verts[k + 1] = {x = fl[pw + 3 * k], y = fl[pw + 3 * k + 1], z = fl[pw + 3 * k + 2]}
      normals[k + 1] = {x = fl[nw + 3 * k], y = fl[nw + 3 * k + 1], z = fl[nw + 3 * k + 2]}
      uvs[k + 1] = {u = fl[tw + 2 * k], v = fl[tw + 2 * k + 1]}
      if k % 256 == 255 then pause() end
    end
    for k = 0, ni - 1 do
      local v = u[iw + k]
      faces[k + 1] = {v = v, n = v, u = v}
      if k % 768 == 767 then pause() end
    end
    w = iw + ni
    meshes[s] = {verts = verts, normals = normals, uvs = uvs, faces = faces, material = mat}
  end
  return meshes
end

local function spawnItem(it, group, pause)
  if it.kind == 'proc' then
    local meshes = it.meshes or binMeshes(it.shape, pause)
    if not meshes then error('missing mesh: ' .. tostring(it.shape)) end
    pause()
    -- the one call that cannot yield: its worst case is the floor of a frame's streaming cost
    local t = hptimer()
    local obj = createObject('ProceduralMesh')
    obj:registerObject(it.name)
    obj.canSave = false
    group:addObject(obj.obj)
    obj:createMesh({meshes})
    obj:setPosition(vec3(it.pos[1], it.pos[2], it.pos[3]))
    local ms = t:stop()
    if ms > (result.createMeshMs or 0) then result.createMeshMs, result.createMeshItem = ms, it.name end
  else
    local obj = createObject('TSStatic')
    obj:setField('shapeName', 0, it.shape)
    obj:setField('collisionType', 0, it.collision or 'Collision Mesh')
    obj:setField('decalType', 0, it.decal or 'None')
    obj.useInstanceRenderData = true
    obj:setPosRot(it.pos[1], it.pos[2], it.pos[3], it.rot[1], it.rot[2], it.rot[3], it.rot[4])
    if it.scale then obj:setScale(vec3(it.scale, it.scale, it.scale)) end
    obj:registerObject(it.name)
    group:addObject(obj)
    -- street lamps: where a night pack's lights go (lightsStep)
    if it.shape == LAMP_SHAPE then
      S.lamps = S.lamps or {}
      S.lamps[string.format('%d_%d', math.floor(it.pos[1]), math.floor(it.pos[2]))] = {it.pos[1], it.pos[2], it.pos[3]}
    end
  end
end

local function tileGroup(name, outside)
  local g = scenetree.findObject(name)
  if g then
    g:deleteAllObjects()
    return g
  end
  g = createObject('SimGroup')
  g:registerObject(name)
  -- outside: decoration kept out of the MissionGroup (and so out of the static collision)
  if not outside then scenetree.MissionGroup:addObject(g) end
  return g
end

local function dropGroup(name)
  local g = scenetree.findObject(name)
  if g then
    g:deleteAllObjects()
    g:delete()
  end
end

-- A decoration group (trees, lamps, lines, the far city) goes a few dozen objects a frame: a
-- scene tile holds up to ~900 trees, and deleting them in one frame stalled it.
local GRAVE_PER_FRAME = 60
local function buryGroup(name)
  local g = scenetree.findObject(name)
  if not g then return end
  -- out of the way at once (a renamed group cannot be found again by a new tile of that key)
  g:setName('')
  S.graves = S.graves or {}
  table.insert(S.graves, g)
end

local function digGraves()
  local graves = S.graves
  if not graves or #graves == 0 then return end
  local left = GRAVE_PER_FRAME
  while left > 0 and #graves > 0 do
    local g = graves[1]
    local n = g:getCount()
    if n == 0 then
      g:delete()
      table.remove(graves, 1)
    else
      for _ = 1, math.min(left, n) do
        local o = g:getObject(g:getCount() - 1)
        if o then o:delete() end
        left = left - 1
      end
    end
  end
end

local function frameStats(list)
  local worst, total = 0, 0
  for _, dt in ipairs(list) do worst, total = math.max(worst, dt), total + dt end
  local sorted = {}
  for k, dt in ipairs(list) do sorted[k] = dt end
  table.sort(sorted)
  return {frames = #list, worstFrameMs = worst, meanFrameMs = #list > 0 and total / #list or 0,
          p99FrameMs = sorted[math.max(1, math.ceil(#sorted * 0.99))] or 0}
end

-- The whole driving phase, however long: 0.5 ms bins up to 1 s (p99 without keeping every frame).
local function newAcc() return {n = 0, total = 0, worst = 0, over33 = 0, over100 = 0, bins = {}, worstAt = 0} end

local function accFrame(acc, ms, now)
  acc.n, acc.total = acc.n + 1, acc.total + ms
  if ms > acc.worst then acc.worst, acc.worstAt = ms, now end
  if ms > 33.3 then acc.over33 = acc.over33 + 1 end
  if ms > 100 then acc.over100 = acc.over100 + 1 end
  local b = math.min(2000, math.floor(ms * 2))
  acc.bins[b] = (acc.bins[b] or 0) + 1
end

local function accStats(acc)
  local need, seen, p99 = math.ceil(acc.n * 0.99), 0, 0
  for b = 0, 2000 do
    seen = seen + (acc.bins[b] or 0)
    if seen >= need then p99 = (b + 1) / 2; break end
  end
  return {frames = acc.n, meanFrameMs = acc.n > 0 and acc.total / acc.n or 0, p99FrameMs = p99,
          worstFrameMs = acc.worst, worstAt = acc.worstAt, over33 = acc.over33, over100 = acc.over100}
end

-- --- tile streaming ------------------------------------------------------------------------------
-- S.stream: the companion's stream status (region, tile size, ready tiles). S.tiles[key]:
-- {state = 'loading'|'loaded', i, j, manifest, group}. S.queue: items of loading tiles, in order.
local function focus()
  if S.replayPose then return S.replayPose.p, S.replayPose.v end
  if S.state == 'driving' then
    local veh = getPlayerVehicle(0)
    if veh then
      local p, v = veh:getPosition(), veh:getVelocity()
      return p, v
    end
  end
  local sp = S.landing and S.landing.spawn.pos
  if sp then return vec3(sp[1], sp[2], sp[3]), vec3(0, 0, 0) end
end

local function tileCentre(i, j)
  local t = S.stream.tile
  return (i + 0.5) * t, (j + 0.5) * t
end

local function tileOf(x, y)
  local t = S.stream.tile
  return math.floor(x / t), math.floor(y / t)
end

local function writePose()
  if S.now - S.poseAt < 0.5 then return end
  S.poseAt = S.now
  local p, v = focus()
  if not p then return end
  -- the planned route ahead (autopilot), so the companion builds along the road the car will take
  -- rather than straight along its heading: points every 250 m for the next 4 km
  local ahead
  if AP and AP.pts then
    ahead = {}
    local k, acc, nextAt = AP.idx, 0, 250
    while k < #AP.pts and acc < 4000 do
      acc = acc + math.sqrt((AP.pts[k + 1][1] - AP.pts[k][1]) ^ 2 + (AP.pts[k + 1][2] - AP.pts[k][2]) ^ 2)
      k = k + 1
      if acc >= nextAt then
        table.insert(ahead, {AP.pts[k][1], AP.pts[k][2], math.floor(acc)})
        nextAt = nextAt + 250
      end
    end
  end
  local active = {}
  for key, tile in pairs(S.tiles) do
    active[key] = tile.failed and 'error' or (tile.collided and 'collision_active' or
      (tile.roadComplete and 'loaded' or 'loading'))
  end
  S.poseSeq = (S.poseSeq or 0) + 1
  local pose = {x = p.x, y = p.y, z = p.z, vx = v.x, vy = v.y, t = os.time(), seq = S.poseSeq,
    region = S.stream and S.stream.region, state = S.state, ahead = ahead,
    active = active, deliveryP99 = S.deliveryP99 or 2}
  if NATIVE then native.pose(pose); return end
  -- a UDP datagram to the companion never blocks; writing pose.json once held a frame 5.2 s
  -- (fast-replay-game-05). The file stays as the fallback for an older companion.
  local port = S.status and S.status.posePort
  if port then
    if not S.udp or S.udpPort ~= port then
      local ok, sock = pcall(function() local s = (socket or require('socket')).udp(); s:settimeout(0); s:setpeername('127.0.0.1', port); return s end)
      S.udp, S.udpPort = ok and sock or nil, port
    end
    if S.udp and S.udp:send(jsonEncode(pose)) then return end
  end
  jsonWriteFile('e2r_world/pose.json', pose, false)
end

-- Every tile's materials file defines all of the region's shared names. Loading a file re-creates
-- the materials that already exist ("duplicate material. Overwriting"), ~80 per tile, and the
-- frames after it stall for 1-3 s (route-05). So only the definitions not loaded yet go through
-- a small temporary file.
loadNewMaterials = function(path)
  if not path then return end
  local ok, defs = pcall(jsonReadFile, path)
  if not ok or type(defs) ~= 'table' then return end
  local fresh, n = {}, 0
  for name, def in pairs(defs) do
    if not S.knownMats[name] then
      fresh[name] = def
      S.knownMats[name] = true
      n = n + 1
    end
  end
  if n == 0 then return end
  S.matSeq = (S.matSeq or 0) + 1
  local tmp = string.format('e2r_world/materials_%d.json', S.matSeq)
  jsonWriteFile(tmp, fresh, false)
  loadJsonMaterialsFile('/' .. tmp)
  result.materialLoads = (result.materialLoads or 0) + 1
  result.materialsLoaded = (result.materialsLoaded or 0) + n
end

local function tilePath(key, layer)
  local st = S.status and S.status.stream
  if NATIVE and st then
    local paths = layer == 'scene' and st.scenes or (layer == 'far' and st.far or st.tileManifests)
    return paths and paths[key]
  end
  return string.format('/e2r_stream/%s/tiles/%s%s.json', S.stream.region, key, layer and '.' .. layer or '')
end

local function startTile(key, i, j)
  local ok, manifest
  if NATIVE then ok, manifest = true, native.manifest(key, 'road')
  else ok, manifest = pcall(jsonReadFile, tilePath(key)) end
  if not ok or type(manifest) ~= 'table' then return end
  loadNewMaterials(manifest.materials)
  local tile = {state = 'loading', i = i, j = j, manifest = manifest, group = tileGroup('E2RT_' .. key), decor = tileGroup('E2RD_' .. key, true),
                left = #manifest.items, roadLeft = #manifest.items, startedAt = S.now}
  S.tiles[key] = tile
  if manifest.nav then table.insert(S.navPending, {key = key, path = manifest.nav}) end
  if manifest.navSegments then table.insert(S.navPending, {key = key, data = {segments = manifest.navSegments}}) end
  for _, it in ipairs(manifest.items) do table.insert(S.queue, {tile = tile, key = key, item = it}) end
  if #manifest.items == 0 then tile.state = 'loaded'; tile.roadComplete = true end
end

local function startScene(key, tile)
  local ok, manifest
  if NATIVE then ok, manifest = true, native.manifest(key, 'scene')
  else ok, manifest = pcall(jsonReadFile, tilePath(key, 'scene')) end
  if not ok or type(manifest) ~= 'table' or manifest.source ~= S.stream.region then return end
  loadNewMaterials(manifest.materials)
  tile.sceneStarted = true
  tile.sceneLeft = #manifest.items
  tile.sceneComplete = tile.sceneLeft == 0
  tile.left = tile.left + #manifest.items
  for _, it in ipairs(manifest.items) do table.insert(S.queue, {tile = tile, key = key, item = it, scene = true}) end
end

-- --- traffic -----------------------------------------------------------------------------------
-- The game's AI drives on a navgraph built from DecalRoads; ours are ProceduralMesh, so the graph
-- comes from the tiles (<key>.nav.json, fast.nav_segments) and goes to map.reset(nodes). Built
-- only while traffic runs, from the segments within NAV_R of the car, again once the car has gone
-- NAV_MOVE m from the last build (a rebuild is one frame of ~30 us a node).
local NAV_R, NAV_MOVE = 1000, 250
-- Agent Traffic mod (if installed): its light EU cars instead of the game's full vehicles
local AGENT_GROUP = '/vehicleGroups/agentTraffic/agent_traffic_eu.vehGroup.json'
-- 8 cars doubled the mean frame (12.7 -> 24.5 ms, traffic-05): each is a full soft-body vehicle
local TRAFFIC_AMOUNTS = {{0, L('Вимк', 'Off')}, {4, L('Мало', 'Light')}, {8, L('Середньо', 'Medium')},
  {14, L('Багато', 'Heavy')}}

local function navRead()
  local job = table.remove(S.navPending, 1)
  if not job then return end
  local ok, data = true, job.data
  if not data then ok, data = pcall(jsonReadFile, job.path) end
  if not ok or type(data) ~= 'table' or not data.segments then return end
  for _, s in ipairs(data.segments) do
    local seg = S.nav[s[1]]
    if not seg then
      local n = (#s - 7) / 3
      local mid = 8 + 3 * math.floor((n - 1) / 2)
      seg = {s = s, tiles = {}, x = s[mid], y = s[mid + 1]}
      S.nav[s[1]] = seg
      S.navDirty = true
    end
    seg.tiles[job.key] = true
  end
end

local function trafficRunning()
  return gameplay_traffic and gameplay_traffic.getState and gameplay_traffic.getState() ~= 'off'
end

local function navBuild()
  local p = focus()
  if not p then return end
  local t = hptimer()
  local nodes, segs, count = {}, 0, 0
  local function node(id, x, y, z, r)
    local n = nodes[id]
    if not n then
      n = {pos = vec3(x, y, z), radius = r, links = {}}
      nodes[id] = n
      count = count + 1
    elseif r > n.radius then
      n.radius = r
    end
    return id
  end
  for sid, seg in pairs(S.nav) do
    -- a segment whose every tile has gone goes too
    local alive = false
    for key in pairs(seg.tiles) do
      if S.tiles[key] then alive = true else seg.tiles[key] = nil end
    end
    if not alive then
      S.nav[sid] = nil
    elseif (seg.x - p.x) ^ 2 + (seg.y - p.y) ^ 2 < NAV_R * NAV_R then
      local s = seg.s
      local a, b, lanes, speed, radius, drive = s[2], s[3], s[4], s[5], s[6], s[7]
      local n = (#s - 7) / 3
      local oneWay = not lanes:find('-', 1, true)
      local prev
      for k = 1, n do
        local o = 8 + 3 * (k - 1)
        local id = k == 1 and ('e2r_n' .. tostring(a)) or k == n and ('e2r_n' .. tostring(b)) or ('e2r_s' .. sid .. '_' .. k)
        node(id, s[o], s[o + 1], s[o + 2], radius)
        if prev then
          local data = {drivability = drive, oneWay = oneWay, lanes = lanes, speedLimit = speed,
                        inNode = prev, outNode = id, inPos = nodes[prev].pos, outPos = nodes[id].pos,
                        inRadius = nodes[prev].radius, outRadius = nodes[id].radius}
          nodes[prev].links[id] = data
          nodes[id].links[prev] = data
        end
        prev = id
      end
      segs = segs + 1
    end
  end
  -- junction radii grew after their first links were made
  for _, n in pairs(nodes) do
    for lid, data in pairs(n.links) do
      data.inRadius, data.outRadius = nodes[data.inNode].radius, nodes[data.outNode].radius
    end
  end
  local tLua = t:stop()
  -- map.reset(custom) puts our table in map.nodes; the traffic extension keeps the old one
  -- (mapNodes, refreshed only on respawns) and indexed it every frame with our node names
  -- (traffic.lua:1252 "attempt to index a nil value", traffic-03). The old table keeps its place.
  local m = map.getMap()
  local keep = m.nodes
  map.reset(nodes)
  m = map.getMap()
  if keep and m.nodes ~= keep then
    table.clear(keep)
    for k, v in pairs(m.nodes) do keep[k] = v end
    m.nodes = keep
  end
  S.navSerial = m.buildSerial
  S.navDirty, S.navAt, S.navPos = false, S.now, {p.x, p.y}
  S.navStats = {nodes = count, segments = segs, ms = t:stop(), luaMs = tLua}
  result.nav = S.navStats
end

local function navStep()
  if #S.navPending > 0 then navRead() end
  if not trafficRunning() and not S.trafficWanted then return end
  -- someone else (an editor tool, a mission) rebuilt the graph from the empty level: ours again
  local foreign = S.navSerial and map.getMap().buildSerial ~= S.navSerial
  local p = focus()
  local moved = p and (not S.navPos or (p.x - S.navPos[1]) ^ 2 + (p.y - S.navPos[2]) ^ 2 > NAV_MOVE * NAV_MOVE)
  if foreign or (S.navDirty and moved) then navBuild() end
end

local function setTraffic(amount)
  if not gameplay_traffic then extensions.load('gameplay_traffic') end
  if not gameplay_traffic then S.lastError = L('gameplay_traffic недоступний', 'gameplay_traffic is not available'); return end
  S.trafficAmount = amount
  if amount == 0 then
    S.trafficWanted = nil
    gameplay_traffic.deleteVehicles()
    return
  end
  S.trafficWanted = true
  navBuild()
  if not next(map.getMap().nodes or {}) then
    S.lastError = L('Трафік: доріг поруч ще немає', 'Traffic: no roads nearby yet')
    return
  end
  local options = {}
  if FS:fileExists(AGENT_GROUP) then
    local ok, g = pcall(jsonReadFile, AGENT_GROUP)
    if ok and g and g.data and #g.data > 0 then
      local group = {}
      for k = 1, amount do group[k] = deepcopy(g.data[math.random(#g.data)]) end
      options.vehGroup = group
      S.trafficSource = 'Agent Traffic EU'
    end
  end
  if not options.vehGroup then S.trafficSource = L('машини гри', 'game cars') end
  result.trafficSource = S.trafficSource
  gameplay_traffic.setupTraffic(amount, options)
end

local function drawTraffic()
  local amount = trafficRunning() and (S.trafficAmount or -1) or 0
  im.Text(L('Трафік:', 'Traffic:'))
  local spacing = im.GetStyle().ItemSpacing.x
  local width = math.floor((im.GetContentRegionAvailWidth() - 3 * spacing) / 4)
  for k, a in ipairs(TRAFFIC_AMOUNTS) do
    if k > 1 then im.SameLine() end
    local active = amount == a[1]
    if active then
      im.PushStyleColor2(im.Col_Button, im.ImVec4(0.15, 0.55, 0.3, 1))
      im.PushStyleColor2(im.Col_ButtonHovered, im.ImVec4(0.2, 0.65, 0.35, 1))
    end
    if im.Button(a[2] .. '##traffic_' .. a[1], im.ImVec2(width, 0)) and not active then setTraffic(a[1]) end
    if active then im.PopStyleColor(2) end
  end
end

local function updateTiles()
  local st = S.status and S.status.stream
  if not (S.stream and st and st.region == S.stream.region) then return end
  local p, v = focus()
  if not p then return end
  local fast = st.generator ~= nil
  local loadR = fast and (st.loadRadius or 1000) or LOAD_R
  local unloadR = fast and loadR + 350 or UNLOAD_R
  -- fast, at speed: tiles well behind the car are neither kept nor loaded (the static collision
  -- costs ~7.5 ms per MB of loaded world at every rebuild); a U-turn loads them back from disk
  local speed = v:length()
  local function behind(cx, cy, d)
    if not fast or speed < 15 then return false end
    return ((cx - p.x) * v.x + (cy - p.y) * v.y) < 0 and d > loadR * 0.6 + 250
  end
  -- unload far tiles (their queued items are skipped)
  for key, tile in pairs(S.tiles) do
    local cx, cy = tileCentre(tile.i, tile.j)
    local d = math.sqrt((cx - p.x) ^ 2 + (cy - p.y) ^ 2)
    if d > unloadR or behind(cx, cy, d) then
      dropGroup('E2RT_' .. key); buryGroup('E2RD_' .. key)
      S.tiles[key] = nil
      if NATIVE then native.unload(key) end
      tile.dropped = true
      S.collisionDirty = true
      -- fast: a far tile's stale collision is harmless until the next rebuild; forcing one per
      -- unload (and per scene piece) made rebuilds back to back at 300 km/h
      if not (S.stream and S.stream.generator) then S.removedCollision = true end
      result.unloaded = (result.unloaded or 0) + 1
    end
  end
  -- start the nearest ready tile in range (one per call keeps the material loads apart);
  -- the look-ahead point counts the speed, so the tile ahead comes before the one behind
  local ax, ay = p.x + v.x * 3, p.y + v.y * 3
  local best, bestD
  for key, n in pairs(st.ready or {}) do
    if not S.tiles[key] then
      local i, j = key:match('^(-?%d+)_(-?%d+)$')
      i, j = tonumber(i), tonumber(j)
      local cx, cy = tileCentre(i, j)
      local dp = math.sqrt((cx - p.x) ^ 2 + (cy - p.y) ^ 2)
      local d = math.min(dp, math.sqrt((cx - ax) ^ 2 + (cy - ay) ^ 2))
      if d <= loadR and not behind(cx, cy, dp) and (not bestD or d < bestD) then best, bestD = {key, i, j}, d end
    end
  end
  if best and #S.queue < 400 then startTile(best[1], best[2], best[3]) end
  -- the nearest scene (look-ahead point too) whose road is in: it used to wait for every road tile
  -- in loadR and then took any one, so buildings next to the car came after roads 1 km away
  if fast and #S.queue < 300 and st.scenes then
    local bestS, bestSD
    for key, tile in pairs(S.tiles) do
      if tile.roadComplete and not tile.sceneStarted and st.scenes[key] then
        local cx, cy = tileCentre(tile.i, tile.j)
        local d = math.min(math.sqrt((cx - p.x) ^ 2 + (cy - p.y) ^ 2), math.sqrt((cx - ax) ^ 2 + (cy - ay) ^ 2))
        if not bestSD or d < bestSD then bestS, bestSD = {key, tile}, d end
      end
    end
    if bestS and (not best or bestSD < bestD) then startScene(bestS[1], bestS[2]) end
  end
  if fast then
    -- the far city (earthng/fast_far.py): a tile without its own scene, within farRadius, gets
    -- its far layer; the far layer goes when the scene is in or the car is far away
    local farR = st.farRadius or 3000
    -- at speed no new far tile starts (their churn at 300 km/h doubled be:reloadCollision,
    -- vis-replay-01); the ones there stay until they are far behind
    local farLoadR = speed > (st.farFast or 25) and 0 or farR
    for key, far in pairs(S.far) do
      local cx, cy = tileCentre(far.i, far.j)
      local d = math.sqrt((cx - p.x) ^ 2 + (cy - p.y) ^ 2)
      local near = S.tiles[key]
      if d > farR + 500 or (near and near.sceneComplete) then
        buryGroup('E2RF_' .. key)
        far.dropped = true
        S.far[key] = nil
        if NATIVE then native.unload(key, 'far') end
      end
    end
    if #S.queue < 60 then
      local bestF, bestFD
      for key in pairs(st.far or {}) do
        local near = S.tiles[key]
        if not S.far[key] and not (near and near.sceneComplete) then
          local i, j = key:match('^(-?%d+)_(-?%d+)$')
          i, j = tonumber(i), tonumber(j)
          local cx, cy = tileCentre(i, j)
          local d = math.sqrt((cx - p.x) ^ 2 + (cy - p.y) ^ 2)
          if d <= farLoadR and (not bestFD or d < bestFD) then bestF, bestFD = {key, i, j}, d end
        end
      end
      if bestF then
        local key, i, j = bestF[1], bestF[2], bestF[3]
        local ok, manifest
        if NATIVE then ok, manifest = true, native.manifest(key, 'far')
        else ok, manifest = pcall(jsonReadFile, tilePath(key, 'far')) end
        if ok and type(manifest) == 'table' and manifest.source == S.stream.region then
          loadNewMaterials(manifest.materials)
          local far = {i = i, j = j, left = #manifest.items, group = tileGroup('E2RF_' .. key, true), far = true}
          S.far[key] = far
          for _, it in ipairs(manifest.items) do table.insert(S.queue, {tile = far, key = key, item = it, far = true}) end
        end
      end
    end
    -- Reprioritize pending work when direction changes; no scene object can block roads, no far
    -- object a scene.
    -- Roads and scenes go by the car's arrival (wanted, s), a scene SCENE_LAG s after its road:
    -- the buildings by the car come before the roads of a tile a kilometre ahead.
    local wanted = st.wanted or {}
    local function rank(job) return job.far and 1 or 0 end
    local function due(job) return (wanted[job.key] or 1e6) + (job.scene and SCENE_LAG or 0) end
    table.sort(S.queue, function(a, b)
      local ra, rb = rank(a), rank(b)
      if ra ~= rb then return ra < rb end
      local da, db = due(a), due(b)
      if da ~= db then return da < db end
      return (a.scene and 1 or 0) < (b.scene and 1 or 0)
    end)
  end
end

local function stepQueue()
  local frame = hptimer()
  local function over() return frame:stop() >= BUDGET_MS end
  local function pause() if over() then coroutine.yield() end end
  if S.co and S.job and S.job.tile.dropped then S.co, S.job = nil, nil end
  repeat
    if not S.co then
      local job = table.remove(S.queue, 1)
      while job and job.tile.dropped do job = table.remove(S.queue, 1) end
      if not job then break end
      S.job = job
      local group = (job.far and job.tile.group) or (job.item.decor and job.tile.decor) or job.tile.group
      S.co = coroutine.create(function(p) spawnItem(job.item, group, p) end)
    end
    -- a resume far over the budget: is it the collector (Lua heap shrinks inside it)?
    local kb0, t0 = collectgarbage('count'), hptimer()
    local ok, err = coroutine.resume(S.co, pause)
    local resumeMs = t0:stop()
    if resumeMs > 30 then
      result.slowResumes = result.slowResumes or {}
      if #result.slowResumes < 40 then
        table.insert(result.slowResumes, {math.floor(S.now * 10) / 10, math.floor(resumeMs), math.floor(kb0),
                                          math.floor(collectgarbage('count')), S.job.item.name})
      end
    end
    if not ok then
      log('E', logTag, tostring(err))
      result.errors = (result.errors or 0) + 1
      if not S.job.far then S.job.tile.failed = true end
    end
    -- createMesh has copied the source arrays into the engine. Retaining every
    -- vertex table in Lua made later garbage collections pause driving frames.
    if NATIVE and ok and coroutine.status(S.co) == 'dead' then S.job.item.meshes = nil end
    if coroutine.status(S.co) == 'dead' and S.job.far then
      S.co = nil
      S.job.tile.left = S.job.tile.left - 1
      if NATIVE and S.job.tile.left == 0 then native.release(S.job.key, 'far') end
    elseif coroutine.status(S.co) == 'dead' then
      S.co = nil
      local tile = S.job.tile
      tile.left = tile.left - 1
      if not S.job.scene then tile.roadLeft = tile.roadLeft - 1 end
      if S.job.scene then
        tile.sceneLeft = tile.sceneLeft - 1
        tile.sceneComplete = tile.sceneLeft == 0 and not tile.failed
        if NATIVE and tile.sceneComplete then native.release(S.job.key, 'scene') end
      end
      if tile.roadLeft <= 0 and not tile.dropped and not tile.failed and not tile.roadComplete then
        tile.roadComplete = true
        if NATIVE then native.release(S.job.key, 'road') end
        tile.state = 'loaded'
        tile.loadSeconds = S.now - tile.startedAt
        S.collisionDirty = true
        result.tilesLoaded = (result.tilesLoaded or 0) + 1
        S.deliveryTimes = S.deliveryTimes or {}
        table.insert(S.deliveryTimes, tile.loadSeconds)
        if #S.deliveryTimes > 128 then table.remove(S.deliveryTimes, 1) end
        local sorted = {}
        for k, value in ipairs(S.deliveryTimes) do sorted[k] = value end
        table.sort(sorted)
        S.deliveryP99 = sorted[math.ceil(#sorted * .99)]
      end
      if S.job.scene and not tile.dropped then S.collisionDirty = true; tile.sceneDirty = true end
    end
  until over()
end

-- be:reloadCollision rebuilds the static collision of the whole loaded world; the call returns in
-- ~0.2 s but the frames after it stall for seconds (route-04), more with more tiles loaded. So it
-- runs only when a loaded tile without collision is near the car (COLLISION_R); far tiles get
-- theirs with the next rebuild. tileReady() (autopilot, touchdown) needs the collision.
local COLLISION_R = 450
local function updateCollision()
  if not S.collisionDirty or S.now - S.collisionAt < COLLISION_EVERY then return end
  local p, v = focus()
  local fast = S.stream and S.stream.generator
  local radius = fast and math.max(750, (v and v:length() or 0) * ((S.deliveryP99 or 2) + 4) + 354) or COLLISION_R
  local near = S.removedCollision or false
  for _, tile in pairs(S.tiles) do
    if tile.state == 'loaded' and not tile.collided and not (tile.manifest.empty or #tile.manifest.items == 0) then
      local cx, cy = tileCentre(tile.i, tile.j)
      if not p or math.sqrt((cx - p.x) ^ 2 + (cy - p.y) ^ 2) <= radius then near = true; break end
    end
    -- buildings/ground next to the car get theirs soon; far scenery waits for a road rebuild
    if fast and tile.sceneDirty and p then
      local cx, cy = tileCentre(tile.i, tile.j)
      if math.sqrt((cx - p.x) ^ 2 + (cy - p.y) ^ 2) <= 400 then near = true; break end
    end
  end
  if not near then return end
  local timer = hptimer()
  be:reloadCollision()
  local ms = timer:stop()
  result.reloadCollisionMs = math.max(result.reloadCollisionMs or 0, ms)
  result.collisionReloads = (result.collisionReloads or 0) + 1
  for _, tile in pairs(S.tiles) do
    if tile.roadComplete and not tile.failed then tile.collided = true end
    tile.sceneDirty = false
  end
  S.collisionDirty, S.collisionAt = false, S.now
  S.removedCollision = false
  return true
end

local function tileLoadedAt(x, y)
  local i, j = tileOf(x, y)
  local t = S.tiles[i .. '_' .. j]
  return t and not t.failed and t.roadComplete and t.collided and t.manifest.drivable ~= false
end

-- --- texture packs (fast generator) -----------------------------------------------------------------
-- packs.json (earthng/fast_art.py): pack -> material -> stage-0 fields. A switch rewrites the
-- fields of the loaded materials in place; no tile is rebuilt or reloaded.
-- Button names come from here first (the labels in packs.json are escaped JSON strings); a pack
-- the table does not know shows its packs.json label or its id.
local PACK_LABELS = LANG == 'en' and
  {real = 'Realistic', toon = 'Cartoon', model = 'Mock-up', synth = 'Synthwave',
   night = 'Night', rain = 'Rain', cyber = 'Cyberpunk', winter = 'Winter', autumn = 'Autumn'} or
  {real = 'Реальний', toon = 'Мультяшний', model = 'Макет', synth = 'Синтвейв',
   night = 'Ніч', rain = 'Дощ', cyber = 'Кіберпанк', winter = 'Зима', autumn = 'Осінь'}
local PACK_NAMES = {}
for _, name in ipairs({'real', 'toon', 'model', 'synth', 'night', 'rain', 'cyber', 'winter', 'autumn'}) do
  table.insert(PACK_NAMES, {name, PACK_LABELS[name]})
end

-- Street lights of a night pack: a pool of PointLights moved onto the lamps nearest the camera
-- (env.lights: colors, radius, brightness, height, count). Lamps are noted as tiles spawn them.
local function dropLights()
  for _, l in ipairs(S.lightPool or {}) do l:delete() end
  S.lightPool, S.lightCfg, S.lightsAt = nil, nil, nil
end

local function showLights(cfg)
  dropLights()
  if not cfg then return end
  S.lightCfg, S.lightPool = cfg, {}
  for k = 1, cfg.count or 24 do
    local c = cfg.colors[(k - 1) % #cfg.colors + 1]
    local l = createObject('PointLight')
    l:setField('radius', 0, tostring(cfg.radius or 20))
    l:setField('brightness', 0, tostring(cfg.brightness or 2))
    l:setField('color', 0, string.format('%f %f %f 1', c[1], c[2], c[3]))
    l:setField('castShadows', 0, '0')
    l:setField('flareType', 0, 'BNG_LightFlare_1')
    l:setField('flareScale', 0, tostring(cfg.flare or 0.6))
    l:registerObject('e2r_light_' .. k)
    l.canSave = false
    l:setPosition(vec3(0, 0, -10000))
    S.lightPool[k] = l
  end
  S.lightsAt = -1
end

local function lightsStep()
  local pool, cfg = S.lightPool, S.lightCfg
  if not pool or not S.lamps or S.now - (S.lightsAt or -1) < 0.4 then return end
  S.lightsAt = S.now
  local ok, cam = pcall(function() return core_camera.getPosition() end)
  if not ok or not cam then return end
  local near = {}
  for key, p in pairs(S.lamps) do
    local d = (p[1] - cam.x) ^ 2 + (p[2] - cam.y) ^ 2
    if d < 400 * 400 then near[#near + 1] = {d, p} elseif d > 2000 * 2000 then S.lamps[key] = nil end
  end
  table.sort(near, function(a, b) return a[1] < b[1] end)
  result.lampsNear, result.lightsPlaced = #near, math.min(#near, #pool)
  for k, l in ipairs(pool) do
    local n = near[k]
    if n then l:setPosition(vec3(n[2][1], n[2][2], n[2][3] + (cfg.height or 7.3))) else l:setPosition(vec3(0, 0, -10000)) end
  end
end

-- Rain or snow round the camera (env.precip: the game's PrecipitationData rain_medium or Snow_menu)
local function showPrecip(cfg)
  local old = scenetree.findObject('e2r_precip')
  if old then old:delete() end
  S.precipPending = nil
  if not cfg then return end
  -- not before the car is on the road: the drops regenerate round a camera that jumps from 350 m
  -- down to the car, and that touch-down frame took 114 ms (vis-packs-drive-cyber)
  if S.state ~= 'driving' or S.now - (S.phaseAt or 0) < 1.5 then
    S.precipPending = cfg
    return
  end
  local obj = createObject('Precipitation')
  obj:setField('dataBlock', 0, cfg.dataBlock)
  for k, v in pairs({numDrops = cfg.numDrops or 3000, boxWidth = cfg.boxWidth or 60, boxHeight = cfg.boxHeight or 40,
                     dropSize = cfg.dropSize or 0.6, minSpeed = cfg.minSpeed or 1.5, maxSpeed = cfg.maxSpeed or 2,
                     splashSize = cfg.splashSize or 0.25, splashMS = 250, fadeDist = 4, fadeDistEnd = 60,
                     useTurbulence = cfg.turbulence and 1 or 0, useWind = 1, followCam = 1}) do
    obj:setField(k, 0, tostring(v))
  end
  obj:registerObject('e2r_precip')
  obj.canSave = false
end

-- The synthwave sun: one big textured quad that keeps its place on the horizon, as far from the
-- camera as the view distance allows (env.sun: azimuth, distance, size, height, material).
local function placeSun()
  local cfg, obj = S.sunCfg, S.sunObj
  if not (cfg and obj) then return end
  local ok, cam = pcall(function() return core_camera.getPosition() end)
  if not ok or not cam then return end
  local a = math.rad(cfg.azimuth)
  obj:setPosition(vec3(cam.x + math.sin(a) * cfg.distance, cam.y + math.cos(a) * cfg.distance, cam.z + cfg.height))
end

local function showSun(cfg)
  if S.sunObj then S.sunObj:delete(); S.sunObj = nil end
  S.sunCfg = cfg or nil
  if not cfg then return end
  local a, h = math.rad(cfg.azimuth), cfg.size / 2
  local rx, ry = math.cos(a), -math.sin(a)          -- right of the view toward the sun
  local nx, ny = -math.sin(a), -math.cos(a)         -- faces the camera
  local verts = {{x = -rx * h, y = -ry * h, z = -h}, {x = rx * h, y = ry * h, z = -h},
                 {x = rx * h, y = ry * h, z = h}, {x = -rx * h, y = -ry * h, z = h}}
  local obj = createObject('ProceduralMesh')
  obj:registerObject('e2r_synth_sun')
  obj.canSave = false
  obj:createMesh({{{verts = verts, normals = {{x = nx, y = ny, z = 0}},
                    uvs = {{u = 0, v = 1}, {u = 1, v = 1}, {u = 1, v = 0}, {u = 0, v = 0}},
                    faces = {{v = 0, n = 0, u = 0}, {v = 2, n = 0, u = 2}, {v = 1, n = 0, u = 1},
                             {v = 0, n = 0, u = 0}, {v = 3, n = 0, u = 3}, {v = 2, n = 0, u = 2}},
                    material = cfg.material}}})
  S.sunObj = obj
  placeSun()
end

local function applyEnvObjects(env)
  for objName, fields in pairs(env.objects or {}) do
    local obj = scenetree.findObject(objName)
    if obj then
      -- in a fixed order and twice: the night cubemap took (or not) depending on the order
      -- pairs() gave nightCubemap and useNightCubemap (vis-packs-10 dark, -11 dusk, same values)
      local keys = {}
      for field in pairs(fields) do table.insert(keys, field) end
      table.sort(keys)
      for _ = 1, 2 do
        for _, field in ipairs(keys) do obj:setField(field, 0, fields[field]) end
        pcall(function() obj:postApply() end)
      end
      result.envCheck = result.envCheck or {}
      for field in pairs(fields) do result.envCheck[objName .. '.' .. field] = tostring(obj:getField(field, 0)) end
    else
      result.envMissing = objName
    end
  end
end

local function envAgainStep()
  local e = S.envAgain
  if not e or S.now < e.at[1] then return end
  table.remove(e.at, 1)
  -- the sun nudged first: a changed elevation makes the sky redo its lighting
  local sky = scenetree.findObject('sunsky')
  local el = e.env.objects and e.env.objects.sunsky and tonumber(e.env.objects.sunsky.elevation)
  if sky and el then
    sky:setField('elevation', 0, tostring(el - 0.3))
    pcall(function() sky:postApply() end)
  end
  applyEnvObjects(e.env)
  if #e.at == 0 then S.envAgain = nil end
end

local RELOADS_PER_FRAME = 2
local function reloadStep()
  local q = S.reloadQueue
  if not q or #q == 0 then return end
  local t = hptimer()
  for _ = 1, RELOADS_PER_FRAME do
    local mat = table.remove(q)
    if not mat then break end
    mat:reload()
  end
  local ms = t:stop()
  if ms > (result.reloadStepMs or 0) then result.reloadStepMs = ms end
end

local function applyPack(name)
  local pack = S.packs and S.packs[name]
  if not pack then return end
  local timer = hptimer()
  -- a field the previous pack set and this one does not goes back to the material's own value
  -- (noted the first time a pack changed it): roughness of wet roads, hidden leaves, tints
  S.packOrig = S.packOrig or {}
  -- only what differs is set, and only a changed material reloads (a reload of every one of
  -- ~90 materials took 0.6-0.9 s, vis-packs-10)
  local dirty = {}
  local function set(mat, matName, field, value)
    value = tostring(value)
    if mat:getField(field, 0) ~= value then
      mat:setField(field, 0, value)
      dirty[matName] = mat
    end
  end
  for matName, fields in pairs(S.packSet or {}) do
    local mat, orig = scenetree.findObject(matName), S.packOrig[matName]
    if mat and orig then
      for field in pairs(fields) do
        if not (pack[matName] and pack[matName][field] ~= nil) and orig[field] ~= nil then set(mat, matName, field, orig[field]) end
      end
    end
  end
  for matName, fields in pairs(pack) do
    local mat = scenetree.findObject(matName)
    if mat then
      local orig = S.packOrig[matName] or {}
      S.packOrig[matName] = orig
      for field, value in pairs(fields) do
        if orig[field] == nil then orig[field] = mat:getField(field, 0) end
        set(mat, matName, field, value)
      end
    end
  end
  -- reloaded a few a frame (reloadStep): ~80 in one frame stalled it for 0.5-1 s
  S.reloadQueue = {}
  for _, mat in pairs(dirty) do table.insert(S.reloadQueue, mat) end
  result.packReloads = #S.reloadQueue
  S.packSet = pack
  local tMats = timer:stop()
  -- sky, fog and the sun of the pack
  local env = S.packEnv and S.packEnv[name]
  if env then
    applyEnvObjects(env)
    -- again a little later: the sky's lighting is captured some frame after a change, and a
    -- capture during the switch left the same night lit like dusk in some runs (vis-packs-11, -13)
    S.envAgain = {env = env, at = {S.now + 0.7, S.now + 2.0, S.now + 4.0}}
    showSun(env.sun ~= false and env.sun or nil)
    showLights(env.lights ~= false and env.lights or nil)
    -- a night is a night only with a fixed exposure: the automatic one brightens it back to dusk
    local lx = scenetree.findObject('PostEffectLocalExposureObject')
    if lx then
      if S.exposureOrig == nil then S.exposureOrig = {lx.autoExposure, lx.manualEV} end
      if env.exposure then
        lx.autoExposure, lx.manualEV = false, env.exposure
      else
        lx.autoExposure, lx.manualEV = S.exposureOrig[1], S.exposureOrig[2]
      end
      result.exposure = {tostring(S.exposureOrig[1]), S.exposureOrig[2], tostring(lx.autoExposure), lx.manualEV}
    end
    showPrecip(env.precip ~= false and env.precip or nil)
  end
  S.pack = name
  result.pack, result.packSwitchMs = name, timer:stop()
  result.packSwitches = result.packSwitches or {}
  table.insert(result.packSwitches, {name, math.floor(tMats), math.floor(result.packSwitchMs)})
end

-- --- arrival ------------------------------------------------------------------------------------
local function cameraOver(height)
  local sp = S.landing.spawn.pos
  local p = vec3(sp[1] - height * 0.35, sp[2] - height * 0.55, sp[3] + height)
  local q = quatFromDir(vec3(sp[1], sp[2], sp[3]) - p, vec3(0, 0, 1))
  core_camera.setPosRot(0, p.x, p.y, p.z, q.x, q.y, q.z, q.w)
end

-- The land beyond the far city: one plane 40 km wide under the whole world at the lowest ground
-- round the landing (horizonZ from the companion), in the ground material, without collision.
local function makeHorizon(z, cx, cy)
  local old = scenetree.findObject('e2r_horizon')
  if old then old:delete() end
  if not z then return end
  local R, k = 20000, 1 / 24
  local verts = {{x = -R, y = -R, z = 0}, {x = R, y = -R, z = 0}, {x = R, y = R, z = 0}, {x = -R, y = R, z = 0}}
  local up = {x = 0, y = 0, z = 1}
  local uvs = {}
  for n, v in ipairs(verts) do uvs[n] = {u = v.x * k, v = v.y * k} end
  local faces = {}
  for _, v in ipairs({0, 2, 1, 0, 3, 2}) do table.insert(faces, {v = v, n = v, u = v}) end
  local obj = createObject('ProceduralMesh')
  obj:registerObject('e2r_horizon')
  obj.canSave = false
  obj:createMesh({{{verts = verts, normals = {up, up, up, up}, uvs = uvs, faces = faces, material = 'e2rf_ground'}}})
  obj:setPosition(vec3(cx, cy, z))
end

local function beginArrival(job)
  local manifest = type(job.manifest) == 'table' and job.manifest or jsonReadFile(job.manifest)
  if not manifest then return end
  -- leave the previous region
  for key, tile in pairs(S.tiles) do dropGroup('E2RT_' .. key); dropGroup('E2RD_' .. key); tile.dropped = true end
  for key, far in pairs(S.far) do dropGroup('E2RF_' .. key); far.dropped = true end
  S.tiles, S.far, S.queue, S.co = {}, {}, {}, nil
  -- traffic of the old place has no roads here
  if trafficRunning() then gameplay_traffic.deleteVehicles() end
  S.nav, S.navPending, S.navDirty, S.trafficWanted = {}, {}, false, nil
  S.stream = {region = job.region, country = job.country, tile = job.tileSize, generator = job.generator}
  -- the level's TimeOfDay at the landing: the sun of the place (Kyiv's at Los Angeles was hours off)
  if job.lat and job.lon then pcall(function() core_environment.setTimeOfDay({latitude = job.lat, longitude = job.lon}) end) end
  S.landing = manifest
  S.knownMats = {}
  S.lamps = {}
  loadNewMaterials(manifest.materials)
  if manifest.spawn then pcall(makeHorizon, job.horizonZ, manifest.spawn.pos[1], manifest.spawn.pos[2]) end
  if manifest.packs and FS:fileExists(manifest.packs) then
    local okP, packs = pcall(jsonReadFile, manifest.packs)
    S.packs = okP and packs and packs.fields or nil
    S.packEnv = okP and packs and packs.environment or nil
    if okP and packs and packs.order then
      PACK_NAMES = {}
      for _, name in ipairs(packs.order) do
        table.insert(PACK_NAMES, {name, PACK_LABELS[name] or (packs.labels and packs.labels[name]) or name})
      end
    end
    if okP and packs and packs.extra and not S.packsExtra then      -- the sun's material
      S.matSeq = (S.matSeq or 0) + 1
      local tmpE = string.format('e2r_world/materials_%d.json', S.matSeq)
      jsonWriteFile(tmpE, packs.extra, false)
      loadJsonMaterialsFile('/' .. tmpE)
      S.packsExtra = true
    end
    -- every pack's textures load now, the normal way, on warm-up triangles (removed with the
    -- warm-up; the materials keep them): a texture first met through a runtime swap streams in broken
    if okP and packs and packs.warm and not S.packsWarm then
      local group = tileGroup('E2RWorldPacks')
      local n = 0
      local all = {}
      for pack, defs in pairs(packs.warm) do all[pack] = defs end
      if packs.extra then all.extra = packs.extra end
      for pack, defs in pairs(all) do
        S.matSeq = (S.matSeq or 0) + 1
        local tmp = string.format('e2r_world/materials_%d.json', S.matSeq)
        jsonWriteFile(tmp, defs, false)
        loadJsonMaterialsFile('/' .. tmp)
        for name in pairs(defs) do
          n = n + 1
          local obj = createObject('ProceduralMesh')
          obj:registerObject('e2r_pk_' .. n)
          obj.canSave = false
          group:addObject(obj.obj)
          obj:createMesh({{{verts = {{x = 0, y = 0, z = 0}, {x = 3, y = 0, z = 0}, {x = 0, y = 3, z = 0}},
                            normals = {{x = 0, y = 0, z = 1}}, uvs = {{u = 0, v = 0}, {u = 1, v = 0}, {u = 0, v = 1}},
                            faces = {{v = 0, n = 0, u = 0}, {v = 2, n = 0, u = 2}, {v = 1, n = 0, u = 1}},
                            material = name}}})
          local sp0 = manifest.spawn.pos
          obj:setPosition(vec3(sp0[1] + (n % 20) * 4 - 40, sp0[2] + math.floor(n / 20) * 4 + 30, sp0[3] + 3))
        end
      end
      S.packsWarm = true
      result.packMaterialsWarmed = n
    end
    if S.pack and S.pack ~= 'real' then applyPack(S.pack) end   -- keep the player's choice
  end
  commands.setFreeCamera()
  cameraOver(CAM_HIGH)
  -- one visible triangle per material: texture upload and pipeline creation happen now, high up,
  -- instead of while driving (Obolon runs 24/25)
  local warm = tileGroup('E2RWorldWarmup')
  local sp = manifest.spawn.pos
  for k, mat in ipairs(manifest.mats) do
    local obj = createObject('ProceduralMesh')
    obj:registerObject('e2r_warm_' .. k)
    obj.canSave = false
    warm:addObject(obj.obj)
    obj:createMesh({{{verts = {{x = 0, y = 0, z = 0}, {x = 3, y = 0, z = 0}, {x = 0, y = 3, z = 0}},
                      normals = {{x = 0, y = 0, z = 1}}, uvs = {{u = 0, v = 0}, {u = 1, v = 0}, {u = 0, v = 1}},
                      faces = {{v = 0, n = 0, u = 0}, {v = 2, n = 0, u = 2}, {v = 1, n = 0, u = 1}},
                      material = mat}}})
    obj:setPosition(vec3(sp[1] + (k % 12) * 4 - 24, sp[2] + math.floor(k / 12) * 4 - 24, sp[3] + 3))
  end
  -- one instance of every streamed shape (trees), 40 m under the start for the whole session:
  -- the shape loads now, not under the first tree while driving
  local shapes = tileGroup('E2RWorldShapes')
  for k, shape in ipairs(manifest.warmShapes or {}) do
    local obj = createObject('TSStatic')
    obj:setField('shapeName', 0, shape)
    obj:setField('collisionType', 0, 'None')
    obj:setPosRot(sp[1] + k * 3, sp[2], sp[3] - 40, 0, 0, 0, 1)
    obj:registerObject('e2r_shape_' .. k)
    shapes:addObject(obj)
  end
  S.state, S.phaseAt, S.frames = 'warmup', S.now, {}
  result.readyAt, result.timings = S.now, job.timings
  saveResult()
end

-- Called after the landing tile is loaded and its collision rebuilt (teleporting in the same frame
-- as reloadCollision dropped the car through the new roads, world-test-04).
local function touchDown()
  local veh = getPlayerVehicle(0)
  local s = S.landing.spawn
  if veh then
    spawn.safeTeleport(veh, vec3(s.pos[1], s.pos[2], s.pos[3] + 0.5), quat(0, 0, math.sin(s.yaw / 2), math.cos(s.yaw / 2)))
  end
  commands.setGameCamera()
  S.state, S.phaseAt = 'driving', S.now
  S.driveAcc = newAcc()
  result.landedAt = S.now
  result.startupSeconds = S.landTimer and S.landTimer:stop() / 1000 or S.now - S.landAt
  saveResult()
end

-- --- autopilot (route test): pure pursuit along the companion's route ----------------------------

local function autopilotStart(route, speed)
  AP = {pts = route.points, idx = 1, speed = speed or 13, travelled = 0, last = nil, stuckAt = S.now}
  -- join the route at its point nearest to the car (the landing spawn is not the route start)
  local veh = getPlayerVehicle(0)
  if veh then
    local p, best = veh:getPosition(), math.huge
    for k, q in ipairs(AP.pts) do
      local d = (q[1] - p.x) ^ 2 + (q[2] - p.y) ^ 2
      if d < best then AP.idx, best = k, d end
    end
    AP.startIdx = AP.idx
  end
end

local function autopilotStep(dt)
  local veh = getPlayerVehicle(0)
  if not (AP and veh) then return end
  local p, v = veh:getPosition(), veh:getVelocity()
  local speed = v:length()
  if AP.last then AP.travelled = AP.travelled + math.sqrt((p.x - AP.last.x) ^ 2 + (p.y - AP.last.y) ^ 2) end
  AP.last = vec3(p.x, p.y, p.z)
  local pts = AP.pts
  -- advance along the route to the nearest point ahead (bounded search window)
  local bestK, bestD = AP.idx, math.huge
  for k = AP.idx, math.min(#pts, AP.idx + 60) do
    local d = (pts[k][1] - p.x) ^ 2 + (pts[k][2] - p.y) ^ 2
    if d < bestD then bestK, bestD = k, d end
  end
  AP.idx = bestK
  AP.offRoute = math.sqrt(bestD)
  if AP.idx >= #pts - 1 then
    AP.done = true
    veh:queueLuaCommand("input.event('throttle', 0, 2) input.event('brake', 1, 2) input.event('steering', 0, 2)")
    return
  end
  local look = math.max(7, speed * 1.1)
  local k, acc = AP.idx, 0
  while k < #pts and acc < look do
    acc = acc + math.sqrt((pts[k + 1][1] - pts[k][1]) ^ 2 + (pts[k + 1][2] - pts[k][2]) ^ 2)
    k = k + 1
  end
  local tx, ty = pts[k][1] - p.x, pts[k][2] - p.y
  local fwd = veh:getDirectionVector()
  local fl = math.sqrt(fwd.x ^ 2 + fwd.y ^ 2) + 1e-6
  local fx, fy = fwd.x / fl, fwd.y / fl
  local cross, dot = fx * ty - fy * tx, fx * tx + fy * ty
  local ang = math.atan2(cross, dot)                     -- >0: target to the left
  local steer = math.max(-1, math.min(1, -ang * 2.2))    -- BeamNG: steering -1 = left
  -- slow down for bends ahead and for tiles that are not there yet
  local k2, acc2 = AP.idx, 0
  local turn = 0
  while k2 < #pts - 1 and acc2 < 45 do
    local ax, ay = pts[k2 + 1][1] - pts[k2][1], pts[k2 + 1][2] - pts[k2][2]
    local bx, by = pts[k2 + 2][1] - pts[k2 + 1][1], pts[k2 + 2][2] - pts[k2 + 1][2]
    turn = turn + math.abs(math.atan2(ax * by - ay * bx, ax * bx + ay * by))
    acc2 = acc2 + math.sqrt(ax * ax + ay * ay)
    k2 = k2 + 1
  end
  local target = AP.speed
  if turn > 0.35 then target = math.min(target, 8) end
  if turn > 0.9 then target = math.min(target, 5.5) end
  local ahead = math.max(25, speed * 4)
  AP.waiting = not tileLoadedAt(p.x + fx * ahead, p.y + fy * ahead)
  AP.aheadKey = string.format('%d_%d', tileOf(p.x + fx * ahead, p.y + fy * ahead))
  if AP.waiting then target = 0 end
  -- stuck without a reason (not waiting for a tile): screenshot, then an assist that puts the car
  -- 20 m further along the route. Assists are counted and reported, never hidden.
  if not AP.waiting and speed < 0.5 then
    AP.stillFor = (AP.stillFor or 0) + dt
  else
    AP.stillFor = 0
  end
  if AP.stillFor > 8 then
    AP.stillFor = 0
    AP.assists = (AP.assists or 0) + 1
    result.assists = result.assists or {}
    table.insert(result.assists, {math.floor(S.now), math.floor(p.x), math.floor(p.y), math.floor(p.z * 10) / 10, AP.idx})
    shot(string.format('7-stuck-%02d', AP.assists))
    local k, acc = AP.idx, 0
    while k < #pts - 1 and acc < 20 do
      acc = acc + math.sqrt((pts[k + 1][1] - pts[k][1]) ^ 2 + (pts[k + 1][2] - pts[k][2]) ^ 2)
      k = k + 1
    end
    -- safeTeleport turns the given rotation by 180 degrees itself (spawn.lua), so the plain
    -- direction quaternion faces the car along the route (route-03 assists faced backwards)
    local dir = vec3(pts[k + 1][1] - pts[k][1], pts[k + 1][2] - pts[k][2], 0):normalized()
    spawn.safeTeleport(veh, vec3(pts[k][1], pts[k][2], p.z + 1.5), quatFromDir(dir, vec3(0, 0, 1)))
    AP.idx = k
    return
  end
  local throttle = math.max(0, math.min(0.6, (target - speed) * 0.25))
  local brake = speed > target + 1.5 and math.min(1, (speed - target) * 0.3) or 0
  -- the arcade gearbox engages reverse when the brake is held at a standstill (and then the
  -- throttle brakes): below 2 m/s stop with the parking brake instead (route-02 stall at 1.8 km)
  local park = 0
  if speed < 2 then
    brake = 0
    if target <= 0.5 then park, throttle = 1, 0 end
  end
  veh:queueLuaCommand(string.format("input.event('steering', %.3f, 2) input.event('throttle', %.3f, 2) input.event('brake', %.3f, 2) input.event('parkingbrake', %d, 2)",
    steer, throttle, brake, park))
end

-- --- UI ------------------------------------------------------------------------------------------
-- --- the map ---------------------------------------------------------------------------------
-- Drawn here from 256 px OSM tiles (one cached texture each; the companion copies missing ones to
-- e2r_world/maptiles/z/x/y.png). A tile not in yet shows a part of an ancestor tile, scaled up.
-- (A composed 720x440 picture per pan was slow, and the old one, shifted by UV while the next was
-- made, wrapped round over itself.)
local MAP_TEX_MAX, MAP_LOADS_PER_FRAME, MAP_RETRY = 320, 4, 8
local mapTex, mapTexN, mapAsked, mapWant, mapLoads, mapFrame = {}, 0, {}, {}, 0, 0
local function col(r, g, b, a) return im.GetColorU322(im.ImVec4(r, g, b, a or 1)) end

local function mapTile(z, x, y, load)
  local key = z .. '/' .. x .. '/' .. y
  local t = mapTex[key]
  if t then t.used = mapFrame; return t end
  if not load then return nil end
  if NATIVE then return nil end
  local asked = mapAsked[key]
  -- the file is looked for a little after asking, not with an FS call per tile and frame
  if asked and S.now - asked.at < 0.2 then return nil end
  local path = '/e2r_world/maptiles/' .. key .. '.png'
  if mapLoads < MAP_LOADS_PER_FRAME and FS:fileExists(path) then
    local ok, tex = pcall(imguiUtils.texObj, path)
    if ok and tex then
      mapLoads = mapLoads + 1
      tex.used = mapFrame
      mapTex[key], mapTexN, mapAsked[key] = tex, mapTexN + 1, nil
      if mapTexN > MAP_TEX_MAX then
        -- forget the least recently drawn half
        local list = {}
        for k, v in pairs(mapTex) do list[#list + 1] = {k, v.used} end
        table.sort(list, function(p, q) return p[2] < q[2] end)
        local drop = math.floor(#list / 2)
        for k = 1, drop do mapTex[list[k][1]] = nil end
        mapTexN = #list - drop
      end
      return tex
    end
  end
  if not asked then
    mapWant[#mapWant + 1] = {z, x, y}
    mapAsked[key] = {at = S.now, sent = S.now, first = S.now}
  elseif S.now - asked.sent > MAP_RETRY then
    mapWant[#mapWant + 1] = {z, x, y}
    asked.at, asked.sent = S.now, S.now
  else
    asked.at = S.now
  end
  return nil
end

-- world x, y (the frame of the landing) -> lat, lon by the companion's local affine (stream.geo)
local function worldToGeo(x, y)
  local g = S.status and S.status.stream and S.status.stream.geo
  if not g then return nil end
  local dx, dy = x - g[1], y - g[2]
  return g[4] + g[7] * dx + g[8] * dy, g[3] + g[5] * dx + g[6] * dy
end

-- the car on the map: lat, lon of it and of a point 30 m ahead
local function carOnMap()
  if S.state ~= 'driving' or not S.stream then return nil end
  local p, v = focus()
  if not p then return nil end
  local dir
  if v and v:length() > 2 then
    dir = v:normalized()
  else
    local veh = getPlayerVehicle(0)
    dir = veh and veh:getDirectionVector() or vec3(0, 1, 0)
  end
  local lat, lon = worldToGeo(p.x, p.y)
  if not lat then return nil end
  local lat2, lon2 = worldToGeo(p.x + dir.x * 30, p.y + dir.y * 30)
  return {lat = lat, lon = lon, lat2 = lat2, lon2 = lon2}
end

local function loadCoverage()
  if S.coverage or S.now - (S.coverageAt or -10) < 2 then return end
  S.coverageAt = S.now
  if FS:fileExists('/e2r_world/coverage.json') then
    local ok, c = pcall(jsonReadFile, '/e2r_world/coverage.json')
    if ok and c and c.boxes then S.coverage = c.boxes end
  end
end

-- view: {center = {lat, lon}, zoom, follow}; pick: a click sets the landing marker
local function drawMap(width, height, view, pick)
  width, height = width or W, height or H
  view = view or S
  mapFrame, mapLoads = mapFrame + 1, 0
  local car = carOnMap()
  if view.follow and car then view.center = {lat = car.lat, lon = car.lon} end
  im.InvisibleButton('##map', im.ImVec2(width, height))
  local hovered = im.IsItemHovered()
  local r0 = im.GetItemRectMin()
  local r1 = im.ImVec2(r0.x + width, r0.y + height)
  local dl = im.GetWindowDrawList()
  im.ImDrawList_PushClipRect(dl, r0, r1, true)
  im.ImDrawList_AddRectFilled(dl, r0, r1, col(0.67, 0.75, 0.8))
  local z = view.zoom
  local n = 2 ^ z
  local cx, cy = merc(view.center.lat, view.center.lon, z)
  local x0, y0 = cx - width / 2, cy - height / 2
  local function screen(lat, lon)
    local x, y = merc(lat, lon, z)
    return r0.x + x - x0, r0.y + y - y0
  end
  if NATIVE then planetMap.draw(im, dl, r0, width, height, view, merc, col) end
  -- the wrapper's default tint (IM_COL32_WHITE) is not defined in the game: nil broke AddImage
  local white = col(1, 1, 1)
  local missing = 0
  for tx = math.floor(x0 / 256), math.floor((x0 + width) / 256) do
    for ty = math.max(0, math.floor(y0 / 256)), math.min(n - 1, math.floor((y0 + height) / 256)) do
      local sx, sy = r0.x + tx * 256 - x0, r0.y + ty * 256 - y0
      local x = tx % n
      local t = mapTile(z, x, ty, true)
      if t then
        im.ImDrawList_AddImage(dl, t.texId, im.ImVec2(sx, sy), im.ImVec2(sx + 256, sy + 256), im.ImVec2(0, 0), im.ImVec2(1, 1), white)
      else
        missing = missing + 1
        for k = 1, math.min(6, z) do
          local f = 2 ^ k
          local a = mapTile(z - k, math.floor(x / f), math.floor(ty / f), false)
          if a then
            local u, v = (x % f) / f, (ty % f) / f
            im.ImDrawList_AddImage(dl, a.texId, im.ImVec2(sx, sy), im.ImVec2(sx + 256, sy + 256),
              im.ImVec2(u, v), im.ImVec2(u + 1 / f, v + 1 / f), white)
            break
          end
        end
      end
    end
  end
  if #mapWant > 0 then
    request({type = 'maptiles', tiles = mapWant})
    mapWant = {}
  end
  -- where a landing works
  loadCoverage()
  local green = col(0.16, 0.67, 0.31, 0.15)
  for _, c in ipairs(S.coverage or {}) do
    local ax, ay = screen(c[4], c[1])
    local bx, by = screen(c[2], c[3])
    if bx >= r0.x and by >= r0.y and ax <= r1.x and ay <= r1.y then
      im.ImDrawList_AddRectFilled(dl, im.ImVec2(ax, ay), im.ImVec2(bx, by), green)
    end
  end
  if NATIVE and S.stream then
    local seen = {}
    for _, tile in pairs(S.tiles) do
      for _, segment in ipairs(tile.manifest.navSegments or {}) do
        if not seen[segment[1]] then
          seen[segment[1]] = true
          local previous
          for k = 8, #segment, 3 do
            local lat, lon = worldToGeo(segment[k], segment[k + 1])
            if lat then
              local x, y = screen(lat, lon)
              if previous then
                im.ImDrawList_AddLine(dl, im.ImVec2(previous[1], previous[2]), im.ImVec2(x, y), col(0.4, 0.43, 0.45), 2)
              end
              previous = {x, y}
            end
          end
        end
      end
    end
  end
  if S.marker then
    local mx, my = screen(S.marker.lat, S.marker.lon)
    im.ImDrawList_AddCircleFilled(dl, im.ImVec2(mx, my), 7, col(1, 1, 1))
    im.ImDrawList_AddCircleFilled(dl, im.ImVec2(mx, my), 5, col(0.86, 0.12, 0.12))
  end
  if car then
    -- an arrow along the heading, with a white rim for any background
    local px, py = screen(car.lat, car.lon)
    local qx, qy = screen(car.lat2, car.lon2)
    local dx, dy = qx - px, qy - py
    local len = math.sqrt(dx * dx + dy * dy)
    if len < 1e-6 then dx, dy, len = 0, -1, 1 end
    dx, dy = dx / len, dy / len
    local function arrow(s, c)
      im.ImDrawList_AddTriangleFilled(dl,
        im.ImVec2(px + dx * 12 * s, py + dy * 12 * s),
        im.ImVec2(px - dx * 8 * s - dy * 8 * s, py - dy * 8 * s + dx * 8 * s),
        im.ImVec2(px - dx * 8 * s + dy * 8 * s, py - dy * 8 * s - dx * 8 * s), c)
    end
    arrow(1.35, col(1, 1, 1))
    arrow(1, col(0.1, 0.45, 0.95))
  end
  local credit = NATIVE and 'Natural Earth | (c) OpenStreetMap contributors' or '(c) OpenStreetMap'
  local tw = im.CalcTextSize(credit).x
  im.ImDrawList_AddRectFilled(dl, im.ImVec2(r1.x - tw - 8, r1.y - 18), r1, col(1, 1, 1, 0.75))
  im.ImDrawList_AddText1(dl, im.ImVec2(r1.x - tw - 4, r1.y - 17), col(0.15, 0.15, 0.15), credit)
  im.ImDrawList_PopClipRect(dl)
  -- a tile asked long ago and still not here: the companion could not download it
  if missing > 0 then
    for _, a in pairs(mapAsked) do
      if S.now - a.first > MAP_RETRY then
        im.TextColored(im.ImVec4(1, 0.6, 0.2, 1), L('Частина мапи не завантажилась (інтернет?) - пробую ще.',
          'Part of the map did not load (internet?) - retrying.'))
        break
      end
    end
  end
  -- wheel: zoom round the cursor (round the car while following); drag: pan, stops following;
  -- click: the landing point
  local io = im.GetIO()
  local mp = im.GetMousePos()
  local px, py = mp.x - r0.x, mp.y - r0.y
  if hovered and io.MouseWheel ~= 0 then
    local nz = math.max(NATIVE and 1 or 3, math.min(18, z + (io.MouseWheel > 0 and 1 or -1)))
    if not (view.follow and car) then
      local lat, lon = unmerc(x0 + px, y0 + py, z)
      local wx, wy = merc(lat, lon, nz)
      view.center = {}
      view.center.lat, view.center.lon = unmerc(wx - px + width / 2, wy - py + height / 2, nz)
    end
    view.zoom = nz
  end
  if hovered and im.IsMouseClicked(0) then view.dragging, view.dragMoved = true, 0 end
  if view.dragging then
    if im.IsMouseDown(0) then
      local d = im.GetMouseDragDelta(0, 0)
      if d.x ~= 0 or d.y ~= 0 then
        local ccx, ccy = merc(view.center.lat, view.center.lon, z)
        view.center = {}
        view.center.lat, view.center.lon = unmerc(ccx - d.x, ccy - d.y, z)
        view.dragMoved = view.dragMoved + math.abs(d.x) + math.abs(d.y)
        if view.dragMoved > 4 then view.follow = false end
        im.ResetMouseDragDelta(0)
      end
    else
      if pick and view.dragMoved < 4 and hovered then setMarker(unmerc(x0 + px, y0 + py, z)) end
      view.dragging = false
    end
  end
end

local function countTiles()
  local loaded, loading = 0, 0
  for _, t in pairs(S.tiles) do
    if t.state == 'loaded' then loaded = loaded + 1 else loading = loading + 1 end
  end
  return loaded, loading
end

-- texture packs: three equal buttons a row across the window, the active one lit
local function drawPacks()
  local current = S.pack or 'real'
  local label = current
  for _, p in ipairs(PACK_NAMES) do if p[1] == current then label = p[2] end end
  im.Text(L('Стиль: ', 'Style: ') .. label)
  local spacing = im.GetStyle().ItemSpacing.x
  local width = math.floor((im.GetContentRegionAvailWidth() - 2 * spacing) / 3)
  for k, p in ipairs(PACK_NAMES) do
    if (k - 1) % 3 ~= 0 then im.SameLine() end
    local active = current == p[1]
    if active then
      im.PushStyleColor2(im.Col_Button, im.ImVec4(0.15, 0.55, 0.3, 1))
      im.PushStyleColor2(im.Col_ButtonHovered, im.ImVec4(0.2, 0.65, 0.35, 1))
    end
    if im.Button(p[2] .. '##pack_' .. p[1], im.ImVec2(width, 0)) and not active then applyPack(p[1]) end
    if active then im.PopStyleColor(2) end
  end
end

local function recentFrames()
  local list, total, worst = {}, 0, 0
  for _, ms in pairs(S.recent or {}) do
    table.insert(list, ms); total = total + ms; worst = math.max(worst, ms)
  end
  if #list == 0 then return nil end
  table.sort(list)
  return 1000 * #list / math.max(total, 1e-3), list[math.ceil(#list * 0.99)], worst
end

local function drawStats(st, p, v)
  local fps, p99, worst = recentFrames()
  if fps then
    im.Text(string.format(L('FPS %.0f   кадр p99 %.0f мс, найгірший %.0f мс', 'FPS %.0f   frame p99 %.0f ms, worst %.0f ms'), fps, p99, worst))
  end
  if p then
    im.Text(string.format(L('Швидкість %.0f км/год   висота %.0f м', 'Speed %.0f km/h   altitude %.0f m'), (v and v:length() or 0) * 3.6, p.z))
  end
  if st and st.car then im.Text(string.format(L('Авто: %.5f, %.5f', 'Car: %.5f, %.5f'), st.car[2], st.car[1])) end
  -- tiles in the game
  local roads, collided, scenes, loading, failed, far = 0, 0, 0, 0, 0, 0
  for _, t in pairs(S.tiles) do
    if t.failed then failed = failed + 1
    elseif t.roadComplete then
      roads = roads + 1
      if t.collided then collided = collided + 1 end
      if t.sceneComplete then scenes = scenes + 1 end
    else loading = loading + 1 end
  end
  for _ in pairs(S.far) do far = far + 1 end
  im.Text(string.format(L('У грі: доріг %d (з колізією %d), сцен %d, далеких %d',
    'In game: roads %d (with collision %d), scenes %d, far %d'), roads, collided, scenes, far))
  im.Text(string.format(L('Вантажаться %d, збої %d, доставка p99 %.1f с', 'Loading %d, failed %d, delivery p99 %.1f s'), loading, failed, S.deliveryP99 or 0))
  if trafficRunning() and gameplay_traffic.getTrafficAmount then
    local nav = S.navStats or {}
    im.Text(string.format(L('Трафік (%s): авто %d, граф %d вузлів, збірка %.0f мс',
      'Traffic (%s): cars %d, graph %d nodes, build %.0f ms'), S.trafficSource or L('гра', 'game'),
      gameplay_traffic.getTrafficAmount(), nav.nodes or 0, nav.ms or 0))
  end
  if st and not NATIVE then
    local nFailed = 0
    for _ in pairs(st.failed or {}) do nFailed = nFailed + 1 end
    im.Text(string.format(L('Компаньйон: готові %d, будуються %d, черга %d, збої %d',
      'Companion: ready %d, building %d, queued %d, failed %d'),
      st.readyCount or 0, st.building or 0, st.queued or 0, nFailed))
    im.Text(string.format(L('Збірка тайла: середня %.1f с, p99 %.1f с, зібрано %d',
      'Tile build: mean %.1f s, p99 %.1f s, built %d'),
      st.meanBuildSeconds or 0, st.buildP99 or 0, st.built or 0))
    im.Text(string.format(L('Запас: %.0f с наперед, радіус %.1f км', 'Ahead: %.0f s, radius %.1f km'), st.horizonSeconds or 0, (st.loadRadius or 0) / 1000))
    if st.edgeKm then im.Text(string.format(L('До краю вікна індексу: %.1f км', 'To the index window edge: %.1f km'), st.edgeKm)) end
    if st.origin and p then
      local grey = im.ImVec4(0.65, 0.65, 0.65, 1)
      im.TextColored(grey, string.format(L('Початок координат: %.3f, %.3f, %s', 'Origin: %.3f, %.3f, %s'), st.origin[2], st.origin[1], st.crs or '?'))
      im.TextColored(grey, string.format(L('Авто від початку: %.1f км', 'Car from origin: %.1f km'), math.sqrt(p.x * p.x + p.y * p.y) / 1000))
    end
  end
end

local function drawWindow()
  if S.widgetHidden then
    im.Begin('EarthNG##restore', nil, im.WindowFlags_AlwaysAutoResize)
    if im.Button(L('Показати мапу', 'Show map')) then S.widgetHidden = false; S.viewDirty = true end
    im.End()
    return
  end
  if S.state == 'driving' and not S.pickerOpen then
    im.SetNextWindowSize(im.ImVec2(392, 0), im.Cond_Always)
    im.Begin('EarthNG##mini', nil, im.WindowFlags_NoCollapse + im.WindowFlags_AlwaysAutoResize)
    if im.Button(L('Приховати', 'Hide')) then S.widgetHidden = true end
    im.SameLine()
    if im.Button(L('Обрати інше місце', 'Pick another place')) then S.pickerOpen = true; S.viewDirty = true end
    -- the mini map follows the car (an arrow along the heading); a drag frees it
    S.mini = S.mini or {center = {lat = S.center.lat, lon = S.center.lon}, zoom = 15, follow = true}
    drawMap(360, 220, S.mini)
    if not S.mini.follow then
      if im.Button(L('До авто', 'To the car')) then S.mini.follow = true end
      im.SameLine()
    end
    im.TextColored(im.ImVec4(0.65, 0.65, 0.65, 1), L('Колесо - масштаб, перетягування - огляд', 'Wheel - zoom, drag - look around'))
    local st = S.status and S.status.stream
    local p, v = focus()
    if st and st.edgeKm and st.edgeKm < 3 then
      im.TextColored(im.ImVec4(1, 0.5, 0.2, 1), string.format(L('Край завантаженої області: %.1f км. Далі доріг немає -',
        'Edge of the loaded area: %.1f km. No roads beyond it -'), math.max(0, st.edgeKm)))
      im.TextColored(im.ImVec4(1, 0.5, 0.2, 1), L('висадьтеся ближче до нової точки ("Обрати інше місце").',
        'land again near a new point ("Pick another place").'))
    end
    if p and not tileLoadedAt(p.x, p.y) then
      im.TextColored(im.ImVec4(1, 0.7, 0.2, 1), L('Генерую світ попереду, зачекайте...', 'Generating the world ahead, please wait...'))
    end
    if AP then
      im.Text(string.format(L('Автопілот: %.1f км%s', 'Autopilot: %.1f km%s'), AP.travelled / 1000,
        AP.waiting and L(' (чекаю тайл)', ' (waiting for a tile)') or ''))
    end
    if S.packs then drawPacks() end
    drawTraffic()
    if im.CollapsingHeader1(L('Статистика', 'Statistics'), im.TreeNodeFlags_DefaultOpen) then drawStats(st, p, v) end
    im.End()
    return
  end
  im.SetNextWindowSize(im.ImVec2(W + 32, 0), im.Cond_Always)
  im.Begin(L('EarthNG - планета', 'EarthNG - Planet') .. '##picker', nil, im.WindowFlags_NoCollapse + im.WindowFlags_AlwaysAutoResize)
  if im.Button(L('Приховати', 'Hide')) then S.widgetHidden = true end
  if S.pickerOpen then
    im.SameLine()
    if im.Button(L('Повернутися до їзди', 'Back to driving')) then S.pickerOpen = false end
  end
  if S.state == 'pick' or S.pickerOpen then
    if NATIVE then
      local countries = S.status and S.status.countries or {}
      if #countries == 0 then
        im.TextWrapped(L('Пакетів країн немає. Додайте earthng_<країна>.zip до папки mods і ввімкніть його в менеджері модів.',
          'No country packs installed. Add earthng_<country>.zip to your mods folder and enable it in the Mod Manager.'))
      end
      for _, country in ipairs(countries) do
        if country.available and im.Button(country.title .. '##country_' .. country.id) then
          local point = country.landing
          if point then goTo(point.lat, point.lon, 13) end
        elseif not country.available then
          im.TextWrapped(country.title .. ': ' .. tostring(country.reason or L('недоступно', 'unavailable')))
        end
      end
    end
    im.Text(L('Колесо - масштаб, перетягування - рух, клік - точка висадки.',
      'Wheel - zoom, drag - pan, click - landing point.'))
    drawMap(W, H, S, true)
    local shown = 0
    for k, p in ipairs(PRESETS) do
      if presetInPackage(p) then
        if shown > 0 and not PRESET_ROWS[k] then im.SameLine() end
        if im.Button(p[1]) then goTo(p[2], p[3], 15) end
        shown = shown + 1
      end
    end
    im.InputText('##coords', coordBuf, 64)
    im.SameLine()
    if im.Button(L('Перейти до координат', 'Go to coordinates')) then
      local lat, lon = ffi.string(coordBuf):match('(-?%d+%.?%d*)%s*[,; ]%s*(-?%d+%.?%d*)')
      if lat then goTo(tonumber(lat), tonumber(lon), 15) end
    end
    im.Separator()
    if S.marker then
      im.Text(string.format(L('Точка: %.5f, %.5f', 'Point: %.5f, %.5f'), S.marker.lat, S.marker.lon))
      if not NATIVE or native.at(S.marker.lat, S.marker.lon) then
        if im.Button(L('Висадитися тут', 'Land here'), im.ImVec2(220, 32)) then land() end
      else
        im.TextWrapped(L('Для цієї точки немає активного пакета даних. Оберіть зелену зону або встановіть відповідний пакет.',
          'No active data pack covers this point. Pick a green area or install the corresponding pack.'))
      end
    else
      im.Text(L('Оберіть точку на мапі. Дані підготовлені там, де зелена зона.',
        'Pick a point on the map. Data is ready inside the green zone.'))
    end
  else
    local job = S.status and S.status.job
    if job and job.seq == S.landSeq then
      im.Text(string.format(L('Генерація %.5f, %.5f', 'Generating %.5f, %.5f'), job.lat or 0, job.lon or 0))
      im.ProgressBar(job.progress or 0, im.ImVec2(W, 0), tostring(job.stage or ''))
      if job.state == 'error' then
        im.TextWrapped(L('Помилка: ', 'Error: ') .. tostring(job.message))
        if im.Button(L('Назад до мапи', 'Back to the map')) then S.state = 'pick' end
      end
    elseif S.state == 'generating' then
      im.Text(NATIVE and L('Завантажую дані пакета...', 'Loading pack data...') or
        L('Запит надіслано, чекаю на компаньйон...', 'Request sent, waiting for the companion...'))
    end
    if S.state == 'warmup' or S.state == 'spawning' then
      local loaded = countTiles()
      im.ProgressBar(math.min(1, loaded / 4), im.ImVec2(W, 0), L('Спуск: будую світ', 'Descent: building the world'))
    end
  end
  if S.lastError then
    im.TextColored(im.ImVec4(1, 0.3, 0.3, 1), L('Помилка мода: ', 'Mod error: ') .. S.lastError:sub(1, 160))
  end
  if not serverUp() then
    im.TextColored(im.ImVec4(1, 0.45, 0.3, 1), L('Компаньйон Earth2Road не відповідає. Запустіть EarthNG.bat і натисніть «Запустити».',
      'The Earth2Road companion is not responding. Run EarthNG.bat and press Start.'))
  end
  if NATIVE and S.status and #(S.status.errors or {}) > 0 then
    if im.CollapsingHeader1(L('Проблеми з пакетами', 'Pack errors')) then
      for _, err in ipairs(S.status.errors) do im.TextWrapped(tostring(err.message or err)) end
    end
  end
  im.End()
end

-- --- lifecycle -----------------------------------------------------------------------------------
local function inOurLevel()
  local f = getMissionFilename and getMissionFilename() or ''
  return f:find('/e2r_world/', 1, true) ~= nil
end

local function refreshCountries()
  if not NATIVE then return end
  native.refresh()
  S.status, S.coverage, S.centredOnPackage = native.status(), nil, nil
  -- A removed/disabled pack must stop contributing geometry as well as map coverage.
  if S.stream then
    for key, tile in pairs(S.tiles) do
      dropGroup('E2RT_' .. key); dropGroup('E2RD_' .. key); tile.dropped = true
    end
    for key, far in pairs(S.far) do dropGroup('E2RF_' .. key); far.dropped = true end
    dropGroup('E2RWorldWarmup'); dropGroup('E2RWorldPacks'); dropGroup('E2RWorldShapes')
    local horizon = scenetree.findObject('e2r_horizon')
    if horizon then horizon:delete() end
    dropLights()
    if trafficRunning() then gameplay_traffic.deleteVehicles() end
    S.stream, S.co, S.job, S.landing = nil, nil, nil, nil
    S.tiles, S.far, S.queue, S.nav, S.navPending = {}, {}, {}, {}, {}
    S.collisionDirty, S.state = true, 'off'
  end
  if S.state == 'generating' or S.state == 'warmup' then
    S.state, S.landSeq, S.landAt, S.landTimer = 'off', nil, nil, nil
  end
end

function M.onModManagerModsMounted() refreshCountries() end
function M.onModActivated() refreshCountries() end
function M.onModDeactivated() refreshCountries() end
function M.onClientEndMission()
  if NATIVE then native.refresh() end
  S.stream, S.co, S.job, S.landing, S.state = nil, nil, nil, nil, 'off'
  S.tiles, S.far, S.queue, S.nav, S.navPending = {}, {}, {}, {}, {}
  S.knownMats, S.coverage, S.centredOnPackage = {}, nil, nil
  dropLights()
end

local function runTest()
  -- automation: pick, land, drive (straight, or the route with the autopilot), screenshots
  local t = S.now - (S.phaseAt or 0)
  if NATIVE and S.now - (S.nativeReportAt or -10) > 2 then
    S.nativeReportAt = S.now
    local status = native.status()
    local job, stream = status.job or {}, status.stream or {}
    result.native = {countryCount = status.countryCount, provider = status.provider,
      job = {state = job.state, progress = job.progress, message = job.message, stage = job.stage},
      built = stream.built, ready = stream.readyCount, error = S.lastError}
    saveResult()
  end
  if test.pickerOnly and S.state == 'pick' and t > 4 then
    shot('1-picker')
    local actual = NATIVE and native.status().countryCount or 0
    result.status = actual == (test.expectedCountries or 0) and not S.lastError and 'passed' or 'failed'
    saveResult()
    shutdown(result.status == 'passed' and 0 or 1)
    return
  end
  if test.pickerOnly then return end
  -- manual play: the automation only picks the point and lands; the player drives from there
  if test.manual and S.testLanded and S.state ~= 'pick' then
    -- keep the report current for the drive (it was frozen at the landing, play-bilychi-01)
    if S.state == 'driving' and S.now - (S.manualSaveAt or 0) > 5 then
      S.manualSaveAt = S.now
      result.status = 'manual'
      result.driveFrames = S.driveAcc and accStats(S.driveAcc) or nil
      local veh = getPlayerVehicle(0)
      if veh then
        local p = veh:getPosition()
        S.trace = S.trace or {}
        table.insert(S.trace, {math.floor(S.now), math.floor(p.x), math.floor(p.y), math.floor(p.z * 10) / 10,
                               math.floor(veh:getVelocity():length() * 10) / 10, countTiles()})
        if #S.trace > 4000 then table.remove(S.trace, 1) end
        result.trace = S.trace
      end
      saveResult()
    end
    return
  end
  if S.state == 'pick' and not S.testPicked and (mapTexN > 0 or test.skipMap) and t > 3 then
    shot('1-picker')
    goTo(test.lat, test.lon, 15)
    S.testPicked, S.phaseAt = S.now, S.now
  elseif S.state == 'pick' and S.testPicked and not S.testLanded and (test.skipMap or mapTexN > 0) and t > 2 then
    shot('2-marker')
    S.testLanded = true
    land()
  elseif S.state == 'driving' and not S.testDriving and S.now - S.phaseAt > 4 then
    local veh = getPlayerVehicle(0)
    local p = veh:getPosition()
    result.vehicleSettled = {p.x, p.y, p.z}
    local dir = veh:getDirectionVector()
    local yaw = S.landing.spawn.yaw
    -- the car's heading against the spawn's road direction (forward: sin, cos of yaw)
    result.spawnHeading = {dir.x, dir.y, math.sin(yaw), math.cos(yaw)}
    local hl = math.sqrt(dir.x ^ 2 + dir.y ^ 2) + 1e-9
    result.spawnHeadingErrorDeg = math.deg(math.acos(math.max(-1, math.min(1, (dir.x * math.sin(yaw) + dir.y * math.cos(yaw)) / hl))))
    -- a first-run dialog over the game pauses the simulation (fast-game-02 stood still under one)
    local okR, cur = pcall(function() return extensions.ui_router.getCurrent() end)
    result.uiRoute = okR and cur and cur.request and cur.request.name or nil
    local okP, paused = pcall(function() return simTimeAuthority.getPause() end)
    result.simPaused = okP and paused or nil
    shot('4-landed')
    -- the game's own weather and time controls need the level's TimeOfDay/Precipitation/CloudLayer
    local okE, env = pcall(function() return core_environment.getState() end)
    if okE and env then
      result.env = {time = env.time, cloudCover = env.cloudCover, numOfDrops = env.numOfDrops, fogDensity = env.fogDensity}
    end
    if test.rain then
      pcall(function() core_environment.setState({numOfDrops = test.rain, cloudCover = 0.9}) end)
      local okR2, env2 = pcall(function() return core_environment.getState() end)
      if okR2 and env2 then result.envRain = {numOfDrops = env2.numOfDrops, cloudCover = env2.cloudCover} end
    end
    if test.traffic then setTraffic(test.traffic) end
    S.testDriving, S.frames = S.now, {}
    if test.route then
      local ok, route = pcall(jsonReadFile, test.route)
      if ok and route then
        if test.replay then
          S.replay = {points = route.points, idx = 1, along = 0, distance = 0, speed = test.speed or 83.333}
          result.replay = true
          result.deadlineMisses = {}
          commands.setFreeCamera()
        else
          autopilotStart(route, test.speed)
        end
        result.route = {points = #route.points, length = route.length}
      end
    else
      veh:queueLuaCommand("input.event('throttle', 0.4, 2)")
    end
    S.trace, S.nextShotKm, S.lowestZ = {}, 2, math.huge
  elseif S.testDriving and not S.testDone then
    local veh = getPlayerVehicle(0)
    local p = veh:getPosition()
    -- straight drive: horizontal distance actually covered by the car
    if not S.replay then
      if S.lastP then S.travelled = (S.travelled or 0) + math.sqrt((p.x - S.lastP.x) ^ 2 + (p.y - S.lastP.y) ^ 2) end
      S.lastP = vec3(p.x, p.y, p.z)
    end
    if AP and AP.waiting then result.waitSeconds = (result.waitSeconds or 0) + (S.dt or 0) end
    if S.now - (S.traceAt or 0) > 1 then
      S.traceAt = S.now
      local loaded, loading = countTiles()
      -- the replay's trace is the synthetic pose, not the parked car
      local tp, speed = p, veh:getVelocity():length()
      if S.replayPose then tp, speed = S.replayPose.p, S.replayPose.v:length() end
      table.insert(S.trace, {math.floor(S.now), math.floor(tp.x), math.floor(tp.y), math.floor(tp.z * 10) / 10,
                             math.floor(speed * 10) / 10, loaded, loading, AP and math.floor(AP.travelled) or math.floor(S.travelled or 0),
                             AP and AP.offRoute and math.floor(AP.offRoute) or -1, AP and AP.waiting and AP.aheadKey or '',
                             AP and AP.idx or 0})
      if #S.trace > 4000 then table.remove(S.trace, 1) end
      result.trace = S.trace
      if test.traffic and gameplay_traffic then
        -- how many AI cars there are, how many move, the nearest one
        local n, moving, nearest = 0, 0, math.huge
        for _, id in ipairs(gameplay_traffic.getTrafficList() or {}) do
          local o = be:getObjectByID(id)
          if o then
            n = n + 1
            if o:getVelocity():length() > 2 then moving = moving + 1 end
            nearest = math.min(nearest, (o:getPosition() - p):length())
          end
        end
        result.trafficTrace = result.trafficTrace or {}
        table.insert(result.trafficTrace, {math.floor(S.now), n, moving, nearest < math.huge and math.floor(nearest) or -1})
        result.trafficMovingMax = math.max(result.trafficMovingMax or 0, moving)
      end
      result.driveFrames = accStats(S.driveAcc)
      if not S.replay and p.z < (S.lowestZ or math.huge) then S.lowestZ = p.z; result.lowestZ = p.z end
      saveResult()
    end
    if AP and AP.travelled / 1000 >= S.nextShotKm then
      shot(string.format('6-route-%02dkm', S.nextShotKm))
      S.nextShotKm = S.nextShotKm + 2
    end
    local limit = test.driveSeconds or 8
    if (AP and (AP.done or S.now - S.testDriving > limit)) or (not AP and S.now - S.testDriving > limit) then
      veh:queueLuaCommand("input.event('throttle', 0, 2) input.event('brake', 1, 2)")
      result.vehicleAfterDrive = {p.x, p.y, p.z, veh:getVelocity():length()}
      result.driveFrames = accStats(S.driveAcc)
      result.travelledM = AP and AP.travelled or S.travelled or 0
      if AP then result.routeDone = AP.done or false end
      shot('5-driving')
      -- 'passed' only when every gate holds; finishing the timer alone is not a result
      local f = {}
      if result.uiRoute and result.uiRoute ~= 'play' then table.insert(f, 'ui_route_' .. tostring(result.uiRoute)) end
      if result.simPaused then table.insert(f, 'sim_paused') end
      if S.replay then
        if #(result.deadlineMisses or {}) > 0 then table.insert(f, 'deadline_misses') end
        local want = (S.replay.speed or 0) * limit * 0.95
        if (result.replayDistanceM or 0) < want then table.insert(f, 'replay_short') end
      else
        if result.travelledM < (test.minDistance or 20) then table.insert(f, 'not_moved') end
        if (result.spawnHeadingErrorDeg or 0) > 20 then table.insert(f, 'spawn_heading') end
        if result.vehicleSettled and S.lowestZ and S.lowestZ < result.vehicleSettled[3] - 30 then table.insert(f, 'fell') end
        if AP and (AP.assists or 0) > 0 then table.insert(f, 'assists') end
        if (result.waitSeconds or 0) > 0 then table.insert(f, 'waited_for_tiles') end
      end
      if result.driveFrames.over100 > 0 then table.insert(f, 'frame_over_100ms') end
      if (result.errors or 0) > 0 then table.insert(f, 'spawn_errors') end
      result.failures = f
      AP = nil
      S.testDone = S.now
    end
  elseif S.testDone and not S.overviewShot and S.now - S.testDone > 0.5 then
    -- a view over the street from 60 m: the look of the city, not just the road under the car
    local veh = getPlayerVehicle(0)
    local p, d = veh:getPosition(), veh:getDirectionVector()
    local f = vec3(d.x, d.y, 0):normalized()
    local cam = vec3(p.x - f.x * 70, p.y - f.y * 70, p.z + 60)
    local q = quatFromDir(vec3(p.x + f.x * 80, p.y + f.y * 80, p.z) - cam, vec3(0, 0, 1))
    commands.setFreeCamera()
    core_camera.setPosRot(0, cam.x, cam.y, cam.z, q.x, q.y, q.z, q.w)
    S.overviewShot = S.now
  elseif S.overviewShot and not S.overviewTaken and S.now - S.overviewShot > 1.5 then
    shot('6-overview')
    S.overviewTaken = S.now
    S.packQueue = test.packs and S.packs and {'toon', 'model', 'synth', 'real'} or nil
  elseif S.packQueue and #S.packQueue > 0 and S.now - S.overviewTaken > 1.5 then
    -- the same view in every pack (test.packs), then back to the default
    local name = table.remove(S.packQueue, 1)
    applyPack(name)
    S.overviewTaken = S.now
    if name ~= 'real' then S.packShot = name end
    if name == 'synth' and S.sunCfg then
      -- look toward the sun from where the overview camera is
      local okC, cam = pcall(function() return core_camera.getPosition() end)
      if okC and cam then
        local a = math.rad(S.sunCfg.azimuth)
        local q = quatFromDir(vec3(math.sin(a), math.cos(a), -0.12), vec3(0, 0, 1))
        core_camera.setPosRot(0, cam.x, cam.y, cam.z, q.x, q.y, q.z, q.w)
      end
    end
  elseif S.packShot and S.now - S.overviewTaken > 1 then
    shot('7-pack-' .. S.packShot)
    S.packShot = nil
  elseif test.gallery and S.overviewTaken and not S.galleryDone and not (S.packQueue and #S.packQueue > 0) and not S.packShot
      and S.now - S.overviewTaken > (S.gallery and 1 or (test.galleryWait or 1)) then
    -- fixed views round the car for before/after comparisons of the look (test.gallery)
    if not S.gallery then
      local veh = getPlayerVehicle(0)
      local p, d = veh:getPosition(), veh:getDirectionVector()
      local f = vec3(d.x, d.y, 0):normalized()
      local r = vec3(f.y, -f.x, 0)
      local function at(back, side, up) return vec3(p.x - f.x * back + r.x * side, p.y - f.y * back + r.y * side, p.z + up) end
      S.gallery = {
        {'g1-street', at(7, 0, 1.9), at(-45, 0, 1.2)},
        {'g2-left', at(0, -1.5, 1.7), at(-18, -30, 7)},
        {'g3-right', at(0, 1.5, 1.7), at(-18, 30, 7)},
        {'g4-low', at(45, 8, 22), at(-60, 0, 0)},
        {'g5-high', at(220, 30, 140), at(-180, 0, 0)},
      }
      for _, v in ipairs(test.views or {}) do
        table.insert(S.gallery, {'v-' .. v.name, vec3(v.cam[1], v.cam[2], v.cam[3]), vec3(v.at[1], v.at[2], v.at[3])})
      end
      S.galleryIdx, S.galleryAt, S.galleryPrefix = 0, S.now - 10, ''
      -- test.packGallery: the same views once per pack
      S.galleryPacks = {}
      for _, name in ipairs(test.packGallery or {}) do table.insert(S.galleryPacks, name) end
      if #S.galleryPacks > 0 then
        local name = table.remove(S.galleryPacks, 1)
        applyPack(name)
        S.galleryPrefix, S.galleryAt = name .. '-', S.now + 3
      end
      -- the game's HUD (dashboard, camera help) off for the pictures
      pcall(function() extensions.ui_visibility.setCef(false) end)
    end
    if S.galleryPending and S.now - S.galleryAt > 1.6 then
      shot(S.galleryPending)
      S.galleryPending = nil
      S.galleryAt = S.now
    elseif not S.galleryPending and S.now - S.galleryAt > 0.3 then
      S.galleryIdx = S.galleryIdx + 1
      local v = S.gallery[S.galleryIdx]
      if not v and #S.galleryPacks > 0 then
        local name = table.remove(S.galleryPacks, 1)
        applyPack(name)
        S.galleryPrefix, S.galleryIdx, S.galleryAt = name .. '-', 0, S.now + 3
      elseif not v then
        S.galleryDone = true
        pcall(function() extensions.ui_visibility.setCef(true) end)
        S.overviewTaken = S.now - 2
      else
        local q = quatFromDir(v[3] - v[2], vec3(0, 0, 1))
        commands.setFreeCamera()
        core_camera.setPosRot(0, v[2].x, v[2].y, v[2].z, q.x, q.y, q.z, q.w)
        S.galleryPending, S.galleryAt = S.galleryPrefix .. v[1], S.now
      end
    end
  elseif S.overviewTaken and not (S.packQueue and #S.packQueue > 0) and not S.packShot and S.now - S.overviewTaken > 1
      and (not test.gallery or S.galleryDone) then
    result.status = #result.failures == 0 and 'passed' or 'failed'
    saveResult()
    shutdown(0)
  end
end

local function replayStep(dt)
  local r = S.replay
  if not r then return end
  r.distance = r.distance + r.speed * dt
  local pts = r.points
  while r.idx < #pts do
    local a,b = pts[r.idx],pts[r.idx+1]
    local len = math.sqrt((b[1]-a[1])^2+(b[2]-a[2])^2)
    if r.along+len > r.distance then break end
    r.along = r.along+len
    r.idx = r.idx+1
  end
  if r.idx >= #pts then
    -- turn round and drive the route back (a long soak keeps moving; tiles behind get revisited)
    result.replayDone = true
    result.replayLaps = (result.replayLaps or 0) + 1
    local back = {}
    for k = #pts, 1, -1 do back[#back + 1] = pts[k] end
    r.points, r.idx, r.along, r.lapStart = back, 1, r.distance, r.distance
    pts = back
  end
  local a,b = pts[r.idx],pts[r.idx+1]
  local d = vec3(b[1]-a[1],b[2]-a[2],0):normalized()
  local p = vec3(a[1],a[2],200)+d*(r.distance-r.along)
  S.replayPose = {p=p,v=d*r.speed}
  local q = quatFromDir(vec3(d.x,d.y,-.45),vec3(0,0,1))
  core_camera.setPosRot(0,p.x-d.x*30,p.y-d.y*30,240,q.x,q.y,q.z,q.w)
  local i,j = tileOf(p.x,p.y)
  local key = i .. '_' .. j
  if not tileLoadedAt(p.x,p.y) and S.lastMiss ~= key then
    table.insert(result.deadlineMisses,{seconds=S.now-S.testDriving,tile=key,distance=r.distance})
    S.lastMiss = key
  end
  result.replayDistanceM = r.distance
end

function M.onUpdate(dtReal)
  if not inOurLevel() then
    S.state = 'off'
    return
  end
  S.now, S.dt = S.now + dtReal, dtReal
  local frameMs = frameTimer:stopAndReset()
  table.insert(S.frames, frameMs)
  S.recentAt = (S.recentAt or 0) % 240 + 1        -- the widget's FPS: the last 240 frames
  S.recent = S.recent or {}
  S.recent[S.recentAt] = frameMs
  if S.driveAcc and S.state == 'driving' then accFrame(S.driveAcc, frameMs, S.now) end
  if frameMs > 300 and S.stream then
    result.hitches = result.hitches or {}
    if #result.hitches < 60 then table.insert(result.hitches, {math.floor(S.now), math.floor(frameMs), S.state}) end
  end
  if #S.frames > 20000 then S.frames = {} end
  if S.state == 'off' then
    if worldReadyState < 2 or not getPlayerVehicle(0) then return end
    S.state, S.phaseAt, S.viewDirty = 'pick', S.now, true
    -- request numbers start from the clock so a status.json left by an earlier session never matches
    S.seq = os.time() * 100
    commands.setFreeCamera()
    core_camera.setPosRot(0, -3000, -60, 40, 0, 0, 0, 1)
  end
  -- automation only: the driver may write the file after start-up (a new profile's first start
  -- removes unknown files from the user folder, world-test-06)
  if not test and S.state == 'pick' and S.now - (S.testCheckAt or -1) > 1 then
    S.testCheckAt = S.now
    if FS:fileExists('/e2r-world-test.json') then
      test = jsonReadFile('/e2r-world-test.json')
      if test and test.pack then S.pack = test.pack end     -- drive in a pack (applied at the landing)
      pcall(function() local tod = scenetree.tod; if tod then tod.play = false end end)
      result.status = 'picker_open'
      S.phaseAt = S.now
      saveResult()
    end
  end
  -- an error in the logic must not stop the window from being drawn (it flickered when it did)
  local ok, err = xpcall(M.tick, debug.traceback, dtReal)
  if not ok then
    if S.lastError ~= err then log('E', logTag, err) end
    S.lastError = err
  end
  drawWindow()
  if test then
    local ok2, err2 = xpcall(runTest, debug.traceback)
    if not ok2 and S.lastError ~= err2 then log('E', logTag, err2); S.lastError = err2 end
  end
end

function M.tick(dt)
  if NATIVE then native.update(dt, BUDGET_MS) end
  placeSun()
  lightsStep()
  reloadStep()
  if S.precipPending and S.state == 'driving' and S.now - (S.phaseAt or 0) >= 1.5 then showPrecip(S.precipPending) end
  envAgainStep()
  readStatus()
  replayStep(dt)
  if S.state == 'generating' then
    local job = S.status and S.status.job
    if job and job.seq == S.landSeq and job.state == 'ready' then beginArrival(job) end
  elseif S.state == 'warmup' and S.now - S.phaseAt > 3 then
    if test then shot('3-descent') end
    result.warmupFrames = frameStats(S.frames)
    dropGroup('E2RWorldWarmup')
    dropGroup('E2RWorldPacks')
    S.state, S.frames, S.phaseAt = 'spawning', {}, S.now
  end
  if S.stream and (S.state == 'spawning' or S.state == 'driving') then
    -- which step made a slow frame (result.slow, automation and diagnosis)
    local timer = hptimer()
    writePose()
    local tPose = timer:stopAndReset()
    updateTiles()
    local tTiles = timer:stopAndReset()
    -- a frame that rebuilds the collision (up to ~65 ms) spawns no mesh: together with a
    -- createMesh and the render they reached 98 ms (fast-oblast-replay-01)
    local reloaded = updateCollision()
    local tColl = timer:stopAndReset()
    if not reloaded then stepQueue(); digGraves() end
    local tQueue = timer:stopAndReset()
    if S.state == 'driving' then navStep() end
    local tNav = timer:stopAndReset()
    if tNav > (result.navStepMs or 0) then result.navStepMs = tNav end
    if tPose + tTiles + tQueue + tColl > 50 then
      result.slow = result.slow or {}
      if #result.slow < 60 then
        table.insert(result.slow, {math.floor(S.now), math.floor(tPose), math.floor(tTiles), math.floor(tQueue), math.floor(tColl),
                                   S.job and S.job.item and S.job.item.name or ''})
      end
    end
  end
  if S.state == 'spawning' then
    local sp = S.landing.spawn.pos
    local loaded = countTiles()
    cameraOver(CAM_HIGH + (CAM_LOW - CAM_HIGH) * math.min(1, loaded / 4))
    -- touch down once the landing tile is in and its collision has been rebuilt a while ago
    local buffered = true
    if S.stream.generator then
      local li,lj = tileOf(sp[1],sp[2])
      local landingTile = S.tiles[li .. '_' .. lj]
      if not landingTile or not landingTile.sceneComplete then buffered = false end
      for x = sp[1]-500, sp[1]+500, 250 do
        for y = sp[2]-500, sp[2]+500, 250 do
          local i,j = tileOf(x,y)
          local t = S.tiles[i .. '_' .. j]
          if not t or t.failed or not t.roadComplete or (#t.manifest.items > 0 and not t.collided) then buffered = false end
        end
      end
      -- the descent only (the index of a big city's window, before it, takes minutes and is
      -- not the stream's fault); a warning in play, a failed run in automation
      if not result.startupFailed and S.now - S.phaseAt > 300 then
        result.startupFailed = true
        S.lastError = 'Descent exceeded 300 seconds'
        if test then result.status = 'failed'; saveResult(); shutdown(1) end
      end
    end
    if buffered and tileLoadedAt(sp[1], sp[2]) and S.collisionAt > 0 and S.now - S.collisionAt > 0.2 then
      result.spawnFrames = frameStats(S.frames)
      result.spawnSeconds = S.now - S.phaseAt
      S.frames = {}
      touchDown()
    end
  end
  if S.state == 'driving' and AP then autopilotStep(dt) end
end

function M.onExtensionLoaded()
  setExtensionUnloadMode(M, 'manual')
  if NATIVE then native.refresh() end
  log('I', logTag, 'Earth2Road planet extension loaded')
end

return M
