"""Mounted-country discovery invariants, executed against the real catalog with LuaJIT."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'out/test-tools'))
from lupa.luajit21 import LuaRuntime


CATALOG = Path(__file__).resolve().parents[1] / 'mod/lua/ge/extensions/e2r/countries.lua'


def country(country_id='ukraine', runtime='baked', coverage=None):
    result = {
        'schemaVersion': 1, 'kind': 'earthng-country', 'id': country_id,
        'title': country_id.title(), 'version': '1.0.0', 'runtime': runtime,
        'coverage': coverage or [[22, 44, 41, 53]],
    }
    if runtime != 'source':
        result['regions'] = [{'id': 'kyiv', 'manifest': 'regions/kyiv.json',
                              'coverage': coverage or [[30, 50, 31, 51]]}]
    return result


class CountryCatalogTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute('''
          mounted, documents, files, discoveryCalls = {}, {}, {}, {}
          FS = {
            findFiles=function(self, root, pattern, depth, recursive, includeDirs)
              discoveryCalls[#discoveryCalls+1] = {root,pattern,depth,recursive,includeDirs}
              return mounted
            end,
            fileExists=function(self, path) return files[path] == true end,
          }
          jsonReadFile=function(path)
            local value = documents[path]
            if value == 'broken-json' then error('invalid JSON') end
            return value
          end
        ''')
        self.mod = self.lua.execute(CATALOG.read_text(encoding='utf8'))

    def table(self, value):
        if isinstance(value, dict):
            return self.lua.table_from({k: self.table(v) for k, v in value.items()})
        if isinstance(value, list):
            return self.lua.table_from([self.table(v) for v in value])
        return value

    def mount(self, manifests, paths=None):
        docs, files = {}, {}
        for manifest in manifests:
            path = f'/earthng/countries/{manifest["id"]}/manifest.json'
            docs[path] = manifest
            for region in manifest.get('regions', []):
                region_path = region.get('manifest', '')
                if not region_path.startswith('/'):
                    region_path = f'/earthng/countries/{manifest["id"]}/' + region_path
                files[region_path] = True
        self.lua.globals().documents = self.table(docs)
        self.lua.globals().files = self.table(files)
        self.lua.globals().mounted = self.table(paths if paths is not None else list(docs))

    def test_discovers_mounted_vfs_and_excludes_source_from_landings(self):
        self.mount([country('zanzibar', 'source'), country()])
        found, errors = self.mod.scan()
        self.assertEqual([entry.id for entry in found.values()], ['ukraine', 'zanzibar'])
        self.assertEqual(len(errors), 0)
        self.assertTrue(found[1].available)
        self.assertIsNone(found[1].reason)
        self.assertFalse(found[2].available)
        self.assertEqual(found[2].reason, 'source-only')
        self.assertEqual(found[1].regions[1].manifest, '/earthng/countries/ukraine/regions/kyiv.json')
        self.assertEqual(len(self.mod.coverage()), 1)
        selected, region = self.mod.at(50.5, 30.5)
        self.assertEqual((selected.id, region.id), ('ukraine', 'kyiv'))
        self.assertIsNone(self.mod.at(49, 35))
        call = self.lua.globals().discoveryCalls[1]
        self.assertEqual([call[i] for i in range(1, 6)],
                         ['/earthng/countries/', 'manifest.json', -1, True, False])

    def test_refresh_drops_deactivated_mods(self):
        self.mount([country()])
        self.mod.scan()
        self.mount([])
        found, errors = self.mod.refresh()
        self.assertEqual((len(found), len(errors), len(self.mod.coverage())), (0, 0, 0))
        self.assertIsNone(self.mod.at(50.5, 30.5))

    def test_duplicate_mounted_path_is_rejected(self):
        path = '/earthng/countries/ukraine/manifest.json'
        self.mount([country()], [path, path])
        found, errors = self.mod.scan()
        self.assertEqual(len(found), 0)
        self.assertEqual((len(errors), errors[1].code), (1, 'duplicate'))

    def test_ignores_nested_and_outside_manifests(self):
        root = '/earthng/countries/ukraine/manifest.json'
        self.mount([country()], ['/mods/disabled/manifest.json', root,
                                 '/earthng/countries/ukraine/regions/manifest.json'])
        found, errors = self.mod.scan()
        self.assertEqual((len(found), len(errors)), (1, 0))

    def test_invalid_shared_fields_are_rejected(self):
        for field, value in [('schemaVersion', 2), ('kind', 'something-else'), ('id', 'UKRAINE'),
                             ('title', ''), ('version', 1), ('runtime', 'future'),
                             ('coverage', [[22, 53, 41, 44]]),
                             ('coverage', [[22, 44, float('inf'), 53]]),
                             ('coverage', [[180, 44, -180, 53]])]:
            with self.subTest(field=field, value=value):
                manifest = country()
                manifest[field] = value
                self.mount([manifest])
                found, errors = self.mod.scan()
                self.assertEqual((len(found), len(errors)), (0, 1))

    def test_manifest_directory_must_match_id(self):
        self.mount([country()])
        self.lua.execute('''
          documents['/earthng/countries/another/manifest.json'] = documents[mounted[1]]
          mounted = {'/earthng/countries/another/manifest.json'}
        ''')
        found, errors = self.mod.scan()
        self.assertEqual((len(found), errors[1].code), (0, 'invalid'))

    def test_runtime_entrypoints_cannot_escape_country_namespace(self):
        for path in ['../elsewhere.json', '/earthng/countries/other/regions/kyiv.json',
                     'regions/../../other.json', 'regions\\kyiv.json',
                     'regions//kyiv.json', 'https://example.test/kyiv.json',
                     'regions/%2e%2e/kyiv.json', 'regions/kyiv.json#fragment']:
            with self.subTest(path=path):
                manifest = country()
                manifest['regions'][0]['manifest'] = path
                self.mount([manifest])  # even an existing file cannot make an unsafe path valid
                found, errors = self.mod.scan()
                self.assertEqual((len(found), errors[1].code), (0, 'invalid'))

    def test_missing_region_manifest_never_becomes_available(self):
        self.mount([country()])
        self.lua.globals().files = self.lua.table()
        found, errors = self.mod.scan()
        self.assertEqual((len(found), len(errors), len(self.mod.coverage())), (0, 1, 0))

    def test_invalid_json_is_reported_and_does_not_abort_scan(self):
        self.mount([country('romania'), country()])
        self.lua.globals().documents['/earthng/countries/romania/manifest.json'] = 'broken-json'
        found, errors = self.mod.scan()
        self.assertEqual((len(found), found[1].id, len(errors), errors[1].code),
                         (1, 'ukraine', 1, 'json'))

    def test_regions_and_overlapping_packages_resolve_deterministically(self):
        first, second = country('zulu'), country('alpha')
        second['regions'].append(copy.deepcopy(second['regions'][0]))
        second['regions'][0]['id'] = 'zulu'
        second['regions'][1]['id'] = 'alpha'
        self.mount([first, second])
        self.mod.scan()
        selected, region = self.mod.at(50.5, 30.5)
        self.assertEqual((selected.id, region.id), ('alpha', 'alpha'))

    def test_dateline_coverage_supports_landings_and_map_rectangles(self):
        self.mount([country('dateline', coverage=[[170, -20, -170, -10]])])
        found, errors = self.mod.scan()
        self.assertEqual((len(found), len(errors)), (1, 0))
        self.assertEqual(self.mod.at(-15, 175)[0].id, 'dateline')
        self.assertEqual(self.mod.at(-15, -175)[0].id, 'dateline')
        self.assertIsNone(self.mod.at(-15, 0))
        coverage = self.mod.coverage()
        self.assertEqual([[box[i] for i in range(1, 5)] for box in coverage.values()],
                         [[170, -20, 180, -10], [-180, -20, -170, -10]])


if __name__ == '__main__':
    unittest.main()
