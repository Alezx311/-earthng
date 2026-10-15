"""Runtime Lua geometry invariants; needs lupa (or the existing out/test-tools install)."""
import copy
import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'out/test-tools'))
from lupa.luajit21 import LuaRuntime

SOURCE = Path(__file__).resolve().parents[1] / 'mod/lua/ge/extensions/e2r/generator.lua'


def area(triangle):
    a, b, c = triangle
    return abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.generator = self.lua.execute(SOURCE.read_text(encoding='utf8'))

    def table(self, value):
        if isinstance(value, dict):
            return self.lua.table_from({k: self.table(v) for k, v in value.items()})
        if isinstance(value, list):
            return self.lua.table_from([self.table(v) for v in value])
        return value

    def plain(self, value):
        if hasattr(value, 'keys'):
            keys = list(value.keys())
            if all(isinstance(k, int) for k in keys):
                return [self.plain(value[k]) for k in range(1, len(keys) + 1)]
            return {k: self.plain(v) for k, v in value.items()}
        return value

    def build(self, cells, i=0, j=0, **options):
        return self.generator.build(self.table(cells), i, j, self.table(options))

    def meshes(self, manifest, material=None):
        for item in manifest['items'].values():
            for mesh in (item['meshes'] or self.lua.table()).values():
                if material is None or mesh.material == material:
                    yield item, mesh

    def triangles(self, manifest, material=None):
        for item, mesh in self.meshes(manifest, material):
            for k in range(1, len(mesh.faces) + 1, 3):
                triangle = []
                for index in range(k, k + 3):
                    p = mesh.verts[mesh.faces[index].v + 1]
                    triangle.append([p.x + item.pos[1], p.y + item.pos[2], p.z + item.pos[3]])
                yield triangle

    def test_concave_polygon_and_courtyard_hole_keep_exact_area(self):
        shapes = [
            ([[[0, 0], [4, 0], [4, 1], [1, 1], [1, 4], [0, 4]]], 7),
            ([[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
              [[3, 3], [7, 3], [7, 7], [3, 7], [3, 3]]], 84),
        ]
        for rings, expected in shapes:
            with self.subTest(area=expected):
                triangles = self.plain(self.generator.triangulate(self.table(rings)))
                self.assertAlmostEqual(sum(map(area, triangles)), expected)
                for triangle in triangles:
                    if expected == 84:
                        cx = sum(p[0] for p in triangle) / 3
                        cy = sum(p[1] for p in triangle) / 3
                        self.assertFalse(3 < cx < 7 and 3 < cy < 7)

    def test_clip_interpolates_height_and_uv_at_tile_border(self):
        clipped = self.plain(self.generator.clip(self.table([
            [-10, 0, 0, 0, 0], [10, 0, 20, 1, 0], [10, 10, 20, 1, 1], [-10, 10, 0, 0, 1],
        ]), self.table([0, 0, 5, 10])))
        self.assertEqual(len(clipped), 4)
        for x, y, z, u, v in clipped:
            self.assertGreaterEqual(x, 0)
            self.assertLessEqual(x, 5)
            self.assertAlmostEqual(z, x + 10)
            self.assertAlmostEqual(u, (x + 10) / 20)
            self.assertAlmostEqual(v, y / 10)

    def test_profiled_road_and_terrain_are_continuous_across_tiles(self):
        source = [{'roads': [{'id': 'road', 'nodes': [10, 20], 'width': 10,
                              'tags': {'highway': 'residential', 'sidewalk': 'no'},
                              'points': [[0, 250, 10], [1000, 250, 20]]}],
                   'terrain': {'rect': [0, 0, 1000, 500], 'nx': 2, 'ny': 2, 'heights': [100] * 4}}]
        left = self.build(source)[0]
        right = self.build(source, i=1)[0]
        borders = []
        for manifest in [left, right]:
            road_border = set()
            for triangle in self.triangles(manifest, 'e2rf_road'):
                for x, y, z in triangle:
                    if abs(x - 500) < 1e-7:
                        road_border.add((round(y, 6), round(z, 6)))
            borders.append(road_border)
        self.assertEqual(borders[0], borders[1])
        self.assertEqual(borders[0], {(245, 15.1), (255, 15.1)})
        ground_points = [p for triangle in self.triangles(left, 'e2rf_ground') for p in triangle]
        pulled = [p[2] for p in ground_points if p[0] == 250 and p[1] == 250]
        self.assertTrue(pulled)
        self.assertTrue(all(abs(z - 12.5) < 1e-7 for z in pulled))
        distant = [p[2] for p in ground_points if p[1] == 0]
        self.assertTrue(all(abs(z - 100) < 1e-7 for z in distant))
        for _, mesh in self.meshes(left, 'e2rf_road'):
            self.assertTrue(all(n.z == 1 for n in mesh.normals.values()))

    def test_building_hole_roof_and_collision_walls(self):
        rings = [[[100, 100, 0], [140, 100, 0], [140, 140, 0], [100, 140, 0]],
                 [[110, 110, 0], [130, 110, 0], [130, 130, 0], [110, 130, 0]]]
        road, scene, far, nav = self.build([{'buildings': [
            {'id': 'courtyard', 'rings': rings, 'height': 12, 'tags': {'building:material': 'brick'}}]}])
        roof = list(self.triangles(scene, 'e2rf_roof'))
        self.assertAlmostEqual(sum(map(area, roof)), 1200)
        self.assertTrue(all(p[2] == 12 for tri in roof for p in tri))
        wall_triangles = sum(len(mesh.faces) // 3 for _, mesh in self.meshes(scene)
                             if mesh.material.startswith('e2rf_wall_brk_'))
        self.assertEqual(wall_triangles, 16)
        self.assertIsNone(far)
        self.assertEqual(len(nav.segments), 0)
        self.assertFalse(road.drivable)

    def test_feature_duplicates_do_not_double_geometry_or_nav(self):
        cell = {'roads': [{'id': 'same', 'points': [[100, 100, 10], [200, 100, 10]],
                           'nodes': [1, 2], 'width': 7, 'tags': {'highway': 'residential'}}]}
        once = self.build([cell])
        twice = self.build([cell, copy.deepcopy(cell)])
        self.assertEqual(once[0].triangles, twice[0].triangles)
        self.assertEqual(len(twice[3].segments), 1)

    def test_navigation_reverses_negative_oneway_and_converts_mph(self):
        road = {'id': 'way', 'nodes': ['first', 'last'], 'width': 7,
                'points': [[0, 250, 5], [500, 250, 15]],
                'tags': {'highway': 'primary', 'oneway': '-1', 'lanes': '2', 'maxspeed': '35 mph'}}
        manifest, _, _, nav = self.build([{'roads': [road]}])
        s = nav.segments[1]
        self.assertEqual((s[2], s[3], s[4]), ('last', 'first', '++'))
        self.assertAlmostEqual(s[5], 35 * 1.609 / 3.6)
        self.assertEqual([s[k] for k in [8, 9, 10]], [500, 250, 15.1])
        coordinates = [[s[k], s[k + 1], s[k + 2]] for k in range(8, len(s) + 1, 3)]
        self.assertTrue(all(math.dist(a[:2], b[:2]) <= 80 for a, b in zip(coordinates, coordinates[1:])))
        self.assertTrue(manifest.drivable)

    def test_mesh_pieces_are_bounded_and_generation_yields(self):
        calls = []
        road, scene, _, _ = self.generator.build(self.table([]), 0, 0,
                                                self.table({'terrainStep': 10, 'pause': lambda: calls.append(1)}))
        self.assertGreater(len(calls), 50)
        self.assertGreater(len(road['items']), 1)
        for item, mesh in self.meshes(road):
            self.assertLessEqual(item.triangles, 1500)
            self.assertTrue(all(0 <= face.v < len(mesh.verts) for face in mesh.faces.values()))
        for triangle in self.triangles(road):
            self.assertTrue(all(0 <= p[0] <= 500 and 0 <= p[1] <= 500 for p in triangle))

    def test_generation_does_not_mutate_source_features(self):
        source = self.table([{'junctions': [{'id': 1, 'height': 20, 'rings': [[[0, 0], [50, 0], [50, 50], [0, 50]]]}]}])
        before = json.dumps(self.plain(source), sort_keys=True)
        first = self.generator.build(source, 0, 0, self.table({'terrainStep': 100}))[0]
        second = self.generator.build(source, 0, 0, self.table({'terrainStep': 100}))[0]
        self.assertEqual(json.dumps(self.plain(source), sort_keys=True), before)
        self.assertEqual(self.plain(first), self.plain(second))

    def test_forest_trees_are_deterministic_bounded_and_avoid_buildings(self):
        source = [{'areas': [{'id': 'forest', 'kind': 'forest',
                              'rings': [[[0, 0], [500, 0], [500, 500], [0, 500]]]}],
                   'buildings': [{'id': 'block', 'height': 30,
                                  'rings': [[[100, 100], [300, 100], [300, 300], [100, 300]]]}]}]
        first = self.build(source, terrainStep=100)[1]
        second = self.build(source, terrainStep=100)[1]
        trees = [item for item in first['items'].values() if item.kind == 'static']
        self.assertEqual(len(trees), 900)
        self.assertTrue(all(not (100 < item.pos[1] < 300 and 100 < item.pos[2] < 300) for item in trees))
        self.assertEqual(self.plain(first), self.plain(second))

    def test_street_lamps_come_from_road_lighting_without_source_points(self):
        road = {'id': 'street', 'width': 7, 'points': [[0, 250, 10], [500, 250, 10]],
                'tags': {'highway': 'residential', 'lit': 'yes'}}
        scene = self.build([{'roads': [road]}], terrainStep=100)[1]
        lamps = [item for item in scene['items'].values() if item.kind == 'static']
        self.assertEqual(len(lamps), 16)
        self.assertTrue(all(item.shape == '/art/shapes/objects/lamp1.dae' for item in lamps))
        self.assertTrue(all(item.decor and abs(item.pos[3] - 10.2) < 1e-8 for item in lamps))

    def test_photo_loggia_facades_use_33_metre_uv_repeat(self):
        source = [{'buildings': [{'id': 'a', 'height': 33, 'tags': {'building:material': 'concrete'},
                                  'rings': [[[100, 100], [140, 100], [140, 140], [100, 140]]]}]}]
        scene = self.build(source, terrainStep=100)[1]
        walls = [mesh for _, mesh in self.meshes(scene) if mesh.material.startswith('e2rf_wall_lgg_')]
        self.assertTrue(walls)
        self.assertTrue(all(abs(mesh.uvs[k].v - 1) < 1e-8
                            for mesh in walls for k in range(1, len(mesh.verts) + 1) if mesh.verts[k].z == 33))

    def test_far_buildings_are_coarse_decoration_without_ground_or_roads(self):
        source = [{'buildings': [{'id': 'a', 'height': 30,
                                  'rings': [[[100, 100], [140, 100], [140, 140], [100, 140]]]}]}]
        road, scene, far, nav = self.build(source, far=True)
        self.assertEqual((len(road['items']), len(scene['items']), len(nav.segments)), (0, 0, 0))
        self.assertEqual(far.layer, 'far')
        self.assertTrue(all(item.decor and item.layer == 'far' for item in far['items'].values()))
        self.assertEqual(far.triangles, 10)

    def test_building_datums_match_across_independently_loaded_dem_cells(self):
        building = {'id': 'cross-cell', 'height': 12,
                    'rings': [[[950, 100, 100], [1150, 100, 100], [1150, 140, 100], [950, 140, 100]]]}
        west_cell = {'buildings': [building], 'terrain': {'rect': [0, 0, 1000, 1000],
                                                        'nx': 2, 'ny': 2, 'heights': [100] * 4}}
        east_cell = {'buildings': [building], 'terrain': {'rect': [1000, 0, 2000, 1000],
                                                        'nx': 2, 'ny': 2, 'heights': [100] * 4}}
        west = self.build([west_cell], i=1, terrainStep=100)[1]
        east = self.build([east_cell], i=2, terrainStep=100)[1]
        for manifest in [west, east]:
            roof = list(self.triangles(manifest, 'e2rf_roof'))
            self.assertTrue(roof)
            self.assertTrue(all(abs(p[2] - 112) < 1e-8 for tri in roof for p in tri))

    def test_dashed_marks_keep_source_phase_and_match_across_tile_border(self):
        source = [{'roads': [{'id': 'marked', 'width': 6, 'points': [[0, 10, 5], [100, 10, 5]],
                              'tags': {'highway': 'residential', 'lanes': '2', 'sidewalk': 'no'}}]}]
        manifests = [self.build(source, i=index, tileSize=25, terrainStep=25)[0] for index in [0, 1]]
        borders = []
        for manifest in manifests:
            marks = list(self.triangles(manifest, 'e2rf_mark'))
            self.assertAlmostEqual(sum(map(area, marks)), .84)
            self.assertTrue(all(abs(p[2] - 5.108) < 1e-8 for triangle in marks for p in triangle))
            borders.append({(round(y, 5), round(z, 5)) for triangle in marks for x, y, z in triangle if x == 25})
            self.assertTrue(all(item.decor for item, _ in self.meshes(manifest, 'e2rf_mark')))
        self.assertEqual(borders[0], borders[1])
        self.assertEqual(borders[0], {(9.94, 5.108), (10.06, 5.108)})

    def test_parking_lots_produce_cars_with_body_glass_and_tyre_atlas_cells(self):
        source = [{'areas': [{'id': 'parking', 'kind': 'paved', 'tags': {'amenity': 'parking'},
                              'rings': [[[10, 10], [70, 10], [70, 50], [10, 50]]]}]}]
        scene = self.build(source, terrainStep=100)[1]
        cars = list(self.meshes(scene, 'e2rf_car'))
        self.assertTrue(cars)
        atlas_cells = set()
        for item, mesh in cars:
            self.assertTrue(item.decor)
            for uv in mesh.uvs.values():
                atlas_cells.add(round(uv.u * 16 - .5))
                self.assertEqual(uv.v, .5)
        self.assertIn(8, atlas_cells)  # glass
        self.assertIn(9, atlas_cells)  # tyres
        self.assertTrue(any(0 <= cell <= 7 for cell in atlas_cells))
        self.assertTrue(all(10 <= x <= 70 and 10 <= y <= 50
                            for triangle in self.triangles(scene, 'e2rf_car') for x, y, z in triangle))

    def test_gabled_and_hipped_roofs_cover_footprint_and_rise_above_eaves(self):
        for shape in ['gabled', 'hipped']:
            with self.subTest(shape=shape):
                source = [{'buildings': [{'id': 'house', 'height': 6, 'tags': {'building': 'house', 'roof:shape': shape},
                                          'rings': [[[100, 100, 10], [120, 100, 10], [120, 110, 10], [100, 110, 10]]]}]}]
                scene = self.build(source, terrainStep=100)[1]
                roof = list(self.triangles(scene, 'e2rf_roof_tile'))
                self.assertTrue(roof)
                self.assertAlmostEqual(sum(map(area, roof)), 200)
                self.assertGreater(max(p[2] for triangle in roof for p in triangle), 16)
                self.assertEqual(list(self.triangles(scene, 'e2rf_roof')), [])

    def test_courtyard_stays_open_even_when_pitched_roof_is_requested(self):
        source = [{'buildings': [{'id': 'courtyard', 'height': 6, 'tags': {'roof:shape': 'gabled'},
                                  'rings': [[[100, 100], [140, 100], [140, 140], [100, 140]],
                                            [[110, 110], [130, 110], [130, 130], [110, 130]]]}]}]
        scene = self.build(source, terrainStep=100)[1]
        self.assertEqual(list(self.triangles(scene, 'e2rf_roof_tile')), [])
        self.assertAlmostEqual(sum(map(area, self.triangles(scene, 'e2rf_roof'))), 1200)

    def test_far_woodland_has_sparse_decorative_canopy(self):
        source = [{'areas': [{'id': 'wood', 'kind': 'forest',
                              'rings': [[[0, 0], [500, 0], [500, 500], [0, 500]]]}]}]
        far = self.build(source, far=True)[2]
        trees = [item for item in far['items'].values() if item.kind == 'static']
        self.assertGreater(len(trees), 0)
        self.assertLessEqual(len(trees), 40)
        self.assertTrue(all(item.decor and item.layer == 'far' for item in trees))


if __name__ == '__main__':
    unittest.main()
