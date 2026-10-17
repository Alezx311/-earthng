-- Offline base map of the picker: Natural Earth 1:10m (public domain) as 256 px tiles made by
-- earthng/build_basemap.py, and city/country labels drawn here (crisp, in the UI language).
-- No network or companion is required. Without the tiles (a dev build) the 1:110m coastline
-- is drawn as lines instead.
local M = {}
local imguiUtils = require('ui/imguiUtils')

local ROOT = '/earthng/planet/basemap/'
local TEX_MAX, LOADS_PER_FRAME = 400, 6
M.SEA = {170 / 255, 211 / 255, 223 / 255}

local index        -- {maxZoom, tiles = {['z/x/y'] = file}} or false
local labels       -- {places, countries} with buckets
local tex, texN, frame, loads = {}, 0, 0, 0
local coast

-- --- tiles ---------------------------------------------------------------------------------------
local function loadIndex()
  if index ~= nil then return index end
  index = false
  if FS:fileExists(ROOT .. 'index.json') then
    local ok, d = pcall(jsonReadFile, ROOT .. 'index.json')
    if ok and type(d) == 'table' and type(d.tiles) == 'table' then
      index = d
      if type(d.sea) == 'table' then M.SEA = {d.sea[1] / 255, d.sea[2] / 255, d.sea[3] / 255} end
    end
  end
  return index
end

function M.available() return loadIndex() and true or false end

