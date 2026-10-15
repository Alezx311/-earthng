"""LuaJIT tests for mounted source discovery and budgeted native generation.

Run with: python -m unittest discover -s earthng -p test_native.py
"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'out/test-tools'))
from lupa.luajit21 import LuaRuntime


def fixture(size=4):
    base = '/earthng/countries/demo/'
    region_path = base + 'regions/sample.json'
    geo = [0, 0, 20, 40, .00001, .000004, -.000003, .00001]
    country = dict(schemaVersion=1, kind='earthng-country', id='demo', title='Demo', version='1',
                   runtime='native', sourceFormat='earthng-features-v1', coverage=[[19, 39, 21, 41]],
                   regions=[dict(id='sample', manifest=region_path, coverage=[[19, 39, 21, 41]])])
    region = dict(schemaVersion=1, id='sample', runtime='lua', tileSize=500, cellSize=1000,
                  geo=geo, bounds=[19, 39, 21, 41], cells={})
    files = {base + 'manifest.json': country, region_path: region}
    for i in range(size):
        for j in range(size):
            bounds = [i * 1000, j * 1000, (i + 1) * 1000, (j + 1) * 1000]
            path = base + f'regions/sample/cells/{i}_{j}.json'
            region['cells'][f'{i}_{j}'] = dict(path=path, bounds=bounds)
            files[path] = dict(roads=[], junctions=[], buildings=[], areas=[], lines=[], points=[],
                               terrain=dict(rect=bounds, nx=2, ny=2, heights=[0] * 4))
    return files


def geographic(x, y):
    return 40 - .000003 * x + .00001 * y, 20 + .00001 * x + .000004 * y


class NativeTests(unittest.TestCase):
    def make(self, files=None, real_generator=False):
        lua = LuaRuntime()

        def convert(value):
            if isinstance(value, dict):
                return lua.table_from({k: convert(v) for k, v in value.items()})
            if isinstance(value, (list, tuple)):
                return lua.table_from([convert(v) for v in value])
            return value

        files = deepcopy(files if files is not None else fixture())
        lua.globals().files = convert(files)
        lua.globals().manifestPaths = convert([p for p in files if p.endswith('/manifest.json')])
        lua.execute('''
          FS={fileExists=function(_,p) return files[p]~=nil end,
              findFiles=function() return manifestPaths end}
          jsonReadFile=function(p) return files[p] end
          jsonWriteFile=function() error('native provider must not write files') end
          hptimer=function() local elapsed=0; return {stop=function() elapsed=elapsed+0.7; return elapsed end} end
          buildCalls=0; fakeTriangles=0
          stubGenerator={build=function(cells,i,j,options)
            buildCalls=buildCalls+1
            for n=1,5 do options.pause() end
            local t=options.tileSize
            local road={items=fakeTriangles>0 and {{triangles=fakeTriangles,meshes={}}} or {},mats={'earthng_ground'},drivable=true,
              spawn={pos={(i+0.5)*t,(j+0.5)*t,5},dir={0,1,0}}}
            return road,{items={},mats={}},options.far and {items={},mats={}} or nil,{segments={{'segment'}}}
          end}
        ''')
        if real_generator:
            lua.globals().stubGenerator = lua.execute((ROOT / 'mod/lua/ge/extensions/e2r/generator.lua').read_text(encoding='utf8'))
        lua.globals().countries = lua.execute((ROOT / 'mod/lua/ge/extensions/e2r/countries.lua').read_text(encoding='utf8'))
        coordinate_path = ROOT / 'mod/lua/ge/extensions/e2r/coordinates.lua'
        lua.globals().coordinates = lua.execute(coordinate_path.read_text(encoding='utf8')) if coordinate_path.exists() else lua.table()
        lua.execute("require=function(name) if name=='ge/extensions/e2r/countries' then return countries elseif name=='ge/extensions/e2r/coordinates' then return coordinates else return stubGenerator end end")
        mod = lua.execute((ROOT / 'mod/lua/ge/extensions/e2r/native.lua').read_text(encoding='utf8'))
        return lua, mod, convert

    def pump(self, mod, count=400):
        for _ in range(count):
            mod.update(.016, 2)
            job = mod.status().job
            if job and job.state != 'working':
                return job
        self.fail('native landing did not finish')

    def test_landing_uses_budgeted_generation_and_direct_manifests(self):
        lua, mod, cv = self.make()
        self.assertEqual(mod.refresh().countryCount, 1)
        lat, lon = geographic(1750, 1750)
        job = mod.land(cv(dict(seq=7, lat=lat, lon=lon)))
        self.assertEqual(job.state, 'working')
        self.assertEqual(lua.globals().buildCalls, 0)
        mod.update(.016, .5)
        self.assertEqual(job.state, 'working')
        job = self.pump(mod)
        self.assertEqual(job.state, 'ready')
        self.assertEqual(job.seq, 7)
        self.assertEqual(job.country, 'demo')
        self.assertEqual(job.manifest.spawn.yaw, 0)
        self.assertEqual(job.horizonZ, -35)
        self.assertGreaterEqual(mod.status().stream.readyCount, 9)
        key = f'{job.tile[1]}_{job.tile[2]}'
        self.assertEqual(mod.manifest(key, 'road').materials, '/earthng/planet/materials.json')
        self.assertEqual(mod.manifest(key, 'road').navSegments[1][1], 'segment')
        self.assertIsNotNone(mod.manifest(key, 'scene'))
        self.assertIsNone(mod.manifest(key, 'far'))

    def test_affine_inverse_and_safe_source_coverage(self):
        _, mod, _ = self.make()
        mod.refresh()
        lat, lon = geographic(1250, 1750)
        country, region, x, y, _ = mod.at(lat, lon)
        self.assertEqual((country.id, region.id), ('demo', 'sample'))
        self.assertAlmostEqual(x, 1250, places=5)
        self.assertAlmostEqual(y, 1750, places=5)
        # Both points are inside the broad geographic box, but lack source cells
        # or the complete 1 km startup neighbourhood.
        self.assertIsNone(mod.at(*geographic(-100, 2000)))
        self.assertIsNone(mod.at(*geographic(250, 2000)))
        self.assertIsNone(mod.at(float('nan'), lon))

    def test_exact_projection_controls_selection_and_car_affine(self):
        files = fixture()
        files['/earthng/countries/demo/regions/sample.json']['frame'] = {'testProjection': True}
        lua, mod, cv = self.make(files)
        # Deliberately differ from fixture.geo: exact frame coordinates take
        # precedence, while legacy sources without frame retain affine support.
        lua.execute('''coordinates.frame=function(spec)
          if not spec or not spec.testProjection then return nil end
          return {
            localPoint=function(lat,lon) return (lon-20)*100000,(lat-40)*100000 end,
            geographic=function(x,y) return 20+x/100000,40+y/100000 end,
            affineAt=function(x,y) return {x,y,20+x/100000,40+y/100000,.00001,0,0,.00001} end}
        end''')
        mod.refresh()
        _, _, x, y, _ = mod.at(40.0175, 20.0175)
        self.assertAlmostEqual(x, 1750)
        self.assertAlmostEqual(y, 1750)
        mod.land(cv(dict(seq=1, lat=40.0175, lon=20.0175)))
        self.assertAlmostEqual(mod.status().stream.geo[1], 1750)
        self.assertAlmostEqual(mod.status().stream.geo[2], 1750)
        mod.pose(cv(dict(x=2200, y=2100, vx=0, vy=0)))
        geo = mod.status().stream.geo
        self.assertEqual((geo[1], geo[2]), (2200, 2100))
        self.assertAlmostEqual(geo[3], 20.022)
        self.assertAlmostEqual(geo[4], 40.021)

    def test_missing_or_escaping_cell_disables_country(self):
        for invalid in ('missing', 'traversal'):
            files = fixture()
            path = files['/earthng/countries/demo/regions/sample.json']['cells']['1_1']['path']
            if invalid == 'missing':
                del files[path]
            else:
                files['/earthng/countries/demo/regions/sample.json']['cells']['1_1']['path'] = '/earthng/countries/demo/../other.json'
            with self.subTest(invalid=invalid):
                _, mod, _ = self.make(files)
                st = mod.refresh()
                self.assertEqual(st.countryCount, 0)
                self.assertFalse(st.countries[1].available)
                self.assertGreater(len(st.errors), 0)
                self.assertIsNone(mod.at(*geographic(1750, 1750)))

    def test_invalid_elevation_becomes_visible_job_error(self):
        files = fixture()
        for path, value in files.items():
            if '/cells/' in path:
                value['terrain']['heights'] = [0]
        _, mod, cv = self.make(files)
        mod.land(cv(dict(seq=2, lat=geographic(1750, 1750)[0], lon=geographic(1750, 1750)[1])))
        job = self.pump(mod)
        self.assertEqual(job.state, 'error')
        self.assertIn('elevation', job.message)

    def test_pose_preserves_geo_and_refresh_discards_old_generation(self):
        _, mod, cv = self.make()
        lat, lon = geographic(1750, 1750)
        mod.land(cv(dict(seq=1, lat=lat, lon=lon)))
        self.pump(mod)
        mod.pose(cv(dict(x=2300, y=2100, vx=25, vy=0, region='sample')))
        st = mod.status()
        want_lat, want_lon = geographic(2300, 2100)
        self.assertAlmostEqual(st.stream.car[1], want_lon)
        self.assertAlmostEqual(st.stream.car[2], want_lat)
        self.assertGreater(st.heartbeat, 0)
        mod.refresh()
        self.assertIsNone(mod.status().stream)
        self.assertIsNone(mod.status().job)
        self.assertIsNone(mod.manifest('3_3', 'road'))

    def test_no_coverage_cannot_start_generator(self):
        lua, mod, cv = self.make()
        job = mod.land(cv(dict(seq=1, lat=0, lon=0)))
        self.assertEqual(job.state, 'error')
        mod.update(.016, 2)
        self.assertEqual(lua.globals().buildCalls, 0)

    def test_source_cells_generate_playable_meshes_without_companion(self):
        files = fixture()
        for path, cell in files.items():
            if '/cells/' in path:
                cell['roads'] = [dict(id='main', nodes=[1, 2], width=8,
                                      points=[[0, 1750, 0], [4000, 1750, 20]],
                                      tags={'highway': 'residential', 'sidewalk': 'no'})]
        _, mod, cv = self.make(files, real_generator=True)
        lat, lon = geographic(1750, 1750)
        mod.land(cv(dict(seq=3, lat=lat, lon=lon)))
        job = self.pump(mod, count=4000)
        self.assertEqual(job.state, 'ready')
        self.assertGreater(job.manifest.triangles, 0)
        self.assertIsNotNone(job.manifest['items'][1].meshes)
        self.assertGreater(len(job.manifest.navSegments), 0)
        self.assertAlmostEqual(job.manifest.spawn.pos[3], 8.85)

    def test_ground_fallback_is_not_a_drivable_landing(self):
        _, mod, cv = self.make(real_generator=True)
        lat, lon = geographic(1750, 1750)
        mod.land(cv(dict(seq=4, lat=lat, lon=lon)))
        job = self.pump(mod, count=4000)
        self.assertEqual(job.state, 'error')
        self.assertIn('no_drivable_spawn', job.message)

    def test_invalid_geometry_is_a_visible_job_error(self):
        files = fixture()
        for path, cell in files.items():
            if '/cells/' in path:
                cell['roads'] = [dict(id='bad', points=[[float('inf'), 1750, 0], [2000, 1750, 0]])]
        _, mod, cv = self.make(files, real_generator=True)
        lat, lon = geographic(1750, 1750)
        mod.land(cv(dict(seq=4, lat=lat, lon=lon)))
        job = self.pump(mod)
        self.assertEqual(job.state, 'error')
        self.assertIn('source geometry', job.message)

    def test_geometry_cache_is_bounded_and_does_not_rebuild_evicted_tiles(self):
        lua, mod, cv = self.make(fixture(6))
        lua.globals().fakeTriangles = 20000
        lat, lon = geographic(1750, 1750)
        mod.land(cv(dict(seq=4, lat=lat, lon=lon)))
        self.assertEqual(self.pump(mod).state, 'ready')
        # The renderer still needs all startup tiles during warm-up/descent.
        # Its first driving pose is the explicit release of that working set.
        self.assertGreaterEqual(mod.status().stream.readyCount, 9)
        self.assertTrue(mod.status().stream.cacheOverBudget)
        for _ in range(30):
            mod.update(.016, 2)
        self.assertGreaterEqual(mod.status().stream.readyCount, 9)
        mod.pose(cv(dict(x=1750, y=1750, vx=0, vy=0, state='driving')))
        for _ in range(1000):
            mod.update(.016, 2)
        stream = mod.status().stream
        self.assertLessEqual(stream.cachedTiles, 32)
        self.assertLessEqual(stream.cachedCells, 32)
        self.assertLessEqual(stream.cachedTriangles, 80000)
        self.assertFalse(stream.cacheOverBudget)
        self.assertGreater(len(list(stream.far.keys())), 0)
        built = stream.built
        for _ in range(100):
            mod.update(.016, 2)
        self.assertEqual(stream.built, built)
        self.assertLess(built, 150)

    def test_gpu_release_drops_arrays_and_unload_regenerates(self):
        lua, mod, cv = self.make()
        lua.globals().fakeTriangles = 1000
        lat, lon = geographic(1750, 1750)
        mod.land(cv(dict(seq=5, lat=lat, lon=lon)))
        job = self.pump(mod)
        key = f'{job.tile[1]}_{job.tile[2]}'
        road = mod.manifest(key, 'road')
        before = mod.status().stream.cachedTriangles
        self.assertTrue(mod.release(key, 'road'))
        self.assertIsNone(road['items'][1].meshes)
        self.assertIsNotNone(road.spawn)
        self.assertEqual(road.navSegments[1][1], 'segment')
        self.assertTrue(mod.status().stream.ready[key])
        self.assertEqual(mod.status().stream.cachedTriangles, before - 1000)
        scene = mod.manifest(key, 'scene')
        scene['items'][1] = cv(dict(kind='proc', decor=True, meshes=[dict(verts=[])], triangles=1))
        self.assertTrue(mod.release(key, 'scene'))
        self.assertIsNone(scene['items'][1].meshes)
        self.assertTrue(scene['items'][1].decor)
        self.assertTrue(mod.unload(key))
        self.assertIsNone(mod.status().stream.ready[key])
        self.assertIsNone(mod.status().stream.scenes[key])
        self.assertIsNone(mod.manifest(key, 'road'))
        for _ in range(100):
            mod.update(.016, 2)
            if mod.manifest(key, 'road') is not None:
                break
        self.assertIsNotNone(mod.manifest(key, 'road')['items'][1].meshes)

    def test_renderer_can_release_a_manifest_after_cache_eviction(self):
        lua, mod, cv = self.make()
        lua.globals().fakeTriangles = 20000
        lat, lon = geographic(1750, 1750)
        mod.land(cv(dict(seq=6, lat=lat, lon=lon)))
        self.pump(mod)
        held = {key: mod.manifest(key, 'road') for key in mod.status().stream.ready.keys()}
        mod.pose(cv(dict(x=1750, y=1750, state='driving')))
        evicted = next(key for key in held if mod.manifest(key, 'road') is None)
        self.assertIsNotNone(held[evicted]['items'][1].meshes)
        self.assertTrue(mod.release(evicted, 'road'))
        self.assertIsNone(held[evicted]['items'][1].meshes)


if __name__ == '__main__':
    unittest.main()
