-- Offline, public-domain Natural Earth coastline. No network or companion is required.
local M = {}
local coast

function M.draw(im, dl, r0, width, height, view, merc, col)
  if not coast then coast = jsonReadFile('/earthng/planet/coastline.json') or {} end
  local cx, cy = merc(view.center.lat, view.center.lon, view.zoom)
  local x0, y0 = cx - width / 2, cy - height / 2
  local function point(lat, lon)
    local x, y = merc(lat, lon, view.zoom)
    return r0.x + x - x0, r0.y + y - y0
  end
  local function line(ax, ay, bx, by, color)
    if math.max(ax, bx) >= r0.x and math.min(ax, bx) <= r0.x + width and
       math.max(ay, by) >= r0.y and math.min(ay, by) <= r0.y + height then
      im.ImDrawList_AddLine(dl, im.ImVec2(ax, ay), im.ImVec2(bx, by), color, 1)
    end
  end
  local grid = col(0.4, 0.55, 0.65, 0.45)
  local step = math.max(0.01, 45 / 2 ^ math.max(0, view.zoom - 3))
  -- Bound the work independently of zoom and of the visible longitude range.
  local span = width / (256 * 2 ^ view.zoom) * 360
  local lo = math.max(-180, view.center.lon - span)
  local hi = math.min(180, view.center.lon + span)
  for lon = math.ceil(lo / step) * step, hi, step do
    local x = point(0, lon)
    line(x, r0.y, x, r0.y + height, grid)
  end
  local _, northY = merc(view.center.lat, 0, view.zoom)
  local scale = 256 * 2 ^ view.zoom
  local function latitude(y)
    local m = math.pi * (1 - 2 * y / scale)
    return math.deg(math.atan(0.5 * (math.exp(m) - math.exp(-m))))
  end
  local south, north = latitude(y0 + height), latitude(y0)
  for lat = math.max(-85, math.ceil(south / step) * step), math.min(85, north), step do
    local _, py = merc(lat, 0, view.zoom)
    local y = r0.y + height / 2 + py - northY
    if y >= r0.y and y <= r0.y + height then line(r0.x, y, r0.x + width, y, grid) end
  end
  local color = col(0.18, 0.3, 0.27, 0.85)
  for _, ring in ipairs(coast) do
    local prev
    for _, p in ipairs(ring) do
      local x, y = point(p[2], p[1])
      if prev and math.abs(p[1] - prev.lon) < 180 then line(prev.x, prev.y, x, y, color) end
      prev = {x = x, y = y, lon = p[1]}
    end
  end
end

return M