-- the texture of a tile; nil while not loaded, false for the open sea (no file)
local function texture(z, x, y, load)
  local file = index.tiles[z .. '/' .. x .. '/' .. y]
  if not file then return false end
  local t = tex[file]
  if t then t.used = frame; return t end
  if not load or loads >= LOADS_PER_FRAME then return nil end
  loads = loads + 1
  local ok, obj = pcall(imguiUtils.texObj, ROOT .. 't/' .. file .. '.png')
  if not ok or not obj then return false end
  obj.used = frame
  tex[file], texN = obj, texN + 1
  if texN > TEX_MAX then
    -- forget the least recently drawn half
    local list = {}
    for k, v in pairs(tex) do list[#list + 1] = {k, v.used} end
    table.sort(list, function(p, q) return p[2] < q[2] end)
    local drop = math.floor(#list / 2)
    for k = 1, drop do tex[list[k][1]] = nil end
    texN = #list - drop
  end
  return obj
end

-- Tiles of min(view zoom, maxZoom) stretched to the view: past the deepest level one tile covers
-- several screen tiles. A tile still loading shows the part of a loaded ancestor.
local function drawTiles(im, dl, r0, width, height, x0, y0, z, white)
  local zb = math.min(z, index.maxZoom or 7)
  local n = 2 ^ zb
  local ts = 256 * 2 ^ (z - zb)
  for tx = math.floor(x0 / ts), math.floor((x0 + width) / ts) do
    for ty = math.max(0, math.floor(y0 / ts)), math.min(n - 1, math.floor((y0 + height) / ts)) do
      local sx, sy = r0.x + tx * ts - x0, r0.y + ty * ts - y0
      local x = tx % n
      local t = texture(zb, x, ty, true)
      if t then
        im.ImDrawList_AddImage(dl, t.texId, im.ImVec2(sx, sy), im.ImVec2(sx + ts, sy + ts), im.ImVec2(0, 0), im.ImVec2(1, 1), white)
      elseif t == nil then
        for k = 1, zb do
          local f = 2 ^ k
          local a = texture(zb - k, math.floor(x / f), math.floor(ty / f), false)
          if a then
            local u, v = (x % f) / f, (ty % f) / f
            im.ImDrawList_AddImage(dl, a.texId, im.ImVec2(sx, sy), im.ImVec2(sx + ts, sy + ts),
              im.ImVec2(u, v), im.ImVec2(u + 1 / f, v + 1 / f), white)
            break
          elseif a == false then break end
        end
      end
    end
  end
end

-- --- the coastline fallback ------------------------------------------------------------------
-- Rings as flat {lon, lat, ...} pieces with their bounds, prepared once.
local CHUNK = 64

local function prepare(raw)
  local out = {}
  for _, ring in ipairs(raw or {}) do
    local first = 1
    while first < #ring do
      local last = math.min(#ring, first + CHUNK)
      local flat, w, s, e, n = {}, math.huge, math.huge, -math.huge, -math.huge
      for k = first, last do
        local p = ring[k]
        flat[#flat + 1], flat[#flat + 2] = p[1], p[2]
        w, s, e, n = math.min(w, p[1]), math.min(s, p[2]), math.max(e, p[1]), math.max(n, p[2])
      end
      out[#out + 1] = {flat = flat, box = {w, s, e, n}}
      first = last
    end
  end
  return out
end

local function drawCoast(im, dl, r0, width, height, view, merc, col, x0, y0, north, south)
  if not coast then coast = prepare(jsonReadFile('/earthng/planet/coastline.json')) end
  local span = width / (256 * 2 ^ view.zoom) * 360
  local lo, hi = math.max(-180, view.center.lon - span), math.min(180, view.center.lon + span)
  local color = col(0.18, 0.3, 0.27, 0.85)
  for _, ring in ipairs(coast) do
    local b = ring.box
    if b[2] <= north and b[4] >= south and ((b[1] <= hi and b[3] >= lo) or b[3] - b[1] > 180) then
      local flat = ring.flat
      local px, py = merc(flat[2], flat[1], view.zoom)
      local plon = flat[1]
      for k = 3, #flat - 1, 2 do
        local lon = flat[k]
        local x, y = merc(flat[k + 1], lon, view.zoom)
        if math.abs(lon - plon) < 180 and math.max(px, x) >= x0 and math.min(px, x) <= x0 + width then
          im.ImDrawList_AddLine(dl, im.ImVec2(r0.x + px - x0, r0.y + py - y0), im.ImVec2(r0.x + x - x0, r0.y + y - y0), color, 1)
        end
        px, py, plon = x, y, lon
      end
    end
  end
end

-- --- labels --------------------------------------------------------------------------------------
-- labels.json: places [lat, lon, minZoom, en, uk, capital, population], countries [lat, lon,
-- minZoom, en, uk]. Places sit in 5-degree buckets so a view looks at its own few hundred.
local BUCKET = 5
local function bucketKey(lat, lon) return math.floor(lat / BUCKET) * 1000 + math.floor(lon / BUCKET) end

local function loadLabels()
  if labels ~= nil then return labels end
  labels = false
  if FS:fileExists(ROOT .. 'labels.json') then
    local ok, d = pcall(jsonReadFile, ROOT .. 'labels.json')
    if ok and type(d) == 'table' then
      local buckets = {}
      for k, p in ipairs(d.places or {}) do
        p.order = k
        local key = bucketKey(p[1], p[2])
        buckets[key] = buckets[key] or {}
        table.insert(buckets[key], p)
      end
      labels = {places = d.places or {}, countries = d.countries or {}, buckets = buckets}
    end
  end
  return labels
end

-- lower case for Latin and Cyrillic (string.lower knows ASCII only): А-Я, Ѐ-Џ (Є, І, Ї), Ґ
local function cyrLower(c)
  local a, b = c:byte(1, 2)
  if a == 0xD0 and b >= 0x90 and b <= 0x9F then return string.char(0xD0, b + 0x20) end
  if a == 0xD0 and b >= 0xA0 and b <= 0xAF then return string.char(0xD1, b - 0x20) end
  if a == 0xD0 and b >= 0x80 and b <= 0x8F then return string.char(0xD1, b + 0x10) end
  if a == 0xD2 and b == 0x90 then return string.char(0xD2, 0x91) end
  return c
end
function M.lower(text)
  return (text:lower():gsub('[\208\210][\128-\191]', cyrLower))
end

-- places whose name contains the text (any case), the biggest first
local lastQuery, lastFound
function M.search(text, lang, limit)
  local l = loadLabels()
  if not l or text == '' then return {} end
  local needle = M.lower(text)
  if needle == lastQuery then return lastFound end
  if not l.lowered then
    for _, p in ipairs(l.places) do p.en, p.uk = p[4]:lower(), M.lower(p[5]) end
    l.lowered = true
  end
  local out = {}
  for _, p in ipairs(l.places) do
    if p.uk:find(needle, 1, true) or p.en:find(needle, 1, true) then
      out[#out + 1] = {name = lang == 'en' and p[4] or p[5], lat = p[1], lon = p[2], pop = p[7] or 0}
    end
  end
  table.sort(out, function(a, b) return a.pop > b.pop end)
  for k = #out, (limit or 20) + 1, -1 do out[k] = nil end
  lastQuery, lastFound = needle, out
  return out
end

local placed = {}
local function free(x0, y0, x1, y1)
  for _, r in ipairs(placed) do
    if x0 < r[3] and x1 > r[1] and y0 < r[4] and y1 > r[2] then return false end
  end
  placed[#placed + 1] = {x0, y0, x1, y1}
  return true
end

-- (the game's GetFont wrapper needs arguments it does not document: no font sizes here)
local HALO = {{-1, 0}, {1, 0}, {0, -1}, {0, 1}}
local function haloText(im, dl, x, y, text, color, halo)
  for _, d in ipairs(HALO) do im.ImDrawList_AddText1(dl, im.ImVec2(x + d[1], y + d[2]), halo, text) end
  im.ImDrawList_AddText1(dl, im.ImVec2(x, y), color, text)
end

-- reserve screen areas (pins, the car, controls) before the labels are placed
function M.reserve(x0, y0, x1, y1) placed[#placed + 1] = {x0, y0, x1, y1} end
-- takes the area when nothing placed covers it yet
function M.place(x0, y0, x1, y1) return free(x0, y0, x1, y1) end
function M.beginLabels() placed = {} end

-- Countries (to z7) and cities by Natural Earth's own min zoom, without overlaps.
-- Returns the hovered place {name, lat, lon} for a click to land at.
function M.drawLabels(im, dl, r0, width, height, view, merc, unmerc, col, lang, mouse, maxLabels)
  local l = loadLabels()
  if not l then return nil end
  local z = view.zoom
  local cx, cy = merc(view.center.lat, view.center.lon, z)
  local x0, y0 = cx - width / 2, cy - height / 2
  local name = lang == 'en' and 4 or 5
  local white, ink, inkCountry = col(1, 1, 1, 0.9), col(0.2, 0.2, 0.22), col(0.42, 0.34, 0.47)
  local fontSize = im.GetFontSize() or 13
  local hovered
  -- country names first: a city never covers its country's label
  if z <= 7 then
    for _, c in ipairs(l.countries) do
      if c[3] <= z + 1.5 then
        local x, y = merc(c[1], c[2], z)
        x, y = r0.x + x - x0, r0.y + y - y0
        if x > r0.x - 100 and x < r0.x + width + 100 and y > r0.y and y < r0.y + height then
          local tw = im.CalcTextSize(c[name]).x
          if free(x - tw / 2 - 4, y - fontSize / 2 - 2, x + tw / 2 + 4, y + fontSize / 2 + 2) then
            haloText(im, dl, x - tw / 2, y - fontSize / 2, c[name], inkCountry, white)
          end
        end
      end
    end
  end
  -- cities: the view's buckets, by Natural Earth's order (min zoom, then population)
  local north, west = unmerc(x0, y0, z)
  local south, east = unmerc(x0 + width, y0 + height, z)
  local list = {}
  for bl = math.floor(south / BUCKET), math.floor(north / BUCKET) do
    for bo = math.floor(math.max(-180, west) / BUCKET), math.floor(math.min(180, east) / BUCKET) do
      for _, p in ipairs(l.buckets[bl * 1000 + bo] or {}) do
        if p[3] <= z + 0.6 and p[1] <= north and p[1] >= south and p[2] >= west and p[2] <= east then
          list[#list + 1] = p
        end
      end
    end
  end
  table.sort(list, function(a, b) return a.order < b.order end)
  local shown = 0
  for _, p in ipairs(list) do
    if shown >= (maxLabels or 60) then break end
    local x, y = merc(p[1], p[2], z)
    x, y = r0.x + x - x0, r0.y + y - y0
    local text = p[name]
    local tw, th = im.CalcTextSize(text).x, fontSize
    local r = p[6] == 1 and 4 or 3
    -- the name to the right of the dot
    if free(x - r - 1, y - th / 2 - 1, x + r + 4 + tw + 1, y + th / 2 + 1) then
      shown = shown + 1
      if p[6] == 1 then
        im.ImDrawList_AddCircleFilled(dl, im.ImVec2(x, y), r + 1.5, white)
        im.ImDrawList_AddCircle(dl, im.ImVec2(x, y), r, ink, 12, 1.5)
      else
        im.ImDrawList_AddCircleFilled(dl, im.ImVec2(x, y), r + 1, white)
        im.ImDrawList_AddCircleFilled(dl, im.ImVec2(x, y), r, col(0.35, 0.35, 0.38))
      end
      haloText(im, dl, x + r + 4, y - th / 2, text, ink, white)
      if mouse and mouse.x >= x - r - 2 and mouse.x <= x + r + 4 + tw and mouse.y >= y - th / 2 - 2 and mouse.y <= y + th / 2 + 2 then
        hovered = {name = text, lat = p[1], lon = p[2]}
      end
    end
  end
  return hovered
end

-- the sea, then the land tiles (or the coastline fallback)
function M.draw(im, dl, r0, width, height, view, merc, unmerc, col)
  frame, loads = frame + 1, 0
  local sea = M.SEA
  im.ImDrawList_AddRectFilled(dl, r0, im.ImVec2(r0.x + width, r0.y + height), col(sea[1], sea[2], sea[3]))
  local cx, cy = merc(view.center.lat, view.center.lon, view.zoom)
  local x0, y0 = cx - width / 2, cy - height / 2
  if loadIndex() then
    drawTiles(im, dl, r0, width, height, x0, y0, view.zoom, col(1, 1, 1))
  else
    local north = unmerc(x0, y0, view.zoom)
    local south = unmerc(x0, y0 + height, view.zoom)
    drawCoast(im, dl, r0, width, height, view, merc, col, x0, y0, north, south)
  end
end

return M
