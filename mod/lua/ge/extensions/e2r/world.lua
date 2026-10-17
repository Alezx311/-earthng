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
-- the UI language: the package's LANG until the player switches it (settings/earthng.json)
local LANG_FILE = 'settings/earthng.json'
local lang = LANG
local function L(uk, en) return lang == 'en' and en or uk end
local function loadLang()
  local ok, cfg = pcall(jsonReadFile, LANG_FILE)
  if ok and type(cfg) == 'table' and (cfg.lang == 'uk' or cfg.lang == 'en') then lang = cfg.lang end
end
local function setLang(l)
  lang = l
  pcall(jsonWriteFile, LANG_FILE, {lang = l}, true)
end

local W, H = 720, 440          -- map picture size, px
local BUDGET_MS = 4            -- spawn work per frame
local CAM_HIGH, CAM_LOW = 350, 28
local LOAD_R, UNLOAD_R = 800, 1250   -- tile centre distance from the car, m
local SCENE_LAG = 3            -- s: a scene item queues as the road of a tile reached this much later
local COLLISION_EVERY = 2.0          -- s between batched reloadCollision calls
local LAMP_SHAPE = '/art/shapes/objects/lamp1.dae'   -- fast_style.LAMP
local PRESETS = {
  {'Оболонь', 50.5070, 30.4990, 'ukraine', 'Obolon'}, {'Поділ', 50.4655, 30.5170, 'ukraine', 'Podil'},
  {'Хрещатик', 50.4475, 30.5225, 'ukraine', 'Khreshchatyk'}, {'Печерськ', 50.4280, 30.5410, 'ukraine', 'Pechersk'},
  {'Голосіїв', 50.3920, 30.5080, 'ukraine', 'Holosiiv'}, {'Біличі', 50.4775, 30.3600, 'ukraine', 'Bilychi'},
  -- (the 5th field: the English name; the rest of the presets are English already)
  -- Kyiv oblast towns (data/osm_tiles/ukraine-260918)
  {'Ірпінь', 50.5210, 30.2500, 'ukraine', 'Irpin'}, {'Бровари', 50.5110, 30.7900, 'ukraine', 'Brovary'},
  {'Бориспіль', 50.3520, 30.9550, 'ukraine', 'Boryspil'}, {'Фастів', 50.0780, 29.9180, 'ukraine', 'Fastiv'},
  {'Біла Церква', 49.7960, 30.1150, 'ukraine', 'Bila Tserkva'},
  -- oblast centres (data/osm_tiles/ukraine-260918)
  {'Львів', 49.8400, 24.0280, 'ukraine', 'Lviv'}, {'Одеса', 46.4800, 30.7300, 'ukraine', 'Odesa'},
  {'Харків', 49.9900, 36.2300, 'ukraine', 'Kharkiv'}, {'Дніпро', 48.4650, 35.0460, 'ukraine', 'Dnipro'},
  {'Запоріжжя', 47.8390, 35.1400, 'ukraine', 'Zaporizhzhia'}, {'Вінниця', 49.2330, 28.4680, 'ukraine', 'Vinnytsia'},
  {'Житомир', 50.2550, 28.6590, 'ukraine', 'Zhytomyr'}, {'Чернігів', 51.4980, 31.2890, 'ukraine', 'Chernihiv'},
  {'Полтава', 49.5880, 34.5510, 'ukraine', 'Poltava'}, {'Черкаси', 49.4440, 32.0600, 'ukraine', 'Cherkasy'},
  {'Ужгород', 48.6210, 22.2880, 'ukraine', 'Uzhhorod'}, {'Чернівці', 48.2920, 25.9400, 'ukraine', 'Chernivtsi'},
  {'Івано-Франківськ', 48.9230, 24.7110, 'ukraine', 'Ivano-Frankivsk'}, {'Луцьк', 50.7470, 25.3250, 'ukraine', 'Lutsk'},
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
local S = {
  widgetHidden = false, pickerOpen = false,
  state = 'off', center = {lat = 50.4501, lon = 30.5234}, zoom = 11, marker = nil,
  seq = 0, viewDirty = true, lastViewAt = -1, tex = nil, texView = nil, status = nil,
  statusReadAt = -1, landSeq = nil, landAt = nil, now = 0, frames = {},
  tiles = {}, far = {}, horizon = {}, queue = {}, nav = {}, navPending = {}, poseAt = -1, collisionDirty = false, collisionAt = -1, knownMats = {},
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
  if box and not S.centredOnPackage and NATIVE then
    -- every installed pack in view; the picker fits the box once it knows its map size
    S.centredOnPackage, S.fitAll = true, true
  elseif box and not S.centredOnPackage then
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

-- One Forest for the whole level. BeamNG gives every Forest global bounds, so a
-- Forest per tile is never culled: each one runs its own cell walk and render
-- submission on the main thread every frame, and the GPU waits. That is a full
-- frame at ~20 FPS with the CPU near one core and the GPU near a third busy.
-- Collidable stays off: deleting a Forest whose items sit in static collision
-- hangs the game, and a colliding population makes every later add cost the
-- engine tick. The object is never deleted; the level unload drops it.
local plantData, sharedForestId = {}, nil
local FOREST_ADDS, FOREST_REMOVES = 80, 80
-- rebuildForestManagerInstanceData reuploads every crown. Once a frame it
-- stalls the GPU and stops the tile queue. Twice a second is enough for crowns
-- to show up, and the upload stays off the mesh frames in between.
local FOREST_REBUILD_S = 0.4

local function shapeKey(shape)
  local h = 17
  for i = 1, #shape do h = (h * 131 + shape:byte(i)) % 2147483647 end
  return h
end

-- Trees and bushes only. Grass and rocks stay TSStatic: a Forest item with an
-- alpha tuft was not drawing, so open slopes stayed a flat sheet.
local function vegetationShape(shape)
  return type(shape) == 'string' and not shape:find('3d_grass', 1, true) and not shape:find('/rocks/', 1, true)
    and (shape:find('/trees/', 1, true) or shape:find('trees_library', 1, true) or shape:find('scots_pine', 1, true))
end

-- BeamNG 0.39 registers the datablock as ForestItemData. TSForestItemData does
-- not exist: createObject logged "unknown class" once per tree and every crown
-- fell back to a TSStatic, which is why the canopy popped while the camera sat still.
local function newForestItemData()
  if worldEditorCppApi and worldEditorCppApi.createObject then
    local ok, created = pcall(worldEditorCppApi.createObject, 'ForestItemData')
    if ok and created then return created end
  end
  local ok, created = pcall(createObject, 'ForestItemData')
  if ok and created then return created end
end

local function setPlantField(obj, field, value)
  if pcall(function() obj:setField(field, '', value) end) then return end
  obj:setField(field, 0, value)
end

local function plantDataFor(shape)
  if S.forestOff then return nil end
  local known = plantData[shape]
  if known ~= nil then return known or nil end
  local name = 'e2rPlant_' .. tostring(shapeKey(shape))
  local d = scenetree.findObject(name)
  if not d then
    local created = newForestItemData()
    if not created then
      if not S.forestOff then log('W', logTag, 'ForestItemData unavailable, vegetation stays TSStatic') end
      S.forestOff = true
      result.forest = 'static'
      return nil
    end
    setPlantField(created, 'shapeFile', shape)
    setPlantField(created, 'collidable', '0')
    setPlantField(created, 'windScale', '0.35')
    setPlantField(created, 'branchAmp', '0.08')
    setPlantField(created, 'detailAmp', '0.15')
    local registered = pcall(function() created:registerObject('') end)
      or pcall(function() created:registerObject(name) end)
    if not registered then
      plantData[shape] = false
      return nil
    end
    pcall(function() created:setName(name) end)
    pcall(function() created:setInternalName(name) end)
    pcall(function()
      local group = scenetree.findObject('dataBlockGroup')
      if group then group:addObject(created) end
    end)
    pcall(function()
      if Sim and Sim.getDataBlockSet then Sim.getDataBlockSet():addObject(created) end
    end)
    d = created
  end
  plantData[shape] = d
  return d
end

local function forestFor(tile)
  if S.forestOff then return nil end
  local f = sharedForestId and scenetree.findObjectById(sharedForestId)
  if not f then
    sharedForestId = nil
    f = scenetree.findObject('e2rForest')
    if f then sharedForestId = f:getID() end
  end
  if not f then
    local ok, created = pcall(createObject, 'Forest')
    if not ok or not created then
      if not S.forestOff then log('W', logTag, 'Forest unavailable, vegetation stays TSStatic') end
      S.forestOff = true
      result.forest = 'off'
      return nil
    end
    created.canSave = false
    -- Reflections redraw the forest. A high scalar drops that pass to billboards.
    pcall(function() created.lodReflectScalar = 8 end)
    if not pcall(function() created:registerObject('e2rForest') end) then
      S.forestOff = true
      result.forest = 'off'
      return nil
    end
    if scenetree.MissionGroup then scenetree.MissionGroup:addObject(created) end
    f = created
    sharedForestId = f:getID()
  end
  if tile then tile.forestId = sharedForestId end
  -- 'on' means a crown was accepted. The container alone used to report 'on'
  -- while every item fell back to a TSStatic.
  if result.forest ~= 'on' and result.forest ~= 'static' then result.forest = 'object' end
  return f
end

local function yawOf(rot)
  return 2 * math.atan2(rot and rot[3] or 0, rot and rot[4] or 1)
end

local function spawnForestItem(it, tile)
  if S.forestOff or not tile then return false end
  local f = forestFor(tile)
  if not f then return false end
  local data = f:getData()
  local kind = data and plantDataFor(it.shape)
  if not data or not kind or not MatrixF then return false end
  local m = MatrixF(true)
  m:setFromEuler(vec3(0, 0, yawOf(it.rot)))
  m:setPosition(vec3(it.pos[1], it.pos[2], it.pos[3]))
  local ok, item = pcall(function() return data:createNewItem(kind, m, it.scale or 1) end)
  if not ok or not item then
    S.forestFails = (S.forestFails or 0) + 1
    if S.forestFails >= 3 then
      S.forestOff = true
      result.forest = 'off'
      log('W', logTag, 'Forest item create failed, vegetation stays TSStatic')
    end
    return false
  end
  tile.forestItems = tile.forestItems or {}
  tile.forestItems[#tile.forestItems + 1] = item
  S.forestBurst = (S.forestBurst or 0) + 1
  S.forestDirty = true
  result.forest = 'on'
  result.forestItems = (result.forestItems or 0) + 1
  return true
end

local function retireForest(tile)
  if not tile or not tile.forestId then return end
  S.forestRetire = S.forestRetire or {}
  S.forestRetire[#S.forestRetire + 1] = {id = tile.forestId, items = tile.forestItems or {}}
  tile.forestId, tile.forestItems = nil, nil
end

local function digForests()
  local jobs = S.forestRetire
  if not jobs or #jobs == 0 then return end
  local left = FOREST_REMOVES
  while left > 0 and #jobs > 0 do
    local job = jobs[1]
    local f = job.id and scenetree.findObjectById(job.id)
    local data = f and f.getData and f:getData()
    while left > 0 and job.items and #job.items > 0 do
      local item = table.remove(job.items)
      if data then
        if pcall(function() data:removeItem(item) end) then S.forestDirty = true end
      end
      left = left - 1
    end
    if not job.items or #job.items == 0 then
      table.remove(jobs, 1)
    else
      break
    end
  end
end

local function resetForests()
  sharedForestId, plantData = nil, {}
  S.forestRetire, S.forestOff, S.forestFails, S.forestBurst, S.forestDirty = nil, nil, nil, nil, nil
  S.forestRebuildAt = nil
end

local function refreshForest()
  if not S.forestDirty then return end
  if (S.now or 0) - (S.forestRebuildAt or -10) < FOREST_REBUILD_S then return end
  S.forestDirty = nil
  S.forestRebuildAt = S.now
  if not (worldEditorCppApi and worldEditorCppApi.rebuildForestManagerInstanceData) then return end
  local timer = hptimer()
  pcall(worldEditorCppApi.rebuildForestManagerInstanceData)
  local ms = timer:stop()
  result.forestRebuildMs = ms
  if ms > (result.forestRebuildMaxMs or 0) then result.forestRebuildMaxMs = ms end
  result.forestRebuilds = (result.forestRebuilds or 0) + 1
end

local function spawnItem(it, group, pause, tile)
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
    if tile and vegetationShape(it.shape) and spawnForestItem(it, tile) then return end
    local obj = createObject('TSStatic')
    local itemName = it.name or ''
    if vegetationShape(it.shape) then result.staticTrees = (result.staticTrees or 0) + 1
    elseif itemName:find('grass', 1, true) then result.grassSpawned = (result.grassSpawned or 0) + 1
    elseif itemName:find('rock', 1, true) then result.rockSpawned = (result.rockSpawned or 0) + 1 end
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
-- Free flight used to keep the near bubble on the parked car, so the lens sat
-- in the far ring: fences and trees popped at the nose. Past this gap the
-- bubble follows the camera and look-ahead stays off.
local CAM_HANDOFF_M = 100
-- Cockpit and hood stay on the car. Every other camera past the gap owns the
-- bubble, so a free-flight name other than "free" or "orbit" still leaves the far ring.
local DRIVE_CAM = {driver = true, ['onboard.driver'] = true, cockpit = true, hood = true,
  driver_in = true, ['driver.driver'] = true, passenger = true}

local function focus()
  if S.replayPose then return S.replayPose.p, S.replayPose.v end
  if S.state == 'driving' then
    local veh = getPlayerVehicle(0)
    if veh then
      local p, v = veh:getPosition(), veh:getVelocity()
      local okName, name = pcall(function() return core_camera.getActiveCamName(0) end)
      local okPos, cam = pcall(function() return core_camera.getPosition() end)
      local dist
      if okPos and cam and p then
        local dx, dy, dz = cam.x - p.x, cam.y - p.y, cam.z - p.z
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
      end
      S.camName = okName and name or nil
      S.camDist = dist
      if okPos and cam and dist and dist > CAM_HANDOFF_M and not (okName and DRIVE_CAM[name]) then
        return vec3(cam.x, cam.y, cam.z), vec3(0, 0, 0)
      end
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
local TRAFFIC_AMOUNTS = {{0, 'Вимк', 'Off'}, {4, 'Мало', 'Light'}, {8, 'Середньо', 'Medium'},
  {14, 'Багато', 'Heavy'}}

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
        -- never a self-link or a zero-length edge: the game's random route walk (graphpath
        -- getRandomPathG, traffic respawns) adds edge lengths and froze on a piece of graph without
        -- any. An inner knot on top of the last one is skipped; an end node always stays.
        local pp = prev and nodes[prev].pos
        local same = pp and (prev == id or (pp.x - s[o]) ^ 2 + (pp.y - s[o + 1]) ^ 2 < 1e-4)
        if same and k < n then goto knot end
        node(id, s[o], s[o + 1], s[o + 2], radius)
        if prev and not same then
          local data = {drivability = drive, oneWay = oneWay, lanes = lanes, speedLimit = speed,
                        inNode = prev, outNode = id, inPos = nodes[prev].pos, outPos = nodes[id].pos,
                        inRadius = nodes[prev].radius, outRadius = nodes[id].radius}
          nodes[prev].links[id] = data
          nodes[id].links[prev] = data
        end
        prev = id
        ::knot::
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
  if not options.vehGroup then S.trafficSource = 'game cars' end
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
    if im.Button(L(a[2], a[3]) .. '##traffic_' .. a[1], im.ImVec2(width, 0)) and not active then setTraffic(a[1]) end
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
  -- the descent needs every tile within 500 m of the spawn (tick): a corner tile's centre is up to
  -- ~1060 m away, beyond the 1 km radius, and was never loaded (native-hops-02, landing hung)
  if S.state ~= 'driving' then loadR = math.max(loadR, 500 * math.sqrt(2) + S.stream.tile * 0.71) end
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
      retireForest(tile)
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
    -- Companion far tiles are decoration and stay gated above 90 km/h (their churn at
    -- 300 km/h doubled be:reloadCollision). Native far ground is the mountain ring, so it
    -- keeps loading; only its buildings and trees wait for a slower speed.
    local farLoadR = (not NATIVE and speed > (st.farFast or 25)) and 0 or farR
    for key, far in pairs(S.far) do
      local cx, cy = tileCentre(far.i, far.j)
      local d = math.sqrt((cx - p.x) ^ 2 + (cy - p.y) ^ 2)
      local near = S.tiles[key]
      -- The far canopy used to stay up until the near scene finished, so the same
      -- trees were drawn twice and z-fought for the whole spawn. Drop the far
      -- forest as soon as the near scene starts; its ground mesh stays until the
      -- near ground has replaced it.
      if near and near.sceneStarted then retireForest(far) end
      if d > farR + 500 or (near and near.sceneComplete) then
        retireForest(far)
        buryGroup('E2RF_' .. key)
        far.dropped = true
        S.far[key] = nil
        if NATIVE then native.unload(key, 'far') end
      end
    end
    for key, token in pairs(st.far or {}) do
      local spawned = S.far[key]
      if spawned and type(token) == 'number' and spawned.serial ~= token then
        retireForest(spawned)
        buryGroup('E2RF_' .. key)
        spawned.dropped = true
        S.far[key] = nil
      end
    end
    if #S.queue < 60 then
      local bestF, bestFD
      for key in pairs(st.far or {}) do
        local near = S.tiles[key]
        if not S.far[key] and not (near and (near.sceneStarted or near.sceneComplete)) then
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
          local far = {i = i, j = j, left = #manifest.items, group = tileGroup('E2RF_' .. key, true), far = true,
            serial = type(st.far[key]) == 'number' and st.far[key] or 1}
          S.far[key] = far
          for _, it in ipairs(manifest.items) do table.insert(S.queue, {tile = far, key = key, item = it, far = true}) end
        end
      end
    end
    -- DEM horizon past the far ring. Its own group stays out of MissionGroup, so it never
    -- joins the static collision. A coarser ring is not scheduled until the 5 km one is checked.
    local horizonR = (NATIVE and st.horizonDem) and (st.horizonRadius or 0) or 0
    S.horizon = S.horizon or {}
    if horizonR > 0 then
      for key, spawned in pairs(S.horizon) do
        local info = st.horizon and st.horizon[key]
        local cx = info and info.x or spawned.x or p.x
        local cy = info and info.y or spawned.y or p.y
        if not info or math.sqrt((cx - p.x) ^ 2 + (cy - p.y) ^ 2) > horizonR + 800 then
          buryGroup(spawned.groupName or ('E2RH_' .. key:gsub('[^%w_]', '_')))
          spawned.dropped = true
          S.horizon[key] = nil
          if NATIVE then native.unload(key, 'horizon') end
        end
      end
      if #S.queue < 40 then
        local bestH, bestHD
        for key, info in pairs(st.horizon or {}) do
          if not S.horizon[key] and type(info) == 'table' and info.x and info.y then
            local d = math.sqrt((info.x - p.x) ^ 2 + (info.y - p.y) ^ 2)
            if d <= horizonR + 250 and (not bestHD or d < bestHD) then bestH, bestHD = key, d end
          end
        end
        if bestH then
          local info = st.horizon[bestH]
          local manifest = native.manifest(bestH, 'horizon')
          if type(manifest) == 'table' and manifest.source == S.stream.region then
            loadNewMaterials(manifest.materials)
            local groupName = 'E2RH_' .. bestH:gsub('[^%w_]', '_')
            local horizon = {left = #manifest.items, group = tileGroup(groupName, true), horizon = true,
              serial = info.serial, x = info.x, y = info.y, groupName = groupName}
            S.horizon[bestH] = horizon
            if horizon.left == 0 then native.release(bestH, 'horizon')
            else
              for _, it in ipairs(manifest.items) do
                table.insert(S.queue, {tile = horizon, key = bestH, item = it, horizon = true})
              end
            end
          end
        end
      end
    end
    local horizonCount = 0
    for _ in pairs(S.horizon) do horizonCount = horizonCount + 1 end
    result.horizonResident = horizonCount
    -- Reprioritize pending work when direction changes. Ground meshes of every
    -- tile come before any plant: a nearer tile's crowns used to sit ahead of
    -- the next tile's road and the bubble painted at the forest cap.
    -- Among meshes, roads and scenes go by the car's arrival (wanted, s), a
    -- scene SCENE_LAG s after its road: the buildings by the car come before
    -- the roads of a tile a kilometre ahead.
    local wanted = st.wanted or {}
    local function rank(job) return job.horizon and 2 or (job.far and 1 or 0) end
    local function due(job) return (wanted[job.key] or 1e6) + (job.scene and SCENE_LAG or 0) end
    -- Ground, then grass and rocks, then trees. A tile of 1800 crowns used to
    -- fill the frame budget before a single tuft or stone was spawned.
    local function decorRank(job)
      local it = job.item
      if it and it.kind == 'proc' then return 0 end
      local name = it and it.name or ''
      if name:find('grass', 1, true) or name:find('rock', 1, true) then return 1 end
      return 2
    end
    table.sort(S.queue, function(a, b)
      local dra, drb = decorRank(a), decorRank(b)
      local pa, pb = dra == 0, drb == 0
      if pa ~= pb then return pa end
      local ra, rb = rank(a), rank(b)
      if ra ~= rb then return ra < rb end
      local da, db = due(a), due(b)
      if da ~= db then return da < db end
      if dra ~= drb then return dra < drb end
      return (a.scene and 1 or 0) < (b.scene and 1 or 0)
    end)
  end
end

local function stepQueue()
  local frame = hptimer()
  S.forestBurst = 0
  -- Retires queued by updateTiles this frame start before new crowns are added.
  digForests()
  local function over() return frame:stop() >= BUDGET_MS end
  local function pause() if over() then coroutine.yield() end end
  if S.co and S.job and S.job.tile.dropped then S.co, S.job = nil, nil end
  repeat
    if not S.co then
      local job = table.remove(S.queue, 1)
      while job and job.tile.dropped do job = table.remove(S.queue, 1) end
      if not job then break end
      S.job = job
      local group = ((job.far or job.horizon) and job.tile.group) or (job.item.decor and job.tile.decor) or job.tile.group
      S.co = coroutine.create(function(p) spawnItem(job.item, group, p, job.tile) end)
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
      if not S.job.far and not S.job.horizon then S.job.tile.failed = true end
    end
    -- createMesh has copied the source arrays into the engine. Retaining every
    -- vertex table in Lua made later garbage collections pause driving frames.
    if NATIVE and ok and coroutine.status(S.co) == 'dead' then S.job.item.meshes = nil end
    if coroutine.status(S.co) == 'dead' and (S.job.far or S.job.horizon) then
      S.co = nil
      S.job.tile.left = S.job.tile.left - 1
      if NATIVE and S.job.tile.left == 0 then native.release(S.job.key, S.job.horizon and 'horizon' or 'far') end
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
  until over() or (S.forestBurst or 0) >= FOREST_ADDS
  refreshForest()
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
  -- the descent waits for collision on every tile within 500 m of the spawn (tick): a corner
  -- tile's centre is up to ~1060 m away, and when such a tile came in last nothing else asked for
  -- a rebuild and the landing waited forever (native-perf-10)
  if S.state ~= 'driving' then radius = math.max(radius, 500 * math.sqrt(2) + S.stream.tile * 0.71) end
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
local PACK_LABELS_EN =
  {real = 'Realistic', toon = 'Cartoon', model = 'Mock-up', synth = 'Synthwave', pixel = 'Retro City',
   night = 'Night', rain = 'Rain', cyber = 'Cyberpunk', winter = 'Winter', autumn = 'Autumn'}
local PACK_LABELS_UK =
  {real = 'Реальний', toon = 'Мультяшний', model = 'Макет', synth = 'Синтвейв', pixel = 'Ретро-місто',
   night = 'Ніч', rain = 'Дощ', cyber = 'Кіберпанк', winter = 'Зима', autumn = 'Осінь'}
-- {id, packs.json label or nil}; the shown name is packLabel(), in the current language
local PACK_NAMES = {}
for _, name in ipairs({'real', 'toon', 'model', 'synth', 'pixel', 'night', 'rain', 'cyber', 'winter', 'autumn'}) do
  table.insert(PACK_NAMES, {name})
end
local function packLabel(p)
  return (lang == 'en' and PACK_LABELS_EN or PACK_LABELS_UK)[p[1]] or p[2] or p[1]
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

local function restoreSkyDriver()
  local c = core_celestial
  if S.skyDriverOwned and c and c.getDriver and c.setDriver then
    -- Do not remove a driver installed by somebody else after ours.
    if c.getDriver() == S.skyDriver then c.setDriver(S.skyDriverOrig) end
  end
  S.skyDriverOwned, S.skyDriver, S.skyDriverOrig = nil, nil, nil
end

local function applySkyDriver(env)
  local c = core_celestial
  if not (c and c.getDriver and c.setDriver) then return end -- older BeamNG
  if not env.authoredSun then restoreSkyDriver(); return end
  if not S.skyDriverOwned then
    S.skyDriverOrig, S.skyDriverOwned = c.getDriver(), true
  end
  local sun = env.objects.sunsky
  local az, el = tonumber(sun.azimuth), tonumber(sun.elevation)
  local previous = S.skyDriverOrig
  S.skyDriver = function(ctx)
    if previous then previous(ctx) end
    ctx.sunAz, ctx.sunEl = az, el
  end
  c.setDriver(S.skyDriver)
end

local function applyExposure(env)
  local lx = scenetree.findObject('PostEffectLocalExposureObject')
  if not lx then
    S.exposurePending = env
    S.exposureRetryAt = S.now + 0.5
    return
  end
  S.exposurePending = nil
  if S.exposureOrig == nil then S.exposureOrig = {lx.autoExposure, lx.manualEV} end
  if env.exposure ~= nil and env.exposure ~= false then
    lx.autoExposure, lx.manualEV = false, env.exposure
  else
    lx.autoExposure, lx.manualEV = S.exposureOrig[1], S.exposureOrig[2]
  end
  result.exposure = {tostring(S.exposureOrig[1]), S.exposureOrig[2], tostring(lx.autoExposure), lx.manualEV}
end

local function applyEnvObjects(env)
  -- Capture the original exposure before sky postApply can change its state.
  applyExposure(env)
  S.envOrig = S.envOrig or {}
  for name, fields in pairs(S.envOrig) do
    if not (env.objects and env.objects[name]) then
      local obj = scenetree.findObject(name)
      if obj then
        for field, value in pairs(fields) do obj:setField(field, 0, value) end
        pcall(function() obj:postApply() end)
      end
    end
  end
  for objName, fields in pairs(env.objects or {}) do
    local obj = scenetree.findObject(objName)
    if obj then
      local orig = S.envOrig[objName] or {}
      S.envOrig[objName] = orig
      for field in pairs(fields) do
        if orig[field] == nil then orig[field] = obj:getField(field, 0) end
      end
      -- A partial preset (e.g. synth) must not inherit clouds or night fields.
      for field, value in pairs(orig) do
        if fields[field] == nil then obj:setField(field, 0, value) end
      end
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
  applyExposure(env)
  if test then
    result.environmentSamples = result.environmentSamples or {}
    local road = scenetree.findObject('e2rf_road')
    local sky = scenetree.findObject('sunsky')
    table.insert(result.environmentSamples, {at = S.now, pack = S.pack,
      exposure = result.exposure, elevation = sky and sky:getField('elevation', 0),
      roadEmission = road and road:getField('emissiveFactor', 0)})
  end
end

local function envAgainStep()
  if S.exposurePending and S.now >= S.exposureRetryAt then applyExposure(S.exposurePending) end
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
  S.pack = name
  S.envAgain, S.exposurePending = nil, nil
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
  -- A second click can arrive before the first queue drains. Even a material
  -- whose final fields already match still needs its pending GPU reload.
  local pending, queued = S.reloadQueue or {}, {}
  S.reloadQueue = {}
  for _, mat in ipairs(pending) do
    if not queued[mat] then table.insert(S.reloadQueue, mat); queued[mat] = true end
  end
  for _, mat in pairs(dirty) do
    if not queued[mat] then table.insert(S.reloadQueue, mat); queued[mat] = true end
  end
  result.packReloads = #S.reloadQueue
  S.packSet = pack
  local tMats = timer:stop()
  -- sky, fog and the sun of the pack
  local env = S.packEnv and S.packEnv[name]
  if env then
    applySkyDriver(env)
    applyEnvObjects(env)
    -- again a little later: the sky's lighting is captured some frame after a change, and a
    -- capture during the switch left the same night lit like dusk in some runs (vis-packs-11, -13)
    S.envAgain = {env = env, at = {S.now + 0.7, S.now + 2.0, S.now + 4.0}}
    showSun(env.sun ~= false and env.sun or nil)
    showLights(env.lights ~= false and env.lights or nil)
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
  for key, tile in pairs(S.tiles) do
    retireForest(tile)
    dropGroup('E2RT_' .. key); dropGroup('E2RD_' .. key); tile.dropped = true
  end
  for key, far in pairs(S.far) do
    retireForest(far)
    dropGroup('E2RF_' .. key); far.dropped = true
  end
  for key, horizon in pairs(S.horizon or {}) do
    dropGroup(horizon.groupName or ('E2RH_' .. key:gsub('[^%w_]', '_')))
    horizon.dropped = true
  end
  S.tiles, S.far, S.horizon, S.queue, S.co = {}, {}, {}, {}, nil
  -- traffic of the old place has no roads here
  if trafficRunning() then gameplay_traffic.deleteVehicles() end
  S.nav, S.navPending, S.navDirty, S.trafficWanted = {}, {}, false, nil
  S.stream = {region = job.region, country = job.country, tile = job.tileSize, generator = job.generator}
  -- the level's TimeOfDay at the landing: the sun of the place (Kyiv's at Los Angeles was hours off)
  if job.lat and job.lon then pcall(function() core_environment.setTimeOfDay({latitude = job.lat, longitude = job.lon}) end) end
  S.landing = manifest
  S.knownMats = {}
  S.lamps = {}
  -- Stock conifers are level assets. Load their definitions from the user's
  -- installation; no game geometry or material file is distributed with EarthNG.
  -- Stock shapes stay in the player's install. Load their materials or the mesh is orange.
  local stockMaterials = {'/levels/italy/art/shapes/trees/trees_italy/main.materials.json',
    '/levels/east_coast_usa/art/shapes/trees/trees_oak/main.materials.json',
    '/levels/east_coast_usa/art/shapes/trees/trees_beech/main.materials.json',
    '/levels/east_coast_usa/art/shapes/trees/trees_aspen/main.materials.json',
    '/levels/east_coast_usa/art/shapes/groundcover/3d_grass_test/main.materials.json',
    '/levels/east_coast_usa/art/shapes/rocks/main.materials.json'}
  for _, path in ipairs(stockMaterials) do
    if FS:fileExists(path) then loadJsonMaterialsFile(path) end
  end
  loadNewMaterials(manifest.materials)
  if manifest.spawn and job.horizonZ then pcall(makeHorizon, job.horizonZ, manifest.spawn.pos[1], manifest.spawn.pos[2]) end
  if manifest.packs and FS:fileExists(manifest.packs) then
    local okP, packs = pcall(jsonReadFile, manifest.packs)
    S.packs = okP and packs and packs.fields or nil
    S.packEnv = okP and packs and packs.environment or nil
    if okP and packs and packs.order then
      PACK_NAMES = {}
      for _, name in ipairs(packs.order) do
        table.insert(PACK_NAMES, {name, packs.labels and packs.labels[name]})
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
  -- Render each shape during loading so its materials/shaders are uploaded as well as
  -- its mesh. Keep the instances underground afterwards to retain the loaded resources.
  local shapes = tileGroup('E2RWorldShapes')
  for k, shape in ipairs(manifest.warmShapes or {}) do
    local obj = createObject('TSStatic')
    obj:setField('shapeName', 0, shape)
    obj:setField('collisionType', 0, 'None')
    obj:setPosRot(sp[1] - CAM_HIGH * 0.35 + 20 + (k - 4) * 8,
                  sp[2] - CAM_HIGH * 0.55 + 35, sp[3] + CAM_HIGH - 65, 0, 0, 0, 1)
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
  local timer = hptimer()
  local veh = getPlayerVehicle(0)
  local s = S.landing.spawn
  if veh then
    spawn.safeTeleport(veh, vec3(s.pos[1], s.pos[2], s.pos[3] + 0.5), quat(0, 0, math.sin(s.yaw / 2), math.cos(s.yaw / 2)))
  end
  result.teleportMs = timer:stopAndReset()
  commands.setGameCamera()
  result.cameraSwitchMs = timer:stop()
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

-- A tile's navigation lanes in lat/lon with their bounds ({south, west, north, east}), once per
-- manifest: the minimap culls them by the view instead of projecting every point every frame.
local navLineCache = setmetatable({}, {__mode = 'k'})
local function navLines(manifest)
  local hit = navLineCache[manifest]
  if hit then return hit end
  local lines = {}
  if not (S.status and S.status.stream and S.status.stream.geo) then return lines end
  for _, segment in ipairs(manifest.navSegments or {}) do
    local geo, s, w, n, e = {}, math.huge, math.huge, -math.huge, -math.huge
    for k = 8, #segment, 3 do
      local lat, lon = worldToGeo(segment[k], segment[k + 1])
      geo[#geo + 1], geo[#geo + 2] = lat, lon
      s, w, n, e = math.min(s, lat), math.min(w, lon), math.max(n, lat), math.max(e, lon)
    end
    if #geo >= 4 then lines[#lines + 1] = {id = segment[1], geo = geo, box = {s, w, n, e}} end
  end
  navLineCache[manifest] = lines
  return lines
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

-- The landing zones as non-overlapping rectangles. Pack coverage boxes are the geographic bounds
-- of projected cells, so neighbours overlap and a translucent fill drew darker stripes where they
-- did. Edges snap to a 0.005 degree grid, each band between edges gets the union of its boxes,
-- and equal neighbouring bands join. Done once per status (the boxes table changes on refresh).
local SNAP = 0.005
local mergedFrom, merged = nil, {}
local function mergedCoverage(boxes)
  if boxes == mergedFrom then return merged end
  mergedFrom, merged = boxes, {}
  if not boxes or #boxes == 0 then return merged end
  -- outwards: neighbouring cells of a tilted grid leave hairline gaps between their boxes
  local bands, keys = {}, {}
  for _, c in ipairs(boxes) do
    local w, s = math.floor(c[1] / SNAP), math.floor(c[2] / SNAP)
    local e, n = math.ceil(c[3] / SNAP), math.ceil(c[4] / SNAP)
    if e > w and n > s then
      for y = s, n - 1 do
        local band = bands[y]
        if not band then band = {}; bands[y] = band; keys[#keys + 1] = y end
        band[#band + 1] = {w, e}
      end
    end
  end
  table.sort(keys)
  local open = {}   -- intervals of the band above, 'w:e' -> its rectangle
  for _, y in ipairs(keys) do
    local band = bands[y]
    table.sort(band, function(a, b) return a[1] < b[1] end)
    local runs, cur = {}, nil
    for _, iv in ipairs(band) do
      if cur and iv[1] <= cur[2] then cur[2] = math.max(cur[2], iv[2])
      else cur = {iv[1], iv[2]}; runs[#runs + 1] = cur end
    end
    local now = {}
    for _, r in ipairs(runs) do
      local key = r[1] .. ':' .. r[2]
      local rect = open[key]
      if rect and rect[4] == y * SNAP then rect[4] = (y + 1) * SNAP
      else
        rect = {r[1] * SNAP, y * SNAP, r[2] * SNAP, (y + 1) * SNAP}
        merged[#merged + 1] = rect
      end
      now[key] = rect
    end
    open = now
  end
  return merged
end

-- Per country: its zone's bounds and the PRESETS inside it, once per status (native.at per
-- preset and frame cost ~120 lookups a frame).
local packsFrom, packs = nil, {}
local function packInfo()
  local status = S.status
  if status == packsFrom and status and status.countries == packs.countries then return packs end
  packsFrom, packs = status, {countries = status and status.countries, byId = {}}
  for _, region in ipairs(status and status.regions or {}) do
    local p = packs.byId[region.country] or {regions = 0, presets = {}}
    packs.byId[region.country] = p
    p.regions = p.regions + 1
    for _, c in ipairs(region.coverage or {}) do
      p.box = p.box and {math.min(p.box[1], c[1]), math.min(p.box[2], c[2]), math.max(p.box[3], c[3]), math.max(p.box[4], c[4])}
        or {c[1], c[2], c[3], c[4]}
    end
  end
  if NATIVE then
    for _, preset in ipairs(PRESETS) do
      local country = native.at(preset[2], preset[3])
      local p = country and packs.byId[country.id]
      if p then p.presets[#p.presets + 1] = preset end
    end
  end
  return packs
end

-- the zoom that shows a {w, s, e, n} box in a width x height map
local function fitZoom(box, width, height)
  local x0, y0 = merc(box[4], box[1], 0)
  local x1, y1 = merc(box[2], box[3], 0)
  local zx = math.log(width * 0.85 / math.max(1e-9, x1 - x0)) / math.log(2)
  local zy = math.log(height * 0.85 / math.max(1e-9, y1 - y0)) / math.log(2)
  return math.max(3, math.min(13, math.floor(math.min(zx, zy))))
end

local function flyToPack(country, width, height)
  local p = packInfo().byId[country.id]
  S.selectedPack = country.id
  if p and p.box then
    S.center = {lat = (p.box[2] + p.box[4]) / 2, lon = (p.box[1] + p.box[3]) / 2}
    S.zoom = fitZoom(p.box, width or S.mapW or W, height or S.mapH or H)
  elseif country.landing then
    S.center, S.zoom = {lat = country.landing.lat, lon = country.landing.lon}, 9
  end
  if country.landing then setMarker(country.landing.lat, country.landing.lon) end
end

-- a round button drawn on the map (imgui items over the map's InvisibleButton never get the
-- hover); returns hovered, clicked
local function mapButton(dl, x, y, size, label, mouse, clicked)
  local hover = mouse.x >= x and mouse.x <= x + size and mouse.y >= y and mouse.y <= y + size
  im.ImDrawList_AddRectFilled(dl, im.ImVec2(x, y), im.ImVec2(x + size, y + size), hover and col(0.93, 0.95, 0.97) or col(1, 1, 1), 5)
  im.ImDrawList_AddRect(dl, im.ImVec2(x, y), im.ImVec2(x + size, y + size), col(0, 0, 0, 0.25), 5, 0, 1)
  local t = im.CalcTextSize(label)
  im.ImDrawList_AddText1(dl, im.ImVec2(x + (size - t.x) / 2, y + (size - t.y) / 2), col(0.2, 0.2, 0.22), label)
  return hover, hover and clicked
end

-- metres per pixel at a latitude -> a 1/2/5 x 10^k bar up to ~110 px
local function drawScale(dl, x, y, lat, z)
  local mpp = 156543.03392 * math.cos(math.rad(lat)) / 2 ^ z
  local metres = mpp * 110
  local p = 10 ^ math.floor(math.log10(metres))
  local nice = metres / p >= 5 and 5 * p or metres / p >= 2 and 2 * p or p
  local px = nice / mpp
  local label = nice >= 1000 and string.format('%g %s', nice / 1000, L('км', 'km')) or string.format('%g %s', nice, L('м', 'm'))
  im.ImDrawList_AddRectFilled(dl, im.ImVec2(x - 4, y - 18), im.ImVec2(x + px + 4, y + 4), col(1, 1, 1, 0.75), 3)
  im.ImDrawList_AddLine(dl, im.ImVec2(x, y), im.ImVec2(x + px, y), col(0.2, 0.2, 0.22), 2)
  im.ImDrawList_AddLine(dl, im.ImVec2(x, y), im.ImVec2(x, y - 5), col(0.2, 0.2, 0.22), 2)
  im.ImDrawList_AddLine(dl, im.ImVec2(x + px, y), im.ImVec2(x + px, y - 5), col(0.2, 0.2, 0.22), 2)
  im.ImDrawList_AddText1(dl, im.ImVec2(x + 2, y - 17), col(0.2, 0.2, 0.22), label)
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
  local mouse = im.GetMousePos()
  local clicked = hovered and im.IsMouseClicked(0)
  local onControl = false   -- the mouse is over something of the map's own (no pan, no marker)
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
  local north, west = unmerc(x0, y0, z)
  local south, east = unmerc(x0 + width, y0 + height, z)
  local missing = 0
  if NATIVE then
    planetMap.draw(im, dl, r0, width, height, view, merc, unmerc, col)
  else
    -- the wrapper's default tint (IM_COL32_WHITE) is not defined in the game: nil broke AddImage
    local white = col(1, 1, 1)
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
  end
  -- where a landing works: one even tint (pick only; the minimap stays clean)
  loadCoverage()
  if pick or not NATIVE then
    local green = col(0.12, 0.62, 0.28, 0.2)
    for _, c in ipairs(mergedCoverage(S.coverage)) do
      if c[3] >= west and c[1] <= east and c[4] >= south and c[2] <= north then
        -- whole pixels: neighbours share an edge exactly, no lighter seam between them
        local ax, ay = screen(c[4], c[1])
        local bx, by = screen(c[2], c[3])
        ax, ay, bx, by = math.floor(ax + 0.5), math.floor(ay + 0.5), math.floor(bx + 0.5), math.floor(by + 0.5)
        if bx > ax and by > ay then
          im.ImDrawList_AddRectFilled(dl, im.ImVec2(ax, ay), im.ImVec2(bx, by), green)
        end
      end
    end
  end
  -- streets of the installed country packs on the landing picker (no map download), OSM-like:
  -- main roads from z11 (wider views take more than MAP_BLOCKS 8 km blocks), the rest closer;
  -- while driving the minimap keeps to the loaded tiles' lanes below, every line costs a draw call
  if NATIVE and pick and z >= 11 then
    local maxRank = z >= 15 and 3 or z >= 13 and 2 or 1
    local casing, major, minor = col(0.75, 0.6, 0.35), col(0.99, 0.84, 0.55), col(1, 1, 1)
    local widthMajor = z >= 13 and 4 or 3
    for _, lines in ipairs(native.mapRoads({w = west, s = south, e = east, n = north}, 3)) do
      for pass = 1, 2 do
        for _, line in ipairs(lines) do
          local b = line.box
          if line.rank <= maxRank and (pass == 2 or line.rank == 1) and b[1] <= north and b[3] >= south and b[2] <= east and b[4] >= west then
            local g, px, py = line.geo, nil, nil
            local c, w = line.rank == 1 and major or minor, line.rank == 1 and widthMajor or (z >= 14 and 2.5 or 1.5)
            if pass == 1 then c, w = casing, widthMajor + 1.5 end
            for k = 1, #g - 1, 2 do
              local x, y = screen(g[k], g[k + 1])
              if px then im.ImDrawList_AddLine(dl, im.ImVec2(px, py), im.ImVec2(x, y), c, w) end
              px, py = x, y
            end
          end
        end
      end
    end
  end
  if NATIVE and S.stream then
    -- the loaded tiles' lanes: projected once per manifest, drawn only where the view is (all of
    -- them every frame, unculled, took 11 ms and 1 MB of garbage a frame in Stone Town)
    local seen, lane = {}, col(0.4, 0.43, 0.45)
    for _, tile in pairs(S.tiles) do
      for _, line in ipairs(navLines(tile.manifest)) do
        local b = line.box
        if not seen[line.id] and b[1] <= north and b[3] >= south and b[2] <= east and b[4] >= west then
          seen[line.id] = true
          -- Mercator pixels at this zoom, once per line and zoom; points under 4 px from the last
          -- drawn one are skipped (every ImVec2 is an engine object, i.e. garbage)
          local m = line.merc
          if line.zoom ~= z then
            m = {}
            local g = line.geo
            for k = 1, #g - 1, 2 do m[k], m[k + 1] = merc(g[k], g[k + 1], z) end
            line.merc, line.zoom = m, z
          end
          local ox, oy = r0.x - x0, r0.y - y0
          local px, py = m[1] + ox, m[2] + oy
          local last = #m - 1
          for k = 3, last, 2 do
            local x, y = m[k] + ox, m[k + 1] + oy
            if k == last or (x - px) ^ 2 + (y - py) ^ 2 >= 16 then
              im.ImDrawList_AddLine(dl, im.ImVec2(px, py), im.ImVec2(x, y), lane, 2)
              px, py = x, y
            end
          end
        end
      end
    end
  end
  -- the controls' corners and the pins keep the labels off
  if NATIVE then
    planetMap.beginLabels()
    planetMap.reserve(r1.x - 44, r0.y, r1.x, r0.y + 120)
    planetMap.reserve(r0.x, r1.y - 26, r0.x + 140, r1.y)
  end
  -- the installed packs as pins until their streets show: a click flies to the pack
  local pinHit
  if NATIVE and pick and z < 10 then
    local blue, rim = col(0.13, 0.4, 0.85), col(1, 1, 1)
    for _, country in ipairs(S.status and S.status.countries or {}) do
      local at = country.available and country.landing
      if at and at.lat <= north + 1 and at.lat >= south - 1 and at.lon >= west - 1 and at.lon <= east + 1 then
        local x, y = screen(at.lat, at.lon)
        local selected = S.selectedPack == country.id
        local c = selected and col(0.1, 0.6, 0.3) or blue
        im.ImDrawList_AddCircleFilled(dl, im.ImVec2(x, y - 12), 8, rim)
        im.ImDrawList_AddTriangleFilled(dl, im.ImVec2(x - 6, y - 9), im.ImVec2(x + 6, y - 9), im.ImVec2(x, y), rim)
        im.ImDrawList_AddCircleFilled(dl, im.ImVec2(x, y - 12), 6.5, c)
        im.ImDrawList_AddTriangleFilled(dl, im.ImVec2(x - 4.8, y - 9.5), im.ImVec2(x + 4.8, y - 9.5), im.ImVec2(x, y - 1.5), c)
        im.ImDrawList_AddCircleFilled(dl, im.ImVec2(x, y - 12), 2.5, rim)
        -- the name where it fits (packs of one country crowd together; a hover names any pin)
        local t = im.CalcTextSize(country.title)
        local lx, ly = x + 11, y - 12 - t.y / 2 - 2
        local named = planetMap.place(x - 9, y - 22, lx + t.x + 8, y + 2)
        local hover = mouse.x >= x - 9 and mouse.y >= y - 22 and mouse.y <= y + 2 and mouse.x <= (named and lx + t.x + 8 or x + 9)
        if named then
          im.ImDrawList_AddRectFilled(dl, im.ImVec2(lx, ly), im.ImVec2(lx + t.x + 8, ly + t.y + 4),
            hover and col(1, 1, 0.85) or col(1, 1, 1, 0.92), 4)
          im.ImDrawList_AddText1(dl, im.ImVec2(lx + 4, ly + 2), selected and col(0.05, 0.4, 0.2) or col(0.1, 0.2, 0.45), country.title)
        else
          planetMap.reserve(x - 9, y - 22, x + 9, y + 2)
        end
        if hover and hovered then pinHit = country end
      end
    end
  end
  -- cities and countries
  local placeHit
  if NATIVE then
    placeHit = planetMap.drawLabels(im, dl, r0, width, height, view, merc, unmerc, col, lang,
      hovered and mouse or nil, pick and 70 or 25)
    if not pick then placeHit = nil end
  end
  if S.marker then
    local mx, my = screen(S.marker.lat, S.marker.lon)
    -- a red pin on the landing point
    im.ImDrawList_AddCircleFilled(dl, im.ImVec2(mx, my - 14), 9, col(1, 1, 1))
    im.ImDrawList_AddTriangleFilled(dl, im.ImVec2(mx - 7, my - 11), im.ImVec2(mx + 7, my - 11), im.ImVec2(mx, my), col(1, 1, 1))
    im.ImDrawList_AddCircleFilled(dl, im.ImVec2(mx, my - 14), 7.5, col(0.86, 0.15, 0.12))
    im.ImDrawList_AddTriangleFilled(dl, im.ImVec2(mx - 5.6, my - 11.5), im.ImVec2(mx + 5.6, my - 11.5), im.ImVec2(mx, my - 1.5), col(0.86, 0.15, 0.12))
    im.ImDrawList_AddCircleFilled(dl, im.ImVec2(mx, my - 14), 3, col(0.5, 0.05, 0.05))
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
  -- zoom buttons and the scale
  local zoomTo
  if hovered or not view.follow then
    local bx, size = r1.x - 38, 28
    local hIn, cIn = mapButton(dl, bx, r0.y + 10, size, '+', mouse, clicked)
    local hOut, cOut = mapButton(dl, bx, r0.y + 10 + size + 4, size, '-', mouse, clicked)
    onControl = hIn or hOut
    if cIn then zoomTo = z + 1 elseif cOut then zoomTo = z - 1 end
  end
  drawScale(dl, r0.x + 10, r1.y - 8, view.center.lat, z)
  local credit = NATIVE and 'Natural Earth | (c) OpenStreetMap contributors' or '(c) OpenStreetMap'
  local tw = im.CalcTextSize(credit).x
  im.ImDrawList_AddRectFilled(dl, im.ImVec2(r1.x - tw - 8, r1.y - 18), r1, col(1, 1, 1, 0.75))
  im.ImDrawList_AddText1(dl, im.ImVec2(r1.x - tw - 4, r1.y - 17), col(0.15, 0.15, 0.15), credit)
  -- what a click here would do
  if pick and hovered and not onControl and (pinHit or placeHit) then
    local tip = pinHit and (pinHit.title .. L(' - показати пакет', ' - show the pack')) or (placeHit.name .. L(' - поставити точку', ' - set the point'))
    local t = im.CalcTextSize(tip)
    im.ImDrawList_AddRectFilled(dl, im.ImVec2(mouse.x + 14, mouse.y + 16), im.ImVec2(mouse.x + 22 + t.x, mouse.y + 20 + t.y), col(0.12, 0.13, 0.15, 0.92), 4)
    im.ImDrawList_AddText1(dl, im.ImVec2(mouse.x + 18, mouse.y + 18), col(1, 1, 1), tip)
  end
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
  -- click: the landing point (a pin: its pack; a city: the city)
  local io = im.GetIO()
  local px, py = mouse.x - r0.x, mouse.y - r0.y
  local minZoom = NATIVE and 2 or 3
  if hovered and io.MouseWheel ~= 0 and not zoomTo then zoomTo = z + (io.MouseWheel > 0 and 1 or -1) end
  if zoomTo then
    local nz = math.max(minZoom, math.min(18, zoomTo))
    -- the buttons zoom round the centre, the wheel round the cursor
    local ax, ay = px, py
    if onControl then ax, ay = width / 2, height / 2 end
    if not (view.follow and car) then
      local lat, lon = unmerc(x0 + ax, y0 + ay, z)
      local wx, wy = merc(lat, lon, nz)
      view.center = {}
      view.center.lat, view.center.lon = unmerc(wx - ax + width / 2, wy - ay + height / 2, nz)
    end
    view.zoom = nz
  end
  if clicked and not onControl then view.dragging, view.dragMoved = true, 0 end
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
      if pick and view.dragMoved < 4 and hovered then
        if pinHit then flyToPack(pinHit, width, height)
        elseif placeHit then setMarker(placeHit.lat, placeHit.lon)
        else setMarker(unmerc(x0 + px, y0 + py, z)) end
      end
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
  for _, p in ipairs(PACK_NAMES) do if p[1] == current then label = packLabel(p) end end
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
    if im.Button(packLabel(p) .. '##pack_' .. p[1], im.ImVec2(width, 0)) and not active then applyPack(p[1]) end
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
  local bands = 0
  for _ in pairs(S.horizon or {}) do bands = bands + 1 end
  result.horizonResident = bands
  im.Text(string.format(L('У грі: доріг %d (з колізією %d), сцен %d, далеких %d, горизонт %d',
    'In game: roads %d (with collision %d), scenes %d, far %d, horizon %d'), roads, collided, scenes, far, bands))
  im.Text(string.format(L('Вантажаться %d, збої %d, доставка p99 %.1f с', 'Loading %d, failed %d, delivery p99 %.1f s'), loading, failed, S.deliveryP99 or 0))
  if trafficRunning() and gameplay_traffic.getTrafficAmount then
    local nav = S.navStats or {}
    im.Text(string.format(L('Трафік (%s): авто %d, граф %d вузлів, збірка %.0f мс',
      'Traffic (%s): cars %d, graph %d nodes, build %.0f ms'), S.trafficSource == 'game cars' and L('машини гри', 'game cars') or S.trafficSource or L('гра', 'game'),
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

-- --- the look: our windows only (pushed round Begin/End, the game's own UI keeps its style) ------
local theme
local function pushTheme()
  if not theme then
    local function c(r, g, b, a) return im.ImVec4(r, g, b, a or 1) end
    theme = {colors = {
      {im.Col_WindowBg, c(0.10, 0.11, 0.13, 0.97)}, {im.Col_ChildBg, c(0.13, 0.14, 0.17, 1)},
      {im.Col_TitleBg, c(0.10, 0.18, 0.30, 1)}, {im.Col_TitleBgActive, c(0.12, 0.28, 0.50, 1)},
      {im.Col_FrameBg, c(0.19, 0.21, 0.25, 1)}, {im.Col_FrameBgHovered, c(0.24, 0.27, 0.32, 1)},
      {im.Col_Button, c(0.21, 0.24, 0.29, 1)}, {im.Col_ButtonHovered, c(0.27, 0.33, 0.41, 1)},
      {im.Col_ButtonActive, c(0.18, 0.42, 0.78, 1)}, {im.Col_Header, c(0.16, 0.38, 0.70, 0.65)},
      {im.Col_HeaderHovered, c(0.22, 0.44, 0.76, 0.55)}, {im.Col_HeaderActive, c(0.18, 0.42, 0.78, 0.9)},
      {im.Col_Separator, c(0.28, 0.30, 0.35, 1)}, {im.Col_Text, c(0.92, 0.93, 0.95, 1)},
      {im.Col_TextDisabled, c(0.50, 0.53, 0.58, 1)}},
      vars = {{im.StyleVar_WindowRounding, 8}, {im.StyleVar_ChildRounding, 6}, {im.StyleVar_FrameRounding, 5}},
      vars2 = {{im.StyleVar_WindowPadding, im.ImVec2(12, 10)}, {im.StyleVar_FramePadding, im.ImVec2(8, 5)},
        {im.StyleVar_ItemSpacing, im.ImVec2(8, 6)}},
      muted = c(0.62, 0.65, 0.70, 1), accent = c(0.45, 0.70, 1, 1), warn = c(1, 0.65, 0.3, 1),
      green = c(0.13, 0.55, 0.30, 1), greenHover = c(0.16, 0.65, 0.36, 1), greenActive = c(0.10, 0.45, 0.25, 1)}
  end
  for _, p in ipairs(theme.colors) do im.PushStyleColor2(p[1], p[2]) end
  for _, p in ipairs(theme.vars) do im.PushStyleVar1(p[1], p[2]) end
  for _, p in ipairs(theme.vars2) do im.PushStyleVar2(p[1], p[2]) end
end
local function popTheme()
  im.PopStyleColor(#theme.colors)
  im.PopStyleVar(#theme.vars + #theme.vars2)
end

-- the packs in the side list by part of the world (their landing point)
local GROUP_ORDER = {'europe', 'america', 'south', 'africa', 'asia', 'oceania', 'other'}
local GROUP_TITLE = {europe = {'Європа', 'Europe'}, america = {'Північна Америка', 'North America'},
  south = {'Південна Америка', 'South America'}, africa = {'Африка', 'Africa'}, asia = {'Азія', 'Asia'},
  oceania = {'Океанія', 'Oceania'}, other = {'Інші', 'Other'}}
local function presetName(p) return lang == 'en' and p[5] or p[1] end
local function packGroup(country)
  local l = country.landing
  if not l then return 'other' end
  if l.lon >= -30 and l.lon <= 60 and l.lat >= 35 then return 'europe' end
  if l.lon < -30 then return l.lat >= 12 and 'america' or 'south' end
  if l.lon <= 55 and l.lat < 37 then return 'africa' end
  if l.lon > 110 and l.lat < -10 then return 'oceania' end
  return 'asia'
end

local searchBuf = im.ArrayChar(64)
local foundPlaces

-- buttons that wrap to the next line when the row is full
local function flowButtons(items, onClick)
  local spacing = im.GetStyle().ItemSpacing.x
  local avail = im.GetContentRegionAvailWidth()
  local used = 0
  for k, item in ipairs(items) do
    local name = presetName(item)
    local w = im.CalcTextSize(name).x + 16
    if k > 1 and used + spacing + w <= avail then im.SameLine(); used = used + spacing + w else used = w end
    if im.Button(name .. '##preset' .. k) then onClick(item) end
  end
end

local function drawSidebar(mapW, mapH)
  im.SetNextItemWidth(-1)
  im.InputTextWithHint('##search', L('Пошук: пакет, місто...', 'Search: pack, city...'), searchBuf, 64)
  local query = planetMap and planetMap.lower(ffi.string(searchBuf):gsub('^%s+', ''):gsub('%s+$', '')) or ''
  local countries = NATIVE and S.status and S.status.countries or {}
  local info = packInfo()
  if query ~= '' then
    local any = false
    for _, country in ipairs(countries) do
      if country.available and planetMap.lower(country.title):find(query, 1, true) then
        if not any then im.TextColored(theme.muted, L('Пакети', 'Packs')); any = true end
        if im.Selectable1(country.title .. '##found_' .. country.id, S.selectedPack == country.id) then flyToPack(country, mapW, mapH) end
      end
    end
    -- the places of a query, with their pack check, once per query (native.at walks the boxes)
    if not foundPlaces or foundPlaces.query ~= query or foundPlaces.status ~= S.status or foundPlaces.lang ~= lang then
      local places = {query = query, status = S.status, lang = lang}
      for _, p in ipairs(PRESETS) do
        local hit = planetMap.lower(p[1]):find(query, 1, true) or (p[5] and planetMap.lower(p[5]):find(query, 1, true))
        if hit and (not NATIVE or native.at(p[2], p[3])) then
          places[#places + 1] = {name = presetName(p), lat = p[2], lon = p[3], zoom = 15, covered = true}
        end
      end
      for _, p in ipairs(planetMap and planetMap.search(query, lang, 25) or {}) do
        places[#places + 1] = {name = p.name, lat = p.lat, lon = p.lon, covered = not NATIVE or native.at(p.lat, p.lon) ~= nil}
      end
      foundPlaces = places
    end
    local places = foundPlaces
    if #places > 0 then
      im.Spacing()
      im.TextColored(theme.muted, L('Місця', 'Places'))
      for k, p in ipairs(places) do
        if im.Selectable1(p.name .. (p.covered and '' or L('  (немає пакета)', '  (no pack)')) .. '##place' .. k) then
          goTo(p.lat, p.lon, p.zoom or 12)
        end
      end
    elseif not any then
      im.TextColored(theme.muted, L('Нічого не знайдено', 'Nothing found'))
    end
    return
  end
  if NATIVE then
    local available = 0
    for _, country in ipairs(countries) do if country.available then available = available + 1 end end
    im.TextColored(theme.muted, string.format(L('Встановлені пакети: %d', 'Installed packs: %d'), available))
    if #countries == 0 then
      im.TextWrapped(L('Пакетів країн немає. Додайте earthng_<країна>.zip до папки mods і ввімкніть його в менеджері модів.',
        'No country packs installed. Add earthng_<country>.zip to your mods folder and enable it in the Mod Manager.'))
    end
    local groups = {}
    for _, country in ipairs(countries) do
      local g = packGroup(country)
      groups[g] = groups[g] or {}
      table.insert(groups[g], country)
    end
    for _, g in ipairs(GROUP_ORDER) do
      local list = groups[g]
      if list then
        table.sort(list, function(a, b) return a.title < b.title end)
        im.Spacing()
        im.TextColored(theme.accent, L(GROUP_TITLE[g][1], GROUP_TITLE[g][2]))
        im.Separator()
        for _, country in ipairs(list) do
          if country.available then
            local selected = S.selectedPack == country.id
            if im.Selectable1(country.title .. '##pack_' .. country.id, selected) then flyToPack(country, mapW, mapH) end
            local p = info.byId[country.id]
            if selected and p and #p.presets > 0 then
              im.Indent(10)
              flowButtons(p.presets, function(preset) goTo(preset[2], preset[3], 15) end)
              im.Unindent(10)
              im.Spacing()
            end
          else
            im.TextColored(theme.muted, country.title .. ': ' .. tostring(country.reason or L('недоступно', 'unavailable')))
          end
        end
      end
    end
  else
    local shown = {}
    for _, p in ipairs(PRESETS) do if presetInPackage(p) then shown[#shown + 1] = p end end
    im.TextColored(theme.muted, L('Місця пакета', 'Places of the package'))
    flowButtons(shown, function(preset) goTo(preset[2], preset[3], 15) end)
  end
end

-- under the map: the point, the landing button, coordinates
local function drawLandingBar()
  if S.marker then
    local country = NATIVE and native.at(S.marker.lat, S.marker.lon)
    if not NATIVE or country then
      im.PushStyleColor2(im.Col_Button, theme.green)
      im.PushStyleColor2(im.Col_ButtonHovered, theme.greenHover)
      im.PushStyleColor2(im.Col_ButtonActive, theme.greenActive)
      if im.Button(L('Висадитися тут', 'Land here'), im.ImVec2(200, 30)) then land() end
      im.PopStyleColor(3)
      im.SameLine()
      im.Text(string.format('%s%.5f, %.5f', country and (country.title .. '  ') or '', S.marker.lat, S.marker.lon))
    else
      im.TextColored(theme.warn, string.format(L('%.5f, %.5f - тут немає даних. Оберіть зелену зону або встановіть пакет.',
        '%.5f, %.5f - no data here. Pick a green area or install a pack.'), S.marker.lat, S.marker.lon))
    end
  else
    im.TextColored(theme.muted, L('Клікніть на мапі в зеленій зоні, щоб обрати точку висадки.',
      'Click inside a green area to pick the landing point.'))
  end
  im.SetNextItemWidth(220)
  im.InputTextWithHint('##coords', L('широта, довгота', 'latitude, longitude'), coordBuf, 64)
  im.SameLine()
  if im.Button(L('Перейти', 'Go')) then
    local lat, lon = ffi.string(coordBuf):match('(-?%d+%.?%d*)%s*[,; ]%s*(-?%d+%.?%d*)')
    if lat then goTo(tonumber(lat), tonumber(lon), 15) end
  end
  im.SameLine()
  im.TextColored(theme.muted, L('Колесо - масштаб, перетягування - рух, клік - точка', 'Wheel - zoom, drag - pan, click - point'))
end

local function drawProblems()
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
end

-- UA/EN beside "Hide": the whole widget and the map labels switch at once, remembered on disk
local function drawLangButton()
  im.SameLine()
  if im.Button((lang == 'en' and 'UA' or 'EN') .. '##lang') then
    setLang(lang == 'en' and 'uk' or 'en')
    S.viewDirty = true
  end
  if im.IsItemHovered() then im.SetTooltip(L('Switch to English', 'Перемкнути на українську')) end
end

local function drawPicker()
  im.SetNextWindowSize(im.ImVec2(1180, 720), im.Cond_FirstUseEver)
  im.SetNextWindowSizeConstraints(im.ImVec2(820, 500), im.ImVec2(8000, 8000))
  im.Begin(L('EarthNG - планета', 'EarthNG - Planet') .. '###planet', nil,
    im.WindowFlags_NoCollapse + im.WindowFlags_NoScrollbar + im.WindowFlags_NoScrollWithMouse)
  if im.Button(L('Приховати', 'Hide')) then S.widgetHidden = true end
  drawLangButton()
  if S.pickerOpen then
    im.SameLine()
    if im.Button(L('Повернутися до їзди', 'Back to driving')) then S.pickerOpen = false end
  end
  im.SameLine()
  im.TextColored(theme.muted, L('Оберіть пакет зліва або місце на мапі', 'Pick a pack on the left or a place on the map'))
  drawProblems()
  local avail = im.GetContentRegionAvail()
  local sideW = 290
  local barH = 2 * im.GetFrameHeightWithSpacing() + 8
  local mapW = math.max(200, avail.x - sideW - im.GetStyle().ItemSpacing.x)
  local mapH = math.max(200, avail.y - barH)
  S.mapW, S.mapH = mapW, mapH
  -- the part of the world with the most packs in view on the first opening (all of them
  -- together can span the planet)
  if S.fitAll then
    local count, best, box = {}, nil, nil
    for _, country in ipairs(S.status and S.status.countries or {}) do
      if country.available then
        local g = packGroup(country)
        count[g] = (count[g] or 0) + 1
        if not best or count[g] > count[best] then best = g end
      end
    end
    for _, country in ipairs(S.status and S.status.countries or {}) do
      local p = packInfo().byId[country.id]
      if country.available and packGroup(country) == best and p and p.box then
        box = box and {math.min(box[1], p.box[1]), math.min(box[2], p.box[2]), math.max(box[3], p.box[3]), math.max(box[4], p.box[4])}
          or {p.box[1], p.box[2], p.box[3], p.box[4]}
      end
    end
    box = box or (S.status and S.status.packageBox)
    if box then
      S.center = {lat = (box[2] + box[4]) / 2, lon = (box[1] + box[3]) / 2}
      S.zoom = fitZoom(box, mapW, mapH)
    end
    S.fitAll = nil
  end
  im.BeginChild1('##side', im.ImVec2(sideW, avail.y), true)
  drawSidebar(mapW, mapH)
  im.EndChild()
  im.SameLine()
  im.BeginGroup()
  drawMap(mapW, mapH, S, true)
  drawLandingBar()
  im.EndGroup()
  im.End()
end

-- Live density and draw distance. Defaults match the generator. Apply drops
-- resident near and far tiles so they rebuild; the horizon stays.
local TUNE_DEFAULT = {nearM = 1000, farM = 3000, maxTrees = 2400, maxFarTrees = 800,
  maxGrass = 4000, maxRocks = 80, forestSpacing = 5, groundSpacing = 6, groundTrees = true}
local tunePtr = {}

local function ensureTune()
  if S.tune then return end
  S.tune = {}
  for k, v in pairs(TUNE_DEFAULT) do S.tune[k] = v end
end

local function applyTune()
  ensureTune()
  if not NATIVE or not native.setTune then return end
  local applied = native.setTune(S.tune)
  if type(applied) == 'table' then
    S.tune = applied
    for key, ptr in pairs(tunePtr) do
      if key == 'groundTrees' then ptr[0] = applied.groundTrees and true or false
      elseif applied[key] then ptr[0] = applied[key] end
    end
  end
  local keep = {}
  for _, job in ipairs(S.queue or {}) do
    if job.horizon then keep[#keep + 1] = job else job.tile.dropped = true end
  end
  S.queue = keep
  if S.job and not S.job.horizon then S.co, S.job = nil, nil end
  for key, tile in pairs(S.tiles) do
    retireForest(tile)
    dropGroup('E2RT_' .. key)
    buryGroup('E2RD_' .. key)
    tile.dropped = true
    native.unload(key)
  end
  for key, far in pairs(S.far) do
    retireForest(far)
    buryGroup('E2RF_' .. key)
    far.dropped = true
    native.unload(key, 'far')
  end
  S.tiles, S.far = {}, {}
  S.collisionDirty = true
  result.tuneApplied = S.now
  result.forestItems, result.staticTrees, result.grassSpawned, result.rockSpawned = 0, 0, 0, 0
  S.appliedTune = {}
  for k, v in pairs(S.tune) do S.appliedTune[k] = v end
end

local function tuneSlider(key, lo, hi, uk, en)
  local tune = S.tune
  if not tunePtr[key] then tunePtr[key] = im.FloatPtr(tune[key]) end
  im.Text(L(uk, en))
  if im.SetNextItemWidth then im.SetNextItemWidth(-1) end
  local ok, changed = pcall(im.SliderFloat, '##' .. key, tunePtr[key], lo, hi, '%.0f')
  if ok then
    -- Copy even when the binding does not return true. Rebuild only on release,
    -- otherwise a drag would drop every tile on every frame.
    local value = tonumber(tunePtr[key][0])
    if value then tune[key] = math.floor(value + 0.5) end
    if im.IsItemDeactivatedAfterEdit and im.IsItemDeactivatedAfterEdit() then S.tuneApplyNow = true end
  else
    im.SameLine()
    if im.Button('-##' .. key) then tune[key] = math.max(lo, tune[key] - math.max(1, (hi - lo) / 20)); S.tuneApplyNow = true end
    im.SameLine()
    if im.Button('+##' .. key) then tune[key] = math.min(hi, tune[key] + math.max(1, (hi - lo) / 20)); S.tuneApplyNow = true end
    im.SameLine()
    im.Text(string.format('%.0f', tune[key]))
  end
end

local function tuneDiffers()
  local base = S.appliedTune or TUNE_DEFAULT
  for key, value in pairs(S.tune) do
    if value ~= base[key] then return true end
  end
  return false
end

local function drawTune()
  ensureTune()
  local nearN, farN = 0, 0
  for _ in pairs(S.tiles) do nearN = nearN + 1 end
  for _ in pairs(S.far) do farN = farN + 1 end
  local cam = S.camName and tostring(S.camName) or '?'
  local dist = S.camDist and string.format('%.0f m', S.camDist) or '?'
  local applied = S.appliedTune or TUNE_DEFAULT
  im.Text(string.format(L('Камера: %s, до авто %s', 'Camera: %s, to the car %s'), cam, dist))
  im.Text(string.format(L('Ліс: %s, крон %d, запасних %d, перезбірка %d мс',
    'Forest: %s, crowns %d, fallback %d, rebuild %d ms'),
    tostring(result.forest or '?'), result.forestItems or 0, result.staticTrees or 0,
    math.floor(result.forestRebuildMs or 0)))
  im.Text(string.format(L('Поставлено: трава %d, каміння %d. Тайли %d/%d, черга %d',
    'Spawned: grass %d, rocks %d. Tiles %d/%d, queue %d'),
    result.grassSpawned or 0, result.rockSpawned or 0, nearN, farN, #(S.queue or {})))
  im.Text(string.format(L('У світі: ближня %d м, далека %d м, дерева %d/%d, трава %d, каміння %d',
    'In the world: near %d m, far %d m, trees %d/%d, grass %d, rocks %d'),
    applied.nearM or 0, applied.farM or 0, applied.maxTrees or 0, applied.maxFarTrees or 0,
    applied.maxGrass or 0, applied.maxRocks or 0))
  if im.Button(L('Застосувати і перебудувати', 'Apply and rebuild')) then applyTune() end
  tuneSlider('nearM', 400, 4000, 'Ближній радіус, м', 'Near radius, m')
  tuneSlider('farM', 800, 8000, 'Далекий радіус, м', 'Far radius, m')
  tuneSlider('maxTrees', 0, 2400, 'Дерева на ближній тайл', 'Trees per near tile')
  tuneSlider('maxFarTrees', 0, 1200, 'Дерева на далекий тайл', 'Trees per far tile')
  tuneSlider('forestSpacing', 4, 24, 'Крок лісу, м', 'Forest spacing, m')
  tuneSlider('groundSpacing', 4, 30, 'Крок на відкритому ґрунті, м', 'Open-ground spacing, m')
  tuneSlider('maxGrass', 0, 4800, 'Трава на ближній тайл', 'Grass per near tile')
  tuneSlider('maxRocks', 0, 200, 'Каміння на ближній тайл', 'Rocks per near tile')
  if not tunePtr.groundTrees then tunePtr.groundTrees = im.BoolPtr(S.tune.groundTrees and true or false) end
  local okBox, flipped = pcall(im.Checkbox, L('Дерева на відкритому ґрунті', 'Trees on open ground'), tunePtr.groundTrees)
  if okBox then
    S.tune.groundTrees = tunePtr.groundTrees[0] and true or false
    if flipped then S.tuneApplyNow = true end
  end
  if not okBox and im.Button(L(S.tune.groundTrees and 'Відкритий ґрунт: дерева є' or 'Відкритий ґрунт: без дерев',
      S.tune.groundTrees and 'Open ground: trees on' or 'Open ground: trees off')) then
    S.tune.groundTrees = not S.tune.groundTrees
    S.tuneApplyNow = true
  end
  if tuneDiffers() then
    im.TextColored(im.ImVec4(1, 0.7, 0.2, 1), L('Відпустіть повзунок — світ перебудується.',
      'Release the slider and the world rebuilds.'))
  end
  im.TextColored(theme.muted, L('Трава лише в ближньому радіусі. Горизонт без рослин. Далекі крони рідші, щоб тримати кадр.',
    'Grass is inside the near radius only. The horizon has no plants. Far crowns are sparser so the frame holds.'))
  if S.tuneApplyNow and tuneDiffers() then applyTune() end
  S.tuneApplyNow = nil
end

local function drawWindow()
  pushTheme()
  if S.widgetHidden then
    im.Begin('EarthNG##restore', nil, im.WindowFlags_AlwaysAutoResize)
    if im.Button(L('Показати мапу', 'Show map')) then S.widgetHidden = false; S.viewDirty = true end
    im.End()
    popTheme()
    return
  end
  if S.state == 'driving' and not S.pickerOpen then
    im.SetNextWindowSize(im.ImVec2(392, 0), im.Cond_Always)
    im.Begin('EarthNG##mini', nil, im.WindowFlags_NoCollapse + im.WindowFlags_AlwaysAutoResize)
    if im.Button(L('Приховати', 'Hide')) then S.widgetHidden = true end
    im.SameLine()
    if im.Button(L('Обрати інше місце', 'Pick another place')) then S.pickerOpen = true; S.viewDirty = true end
    drawLangButton()
    -- the mini map follows the car (an arrow along the heading); a drag frees it
    S.mini = S.mini or {center = {lat = S.center.lat, lon = S.center.lon}, zoom = 15, follow = true}
    drawMap(360, 220, S.mini)
    if not S.mini.follow then
      if im.Button(L('До авто', 'To the car')) then S.mini.follow = true end
      im.SameLine()
    end
    im.TextColored(theme.muted, L('Колесо - масштаб, перетягування - огляд', 'Wheel - zoom, drag - look around'))
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
    if im.CollapsingHeader1(L('Щільність і дистанція', 'Density and distance'), im.TreeNodeFlags_DefaultOpen) then drawTune() end
    im.End()
    popTheme()
    return
  end
  if S.state == 'pick' or S.pickerOpen then
    drawPicker()
    popTheme()
    return
  end
  -- the landing under way: a small window of its own
  im.SetNextWindowSize(im.ImVec2(W, 0), im.Cond_Always)
  im.Begin(L('EarthNG - висадка', 'EarthNG - Landing') .. '###progress', nil, im.WindowFlags_NoCollapse + im.WindowFlags_AlwaysAutoResize)
  if im.Button(L('Приховати', 'Hide')) then S.widgetHidden = true end
  drawLangButton()
  local job = S.status and S.status.job
  if job and job.seq == S.landSeq then
    im.Text(string.format(L('Генерація %.5f, %.5f', 'Generating %.5f, %.5f'), job.lat or 0, job.lon or 0))
    im.ProgressBar(job.progress or 0, im.ImVec2(W - 24, 0), tostring(job.stage or ''))
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
    im.ProgressBar(math.min(1, loaded / 4), im.ImVec2(W - 24, 0), L('Спуск: будую світ', 'Descent: building the world'))
  end
  drawProblems()
  im.End()
  popTheme()
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
      retireForest(tile)
      dropGroup('E2RT_' .. key); dropGroup('E2RD_' .. key); tile.dropped = true
    end
    for key, far in pairs(S.far) do
      retireForest(far)
      dropGroup('E2RF_' .. key); far.dropped = true
    end
    for key, band in pairs(S.horizon or {}) do
      dropGroup(band.groupName or ('E2RH_' .. key:gsub('[^%w_]', '_')))
      band.dropped = true
    end
    dropGroup('E2RWorldWarmup'); dropGroup('E2RWorldPacks'); dropGroup('E2RWorldShapes')
    local horizon = scenetree.findObject('e2r_horizon')
    if horizon then horizon:delete() end
    dropLights()
    if trafficRunning() then gameplay_traffic.deleteVehicles() end
    S.stream, S.co, S.job, S.landing = nil, nil, nil, nil
    S.tiles, S.far, S.horizon, S.queue, S.nav, S.navPending = {}, {}, {}, {}, {}, {}
    S.collisionDirty, S.state = true, 'off'
  end
  if S.state == 'generating' or S.state == 'warmup' then
    S.state, S.landSeq, S.landAt, S.landTimer = 'off', nil, nil, nil
  end
end

function M.onModManagerModsMounted() refreshCountries() end
function M.onModActivated() refreshCountries() end
function M.onModDeactivated() refreshCountries() end
local function restorePackState()
  restoreSkyDriver()
  S.envAgain, S.exposurePending, S.exposureRetryAt = nil, nil, nil
  local lx = scenetree.findObject('PostEffectLocalExposureObject')
  for name, fields in pairs(S.envOrig or {}) do
    local obj = scenetree.findObject(name)
    if obj then
      for field, value in pairs(fields) do obj:setField(field, 0, value) end
      pcall(function() obj:postApply() end)
    end
  end
  for name, fields in pairs(S.packOrig or {}) do
    local mat = scenetree.findObject(name)
    if mat then
      for field, value in pairs(fields) do mat:setField(field, 0, value) end
      mat:reload()
    end
  end
  if lx and S.exposureOrig then lx.autoExposure, lx.manualEV = S.exposureOrig[1], S.exposureOrig[2] end
  S.exposureOrig, S.envOrig, S.packOrig, S.packSet, S.reloadQueue = nil, nil, nil, nil, nil
  S.packsWarm, S.packsExtra, S.packs, S.packEnv = nil, nil, nil, nil
  showPrecip(nil)
  showSun(nil)
  dropLights()
end

function M.onExtensionUnloaded() restorePackState() end

function M.onClientEndMission()
  restorePackState()
  resetForests()
  if NATIVE then native.refresh() end
  S.stream, S.co, S.job, S.landing, S.state = nil, nil, nil, nil, 'off'
  S.tiles, S.far, S.horizon, S.queue, S.nav, S.navPending = {}, {}, {}, {}, {}, {}
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
      built = stream.built, ready = stream.readyCount, error = S.lastError,
      cachedTiles = stream.cachedTiles, cachedCells = stream.cachedCells, rebuilds = stream.rebuilds,
      slowResumes = stream.slowResumes}
    local P = S.prof
    if P and P.frames > 0 then
      local mean = {}
      for part, sum in pairs(P.sum) do mean[part] = sum / P.frames end
      local kbMean = {}
      for part, sum in pairs(P.kb) do kbMean[part] = sum / P.frames end
      result.luaProfile = {frames = P.frames, meanMs = mean, maxMs = P.max, heapKb = collectgarbage('count'), kbPerFrame = kbMean,
        garbageKbPerFrame = P.kbUp / P.frames, collections = P.collections}
    end
    saveResult()
  end
  -- picker check: optionally centre the map on a point and hold it there (a desktop capture
  -- sees the imgui map; the game's own screenshots do not contain it)
  if test.pickerOnly and S.state == 'pick' and test.pickerZoom and not S.testPicked and t > 2 then
    goTo(test.lat, test.lon, test.pickerZoom)
    S.testPicked = S.now
  end
  if test.pickerOnly and S.state == 'pick' and t > (test.pickerHold or 4) then
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
  -- teleports (test.hops: {lat, lon, stage}): land again from 'driving', or mid-descent from
  -- 'spawning'/'warmup', as a player does from the picker; each hop's descent time is reported
  if test.hops and S.testLanded then
    local done = S.hop or 0
    local last = result.hops and result.hops[done]
    if last and not last.landedAfter and S.state == 'driving' then
      last.landedAfter = S.now - last.at
      saveResult()
    end
    if done < #test.hops then
      local h = test.hops[done + 1]
      local stage = h[3] or 'driving'
      if S.state == stage and S.now - S.phaseAt > (stage == 'driving' and 3 or 1) then
        S.hop = done + 1
        result.hops = result.hops or {}
        table.insert(result.hops, {n = S.hop, lat = h[1], lon = h[2], from = S.state, at = S.now})
        S.pickerOpen = true
        goTo(h[1], h[2], 15)
        land()
        saveResult()
      end
      return
    end
    if last and not last.landedAfter then return end
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
      -- a native route is lat/lon (geo); the region's own projection puts it in the game's frame
      if ok and route and NATIVE and route.geo then
        route.points = {}
        for _, g in ipairs(route.geo) do
          local x, y = native.localPoint(g[1], g[2])
          if x then route.points[#route.points + 1] = {x, y, 0} end
        end
        local length = 0
        for k = 2, #route.points do
          local a, b = route.points[k - 1], route.points[k]
          length = length + math.sqrt((b[1] - a[1]) ^ 2 + (b[2] - a[2]) ^ 2)
        end
        route.length = length
      end
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
  -- delivered = loaded with collision; a replay crosses tiles without roads (not drivable)
  local here = S.tiles[key]
  if not (here and not here.failed and here.roadComplete and here.collided) and S.lastMiss ~= key then
    local t, st = here, S.status and S.status.stream or {}
    table.insert(result.deadlineMisses,{seconds=S.now-S.testDriving,tile=key,distance=r.distance,
      inGame=t~=nil, road=t and t.roadComplete, collided=t and t.collided, loadSeconds=t and t.startedAt and S.now-t.startedAt,
      left=t and t.left, ready=st.ready and st.ready[key]~=nil, wanted=st.wanted and st.wanted[key],
      failed=st.failed and st.failed[key], queue=#S.queue, collisionAgo=S.now-S.collisionAt, dirty=S.collisionDirty})
    S.lastMiss = key
  end
  result.replayDistanceM = r.distance
end

-- Where a frame's Lua time goes (automation report): sums and maxima per part, and the Lua heap
-- growth between frames (garbage the collector must walk later).
local function profAdd(part, ms, kb)
  local P = S.prof
  if not P then P = {sum = {}, max = {}, kb = {}, frames = 0, kbUp = 0, collections = 0}; S.prof = P end
  P.sum[part] = (P.sum[part] or 0) + ms
  if ms > (P.max[part] or 0) then P.max[part] = ms end
  if kb and kb > 0 then P.kb[part] = (P.kb[part] or 0) + kb end
end

function M.onUpdate(dtReal)
  if not inOurLevel() then
    S.state = 'off'
    return
  end
  S.now, S.dt = S.now + dtReal, dtReal
  digForests()
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
  -- A frame stuck in Lua (ours or another mod's) for seconds writes its Lua stack to
  -- e2r-hang.txt in the user folder from inside the loop (the LuaJIT profiler samples compiled
  -- code too). Freezes in play were otherwise invisible: the log just stopped.
  S.frameStamp = os.clock()
  if not S.watchdog then
    S.watchdog = true
    local okProfile, profile = pcall(require, 'jit.profile')
    if okProfile and profile then
      local dumps, lastDump = 0, -1
      profile.start('i200', function(thread)
        local now = os.clock()
        if S.frameStamp and now - S.frameStamp > 3 and now - lastDump > 2 and dumps < 30 then
          lastDump, dumps = now, dumps + 1
          local f = io.open('e2r-hang.txt', 'a')
          if f then
            f:write(string.format('stuck %.1f s: %s\n', now - S.frameStamp, profile.dumpstack(thread, 'Fpl < ', 40)))
            f:close()
          end
        end
      end)
    end
  end
  -- an error in the logic must not stop the window from being drawn (it flickered when it did)
  local ok, err = xpcall(M.tick, debug.traceback, dtReal)
  if not ok then
    if S.lastError ~= err then log('E', logTag, err) end
    S.lastError = err
  end
  local wkb, wt = collectgarbage('count'), hptimer()
  drawWindow()
  if S.prof then profAdd('window', wt:stop(), collectgarbage('count') - wkb) end
  if test then
    local ok2, err2 = xpcall(runTest, debug.traceback)
    if not ok2 and S.lastError ~= err2 then log('E', logTag, err2); S.lastError = err2 end
  end
end

function M.tick(dt)
  if NATIVE then
    local kb, t = collectgarbage('count'), hptimer()
    if S.profKb then
      local P = S.prof
      if P then
        P.frames = P.frames + 1
        if kb >= S.profKb then P.kbUp = P.kbUp + kb - S.profKb else P.collections = P.collections + 1 end
      end
    end
    native.update(dt, BUDGET_MS)
    S.nativeMs = t:stop()
    profAdd('native', S.nativeMs, collectgarbage('count') - kb)
    S.profKb = collectgarbage('count')
  end
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
    local sp = S.landing.spawn.pos
    for k in ipairs(S.landing.warmShapes or {}) do
      local obj = scenetree.findObject('e2r_shape_' .. k)
      if obj then obj:setPosRot(sp[1] + k * 3, sp[2], sp[3] - 40, 0, 0, 0, 1) end
    end
    S.state, S.frames, S.phaseAt = 'spawning', {}, S.now
  end
  if S.stream and (S.state == 'spawning' or S.state == 'driving') then
    -- which step made a slow frame (result.slow, automation and diagnosis)
    local timer = hptimer()
    writePose()
    local tPose = timer:stopAndReset()
    local kb0 = collectgarbage('count')
    updateTiles()
    local tTiles = timer:stopAndReset()
    local kb1 = collectgarbage('count')
    -- a frame that rebuilds the collision (up to ~65 ms) spawns no mesh: together with a
    -- createMesh and the render they reached 98 ms (fast-oblast-replay-01)
    local reloaded = updateCollision()
    local tColl = timer:stopAndReset()
    local kb2 = collectgarbage('count')
    if not reloaded then stepQueue(); digGraves() end
    local tQueue = timer:stopAndReset()
    local kb3 = collectgarbage('count')
    if S.state == 'driving' then navStep() end
    local tNav = timer:stopAndReset()
    if tNav > (result.navStepMs or 0) then result.navStepMs = tNav end
    profAdd('pose', tPose); profAdd('tiles', tTiles, kb1 - kb0); profAdd('collision', tColl); profAdd('queue', tQueue, kb3 - kb2)
    profAdd('nav', tNav)
    if tPose + tTiles + tQueue + tColl + (S.nativeMs or 0) > 50 then
      result.slow = result.slow or {}
      if #result.slow < 60 then
        table.insert(result.slow, {math.floor(S.now), math.floor(tPose), math.floor(tTiles), math.floor(tQueue), math.floor(tColl),
                                   S.job and S.job.item and S.job.item.name or '', math.floor(S.nativeMs or 0)})
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
      -- why the descent waits (automation report): the tiles round the spawn that hold it back
      if S.now - S.phaseAt > 60 and not result.descentBlocked then
        local blocked, st = {}, S.status and S.status.stream or {}
        for x = sp[1]-500, sp[1]+500, 250 do
          for y = sp[2]-500, sp[2]+500, 250 do
            local i,j = tileOf(x,y)
            local key = i .. '_' .. j
            local t = S.tiles[key]
            if not t or t.failed or not t.roadComplete or (#t.manifest.items > 0 and not t.collided) or not blocked[key] then
              blocked[key] = {inGame = t ~= nil, failed = t and t.failed, road = t and t.roadComplete, scene = t and t.sceneComplete,
                collided = t and t.collided, left = t and t.left, ready = st.ready and st.ready[key] ~= nil,
                wanted = st.wanted and st.wanted[key], nativeFailed = st.failed and st.failed[key]}
            end
          end
        end
        result.descentBlocked = {seconds = S.now - S.phaseAt, landing = li .. '_' .. lj, tiles = blocked, queue = #S.queue,
          collisionDirty = S.collisionDirty, collisionAt = S.collisionAt, now = S.now}
        saveResult()
      end
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
  loadLang()
  if NATIVE then native.refresh() end
  log('I', logTag, 'Earth2Road planet extension loaded')
end

return M
