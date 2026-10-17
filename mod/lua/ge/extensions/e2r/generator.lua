-- Runtime geometry from compact source features. Nothing is written to disk, and no
-- service or executable is required. All coordinates remain in the source region's frame.
local M = {}
local EPS = 1e-8
local ROAD_LIFT = 0.10
local floor = math.floor
local NONE = {}
-- Weld keys: millimetres from the tile centre (|x|, |y| < 1048 m) and 1/1024 of a UV repeat
-- (|u|, |v| < 32768 repeats: region UVs are absolute, ~27000 at 80 km with a 3 m repeat).
local WELD, WELD2 = 2 ^ 20, 2 ^ 21
local UVQ, UVQ2 = 2 ^ 25, 2 ^ 26
-- Road and ground shaping; the road quality bench (earthng/road_quality.py) overrides them
-- through options.tune. Metres.
local TUNE = {roadLift = ROAD_LIFT, groundDrop = .25, pullFull = 12, pullZero = 70, shoulder = 3, margin = .12,
  terrainStep = 25, sidewalk = 2, curb = .25, vergeCut = 1, vergeSink = 0, pressEven = 1, mitre = 2,
  simplifyXY = 0, simplifyZ = .02, fenceStep = 4, wallThick = .2, fenceThick = .08}
-- Covers that take a whole ground triangle over (lower rank wins where they overlap).
local CLAIM = {paved = 1, water = 2, glacier = 3, bare_rock = 4, scree = 5, beach = 6,
  wetland = 7, orchard = 8, vineyard = 9, farmland = 10, meadow = 11, heath = 12,
  scrub = 13, forest = 14, park = 15, bare_ground = 16}
local COVER_SCALE = {ground = 24, forest = 24, park = 24, meadow = 24, orchard = 24,
  scrub = 24, heath = 24, glacier = 24, farmland = 12, vineyard = 12, bare_rock = 12,
  scree = 12, beach = 12, wetland = 12, bare_ground = 12}
local ABIN = 12.5      -- m: bins of asphalt pieces that sidewalks and verges are cut by
local SEA_ABOVE = 0.3   -- the sea plane over the DEM's flat sea surface (no z-fighting)
local ECA_TREE = '/levels/east_coast_usa/art/shapes/trees/'
local LAMP = '/art/shapes/objects/lamp1.dae'
local BUS_STOP = '/art/shapes/objects/s_busstop_jri.dae'
-- East Coast shapes have billboard LODs, so a crown stays up past the mesh pop.
-- The foliage-library oak is the last slot: it still draws if a level mesh is missing.
local TREES = {
  ECA_TREE .. 'trees_oak/tree_oak_sml_a.dae',
  ECA_TREE .. 'trees_oak/tree_oak_forest_a.dae',
  ECA_TREE .. 'trees_oak/tree_oak_large_b.dae',
  ECA_TREE .. 'trees_beech/tree_beech_small_c.dae',
  ECA_TREE .. 'trees_beech/tree_beech_forest_a.dae',
  ECA_TREE .. 'trees_aspen/tree_aspen_small_a.dae',
  ECA_TREE .. 'trees_aspen/tree_aspen_forest_b.dae',
  '/assets/meshes/foliage/trees_library/oak/tree_oak_sml_a.dae'}
-- A single OSM tree node (not the forest lattice). The first East Coast oak has a billboard.
local TREE = TREES[1]
local CONIFERS = {'/levels/italy/art/shapes/trees/trees_italy/scots_pine.dae',
  '/levels/italy/art/shapes/trees/trees_italy/scots_pine_filler.dae'}
local BUSHES = {
  ECA_TREE .. 'trees_beech/tree_beech_bush_b.dae',
  ECA_TREE .. 'trees_oak/tree_oak_bush_c.dae',
  '/assets/meshes/foliage/trees_library/beech/tree_beech_bush_a.dae',
  '/assets/meshes/foliage/trees_library/birch/tree_aspen_bush_a.dae'}
-- Stock tufts and stones from the player's install. GroundCover needs a TerrainBlock,
-- so near scatter uses these meshes. Nothing is copied into the mod.
local GRASS = {'/levels/east_coast_usa/art/shapes/groundcover/3d_grass_test/s_grass_green_short_01.dae',
  '/levels/east_coast_usa/art/shapes/groundcover/3d_grass_test/s_grass_green_short_02.dae',
  '/levels/east_coast_usa/art/shapes/groundcover/3d_grass_test/s_grass_green_short_03.dae',
  '/levels/east_coast_usa/art/shapes/groundcover/3d_grass_test/s_grass_green_long_01.dae',
  '/levels/east_coast_usa/art/shapes/groundcover/3d_grass_test/s_grass_green_long_02.dae'}
-- Native blade span and the lowest vertex, both in metres, from the position
-- arrays. A shared scale of 9 made the long meshes several metres tall.
local GRASS_MESH = {
  [GRASS[1]] = {span = 0.265, bottom = -0.038},
  [GRASS[2]] = {span = 0.258, bottom = -0.033},
  [GRASS[3]] = {span = 0.255, bottom = -0.019},
  [GRASS[4]] = {span = 0.514, bottom = -0.023},
  [GRASS[5]] = {span = 0.530, bottom = -0.046},
}
local ROCKS = {'/levels/east_coast_usa/art/shapes/rocks/eca_rock_small.dae',
  '/levels/east_coast_usa/art/shapes/rocks/eca_rock_small_2.dae',
  '/levels/east_coast_usa/art/shapes/rocks/eca_rock_small_3.dae',
  '/levels/east_coast_usa/art/shapes/rocks/eca_rock_small_4.dae',
  '/levels/east_coast_usa/art/shapes/rocks/eca_rock_medium_1.dae'}

local function hash(value)
  local h = 17
  for index = 1, #value do h = (h * 131 + value:byte(index)) % 2147483647 end
  return h
end

