"""Check Lua source-frame coordinates against the authoring projection library."""
from pathlib import Path
import sys
import unittest

import pyproj

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'out/test-tools'))
from lupa.luajit21 import LuaRuntime


class CoordinateTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.mod = self.lua.execute((ROOT / 'mod/lua/ge/extensions/e2r/coordinates.lua').read_text())

    def test_utm_matches_authoring_in_both_hemispheres(self):
        for lat, lon in [(50.4475, 30.5225), (44.4355, 26.1025), (-6.163, 39.191),
                         (51.508, -.1281), (34.0505, -118.2475), (-33.87, 151.21),
                         (80, 17.99), (-78, -59.99), (0, -179.99), (0, 179.99)]:
            zone = int((lon + 180) // 6) % 60 + 1
            crs = f'EPSG:{(32600 if lat >= 0 else 32700) + zone}'
            project = pyproj.Transformer.from_crs('EPSG:4326', crs, always_xy=True)
            x, y = project.transform(lon, lat)
            ox, oy = round(x) - 50000, round(y) + 40000
            frame = self.mod.frame(self.lua.table_from(dict(crs=crs, origin=self.lua.table_from([ox, oy]))))
            with self.subTest(crs=crs, lat=lat, lon=lon):
                actual = frame.localPoint(lat, lon)
                self.assertAlmostEqual(actual[0], x - ox, delta=.002)
                self.assertAlmostEqual(actual[1], y - oy, delta=.002)
                lng, latitude = frame.geographic(x - ox, y - oy)
                self.assertAlmostEqual(lng, lon, delta=2e-7)
                self.assertAlmostEqual(latitude, lat, delta=2e-7)
                affine = frame.affineAt(x - ox, y - oy)
                self.assertAlmostEqual(affine[3], lon, delta=2e-7)

    def test_unsupported_frames_are_not_misinterpreted(self):
        for crs in ('EPSG:4326', 'EPSG:32600', 'EPSG:32761', 'junk'):
            self.assertIsNone(self.mod.frame(self.lua.table_from(dict(crs=crs, origin=self.lua.table_from([0, 0])))))


if __name__ == '__main__':
    unittest.main()
