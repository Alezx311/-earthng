-- Streaming spike S1/S3 (docs/BEAMNG_STREAMING.md), loaded only in an isolated user directory
-- by earthng/s1.py. The level e2r_void starts empty; tiles are written into the
-- user folder by the driver *after* the game has started, then spawned here in batches.
-- Every step is timed; the driver reads e2r-stream-result.json.
local M = {}
local cfg
local result = {status = 'waiting', batches = {}, shots = {}}
local phase, elapsed, phaseStart = 'waiting', 0, 0
local batch, group = 0, nil
local frames = {}
local frameTimer = hptimer()
local pending, spawnRec = nil, nil

local function save()
  jsonWriteFile('e2r-stream-result.json', result, true)
end

local function ms(timer)
  return timer:stopAndReset()
end

local function frameStats()
  local worst, total = 0, 0
  for _, dt in ipairs(frames) do
    worst = math.max(worst, dt)
    total = total + dt
  end
  return {frames = #frames, worstFrameMs = 1000 * worst, meanFrameMs = #frames > 0 and 1000 * total / #frames or 0}
end

local function shot(name)
  createScreenshot2({filename = 'screenshots/s1-' .. name, writeJPG = false, superSampling = 1})
  table.insert(result.shots, name)
end

local function look(pos, target)
  local p, t = vec3(pos[1], pos[2], pos[3]), vec3(target[1], target[2], target[3])
  local q = quatFromDir(t - p, vec3(0, 0, 1))
  core_camera.setPosRot(0, p.x, p.y, p.z, q.x, q.y, q.z, q.w)
end

local function enter(name)
  phase, phaseStart, frames = name, elapsed, {}
  result.phase = name
  save()
end

local ffi = require('ffi')

-- Binary piece from s1.py write_bin(): u32 subCount, then per submesh u32 nameLen (padded),
-- name, u32 vertCount, u32 indexCount, f32 pos[3v], f32 normal[3v], f32 uv[2v], u32 index[i].
-- All fields are 4-byte words indexed through typed buffers: pointer arithmetic on cdata failed
-- (runs 10-13) and ffi.cast returns its argument unchanged in the game's Lua (run 17 read floats as
-- raw uint32 bits), so the same bytes are copied into a uint32 and a float buffer.
-- Builds the createMesh tables directly; pause() may yield the coroutine between chunks of work.
local function binMeshes(path, pause)
  local f = io.open(path, 'rb')
  if not f then return nil end
  local data = f:read('*all')
  f:close()
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

-- Flat arrays from s1.py proc_mesh() -> the tables ProceduralMesh:createMesh expects.
local function expand(sub)
  local p, n, t, idx = sub.p, sub.n, sub.t, sub.i
  local verts, normals, uvs, faces = {}, {}, {}, {}
  for k = 0, #p / 3 - 1 do
    verts[k + 1] = {x = p[3 * k + 1], y = p[3 * k + 2], z = p[3 * k + 3]}
    normals[k + 1] = {x = n[3 * k + 1], y = n[3 * k + 2], z = n[3 * k + 3]}
    uvs[k + 1] = {u = t[2 * k + 1], v = t[2 * k + 2]}
  end
  for k = 1, #idx do
    local v = idx[k]
    faces[k] = {v = v, n = v, u = v}
  end
  return {verts = verts, normals = normals, uvs = uvs, faces = faces, material = sub.mat}
end

-- S2: geometry built in Lua, no COLLADA import. Runs as a coroutine (see 'spawning'); the
-- read/expand/create times are wall time and include frames spent yielded.
local function spawnProc(it, rec, pause)
  local timer = hptimer()
  local kb = collectgarbage('count')
  local meshes
  if it.shape:sub(-4) == '.bin' then
    meshes = binMeshes(it.shape, pause)
    rec.expandMs = rec.expandMs + ms(timer)
  else
    local subs = jsonReadFile(it.shape)
    local took = ms(timer)
    rec.readMs, rec.worstReadMs = rec.readMs + took, math.max(rec.worstReadMs, took)
    meshes = {}
    for k, sub in ipairs(subs) do meshes[k] = expand(sub) end
    took = ms(timer)
    rec.expandMs, rec.worstExpandMs = rec.expandMs + took, math.max(rec.worstExpandMs, took)
  end
  pause()
  timer:stopAndReset()
  local obj = createObject('ProceduralMesh')
  obj:registerObject(it.name)
  obj.canSave = false
  group:addObject(obj.obj)
  obj:createMesh({meshes})
  obj:setPosition(vec3(it.pos[1], it.pos[2], it.pos[3]))
  took = ms(timer)
  rec.createMs, rec.worstCreateMs = rec.createMs + took, math.max(rec.worstCreateMs, took)
  -- a drop in Lua heap during this object means a GC cycle ran inside it
  if collectgarbage('count') < kb then rec.gcHits = rec.gcHits + 1 end
