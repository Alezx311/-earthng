"""Build the tester package: everything needed to drive the fast generator over Kyiv oblast.

    C:/js/Earth2Road/.venv/Scripts/python.exe earthng/package.py --out out/package

<out>/EarthNG-KyivOblast/
    start.bat, start.ps1        install the mod, start the companion and the game
    ІНСТРУКЦІЯ.txt              install and run (Ukrainian)
    python/                     Python 3.14 with numpy, shapely, pyproj, osmium, Pillow
    earthng/                    the companion (fast generator); needs nothing from Earth2Road
    mod/earth2road_world.zip    the BeamNG mod
    data/osm_tiles/<package>/   OSM source tiles;  data/dem/terrain/12/  Terrarium DEM
and <out>/EarthNG-KyivOblast-<date>.zip of that folder. The package is checked with its own
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

PYTHON = Path(sys.base_prefix)                      # the venv's base install (C:/Python314)
SITE = Path(sys.prefix) / 'Lib' / 'site-packages'   # the venv's packages
PACKAGES = ['PIL', 'numpy', 'numpy.libs', 'osmium', 'osmium.libs', 'pyproj', 'pyproj.libs', 'shapely', 'shapely.libs', 'certifi']
DIST = ['pillow', 'numpy', 'osmium', 'pyproj', 'shapely', 'certifi']
SKIP_LIB = {'test', 'idlelib', 'tkinter', 'turtledemo', 'ensurepip', 'site-packages', '__pycache__', 'pydoc_data'}
NAME = 'EarthNG-KyivOblast'


def copy_python(dst):
    dst.mkdir(parents=True)
    for f in PYTHON.iterdir():
        if f.is_file() and f.suffix.lower() in ('.exe', '.dll') or f.name == 'LICENSE.txt':
            shutil.copy2(f, dst / f.name)
    shutil.copytree(PYTHON / 'DLLs', dst / 'DLLs', ignore=shutil.ignore_patterns('*tcl*', '*tk*', '_tkinter*'))
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
    p.add_argument('--tiles', type=Path, default=ROOT / 'data' / 'osm_tiles' / 'kyiv-oblast-260918')
    p.add_argument('--dem', type=Path, default=ROOT / 'out' / 'world' / 'cache')
    p.add_argument('--no-zip', action='store_true')
    a = p.parse_args()
    a.out, a.tiles, a.dem = a.out.resolve(), a.tiles.resolve(), a.dem.resolve()
    pkg = a.out / NAME
    if pkg.exists():
        shutil.rmtree(pkg)
    pkg.mkdir(parents=True)
    print('python ...', flush=True)
    copy_python(pkg / 'python')
    print('companion and mod ...', flush=True)
    shutil.copytree(HERE, pkg / 'earthng', ignore=shutil.ignore_patterns('__pycache__', 'test_*.py', 'package.py', 'package'))
    (pkg / 'mod').mkdir()
    shutil.copy2(build_mod.build(a.out / 'mod-build'), pkg / 'mod' / build_mod.ZIP)
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
    for name in ('start.ps1', 'start.bat', 'ІНСТРУКЦІЯ.txt'):
        shutil.copy2(HERE / 'package' / name, pkg / name)

    print('check with the packaged Python ...', flush=True)
    env = dict(os.environ, EARTH2ROAD=str(pkg / 'none'), PYTHONNOUSERSITE='1')
    env.pop('PYTHONPATH', None)
    check = ("import sys, json; sys.path.insert(0, 'earthng');"
             "import world_server, fast_server, fast, fast_art;"
             "import fast_sources as S;"
             "assert not any('Earth2Road' in (getattr(m,'__file__','') or '') for m in sys.modules.values()), 'Earth2Road imported';"
             "print('imports ok, python', sys.version.split()[0])")
    subprocess.run([str(pkg / 'python' / 'python.exe'), '-c', check], cwd=pkg, env=env, check=True)
    smoke = pkg / 'work-check'
    subprocess.run([str(pkg / 'python' / 'python.exe'), 'earthng/fast.py', '--sources', f'data/osm_tiles/{a.tiles.name}',
                    '--dem', 'data/dem', '--out', str(smoke), '--lat', '49.796', '--lon', '30.115'],
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
