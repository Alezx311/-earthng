-- Runtime geometry from compact source features. Nothing is written to disk, and no
-- service or executable is required. All coordinates remain in the source region's frame.
local M = {}
local EPS = 1e-8
local ROAD_LIFT = 0.10
local TREE = '/assets/meshes/foliage/trees_library/oak/tree_oak_sml_a.dae'
local LAMP = '/art/shapes/objects/lamp1.dae'
local TREES = {TREE, '/assets/meshes/foliage/trees_library/beech/tree_beech_small_b.dae',
  '/assets/meshes/foliage/trees_library/birch/tree_aspen_small_b.dae',
  '/assets/meshes/foliage/trees_library/oak/tree_oak_forest_a.dae'}

local function hash(value)
  local h = 17
  for index = 1, #value do h = (h * 131 + value:byte(index)) % 2147483647 end
  return h
end

local function lerp(a, b, t)
  local out = {}
  for k = 1, math.max(#a, #b) do out[k] = (a[k] or 0) + ((b[k] or 0) - (a[k] or 0)) * t end
  return out
end

local function cleanRing(ring)
  local out = {}
  for _, p in ipairs(ring or {}) do
    local q = out[#out]
    if not q or math.abs(q[1] - p[1]) + math.abs(q[2] - p[2]) > EPS then out[#out + 1] = p end
  end
  if #out > 1 and math.abs(out[1][1] - out[#out][1]) + math.abs(out[1][2] - out[#out][2]) < EPS then
    out[#out] = nil
  end
  return out
end

-- Horizontal slabs use even/odd filling across every ring. They triangulate concave
-- footprints and courtyard holes directly, without fragile bridges between duplicate vertices.
local function triangulate(rings, pause, rect)
  local edges, ys, seen, result = {}, {}, {}, {}
  if rect then ys, seen = {rect[2], rect[4]}, {[rect[2]] = true, [rect[4]] = true} end
  for _, raw in ipairs(rings or {}) do
    local ring = cleanRing(raw)
    for k, a in ipairs(ring) do
      local b = ring[k % #ring + 1]
      if not seen[a[2]] and (not rect or (a[2] > rect[2] and a[2] < rect[4])) then ys[#ys + 1], seen[a[2]] = a[2], true end
      if math.abs(a[2] - b[2]) > EPS and (not rect or (math.min(a[2], b[2]) < rect[4] and math.max(a[2], b[2]) > rect[2])) then edges[#edges + 1] = {a, b} end
    end
  end
  table.sort(ys)
  local function on(edge, y)
    return lerp(edge[1], edge[2], (y - edge[1][2]) / (edge[2][2] - edge[1][2]))
  end
  for n = 1, #ys - 1 do
    local low, high, cross = ys[n], ys[n + 1], {}
    local mid = (low + high) * 0.5
    for index, edge in ipairs(edges) do
      local a, b = edge[1][2], edge[2][2]
      if (a <= mid and b > mid) or (b <= mid and a > mid) then
        cross[#cross + 1] = {x = on(edge, mid)[1], edge = edge, index = index}
      end
    end
    table.sort(cross, function(a, b) return a.x == b.x and a.index < b.index or a.x < b.x end)
    for k = 1, #cross - 1, 2 do
      local a, b, c, d = on(cross[k].edge, low), on(cross[k + 1].edge, low),
                           on(cross[k + 1].edge, high), on(cross[k].edge, high)
      if b[1] - a[1] > EPS then result[#result + 1] = {a, b, c} end
      if c[1] - d[1] > EPS then result[#result + 1] = {a, c, d} end
    end
    if pause then pause() end
  end
  return result
end

-- Clip in XY while interpolating elevation and optional UVs. Identical world-space
-- intersections on adjacent tiles prevent cracks and preserve road profiles at their border.
local function clip(poly, rect)
  local output = poly
  for side = 1, 4 do
    local axis = (side == 1 or side == 3) and 1 or 2
    local boundary, greater = rect[side], side < 3
    local input = output
    output = {}
    if #input == 0 then break end
    local a = input[#input]
    local inA = greater and a[axis] >= boundary - EPS or not greater and a[axis] <= boundary + EPS
    for _, b in ipairs(input) do
      local inB = greater and b[axis] >= boundary - EPS or not greater and b[axis] <= boundary + EPS
      if inA ~= inB then
        local t = (boundary - a[axis]) / (b[axis] - a[axis])
        local p = lerp(a, b, t)
        p[axis] = boundary
        output[#output + 1] = p
      end
      if inB then output[#output + 1] = b end
      a, inA = b, inB
    end
  end
  return output
end

local function boundsTouch(a, b, margin)
  margin = margin or 0
  return a[1] <= b[3] + margin and a[3] >= b[1] - margin and a[2] <= b[4] + margin and a[4] >= b[2] - margin
end

local function ringBounds(rings)
  local bounds = {math.huge, math.huge, -math.huge, -math.huge}
  for _, ring in ipairs(rings or {}) do
    for _, p in ipairs(ring) do
      bounds[1], bounds[2] = math.min(bounds[1], p[1]), math.min(bounds[2], p[2])
      bounds[3], bounds[4] = math.max(bounds[3], p[1]), math.max(bounds[4], p[2])
    end
  end
  return bounds
end

local function ringsTouch(rings, rect)
  return boundsTouch(ringBounds(rings), rect)
end

local function inside(rings, x, y)
  local filled = false
  for _, raw in ipairs(rings or {}) do
    local ring = raw
    local previous = ring[#ring]
    for _, p in ipairs(ring) do
      if (p[2] > y) ~= (previous[2] > y)
        and x < (previous[1] - p[1]) * (y - p[2]) / (previous[2] - p[2]) + p[1] then filled = not filled end
      previous = p
    end
  end
  return filled
end

local function number(value, default)
  return tonumber(tostring(value or ''):match('^%s*([%d%.%-]+)')) or default
end

local function collector(rect, key, layer, pause)
  local origin = {(rect[1] + rect[3]) * 0.5, (rect[2] + rect[4]) * 0.5, 0}
  local items, groups, used, triangles, operations = {}, {}, {}, 0, 0
  local function flush()
    local meshes, mats = {}, {}
    for material in pairs(groups) do mats[#mats + 1] = material end
    table.sort(mats)
    for _, material in ipairs(mats) do meshes[#meshes + 1] = groups[material] end
    if #meshes > 0 then
      items[#items + 1] = {kind = 'proc', name = 'e2rl_' .. key .. '_' .. layer .. '_' .. #items,
        pos = origin, meshes = meshes, triangles = triangles, mats = mats, layer = layer}
    end
    groups, triangles = {}, 0
  end
  local function triangle(kind, a, b, c, up)
    local ux, uy, uz = b[1] - a[1], b[2] - a[2], (b[3] or 0) - (a[3] or 0)
    local vx, vy, vz = c[1] - a[1], c[2] - a[2], (c[3] or 0) - (a[3] or 0)
    local nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    local len = math.sqrt(nx * nx + ny * ny + nz * nz)
    if len < EPS then return end
    if up and nz < 0 then b, c, nx, ny, nz = c, b, -nx, -ny, -nz end
    nx, ny, nz = nx / len, ny / len, nz / len
    if up == 'flat' then nx, ny, nz = 0, 0, 1 end
    if triangles >= 1500 then flush() end
    local material = 'e2rf_' .. kind
    local mesh = groups[material]
    if not mesh then
      mesh = {verts = {}, normals = {}, uvs = {}, faces = {}, material = material}
      groups[material], used[material] = mesh, true
    end
    local offset, scale = #mesh.verts, kind == 'ground' and 24 or 6
    if kind == 'sidewalk' or kind:find('wall_', 1, true) == 1 then scale = 3 end
    for _, p in ipairs({a, b, c}) do
      mesh.verts[#mesh.verts + 1] = {x = p[1] - origin[1], y = p[2] - origin[2], z = p[3] or 0}
      mesh.normals[#mesh.normals + 1] = {x = nx, y = ny, z = nz}
      mesh.uvs[#mesh.uvs + 1] = {u = p[4] or p[1] / scale, v = p[5] or p[2] / scale}
    end
    -- BeamNG ProceduralMesh expects clockwise face indices and zero-based indices.
    for _, index in ipairs({offset, offset + 2, offset + 1}) do
      mesh.faces[#mesh.faces + 1] = {v = index, n = index, u = index}
    end
    triangles, operations = triangles + 1, operations + 1
    if operations % 64 == 0 then pause() end
  end
  local function polygon(kind, polygon, up)
    local clipped = clip(polygon, rect)
    for k = 2, #clipped - 1 do triangle(kind, clipped[1], clipped[k], clipped[k + 1], up) end
  end
  return {items = items, used = used, polygon = polygon, finish = flush}
end

local SPEED = {motorway = 110, trunk = 90, primary = 60, secondary = 60, tertiary = 50,
  unclassified = 50, residential = 40, living_street = 20}
local DRIVE = {motorway = 1, trunk = 1, primary = 1, secondary = 1, tertiary = .9,
  unclassified = .7, residential = .7, living_street = .4}
local function navSegment(road)
  local tags, points = road.tags or {}, road.points or {}
  local highway = (tags.highway or 'residential'):gsub('_link$', '')
  if not SPEED[highway] or tags.access == 'private' or tags.access == 'no' or tags.area == 'yes' or #points < 2 then return nil end
  local total = math.max(1, math.min(8, math.floor(number(tags.lanes, math.floor((road.width or 7) / 3.5 + .5)))))
  local one = tostring(tags.oneway or ''):lower()
  if (tags.junction == 'roundabout' or tags.junction == 'circular') and one ~= 'no' and one ~= '-1' then one = 'yes' end
  local flip = one == '-1'
  local lanes
  if one == 'yes' or one == '1' or one == 'true' or flip then
    lanes = string.rep('+', math.min(4, total))
  else
    local back = number(tags['lanes:backward'], math.max(1, math.floor(total / 2)))
    local forward = number(tags['lanes:forward'], math.max(1, total - back))
    lanes = string.rep('-', math.min(4, back)) .. string.rep('+', math.min(4, forward))
  end
  local speedText = tostring(tags.maxspeed or ''):lower()
  local speed = number(speedText, SPEED[highway])
  if speedText:find('mph', 1, true) then speed = speed * 1.609 end
  if speedText == 'none' then speed = 130 end
  if speedText:find('urban', 1, true) then speed = 50 end
  if speedText:find('rural', 1, true) then speed = 90 end
  local nodes = road.nodes or {tostring(road.id) .. ':a', tostring(road.id) .. ':b'}
  local result = {tostring(road.id), nodes[flip and 2 or 1], nodes[flip and 1 or 2], lanes,
    speed / 3.6, (road.width or 7) / 2, DRIVE[highway]}
  local ordered = {}
  for index = 1, #points do ordered[index] = points[flip and (#points - index + 1) or index] end
  for index, p in ipairs(ordered) do
    if index > 1 then
      local prev = ordered[index - 1]
      local distance = math.sqrt((p[1] - prev[1]) ^ 2 + (p[2] - prev[2]) ^ 2)
      local steps = math.max(1, math.ceil(distance / 80))
      for step = 1, steps - 1 do
        local q = lerp(prev, p, step / steps)
        result[#result + 1], result[#result + 2], result[#result + 3] = q[1], q[2], (q[3] or 0) + ROAD_LIFT
      end
    end
    result[#result + 1], result[#result + 2], result[#result + 3] = p[1], p[2], (p[3] or 0) + ROAD_LIFT
  end
  return result
end

local function wallMaterial(building)
  local tags = building.tags or {}
  local h = hash(tostring(building.id or ''))
  local height = building.height or number(tags.height, number(tags['building:levels'], 3) * 3)
  local material = tags['building:material'] or tags['facade:material']
  local b = tags.building
  if b == 'industrial' or b == 'warehouse' or b == 'garages' or b == 'garage' or b == 'shed' or b == 'factory' then return 'wall_block' end
  if material == 'glass' or b == 'office' or b == 'commercial' or b == 'retail' or b == 'hotel' or tags.office then
    local family = height > 20 and {'off_blue', 'off_green', 'off_grey', 'off_ribbon'} or {'off_ribbon', 'off_ribbon_dark'}
    return 'wall_' .. family[h % #family + 1]
  end
  local panel = {'pnl_grey', 'pnl_beige', 'pnl_pink', 'pnl_blue', 'pnl_white'}
  local loggia = {'lgg_cream', 'lgg_grey', 'lgg_pink'}
  local brick = {'brk_red', 'brk_white'}
  local plaster = {'pls_yellow', 'pls_cream', 'pls_peach', 'pls_green', 'pls_blue', 'pls_grey'}
  local family
  if material == 'brick' then family = brick
  elseif material == 'panel' or material == 'concrete' or material == 'reinforced_concrete' then family = height >= 26 and h % 3 ~= 0 and loggia or panel
  elseif material == 'plaster' or material == 'stucco' then family = plaster
  elseif height >= 26 then family = h % 9 < 4 and loggia or panel
  elseif height >= 14 then family = h % 20 < 8 and panel or h % 20 < 15 and brick or plaster
  else family = h % 5 < 3 and plaster or brick end
  return 'wall_' .. family[math.floor(h / 7) % #family + 1]
end

local function facadeScale(kind)
  if kind:find('_end$', 1) then return 6 end
  if kind:find('wall_lgg_', 1, true) == 1 then return 33 end
  if kind:find('wall_pnl_', 1, true) == 1 then return 12 end
  if kind == 'wall_block' then return 3 end
  return 6
end

local function footprintFrame(ring)
  local ax, ay, longest, area = 1, 0, 0, 0
  for index, p in ipairs(ring) do
    local q = ring[index % #ring + 1]
    local dx, dy = q[1] - p[1], q[2] - p[2]
    local length = math.sqrt(dx * dx + dy * dy)
    if length > longest then ax, ay, longest = dx / length, dy / length, length end
    area = area + p[1] * q[2] - q[1] * p[2]
  end
  local bx, by = -ay, ax
  local u0, v0, u1, v1 = math.huge, math.huge, -math.huge, -math.huge
  for _, p in ipairs(ring) do
    local u, v = p[1] * ax + p[2] * ay, p[1] * bx + p[2] * by
    u0, v0, u1, v1 = math.min(u0, u), math.min(v0, v), math.max(u1, u), math.max(v1, v)
  end
  if u1 - u0 < v1 - v0 then ax, ay, bx, by, u0, v0, u1, v1 = bx, by, -ax, -ay, v0, -u1, v1, -u0 end
  return {ax = ax, ay = ay, bx = bx, by = by, u0 = u0, v0 = v0, u1 = u1, v1 = v1, area = math.abs(area) / 2}
end

local function pitchedRoof(scene, rings, tags, top, base, kind, height)
  if #rings ~= 1 then return false end -- Courtyards always keep their open flat-roof footprint.
  local ring = cleanRing(rings[1])
  if #ring < 3 then return false end
  local frame = footprintFrame(ring)
  local length, width = frame.u1 - frame.u0, frame.v1 - frame.v0
  local shape, house = tags['roof:shape'], tags.building == 'house' or tags.building == 'detached' or tags.building == 'bungalow'
  if shape and shape ~= 'gabled' and shape ~= 'hipped' and shape ~= 'pyramidal' and shape ~= 'half-hipped' then return false end
  if not shape and not (house or (height < 8 and frame.area < 260)) then return false end
  if width < 3 or length < 3 or frame.area / math.max(EPS, length * width) < .82 then return false end
  if not shape and width > 18 then return false end
  local rise = math.max(.5, math.min(10, number(tags['roof:height'], math.min(6, width / 2 * math.tan(math.rad(house and 35 or 28))))))
  local function point(u, v, z) return {u * frame.ax + v * frame.bx, u * frame.ay + v * frame.by, z} end
  local a, b, c, d = point(frame.u0, frame.v0, top), point(frame.u1, frame.v0, top),
                         point(frame.u1, frame.v1, top), point(frame.u0, frame.v1, top)
  local inset = (shape == 'hipped' or shape == 'half-hipped' or shape == 'pyramidal') and math.min(width / 2, length / 2) or 0
  if shape == 'pyramidal' then inset = length / 2 end
  local r0, r1 = point(frame.u0 + inset, (frame.v0 + frame.v1) / 2, top + rise),
                   point(frame.u1 - inset, (frame.v0 + frame.v1) / 2, top + rise)
  local material = (tags['roof:material'] == 'metal' or tags['roof:material'] == 'tin') and 'roof_metal' or 'roof_tile'
  scene.polygon(material, {a, b, r1, r0}, true)
  scene.polygon(material, {c, d, r0, r1}, true)
  if inset > 0 then
    scene.polygon(material, {b, c, r1}, true)
    scene.polygon(material, {d, a, r0}, true)
  else
    local scale = facadeScale(kind)
    for _, face in ipairs({{b, c, r1}, {d, a, r0}}) do
      face[1] = {face[1][1], face[1][2], top, 0, (top - base) / scale}
      face[2] = {face[2][1], face[2][2], top, width / scale, (top - base) / scale}
      face[3] = {face[3][1], face[3][2], top + rise, width / (2 * scale), (top + rise - base) / scale}
      scene.polygon(kind, face, false)
    end
  end
  return true
end

local function parkedCar(mesh, x, y, z, yaw, colour)
  local cosine, sine = math.cos(yaw), math.sin(yaw)
  local function box(x0, y0, z0, x1, y1, z1, tx0, ty0, tx1, ty1, body, windows)
    local bottom = {{x0, y0, z0}, {x1, y0, z0}, {x1, y1, z0}, {x0, y1, z0}}
    local top = {{tx0 or x0, ty0 or y0, z1}, {tx1 or x1, ty0 or y0, z1}, {tx1 or x1, ty1 or y1, z1}, {tx0 or x0, ty1 or y1, z1}}
    local faces = {{top[1], top[2], top[3], top[4]}}
    for index = 1, 4 do faces[#faces + 1] = {bottom[index], bottom[index % 4 + 1], top[index % 4 + 1], top[index]} end
    for index, face in ipairs(faces) do
      local cell = index == 1 and body or windows or body
      local polygon = {}
      for _, p in ipairs(face) do polygon[#polygon + 1] = {x + p[1] * cosine - p[2] * sine,
        y + p[1] * sine + p[2] * cosine, z + p[3], (cell + .5) / 16, .5} end
      mesh.polygon('car', polygon, index == 1)
    end
  end
  box(-2.15, -.88, .32, 2.15, .88, .86, nil, nil, nil, nil, colour)
  box(-1.25, -.8, .86, .85, .8, 1.4, -1, -.72, .3, .72, colour, 8)
  box(-1.55, -.84, .04, 1.55, .84, .32, nil, nil, nil, nil, 9)
end

local function build(cells, i, j, options)
  options = options or {}
  local pause = options.pause or options.yield or function() end
  local size = options.tileSize or 500
  local rect, key = {i * size, j * size, (i + 1) * size, (j + 1) * size}, tostring(i) .. '_' .. tostring(j)
  local target = options.center or {(i + .5) * size, (j + .5) * size}
  local lists = {roads = {}, junctions = {}, buildings = {}, areas = {}, lines = {}, points = {}}
  local seen, terrain = {}, {}
  for _, cell in ipairs(cells or {}) do
    if cell.terrain then terrain[#terrain + 1] = cell.terrain end
    for kind, output in pairs(lists) do
      seen[kind] = seen[kind] or {}
      for index, feature in ipairs(cell[kind] or {}) do
        local id = tostring(feature.id or index)
        if not seen[kind][id] then
          seen[kind][id], output[#output + 1] = true, feature
        end
      end
    end
    pause()
  end
  for _, list in pairs(lists) do table.sort(list, function(a, b) return tostring(a.id or '') < tostring(b.id or '') end) end
  local roads, scene = collector(rect, key, 'road', pause), collector(rect, key, options.far and 'far' or 'scene', pause)
  local roadDecor, sceneDecor = collector(rect, key .. '_marks', 'road', pause), collector(rect, key .. '_cars', 'scene', pause)
  local segments, segmentBins, nav, bestSpawn, bestDistance = {}, {}, {segments = {}}, nil, math.huge
  for _, road in ipairs(lists.roads) do
    local tags, points, width = road.tags or {}, road.points or {}, math.max(.5, math.min(80, road.width or 7))
    for index = 1, #points - 1 do
      local a, b = points[index], points[index + 1]
      local dx, dy = b[1] - a[1], b[2] - a[2]
      local length = math.sqrt(dx * dx + dy * dy)
      if length > EPS then
        local bounds = {math.min(a[1], b[1]), math.min(a[2], b[2]), math.max(a[1], b[1]), math.max(a[2], b[2])}
        if not options.far and boundsTouch(rect, bounds, width / 2 + 75) then
          local segment = {a = a, b = b, dx = dx, dy = dy, len2 = length * length, width = width,
            surface = tags.bridge ~= 'yes' and tags.tunnel ~= 'yes' and tags.layer ~= '-1'}
          segments[#segments + 1] = segment
          local margin = width / 2 + 70
          for by = math.floor((bounds[2] - margin) / 50), math.floor((bounds[4] + margin) / 50) do
            for bx = math.floor((bounds[1] - margin) / 50), math.floor((bounds[3] + margin) / 50) do
              local binKey = tostring(bx) .. ':' .. tostring(by)
              local bin = segmentBins[binKey] or {}
              segmentBins[binKey], bin[#bin + 1] = bin, segment
            end
          end
        end
        if not options.far and boundsTouch(rect, bounds, width / 2 + 3) then
          local nx, ny = -dy / length, dx / length
          local function ribbon(left, right, lift, material)
            roads.polygon(material, {
              {a[1] + nx * left, a[2] + ny * left, (a[3] or 0) + lift},
              {a[1] + nx * right, a[2] + ny * right, (a[3] or 0) + lift},
              {b[1] + nx * right, b[2] + ny * right, (b[3] or 0) + lift},
              {b[1] + nx * left, b[2] + ny * left, (b[3] or 0) + lift}}, 'flat')
          end
          local major = tags.highway == 'motorway' or tags.highway == 'trunk' or tags.highway == 'primary'
          ribbon(-width / 2, width / 2, ROAD_LIFT, major and 'road_major' or 'road')
          if not major and tags.sidewalk ~= 'no' and tags.bridge ~= 'yes' and width >= 5 then
            ribbon(width / 2, width / 2 + 1.8, .22, 'sidewalk')
            ribbon(-width / 2 - 1.8, -width / 2, .22, 'sidewalk')
          end
          local t = math.max(0, math.min(1, ((target[1] - a[1]) * dx + (target[2] - a[2]) * dy) / (length * length)))
          local p = lerp(a, b, t)
          local distance = (p[1] - target[1]) ^ 2 + (p[2] - target[2]) ^ 2
          if distance < bestDistance and p[1] >= rect[1] and p[1] <= rect[3] and p[2] >= rect[2] and p[2] <= rect[4] then
            bestDistance = distance
            bestSpawn = {pos = {p[1], p[2], (p[3] or 0) + ROAD_LIFT}, yaw = math.atan2(dx, dy)}
          end
        end
      end
      if index % 32 == 0 then pause() end
    end
    -- A bevel closes the outer gap between consecutive ribbons on a bend. Its
    -- source-space vertices stay identical on adjacent tiles.
    for index = 2, options.far and 1 or #points - 1 do
      local p = points[index]
      if p[1] + width / 2 >= rect[1] and p[1] - width / 2 <= rect[3]
        and p[2] + width / 2 >= rect[2] and p[2] - width / 2 <= rect[4] then
        local a, b = points[index - 1], points[index + 1]
        local ax, ay, bx, by = p[1] - a[1], p[2] - a[2], b[1] - p[1], b[2] - p[2]
        local al, bl = math.sqrt(ax * ax + ay * ay), math.sqrt(bx * bx + by * by)
        local turn = ax * by - ay * bx
        if al > EPS and bl > EPS and math.abs(turn) > EPS then
          local side = turn > 0 and -width / 2 or width / 2
          local material = (tags.highway == 'motorway' or tags.highway == 'trunk' or tags.highway == 'primary') and 'road_major' or 'road'
          roads.polygon(material, {{p[1], p[2], (p[3] or 0) + ROAD_LIFT},
            {p[1] - ay / al * side, p[2] + ax / al * side, (p[3] or 0) + ROAD_LIFT},
            {p[1] - by / bl * side, p[2] + bx / bl * side, (p[3] or 0) + ROAD_LIFT}}, 'flat')
        end
      end
    end
    local navigation = not options.far and navSegment(road)
    if navigation then nav.segments[#nav.segments + 1] = navigation end
    pause()
  end

  if not options.far then
    for _, road in ipairs(lists.roads) do
      local points, tags, width = road.points or {}, road.tags or {}, road.width or 7
      local one = tostring(tags.oneway or '')
      local oneway = one == 'yes' or one == '1' or one == '-1' or one == 'true'
      local lanes = math.max(1, math.min(math.floor(width / 2.5), math.floor(number(tags.lanes, oneway and math.floor(width / 3.3 + .5) or 2))))
      local marks = {}
      if width >= 5 then
        for lane = 1, lanes - 1 do marks[#marks + 1] = {offset = -width / 2 + lane * width / lanes, width = .12,
          dashed = not (not oneway and lanes >= 4 and math.abs(lane - lanes / 2) < .01)} end
        local major = (tags.highway or ''):gsub('_link$', '')
        if width >= 7 and (major == 'motorway' or major == 'trunk' or major == 'primary' or major == 'secondary') then
          marks[#marks + 1], marks[#marks + 2] = {offset = width / 2 - .3, width = .15}, {offset = -width / 2 + .3, width = .15}
        end
      end
      local distance = 0
      for index = 1, #points - 1 do
        local a, b = points[index], points[index + 1]
        local dx, dy = b[1] - a[1], b[2] - a[2]
        local length = math.sqrt(dx * dx + dy * dy)
        if length > EPS then
          if boundsTouch(rect, {math.min(a[1], b[1]), math.min(a[2], b[2]), math.max(a[1], b[1]), math.max(a[2], b[2])}, width / 2) then
            local nx, ny = -dy / length, dx / length
            local function span(mark, start, finish)
              if finish - start <= EPS then return end
              local p, q = lerp(a, b, (start - distance) / length), lerp(a, b, (finish - distance) / length)
              local left, right = mark.offset - mark.width / 2, mark.offset + mark.width / 2
              roadDecor.polygon('mark', {{p[1] + nx * left, p[2] + ny * left, (p[3] or 0) + ROAD_LIFT + .008},
                {p[1] + nx * right, p[2] + ny * right, (p[3] or 0) + ROAD_LIFT + .008},
                {q[1] + nx * right, q[2] + ny * right, (q[3] or 0) + ROAD_LIFT + .008},
                {q[1] + nx * left, q[2] + ny * left, (q[3] or 0) + ROAD_LIFT + .008}}, 'flat')
            end
            for _, mark in ipairs(marks) do
              if mark.dashed then
                for start = math.floor(distance / 12) * 12, distance + length, 12 do span(mark, math.max(distance, start), math.min(distance + length, start + 3)) end
              else span(mark, distance, distance + length) end
            end
          end
          distance = distance + length
        end
        if index % 32 == 0 then pause() end
      end
      pause()
    end
  end

  local function rawHeight(x, y)
    for _, grid in ipairs(terrain) do
      local box = grid.rect
      if box and x >= box[1] - EPS and x <= box[3] + EPS and y >= box[2] - EPS and y <= box[4] + EPS and grid.nx >= 2 and grid.ny >= 2 then
        local gx = math.max(0, math.min(grid.nx - 1, (x - box[1]) / (box[3] - box[1]) * (grid.nx - 1)))
        local gy = math.max(0, math.min(grid.ny - 1, (y - box[2]) / (box[4] - box[2]) * (grid.ny - 1)))
        local ix, iy = math.min(grid.nx - 2, math.floor(gx)), math.min(grid.ny - 2, math.floor(gy))
        local tx, ty = gx - ix, gy - iy
        local function z(dx, dy) return grid.heights[(iy + dy) * grid.nx + ix + dx + 1] or 0 end
        return (z(0, 0) * (1 - tx) + z(1, 0) * tx) * (1 - ty) + (z(0, 1) * (1 - tx) + z(1, 1) * tx) * ty
      end
    end
    return 0
  end
  local heightOperations = 0
  local function ground(x, y)
    local base, nearest, profile = rawHeight(x, y), math.huge, nil
    if options.far then return base end
    local nearby = segmentBins[tostring(math.floor(x / 50)) .. ':' .. tostring(math.floor(y / 50))] or {}
    for _, segment in ipairs(nearby) do
      if segment.surface then
        local a = segment.a
        local t = math.max(0, math.min(1, ((x - a[1]) * segment.dx + (y - a[2]) * segment.dy) / segment.len2))
        local px, py = a[1] + t * segment.dx, a[2] + t * segment.dy
        local edge = math.sqrt((x - px) ^ 2 + (y - py) ^ 2) - segment.width / 2
        if edge < nearest then nearest, profile = edge, (a[3] or 0) + t * ((segment.b[3] or 0) - (a[3] or 0)) end
      end
      heightOperations = heightOperations + 1
      if heightOperations % 512 == 0 then pause() end
    end
    if profile and nearest < 70 then
      local pull = math.max(0, math.min(1, (70 - nearest) / 58))
      return base * (1 - pull) + profile * pull
    end
    return base
  end

  local step = options.terrainStep or 25
  local count = math.max(1, math.ceil(size / step))
  local rows = {}
  for gy = 0, options.far and -1 or count do
    local row = {}
    for gx = 0, count do
      local x, y = rect[1] + size * gx / count, rect[2] + size * gy / count
      row[gx + 1] = {x, y, ground(x, y)}
    end
    rows[gy + 1] = row
    if gy > 0 then
      for gx = 1, count do roads.polygon('ground', {rows[gy][gx], rows[gy][gx + 1], row[gx + 1], row[gx]}, true) end
    end
    pause()
  end
  for _, junction in ipairs(options.far and {} or lists.junctions) do
    for _, tri in ipairs(ringsTouch(junction.rings, rect) and triangulate(junction.rings, pause, rect) or {}) do
      for _, p in ipairs(tri) do p[3] = (junction.height or p[3] or ground(p[1], p[2])) + ROAD_LIFT + .01 end
      roads.polygon('road', tri, 'flat')
    end
    pause()
  end
  for _, area in ipairs(options.far and {} or lists.areas) do
    local kind = area.kind
    if (kind == 'water' or kind == 'paved' or kind == 'forest' or kind == 'park') and ringsTouch(area.rings, rect) then
      for _, tri in ipairs(triangulate(area.rings, pause, rect)) do
        for _, p in ipairs(tri) do p[3] = ground(p[1], p[2]) + .025 end
        scene.polygon(kind, tri, true)
      end
    end
    pause()
  end
  for _, building in ipairs(lists.buildings) do
    local rings, tags = building.rings or {}, building.tags or {}
    local exterior = cleanRing(rings[1])
    if #exterior >= 3 and ringsTouch(rings, rect) then
      -- Buildings can cross source-cell boundaries. Their full exported footprint Z
      -- gives both tiles the same datum even when the centroid's DEM cell is absent.
      local sourceLow, sourceSum, sourceCount = math.huge, 0, 0
      for _, p in ipairs(exterior) do
        if p[3] then sourceLow, sourceSum, sourceCount = math.min(sourceLow, p[3]), sourceSum + p[3], sourceCount + 1 end
      end
      if options.far then
        local box = ringBounds(rings)
        rings = {{{box[1], box[2]}, {box[3], box[2]}, {box[3], box[4]}, {box[1], box[4]}}}
        exterior = rings[1]
      end
      local low, cx, cy = math.huge, 0, 0
      for _, p in ipairs(exterior) do low, cx, cy = math.min(low, ground(p[1], p[2])), cx + p[1], cy + p[2] end
      local base = sourceCount > 0 and sourceSum / sourceCount or ground(cx / #exterior, cy / #exterior)
      if sourceCount > 0 then low = sourceLow end
      local height = math.max(2.5, math.min(250, building.height or number(tags.height, number(tags['building:levels'], 3) * 3)))
      local bottom = (building.minHeight or number(tags.min_height, 0)) > 0 and base + (building.minHeight or number(tags.min_height, 0)) or low - .3
      local top, kind = base + height, wallMaterial(building)
      for _, raw in ipairs(rings) do
        local ring, along = cleanRing(raw), 0
        local signed = 0
        for k, p in ipairs(ring) do local q = ring[k % #ring + 1]; signed = signed + p[1] * q[2] - q[1] * p[2] end
        -- Outer walls face away from CCW rings; courtyard walls face into clockwise rings.
        local reverse = (raw == rings[1] and signed < 0) or (raw ~= rings[1] and signed > 0)
        if reverse then local reversed = {}; for k = #ring, 1, -1 do reversed[#reversed + 1] = ring[k] end; ring = reversed end
        for k, a in ipairs(ring) do
          local b = ring[k % #ring + 1]
          local length = math.sqrt((b[1] - a[1]) ^ 2 + (b[2] - a[2]) ^ 2)
          local wallKind = kind
          if length < 16 and (kind:find('wall_pnl_', 1, true) == 1 or kind:find('wall_lgg_', 1, true) == 1 or kind:find('wall_brk_', 1, true) == 1) then wallKind = kind .. '_end' end
          local scale = facadeScale(wallKind)
          scene.polygon(wallKind, {{a[1], a[2], bottom, along / scale, (bottom - base) / scale},
            {b[1], b[2], bottom, (along + length) / scale, (bottom - base) / scale},
            {b[1], b[2], top, (along + length) / scale, height / scale},
            {a[1], a[2], top, along / scale, height / scale}}, false)
          along = along + length
          if k % 32 == 0 then pause() end
        end
      end
      if options.far or not pitchedRoof(scene, rings, tags, top, base, kind, height) then
        for _, tri in ipairs(triangulate(rings, pause, rect)) do
          for _, p in ipairs(tri) do p[3] = top end
          scene.polygon('roof', tri, true)
        end
      end
    end
    pause()
  end
  for _, line in ipairs(options.far and {} or lists.lines) do
    local points, tags = line.points or {}, line.tags or {}
    for index = 1, #points - 1 do
      local a, b = points[index], points[index + 1]
      local dx, dy = b[1] - a[1], b[2] - a[2]
      local length = math.sqrt(dx * dx + dy * dy)
      if length > EPS then
        if line.kind == 'fence' then
          local za, zb, height = ground(a[1], a[2]), ground(b[1], b[2]), number(tags.height, 1.7)
          scene.polygon(tags.barrier == 'wall' and 'fence_wall' or 'fence_mesh',
            {{a[1], a[2], za, 0, 0}, {b[1], b[2], zb, length / 2, 0},
             {b[1], b[2], zb + height, length / 2, height / 2}, {a[1], a[2], za + height, 0, height / 2}}, false)
        elseif line.kind == 'path' then
          local width = number(tags.width, 2) / 2
          local nx, ny = -dy / length * width, dx / length * width
          local material = tags.surface == 'asphalt' and 'footway_asphalt' or tags.highway == 'path' and 'trodden' or 'footway'
          local poly = {{a[1] + nx, a[2] + ny}, {a[1] - nx, a[2] - ny}, {b[1] - nx, b[2] - ny}, {b[1] + nx, b[2] + ny}}
          for _, p in ipairs(poly) do p[3] = ground(p[1], p[2]) + .04 end
          scene.polygon(material, poly, true)
        end
      end
      if index % 32 == 0 then pause() end
    end
    pause()
  end
  local function static(name, shape, x, y, z, yaw, scale)
    scene.items[#scene.items + 1] = {kind = 'static', name = 'e2rl_' .. key .. '_' .. name,
      pos = {x, y, z}, rot = {0, 0, math.sin(yaw / 2), math.cos(yaw / 2)}, scale = scale or 1,
      shape = shape, decor = true, layer = 'scene', collision = 'None'}
  end
  if not options.far then
    local occupied = {}
    for _, building in ipairs(lists.buildings) do
      if ringsTouch(building.rings, rect) then occupied[#occupied + 1] = {bounds = ringBounds(building.rings), rings = building.rings} end
    end
    local cars, maxCars = 0, math.max(0, math.min(200, options.maxCars or 200))
    local function buildingAt(x, y)
      for _, building in ipairs(occupied) do
        local b = building.bounds
        if x >= b[1] and x <= b[3] and y >= b[2] and y <= b[4] and inside(building.rings, x, y) then return true end
      end
      return false
    end
    for _, area in ipairs(lists.areas) do
      local tags = area.tags or {}
      if cars < maxCars and (tags.amenity == 'parking' or area.kind == 'parking') and ringsTouch(area.rings, rect) then
        local frame = footprintFrame(cleanRing(area.rings[1]))
        if frame.area >= 120 then
          local row = 0
          for v = frame.v0 + 2.75, frame.v1 - 2.7, 5.5 do
            if row % 3 ~= 1 then
              for u = frame.u0 + 1.35, frame.u1 - 1.3, 2.7 do
                local x, y = u * frame.ax + v * frame.bx, u * frame.ay + v * frame.by
                local h = hash(tostring(area.id) .. ':' .. string.format('%.3f:%.3f', u, v))
                if h % 100 < 66 and x >= rect[1] - 3 and x < rect[3] + 3 and y >= rect[2] - 3 and y < rect[4] + 3
                  and not buildingAt(x, y) then
                  local yaw = math.atan2(frame.by, frame.bx) + (h % 2 == 0 and math.pi or 0)
                  local fits = true
                  for _, corner in ipairs({{-2.2, -.9}, {2.2, -.9}, {2.2, .9}, {-2.2, .9}}) do
                    local cx = x + corner[1] * math.cos(yaw) - corner[2] * math.sin(yaw)
                    local cy = y + corner[1] * math.sin(yaw) + corner[2] * math.cos(yaw)
                    if not inside(area.rings, cx, cy) then fits = false end
                  end
                  if fits then parkedCar(sceneDecor, x, y, ground(x, y) + .02, yaw, math.floor(h / 100) % 8); cars = cars + 1 end
                end
                if cars >= maxCars then break end
              end
            end
            row = row + 1
            pause()
            if cars >= maxCars then break end
          end
        end
      end
      if cars >= maxCars then break end
    end
  else
    -- A sparse, stable canopy keeps distant woodland readable without regenerating
    -- the dense near-tile lattice or adding detailed ground geometry.
    local planted = 0
    for _, area in ipairs(lists.areas) do
      if (area.kind == 'forest' or area.kind == 'park') and ringsTouch(area.rings, rect) then
        local box = ringBounds(area.rings)
        for gy = math.floor(math.max(rect[2], box[2]) / 80), math.ceil(math.min(rect[4], box[4]) / 80) do
          for gx = math.floor(math.max(rect[1], box[1]) / 80), math.ceil(math.min(rect[3], box[3]) / 80) do
            local h = hash('far:' .. gx .. ':' .. gy)
            local x, y = (gx + .3 + h % 400 / 1000) * 80, (gy + .3 + math.floor(h / 1000) % 400 / 1000) * 80
            if x >= rect[1] and x < rect[3] and y >= rect[2] and y < rect[4] and inside(area.rings, x, y) then
              static('far_tree_' .. planted, TREES[h % #TREES + 1], x, y, rawHeight(x, y), h % 6283 / 1000, 1.2)
              planted = planted + 1
              if planted >= 40 then break end
            end
          end
          pause()
          if planted >= 40 then break end
        end
      end
      if planted >= 40 then break end
    end
  end
  roads.finish()
  scene.finish()
  roadDecor.finish()
  sceneDecor.finish()
  for _, pair in ipairs({{roads, roadDecor}, {scene, sceneDecor}}) do
    for _, item in ipairs(pair[2].items) do item.decor = true; pair[1].items[#pair[1].items + 1] = item end
    for material in pairs(pair[2].used) do pair[1].used[material] = true end
  end
  for _, point in ipairs(options.far and {} or lists.points) do
    local p = point.pos
    if p and p[1] >= rect[1] and p[1] < rect[3] and p[2] >= rect[2] and p[2] < rect[4] then
      local shape = point.kind == 'tree' and TREE or point.kind == 'lamp' and LAMP
      if point.kind == 'stop' then shape = '/art/shapes/objects/s_busstop_jri.dae' end
      if shape then static('point_' .. tostring(point.id):gsub('[^%w_]', '_'), shape, p[1], p[2], ground(p[1], p[2]), 0) end
    end
    pause()
  end
  if not options.far then
    local blockers = {}
    for _, building in ipairs(lists.buildings) do
      if ringsTouch(building.rings, rect) then blockers[#blockers + 1] = {rings = building.rings, bounds = ringBounds(building.rings)} end
    end
    for _, area in ipairs(lists.areas) do
      if (area.kind == 'water' or area.kind == 'paved') and ringsTouch(area.rings, rect) then
        blockers[#blockers + 1] = {rings = area.rings, bounds = ringBounds(area.rings)}
      end
    end
    local blockedOps = 0
    local function blocked(x, y)
      local nearby = segmentBins[tostring(math.floor(x / 50)) .. ':' .. tostring(math.floor(y / 50))] or {}
      for _, segment in ipairs(nearby) do
        local a = segment.a
        local t = math.max(0, math.min(1, ((x - a[1]) * segment.dx + (y - a[2]) * segment.dy) / segment.len2))
        if (x - a[1] - t * segment.dx) ^ 2 + (y - a[2] - t * segment.dy) ^ 2 < (segment.width / 2 + 3.5) ^ 2 then return true end
        blockedOps = blockedOps + 1
        if blockedOps % 512 == 0 then pause() end
      end
      for _, blocker in ipairs(blockers) do
        local b = blocker.bounds
        if x >= b[1] and y >= b[2] and x <= b[3] and y <= b[4] and inside(blocker.rings, x, y) then return true end
        blockedOps = blockedOps + 1
        if blockedOps % 512 == 0 then pause() end
      end
      return false
    end
    local candidates, positions = {}, {}
    for _, area in ipairs(lists.areas) do
      local spacing = area.kind == 'forest' and 9 or area.kind == 'park' and 14 or area.kind == 'residential' and 22
      if spacing and ringsTouch(area.rings, rect) then
        local box = ringBounds(area.rings)
        for gy = math.floor(math.max(rect[2], box[2]) / spacing) - 1, math.ceil(math.min(rect[4], box[4]) / spacing) do
          for gx = math.floor(math.max(rect[1], box[1]) / spacing) - 1, math.ceil(math.min(rect[3], box[3]) / spacing) do
            local h = hash(tostring(gx) .. ':' .. tostring(gy) .. ':' .. spacing)
            local x = (gx + .5 + (h % 1000 / 1000 - .5) * .6) * spacing
            local y = (gy + .5 + (math.floor(h / 1000) % 1000 / 1000 - .5) * .6) * spacing
            local position = string.format('%.3f:%.3f', x, y)
            if x >= rect[1] and x < rect[3] and y >= rect[2] and y < rect[4]
              and not positions[position] and inside(area.rings, x, y) then
              positions[position] = true
              candidates[#candidates + 1] = {x = x, y = y, hash = h, key = position}
            end
          end
          pause()
        end
      end
    end
    -- Ranking by position spreads the 900-instance budget across a whole forest tile.
    table.sort(candidates, function(a, b) return a.hash == b.hash and a.key < b.key or a.hash < b.hash end)
    local planted, maxTrees = 0, math.max(0, math.min(900, options.maxTrees or 900))
    for _, candidate in ipairs(candidates) do
      if planted >= maxTrees then break end
      if not blocked(candidate.x, candidate.y) then
        local h = candidate.hash
        static('tree_' .. planted, TREES[h % #TREES + 1], candidate.x, candidate.y, ground(candidate.x, candidate.y),
          h % 6283 / 1000, .8 + math.floor(h / 1000) % 400 / 1000)
        planted = planted + 1
      end
      pause()
    end
    local lamp = 0
    for _, road in ipairs(lists.roads) do
      local tags, points = road.tags or {}, road.points or {}
      local major = tags.highway == 'primary' or tags.highway == 'secondary' or tags.highway == 'tertiary' or tags.highway == 'trunk'
      if tags.lit ~= 'no' and (tags.lit == 'yes' or major) then
        local distance = 0
        for index = 1, #points - 1 do
          local a, b = points[index], points[index + 1]
          local dx, dy = b[1] - a[1], b[2] - a[2]
          local length = math.sqrt(dx * dx + dy * dy)
          if length > EPS then
            local nextDistance = math.ceil((distance + EPS) / 30) * 30
            while nextDistance < distance + length - EPS do
              local t, offset = (nextDistance - distance) / length, (road.width or 7) / 2 + 1.1
              local x, y = a[1] + t * dx + dy / length * offset, a[2] + t * dy - dx / length * offset
              if x >= rect[1] and x < rect[3] and y >= rect[2] and y < rect[4] then
                static('lamp_' .. lamp, LAMP, x, y, (a[3] or 0) + t * ((b[3] or 0) - (a[3] or 0)) + .2, math.atan2(dx, dy))
                lamp = lamp + 1
              end
              nextDistance = nextDistance + 30
            end
            distance = distance + length
          end
          if index % 32 == 0 then pause() end
        end
      end
      pause()
    end
  end
  local function manifest(collector, layer)
    local mats, total = {}, 0
    for material in pairs(collector.used) do mats[#mats + 1] = material end
    table.sort(mats)
    for _, item in ipairs(collector.items) do total = total + (item.triangles or 0) end
    return {version = 1, generator = 'earthng-lua-v1', source = options.regionId, layer = layer,
      tile = {i, j}, rect = rect, materials = options.materials or '/earthng/planet/materials.json',
      items = collector.items, mats = mats, triangles = total, packs = options.packs,
      warmShapes = {TREES[1], TREES[2], TREES[3], TREES[4], LAMP, '/art/shapes/objects/s_busstop_jri.dae'}}
  end
  local roadManifest, sceneManifest = manifest(roads, 'road'), manifest(scene, 'scene')
  roadManifest.spawn = bestSpawn or {pos = {target[1], target[2], ground(target[1], target[2]) + ROAD_LIFT}, yaw = 0}
  roadManifest.drivable = bestSpawn ~= nil
  if options.far then
    sceneManifest.layer = 'far'
    for _, item in ipairs(sceneManifest.items) do item.decor, item.layer = true, 'far' end
    return roadManifest, manifest({used = {}, items = {}}, 'scene'), sceneManifest, nav
  end
  return roadManifest, sceneManifest, nil, nav
end

M.build = build
M.triangulate = triangulate
M.clip = clip
return M
