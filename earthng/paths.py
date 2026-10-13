"""Where EarthNG and the Earth2Road generator live.

EarthNG uses Earth2Road (https://github.com/Alezx311/earth2road) as its world generator:
akadem_maps, its .venv and tools/beamng_bench.py. Set EARTH2ROAD to its checkout when it is
not at C:/js/Earth2Road.
"""
import os
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
EARTH2ROAD = Path(os.environ.get('EARTH2ROAD', 'C:/js/Earth2Road')).resolve()
E2R_PYTHON = EARTH2ROAD / '.venv/Scripts/python.exe'
E2R_CLI = EARTH2ROAD / '.venv/Scripts/earth2road.exe'
TILES = PROJECT / 'data/osm_tiles/kyiv-260918'
OUT = PROJECT / 'out'

for path in (EARTH2ROAD, EARTH2ROAD / 'tools'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