end

local function spawnOne(it, rec, pause)
  if it.kind == 'proc' then return spawnProc(it, rec, pause) end
  local obj = createObject('TSStatic')
  obj:setField('shapeName', 0, it.shape)
  obj:setField('collisionType', 0, it.collision)
  obj:setField('decalType', 0, it.decal)
  obj.useInstanceRenderData = true
  obj:setPosRot(it.pos[1], it.pos[2], it.pos[3], it.rot[1], it.rot[2], it.rot[3], it.rot[4])
  obj:registerObject(it.name)
  group:addObject(obj)
end

local function vehicleState()
  local veh = getPlayerVehicle(0)
  if not veh then return nil end
  local p = veh:getPosition()
  return {pos = {p.x, p.y, p.z}, speed = veh:getVelocity():length()}
end

function M.onInit()
  setExtensionUnloadMode(M, 'manual')
  cfg = jsonReadFile('e2r-stream-input.json')
  result.status = 'extension_loaded'
  save()
end

function M.onUpdate(dtReal)
  elapsed = elapsed + dtReal
  -- wall time between our updates; dtReal hides a long Lua stall
  table.insert(frames, frameTimer:stopAndReset() / 1000)
  local t = elapsed - phaseStart
  if elapsed > cfg.timeout then
    result.status = 'timeout'
    save()
    shutdown(1)
    return
  end

  if phase == 'waiting' then
    if not getMissionFilename():find('e2r_void', 1, true) or worldReadyState < 2 or not getPlayerVehicle(0) then return end
    result.status = 'level_loaded'
    result.levelLoadedAt = elapsed
    result.vehicleOnPad = vehicleState()
    pcall(function() extensions.ui_visibility.setCef(false) end)
    pcall(function() local tod = scenetree.tod; if tod then tod.play = false end end)
    group = createObject('SimGroup')
    group:registerObject('E2RStreamTiles')
    scenetree.MissionGroup:addObject(group)
    enter('settle')

  elseif phase == 'settle' and t > cfg.settleSeconds then
    commands.setFreeCamera()
    look(cfg.overview.pos, cfg.overview.look)
    enter('void_shot')

  elseif phase == 'void_shot' and t > 1 then
    shot('0-void')
    result.status = 'request_tiles'
    result.requestedAt = elapsed
    enter('wait_files')

  elseif phase == 'wait_files' then
    if FS:fileExists(cfg.readyFile) then
      result.filesSeenAfter = elapsed - result.requestedAt
      local timer = hptimer()
      loadJsonMaterialsFile(cfg.materials)
      result.materialsMs = ms(timer)
      batch = 0
      enter(cfg.warmupMaterials and 'warmup' or 'next_batch')
    end

  elseif phase == 'warmup' then
    -- One small visible triangle per material in front of the overview camera, so texture upload
    -- and shader/pipeline creation happen here (during the descent in the product), not while driving.
    local timer, c = hptimer(), cfg.overview.look
    local warm = createObject('SimGroup')
    warm:registerObject('E2RWarmup')
    scenetree.MissionGroup:addObject(warm)
    for k, mat in ipairs(cfg.warmupMaterials) do
      local x, y = c[1] + (k % 12) * 3 - 18, c[2] + math.floor(k / 12) * 3 - 18
      local obj = createObject('ProceduralMesh')
      obj:registerObject('e2r_warm_' .. k)
      obj.canSave = false
      warm:addObject(obj.obj)
      obj:createMesh({{{verts = {{x = 0, y = 0, z = 0}, {x = 2, y = 0, z = 0}, {x = 0, y = 2, z = 0}},
                        normals = {{x = 0, y = 0, z = 1}}, uvs = {{u = 0, v = 0}, {u = 1, v = 0}, {u = 0, v = 1}},
                        faces = {{v = 0, n = 0, u = 0}, {v = 2, n = 0, u = 2}, {v = 1, n = 0, u = 1}},
                        material = mat}}})
      obj:setPosition(vec3(x, y, c[3] + 2))
    end
    result.warmup = {materials = #cfg.warmupMaterials, createMs = ms(timer)}
    enter('warmup_wait')

  elseif phase == 'warmup_wait' and t > cfg.warmupSeconds then
    result.warmup.frames = frameStats()
    scenetree.findObject('E2RWarmup'):deleteAllObjects()
    shot('0-warmup')
    enter('next_batch')

  elseif phase == 'next_batch' then
    batch = batch + 1
    local b = cfg.batches[batch]
    if not b then
      enter('teleport')
      return
    end
    local rec = {name = b.name, objects = #b.items, triangles = b.triangles}
    result.batches[batch] = rec
    pending, spawnRec = {items = b.items, i = 0}, rec
    rec.spawnMs, rec.worstObjectMs, rec.spawnFrames = 0, 0, 0
    rec.readMs, rec.expandMs, rec.createMs, rec.worstProcMs, rec.worstStaticMs = 0, 0, 0, 0, 0
    rec.worstReadMs, rec.worstExpandMs, rec.worstCreateMs, rec.gcHits = 0, 0, 0, 0
    enter('spawning')

  elseif phase == 'spawning' then
    -- a time budget per frame; budget 0 means the whole batch in one frame
    -- Each object is a coroutine; ProceduralMesh building yields when the frame is over budget and
    -- resumes next frame. worst*Ms are the longest single slice of one object within one frame.
    local rec, frame = spawnRec, hptimer()
    local budget = cfg.spawnBudgetMs
    local function over() return budget > 0 and frame:stop() >= budget end
    -- the coroutine keeps the pause it was started with, so pause reads this frame's check
    pending.over = over
    local function pause() if pending.over() then coroutine.yield() end end
    repeat
      if not pending.co then
        pending.i = pending.i + 1
        local it = pending.items[pending.i]
        if not it then break end
        pending.it = it
        pending.co = coroutine.create(function(p) spawnOne(it, rec, p) end)
      end
      local one = hptimer()
      local ok, err = coroutine.resume(pending.co, pause)
      local took = ms(one)
      if not ok then
        log('E', 'e2rstream', tostring(err))
        rec.errors = (rec.errors or 0) + 1
      end
      rec.worstObjectMs = math.max(rec.worstObjectMs, took)
      if pending.it.kind == 'proc' then
        rec.worstProcMs = math.max(rec.worstProcMs, took)
      else
        rec.worstStaticMs = math.max(rec.worstStaticMs, took)
      end
      rec.spawnMs = rec.spawnMs + took
      if coroutine.status(pending.co) == 'dead' then pending.co = nil end
    until over()
    rec.spawnFrames = rec.spawnFrames + 1
    if pending.i >= #pending.items and not pending.co then
      rec.spawnWallFrames = frameStats()
      local timer = hptimer()
      be:reloadCollision()
      rec.reloadCollisionMs = ms(timer)
      rec.totalObjects = #scenetree.findClassObjects('TSStatic') + #scenetree.findClassObjects('ProceduralMesh')
      save()
      enter('batch_frames')
    end

  elseif phase == 'batch_frames' and t > cfg.batchSettleSeconds then
    result.batches[batch].afterFrames = frameStats()
    look(cfg.overview.pos, cfg.overview.look)
    shot(string.format('%d-%s', batch, cfg.batches[batch].name))
    enter('next_batch_wait')

  elseif phase == 'next_batch_wait' and t > 1 then
    enter('next_batch')

  elseif phase == 'teleport' then
    local veh = getPlayerVehicle(0)
    local s = cfg.spawn
    local rot = quat(0, 0, math.sin(s.yaw / 2), math.cos(s.yaw / 2))
    spawn.safeTeleport(veh, vec3(s.pos[1], s.pos[2], s.pos[3]), rot)
    commands.setGameCamera()
    enter('settle_car')

  elseif phase == 'settle_car' and t > 4 then
    result.vehicleSettled = vehicleState()
    shot('car-settled')
    getPlayerVehicle(0):queueLuaCommand("input.event('throttle', 0.45, 2)")
    enter('drive')

  elseif phase == 'drive' and t > cfg.driveSeconds then
    getPlayerVehicle(0):queueLuaCommand("input.event('throttle', 0, 2) input.event('brake', 1, 2)")
    result.vehicleAfterDrive = vehicleState()
    result.driveFrames = frameStats()
    shot('car-driven')
    enter('finish')

  elseif phase == 'finish' and t > 2 then
    result.status = 'complete'
    save()
    shutdown(0)
  end
end

return M
