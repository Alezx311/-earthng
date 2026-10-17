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
        # the ground lies at least groundDrop (0.25 m) under the road's profile, as in the Python
        # generator, and lower where the high DEM beside the road would lift it through the asphalt
        self.assertTrue(all(z <= 12.25 + 1e-7 for z in pulled))
        distant = [p[2] for p in ground_points if p[1] == 0]
        self.assertTrue(all(abs(z - 99.75) < 1e-7 for z in distant))
        for _, mesh in self.meshes(left, 'e2rf_road'):
            self.assertTrue(all(n.z == 1 for n in mesh.normals.values()))

    def surface_at(self, triangles, x, y):
        """The highest of the triangles over (x, y), or None."""
        best = None
        for a, b, c in triangles:
            den = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(den) < 1e-12:
                continue
            u = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / den
            v = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / den
            if u >= -1e-9 and v >= -1e-9 and u + v <= 1 + 1e-9:
                z = u * a[2] + v * b[2] + (1 - u - v) * c[2]
                best = z if best is None else max(best, z)
        return best

    def test_sidewalks_stay_off_the_other_street_at_a_crossing(self):
        # two streets crossing at (250, 250); the old ribbons ran their sidewalks over each other
        roads = [{'id': 'ns', 'nodes': ['a', 'b'], 'width': 8, 'tags': {'highway': 'residential'},
                  'points': [[250, 0, 10], [250, 500, 10]]},
                 {'id': 'ew', 'nodes': ['c', 'd'], 'width': 8, 'tags': {'highway': 'residential'},
                  'points': [[0, 250, 10], [500, 250, 10]]}]
        road = self.build([{'roads': roads}])[0]
        walks = list(self.triangles(road, 'e2rf_sidewalk')) + list(self.triangles(road, 'e2rf_curb'))
        self.assertTrue(walks)
        for tri in walks:
            self.assertTrue(all(abs(p[2] - 10.1) < 1e-6 for p in tri))  # flush with the road
        # no sidewalk anywhere on either carriageway near the crossing
        for x in range(200, 301, 2):
            for y in (246.5, 250, 253.5):
                self.assertIsNone(self.surface_at(walks, x + .25, y), (x, y))
                self.assertIsNone(self.surface_at(walks, y, x + .25), (y, x))

    def test_ground_never_reaches_the_asphalt_in_a_dip(self):
        # the road's profile dips 1.5 m between two 25 m lattice nodes (an underpass approach): the
        # ground, pulled to the profile at the nodes only, used to come up through the asphalt
        cell = {'roads': [{'id': 'r', 'nodes': ['a', 'b'], 'width': 7, 'tags': {'highway': 'tertiary', 'sidewalk': 'no'},
                           'points': [[0, 237, 10], [250, 250, 10], [262.5, 250.65, 8.5], [275, 251.3, 10], [500, 263, 10]]}],
                'terrain': {'rect': [0, 0, 500, 500], 'nx': 2, 'ny': 2, 'heights': [10] * 4}}
        road = self.build([cell])[0]
        ground = list(self.triangles(road, 'e2rf_ground'))
        asphalt = list(self.triangles(road, 'e2rf_road'))
        for k in range(200, 320):
            x = float(k)
            y = 237 + 26 * x / 500
            for off in (-3.4, 0, 3.4):
                top = self.surface_at(asphalt, x, y + off)
                low = self.surface_at(ground, x, y + off)
                self.assertIsNotNone(top)
                if low is not None:
                    self.assertLess(low, top - .05, (x, off))

    def test_junction_is_derived_from_road_ends_without_source_junctions(self):
        # a T of three graph segments meeting at node 'n' (500, 250), no junction feature: the
        # roads stop 0.8 x the widest (10 m) = 8 m short of the node, a fan from the node fills
        # the mouths, and both tiles round the node draw the same fan
        roads = [{'id': 'w', 'nodes': ['a', 'n'], 'width': 10, 'tags': {'highway': 'residential'},
                  'points': [[300, 250, 10], [500, 250, 10]]},
                 {'id': 'e', 'nodes': ['n', 'b'], 'width': 10, 'tags': {'highway': 'residential'},
                  'points': [[500, 250, 10], [700, 250, 10]]},
                 {'id': 's', 'nodes': ['n', 'c'], 'width': 6, 'tags': {'highway': 'residential'},
                  'points': [[500, 250, 10], [500, 50, 10]]}]
        left, right = self.build([{'roads': roads}], i=0)[0], self.build([{'roads': roads}], i=1)[0]
        for manifest, x0 in ((left, 400), (right, 500)):
            asphalt = list(self.triangles(manifest, 'e2rf_road'))
            # every point of the three carriageways near the node is asphalt at road height
            for x in [x0 + k + .5 for k in range(0, 100, 2)]:
                for y in (245.5, 250, 254.5):
                    if abs(x - 500) < 60:
                        z = self.surface_at(asphalt, x, y)
                        self.assertIsNotNone(z, (x, y))
                        self.assertAlmostEqual(z, 10.1, places=6)
            for y in [200 + k + .5 for k in range(0, 45, 2)]:
                for x in (497.5, 502.5):
                    if x0 <= x <= x0 + 100:
                        self.assertIsNotNone(self.surface_at(asphalt, x, y), (x, y))
        # the strips stop at the cut: no strip vertex between the node and 8 m out on the west road
        strip_x = [p[0] for tri in self.triangles(left, 'e2rf_road') for p in tri if abs(p[1] - 245) < 1e-6]
        self.assertFalse([x for x in strip_x if 492 + 1e-6 < x < 500 - 1e-6])
        # the fan is the same on both sides of the tile border x = 500
        border = [sorted((round(p[1], 4), round(p[2], 4)) for tri in self.triangles(m, 'e2rf_road')
                         for p in tri if abs(p[0] - 500) < 1e-7) for m in (left, right)]
        self.assertEqual(set(border[0]), set(border[1]))

    def test_fences_follow_the_ground_and_have_thickness(self):
        # one 400 m wall over a 6 m hump: its ends alone on the ground buried the middle
        heights = [0, 0, 0, 6, 0, 0, 0, 0, 0]
        cell = {'lines': [{'id': 'w', 'kind': 'fence', 'tags': {'barrier': 'wall'},
                           'points': [[50, 250, 0], [450, 250, 0]]}],
                'terrain': {'rect': [0, 0, 500, 500], 'nx': 3, 'ny': 3, 'heights': heights}}
        road, scene = self.build([cell])[:2]
        ground = list(self.triangles(road, 'e2rf_ground'))
        wall = list(self.triangles(scene, 'e2rf_fence_wall'))
        ys = {round(p[1], 4) for tri in wall for p in tri}
        self.assertEqual(ys, {249.9, 250.1})  # 0.2 m thick
        for x in range(60, 441, 10):
            g = self.surface_at(ground, x, 250)
            top = max(p[2] for tri in wall for p in tri if abs(p[0] - x) < 2.01)
            self.assertGreater(top - g, 1.9, x)    # a 2 m wall stands over the hump
            self.assertLess(top - g, 2.2, x)

    def test_bus_stop_on_the_carriageway_stands_beside_the_road(self):
        road = {'id': 'r', 'nodes': ['a', 'b'], 'width': 8, 'tags': {'highway': 'residential'},
                'points': [[0, 250, 0], [500, 250, 0]]}
        stop = {'id': 's', 'kind': 'stop', 'tags': {'highway': 'bus_stop'}, 'pos': [200, 250.5, 0]}
        scene = self.build([{'roads': [road], 'points': [stop]}])[1]
        shelters = [it for it in scene['items'].values() if it['kind'] == 'static' and 'busstop' in it['shape']]
        self.assertEqual(len(shelters), 1)
        pos = shelters[0]['pos']
        # right of the eastward road: beyond its 4 m half width, the 2 m sidewalk and 1.4 m more
        self.assertAlmostEqual(pos[2], 250 - 7.4)  # a Lua table: x, y, z at 1, 2, 3
        self.assertAlmostEqual(pos[1], 200.0)

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

    def test_navigation_has_no_zero_length_edges(self):
        # repeated source points made zero-length navgraph edges; the game's random route walk
        # (traffic respawns) never got its length on them and froze the game
        road = {'id': 'r', 'nodes': ['a', 'b'], 'width': 7, 'tags': {'highway': 'residential'},
                'points': [[0, 250, 5], [100, 250, 5], [100, 250, 5], [100.1, 250, 5], [200, 250, 5]]}
        flat = {'id': 'dot', 'nodes': ['c', 'd'], 'width': 7, 'tags': {'highway': 'residential'},
                'points': [[300, 300, 5], [300, 300, 5]]}
        nav = self.build([{'roads': [road, flat]}])[3]
        segments = [nav.segments[k] for k in range(1, len(nav.segments) + 1)]
        self.assertEqual([s[1] for s in segments], ['r'])
        s = segments[0]
        points = [(s[k], s[k + 1]) for k in range(8, len(s) + 1, 3)]
        self.assertTrue(all(math.dist(p, q) >= .5 for p, q in zip(points, points[1:])))
        self.assertEqual(points[-1], (200, 250))

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
        statics = [item for item in first['items'].values() if item.kind == 'static']
        trees = [item for item in statics if 'grass' not in item.shape and 'rock' not in item.shape and '_bush_' not in item.shape]
        self.assertEqual(len(trees), 2400)
        self.assertTrue(any('grass' in item.shape for item in statics))
        self.assertTrue(any('_bush_' in item.shape for item in statics))
        self.assertTrue(all(not (100 < item.pos[1] < 300 and 100 < item.pos[2] < 300) for item in statics))
        self.assertEqual(self.plain(first), self.plain(second))

    def test_street_lamps_come_from_road_lighting_without_source_points(self):
        road = {'id': 'street', 'width': 7, 'points': [[0, 250, 10], [500, 250, 10]],
                'tags': {'highway': 'residential', 'lit': 'yes'}}
        scene = self.build([{'roads': [road]}], terrainStep=100)[1]
        lamps = [item for item in scene['items'].values() if item.kind == 'static' and item.shape.endswith('lamp1.dae')]
        self.assertEqual(len(lamps), 16)
        self.assertTrue(all(item.shape == '/art/shapes/objects/lamp1.dae' for item in lamps))
        self.assertTrue(all(item.decor and abs(item.pos[3] - 10.2) < 1e-8 for item in lamps))

    def test_country_look_selects_worn_asphalt_brick_and_skips_lamps(self):
        road = {'id': 'street', 'width': 7, 'points': [[0, 250, 10], [500, 250, 10]],
                'tags': {'highway': 'residential', 'lit': 'yes', 'sidewalk': 'no'}}
        building = {'id': 'house', 'height': 12,
                    'rings': [[[100, 100], [140, 100], [140, 140], [100, 140]]]}
        look = {'block': 'postsoviet', 'asphalt': 'worn', 'buildings': 'brick', 'statics': 'none',
                'markings': 'none', 'verge': 'dirt', 'trees': 'regional'}
        road_manifest, scene = self.build([{'roads': [road], 'buildings': [building]}],
                                          terrainStep=100, look=look)[:2]
        materials = {mesh.material for _item, mesh in self.meshes(road_manifest)}
        self.assertIn('e2rf_road_worn', materials)
        self.assertNotIn('e2rf_mark', materials)
        self.assertIn('e2rf_bare_ground', materials)
        walls = [mesh.material for _item, mesh in self.meshes(scene)
                 if str(mesh.material).startswith('e2rf_wall_')]
        self.assertTrue(walls)
        self.assertTrue(all(material.startswith('e2rf_wall_brk_') for material in walls))
        lamps = [item for item in scene['items'].values()
                 if item.kind == 'static' and str(item.shape).endswith('lamp1.dae')]
        self.assertEqual(len(lamps), 0)

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

    def test_sea_below_zero_gets_a_water_plane_only_where_ground_dips(self):
        def cell(heights):
            return {'terrain': {'rect': [0, 0, 1000, 1000], 'nx': 2, 'ny': 2, 'heights': heights}}
        for far in (False, True):
            with self.subTest(far=far):
                _, scene, farScene, _ = self.build([cell([20, 20, 20, 20])], far=far, seaLevel=20)
                scene = farScene if far else scene
                water = list(self.triangles(scene, 'e2rf_water'))
                self.assertEqual(len(water), 2)  # open sea: one quad for the whole tile
                self.assertTrue(all(p[2] == 20.3 for t in water for p in t))
                self.assertAlmostEqual(sum(map(area, water)), 500 * 500)
                _, scene, farScene, _ = self.build([cell([30, 30, 30, 30])], far=far, seaLevel=20)
                self.assertEqual(list(self.triangles(farScene if far else scene, 'e2rf_water')), [])
        # a coast (west under the sea, east on land): water over the western quads only
        _, scene, *_ = self.build([cell([20, 30, 20, 30])], seaLevel=20)
        water = list(self.triangles(scene, 'e2rf_water'))
        self.assertGreater(len(water), 2)
        self.assertLess(max(p[0] for t in water for p in t), 500)
        _, scene, *_ = self.build([cell([20, 20, 20, 20])])  # no seaLevel (v1 data): no sea
        self.assertEqual(list(self.triangles(scene, 'e2rf_water')), [])

    def corners(self, manifest, material=None):
        found = []
        for item, mesh in self.meshes(manifest, material):
            for k in range(1, len(mesh.faces) + 1, 3):
                for index in range(k, k + 3):
                    face = mesh.faces[index]
                    p = mesh.verts[face.v + 1]
                    n = mesh.normals[face.n + 1]
                    found.append((p.x + item.pos[1], p.y + item.pos[2], p.z + item.pos[3], n.x, n.y, n.z))
        return found

    def border(self, manifest, x, material='e2rf_ground', surface=False):
        pts = {}
        for px, py, pz, nx, ny, nz in self.corners(manifest, material):
            # A skirt shares the data-edge XY and would hide the surface sample.
            if abs(px - x) < 1e-5 and not (surface and abs(nz) < 0.05):
                pts[round(py, 3)] = (round(pz, 4), round(nx, 4), round(ny, 4), round(nz, 4))
        return pts

    def test_far_ground_keeps_a_hill_and_a_valley(self):
        def cell(heights):
            return {'terrain': {'rect': [0, 0, 1000, 1000], 'nx': 5, 'ny': 5, 'heights': heights}}
        hill = [20.0] * 25
        hill[6] = 400  # grid point (250 m, 250 m)
        road, scene, far, nav = self.build([cell(hill)], far=True)
        self.assertEqual((len(road['items']), len(scene['items']), len(nav.segments)), (0, 0, 0))
        self.assertEqual(far.layer, 'far')
        ground = list(self.triangles(far, 'e2rf_ground'))
        self.assertAlmostEqual(sum(map(area, ground)), 500 * 500, delta=1)
        # (0, 0) is a border vertex. (200, 200) is a coarse vertex on the slope up to the peak.
        self.assertAlmostEqual(self.surface_at(ground, 0, 0), 19.75, places=2)
        self.assertAlmostEqual(self.surface_at(ground, 200, 200), 262.95, places=2)
        self.assertGreater(self.surface_at(ground, 250, 250), 200)
        valley = [80.0] * 25
        valley[6] = 5
        low = list(self.triangles(self.build([cell(valley)], far=True)[2], 'e2rf_ground'))
        self.assertLess(self.surface_at(low, 200, 200), self.surface_at(low, 0, 0))

    def test_far_ridge_normals_tilt_away_from_the_crest(self):
        heights = [300.0 if ix == 1 else 20.0 for iy in range(5) for ix in range(5)]
        cell = {'terrain': {'rect': [0, 0, 1000, 1000], 'nx': 5, 'ny': 5, 'heights': heights}}
        far = self.build([cell], far=True)[2]
        left = [n for x, y, z, *n in self.corners(far, 'e2rf_ground') if abs(x - 200) < 1e-4 and abs(y - 200) < 1e-4]
        right = [n for x, y, z, *n in self.corners(far, 'e2rf_ground') if abs(x - 300) < 1e-4 and abs(y - 200) < 1e-4]
        self.assertTrue(left and right)
        self.assertLess(left[0][0], 0)
        self.assertGreater(right[0][0], 0)

    def test_near_and_far_share_ground_on_a_sloping_road(self):
        heights = [20.0 + ix * 20 for iy in range(5) for ix in range(5)]
        cell = {'roads': [{'id': 'road', 'nodes': [1, 2], 'width': 10,
                           'tags': {'highway': 'residential', 'sidewalk': 'no'},
                           'points': [[0, 250, 10], [1000, 250, 30]]}],
                'terrain': {'rect': [0, 0, 1000, 1000], 'nx': 5, 'ny': 5, 'heights': heights}}
        near = self.border(self.build([cell], i=0)[0], 500, surface=True)
        far = self.border(self.build([cell], i=1, far=True)[2], 500, surface=True)
        self.assertTrue(near and far)
        self.assertEqual(near, far)
        # x = 500 is DEM column 60 m. Away from the road the surface is that minus groundDrop.
        self.assertAlmostEqual(near[0][0], 59.75, places=2)
        self.assertNotAlmostEqual(near[250][0], near[0][0], places=2)

    def test_negative_tiles_share_the_ground_edge(self):
        cell = {'terrain': {'rect': [-1000, -1000, 0, 0], 'nx': 2, 'ny': 2, 'heights': [10, 40, 10, 40]}}
        left = self.border(self.build([cell], i=-2, j=-1, far=True)[2], -500)
        right = self.border(self.build([cell], i=-1, j=-1, far=True)[2], -500)
        self.assertTrue(left)
        self.assertEqual(left, right)

    def test_shared_dem_edge_does_not_depend_on_cell_order(self):
        low = {'terrain': {'rect': [0, 0, 500, 500], 'nx': 2, 'ny': 2, 'heights': [10, 10, 10, 10]}}
        high = {'terrain': {'rect': [500, 0, 1000, 500], 'nx': 2, 'ny': 2, 'heights': [80, 80, 80, 80]}}
        for cells in ([low, high], [high, low]):
            ground = list(self.triangles(self.build(cells, far=True)[2], 'e2rf_ground'))
            self.assertAlmostEqual(self.surface_at(ground, 500, 250), 9.75, places=2)

    def test_missing_dem_is_omitted_instead_of_a_zero_cliff(self):
        cell = {'terrain': {'rect': [0, 0, 400, 500], 'nx': 2, 'ny': 2, 'heights': [50, 50, 50, 50]}}
        far = self.build([cell], far=True)[2]
        verts = self.corners(far, 'e2rf_ground')
        self.assertTrue(all(v[0] <= 400 + 1e-4 for v in verts))
        self.assertTrue(all(abs(v[2]) > 1 for v in verts))
        dropped = [v for v in verts if v[2] < 40]
        self.assertTrue(dropped)
        for x, y, z, *_ in dropped:
            self.assertTrue(abs(x) < 1e-3 or abs(x - 400) < 1e-3 or abs(y) < 1e-3 or abs(y - 500) < 1e-3)

    def test_horizon_mesh_is_ground_without_buildings_or_trees(self):
        source = [{'buildings': [{'id': 'a', 'height': 30,
                                  'rings': [[[100, 100], [140, 100], [140, 140], [100, 140]]]}],
                   'areas': [{'id': 'wood', 'kind': 'forest',
                              'rings': [[[0, 0], [500, 0], [500, 500], [0, 500]]]}],
                   'terrain': {'rect': [0, 0, 1000, 1000], 'nx': 2, 'ny': 2, 'heights': [40, 40, 40, 40]}}]
        road, scene, horizon, nav = self.build(source, horizon=True)
        self.assertEqual(horizon.layer, 'horizon')
        self.assertEqual((len(road['items']), len(scene['items']), len(nav.segments)), (0, 0, 0))
        self.assertTrue(list(self.triangles(horizon, 'e2rf_ground')))
        self.assertFalse(any(item.kind == 'static' for item in horizon['items'].values()))
        self.assertTrue(all(item.layer == 'horizon' and item.collision == 'None'
                            for item in horizon['items'].values()))

    def test_far_woodland_has_sparse_decorative_canopy(self):
        source = [{'areas': [{'id': 'wood', 'kind': 'forest',
                              'rings': [[[0, 0], [500, 0], [500, 500], [0, 500]]]}]}]
        far = self.build(source, far=True)[2]
        trees = [item for item in far['items'].values() if item.kind == 'static']
        self.assertGreater(len(trees), 40)
        self.assertLessEqual(len(trees), 800)
        self.assertTrue(all(item.decor and item.layer == 'far' for item in trees))

    def test_far_tiles_keep_fences_and_horizon_does_not(self):
        cell = {'lines': [{'id': 'w', 'kind': 'fence', 'tags': {'barrier': 'wall'},
                           'points': [[50, 250, 0], [450, 250, 0]]}]}
        far = self.build([cell], far=True)[2]
        self.assertTrue(list(self.triangles(far, 'e2rf_fence_wall')))
        self.assertFalse(list(self.triangles(far, 'e2rf_footway')))
        horizon = self.build([cell], horizon=True)[2]
        self.assertFalse(list(self.triangles(horizon, 'e2rf_fence_wall')))


if __name__ == '__main__':
    unittest.main()
