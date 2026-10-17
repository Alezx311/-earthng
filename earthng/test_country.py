"""Native-country source export invariants, independent of BeamNG and network."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

import numpy as np
from shapely.geometry import LineString, Point, Polygon

import build_country as country
import country_format as binary
import fast_sources as sources


ROAD = {'xy': [[-200, 100], [33, 100], [33, 600], [1400, 600]],
        'prof': [[0, 233, 500, 733, 2100], [40, 41, 50, 55, 65]],
        'width': 7, 'tags': {'highway': 'residential', 'name': 'Вулиця'},
        'nodes': [9007199254740993, 9007199254740994]}


class Terrain:
    def sample(self, xy):
        xy = np.asarray(xy)
        return 40 + xy[:, 0] * .01 + xy[:, 1] * .02


class Store:
    def __init__(self):
        self.frame = sources.Frame.at(30.52, 50.45)
        self.terrain = Terrain()
        self.source_index = {'source': 'C:\\private\\sources\\ukraine-260918.osm.pbf'}
        self.db = Mock()
        self.queries = []

    def covered(self, rect):
        return rect[0] >= 0 and rect[1] >= 0 and rect[2] <= 2000 and rect[3] <= 1000

    def query(self, rect):
        self.queries.append(rect)
        road = (17, 'road', copy.deepcopy(ROAD), LineString(ROAD['xy']))
        return [road, (-1, 'junction', {'h': 50 + sources.ROAD_LIFT},
                       Polygon([(0, 0), (20, 0), (20, 20), (0, 20)])), road]

    def scenery(self, rect):
        building = (1, 'building', {'tags': {'building': 'yes', 'height': '12 m', 'min_height': '3'}},
                    Polygon([(900, 0), (1100, 0), (1100, 100), (900, 100)],
                            holes=[[(950, 25), (950, 75), (1050, 75), (1050, 25)]]))
        return [building, building,
                (2, 'water', {'tags': {}}, Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])),
                (-3, 'fence', {'tags': {'barrier': 'fence'}}, LineString([(900, 30), (1100, 30)])),
                (-4, 'stop', {'tags': {'highway': 'bus_stop'}}, Point(1000, 20))]


class CountryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.region = self.root / 'source'
        self.region.mkdir()
        self.store = Store()
        self.meta = {'id': 'test_region', 'generator': sources.VERSION, 'bbox': [30, 50, 31, 51],
                     'frame': self.store.frame.spec(), 'dem': '/private/dem', 'source': '/private/osm'}
        self.save_meta()
        (self.region / 'sources.sqlite').write_bytes(b'author-only database')
        (self.region / 'do-not-ship.exe').write_bytes(b'not a game dependency')

    def save_meta(self):
        (self.region / 'region.json').write_text(json.dumps(self.meta), encoding='utf8')

    def build(self, bbox=(-1000, 0, 2000, 1000)):
        with patch.object(sources, 'Store', return_value=self.store):
            return country.build_country('ua', 'Україна', '0.1.0', [self.region],
                                         self.root / 'mods', bbox, emit=lambda _: None)

    def test_sources_only_country_zip_and_exact_export_coverage(self):
        result = self.build()
        with zipfile.ZipFile(result) as archive:
            names = archive.namelist()
            self.assertTrue(all(name.startswith('earthng/countries/ua/') and name.endswith(('.json', '.bin'))
                                for name in names))
            self.assertEqual(len(names), 3)  # country, region and one block of two source cells
            index = json.loads(archive.read('earthng/countries/ua/manifest.json'))
            self.assertEqual(index['runtime'], 'native')
            self.assertEqual(index['sourceFormat'], country.FORMAT)
            self.assertEqual(index['title'], 'Україна')
            self.assertEqual(index['source'], 'OpenStreetMap contributors')
            self.assertEqual(index['license'], 'ODbL-1.0')
            self.assertEqual(index['sourceId'], 'ukraine-260918')
            self.assertEqual(index['sourceDate'], '2026-09-18')
            descriptor = index['regions'][0]
            self.assertEqual(descriptor['id'], 'test-region')
            region = json.loads(archive.read(descriptor['manifest'].lstrip('/')))
            self.assertEqual((region['schemaVersion'], region['runtime'], region['blockCells']), (2, 'lua', 8))
            self.assertEqual(region['sourceId'], index['sourceId'])
            self.assertEqual(region['sourceDate'], index['sourceDate'])
            self.assertEqual(region['cellCount'], 2)
            self.assertEqual(region['seaLevel'], sources.LIFT)  # the DEM sea surface in region metres
            self.assertEqual(set(region['blocks']), {'0_0'})
            block = region['blocks']['0_0']
            self.assertEqual(block['mask'], '3000000000000000')  # slots 0 and 1: cells 0_0 and 1_0
            self.assertEqual(region['coverage'], [self.store.frame.geo_bbox([0, 0, 2000, 1000])])
            self.assertEqual(index['coverage'], region['coverage'])
            self.assertNotIn('landing', region)  # 2 x 1 km has no full 3 x 3 km landing neighbourhood
            self.assertNotIn('/private', archive.read(descriptor['manifest'].lstrip('/')).decode())
            self.assertEqual(len(region['geo']), 8)
            data = archive.read(block['path'].lstrip('/'))
            ranges = binary.decode_block(data)
            self.assertEqual(sorted(ranges), [0, 1])
            cell = binary.decode_cell(data, (1000, 0), [1000, 0, 2000, 1000], *ranges[1])
            self.assertEqual(cell['terrain']['rect'], [1000, 0, 2000, 1000])
            self.assertEqual(cell['roads'][0]['nodes'][0], '9007199254740993')
        self.store.db.close.assert_called_once()

    def test_binary_cell_round_trip_keeps_centimetres_and_shared_seams(self):
        a = country.compile_cell(self.store, [0, 0, 1000, 1000])
        b = country.compile_cell(self.store, [1000, 0, 2000, 1000])
        da = binary.decode_cell(binary.encode_cell(a, (0, 0)), (0, 0), [0, 0, 1000, 1000])
        db = binary.decode_cell(binary.encode_cell(b, (1000, 0)), (1000, 0), [1000, 0, 2000, 1000])
        self.assertEqual(da['roads'], db['roads'])  # same integers whatever the cell origin
        for kind in binary.KINDS:
            self.assertEqual([f['id'] for f in da[kind]], [f['id'] for f in a[kind]])
        for p, q in zip(a['roads'][0]['points'], da['roads'][0]['points']):
            self.assertTrue(all(abs(x - y) <= .005 for x, y in zip(p, q)))
        self.assertEqual(da['roads'][0]['tags'], ROAD['tags'])
        self.assertEqual(da['buildings'][0]['height'], 12.)
        self.assertEqual(len(da['buildings'][0]['rings']), 2)
        self.assertEqual(da['points'][0]['pos'], [1000., 20., 50.4])
        heights = da['terrain']['heights']
        self.assertTrue(all(abs(x - y) <= .05 for x, y in zip(heights, a['terrain']['heights'])))
        self.assertEqual(heights[40::41], db['terrain']['heights'][0::41])
        with self.assertRaisesRegex(ValueError, 'trailing'):
            binary.decode_cell(binary.encode_cell(a, (0, 0)) + b'\x00', (0, 0), [0, 0, 1000, 1000])
        with self.assertRaisesRegex(ValueError, 'truncated'):
            binary.decode_cell(binary.encode_cell(a, (0, 0))[:-3], (0, 0), [0, 0, 1000, 1000])

    def test_large_areas_are_cut_to_the_padded_cell(self):
        forest = (5, 'forest', {'tags': {'landuse': 'forest'}},
                  Polygon([(-5000, -5000), (6000, -5000), (6000, 6000), (-5000, 6000)]))
        self.store.scenery = lambda rect: [forest]
        cell = country.compile_cell(self.store, [0, 0, 1000, 1000])
        ring = cell['areas'][0]['rings'][0]
        xs, ys = [p[0] for p in ring], [p[1] for p in ring]
        self.assertEqual((min(xs), min(ys), max(xs), max(ys)), (-100, -100, 1100, 1100))

    def test_ragged_package_edge_reads_quarters_from_several_tiles(self):
        calls = []

        def scenery(rect):
            calls.append(rect)
            if rect[2] - rect[0] > 600:
                raise ValueError('no_osm scenery coverage: ragged edge')
            if rect[0] < 0 and rect[1] < 0:
                raise ValueError('no_osm scenery coverage: open sea')
            return [(7, 'water', {'tags': {}}, Polygon([(0, 0), (50, 0), (50, 50)]))]
        self.store.scenery = scenery
        cell = country.compile_cell(self.store, [0, 0, 1000, 1000])
        self.assertEqual(len(cell['areas']), 1)  # one area, though three quarters returned it
        self.assertGreater(len(calls), 5)
        self.store.scenery = Mock(side_effect=ValueError('broken tile'))
        with self.assertRaisesRegex(ValueError, 'broken tile'):
            country.compile_cell(self.store, [0, 0, 1000, 1000])

    def test_grid_region_lands_on_roads_and_clips_coverage_to_its_core(self):
        self.store.covered = lambda rect: -3000 <= rect[0] and rect[2] <= 3000 and -3000 <= rect[1] and rect[3] <= 3000
        lon, lat = self.store.frame.geographic([-1500, 1500], [-1500, 1500])
        core = [float(lon[0]), float(lat[0]), float(lon[1]), float(lat[1])]
        with patch.object(sources, 'Store', return_value=self.store):
            result = country.build_country('ua', 'Україна', '1', [{'dir': self.region, 'core': core, 'id': 'e0300-n500'}],
                                           self.root / 'mods', emit=lambda _: None, landing=(50.45, 30.52))
        with zipfile.ZipFile(result) as archive:
            index = json.loads(archive.read('earthng/countries/ua/manifest.json'))
            self.assertEqual(index['landing'], {'lat': 50.45, 'lon': 30.52})
            region = json.loads(archive.read(index['regions'][0]['manifest'].lstrip('/')))
        self.assertEqual(region['id'], 'e0300-n500')
        self.assertEqual(region['cellCount'], 36)  # core +- 4 km margin, cut to source coverage
        self.assertEqual(set(region['blocks']), {'-1_-1', '0_-1', '-1_0', '0_0'})
        x, y = region['landing']
        self.assertTrue(-1000 <= x <= 1000 and -1000 <= y <= 1000)
        for box in region['coverage']:
            self.assertTrue(core[0] <= box[0] < box[2] <= core[2] and core[1] <= box[1] < box[3] <= core[3])

    def test_full_feature_halo_holes_profile_and_terrain_seams(self):
        a = country.compile_cell(self.store, [0, 0, 1000, 1000])
        b = country.compile_cell(self.store, [1000, 0, 2000, 1000])
        self.assertEqual(self.store.queries[0], [-100, -100, 1100, 1100])
        self.assertEqual(a['roads'], b['roads'])
        self.assertEqual(len(a['roads']), 1)  # duplicate source rows are harmless
        points = a['roads'][0]['points']
        self.assertIn([33., 100., 41.], points)  # original bend is exact
        self.assertIn([33., 367., 50.], points)  # solved profile knot is exact
        self.assertEqual(points[0], [-200., 100., 40.])  # full line, not clipped to the cell
        self.assertEqual(points[-1], [1400., 600., 65.])
        self.assertEqual(a['roads'][0]['nodes'][0], '9007199254740993')
        self.assertEqual(a['buildings'], b['buildings'])
        building = a['buildings'][0]
        self.assertEqual(len(a['buildings']), 1)
        self.assertEqual(len(building['rings']), 2)
        self.assertEqual(building['height'], 12.)
        self.assertEqual(building['minHeight'], 3.)
        self.assertEqual(a['junctions'][0]['height'], 50.)
        self.assertEqual(a['points'][0]['pos'], [1000., 20., 50.4])
        self.assertEqual(a['lines'][0]['kind'], 'fence')
        self.assertEqual(a['areas'][0]['kind'], 'water')
        ta, tb = a['terrain'], b['terrain']
        self.assertEqual((ta['nx'], ta['ny'], len(ta['heights'])), (41, 41, 1681))
        self.assertEqual(ta['heights'][40::41], tb['heights'][0::41])

    def test_identical_sources_produce_identical_zip(self):
        first = self.build().read_bytes()
        second = self.build().read_bytes()
        self.assertEqual(first, second)

    def test_failed_export_preserves_previous_archive_and_removes_temporary(self):
        target = self.build()
        expected = target.read_bytes()
        self.store.terrain.sample = Mock(side_effect=FileNotFoundError('no_dem'))
        with self.assertRaisesRegex(ValueError, 'no_dem'):  # every cell lacked elevation
            self.build()
        self.assertEqual(target.read_bytes(), expected)
        self.assertEqual(list(target.parent.glob('*.tmp')), [])

    def test_no_coverage_does_not_create_available_country(self):
        with self.assertRaisesRegex(ValueError, 'no fully covered source cells'):
            self.build(bbox=(-2000, -2000, -1000, -1000))
        self.assertFalse((self.root / 'mods' / 'earthng_ua.zip').exists())

    def test_bad_metadata_paths_and_normalized_duplicate_ids_are_rejected(self):
        for invalid in ('../escape', '-ua', 'ua-', 'ua_'):
            with self.subTest(country_id=invalid), self.assertRaisesRegex(ValueError, 'country id'):
                country.build_country(invalid, 'Title', '1', [self.region], self.root)
        self.meta['generator'] = 'old-index'
        self.save_meta()
        with self.assertRaisesRegex(ValueError, 'unsupported source index'):
            self.build()
        self.meta['generator'] = sources.VERSION
        self.save_meta()
        other = self.root / 'other'
        other.mkdir()
        (other / 'region.json').write_text(json.dumps(dict(self.meta, id='test-region')), encoding='utf8')
        with self.assertRaisesRegex(ValueError, 'duplicate region'):
            country.build_country('ua', 'Title', '1', [self.region, other], self.root)

    def test_source_provenance_never_exports_paths_or_invents_invalid_date(self):
        fields = country._provenance(self.meta, {'source': '/private/data/romania-261014.osm.pbf'})
        self.assertEqual(fields['sourceId'], 'romania-261014')
        self.assertEqual(fields['sourceDate'], '2026-10-14')
        self.assertNotIn('/private', json.dumps(fields))
        fields = country._provenance(self.meta, {'source': 'area-261399.osm.pbf'})
        self.assertNotIn('sourceDate', fields)

    def test_multiple_independent_regions_have_separate_namespaces(self):
        other = self.root / 'other'
        other.mkdir()
        (other / 'region.json').write_text(json.dumps(dict(self.meta, id='second-region')), encoding='utf8')
        with patch.object(sources, 'Store', side_effect=lambda _: Store()):
            result = country.build_country('ua', 'Title', '1', [self.region, other], self.root,
                                           bbox=[0, 0, 1000, 1000], emit=lambda _: None)
        with zipfile.ZipFile(result) as archive:
            index = json.loads(archive.read('earthng/countries/ua/manifest.json'))
            self.assertEqual(len(index['regions']), 2)
            self.assertTrue(any('/second-region/blocks/' in name for name in archive.namelist()))

    def test_dem_spikes_and_pits_take_the_neighbourhood_median_with_equal_seams(self):
        class Noisy:
            def sample(self, xy):
                xy = np.asarray(xy)
                h = 40 + xy[:, 0] * .2  # a steep, real 20 % slope stays
                h[(np.abs(xy[:, 0] - 1000) < 1) & (np.abs(xy[:, 1] - 500) < 1)] += 230  # a spike
                h[(np.abs(xy[:, 0] - 500) < 1) & (np.abs(xy[:, 1] - 500) < 1)] -= 90   # a pit
                return h
        terrain = country.Despiked(Noisy())
        self.assertEqual(list(terrain.sample([[1000, 500], [500, 500], [300, 300]])), [240., 140., 100.])
        self.store.terrain = terrain
        a = country.compile_cell(self.store, [0, 0, 1000, 1000])['terrain']['heights']
        b = country.compile_cell(self.store, [1000, 0, 2000, 1000])['terrain']['heights']
        self.assertEqual(a[40::41], b[0::41])
        self.assertEqual((a[20 * 41 + 40], a[20 * 41 + 20]), (240., 140.))  # spike and pit gone

    def test_nonfinite_geometry_and_terrain_are_rejected(self):
        data = copy.deepcopy(ROAD)
        data['prof'][1][0] = float('nan')
        with self.assertRaisesRegex(ValueError, 'non-finite'):
            country.road_feature(1, data)
        self.store.terrain.sample = lambda xy: np.full(len(xy), float('inf'))
        with self.assertRaisesRegex(ValueError, 'non-finite'):
            country.compile_cell(self.store, [0, 0, 1000, 1000])


if __name__ == '__main__':
    unittest.main()