local function lerp(a, b, t)
  local n = math.max(#a, #b)
  -- A per-corner normal sits at [6..8]. A nil UV in [4] or [5] would stop `#` before it,
  -- and the clipped border would fall back to a face normal that the neighbour does not share.
  if type(a[6]) == 'number' or type(b[6]) == 'number' or type(a[9]) == 'number' or type(b[9]) == 'number' then
    n = math.max(n, 9)
  end
  local out = {}
  for k = 1, n do out[k] = (a[k] or 0) + ((b[k] or 0) - (a[k] or 0)) * t end
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
-- Edges (sorted by their low y) and vertex heights of rings, made once for a polygon that is
-- swept many times (row by row).
local function prepareRings(rings)
  local edges, vys, seen = {}, {}, {}
  for _, raw in ipairs(rings or {}) do
    local ring = cleanRing(raw)
    for k, a in ipairs(ring) do
      local b = ring[k % #ring + 1]
      if not seen[a[2]] then vys[#vys + 1], seen[a[2]] = a[2], true end
      if math.abs(a[2] - b[2]) > EPS then
        edges[#edges + 1] = {a, b, lo = math.min(a[2], b[2]), hi = math.max(a[2], b[2]), index = #edges + 1}
      end
    end
  end
  table.sort(vys)
  table.sort(edges, function(a, b) return a.lo == b.lo and a.index < b.index or a.lo < b.lo end)
  return {prepared = true, edges = edges, vys = vys}
end

-- quads: band trapezoids {a, b, c, d} instead of their two triangles (to be clipped further).
-- rings may be prepareRings' result.
local function triangulate(rings, pause, rect, quads)
  local prep = rings and rings.prepared and rings or prepareRings(rings)
  local ys, result = {}, {}
  if rect then ys = {rect[2], rect[4]} end
  for _, y in ipairs(prep.vys) do
    if not rect or (y > rect[2] and y < rect[4]) then ys[#ys + 1] = y end
  end
  local order = {}
  for _, edge in ipairs(prep.edges) do
    if rect and edge.lo >= rect[4] then break end
    if not rect or edge.hi > rect[2] then order[#order + 1] = edge end
  end
  table.sort(ys)
  -- A sweep: the edges crossing a band are those begun below its middle and not yet ended,
  -- kept in an active list instead of testing every edge of a big forest in every band.
  local function at(edge, y)
    local a, b = edge[1], edge[2]
    return a[1] + (b[1] - a[1]) * ((y - a[2]) / (b[2] - a[2]))
  end
  local function on(edge, y)
    return lerp(edge[1], edge[2], (y - edge[1][2]) / (edge[2][2] - edge[1][2]))
  end
  -- Band pieces wholly left or right of the tile would be clipped to nothing.
  local left, right = rect and rect[1] - EPS, rect and rect[3] + EPS
  local active, nextEdge = {}, 1
  local byX = function(a, b) return a.x == b.x and a.index < b.index or a.x < b.x end
  for n = 1, #ys - 1 do
    local low, high = ys[n], ys[n + 1]
    local mid = (low + high) * 0.5
    while nextEdge <= #order and order[nextEdge].lo <= mid do
      active[#active + 1] = order[nextEdge]
      nextEdge = nextEdge + 1
    end
    local cross, alive = {}, 0
    for k = 1, #active do
      local edge = active[k]
      if edge.hi > mid then
        alive = alive + 1
        active[alive] = edge
        cross[#cross + 1] = {x = at(edge, mid), edge = edge, index = edge.index}
      end
    end
    for k = #active, alive + 1, -1 do active[k] = nil end
    table.sort(cross, byX)
    for k = 1, #cross - 1, 2 do
      local e, f = cross[k].edge, cross[k + 1].edge
      local outside = false
      if rect then
        local xa, xb, xc, xd = at(e, low), at(f, low), at(f, high), at(e, high)
        outside = math.max(xa, xb, xc, xd) < left or math.min(xa, xb, xc, xd) > right
      end
      if not outside then
        local a, b, c, d = on(e, low), on(f, low), on(f, high), on(e, high)
        if quads then
          if b[1] - a[1] > EPS or c[1] - d[1] > EPS then result[#result + 1] = {a, b, c, d} end
        else
          if b[1] - a[1] > EPS then result[#result + 1] = {a, b, c} end
          if c[1] - d[1] > EPS then result[#result + 1] = {a, c, d} end
        end
      end
    end
    if pause then pause() end
  end
  return result
end

-- Clip in XY while interpolating elevation and optional UVs. Identical world-space
-- intersections on adjacent tiles prevent cracks and preserve road profiles at their border.
local function clip(poly, rect)
  -- Wholly inside (the usual case): every pass would copy it unchanged.
  local x0, y0, x1, y1 = rect[1] - EPS, rect[2] - EPS, rect[3] + EPS, rect[4] + EPS
  local inside = true
  for k = 1, #poly do
    local p = poly[k]
    local x, y = p[1], p[2]
    if x < x0 or x > x1 or y < y0 or y > y1 then inside = false; break end
  end
  if inside then return poly end
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

-- The part of a polyline from distance a after its start to distance b before its end (z and
-- any other coordinates interpolated); {} when nothing is left.
local function trimLine(points, a, b)
  local dist = {0}
  for k = 2, #points do
    local p, q = points[k - 1], points[k]
    dist[k] = dist[k - 1] + math.sqrt((q[1] - p[1]) ^ 2 + (q[2] - p[2]) ^ 2)
  end
  local s0, s1 = a, dist[#points] - b
  if s1 - s0 < .05 then return {} end
  local function at(d)
    for k = 1, #points - 1 do
      if d <= dist[k + 1] or k == #points - 1 then
        return lerp(points[k], points[k + 1], (d - dist[k]) / math.max(EPS, dist[k + 1] - dist[k]))
      end
    end
  end
  local out = {at(s0)}
  for k = 2, #points - 1 do
    if dist[k] > s0 + 1e-3 and dist[k] < s1 - 1e-3 then out[#out + 1] = points[k] end
  end
  out[#out + 1] = at(s1)
  return out
end

-- Metres of centre-line rise and fall. Grade 0–1 stays on the surveyed profile so a worn
-- city street does not grow new vertices. Grade 2–5 samples every 8 m; the first and last
-- 6 m of each span stay put so a junction mouth still meets the fan.
local WEAR_AMP = {0, 0, .06, .10, .14, .18}
local function densifyWear(points, grade)
  if grade < 2 or #points < 2 then return points end
  local amp, step = WEAR_AMP[grade + 1] or 0, 8
  local out = {points[1]}
  local function push(p)
    local q = out[#out]
    if not q or math.abs(q[1] - p[1]) + math.abs(q[2] - p[2]) > 1e-3 then out[#out + 1] = p end
  end
  for k = 1, #points - 1 do
    local a, b = points[k], points[k + 1]
    local dx, dy = b[1] - a[1], b[2] - a[2]
    local dz = (b[3] or 0) - (a[3] or 0)
    local length = math.sqrt(dx * dx + dy * dy)
    local n = math.max(1, math.ceil(length / step))
    for s = 1, n - 1 do
      local t = s / n
      local along = math.min(t, 1 - t) * length
      local p = {a[1] + dx * t, a[2] + dy * t, (a[3] or 0) + dz * t}
      if along > 6 then
        local wobble = (hash('wear:' .. floor(p[1] + .5) .. ':' .. floor(p[2] + .5)) % 1000 / 1000 - .5) * 2 * amp
        p[3] = p[3] + wobble
      end
      push(p)
    end
    push(b)
  end
  return out
end

-- Douglas-Peucker in plan and height: a knot stays when dropping it moves the line more than
-- xy metres sideways or z metres up or down (the sources carry a profile knot every few metres).
local function simplify(points, xy, z)
  local n = #points
  if n <= 2 then return points end
  local keep = {[1] = true, [n] = true}
  local stack = {{1, n}}
  while #stack > 0 do
    local span = table.remove(stack)
    local i0, i1 = span[1], span[2]
    local a, b = points[i0], points[i1]
    local dx, dy = b[1] - a[1], b[2] - a[2]
    local len2 = dx * dx + dy * dy
    local worst, at = 0, nil
    for k = i0 + 1, i1 - 1 do
      local p = points[k]
      local t = len2 > 0 and math.max(0, math.min(1, ((p[1] - a[1]) * dx + (p[2] - a[2]) * dy) / len2)) or 0
      local ex, ey = a[1] + dx * t - p[1], a[2] + dy * t - p[2]
      local ez = (a[3] or 0) + ((b[3] or 0) - (a[3] or 0)) * t - (p[3] or 0)
      local e = math.max(math.sqrt(ex * ex + ey * ey) / xy, math.abs(ez) / z)
      if e > worst then worst, at = e, k end
    end
    if at and worst > 1 then
      keep[at] = true
      stack[#stack + 1], stack[#stack + 2] = {i0, at}, {at, i1}
    end
  end
  local out = {}
  for k = 1, n do if keep[k] then out[#out + 1] = points[k] end end
  return out
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

-- 0 smooth asphalt, 1 worn asphalt, 2 gravel, 3 dirt, 4 broken, 5 overgrown.
-- Explicit surface wins over the highway class. tracktype and a bad smoothness
-- only make a minor road worse. Motorway–secondary ignore smoothness alone.
local function roadGrade(tags)
  tags = tags or {}
  local highway = (tags.highway or ''):gsub('_link$', '')
  local surface, grade = tags.surface, 0
  if surface == 'asphalt' or surface == 'concrete' or surface == 'paved' or surface == 'chipseal' then
    grade = 0
  elseif surface == 'cobblestone' or surface == 'sett' or surface == 'paving_stones' or surface == 'unhewn_cobblestone' then
    grade = 1
  elseif surface == 'gravel' or surface == 'fine_gravel' or surface == 'pebblestone' or surface == 'compacted' then
    grade = 2
  elseif surface == 'dirt' or surface == 'ground' or surface == 'earth' or surface == 'unpaved'
      or surface == 'mud' or surface == 'soil' or surface == 'sand' then
    grade = 3
  elseif surface == 'grass' or surface == 'grass_paver' then
    grade = 5
  elseif highway == 'track' then
    grade = 3
  end
  local track = tags.tracktype
  if track == 'grade1' then grade = math.max(grade, 1)
  elseif track == 'grade2' then grade = math.max(grade, 2)
  elseif track == 'grade3' then grade = math.max(grade, 3)
  elseif track == 'grade4' then grade = math.max(grade, 4)
  elseif track == 'grade5' then grade = 5 end
  local smooth = tags.smoothness
  local major = highway == 'motorway' or highway == 'trunk' or highway == 'primary' or highway == 'secondary'
  if not major and grade == 0 and (smooth == 'bad' or smooth == 'very_bad' or smooth == 'horrible'
      or smooth == 'very_horrible' or smooth == 'impassable') then
    grade = 1
  end
  if highway == 'track' and (smooth == 'horrible' or smooth == 'very_horrible') then grade = math.max(grade, 4) end
  if highway == 'track' and smooth == 'impassable' then grade = 5 end
  return grade
end

local ROAD_GRADE_MAT = {'road_worn', 'road_gravel', 'road_dirt', 'road_broken', 'road_track'}
local TRACK_WIDTH = {4.0, 3.8, 3.5, 3.2, 2.8, 2.5}

local function collector(rect, key, layer, pause)
  local origin = {(rect[1] + rect[3]) * 0.5, (rect[2] + rect[4]) * 0.5, 0}
  local items, groups, used, triangles, operations = {}, {}, {}, 0, 0
  local function flush()
    local meshes, mats = {}, {}
    for material in pairs(groups) do mats[#mats + 1] = material end
    table.sort(mats)
    for _, material in ipairs(mats) do
      local mesh = groups[material]
      mesh.vmap, mesh.nmap, mesh.umap, mesh.fmap = nil, nil, nil, nil
      meshes[#meshes + 1] = mesh
    end
    if #meshes > 0 then
      items[#items + 1] = {kind = 'proc', name = 'e2rl_' .. key .. '_' .. layer .. '_' .. #items,
        pos = origin, meshes = meshes, triangles = triangles, mats = mats, layer = layer}
    end
    groups, triangles = {}, 0
  end
  -- Faces index positions, normals and UVs separately (BeamNG ProceduralMesh, like OBJ), so each
  -- is welded on its own: a ground node is one position for its six triangles, a flat road one
  -- normal for the whole piece, and a corner {v, n, u} seen again reuses its face table. The
  -- geometry is unchanged; the Lua tables (and the garbage the game's collector walks) are not.
  local function vertexIndex(mesh, p)
    local x, y, z = p[1] - origin[1], p[2] - origin[2], p[3] or 0
    local xq, yq = floor(x * 1000 + .5), floor(y * 1000 + .5)
    if xq < -WELD or xq >= WELD or yq < -WELD or yq >= WELD then
      local verts = mesh.verts
      verts[#verts + 1] = {x = x, y = y, z = z}
      return #verts - 1
    end
    local key = (xq + WELD) * WELD2 + yq + WELD
    local zs = mesh.vmap[key]
    if not zs then zs = {}; mesh.vmap[key] = zs end
    local zq = floor(z * 1000 + .5)
    local index = zs[zq]
    if not index then
      local verts = mesh.verts
      index = #verts
      verts[index + 1] = {x = x, y = y, z = z}
      zs[zq] = index
    end
    return index
  end
  local function normalIndex(mesh, nx, ny, nz)
    local key = floor((nx + 1) * 10000 + .5) * 1073741824 + floor((ny + 1) * 10000 + .5) * 32768 + floor((nz + 1) * 10000 + .5)
    local index = mesh.nmap[key]
    if not index then
      local normals = mesh.normals
      index = #normals
      normals[index + 1] = {x = nx, y = ny, z = nz}
      mesh.nmap[key] = index
    end
    return index
  end
  local function uvIndex(mesh, u, v)
    local uq, vq = floor(u * 1024 + .5), floor(v * 1024 + .5)
    local uvs = mesh.uvs
    if uq < -UVQ or uq >= UVQ or vq < -UVQ or vq >= UVQ then
      uvs[#uvs + 1] = {u = u, v = v}
      return #uvs - 1
    end
    local key = (uq + UVQ) * UVQ2 + vq + UVQ
    local index = mesh.umap[key]
    if not index then
      index = #uvs
      uvs[index + 1] = {u = u, v = v}
      mesh.umap[key] = index
    end
    return index
  end
  local function corner(mesh, p, n, scale, axis)
    local v = vertexIndex(mesh, p)
    local pu, pv = p[1], p[2]
    -- Dominant-axis box projection for rock; world-locked and independent of
    -- tile origin. It uses ordinary mesh UVs, no undocumented shader API.
    if axis == 'x' then pu, pv = p[2], p[3] or 0
    elseif axis == 'y' then pu, pv = p[1], p[3] or 0 end
    local u = uvIndex(mesh, p[4] or pu / scale, p[5] or pv / scale)
    local key = (v * 8192 + n) * 8192 + u
    local face = mesh.fmap[key]
    if not face then face = {v = v, n = n, u = u}; mesh.fmap[key] = face end
    local faces = mesh.faces
    faces[#faces + 1] = face
  end
  local scales = {}
  local function triangle(kind, a, b, c, up)
    local a3, b3, c3 = a[3] or 0, b[3] or 0, c[3] or 0
    local ux, uy, uz = b[1] - a[1], b[2] - a[2], b3 - a3
    local vx, vy, vz = c[1] - a[1], c[2] - a[2], c3 - a3
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
      mesh = {verts = {}, normals = {}, uvs = {}, faces = {}, material = material,
        vmap = {}, nmap = {}, umap = {}, fmap = {}}
      groups[material], used[material] = mesh, true
    end
    local scale = scales[kind]
    if not scale then
      scale = COVER_SCALE[kind] or 6
      if kind == 'sidewalk' or kind:find('wall_', 1, true) == 1 then scale = 3 end
      scales[kind] = scale
    end
    local n = normalIndex(mesh, nx, ny, nz)
    -- Ground passes a per-corner normal in p[6..8] (nx can be 0, so the type is the test).
    -- Walls, curbs and roads leave it unset and keep this face normal.
    local function cornerNormal(p)
      if type(p[6]) == 'number' and type(p[7]) == 'number' and type(p[8]) == 'number' then
        return normalIndex(mesh, p[6], p[7], p[8])
      end
      return n
    end
    -- BeamNG ProceduralMesh expects clockwise face indices and zero-based indices.
    local axis
    if kind == 'bare_rock' or kind == 'scree' then
      local ax, ay, az = math.abs(nx), math.abs(ny), math.abs(nz)
      if ax > az and ax >= ay then axis = 'x' elseif ay > az then axis = 'y' end
    end
    corner(mesh, a, cornerNormal(a), scale, axis)
    corner(mesh, c, cornerNormal(c), scale, axis)
    corner(mesh, b, cornerNormal(b), scale, axis)
    triangles, operations = triangles + 1, operations + 1
    if operations % 64 == 0 then pause() end
  end
  local function polygon(kind, polygon, up)
    local clipped = clip(polygon, rect)
    local first = clipped[1]
    for k = 2, #clipped - 1 do triangle(kind, first, clipped[k], clipped[k + 1], up) end
  end
  return {items = items, used = used, polygon = polygon, triangle = triangle, finish = flush}
end

local SPEED = {motorway = 110, trunk = 90, primary = 60, secondary = 60, tertiary = 50,
  unclassified = 50, residential = 40, living_street = 20}
local DRIVE = {motorway = 1, trunk = 1, primary = 1, secondary = 1, tertiary = .9,
  unclassified = .7, residential = .7, living_street = .4}
local NAV_MIN = .5     -- m: the shortest navgraph edge
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
  -- Knots closer than NAV_MIN are one: the game's random route walk (graphpath getRandomPathG,
  -- traffic respawns) adds edge lengths until it has enough, and on a piece of graph of only
  -- zero-length edges (repeated source points) it never had, and froze the game.
  local ordered = {}
  for index = 1, #points do
    local p = points[flip and (#points - index + 1) or index]
    local q = ordered[#ordered]
    if not q or (p[1] - q[1]) ^ 2 + (p[2] - q[2]) ^ 2 >= NAV_MIN * NAV_MIN then ordered[#ordered + 1] = p
    elseif index == #points then ordered[#ordered] = p end   -- the end node keeps its own point
  end
  if #ordered < 2 then return nil end
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

-- Which sides get a sidewalk (1 left, -1 right of the direction): city streets by default unless
-- tagged otherwise; one carriageway of a divided road only on its right.
local WALKS = {primary = true, secondary = true, tertiary = true, residential = true, living_street = true, unclassified = true}
local function walkSides(tags)
  if roadGrade(tags) >= 2 then return {} end
  if not WALKS[tags.highway] then return {} end
  local v = tags.sidewalk
  if v == nil and (tags.oneway == 'yes' or tags.oneway == '1') then return {[-1] = true} end
  if v == 'no' or v == 'none' then return {} end
  if v == 'left' then return {[1] = true} end
  if v == 'right' then return {[-1] = true} end
  return {[1] = true, [-1] = true}
end

local function wallMaterial(building, look)
  local tags = building.tags or {}
  local h = hash(tostring(building.id or ''))
  local height = building.height or number(tags.height, number(tags['building:levels'], 3) * 3)
  local material = tags['building:material'] or tags['facade:material']
  local b = tags.building
  look = type(look) == 'table' and look or NONE
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
  elseif look.buildings == 'panel' then family = height >= 26 and (h % 3 ~= 0 and loggia or panel) or panel
  elseif look.buildings == 'brick' then family = brick
  elseif look.buildings == 'plaster' then family = plaster
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

-- Per source cell, once: its features deduplicated by id and sorted. The renderer's cell cache
-- keeps the cell table, so the four tiles of a 1 km cell share this work. Weak keys: nothing
-- is written into the source tables.
local prepared = setmetatable({}, {__mode = 'k'})
local KINDS = {'roads', 'junctions', 'buildings', 'areas', 'lines', 'points'}

local function collect(cells, pause)
  local lists = {roads = {}, junctions = {}, buildings = {}, areas = {}, lines = {}, points = {}}
  local seen, terrain = {}, {}
  for _, cell in ipairs(cells or {}) do
    if cell.terrain then terrain[#terrain + 1] = cell.terrain end
    for _, kind in ipairs(KINDS) do
      local output = lists[kind]
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
  -- highway=track is stored as a path line. Draw it as a drivable ribbon. A bridge or
  -- tunnel stays a thin path: its deck is not the ground profile.
  local lines = {}
  for _, line in ipairs(lists.lines) do
    local tags = line.tags or {}
    local highway = (tags.highway or ''):gsub('_link$', '')
    if line.kind == 'path' and highway == 'track' and tags.bridge ~= 'yes' and tags.tunnel ~= 'yes' and tags.area ~= 'yes' then
      local grade = roadGrade(tags)
      lists.roads[#lists.roads + 1] = {id = 'track:' .. tostring(line.id), points = line.points,
        width = number(tags.width, TRACK_WIDTH[grade + 1] or 3.2), tags = tags}
    else
      lines[#lines + 1] = line
    end
  end
  lists.lines = lines
  for _, list in pairs(lists) do
    local ids = {}
    for _, feature in ipairs(list) do ids[feature] = tostring(feature.id or '') end
    table.sort(list, function(a, b) return ids[a] < ids[b] end)
  end
  return lists, terrain
end

local function build(cells, i, j, options)
  options = options or {}
  local pause = options.pause or options.yield or function() end
  local size = options.tileSize or 500
  -- far: the 3 km ring. horizon: DEM-only past it. Both shape the ground like near (so the
  -- shared edge matches) but emit no road mesh, nav or collision. Decor is the optional
  -- far buildings and trees; a horizon tile never has it.
  local distant = options.far or options.horizon
  local visualRoads = not distant
  local decor = not options.horizon and (not options.far or options.farDecor ~= false)
  local layerName = options.horizon and 'horizon' or (options.far and 'far' or 'scene')
  local rect, key = {i * size, j * size, (i + 1) * size, (j + 1) * size}, tostring(i) .. '_' .. tostring(j)
  local target = options.center or {(i + .5) * size, (j + .5) * size}
  local lists, terrain
  local single = cells and #cells == 1 and cells[1]
  if single and prepared[single] then
    lists, terrain = prepared[single][1], prepared[single][2]
  else
    lists, terrain = collect(cells, pause)
    if single then prepared[single] = {lists, terrain} end
  end
  local roads, scene = collector(rect, key, 'road', pause), collector(rect, key, layerName, pause)
  local roadDecor, sceneDecor = collector(rect, key .. '_marks', 'road', pause), collector(rect, key .. '_cars', 'scene', pause)
  -- mark(name): the stage that just ended (the road quality bench times them)
  local mark = options.mark or function() end
  local look = type(options.look) == 'table' and options.look or NONE
  local function lookOf(slot)
    local value = look[slot]
    return type(value) == 'string' and value or nil
  end
  local asphalt = lookOf('asphalt')
  local minorRoad, majorRoad = 'road', 'road_major'
  if asphalt == 'worn' then minorRoad, majorRoad = 'road_worn', 'road'
  elseif asphalt == 'fresh' then minorRoad, majorRoad = 'road_major', 'road_major' end
  local vergeKind = 'ground'
  local vergeChoice = lookOf('verge')
  if vergeChoice == 'dirt' then vergeKind = 'bare_ground'
  elseif vergeChoice == 'dry' then vergeKind = 'heath'
  elseif vergeChoice == 'gravel' then vergeKind = 'scree' end
  local markChoice = lookOf('markings')
  local markKind = 'mark'
  if markChoice == 'yellow' then markKind = 'mark_yellow'
  elseif markChoice == 'none' then markKind = nil end
  local staticChoice = lookOf('statics')
  local wantLamps = staticChoice ~= 'sparse' and staticChoice ~= 'none'
  local wantStops = staticChoice ~= 'none'
  local tune = {}
  for name, value in pairs(TUNE) do tune[name] = value end
  if options.terrainStep then tune.terrainStep = options.terrainStep end
  for name, value in pairs(options.tune or NONE) do tune[name] = value end
  local lift, step = tune.roadLift, tune.terrainStep
  -- A road shapes the ground up to pullZero from its edge, and the ground lattice reaches one
  -- step past the tile (its border nodes are lowered for roads in the neighbour's cells too).
  local reach = tune.pullZero + step + 5
  local segments, segmentBins, nav, bestSpawn, bestDistance = {}, {}, {segments = {}}, nil, math.huge
  for _, road in ipairs(lists.roads) do
    local tags, points, width = road.tags or {}, road.points or {}, math.max(.5, math.min(80, road.width or 7))
    for index = 1, #points - 1 do
      local a, b = points[index], points[index + 1]
      local dx, dy = b[1] - a[1], b[2] - a[2]
      local length = math.sqrt(dx * dx + dy * dy)
      if length > EPS then
        local bounds = {math.min(a[1], b[1]), math.min(a[2], b[2]), math.max(a[1], b[1]), math.max(a[2], b[2])}
        if boundsTouch(rect, bounds, width / 2 + reach) then
          local segment = {a = a, b = b, dx = dx, dy = dy, len2 = length * length, width = width,
            surface = tags.bridge ~= 'yes' and tags.tunnel ~= 'yes' and tags.layer ~= '-1'}
          segments[#segments + 1] = segment
          local margin = width / 2 + tune.pullZero
          for by = math.floor((bounds[2] - margin) / 50), math.floor((bounds[4] + margin) / 50) do
            for bx = math.floor((bounds[1] - margin) / 50), math.floor((bounds[3] + margin) / 50) do
              local binKey = (bx + 32768) * 65536 + by + 32768
              local bin = segmentBins[binKey] or {}
              segmentBins[binKey], bin[#bin + 1] = bin, segment
            end
          end
        end
      end
      if index % 32 == 0 then pause() end
    end
    local navigation = visualRoads and navSegment(road)
    if navigation then nav.segments[#nav.segments + 1] = navigation end
    pause()
  end

  -- One world-space sample. No grids: 0, so a near tile without a DEM stays the flat
  -- lattice the roads already sit on. Grids that miss the point: nil, so a distant mesh
  -- omits the quad instead of dropping a cliff to z = 0. A point on a shared cell edge
  -- belongs to every grid that contains it; the lower rect origin wins, so the order the
  -- cells were loaded in cannot move the surface.
  local function demAt(x, y)
    if #terrain == 0 then return 0 end
    local best, bestX, bestY
    for _, grid in ipairs(terrain) do
      local box = grid.rect
      if box and x >= box[1] - EPS and x <= box[3] + EPS and y >= box[2] - EPS and y <= box[4] + EPS
          and grid.nx >= 2 and grid.ny >= 2 and grid.heights then
        local ox, oy = box[1], box[2]
        if not best or ox < bestX - EPS or (math.abs(ox - bestX) <= EPS and oy < bestY) then
          best, bestX, bestY = grid, ox, oy
        end
      end
    end
    if not best then return nil end
    local grid, box = best, best.rect
    local gx = math.max(0, math.min(grid.nx - 1, (x - box[1]) / (box[3] - box[1]) * (grid.nx - 1)))
    local gy = math.max(0, math.min(grid.ny - 1, (y - box[2]) / (box[4] - box[2]) * (grid.ny - 1)))
    local ix, iy = math.min(grid.nx - 2, math.floor(gx)), math.min(grid.ny - 2, math.floor(gy))
    local tx, ty = gx - ix, gy - iy
    local heights, row = grid.heights, iy * grid.nx + ix + 1
    local z00, z10 = heights[row] or 0, heights[row + 1] or 0
    local z01, z11 = heights[row + grid.nx] or 0, heights[row + grid.nx + 1] or 0
    return (z00 * (1 - tx) + z10 * tx) * (1 - ty) + (z01 * (1 - tx) + z11 * tx) * ty
  end
  local function rawHeight(x, y)
    local z = demAt(x, y)
    if z == nil then return 0 end
    return z
  end
  -- The DEM pulled to the roads round a point (fast.ground_corners): fully at a road's profile
  -- within pullFull of its edge, the DEM alone from pullZero. Nearer roads weigh more in the
  -- height, the strongest pull wins. Bridges and tunnels do not pull.
  local heightOperations = 0
  local span = math.max(1, tune.pullZero - tune.pullFull)
  local function pulled(x, y)
    local base = demAt(x, y)
    if base == nil then return nil end
    local nearby = segmentBins[(floor(x / 50) + 32768) * 65536 + floor(y / 50) + 32768] or NONE
    local sa, sz, pull = 0, 0, 0
    for _, segment in ipairs(nearby) do
      if segment.surface then
        local a = segment.a
        local t = math.max(0, math.min(1, ((x - a[1]) * segment.dx + (y - a[2]) * segment.dy) / segment.len2))
        local px, py = a[1] + t * segment.dx, a[2] + t * segment.dy
        local d = math.max(0, math.sqrt((x - px) ^ 2 + (y - py) ^ 2) - segment.width / 2)
        if d < tune.pullZero then
          local s = math.max(0, math.min(1, (tune.pullZero - d) / span))
          local w = s * s * (3 - 2 * s)
          local weight = w / math.max(d, 1)
          sa, sz = sa + weight, sz + weight * ((a[3] or 0) + t * ((segment.b[3] or 0) - (a[3] or 0)))
          if w > pull then pull = w end
        end
      end
      heightOperations = heightOperations + 1
      if heightOperations % 512 == 0 then pause() end
    end
    if sa > 0 then return base + (sz / sa - base) * pull end
    return base
  end

  mark('segments')
  -- The ground lattice: global nodes every step metres, one ring past the tile. Its height is
  -- the pulled DEM minus groundDrop, then lowered wherever the bilinear surface would reach a
  -- road, sidewalk or junction from below (the Python generator cut holes into the ground there;
  -- lowering the four corners of a cell by the worst excess in it is cheap, and since a node's
  -- drop depends only on the cells round it, neighbouring tiles agree on their shared nodes).
  -- One step past the tile is not enough for a border normal: the sample a step
  -- outside has to be pressed from both of its cells, or the neighbour tile's slope
  -- disagrees. The extra ring is only for those samples; drawn quads stay in the tile.
  local n0x, n0y = floor(rect[1] / step + .5) - 1, floor(rect[2] / step + .5) - 1
  local count = math.max(1, floor(size / step + .5))
  local nodes, lower = {}, {}
  local function nodeKey(gx, gy) return (gx - n0x) * 4096 + (gy - n0y) end
  for gy = n0y - 1, n0y + count + 3 do
    for gx = n0x - 1, n0x + count + 3 do
      local z = pulled(gx * step, gy * step)
      if z == nil then nodes[nodeKey(gx, gy)] = nil else nodes[nodeKey(gx, gy)] = z - tune.groundDrop end
    end
    pause()
  end
  local function lattice(x, y)
    local fx, fy = x / step, y / step
    local gx, gy = floor(fx), floor(fy)
    if gx < n0x - 1 or gy < n0y - 1 or gx >= n0x + count + 3 or gy >= n0y + count + 3 then return nil end
    local u, v = fx - gx, fy - gy
    local k = nodeKey(gx, gy)
    return k, u, v
  end
  -- The drawn ground at (x, y) must stay at or under target. The ground triangle's corners are
  -- lowered in proportion to how far each stands above target, just enough at this point: the
  -- high corner that props the surface up takes the drop, not the low one on the road. A node
  -- keeps the largest drop asked of it, so the order of the samples does not matter.
  local function press(x, y, target)
    local k, u, v = lattice(x, y)
    if not k then return end
    local ka, kb, kc, wa, wb, wc
    if u >= v then ka, kb, kc, wa, wb, wc = k, k + 4096, k + 4097, 1 - u, u - v, v
    else ka, kb, kc, wa, wb, wc = k, k + 4097, k + 1, 1 - v, u, v - u end
    local za, zb, zc = nodes[ka], nodes[kb], nodes[kc]
    if za == nil or zb == nil or zc == nil then return end
    local excess = wa * za + wb * zb + wc * zc - target
    if excess <= 0 then return end
    local ha, hb, hc = math.max(0, za - target), math.max(0, zb - target), math.max(0, zc - target)
    local f = excess / (wa * ha + wb * hb + wc * hc)
    if tune.pressEven > 0 then ha, hb, hc, f = 1, 1, 1, excess end
    for _, pair in ipairs({{ka, ha}, {kb, hb}, {kc, hc}}) do
      local drop = pair[2] * f
      if drop > (lower[pair[1]] or 0) then lower[pair[1]] = drop end
    end
  end
  -- The ground as drawn: each lattice cell is two triangles split along its (x0, y0)-(x1, y1)
  -- diagonal (the collector fans a quad from its first corner).
  local function ground(x, y)
    local function fallen(z) return z and z - tune.groundDrop or 0 end
    local k, u, v = lattice(x, y)
    if not k then return fallen(pulled(x, y)) end
    local function nz(key)
      local z = nodes[key]
      if z == nil then return nil end
      return z - (lower[key] or 0)
    end
    local z00, z10, z01, z11 = nz(k), nz(k + 4096), nz(k + 1), nz(k + 4097)
    -- A corner outside the DEM used to fall back to the raw grid and drop the road pull,
    -- so the same verge sampled from the other tile landed tens of metres higher.
    if z00 == nil or z10 == nil or z01 == nil or z11 == nil then return fallen(pulled(x, y)) end
    if u >= v then return z00 + (z10 - z00) * u + (z11 - z10) * v end
    return z00 + (z11 - z01) * u + (z01 - z00) * v
  end
  -- Height of the drawn surface, or nil outside every DEM grid. No grids at all stays the
  -- numeric ground (flat near tiles). A sample just outside a grid is nil so the normal
  -- there is one-sided instead of a slope down to the old z = 0 fallback.
  local function heightAt(x, y)
    if #terrain == 0 then return ground(x, y) end
    if demAt(x, y) == nil then return nil end
    return ground(x, y)
  end
  local normalCache -- enabled only after road pressing has finished
  local function groundNormal(x, y)
    local key = normalCache and (string.format('%.6f:%.6f', x, y))
    local cached = key and normalCache[key]
    if cached then return cached[1], cached[2], cached[3] end
    local function slope(dx, dy)
      local zp, zm, z0 = heightAt(x + dx, y + dy), heightAt(x - dx, y - dy), heightAt(x, y)
      if zp and zm then return (zp - zm) / (2 * step) end
      if zp and z0 then return (zp - z0) / step end
      if zm and z0 then return (z0 - zm) / step end
      return 0
    end
    local nx, ny, nz = -slope(step, 0), -slope(0, step), 1
    local len = math.sqrt(nx * nx + ny * ny + nz * nz)
    nx, ny, nz = nx / len, ny / len, nz / len
    if key then normalCache[key] = {nx, ny, nz} end
    return nx, ny, nz
  end
  -- Spatially indexed mapped cover, shared by terrain and vegetation at all LODs.
  -- Explicit OSM wins over WorldCover. Tie-breaking never depends on load order.
  local coverBins = {}
  for _, area in ipairs(lists.areas) do
    if CLAIM[area.kind] or area.kind == 'residential' then
      local b = ringBounds(area.rings)
      local rank = (CLAIM[area.kind] or 50) + (((area.tags or NONE).source or ''):find('worldcover', 1, true) and 100 or 0)
      for gx = floor(math.max(rect[1], b[1]) / 100), floor(math.min(rect[3], b[3]) / 100) do
        for gy = floor(math.max(rect[2], b[2]) / 100), floor(math.min(rect[4], b[4]) / 100) do
          local k = gx .. ':' .. gy
          local bin = coverBins[k] or {}; coverBins[k] = bin
          bin[#bin + 1] = {area = area, rank = rank}
        end
      end
      pause()
    end
  end
  local function coverAt(x, y)
    local best, rank
    for _, entry in ipairs(coverBins[floor(x / 100) .. ':' .. floor(y / 100)] or NONE) do
      if (not rank or entry.rank < rank or (entry.rank == rank and tostring(entry.area.id) < tostring(best.id)))
          and inside(entry.area.rings, x, y) then best, rank = entry.area, entry.rank end
    end
    return best and best.kind or 'ground', best
  end
  local function surfaceKind(x, y, kind)
    kind = kind or coverAt(x, y)
    if kind == 'residential' then return 'ground' end
    if kind == 'forest' or kind == 'meadow' or kind == 'scrub' or kind == 'heath' or kind == 'bare_ground' then
      local _, _, nz = groundNormal(x, y)
      if nz < .64 then return 'bare_rock' end
      if kind == 'bare_ground' and nz < .85 then return 'scree' end
    end
    return kind
  end
  local function backgroundKind(x, y)
    local kind, area = coverAt(x, y)
    -- Satellite classes are already a coarse raster. Sample the base lattice
    -- instead of triangulating every staircase/OSM cut-out a second time.
    if area and ((area.tags or NONE).source or ''):find('worldcover', 1, true) then return kind end
    return 'ground'
  end
  mark('lattice')
  -- Roads as continuous strips: a knot's normal is the mean of its two segments' directions, so
  -- consecutive quads share their edge vertices (no bevels, no gaps on bends), on every tile.
  local strips, asphalt, asphaltBins = {}, {}, {}
  -- twice the signed area (counter-clockwise > 0)
  local function area2(poly)
    local s = 0
    for k = 1, #poly do local p, q = poly[k], poly[k % #poly + 1]; s = s + p[1] * q[2] - q[1] * p[2] end
    return s
  end
  local function addAsphalt(poly, road, index)
    local box = ringBounds({poly})
    local entry = {poly = poly, bounds = box, road = road, index = index, ccw = area2(poly) > 0}
    asphalt[#asphalt + 1] = entry
    for by = math.floor(box[2] / ABIN), math.floor(box[4] / ABIN) do
      for bx = math.floor(box[1] / ABIN), math.floor(box[3] / ABIN) do
        local binKey = (bx + 32768) * 65536 + by + 32768
        local bin = asphaltBins[binKey] or {}
        asphaltBins[binKey], bin[#bin + 1] = bin, entry
      end
    end
  end
  local stripReach = 3 + tune.sidewalk + tune.shoulder + step
  -- Junctions from the road graph itself. A source road is one graph segment, its two ends
  -- the nodes road.nodes names, so a node's mouths are the road ends sharing its id. The pack
  -- builder's rules (fast_sources.write_index, fast.junction_star): a node of two or more mouths
  -- takes 0.8 x its widest road off every road end, at most a quarter of the road; each mouth has
  -- two corners at that distance, half a width either side (the direction over +-0.25 m), at the
  -- road's profile height; the corners in angle order round the node make its star. Every road
  -- touching a node near the tile is in the tile's cells, so neighbouring tiles agree.
  local cutsOf, stars = {}, {}
  local function along(points, dist, s)
    for k = 1, #points - 1 do
      if s <= dist[k + 1] or k == #points - 1 then
        return lerp(points[k], points[k + 1], math.max(0, math.min(1, (s - dist[k]) / math.max(EPS, dist[k + 1] - dist[k]))))
      end
    end
    return points[1]
  end
  do
    local mouths, order = {}, {}
    for _, road in ipairs(lists.roads) do
      local points, nodes = road.points or NONE, road.nodes or NONE
      if #points >= 2 and nodes[1] ~= nil and nodes[2] ~= nil then
        local dist = {0}
        for k = 2, #points do
          local a, b = points[k - 1], points[k]
          dist[k] = dist[k - 1] + math.sqrt((b[1] - a[1]) ^ 2 + (b[2] - a[2]) ^ 2)
        end
        if dist[#points] > EPS then
          for e = 1, 2 do
            local id = tostring(nodes[e])
            local list = mouths[id]
            if not list then list = {}; mouths[id], order[#order + 1] = list, id end
            list[#list + 1] = {road = road, e = e, dist = dist, width = math.max(.5, math.min(80, road.width or 7))}
          end
        end
      end
    end
    for _, id in ipairs(order) do
      local list = mouths[id]
      if #list > 1 then
        local radius = 0
        for _, m in ipairs(list) do radius = math.max(radius, m.width * .8) end
        local first = list[1]
        local fp = first.road.points
        local node = first.e == 1 and fp[1] or fp[#fp]
        local corners, raised = {}, false
        for _, m in ipairs(list) do
          local points, dist = m.road.points, m.dist
          local length = dist[#points]
          local cut = math.min(radius, length * .25)
          local cuts = cutsOf[m.road] or {0, 0}
          cutsOf[m.road], cuts[m.e] = cuts, cut
          local s = m.e == 1 and cut or length - cut
          local p = along(points, dist, s)
          local f, b = along(points, dist, math.min(length, s + .25)), along(points, dist, math.max(0, s - .25))
          local dx, dy = f[1] - b[1], f[2] - b[2]
          local l = math.max(EPS, math.sqrt(dx * dx + dy * dy))
          local nx, ny = -dy / l * m.width / 2, dx / l * m.width / 2
          corners[#corners + 1] = {p[1] + nx, p[2] + ny, p[3] or 0}
          corners[#corners + 1] = {p[1] - nx, p[2] - ny, p[3] or 0}
          local bridge = (m.road.tags or NONE).bridge
          if bridge ~= nil and bridge ~= 'no' then raised = true end
        end
        local angle = {}
        for _, c in ipairs(corners) do angle[c] = math.atan2(c[2] - node[2], c[1] - node[1]) end
        table.sort(corners, function(a, b) return angle[a] < angle[b] end)
        stars[#stars + 1] = {node = {node[1], node[2]}, height = node[3] or 0, rings = {corners}, raised = raised}
      end
    end
  end
  local trimmed = {}
  for _, road in ipairs(lists.roads) do
    local tags, raw, width = road.tags or {}, road.points or {}, math.max(.5, math.min(80, road.width or 7))
    local points = {}
    for _, p in ipairs(raw) do
      local q = points[#points]
      if not q or math.abs(q[1] - p[1]) + math.abs(q[2] - p[2]) > 1e-4 then
        points[#points + 1] = {p[1], p[2], p[3] or 0}
      end
    end
    -- the road stops at its junction mouths; the junction's own surface is between
    local cuts, full = cutsOf[road], points
    if cuts and #points >= 2 and (cuts[1] > 0 or cuts[2] > 0) then points = trimLine(points, cuts[1], cuts[2]) end
    if tune.simplifyXY > 0 then points = simplify(points, tune.simplifyXY, tune.simplifyZ) end
    if #points >= 2 and boundsTouch(rect, ringBounds({points}), width / 2 + stripReach) then
      local grade = roadGrade(tags)
      points = densifyWear(points, grade)
      local normals, hw = {}, width / 2
      for k = 1, #points do
        local tx, ty = 0, 0
        for _, pair in ipairs({{k - 1, k}, {k, k + 1}}) do
          local a, b = points[pair[1]], points[pair[2]]
          if a and b then
            local dx, dy = b[1] - a[1], b[2] - a[2]
            local l = math.sqrt(dx * dx + dy * dy)
            tx, ty = tx + dx / l, ty + dy / l
          end
        end
        local l = math.max(EPS, math.sqrt(tx * tx + ty * ty))
        -- a mitre keeps the full width on a bend: the mean of two unit directions is cos(turn / 2)
        -- long (each segment counted once at an end); capped so a hairpin does not spike
        local ends = (k == 1 or k == #points) and 1 or 2
        local mitre = math.min(tune.mitre, ends / l)
        normals[k] = {-ty / l * mitre, tx / l * mitre}
      end
      local surfaceRoad = tags.bridge ~= 'yes' and tags.tunnel ~= 'yes' and tags.layer ~= '-1'
      local major = tags.highway == 'motorway' or tags.highway == 'trunk' or tags.highway == 'primary'
      local material = grade == 0 and (major and majorRoad or minorRoad) or ROAD_GRADE_MAT[grade]
      local strip = {road = road, points = points, normals = normals, hw = hw, tags = tags,
        surface = surfaceRoad, material = material, grade = grade, sides = walkSides(tags)}
      strips[#strips + 1] = strip
      trimmed[road] = points
      -- A cut mouth's corridor back to its node, a centimetre under the junction fan: drawn only
      -- where the junction's star misses this mouth (an odd star, e.g. a short wide link between
      -- two carriageways, whose fan leaves gaps).
      strip.corridors = {}
      for _, e in ipairs(points ~= full and surfaceRoad and {{1, full[1]}, {#points, full[#full]}} or NONE) do
        local k, node = e[1], e[2]
        local p, n = points[k], normals[k]
        local nl = math.sqrt(n[1] * n[1] + n[2] * n[2])
        if math.sqrt((p[1] - node[1]) ^ 2 + (p[2] - node[2]) ^ 2) > .05 and nl > EPS then
          strip.nodes = strip.nodes or {}
          strip.nodes[k] = node
          local ux, uy = n[1] / nl * hw, n[2] / nl * hw
          local zp, zn = (p[3] or 0) + lift - .01, (node[3] or 0) + lift - .01
          local quad = {{p[1] + ux, p[2] + uy, zp}, {p[1] - ux, p[2] - uy, zp},
            {node[1] - ux, node[2] - uy, zn}, {node[1] + ux, node[2] + uy, zn}}
          strip.corridors[k] = quad
        end
      end
      local function edge(k, offset, dz)
        local p, n = points[k], normals[k]
        local widen = 1
        if grade >= 4 and k > 1 and k < #points then
          widen = 1 + (hash('edge:' .. floor(p[1] + .5) .. ':' .. floor(p[2] + .5)) % 1000 / 1000 - .5) * .34
        end
        return {p[1] + n[1] * offset * widen, p[2] + n[2] * offset * widen, (p[3] or 0) + lift + (dz or 0)}
      end
      strip.edge = edge
      for k = 1, #points - 1 do
        local a, b = points[k], points[k + 1]
        local quad = {edge(k, hw), edge(k, -hw), edge(k + 1, -hw), edge(k + 1, hw)}
        local box = ringBounds({quad})
        if boundsTouch(rect, box, stripReach) then
          if surfaceRoad then addAsphalt(quad, strip, k) end
          if visualRoads and boundsTouch(rect, box, 0) then roads.polygon(strip.material, quad, 'flat') end
          local dx, dy = b[1] - a[1], b[2] - a[2]
          local length = math.sqrt(dx * dx + dy * dy)
          local t = math.max(0, math.min(1, ((target[1] - a[1]) * dx + (target[2] - a[2]) * dy) / (length * length)))
          local p = lerp(a, b, t)
          local distance = (p[1] - target[1]) ^ 2 + (p[2] - target[2]) ^ 2
          if distance < bestDistance and p[1] >= rect[1] and p[1] <= rect[3] and p[2] >= rect[2] and p[2] <= rect[4] then
            bestDistance = distance
            bestSpawn = {pos = {p[1], p[2], (p[3] or 0) + lift}, yaw = math.atan2(dx, dy)}
          end
        end
        if k % 32 == 0 then pause() end
      end
    end
    pause()
  end
  mark('strips')
  -- Strip ends of streets with sidewalks, by the metre: a junction with one of them at a mouth
  -- gets a sidewalk ring.
  local walkEnds = {}
  for _, strip in ipairs(strips) do
    if next(strip.sides) then
      for _, p in ipairs({strip.points[1], strip.points[#strip.points]}) do
        local key = (floor(p[1]) + 65536) * 131072 + floor(p[2]) + 65536
        local list = walkEnds[key] or {}
        walkEnds[key], list[#list + 1] = list, p
      end
    end
  end
  -- Every strip end by the metre, with its direction into the road: a junction's mouth is the
  -- edge between two corners whose midpoint is a strip end.
  local stripEnds, corridors, corridorOf, corridorsAt = {}, {}, {}, {}
  for _, strip in ipairs(strips) do
    local pts = strip.points
    for _, e in ipairs({{pts[1], pts[2], 1}, {pts[#pts], pts[#pts - 1], #pts}}) do
      local p, q = e[1], e[2]
      local dx, dy = q[1] - p[1], q[2] - p[2]
      local l = math.max(EPS, math.sqrt(dx * dx + dy * dy))
      local key = (floor(p[1]) + 65536) * 131072 + floor(p[2]) + 65536
      local list = stripEnds[key] or {}
      local corridor = strip.corridors and strip.corridors[e[3]]
      stripEnds[key], list[#list + 1] = list, {p = p, dx = dx / l, dy = dy / l, corridor = corridor}
      if corridor then
        local node = strip.nodes[e[3]]
        local c = {quad = corridor, material = strip.material}
        corridors[#corridors + 1], corridorOf[corridor] = c, c
        local key = floor(node[1] * 10 + .5) * 2 ^ 26 + floor(node[2] * 10 + .5)
        local list = corridorsAt[key] or {}
        corridorsAt[key], list[#list + 1] = list, c
      end
    end
  end
  local function stripEndAt(x, y)
    for dx = -1, 1 do
      for dy = -1, 1 do
        for _, e in ipairs(stripEnds[(floor(x) + dx + 65536) * 131072 + floor(y) + dy + 65536] or NONE) do
          if (e.p[1] - x) ^ 2 + (e.p[2] - y) ^ 2 < .01 then return e end
        end
      end
    end
  end
  local function walkEndNear(x, y)
    for dx = -1, 1 do
      for dy = -1, 1 do
        for _, p in ipairs(walkEnds[(floor(x) + dx + 65536) * 131072 + floor(y) + dy + 65536] or NONE) do
          if (p[1] - x) ^ 2 + (p[2] - y) ^ 2 < .01 then return true end
        end
      end
    end
    return false
  end
  local plates = {}
  -- Junctions: a fan from the node over its star, each corner at its road's profile, so the
  -- surface meets every cut road end without a step.
  for _, junction in ipairs(stars) do
    local rings = junction.rings or NONE
    local ring = cleanRing(rings[1])
    if #ring >= 3 and boundsTouch(rect, ringBounds({ring}), stripReach) then
      local cx, cy = junction.node[1], junction.node[2]
      local centre = junction.height + lift
      local corners = {}
      for k, p in ipairs(ring) do corners[k] = {p[1], p[2], p[3] + lift} end
      do
        -- Between two mouths the star's chord cut the inner corner off (a wedge of grass in the
        -- asphalt, up to several m2): the edges of the two roads meet at a point instead.
        local n, dirs = #corners, {}
        local mouth = {}
        for k = 1, n do
          local a, b = corners[k], corners[k % n + 1]
          local e = stripEndAt((a[1] + b[1]) / 2, (a[2] + b[2]) / 2)
          if e then
            mouth[k], dirs[k], dirs[k % n + 1] = true, e, e
            if e.corridor then corridorOf[e.corridor].fanned = true end
          end
        end
        -- one mouth of this node missing from the star: the star is odd (interleaved corners),
        -- so every road here keeps its corridor
        local odd = false
        for _, c in ipairs(corridorsAt[floor(cx * 10 + .5) * 2 ^ 26 + floor(cy * 10 + .5)] or NONE) do
          if not c.fanned then odd = true end
        end
        if odd then
          for _, c in ipairs(corridorsAt[floor(cx * 10 + .5) * 2 ^ 26 + floor(cy * 10 + .5)] or NONE) do c.fanned = false end
        end
        local out = {}
        for k = 1, n do
          local a, b = corners[k], corners[k % n + 1]
          out[#out + 1] = a
          local ea, eb = dirs[k], dirs[k % n + 1]
          if not mouth[k] and ea and eb and ea ~= eb then
            -- a + t * ea = b + u * eb
            local det = ea.dx * (-eb.dy) - ea.dy * (-eb.dx)
            if math.abs(det) > .2 then
              local rx, ry = b[1] - a[1], b[2] - a[2]
              local t = (rx * (-eb.dy) - ry * (-eb.dx)) / det
              local u = (ea.dx * ry - ea.dy * rx) / det
              local reach = math.sqrt((a[1] - cx) ^ 2 + (a[2] - cy) ^ 2) + math.sqrt((b[1] - cx) ^ 2 + (b[2] - cy) ^ 2)
              local x, y = a[1] + ea.dx * t, a[2] + ea.dy * t
              -- only on the far side of the chord from the node (else the fan would lose the
              -- triangle between them), and near (not a spike between near-parallel roads)
              local away = (b[1] - a[1]) * (y - a[2]) - (b[2] - a[2]) * (x - a[1])
              local node = (b[1] - a[1]) * (cy - a[2]) - (b[2] - a[2]) * (cx - a[1])
              if away * node < 0 and math.abs(t) < reach and math.abs(u) < reach then
                out[#out + 1] = {x, y, (a[3] + b[3]) / 2}
              end
            end
          end
        end
        corners = out
      end
      local plate = {ring = corners, centre = {cx, cy, centre}}
      plates[#plates + 1] = plate
      local touches = boundsTouch(rect, ringBounds({ring}), 0)
      for k = 1, #corners do
        local tri = {{cx, cy, centre}, corners[k], corners[k % #corners + 1]}
        addAsphalt(tri, nil, nil)
        if visualRoads and touches then roads.polygon('road', tri, 'flat') end
      end
      -- round a ground-level star (fast.junction_verge): a sidewalk ring when a street with
      -- sidewalks meets here, then the verge down to the ground, both away from the node
      if not junction.raised then
        local walks = false
        for k = 1, #corners do
          local a, b = corners[k], corners[k % #corners + 1]
          if walkEndNear((a[1] + b[1]) / 2, (a[2] + b[2]) / 2) then walks = true; break end
        end
        local inset = walks and tune.sidewalk or 0
        local mids, outs = {}, {}
        for k, q in ipairs(corners) do
          local dx, dy = q[1] - cx, q[2] - cy
          local l = math.max(EPS, math.sqrt(dx * dx + dy * dy))
          dx, dy = dx / l, dy / l
          mids[k] = {q[1] + dx * inset, q[2] + dy * inset, q[3]}
          outs[k] = {q[1] + dx * (inset + tune.shoulder), q[2] + dy * (inset + tune.shoulder), 0}
        end
        plate.mids, plate.walks, plate.verges = mids, {}, {}
        for k = 1, #corners do
          local n = k % #corners + 1
          if walks then plate.walks[#plate.walks + 1] = {corners[k], corners[n], mids[n], mids[k]} end
          if tune.shoulder > 0 then plate.verges[#plate.verges + 1] = {mids[k], mids[n], outs[n], outs[k]} end
        end
      end
    end
    pause()
  end

  for _, c in ipairs(corridors) do
    if not c.fanned then
      local box = ringBounds({c.quad})
      if boundsTouch(rect, box, stripReach) then
        addAsphalt(c.quad, nil, nil)
        if visualRoads and boundsTouch(rect, box, 0) then roads.polygon(c.material, c.quad, 'flat') end
      end
    end
  end

  -- Convex polygon minus convex polygons (Sutherland-Hodgman per edge): the pieces of a
  -- sidewalk or verge that are not on asphalt. z and UVs are interpolated along the cuts.
  local function side(ax, ay, bx, by, p) return (bx - ax) * (p[2] - ay) - (by - ay) * (p[1] - ax) end
  local function split(poly, ax, ay, bx, by, ccw)
    local inside, outside = {}, {}
    local n = #poly
    for k = 1, n do
      local p, q = poly[k], poly[k % n + 1]
      local sp, sq = side(ax, ay, bx, by, p), side(ax, ay, bx, by, q)
      if not ccw then sp, sq = -sp, -sq end
      if sp >= -1e-9 then inside[#inside + 1] = p end
      if sp <= 1e-9 then outside[#outside + 1] = p end
      if (sp > 1e-9 and sq < -1e-9) or (sp < -1e-9 and sq > 1e-9) then
        local x = lerp(p, q, sp / (sp - sq))
        inside[#inside + 1], outside[#outside + 1] = x, x
      end
    end
    return inside, outside
  end
  -- Some edge of c has every vertex of poly strictly on its outer side.
  local function beyond(poly, c, ccw)
    for k = 1, #c do
      local a, b = c[k], c[k % #c + 1]
      local ax, ay, bx, by = a[1], a[2], b[1], b[2]
      local all = true
      for n = 1, #poly do
        local sd = side(ax, ay, bx, by, poly[n])
        if (ccw and sd > -1e-9) or (not ccw and sd < 1e-9) then all = false; break end
      end
      if all then return true end
    end
    return false
  end
  local cutOps = 0
  local function subtract(poly, skip)
    local pieces = {poly}
    local box = ringBounds({poly})
    local pccw = area2(poly) > 0
    local seen = {}
    for by = math.floor(box[2] / ABIN), math.floor(box[4] / ABIN) do
      for bx = math.floor(box[1] / ABIN), math.floor(box[3] / ABIN) do
        for _, entry in ipairs(asphaltBins[(bx + 32768) * 65536 + by + 32768] or NONE) do
          if not seen[entry] and boundsTouch(box, entry.bounds) and not (skip and skip(entry)) then
            seen[entry] = true
            local c, ccw = entry.poly, entry.ccw
            local out, cut = {}, false
            for _, piece in ipairs(pieces) do
              -- separated convex pieces (the usual case: only the bounds touch) stay whole
              if beyond(piece, c, ccw) or beyond(c, piece, pccw) then
                out[#out + 1] = piece
              else
                cut = true
                local rest = piece
                for k = 1, #c do
                  local a, b = c[k], c[k % #c + 1]
                  local inner, outer = split(rest, a[1], a[2], b[1], b[2], ccw)
                  if #outer >= 3 and math.abs(area2(outer)) > 2e-4 then out[#out + 1] = outer end
                  rest = inner
                  if #rest < 3 or math.abs(area2(rest)) <= 2e-4 then break end
                end
              end
              cutOps = cutOps + 1
              if cutOps % 256 == 0 then pause() end
            end
            if cut then pieces = out end
            if #pieces == 0 then return pieces end
          end
        end
      end
    end
    return pieces
  end
  local function emit(collectorOf, material, poly, skip, up)
    -- Distant tiles skip the carriageway. The verge is the transition band: the same
    -- ground wedge the near tile cuts at this edge, so the shared border keeps its vertices.
    if not visualRoads and not (distant and material == 'ground') then return end
    for _, piece in ipairs(subtract(poly, skip)) do
      if material == 'ground' then
        -- Outer edge points are marked at [9]. Resample them after the clip: a linear
        -- blend of the two tiles' endpoint heights does not land on the shared ground().
        local clipped = clip(piece, rect)
        local shaded = {}
        for i, p in ipairs(clipped) do
          local z = p[3] or 0
          if (p[9] or 0) > 0.9 then z = ground(p[1], p[2]) end
          local q = {p[1], p[2], z}
          q[6], q[7], q[8] = groundNormal(p[1], p[2])
          shaded[i] = q
        end
        if #shaded >= 3 then collectorOf.polygon(material, shaded, up) end
      else
        collectorOf.polygon(material, piece, up)
      end
    end
  end
  mark('plates')
  -- Sidewalks flush with the road (a curb band, then paving) on the sides the tags allow, cut by
  -- every carriageway and junction; verges from the outer edge down to the ground, cut the same.
  for _, strip in ipairs(strips) do
    local points, hw, edge = strip.points, strip.hw, strip.edge
    local own = function(entry) return entry.road == strip end
    for k = 1, #points - 1 do
      local box = ringBounds({{points[k], points[k + 1]}})
      if boundsTouch(rect, box, hw + stripReach) then
        local near = function(entry) return entry.road == strip and math.abs(entry.index - k) <= 1 end
        for _, sign in ipairs({1, -1}) do
          local walk = strip.sides[sign] and tune.sidewalk or 0
          local outer = hw + walk
          if walk > 0 then
            local curb = math.min(tune.curb, walk)
            local band = {edge(k, sign * hw), edge(k, sign * (hw + curb)), edge(k + 1, sign * (hw + curb)), edge(k + 1, sign * hw)}
            local paved = {edge(k, sign * (hw + curb)), edge(k, sign * outer), edge(k + 1, sign * outer), edge(k + 1, sign * (hw + curb))}
            if boundsTouch(rect, ringBounds({band, paved}), 0) then
              if curb > 0 then emit(roads, 'curb', band, near, 'flat') end
              emit(roads, 'sidewalk', paved, near, 'flat')
            end
          end
          if strip.surface and tune.shoulder > 0 then
            local inner = {edge(k, sign * outer, -tune.vergeSink), edge(k + 1, sign * outer, -tune.vergeSink)}
            local o1, o2 = edge(k, sign * (outer + tune.shoulder)), edge(k + 1, sign * (outer + tune.shoulder))
            local verge = {inner[1], o1, o2, inner[2]}
            if boundsTouch(rect, ringBounds({verge}), 0) then strip.verges = strip.verges or {}; strip.verges[#strip.verges + 1] = {verge, own} end
          end
        end
      end
      if k % 32 == 0 then pause() end
    end
    pause()
  end
  mark('sidewalks')
  for _, plate in ipairs(plates) do
    for _, quad in ipairs(plate.walks or NONE) do
      if boundsTouch(rect, ringBounds({quad}), 0) then emit(roads, 'sidewalk', quad, nil, 'flat') end
    end
  end
  -- Press the lattice under every surface strip (road and sidewalk, sampled every 2 m from each
  -- segment's start) and under junction plates, then the verges take the lowered ground.
  for _, strip in ipairs(strips) do
    if strip.surface then
      local points, hw = strip.points, strip.hw
      for k = 1, #points - 1 do
        local a, b = points[k], points[k + 1]
        local length = math.sqrt((b[1] - a[1]) ^ 2 + (b[2] - a[2]) ^ 2)
        if boundsTouch(rect, ringBounds({{a, b}}), hw + tune.sidewalk + step + 1) then
          local left = hw + (strip.sides[1] and tune.sidewalk or 0)
          local right = hw + (strip.sides[-1] and tune.sidewalk or 0)
          local na, nb = strip.normals[k], strip.normals[k + 1]
          local samples = math.max(1, math.ceil(length / 2))
          for s = 0, samples do
            local t = s / samples
            local x, y = a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t
            local nx, ny = na[1] + (nb[1] - na[1]) * t, na[2] + (nb[2] - na[2]) * t
            local z = (a[3] or 0) + ((b[3] or 0) - (a[3] or 0)) * t + lift - tune.margin
            for _, offset in ipairs({-right, -hw, -hw * .5, 0, hw * .5, hw, left}) do
              press(x + nx * offset, y + ny * offset, z)
            end
          end
        end
        if k % 16 == 0 then pause() end
      end
    end
  end
  for _, plate in ipairs(plates) do
    local c = plate.centre
    press(c[1], c[2], c[3] - tune.margin)
    for k, m in ipairs(plate.mids or NONE) do
      local q = plate.ring[k]
      press(m[1], m[2], m[3] - tune.margin)
      press((m[1] + q[1]) / 2, (m[2] + q[2]) / 2, m[3] - tune.margin)
    end
    for k, p in ipairs(plate.ring) do
      press(p[1], p[2], p[3] - tune.margin)
      local q = plate.ring[k % #plate.ring + 1]
      press((p[1] + q[1] + c[1]) / 3, (p[2] + q[2] + c[2]) / 3, (p[3] + q[3] + c[3]) / 3 - tune.margin)
    end
  end
  normalCache = {} -- no more press() calls; covers repeatedly shade shared lattice corners
  -- Ground cover lies on the ground as drawn: cut by lattice cell and diagonal, so each piece is
  -- in one ground triangle and exactly parallel to it. The pressed ground keeps it under the
  -- asphalt, so carriageways need not be cut out (that shattered big squares into 24k pieces).
  local function drape(kind, poly, above)
    local box = ringBounds({poly})
    local x0, x1 = math.max(box[1], rect[1]), math.min(box[3], rect[3])
    local y0, y1 = math.max(box[2], rect[2]), math.min(box[4], rect[4])
    if x0 >= x1 or y0 >= y1 then return end
    for gy = floor(y0 / step), math.ceil(y1 / step) - 1 do
      for gx = floor(x0 / step), math.ceil(x1 / step) - 1 do
        local cx, cy = gx * step, gy * step
        local piece = clip(poly, {cx, cy, cx + step, cy + step})
        if #piece >= 3 and math.abs(area2(piece)) > 2e-4 then
          local upper, lower2 = split(piece, cx, cy, cx + step, cy + step, true)
          for _, half in ipairs({upper, lower2}) do
            if #half >= 3 and math.abs(area2(half)) > 2e-4 then
              local out = {}
              for n, q in ipairs(half) do out[n] = {q[1], q[2], ground(q[1], q[2]) + above} end
              scene.polygon(kind, out, true)
            end
          end
        end
      end
      pause()
    end
  end
  -- A big area (a square, a forest): bands of one sweep, aligned to the lattice rows, cut by
  -- column and diagonal. Sweeping the whole polygon alone gave hundreds of long slivers.
  local claims = {}
  local function drapeRings(kind, rings, above)
    local box = ringBounds(rings)
    local x0, x1 = math.max(box[1], rect[1]), math.min(box[3], rect[3])
    local y0, y1 = math.max(box[2], rect[2]), math.min(box[4], rect[4])
    if x0 >= x1 or y0 >= y1 then return end
    -- pieces per ground triangle; the bands of one polygon never overlap, so their areas add up
    local pieces, filled, order = {}, {}, {}
    local full = step * step
    local prep = prepareRings(rings)
    -- row by row: a row's bands split only at the vertices inside it, so most cells of a big
    -- area lie inside one band and are taken whole, without clipping
    for gy = floor(y0 / step), math.ceil(y1 / step) - 1 do
     local ry0, ry1 = math.max(y0, gy * step), math.min(y1, (gy + 1) * step)
     for _, band in ipairs(triangulate(prep, nil, {x0, ry0, x1, ry1}, true)) do
      local bx0 = math.max(x0, math.min(band[1][1], band[4][1]))
      local bx1 = math.min(x1, math.max(band[2][1], band[3][1]))
      local inX0, inX1 = math.max(band[1][1], band[4][1]), math.min(band[2][1], band[3][1])
      local tall = math.abs(band[1][2] - gy * step) < 1e-6 and math.abs(band[4][2] - (gy + 1) * step) < 1e-6
      for gx = floor(bx0 / step), math.ceil(bx1 / step) - 1 do
        local cx, cy = gx * step, gy * step
        local key0 = ((gx + 32768) * 65536 + gy + 32768) * 2
        if tall and cx >= inX0 - 1e-6 and cx + step <= inX1 + 1e-6 then
          for key = key0, key0 + 1 do
            if not filled[key] then pieces[key], filled[key], order[#order + 1] = {}, 0, key end
            filled[key] = filled[key] + full
          end
        else
        local piece = cleanRing(clip(band, {cx, cy, cx + step, cy + step}))
        if #piece >= 3 and math.abs(area2(piece)) > 2e-4 then
          for _, half in ipairs({split(piece, cx, cy, cx + step, cy + step, true)}) do
            local twice = math.abs(area2(half))
            if #half >= 3 and twice > 2e-4 then
              local sx, sy = 0, 0
              for _, q in ipairs(half) do sx, sy = sx + q[1], sy + q[2] end
              local key = ((gx + 32768) * 65536 + gy + 32768) * 2 + ((sx - cx * #half) >= (sy - cy * #half) and 0 or 1)
              local list = pieces[key]
              if not list then list = {}; pieces[key], filled[key], order[#order + 1] = list, 0, key end
              list[#list + 1], filled[key] = half, filled[key] + twice
            end
          end
        end
        end
      end
     end
     pause()
    end
    local rank = CLAIM[kind]
    for _, key in ipairs(order) do
      if rank and filled[key] > full - 1e-2 then
        -- the whole ground triangle: drawn in this material instead of grass
        local held = claims[key]
        if not held or CLAIM[held] > rank then claims[key] = kind end
      else
        for _, half in ipairs(pieces[key]) do
          local out = {}
          local cx, cy = 0, 0
          for n, q in ipairs(half) do
            out[n] = {q[1], q[2], ground(q[1], q[2]) + above}
            if kind ~= 'water' then out[n][6], out[n][7], out[n][8] = groundNormal(q[1], q[2]) end
            cx, cy = cx + q[1], cy + q[2]
          end
          scene.polygon(surfaceKind(cx / #half, cy / #half, kind), out, true)
        end
      end
    end
    pause()
  end
  for _, plate in ipairs(plates) do
    for _, quad in ipairs(plate.verges or NONE) do
      if boundsTouch(rect, ringBounds({quad}), 0) then
        local outerA = {quad[3][1], quad[3][2], ground(quad[3][1], quad[3][2])}
        local outerB = {quad[4][1], quad[4][2], ground(quad[4][1], quad[4][2])}
        outerA[9], outerB[9] = 1, 1
        emit(distant and scene or roads, vergeKind, {quad[1], quad[2], outerA, outerB}, nil, true)
      end
    end
  end
  for _, strip in ipairs(strips) do
    for _, item in ipairs(strip.verges or NONE) do
      local verge = item[1]
      verge[2][3], verge[3][3] = ground(verge[2][1], verge[2][2]), ground(verge[3][1], verge[3][2])
      -- [9] marks the outer edge. The clip resamples it from ground() so both tiles agree.
      verge[2][9], verge[3][9] = 1, 1
      local groundMesh = distant and scene or roads
      if tune.vergeCut > 0 then emit(groundMesh, vergeKind, verge, item[2], true)
      elseif visualRoads then roads.polygon(vergeKind, verge, true)
      elseif distant then scene.polygon(vergeKind, verge, true) end
    end
  end

  mark('verges')
  if visualRoads and markKind then
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
              roadDecor.polygon(markKind, {{p[1] + nx * left, p[2] + ny * left, (p[3] or 0) + lift + .008},
                {p[1] + nx * right, p[2] + ny * right, (p[3] or 0) + lift + .008},
                {q[1] + nx * right, q[2] + ny * right, (q[3] or 0) + lift + .008},
                {q[1] + nx * left, q[2] + ny * left, (q[3] or 0) + lift + .008}}, 'flat')
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

  mark('marks')
  -- OSM has no sea polygons (only coastlines); the DEM's sea is flat at the region's seaLevel
  -- (0 m plus the world lift). Open water is a plane just above it over every ground quad that
  -- reaches it; higher ground covers the plane on the shore. Lakes and rivers are OSM areas.
  local sea = type(options.seaLevel) == 'number' and options.seaLevel + SEA_ABOVE or nil
  local seaStep = distant and size / 5 or size / count
  local seaCount = math.max(1, math.floor(size / seaStep + .5))
  local seaQuads, seaAll = {}, true
  for gy = 0, sea and seaCount - 1 or -1 do
    for gx = 0, seaCount - 1 do
      local x0, y0 = rect[1] + size * gx / seaCount, rect[2] + size * gy / seaCount
      local x1, y1 = rect[1] + size * (gx + 1) / seaCount, rect[2] + size * (gy + 1) / seaCount
      if math.min(rawHeight(x0, y0), rawHeight(x1, y0), rawHeight(x0, y1), rawHeight(x1, y1)) < sea then
        seaQuads[#seaQuads + 1] = {x0, y0, x1, y1}
      else
        seaAll = false
      end
    end
    pause()
  end
  if sea and seaAll then seaQuads = {rect} end
  for _, q in ipairs(seaQuads) do
    scene.polygon('water', {{q[1], q[2], sea}, {q[3], q[2], sea}, {q[3], q[4], sea}, {q[1], q[4], sea}}, 'flat')
  end
  mark('groundsea')
  for _, area in ipairs(distant and NONE or lists.areas) do
    local kind = area.kind
    if CLAIM[kind] and not (((area.tags or NONE).source or ''):find('worldcover', 1, true)) and ringsTouch(area.rings, rect) then
      drapeRings(kind, area.rings, .025)
    end
    pause()
  end
  -- Height of the drawn surface, or nil outside every DEM grid. No grids at all stays the
  -- numeric ground (flat near tiles). A sample just outside a grid is nil so the normal
  -- there is one-sided instead of a slope down to the old z = 0 fallback.
  local function vertex(x, y, z)
    local p = {x, y, z}
    p[6], p[7], p[8] = groundNormal(x, y)
    return p
  end
  -- The ground lattice: each cell two triangles along its (x0, y0)-(x1, y1) diagonal, drawn in
  -- the material of the cover that fills it whole (claims), else as grass.
  for gy = 0, distant and -1 or count - 1 do
    for gx = 0, count - 1 do
      local cx, cy = n0x + 1 + gx, n0y + 1 + gy
      local x0, y0 = cx * step, cy * step
      local x1, y1 = x0 + step, y0 + step
      local a, b = vertex(x0, y0, ground(x0, y0)), vertex(x1, y0, ground(x1, y0))
      local c, d = vertex(x1, y1, ground(x1, y1)), vertex(x0, y1, ground(x0, y1))
      local gkey = ((cx + 32768) * 65536 + cy + 32768) * 2
      local tx, ty = (x0+x1+x1)/3, (y0+y0+y1)/3
      roads.polygon(surfaceKind(tx, ty, claims[gkey] or backgroundKind(tx, ty)), {a, b, c}, true)
      tx, ty = (x0+x1+x0)/3, (y0+y1+y1)/3
      roads.polygon(surfaceKind(tx, ty, claims[gkey + 1] or backgroundKind(tx, ty)), {a, c, d}, true)
    end
    pause()
  end
  -- Distant ground is render-only and lives in the far/horizon manifest. The tile border is
  -- sampled at the near step so it shares that edge; the interior is the coarse step. A fan
  -- joins them. Corners belong to one fan, so two strips never cover the same quad.
  if distant and #terrain > 0 then
    local fine = step
    local coarse = options.horizon and (options.horizonStep or options.farStep or 100) or (options.farStep or 100)
    coarse = fine * math.max(1, math.floor(coarse / fine + 1e-6))
    if size < coarse * 2 - EPS then coarse = fine end
    local x0, y0, x1, y1 = rect[1], rect[2], rect[3], rect[4]
    local function emitTri(a, b, c)
      if not a or not b or not c then return end
      scene.triangle(surfaceKind((a[1]+b[1]+c[1])/3, (a[2]+b[2]+c[2])/3), a, b, c, true)
    end
    local function at(x, y)
      local z = heightAt(x, y)
      if not z then return nil end
      return vertex(x, y, z)
    end
    local ix0, iy0, ix1, iy1 = x0 + coarse, y0 + coarse, x1 - coarse, y1 - coarse
    if ix1 > ix0 + EPS and iy1 > iy0 + EPS then
      local function ticks(a, b, stride)
        local out, v = {}, a
        while v < b - EPS do out[#out + 1] = v; v = v + stride end
        out[#out + 1] = b
        return out
      end
      local xs, ys = ticks(ix0, ix1, coarse), ticks(iy0, iy1, coarse)
      for gy = 1, #ys - 1 do
        for gx = 1, #xs - 1 do
          local ax, ay = xs[gx], ys[gy]
          local bx, by = xs[gx + 1], ys[gy + 1]
          emitTri(at(ax, ay), at(bx, ay), at(bx, by))
          emitTri(at(ax, ay), at(bx, by), at(ax, by))
        end
        pause()
      end
    end
    local function pointsAlong(ax, ay, bx, by, n)
      local out = {}
      for k = 0, n do
        local t = k / math.max(n, 1)
        out[#out + 1] = {ax + (bx - ax) * t, ay + (by - ay) * t}
      end
      return out
    end
    -- Outer fine polyline P and inner coarse knots. P[0] matches the first knot's
    -- parameter, P[n] the last. Triangles (L, P[i], P[i+1]) plus (L, P[n], R).
    local function fan(outer, inner)
      local ia = 1
      for k = 1, #inner - 1 do
        local L, R = inner[k], inner[k + 1]
        local ib = ia
        while ib < #outer do
          ib = ib + 1
          local p = outer[ib]
          if math.abs(p[1] - R[1]) <= EPS and math.abs(p[2] - R[2]) <= EPS then break end
          -- R is inset, so the outer end shares R's along-edge coordinate, not its position.
          local along = math.abs(R[1] - L[1]) >= math.abs(R[2] - L[2])
          local target = along and R[1] or R[2]
          local got = along and p[1] or p[2]
          if math.abs(got - target) <= EPS then break end
        end
        for n = ia, ib - 1 do
          emitTri(at(L[1], L[2]), at(outer[n][1], outer[n][2]), at(outer[n + 1][1], outer[n + 1][2]))
        end
        local p = outer[ib]
        if math.abs(p[1] - R[1]) > EPS or math.abs(p[2] - R[2]) > EPS then
          emitTri(at(L[1], L[2]), at(p[1], p[2]), at(R[1], R[2]))
        end
        ia = ib
      end
    end
    local function fanPoint(outer, x, y)
      for n = 1, #outer - 1 do
        emitTri(at(x, y), at(outer[n][1], outer[n][2]), at(outer[n + 1][1], outer[n + 1][2]))
      end
    end
    local nFine = math.floor(coarse / fine + .5)
    local span = (x1 - x0) - 2 * coarse
    local nSpanFine = math.floor(span / fine + .5)
    local nSpanCoarse = math.floor(span / coarse + .5)
    if span > EPS then
      fan(pointsAlong(x0 + coarse, y0, x1 - coarse, y0, nSpanFine),
        pointsAlong(x0 + coarse, y0 + coarse, x1 - coarse, y0 + coarse, nSpanCoarse))
      fan(pointsAlong(x0 + coarse, y1, x1 - coarse, y1, nSpanFine),
        pointsAlong(x0 + coarse, y1 - coarse, x1 - coarse, y1 - coarse, nSpanCoarse))
      fan(pointsAlong(x0, y0 + coarse, x0, y1 - coarse, nSpanFine),
        pointsAlong(x0 + coarse, y0 + coarse, x0 + coarse, y1 - coarse, nSpanCoarse))
      fan(pointsAlong(x1, y0 + coarse, x1, y1 - coarse, nSpanFine),
        pointsAlong(x1 - coarse, y0 + coarse, x1 - coarse, y1 - coarse, nSpanCoarse))
    end
    local function corner(outer, x, y) fanPoint(outer, x, y) end
    corner(pointsAlong(x0, y0 + coarse, x0, y0, nFine), x0 + coarse, y0 + coarse)
    -- continue the L from the SW corner of the tile to the south inset
    fanPoint(pointsAlong(x0, y0, x0 + coarse, y0, nFine), x0 + coarse, y0 + coarse)
    fanPoint(pointsAlong(x1, y0 + coarse, x1, y0, nFine), x1 - coarse, y0 + coarse)
    fanPoint(pointsAlong(x1, y0, x1 - coarse, y0, nFine), x1 - coarse, y0 + coarse)
    fanPoint(pointsAlong(x0, y1 - coarse, x0, y1, nFine), x0 + coarse, y1 - coarse)
    fanPoint(pointsAlong(x0, y1, x0 + coarse, y1, nFine), x0 + coarse, y1 - coarse)
    fanPoint(pointsAlong(x1, y1 - coarse, x1, y1, nFine), x1 - coarse, y1 - coarse)
    fanPoint(pointsAlong(x1, y1, x1 - coarse, y1, nFine), x1 - coarse, y1 - coarse)
    -- Skirt only where the next sample really has no DEM. A shared tile edge whose
    -- neighbour grid is loaded is left open; the skirt does not hide a wrong height.
    local function skirtEdge(p, q, outwardX, outwardY)
      local ux, uy = q[1] - p[1], q[2] - p[2]
      if -uy * outwardX + ux * outwardY < 0 then p, q = q, p end
      local drop = 24
      local pb, qb = {p[1], p[2], p[3] - drop}, {q[1], q[2], q[3] - drop}
      scene.triangle('ground', p, q, qb, false)
      scene.triangle('ground', p, qb, pb, false)
    end
    local n = math.floor(size / fine + .5)
    local function gridZ(ix, iy) return heightAt(x0 + ix * fine, y0 + iy * fine) end
    for iy = 0, n do
      for ix = 0, n do
        if ix < n then
          local z0, z1 = gridZ(ix, iy), gridZ(ix + 1, iy)
          if z0 and z1 then
            local function side(dir)
              local a, b = gridZ(ix, iy + dir), gridZ(ix + 1, iy + dir)
              return a ~= nil and b ~= nil
            end
            local south, north = side(-1), side(1)
            if south ~= north then
              skirtEdge({x0 + ix * fine, y0 + iy * fine, z0}, {x0 + (ix + 1) * fine, y0 + iy * fine, z1},
                0, south and 1 or -1)
            end
          end
        end
        if iy < n then
          local z0, z1 = gridZ(ix, iy), gridZ(ix, iy + 1)
          if z0 and z1 then
            local function side(dir)
              local a, b = gridZ(ix + dir, iy), gridZ(ix + dir, iy + 1)
              return a ~= nil and b ~= nil
            end
            local west, east = side(-1), side(1)
            if west ~= east then
              skirtEdge({x0 + ix * fine, y0 + iy * fine, z0}, {x0 + ix * fine, y0 + (iy + 1) * fine, z1},
                west and 1 or -1, 0)
            end
          end
        end
      end
      if iy % 4 == 3 then pause() end
    end
  end
  mark('areas')
  for _, building in ipairs(decor and lists.buildings or NONE) do
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
      local top, kind = base + height, wallMaterial(building, look)
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
  mark('buildings')
  -- A fence or wall as a thin box that follows the drawn ground: a knot every fenceStep metres
  -- (one quad per OSM segment, its ends alone on the ground, sank metres into a hump between
  -- them and vanished from the street), sides along mitred normals, a top and two end caps.
  -- Concrete for walls and concrete/brick/stone fences, chain link for the rest (fast.add_fence).
  local function fence(line)
    local tags = line.tags or NONE
    local ft = tags.fence_type
    local concrete = tags.barrier == 'wall' or ft == 'concrete' or ft == 'brick' or ft == 'stone'
    local kind = concrete and 'fence_wall' or 'fence_mesh'
    local height = math.min(3, number(tags.height, concrete and 2 or 1.6))
    local half = (concrete and tune.wallThick or tune.fenceThick) / 2
    local scale = concrete and 2.5 or 2
    local raw, pts, along = line.points or NONE, {}, {}
    for index = 1, #raw - 1 do
      local a, b = raw[index], raw[index + 1]
      local length = math.sqrt((b[1] - a[1]) ^ 2 + (b[2] - a[2]) ^ 2)
      if length > EPS then
        local n = math.max(1, math.ceil(length / tune.fenceStep))
        for k = #pts == 0 and 0 or 1, n do
          pts[#pts + 1] = {a[1] + (b[1] - a[1]) * k / n, a[2] + (b[2] - a[2]) * k / n}
          along[#pts] = (#pts > 1 and along[#pts - 1] + length / n) or 0
        end
      end
    end
    if #pts < 2 or not boundsTouch(rect, ringBounds({pts}), half + 1) then return end
    local normals = {}
    for k = 1, #pts do
      local tx, ty = 0, 0
      for _, pair in ipairs({{k - 1, k}, {k, k + 1}}) do
        local a, b = pts[pair[1]], pts[pair[2]]
        if a and b then
          local dx, dy = b[1] - a[1], b[2] - a[2]
          local l = math.sqrt(dx * dx + dy * dy)
          tx, ty = tx + dx / l, ty + dy / l
        end
      end
      local l = math.max(EPS, math.sqrt(tx * tx + ty * ty))
      local mitre = math.min(2, ((k == 1 or k == #pts) and 1 or 2) / l)
      normals[k] = {-ty / l * mitre * half, tx / l * mitre * half}
      pts[k][3] = ground(pts[k][1], pts[k][2]) - .1
    end
    local function corner(k, side, up)
      local p, n = pts[k], normals[k]
      local z = p[3] + (up and height + .1 or 0)
      return {p[1] + n[1] * side, p[2] + n[2] * side, z, along[k] / scale, (z - p[3]) / scale}
    end
    for k = 1, #pts - 1 do
      local box = ringBounds({{pts[k], pts[k + 1]}})
      if boundsTouch(rect, box, half * 2 + .5) then
        scene.polygon(kind, {corner(k + 1, 1), corner(k, 1), corner(k, 1, true), corner(k + 1, 1, true)}, false)
        scene.polygon(kind, {corner(k, -1), corner(k + 1, -1), corner(k + 1, -1, true), corner(k, -1, true)}, false)
        scene.polygon(kind, {corner(k, 1, true), corner(k, -1, true), corner(k + 1, -1, true), corner(k + 1, 1, true)}, true)
      end
    end
    for _, e in ipairs({{1, 2}, {#pts, #pts - 1}}) do
      local k = e[1]
      if boundsTouch(rect, ringBounds({{pts[k]}}), half * 2 + .5) then
        local a, b, c, d = corner(k, 1), corner(k, -1), corner(k, -1, true), corner(k, 1, true)
        a[4], b[4], c[4], d[4] = 0, 2 * half / scale, 2 * half / scale, 0
        if k == 1 then scene.polygon(kind, {b, a, d, c}, false) else scene.polygon(kind, {a, b, c, d}, false) end
      end
    end
    pause()
  end
  -- Far tiles keep fences: the near scene used to be the only copy, so a wall
  -- on a far tile vanished as the camera reached it. Paths stay near-only.
  -- Horizon draws neither.
  for _, line in ipairs(options.horizon and NONE or lists.lines) do
    local points, tags = line.points or {}, line.tags or {}
    if line.kind == 'fence' then fence(line) end
    if not distant then
      for index = 1, #points - 1 do
        local a, b = points[index], points[index + 1]
        local dx, dy = b[1] - a[1], b[2] - a[2]
        local length = math.sqrt(dx * dx + dy * dy)
        if length > EPS then
          if line.kind == 'path' then
            local width = number(tags.width, 2) / 2
            local nx, ny = -dy / length * width, dx / length * width
            local material = tags.surface == 'asphalt' and 'footway_asphalt' or tags.highway == 'path' and 'trodden' or 'footway'
            local poly = {{a[1] + nx, a[2] + ny}, {a[1] - nx, a[2] - ny}, {b[1] - nx, b[2] - ny}, {b[1] + nx, b[2] + ny}}
            drape(material, poly, .04)
          end
        end
        if index % 32 == 0 then pause() end
      end
    end
    pause()
  end
  local function static(name, shape, x, y, z, yaw, scale)
    scene.items[#scene.items + 1] = {kind = 'static', name = 'e2rl_' .. key .. '_' .. name,
      pos = {x, y, z}, rot = {0, 0, math.sin(yaw / 2), math.cos(yaw / 2)}, scale = scale or 1,
      shape = shape, decor = true, layer = 'scene', collision = 'None'}
  end
  mark('lines')
  if not distant then
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
  end
  mark('decor')
  roads.finish()
  scene.finish()
  roadDecor.finish()
  sceneDecor.finish()
  for _, pair in ipairs({{roads, roadDecor}, {scene, sceneDecor}}) do
    for _, item in ipairs(pair[2].items) do item.decor = true; pair[1].items[#pair[1].items + 1] = item end
    for material in pairs(pair[2].used) do pair[1].used[material] = true end
  end
  -- A bus shelter stands beyond the curb (and sidewalk) of the nearest ground-level road, its open
  -- side to the road (fast.add_street_points): OSM puts most stops on the carriageway itself.
  -- A platform and its stop position nearby make one shelter.
  local shelters = {}
  local function shelter(point, p)
    for _, q in ipairs(shelters) do
      if (q[1] - p[1]) ^ 2 + (q[2] - p[2]) ^ 2 < 900 then return end
    end
    local best, hit = 3600, nil
    for _, strip in ipairs(strips) do
      if strip.surface then
        local pts = strip.points
        for k = 1, #pts - 1 do
          local a, b = pts[k], pts[k + 1]
          local dx, dy = b[1] - a[1], b[2] - a[2]
          local len2 = dx * dx + dy * dy
          if len2 > EPS then
            local t = math.max(0, math.min(1, ((p[1] - a[1]) * dx + (p[2] - a[2]) * dy) / len2))
            local qx, qy = a[1] + dx * t, a[2] + dy * t
            local d = (p[1] - qx) ^ 2 + (p[2] - qy) ^ 2
            if d < best then best, hit = d, {strip = strip, x = qx, y = qy, dx = dx, dy = dy, len = math.sqrt(len2)} end
          end
        end
      end
    end
    if not hit then return end
    local nx, ny = -hit.dy / hit.len, hit.dx / hit.len
    local side = (p[1] - hit.x) * nx + (p[2] - hit.y) * ny >= 0 and 1 or -1
    if math.sqrt(best) < hit.strip.hw + .5 then side = -1 end  -- on the carriageway: its right side
    local walk = hit.strip.sides[side] and tune.sidewalk or 0
    local off = hit.strip.hw + walk + 1.4
    local x, y = hit.x + nx * side * off, hit.y + ny * side * off
    if x < rect[1] or x >= rect[3] or y < rect[2] or y >= rect[4] then return end
    shelters[#shelters + 1] = p
    local fx, fy = -nx * side, -ny * side
    static('point_' .. tostring(point.id):gsub('[^%w_]', '_'), BUS_STOP, x, y, ground(x, y) + .25,
      math.atan2(fx, fy) + math.pi)
  end
  for _, point in ipairs(distant and NONE or lists.points) do
    local p = point.pos
    if p and point.kind == 'stop' and wantStops then
      shelter(point, p)
    elseif p and p[1] >= rect[1] and p[1] < rect[3] and p[2] >= rect[2] and p[2] < rect[4] then
      local shape = point.kind == 'tree' and TREE or (point.kind == 'lamp' and wantLamps) and LAMP
      if shape then static('point_' .. tostring(point.id):gsub('[^%w_]', '_'), shape, p[1], p[2], ground(p[1], p[2]), 0) end
    end
    pause()
  end
  if decor then
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
    local function blocked(x, y, pad)
      pad = pad or 3.5
      local nearby = segmentBins[(floor(x / 50) + 32768) * 65536 + floor(y / 50) + 32768] or NONE
      for _, segment in ipairs(nearby) do
        local a = segment.a
        local t = math.max(0, math.min(1, ((x - a[1]) * segment.dx + (y - a[2]) * segment.dy) / segment.len2))
        if (x - a[1] - t * segment.dx) ^ 2 + (y - a[2] - t * segment.dy) ^ 2 < (segment.width / 2 + pad) ^ 2 then return true end
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
    -- The same world lattice, eligibility and ordering are used for near/far.
    -- Far takes the first N accepted plants, never a new seed or position.
    local candidates, positions = {}, {}
    local function optNum(name, default, lo, hi)
      local value = tonumber(options[name])
      if not value then value = default end
      return math.max(lo, math.min(hi, value))
    end
    local forestSpacing = optNum('forestSpacing', 5, 4, 24)
    local groundSpacing = optNum('groundSpacing', 6, 4, 30)
    -- Town yards stay a loose lattice. A 8 m step turned every courtyard into forest
    -- and the extra crowns were what dropped the frame from 60 to the twenties.
    local spacingOf = {forest = forestSpacing, park = 12, residential = 14, orchard = 12, scrub = 12, heath = 16}
    for _, area in ipairs(lists.areas) do
      local spacing = spacingOf[area.kind]
      if spacing and ringsTouch(area.rings, rect) then
        local box = ringBounds(area.rings)
        for gy = floor(math.max(rect[2], box[2]) / spacing) - 1, math.ceil(math.min(rect[4], box[4]) / spacing) do
          for gx = floor(math.max(rect[1], box[1]) / spacing) - 1, math.ceil(math.min(rect[3], box[3]) / spacing) do
            local h = hash(tostring(gx) .. ':' .. tostring(gy) .. ':' .. spacing)
            local x = (gx + .5 + (h % 1000 / 1000 - .5) * .6) * spacing
            local y = (gy + .5 + (floor(h / 1000) % 1000 / 1000 - .5) * .6) * spacing
            local position = string.format('%.3f:%.3f', x, y)
            if x >= rect[1] and x < rect[3] and y >= rect[2] and y < rect[4] and not positions[position] then
              local kind, owner = coverAt(x, y)
              if owner and spacingOf[kind] == spacing then
                positions[position] = true
                -- Coherent patches make clearings, instead of uniformly filling polygons.
                local cluster = hash('grove:' .. floor(x / 65) .. ':' .. floor(y / 65)) % 1000 / 1000
                -- A low floor left whole 65 m groves almost empty, which read as a broken forest.
                -- Parks and yards keep a lower chance so the town reads as lawns with trees, not canopy.
                local townChance = {park = .7, residential = .4, orchard = .55}
                local chance = kind == 'forest' and (.8 + .2 * cluster) or (townChance[kind] or 1)
                if hash('density:' .. position) % 1000 / 1000 < chance then
                  candidates[#candidates + 1] = {x=x, y=y, hash=h, key=position, kind=kind, tags=owner.tags or NONE}
                end
              end
            end
          end
          pause()
        end
      end
    end
    -- Untagged hills in the country pack are kind 'ground'. Without this lattice
    -- they stay a green sheet with a tree only where OSM drew a forest polygon.
    local wantGround = options.groundTrees ~= false and options.groundTrees ~= 0
    if wantGround then
      for gy = floor(rect[2] / groundSpacing) - 1, math.ceil(rect[4] / groundSpacing) do
        for gx = floor(rect[1] / groundSpacing) - 1, math.ceil(rect[3] / groundSpacing) do
          local h = hash('open:' .. tostring(gx) .. ':' .. tostring(gy) .. ':' .. groundSpacing)
          local x = (gx + .5 + (h % 1000 / 1000 - .5) * .6) * groundSpacing
          local y = (gy + .5 + (floor(h / 1000) % 1000 / 1000 - .5) * .6) * groundSpacing
          local position = string.format('%.3f:%.3f', x, y)
          if x >= rect[1] and x < rect[3] and y >= rect[2] and y < rect[4] and not positions[position]
              and coverAt(x, y) == 'ground' then
            positions[position] = true
            local cluster = hash('grove:' .. floor(x / 65) .. ':' .. floor(y / 65)) % 1000 / 1000
            if hash('density:' .. position) % 1000 / 1000 < (.94 + .06 * cluster) then
              candidates[#candidates + 1] = {x = x, y = y, hash = h, key = position, kind = 'ground', tags = NONE}
            end
          end
        end
        pause()
      end
    end
    table.sort(candidates, function(a, b) return a.hash == b.hash and a.key < b.key or a.hash < b.hash end)
    local config = type(options.nature) == 'table' and options.nature or NONE
    local treeChoice = lookOf('trees')
    if treeChoice == 'regional' then treeChoice = nil end
    local openPine = treeChoice == 'conifer' and .82 or treeChoice == 'broadleaf' and .12 or .4
    local latitude = math.abs(tonumber(options.latitude) or 48)
    -- A configurable regional heuristic, not a measured tree-line dataset.
    -- Explicit OSM leaf_type overrides the biome mix, never slope/water exclusions.
    local treeline = tonumber(config.treeLine) or (latitude > 65 and 800 or latitude > 55 and 1500 or latitude > 35 and 2200 or 3600)
    local coniferStart = tonumber(config.coniferStart) or (latitude > 55 and 0 or 1000)
    -- 0 is a real cap. `or` would also keep it (0 is truthy in Lua); optNum clamps it.
    local maxTrees = optNum('maxTrees', 2400, 0, 2400)
    if distant then maxTrees = math.min(maxTrees, optNum('maxFarTrees', 800, 0, 1200)) end
    -- A global hash prefix spends the whole cap on whichever grove hashed lowest,
    -- so the next street or hillside stays a green sheet. Round-robin gives every
    -- 25 m cell a turn. Far stops earlier on that same sequence, so it stays a prefix.
    local function spread(list, limit, cell, accept)
      if limit <= 0 then return end
      local buckets, ids = {}, {}
      for index = 1, #list do
        local item = list[index]
        local id = (floor(item.x / cell) + 32768) * 65536 + floor(item.y / cell) + 32768
        local bucket = buckets[id]
        if not bucket then
          bucket = {}
          buckets[id] = bucket
          ids[#ids + 1] = id
        end
        bucket[#bucket + 1] = item
      end
      table.sort(ids)
      local cursor, planted, moving = {}, 0, true
      while planted < limit and moving do
        moving = false
        for index = 1, #ids do
          if planted >= limit then break end
          local id = ids[index]
          local bucket = buckets[id]
          local at = (cursor[id] or 0) + 1
          while at <= #bucket do
            local item = bucket[at]
            at = at + 1
            if accept(item) then
              planted = planted + 1
              moving = true
              break
            end
          end
          cursor[id] = at - 1
        end
        pause()
      end
    end
    spread(candidates, maxTrees, 25, function(candidate)
      local x, y, h = candidate.x, candidate.y, candidate.hash
      local z = ground(x, y)
      local elevation = rawHeight(x, y) - (options.seaLevel or 20)
      local _, _, nz = groundNormal(x, y)
      local bush = candidate.kind == 'scrub' or candidate.kind == 'heath'
      if not (nz >= (bush and .64 or .72) and elevation < treeline + (bush and 250 or 0)
          and (not sea or z > sea) and not blocked(x, y)) then return false end
      local leaf = candidate.tags.leaf_type or candidate.tags.wood
      local needle = leaf == 'needleleaved' or leaf == 'coniferous'
      if leaf ~= 'broadleaved' and leaf ~= 'deciduous' and not needle and candidate.kind == 'forest' then
        local mix = leaf == 'mixed' and .5 or math.max(0, math.min(.85, (elevation-coniferStart)/700))
        if treeChoice == 'conifer' then mix = math.max(mix, .7)
        elseif treeChoice == 'broadleaf' and leaf ~= 'mixed' then mix = math.min(mix, .15) end
        needle = hash('species:' .. candidate.key) % 1000 / 1000 < mix
      end
      -- Open hills have no leaf_type. A fixed share of pines keeps a Carpathian mix.
      if candidate.kind == 'ground' then
        needle = hash('species:' .. candidate.key) % 1000 / 1000 < openPine
      end
      local set = bush and BUSHES or needle and CONIFERS or TREES
      local scale = .65 + hash('age:' .. candidate.key) % 900 / 1000
      static('tree_' .. candidate.key:gsub('[^%w]', '_'), set[h % #set + 1], x, y, z,
        h % 6283 / 1000, bush and scale * .8 or scale)
      return true
    end)
    -- Grass, forest floor and loose stone. Far keeps only a ranked handful of rocks:
    -- tufts do not read at the horizon, and the far list stays inside the near list.
    local function scatter(prefix, spacingOf, chanceOf, shapes, cap, farCap, scaleMin, scaleMax, minNz, elevationOf)
      local limit = cap
      if distant then limit = farCap or 0 end
      if limit <= 0 then return end
      local candidates, positions = {}, {}
      for _, area in ipairs(lists.areas) do
        local spacing = spacingOf[area.kind]
        if spacing and ringsTouch(area.rings, rect) then
          local box = ringBounds(area.rings)
          for gy = floor(math.max(rect[2], box[2]) / spacing) - 1, math.ceil(math.min(rect[4], box[4]) / spacing) do
            for gx = floor(math.max(rect[1], box[1]) / spacing) - 1, math.ceil(math.min(rect[3], box[3]) / spacing) do
              local h = hash(prefix .. ':' .. tostring(gx) .. ':' .. tostring(gy) .. ':' .. spacing)
              local x = (gx + .5 + (h % 1000 / 1000 - .5) * .55) * spacing
              local y = (gy + .5 + (floor(h / 1000) % 1000 / 1000 - .5) * .55) * spacing
              local position = string.format('%.3f:%.3f', x, y)
              if x >= rect[1] and x < rect[3] and y >= rect[2] and y < rect[4] and not positions[position] then
                local kind = coverAt(x, y)
                if spacingOf[kind] == spacing and hash(prefix .. ':density:' .. position) % 1000 / 1000 < (chanceOf[kind] or 0) then
                  positions[position] = true
                  candidates[#candidates + 1] = {x = x, y = y, hash = h, key = position, kind = kind}
                end
              end
            end
            pause()
          end
        end
      end
      -- Kind 'ground' is the absence of a cover, so it has no area to walk.
      -- The Ukraine pack's open hills are this kind: without the lattice they stay a flat sheet.
      local openSpacing = spacingOf.ground
      if openSpacing then
        for gy = floor(rect[2] / openSpacing) - 1, math.ceil(rect[4] / openSpacing) do
          for gx = floor(rect[1] / openSpacing) - 1, math.ceil(rect[3] / openSpacing) do
            local h = hash(prefix .. ':open:' .. tostring(gx) .. ':' .. tostring(gy))
            local x = (gx + .5 + (h % 1000 / 1000 - .5) * .55) * openSpacing
            local y = (gy + .5 + (floor(h / 1000) % 1000 / 1000 - .5) * .55) * openSpacing
            local position = string.format('%.3f:%.3f', x, y)
            if x >= rect[1] and x < rect[3] and y >= rect[2] and y < rect[4] and not positions[position] then
              if coverAt(x, y) == 'ground' and hash(prefix .. ':density:' .. position) % 1000 / 1000 < (chanceOf.ground or 0) then
                positions[position] = true
                candidates[#candidates + 1] = {x = x, y = y, hash = h, key = position, kind = 'ground'}
              end
            end
          end
          pause()
        end
      end
      table.sort(candidates, function(a, b) return a.hash == b.hash and a.key < b.key or a.hash < b.hash end)
      spread(candidates, limit, 25, function(candidate)
        local x, y, h = candidate.x, candidate.y, candidate.hash
        local z = ground(x, y)
        local elevation = rawHeight(x, y) - (options.seaLevel or 20)
        local _, _, nz = groundNormal(x, y)
        local elevationCap = elevationOf and elevationOf[candidate.kind]
        local clearance = prefix == 'grass' and 2.2 or 3.5
        if not (nz >= minNz and (not elevationCap or elevation < elevationCap) and (not sea or z > sea) and not blocked(x, y, clearance)) then
          return false
        end
        local shape = shapes[h % #shapes + 1]
        local scale
        if prefix == 'grass' then
          -- scaleMin/scaleMax are the blade height in metres, not a raw multiplier.
          local mesh = GRASS_MESH[shape]
          local span = mesh and mesh.span or 0.26
          local want = scaleMin + (hash(prefix .. ':scale:' .. candidate.key) % 1000) / 1000 * (scaleMax - scaleMin)
          scale = want / span
          z = z - (mesh and mesh.bottom or 0) * scale
        else
          scale = scaleMin + (hash(prefix .. ':scale:' .. candidate.key) % 1000) / 1000 * (scaleMax - scaleMin)
          if prefix == 'rock' and h % 5 == 0 then shape = shapes[#shapes]; scale = scale * .8 end
        end
        static(prefix .. '_' .. candidate.key:gsub('[^%w]', '_'), shape, x, y, z, h % 6283 / 1000, scale)
        return true
      end)
    end
    local function opt(name, default, cap)
      local value = options[name]
      if value == nil then value = default end
      return math.max(0, math.min(cap, value))
    end
    local maxGrass = opt('maxGrass', 4000, 4800)
    local maxRocks = opt('maxRocks', 80, 200)
    local maxUnder = opt('maxUnderstory', 220, 400)
    -- Town tiles spend the grass cap. Forest floor stays thin so those tufts are not
    -- stolen by the woods, and so the TSStatic count stays off the frame time.
    scatter('grass', {meadow = 3.2, farmland = 8, heath = 7, park = 2.6, forest = 12, residential = 2.6, orchard = 5, vineyard = 9, ground = 8},
      {meadow = .94, farmland = .35, heath = .5, park = .98, forest = .2, residential = .98, orchard = .55, vineyard = .28, ground = .35},
      GRASS, maxGrass, 0, 0.20, 0.30, .72, {forest = treeline + 80})
    scatter('bush', {forest = 14, park = 18},
      {forest = .4, park = .22},
      BUSHES, maxUnder, 0, .85, 1.3, .72, {forest = treeline + 80})
    scatter('rock', {bare_rock = 11, scree = 8}, {bare_rock = .72, scree = .84},
      ROCKS, maxRocks, math.min(16, maxRocks), .55, 1.25, .45, nil)
    -- Slopes too steep for trees still had no stones unless OSM tagged bare_rock or scree.
    -- Same sorted lattice near and far, so the far handful stays inside the near list.
    local function scatterSlopeRocks()
      local limit = distant and math.min(16, maxRocks) or maxRocks
      if limit <= 0 then return end
      local spacing, candidates, positions = 16, {}, {}
      for gy = floor(rect[2] / spacing) - 1, math.ceil(rect[4] / spacing) do
        for gx = floor(rect[1] / spacing) - 1, math.ceil(rect[3] / spacing) do
          local h = hash('slope:' .. tostring(gx) .. ':' .. tostring(gy))
          local x = (gx + .5 + (h % 1000 / 1000 - .5) * .5) * spacing
          local y = (gy + .5 + (floor(h / 1000) % 1000 / 1000 - .5) * .5) * spacing
          if x >= rect[1] and x < rect[3] and y >= rect[2] and y < rect[4] then
            local _, _, nz = groundNormal(x, y)
            local kind = coverAt(x, y)
            -- 0.94 is about 20°. Rounded Carpathian hills were steeper than flat ground
            -- and gentler than 45°, so the old 0.7 gate never placed a stone on them.
            if nz < .94 and nz >= .25 and kind ~= 'water' and kind ~= 'paved' and kind ~= 'residential' then
              local position = string.format('%.3f:%.3f', x, y)
              local steep = (.94 - nz) / .69
              if not positions[position] and hash('slope:keep:' .. position) % 1000 / 1000 < (.45 + .55 * steep) then
                positions[position] = true
                candidates[#candidates + 1] = {x = x, y = y, hash = h, key = position}
              end
            end
          end
        end
        pause()
      end
      table.sort(candidates, function(a, b) return a.hash == b.hash and a.key < b.key or a.hash < b.hash end)
      spread(candidates, limit, 25, function(candidate)
        local x, y, h = candidate.x, candidate.y, candidate.hash
        local z = ground(x, y)
        if not ((not sea or z > sea) and not blocked(x, y)) then return false end
        local scale = .7 + (hash('slope:scale:' .. candidate.key) % 1000) / 1000 * .7
        local shape = ROCKS[h % #ROCKS + 1]
        if h % 5 == 0 then shape = ROCKS[#ROCKS]; scale = scale * .8 end
        static('srock_' .. candidate.key:gsub('[^%w]', '_'), shape, x, y, z, h % 6283 / 1000, scale)
        return true
      end)
    end
    scatterSlopeRocks()
  end
  if wantLamps and not distant then
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
  mark('trees')
  local function manifest(collector, layer)
    local mats, total = {}, 0
    for material in pairs(collector.used) do mats[#mats + 1] = material end
    table.sort(mats)
    for _, item in ipairs(collector.items) do total = total + (item.triangles or 0) end
    return {version = 1, generator = 'earthng-lua-v1', source = options.regionId, layer = layer,
      tile = {i, j}, rect = rect, materials = options.materials or '/earthng/planet/materials.json',
      items = collector.items, mats = mats, triangles = total, packs = options.packs,
      warmShapes = {TREES[1], TREES[2], TREES[3], TREES[4], TREES[5], TREES[6], TREES[7], TREES[8],
        CONIFERS[1], CONIFERS[2], BUSHES[1], BUSHES[2], BUSHES[3], BUSHES[4],
        GRASS[1], GRASS[4], ROCKS[1], ROCKS[5], LAMP, BUS_STOP}}
  end
  local roadManifest, sceneManifest = manifest(roads, 'road'), manifest(scene, 'scene')
  roadManifest.spawn = bestSpawn or {pos = {target[1], target[2], ground(target[1], target[2]) + ROAD_LIFT}, yaw = 0}
  roadManifest.drivable = bestSpawn ~= nil
  if distant then
    sceneManifest.layer = layerName
    for _, item in ipairs(sceneManifest.items) do
      item.decor, item.layer, item.collision = true, layerName, 'None'
    end
    return roadManifest, manifest({used = {}, items = {}}, 'scene'), sceneManifest, nav
  end
  return roadManifest, sceneManifest, nil, nav
end

M.build = build
M.triangulate = triangulate
M.clip = clip
return M
