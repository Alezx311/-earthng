-- WGS84 / UTM conversions for source frames, using the standard transverse
-- Mercator series. Country packs contain coordinates, never executable projection code.
local M = {}
local A, E2, K = 6378137, 0.0066943799901413165, 0.9996
local EP = E2 / (1 - E2)
local E4, E6 = E2 * E2, E2 * E2 * E2
local M0 = 1 - E2 / 4 - 3 * E4 / 64 - 5 * E6 / 256
local E1 = (1 - math.sqrt(1 - E2)) / (1 + math.sqrt(1 - E2))

local function finite(v) return type(v) == 'number' and v == v and math.abs(v) < math.huge end

function M.frame(spec)
  if type(spec) ~= 'table' or type(spec.crs) ~= 'string' or type(spec.origin) ~= 'table'
    or not finite(spec.origin[1]) or not finite(spec.origin[2]) then return nil end
  local code = tonumber(spec.crs:match('^EPSG:(%d+)$'))
  if not code then return nil end
  local family, zone = math.floor(code / 100), code % 100
  if (family ~= 326 and family ~= 327) or zone < 1 or zone > 60 then return nil end
  local ox, oy = spec.origin[1], spec.origin[2]
  local central, south = math.rad(zone * 6 - 183), family == 327
  local transform = {}

  function transform.localPoint(lat, lon)
    local p, l = math.rad(lat), math.rad(lon)
    local sn, cs, tn = math.sin(p), math.cos(p), math.tan(p)
    local n, t, c, a = A / math.sqrt(1 - E2 * sn * sn), tn * tn, EP * cs * cs, cs * (l - central)
    local m = A * (M0 * p - (3 * E2 / 8 + 3 * E4 / 32 + 45 * E6 / 1024) * math.sin(2 * p)
      + (15 * E4 / 256 + 45 * E6 / 1024) * math.sin(4 * p) - 35 * E6 / 3072 * math.sin(6 * p))
    local x = 500000 + K * n * (a + (1 - t + c) * a ^ 3 / 6
      + (5 - 18 * t + t * t + 72 * c - 58 * EP) * a ^ 5 / 120)
    local y = K * (m + n * tn * (a * a / 2 + (5 - t + 9 * c + 4 * c * c) * a ^ 4 / 24
      + (61 - 58 * t + t * t + 600 * c - 330 * EP) * a ^ 6 / 720))
    return x - ox, y + (south and 10000000 or 0) - oy
  end

  function transform.geographic(x, y)
    x, y = x + ox - 500000, y + oy - (south and 10000000 or 0)
    local mu = y / (K * A * M0)
    local p = mu + (3 * E1 / 2 - 27 * E1 ^ 3 / 32) * math.sin(2 * mu)
      + (21 * E1 * E1 / 16 - 55 * E1 ^ 4 / 32) * math.sin(4 * mu)
      + 151 * E1 ^ 3 / 96 * math.sin(6 * mu) + 1097 * E1 ^ 4 / 512 * math.sin(8 * mu)
    local sn, cs, tn = math.sin(p), math.cos(p), math.tan(p)
    local n = A / math.sqrt(1 - E2 * sn * sn)
    local r = A * (1 - E2) / (1 - E2 * sn * sn) ^ 1.5
    local t, c, d = tn * tn, EP * cs * cs, x / (n * K)
    local lat = p - n * tn / r * (d * d / 2 - (5 + 3 * t + 10 * c - 4 * c * c - 9 * EP) * d ^ 4 / 24
      + (61 + 90 * t + 298 * c + 45 * t * t - 252 * EP - 3 * c * c) * d ^ 6 / 720)
    local lon = central + (d - (1 + 2 * t + c) * d ^ 3 / 6
      + (5 - 2 * c + 28 * t - 3 * c * c + 8 * EP + 24 * t * t) * d ^ 5 / 120) / cs
    return math.deg(lon), math.deg(lat)
  end

  function transform.affineAt(x, y)
    local lon, lat = transform.geographic(x, y)
    local lx, ax = transform.geographic(x + 1, y)
    local ly, ay = transform.geographic(x, y + 1)
    return {x, y, lon, lat, lx - lon, ly - lon, ax - lat, ay - lat}
  end
  return transform
end

return M
