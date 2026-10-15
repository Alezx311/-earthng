"""Build the tester package: everything needed to drive the fast generator (all of Ukraine by default).

    C:/js/Earth2Road/.venv/Scripts/python.exe earthng/package.py --out out/package

<out>/EarthNG-<Area>/  (Ukraine for ukraine-* tiles, UK, California, NewYork)
    EarthNG.bat                 launcher window (earthng/launcher.py): settings, mod, companion, game
    start.bat, start.ps1        the same from a console, default settings
    ІНСТРУКЦІЯ.txt | README.txt install and run: Ukrainian for Ukraine, English for the rest (lang.py;
                                the mod is built with the same --lang, earthng/package/en/ has the
                                English start.ps1 and README.txt)
    python/                     Python 3.14 with numpy, shapely, pyproj, osmium, Pillow
    earthng/                    the companion (fast generator); needs nothing from Earth2Road
    mod/earthng_planet.zip    the BeamNG mod
    data/osm_tiles/<package>/   OSM source tiles;  data/dem/terrain/12/  Terrarium DEM
and <out>/EarthNG-<Area>-<date>.zip of that folder. The package is checked with its own
Python (imports, one tile built from its own data) before it is zipped.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import build_mod  # noqa: E402
import fast_dem  # noqa: E402
import lang  # noqa: E402

PYTHON = Path(sys.base_prefix)                      # the venv's base install (C:/Python314)
SITE = Path(sys.prefix) / 'Lib' / 'site-packages'   # the venv's packages
PACKAGES = ['PIL', 'numpy', 'numpy.libs', 'osmium', 'osmium.libs', 'pyproj', 'pyproj.libs', 'shapely', 'shapely.libs', 'certifi']
DIST = ['pillow', 'numpy', 'osmium', 'pyproj', 'shapely', 'certifi']
SKIP_LIB = {'test', 'idlelib', 'turtledemo', 'ensurepip', 'site-packages', '__pycache__', 'pydoc_data'}
AREAS = {'ukraine': 'Ukraine', 'kyiv': 'Kyiv', 'california': 'California',
         'united-kingdom': 'UK', 'new-york': 'NewYork', 'zanzibar': 'Zanzibar', 'romania': 'Romania',
         # the UK cut into regions (the whole country's archive is ~25 GB)
         'uk-london-south-east': 'UK-LondonSouthEast', 'uk-south-west': 'UK-SouthWest', 'uk-central-england': 'UK-CentralEngland',
         'uk-north-england': 'UK-NorthEngland', 'uk-wales': 'UK-Wales', 'uk-northern-ireland': 'UK-NorthernIreland',
         'uk-south-scotland': 'UK-SouthScotland', 'uk-scotland-east': 'UK-ScotlandEast', 'uk-scotland-west': 'UK-ScotlandWest'}
# the packaged-Python check builds one tile here (lat, lon); other packages: their first tile's centre
SMOKE = {'ukraine': (49.796, 30.115), 'kyiv': (50.4475, 30.5225),
         'california': (34.0505, -118.2475), 'united-kingdom': (51.5080, -0.1281),
         'new-york': (40.7580, -73.9855), 'zanzibar': (-6.1630, 39.1910), 'romania': (44.4355, 26.1025),
         'uk-london-south-east': (51.5080, -0.1281), 'uk-south-west': (51.4545, -2.5879), 'uk-central-england': (52.4796, -1.9026),
         'uk-north-england': (53.7965, -1.5478), 'uk-wales': (51.4816, -3.1791), 'uk-northern-ireland': (54.5973, -5.9301),
         'uk-south-scotland': (55.8609, -4.2514), 'uk-scotland-east': (57.1497, -2.0943), 'uk-scotland-west': (56.6826, -5.1023)}


def package_name(tiles):
    """EarthNG-<Area> from the tiles folder name (ukraine-260918 -> EarthNG-Ukraine)."""
    stem = tiles.name.rsplit('-', 1)[0]
    return 'EarthNG-' + AREAS.get(stem, stem)


def copy_python(dst):
    dst.mkdir(parents=True)
    for f in PYTHON.iterdir():
        if f.is_file() and f.suffix.lower() in ('.exe', '.dll') or f.name == 'LICENSE.txt':
            shutil.copy2(f, dst / f.name)
    shutil.copytree(PYTHON / 'DLLs', dst / 'DLLs')
    # Tcl/Tk for the launcher window (earthng/launcher.py), ~7 MB
    for name in ('tcl8', 'tcl8.6', 'tk8.6'):
        shutil.copytree(PYTHON / 'tcl' / name, dst / 'tcl' / name, ignore=shutil.ignore_patterns('demos', '*.lib'))
    (dst / 'Lib').mkdir()
    for f in (PYTHON / 'Lib').iterdir():
        if f.name in SKIP_LIB:
            continue
        if f.is_dir():
            shutil.copytree(f, dst / 'Lib' / f.name, ignore=shutil.ignore_patterns('__pycache__', 'test', 'tests'))
        else:
            shutil.copy2(f, dst / 'Lib' / f.name)
    site = dst / 'Lib' / 'site-packages'
    site.mkdir()
    for name in PACKAGES:
        shutil.copytree(SITE / name, site / name, ignore=shutil.ignore_patterns('__pycache__', 'tests'))
    for d in SITE.glob('*.dist-info'):
        if d.name.split('-')[0].lower() in DIST:
            shutil.copytree(d, site / d.name)


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--out', type=Path, default=ROOT / 'out' / 'package')
    p.add_argument('--tiles', type=Path, default=ROOT / 'data' / 'osm_tiles' / 'ukraine-260918')
    p.add_argument('--dem', type=Path, default=ROOT / 'out' / 'world' / 'cache')
    p.add_argument('--no-zip', action='store_true')
    a = p.parse_args()
    a.out, a.tiles, a.dem = a.out.resolve(), a.tiles.resolve(), a.dem.resolve()
    NAME = package_name(a.tiles)
    LANG = lang.of(a.tiles)
    pkg = a.out / NAME
    if pkg.exists():
        shutil.rmtree(pkg)
    pkg.mkdir(parents=True)
    print('python ...', flush=True)
    copy_python(pkg / 'python')
    print('companion and mod ...', flush=True)
    shutil.copytree(HERE, pkg / 'earthng', ignore=shutil.ignore_patterns('__pycache__', 'test_*.py', 'package.py', 'package'))
    (pkg / 'mod').mkdir()
    shutil.copy2(build_mod.build(a.out / 'mod-build' / NAME, LANG, legacy=True), pkg / 'mod' / build_mod.ZIP)
    print('data ...', flush=True)
    index = json.loads((a.tiles / 'index.json').read_text(encoding='utf8'))
    shutil.copytree(a.tiles, pkg / 'data' / 'osm_tiles' / a.tiles.name)
    dem = fast_dem.audit(index, a.dem)
    missing = [d for d in dem if not d.exists()]
    if missing:
        raise SystemExit(f'{len(missing)} DEM tiles missing: run earthng/fast_dem.py --download first')
    for d in dem:
        rel = d.relative_to(a.dem)
        (pkg / 'data' / 'dem' / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(d, pkg / 'data' / 'dem' / rel)
    # the art (photo facades and ground in BC1 DDS, texture packs): built here once, shipped
    # ready, so a tester's first landing does not spend ~40 s on it
    import fast_art
    shutil.copytree(fast_art.prebuild(), pkg / 'data' / 'art' / fast_art.VERSION)
    for name in ('EarthNG.bat', 'start.bat', 'start.sh'):
        shutil.copy2(HERE / 'package' / name, pkg / name)
    texts = HERE / 'package' / 'en' if LANG == 'en' else HERE / 'package'
    for name in ('start.ps1', 'README.txt' if LANG == 'en' else 'ІНСТРУКЦІЯ.txt'):
        shutil.copy2(texts / name, pkg / name)

    print('check with the packaged Python ...', flush=True)
    env = dict(os.environ, EARTH2ROAD=str(pkg / 'none'), PYTHONNOUSERSITE='1')
    env.pop('PYTHONPATH', None)
    check = ("import sys, json; sys.path.insert(0, 'earthng');"
             "import world_server, fast_server, fast, fast_art, fast_far, launcher; launcher.tk.Tcl();"
             "import paths; assert (paths.PROJECT / 'data' / 'art' / fast_art.VERSION / 'packs.json').exists(), 'art missing';"
             "import fast_sources as S;"
             "assert not any('Earth2Road' in (getattr(m,'__file__','') or '') for m in sys.modules.values()), 'Earth2Road imported';"
             "print('imports ok, python', sys.version.split()[0])")
    subprocess.run([str(pkg / 'python' / 'python.exe'), '-c', check], cwd=pkg, env=env, check=True)
    smoke = pkg / 'work-check'
    stem = a.tiles.name.rsplit('-', 1)[0]
    if stem in SMOKE:
        lat, lon = SMOKE[stem]
    else:
        w, s_, e, n = next(iter(index['tiles'].values()))['coverage']
        lat, lon = (s_ + n) / 2, (w + e) / 2
    subprocess.run([str(pkg / 'python' / 'python.exe'), 'earthng/fast.py', '--sources', f'data/osm_tiles/{a.tiles.name}',
                    '--dem', 'data/dem', '--out', str(smoke), '--lat', str(lat), '--lon', str(lon)],
                   cwd=pkg, env=env, check=True)
    shutil.rmtree(smoke)
    if a.no_zip:
        return
    out = a.out / f'{NAME}-{datetime.date.today():%Y%m%d}.zip'
    print(f'zip {out} ...', flush=True)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(pkg.rglob('*')):
            if f.is_file():
                stored = f.suffix.lower() in ('.pbf', '.png', '.zip')
                z.write(f, Path(NAME) / f.relative_to(pkg), zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
    print(f'{out}: {out.stat().st_size / 2**20:.0f} MB')


if __name__ == '__main__':
    main()
