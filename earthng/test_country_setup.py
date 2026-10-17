"""Country setup: asset blocks, Geofabrik index, ready-pack discovery. No downloads."""
import unittest
from pathlib import Path
import zipfile

import country_fetch
import looks
import places


INDEX = {
    'features': [
        {'properties': {'id': 'europe', 'name': 'Europe', 'urls': {'pbf': 'https://download.geofabrik.de/europe-latest.osm.pbf'}}},
        {'properties': {'id': 'ukraine', 'parent': 'europe', 'name': 'Ukraine',
                        'urls': {'pbf': 'https://download.geofabrik.de/europe/ukraine-latest.osm.pbf'}}},
        {'properties': {'id': 'georgia', 'parent': 'asia', 'name': 'Georgia',
                        'urls': {'pbf': 'https://download.geofabrik.de/asia/georgia-latest.osm.pbf'}}},
        {'properties': {'id': 'asia', 'name': 'Asia', 'urls': {'pbf': 'https://download.geofabrik.de/asia-latest.osm.pbf'}}},
        {'properties': {'id': 'north-america', 'name': 'North America',
                        'urls': {'pbf': 'https://download.geofabrik.de/north-america-latest.osm.pbf'}}},
        {'properties': {'id': 'us/georgia', 'parent': 'north-america', 'name': 'Georgia',
                        'urls': {'pbf': 'https://download.geofabrik.de/north-america/us/georgia-latest.osm.pbf'}}},
        {'properties': {'id': 'berlin', 'parent': 'germany', 'name': 'Berlin',
                        'urls': {'pbf': 'https://download.geofabrik.de/europe/germany/berlin-latest.osm.pbf'}}},
        {'properties': {'id': 'germany', 'parent': 'europe', 'name': 'Germany',
                        'urls': {'pbf': 'https://download.geofabrik.de/europe/germany-latest.osm.pbf'}}},
        {'properties': {'id': 'secret', 'name': 'Nope', 'urls': {'pbf': 'https://example.com/secret.osm.pbf'}}},
    ]
}


class LookTests(unittest.TestCase):
    def setUp(self):
        self.places = places.by_id(places.parse_index(INDEX))

    def test_postsoviet_default_is_panel_and_worn_asphalt(self):
        look = looks.recommended('ukraine', self.places)
        self.assertEqual(look['block'], 'postsoviet')
        self.assertEqual(look['buildings'], 'panel')
        self.assertEqual(look['asphalt'], 'worn')
        self.assertEqual(look['trees'], 'regional')

    def test_child_region_follows_its_country(self):
        look = looks.recommended('berlin', self.places)
        self.assertEqual(look['block'], 'western')
        self.assertEqual(look['buildings'], 'mixed')
        self.assertEqual(look['asphalt'], 'smooth')

    def test_us_state_does_not_inherit_the_country_of_georgia(self):
        country = looks.recommended('georgia', self.places)
        state = looks.recommended('us/georgia', self.places)
        self.assertEqual(country['block'], 'postsoviet')
        self.assertEqual(state['block'], 'north-america')
        self.assertEqual(state['markings'], 'yellow')

    def test_unknown_place_stays_on_the_general_block(self):
        self.assertEqual(looks.recommended('atlantis', self.places)['block'], 'general')

    def test_override_keeps_the_block_and_rejects_an_unknown_choice(self):
        look = looks.canonical({'block': 'postsoviet', 'buildings': 'brick'})
        self.assertEqual((look['buildings'], look['asphalt']), ('brick', 'worn'))
        with self.assertRaises(ValueError):
            looks.canonical({'block': 'postsoviet', 'asphalt': 'cobble'})

    def test_layers_drop_buildings_and_keep_roads(self):
        include = looks.canonical_include({'buildings': False})
        self.assertFalse(looks.keep_scenery('building', include))
        self.assertTrue(looks.keep_scenery('water', include))
        self.assertTrue(looks.keep_scenery('road', include))
        self.assertTrue(looks.keep_scenery('forest', None))

    def test_slug_flattens_a_state_id(self):
        self.assertEqual(places.slug('us/california'), 'us-california')

    def test_index_ignores_a_pbf_outside_geofabrik(self):
        ids = {place['id'] for place in places.parse_index(INDEX)}
        self.assertNotIn('secret', ids)
        self.assertIn('ukraine', ids)

    def test_search_finds_a_name(self):
        found = places.search(list(self.places.values()), 'ber')
        self.assertEqual([place['id'] for place in found], ['berlin'])


class PackFileTests(unittest.TestCase):
    def test_missing_osmium_does_not_ask_for_another_checkout(self):
        import builtins
        import unittest.mock
        real = builtins.__import__

        def guarded(name, globals=None, locals=None, fromlist=(), level=0):
            if name == 'osmium':
                raise ImportError(name)
            return real(name, globals, locals, fromlist, level)

        with unittest.mock.patch('builtins.__import__', guarded), \
                unittest.mock.patch.object(Path, 'is_file', return_value=False):
            with self.assertRaises(RuntimeError) as caught:
                country_fetch._python()
        self.assertNotIn('earth2road', str(caught.exception).lower())
        self.assertIn('requirements-fast.txt', str(caught.exception))

    def test_install_requires_the_country_manifest(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'earthng_ukraine.zip'
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('earthng/countries/ukraine/manifest.json', '{}')
            mods = root / 'profile'
            installed = country_fetch.install_zip(source, mods, 'ukraine')
            self.assertTrue(installed.is_file())
            self.assertEqual(country_fetch.availability('ukraine', mods, root)['state'], 'installed')

    def test_refresh_replaces_lua_and_leaves_art(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            root = Path(folder)
            mods = root / 'profile'
            planet = mods / 'mods' / 'earthng_planet.zip'
            planet.parent.mkdir(parents=True)
            with zipfile.ZipFile(planet, 'w') as archive:
                archive.writestr('lua/ge/extensions/e2r/generator.lua', 'old')
                archive.writestr('e2r_stream/fast-art/v10/real/keep.dds', b'dds')
            # refresh_planet reads the repository mod/, so the lua member changes
            # and the untouched art member stays.
            country_fetch.refresh_planet(mods, emit=lambda _text: None)
            with zipfile.ZipFile(planet) as archive:
                names = set(archive.namelist())
                self.assertIn('e2r_stream/fast-art/v10/real/keep.dds', names)
                self.assertNotEqual(archive.read('lua/ge/extensions/e2r/generator.lua'), b'old')
                self.assertIn(b'mark_yellow', archive.read('earthng/planet/materials.json'))


if __name__ == '__main__':
    unittest.main()
