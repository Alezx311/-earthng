"""P1 contracts across source classification, offline export and the real Lua generator."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import shapely
from shapely.geometry import Polygon

import nature
import fast_style
import country_format
import build_country
from test_country import Store
import test_generator as harness


def patch(kind, rect=(0, 0, 500, 500), tags=None, name=None):
    x0, y0, x1, y1 = rect
    return {'id': name or kind, 'kind': kind, 'tags': tags or {},
            'rings': [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]}


def terrain(z=100):
    return {'rect': [-100, -100, 1100, 1100], 'nx': 2, 'ny': 2, 'heights': [z]*4}


class Sources(unittest.TestCase):
    def test_all_natural_tags_have_material_and_physical_ground(self):
        for kind in nature.NATURAL:
            tags = {'landuse': kind} if kind in ('meadow', 'farmland', 'orchard', 'vineyard') else {'natural': kind}
            self.assertEqual(fast_style.ground_kind(tags), kind)
            material = fast_style.MATERIALS['e2rf_' + kind]
            self.assertTrue(material['groundType'])
            self.assertTrue(material['Stages'][0])
        self.assertEqual(fast_style.ground_kind({'natural': 'grassland'}), 'meadow')
        self.assertEqual(fast_style.ground_kind({'natural': 'wood'}), 'forest')

    def test_country_road_grades_have_distinct_physical_surfaces(self):
        import fast_pixel
        expect = {'road_worn': 'ASPHALT', 'road_gravel': 'GRAVEL', 'road_dirt': 'DIRT',
                  'road_broken': 'DIRT', 'road_track': 'DIRT'}
        for kind, ground in expect.items():
            material = fast_style.MATERIALS['e2rf_' + kind]
            self.assertEqual(material['groundType'], ground)
            self.assertGreaterEqual(material['Stages'][0]['roughnessFactor'], .9)
            self.assertIn(kind, fast_pixel.SURFACES)

    def test_overlap_partition_keeps_holes_and_explicit_priority(self):
        forest = patch('forest')
        lake = patch('water', (100, 100, 200, 200))
        field = patch('farmland', (250, 0, 500, 500))
        output = nature.partition([forest, field, lake])
        geoms = [Polygon(f['rings'][0], f['rings'][1:]) for f in output]
        self.assertAlmostEqual(sum(g.area for g in geoms), 250000)
        for i, a in enumerate(geoms):
            for b in geoms[i+1:]:
                self.assertLess(a.intersection(b).area, .001)
        self.assertEqual(output, nature.partition([lake, forest, field]))

    def test_pack_switches_reference_existing_pixel_surfaces_and_real_normals(self):
        import fast_art
        import fast_pixel
        pixels = fast_pixel.fields(fast_style.ART)
        real = fast_art.pack_fields('real', fast_style.ART)
        for kind in nature.NATURAL:
            file = pixels['e2rf_' + kind]['baseColorMap'].rsplit('/', 1)[1]
            self.assertIn(file.split('.')[0], fast_pixel.SURFACES)
            for field in ('baseColorMap', 'normalMap'):
                stage = fast_style.real_stage(kind)
                if field in stage:
                    self.assertEqual(real['e2rf_' + kind][field], stage[field])

    def test_new_classes_survive_compiler_and_v2_binary(self):
        store = Store()
        records = [(n, k, {'tags': {'natural': k, 'leaf_type': 'mixed'}},
                    Polygon([(n*50, 0), (n*50+40, 0), (n*50+40, 40), (n*50, 40)]))
                   for n, k in enumerate(nature.NATURAL)]
        store.scenery = lambda rect: records
        cell = build_country.compile_cell(store, [0, 0, 1000, 1000])
        decoded = country_format.decode_cell(country_format.encode_cell(cell, (0, 0)), (0, 0), [0, 0, 1000, 1000])
        self.assertEqual({f['kind'] for f in decoded['areas']}, set(nature.NATURAL))
        self.assertTrue(all(f['tags']['leaf_type'] == 'mixed' for f in decoded['areas']))

    def test_worldcover_window_nodata_holes_and_osm_override(self):
        import rasterio
        from affine import Affine
        from landcover import WorldCover, provenance
        store = Store()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = temp.name
        path = Path(directory) / 'worldcover.tif'
        data = np.full((40, 40), 10, dtype=np.uint8)
        data[:10, :10] = 0
        data[:10, 10:20] = 50  # built-up is not a forest/field guess
        data[20:, :] = 40
        with rasterio.open(path, 'w', driver='GTiff', width=40, height=40, count=1,
                           dtype='uint8', crs=store.frame.crs, nodata=0,
                           transform=Affine(25, 0, store.frame.cx, 0, -25, store.frame.cy+1000)) as ds:
            ds.write(data, 1)
        cover = WorldCover([path], store.frame)
        self.addCleanup(cover.close)
        override = Polygon([(0, 0), (1000, 0), (1000, 250), (0, 250)])
        areas = list(cover.areas([0, 0, 1000, 1000], [override]))
        self.assertEqual({k for k, _, _ in areas}, {'forest', 'farmland'})
        self.assertAlmostEqual(sum(g.area for _, g, _ in areas), 625000)
        self.assertTrue(all(g.intersection(override).area < .01 for _, g, _ in areas))
        meta = provenance([path])
        self.assertEqual(meta['license'], 'CC-BY-4.0')
        self.assertEqual(len(meta['files'][0]['sha256']), 64)
        self.assertNotIn(directory, json.dumps(meta))
        store.landcover = cover
        compiled = build_country.compile_cell(store, [0, 0, 1000, 1000])
        self.assertTrue(any(f['tags'].get('source') == 'worldcover-2021-v200' for f in compiled['areas']))


class Runtime(unittest.TestCase):
    def setUp(self):
        self.g = harness.GeneratorTests()
        self.g.setUp()

    def statics(self, manifest):
        return sorted((i.shape, tuple(i.pos.values()), i.scale, tuple(i.rot.values()))
                      for i in manifest['items'].values() if i.kind == 'static')

    def test_every_natural_material_renders_near_far_and_horizon(self):
        for kind in nature.NATURAL:
            cell = {'terrain': terrain(), 'areas': [patch(kind)]}
            for options, index in (({}, 0), ({'far': True}, 2), ({'horizon': True}, 2)):
                with self.subTest(kind=kind, options=options):
                    manifest = self.g.build([cell], **options)[index]
                    self.assertTrue(list(self.g.triangles(manifest, 'e2rf_' + kind)))

    def test_far_plants_are_exact_near_subset_with_same_shape_transform(self):
        cell = {'terrain': terrain(1200), 'areas': [patch('forest', tags={'leaf_type': 'needleleaved'})],
                'buildings': [dict(patch('building', (100, 100, 200, 200)), height=10)]}
        near = self.statics(self.g.build([cell])[1])
        far = self.statics(self.g.build([cell], far=True)[2])
        self.assertTrue(far)
        self.assertGreater(len(far), 40)
        self.assertLessEqual(len(far), 800)
        self.assertTrue(set(far).issubset(set(near)))
        self.assertTrue(all('scots_pine' in s[0] for s in far))
        self.assertTrue(all(not (100 < s[1][0] < 200 and 100 < s[1][1] < 200) for s in near))
        self.assertEqual(near, self.statics(self.g.build([copy.deepcopy(cell)])[1]))

    def test_vegetation_excludes_water_rock_and_regional_tree_line(self):
        cell = {'terrain': terrain(), 'areas': [patch('forest'), patch('water', (0, 0, 250, 500)),
                                               patch('bare_rock', (250, 0, 500, 500))]}
        near = self.statics(self.g.build([cell])[1])
        far = self.statics(self.g.build([cell], far=True)[2])
        self.assertTrue(near)
        self.assertTrue(all('rock' in item[0] and item[1][0] >= 250 for item in near))
        self.assertTrue(set(far).issubset(set(near)))
        self.assertTrue(all('rock' in item[0] for item in far))
        cell['areas'] = [patch('forest')]
        cell['terrain'] = terrain(2500)
        self.assertFalse(self.statics(self.g.build([cell], nature={'treeLine': 2000})[1]))
        grown = self.statics(self.g.build([cell], nature={'treeLine': 3000})[1])
        self.assertTrue(any('_bush_' not in item[0] and 'grass' not in item[0] and 'rock' not in item[0] for item in grown))

    def test_scrub_uses_stock_bushes_not_full_trees(self):
        cell = {'terrain': terrain(), 'areas': [patch('scrub')]}
        items = self.statics(self.g.build([cell])[1])
        self.assertTrue(items)
        self.assertTrue(all('_bush_' in i[0] for i in items))

    def test_steep_rock_uses_vertical_uv_and_keeps_p0_normals(self):
        cell = {'terrain': dict(terrain(), heights=[0, 3600, 0, 3600]), 'areas': [patch('meadow')]}
        manifest = self.g.build([cell])[0]
        meshes = list(self.g.meshes(manifest, 'e2rf_bare_rock'))
        self.assertTrue(meshes)
        steep_forest = self.statics(self.g.build([dict(cell, areas=[patch('forest')])])[1])
        self.assertTrue(steep_forest)
        self.assertTrue(all('rock' in item[0] for item in steep_forest))
        for item, mesh in meshes:
            for face in mesh.faces.values():
                p, uv = mesh.verts[face.v+1], mesh.uvs[face.u+1]
                self.assertAlmostEqual(uv.u, (p.y+item.pos[2])/12, places=4)
                self.assertAlmostEqual(uv.v, (p.z+item.pos[3])/12, places=4)
            self.assertTrue(all(n.z > 0 for n in mesh.normals.values()))

    def test_negative_tile_boundary_has_no_duplicate_plants(self):
        cell = {'areas': [patch('forest', (-1000, -500, 500, 500))]}
        left = self.statics(self.g.build([cell], i=-1)[1])
        right = self.statics(self.g.build([cell], i=0)[1])
        self.assertTrue(left and right)
        self.assertFalse({i[1][:2] for i in left} & {i[1][:2] for i in right})

    def test_worldcover_base_does_not_add_overlay_geometry_or_override_osm(self):
        forest = patch('forest', tags={'source': 'worldcover-2021-v200'})
        cell = {'terrain': terrain(), 'areas': [forest]}
        road, scene, _, _ = self.g.build([cell], maxTrees=0)
        self.assertEqual(len(list(self.g.triangles(road, 'e2rf_forest'))), 800)
        self.assertFalse(list(self.g.triangles(scene)))
        cell['areas'].append(patch('bare_rock', tags={'natural': 'bare_rock'}))
        for far, index in ((False, 0), (True, 2)):
            output = self.g.build([cell], far=far)[index]
            self.assertTrue(list(self.g.triangles(output, 'e2rf_bare_rock')))
            self.assertFalse(list(self.g.triangles(output, 'e2rf_forest')))

    def ribbon(self, tags, points=None, line=False):
        points = points or [[0, 250, 10], [500, 250, 10]]
        if line:
            cell = {'lines': [{'id': 't', 'kind': 'path', 'tags': tags, 'points': points}]}
        else:
            cell = {'roads': [{'id': 'r', 'nodes': ['a', 'b'], 'width': 8, 'tags': tags, 'points': points}]}
        return self.g.build([cell])[0]

    def profile(self, manifest, material):
        triangles = list(self.g.triangles(manifest, material))
        return [z for z in (self.g.surface_at(triangles, x, 250) for x in range(16, 485, 12)) if z is not None]

    def test_country_roads_gain_a_grade_without_moving_city_asphalt(self):
        city = self.ribbon({'highway': 'residential'})
        city_z = self.profile(city, 'e2rf_road')
        self.assertTrue(city_z)
        self.assertTrue(all(abs(z - 10.1) < 1e-4 for z in city_z))
        self.assertTrue(list(self.g.triangles(city, 'e2rf_sidewalk')))
        worn = self.ribbon({'highway': 'residential', 'smoothness': 'bad'})
        self.assertTrue(self.profile(worn, 'e2rf_road_worn'))
        self.assertFalse(list(self.g.triangles(worn, 'e2rf_road')))
        self.assertTrue(list(self.g.triangles(worn, 'e2rf_sidewalk')))
        self.assertTrue(all(abs(z - 10.1) < 1e-4 for z in self.profile(worn, 'e2rf_road_worn')))
        dirt = self.ribbon({'highway': 'unclassified', 'surface': 'dirt'})
        dirt_z = self.profile(dirt, 'e2rf_road_dirt')
        self.assertGreater(max(dirt_z) - min(dirt_z), .05)
        self.assertFalse(list(self.g.triangles(dirt, 'e2rf_sidewalk')))
        mouths = [p[2] for triangle in self.g.triangles(dirt, 'e2rf_road_dirt') for p in triangle if abs(p[0]) < 1e-6]
        self.assertTrue(mouths)
        self.assertTrue(all(abs(z - 10.1) < 1e-4 for z in mouths))
        gravel = self.ribbon({'highway': 'primary', 'surface': 'gravel'})
        self.assertTrue(self.profile(gravel, 'e2rf_road_gravel'))
        self.assertFalse(list(self.g.triangles(gravel, 'e2rf_road_major')))
        self.assertFalse(list(self.g.triangles(gravel, 'e2rf_sidewalk')))
        track = self.ribbon({'highway': 'track', 'tracktype': 'grade5', 'surface': 'grass'}, line=True)
        track_z = self.profile(track, 'e2rf_road_track')
        self.assertGreater(max(track_z) - min(track_z), .08)
        self.assertFalse(list(self.g.triangles(track, 'e2rf_trodden')))
        self.assertFalse(list(self.g.triangles(track, 'e2rf_road')))

    def test_meadow_grass_forest_floor_and_rocks_stay_off_roads_and_far_grass(self):
        meadow = {'terrain': terrain(), 'areas': [patch('meadow')],
                  'roads': [{'id': 'r', 'nodes': ['a', 'b'], 'width': 8, 'tags': {'highway': 'residential', 'sidewalk': 'no'},
                             'points': [[0, 250, 10], [500, 250, 10]]}]}
        grass = [item for item in self.statics(self.g.build([meadow])[1]) if 'grass' in item[0]]
        self.assertTrue(grass)
        self.assertTrue(all(abs(item[1][1] - 250) > 6 for item in grass))
        self.assertFalse([item for item in self.statics(self.g.build([meadow], far=True)[2]) if 'grass' in item[0]])
        forest = self.statics(self.g.build([{'terrain': terrain(), 'areas': [patch('forest')]}])[1])
        self.assertTrue(any('grass' in item[0] for item in forest))
        self.assertTrue(any('_bush_' in item[0] for item in forest))
        self.assertTrue(any('_bush_' not in item[0] and 'grass' not in item[0] for item in forest))
        far = self.statics(self.g.build([{'terrain': terrain(), 'areas': [patch('forest', tags={'leaf_type': 'needleleaved'})]}], far=True)[2])
        self.assertTrue(far)
        self.assertTrue(all('scots_pine' in item[0] for item in far))
        rocks = self.statics(self.g.build([{'terrain': terrain(), 'areas': [patch('scree')]}])[1])
        self.assertTrue(rocks)
        self.assertTrue(all('rock' in item[0] for item in rocks))
        self.assertFalse(self.statics(self.g.build([{'terrain': terrain(), 'areas': [patch('scree')]}], far=True, maxRocks=0)[2]))

    def test_open_ground_gets_grass_and_flat_ground_stays_clear_of_rocks(self):
        open_ground = {'terrain': terrain()}
        near = self.statics(self.g.build([open_ground])[1])
        grass = [item for item in near if 'grass' in item[0]]
        trees = [item for item in near if 'grass' not in item[0] and 'rock' not in item[0]]
        self.assertTrue(grass)
        self.assertGreater(len(trees), 40)
        self.assertTrue(any('scots_pine' in item[0] for item in trees))
        self.assertTrue(any('scots_pine' not in item[0] for item in trees))
        self.assertFalse([item for item in near if 'rock' in item[0]])
        far = self.statics(self.g.build([open_ground], far=True)[2])
        self.assertFalse([item for item in far if 'grass' in item[0]])
        far_trees = [item for item in far if 'grass' not in item[0] and 'rock' not in item[0]]
        self.assertGreater(len(far_trees), 40)
        self.assertLessEqual(len(far_trees), 800)
        self.assertTrue(set(far_trees).issubset(set(trees)))
        steep = {'terrain': dict(terrain(), heights=[0, 3600, 0, 3600])}
        rocks = self.statics(self.g.build([steep])[1])
        self.assertTrue(rocks)
        self.assertTrue(all('rock' in item[0] for item in rocks))
        far_rocks = self.statics(self.g.build([steep], far=True)[2])
        self.assertTrue(far_rocks)
        self.assertLessEqual(len(far_rocks), 16)
        self.assertTrue(set(far_rocks).issubset(set(rocks)))


if __name__ == '__main__':
    unittest.main()
