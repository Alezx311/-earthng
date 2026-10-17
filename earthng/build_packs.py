"""Build every public native country pack in one resumable queue (author tool).

    C:/js/Earth2Road/.venv/Scripts/python.exe earthng/build_packs.py --out out/native-packs [--only id ...]

A pack is a whole country or state: the OSM tiles of a Geofabrik extract (cut once by
osm_tiles_country.py into data/osm_tiles/<name>), the Terrarium DEM under them (out/world/cache)
and build_country's grid of regions. A preparing thread downloads DEM and cuts tiles a pack or
two ahead; the main thread builds one ZIP at a time (all cores). A finished ZIP, cut tiles and
cached indexes are skipped on a rerun. Logs: <out>/<id>.log, state: <out>/status.json.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import build_country  # noqa: E402
import fast_dem  # noqa: E402

PBF = Path('C:/js/Earth2Road/out/districts/.inputs/geofabrik')
TILES = ROOT / 'data/osm_tiles'
DEM = ROOT / 'out/world/cache'
WORK = ROOT / 'out/world'
NEW = '261016'


def pack(pid, title, tiles, landing, pbf=None, box=None, block=1.5):
    return {'id': pid, 'title': title, 'tiles': tiles, 'landing': landing, 'pbf': pbf, 'box': box, 'block': block}


def new(pid, title, name, landing, box=None, block=1.5):
    return pack(pid, title, f'{name}-{NEW}', landing, f'{name}-{NEW}.osm.pbf', box, block)


UK = [('uk-london-south-east', 'London & South East England', (51.5080, -0.1281)),
      ('uk-south-west', 'South West England', (51.4545, -2.5879)),
      ('uk-central-england', 'Central England', (52.4796, -1.9026)),
      ('uk-north-england', 'North England', (53.7965, -1.5478)),
      ('uk-wales', 'Wales', (51.4816, -3.1791)),
      ('uk-northern-ireland', 'Northern Ireland', (54.5973, -5.9301)),
      ('uk-south-scotland', 'South Scotland', (55.8609, -4.2514)),
      ('uk-scotland-east', 'Scotland East', (57.1497, -2.0943)),
      ('uk-scotland-west', 'Scotland West', (56.6826, -5.1023))]
GERMANY = [('berlin', 'Berlin', (52.5163, 13.3777)), ('hamburg', 'Hamburg', (53.5503, 9.9930)),
           ('bremen', 'Bremen', (53.0758, 8.8072)), ('saarland', 'Saarland', (49.2340, 6.9950)),
           ('schleswig-holstein', 'Schleswig-Holstein', (54.3233, 10.1228)),
           ('mecklenburg-vorpommern', 'Mecklenburg-Vorpommern', (53.6355, 11.4012)),
           ('thueringen', 'Thuringia', (50.9781, 11.0290)), ('sachsen-anhalt', 'Saxony-Anhalt', (52.1270, 11.6290)),
           ('brandenburg', 'Brandenburg', (52.3989, 13.0657)), ('rheinland-pfalz', 'Rhineland-Palatinate', (49.9990, 8.2730)),
           ('sachsen', 'Saxony', (51.0504, 13.7373)), ('hessen', 'Hesse', (50.1109, 8.6821)),
           ('niedersachsen', 'Lower Saxony', (52.3759, 9.7320)),
           ('baden-wuerttemberg', 'Baden-Wurttemberg', (48.7758, 9.1829)),
           ('nordrhein-westfalen', 'North Rhine-Westphalia', (50.9375, 6.9603)), ('bayern', 'Bavaria', (48.1374, 11.5755))]

PACKS = [
    pack('zanzibar', 'Zanzibar', 'zanzibar-261013', (-6.1630, 39.1910)),
    new('moldova', 'Moldova', 'moldova', (47.0245, 28.8323)),
    # Vatican City is 0.44 km2; a landing needs 3 x 3 km of sources, so central Rome comes with it
    new('vatican', 'Vatican City & Rome', 'italy-centro', (41.9022, 12.4572), box=[12.35, 41.80, 12.62, 41.99], block=0.5),
    new('slovakia', 'Slovakia', 'slovakia', (48.1486, 17.1077)),
    new('serbia', 'Serbia', 'serbia', (44.8125, 20.4612)),
    new('netherlands', 'Netherlands', 'netherlands', (52.3730, 4.8924)),
    pack('ukraine', 'Україна', 'ukraine-260918', (50.4501, 30.5234)),
    pack('romania', 'Romania', 'romania-261014', (44.4355, 26.1025)),
    pack('new-york', 'New York', 'new-york-261013', (40.7580, -73.9855)),
    new('nevada', 'Nevada', 'nevada', (36.1147, -115.1728)),
    new('north-carolina', 'North Carolina', 'north-carolina', (35.2271, -80.8431)),
    new('washington', 'Washington', 'washington', (47.6062, -122.3321)),
    new('michigan', 'Michigan', 'michigan', (42.3314, -83.0458)),
    *[pack(pid, title, f'{pid}-261013', at) for pid, title, at in UK],
    new('philippines', 'Philippines', 'philippines', (14.5995, 120.9842)),
    pack('california', 'California', 'california-261013', (34.0505, -118.2475)),
    new('spain', 'Spain', 'spain', (40.4168, -3.7038)),
    *[new(f'germany-{land}', f'Germany: {title}', f'de-{land}', at) for land, title, at in GERMANY],
]


class Log:
    def __init__(self, out):
        self.out, self.lock, self.status = out, threading.Lock(), {}
        path = out / 'status.json'
        if path.exists():
            self.status = json.loads(path.read_text(encoding='utf8'))

    def say(self, pid, text):
        line = f"{time.strftime('%H:%M:%S')} {text}"
        with self.lock, open(self.out / f'{pid}.log', 'a', encoding='utf8') as f:
            f.write(line + '\n')
        print(f'[{pid}] {line}', flush=True)

    def set(self, pid, **values):
        with self.lock:
            self.status.setdefault(pid, {}).update(values, updated=time.strftime('%Y-%m-%d %H:%M:%S'))
            tmp = self.out / 'status.json.tmp'
            tmp.write_text(json.dumps(self.status, indent=1, ensure_ascii=False), encoding='utf8')
            tmp.replace(self.out / 'status.json')


def fetch_dem(index, log, pid):
    missing = [p for p in fast_dem.audit(index, DEM) if not p.exists()]
    log.say(pid, f'DEM: {len(missing)} tiles to download')

    def one(path):
        for attempt in range(6):
            try:
                return fast_dem.fetch(path)
            except Exception as exc:  # network hiccups: retry, a hole would drop cells
                if attempt == 5:
                    log.say(pid, f'DEM failed {path}: {exc}')
                time.sleep(2 + attempt * 3)
    with ThreadPoolExecutor(12) as pool:
        list(pool.map(one, missing))
    left = sum(1 for p in missing if not p.exists())
    log.say(pid, f'DEM ready, {left} missing')


def prepare(p, log, cut_jobs):
    """Tiles and DEM of a pack. Returns the tiles directory, or None after an error."""
    pid, tiles = p['id'], TILES / p['tiles']
    if not (tiles / 'index.json').exists():
        source = PBF / p['pbf']
        while not source.exists():                      # build_packs runs beside the downloader
            log.set(pid, stage='waiting for pbf')
            time.sleep(30)
        log.set(pid, stage='cutting tiles')
        log.say(pid, f'cut {source.name}')
        cmd = [sys.executable, str(HERE / 'osm_tiles_country.py'), str(source), '--out', str(tiles),
               '--jobs', str(cut_jobs), '--block', str(p['block'])]
        if p['box']:
            cmd += ['--package-box', *map(str, p['box'])]
        with open(log.out / f'{pid}.log', 'a', encoding='utf8') as f:
            code = subprocess.call(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=ROOT)
        if code:
            log.say(pid, f'cut failed ({code})')
            log.set(pid, stage='failed', error=f'cut exit {code}')
            return None
    log.set(pid, stage='DEM')
    fetch_dem(json.loads((tiles / 'index.json').read_bytes()), log, pid)
    log.set(pid, stage='prepared')
    return tiles


def check(zip_path):
    """Manifests read, each region has a landing, block masks match the .bin members."""
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        country = [n for n in names if n.count('/') == 3 and n.endswith('/manifest.json')]
        assert len(country) == 1, country
        top = json.loads(z.read(country[0]))
        cells = 0
        for region in top['regions']:
            m = json.loads(z.read(region['manifest'].lstrip('/')))
            assert m['blocks'] and 'landing' in m, region['id']
            for block in m['blocks'].values():
                assert block['path'].lstrip('/') in names, block['path']
            cells += m['cellCount']
    return {'regions': len(top['regions']), 'cells': cells, 'landing': top.get('landing')}


def build(p, tiles, log, out, jobs):
    pid = p['id']
    log.set(pid, stage='building')
    started = time.monotonic()
    emit = lambda text: log.say(pid, text)  # noqa: E731
    regions = build_country.grid_regions(tiles, DEM, WORK, emit=emit)
    target = build_country.build_country(pid, p['title'], '0.3.0', regions, out, emit=emit, jobs=jobs,
                                         landing=p['landing'])
    info = check(target)
    size = target.stat().st_size
    minutes = (time.monotonic() - started) / 60
    log.say(pid, f'done {target.name}: {size/2**30:.2f} GB, {info} in {minutes:.0f} min')
    log.set(pid, stage='done', zip=str(target), bytes=size, minutes=round(minutes, 1), **info)


def main():
    a = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    a.add_argument('--out', type=Path, default=ROOT / 'out/native-packs')
    a.add_argument('--only', nargs='*', help='pack ids (default: all, in queue order)')
    a.add_argument('--jobs', type=int, default=13)
    a.add_argument('--cut-jobs', type=int, default=4)
    a.add_argument('--ahead', type=int, default=2, help='packs prepared ahead of the build')
    a.add_argument('--later', nargs='*', default=[], help='pack ids moved to the end of the queue')
    a.add_argument('--wait-log', type=Path, help='start building once this log has an "exit" line')
    args = a.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    log = Log(args.out)
    todo = [p for p in PACKS if not args.only or p['id'] in args.only]
    todo = [p for p in todo if p['id'] not in args.later] + [p for p in todo if p['id'] in args.later]
    todo = [p for p in todo if not (args.out / f"earthng_{p['id']}.zip").exists() or log.say(p['id'], 'zip exists, skip')]
    ready = queue.Queue(args.ahead)

    def preparer():
        for p in todo:
            try:
                ready.put((p, prepare(p, log, args.cut_jobs)))
            except Exception as exc:
                log.say(p['id'], f'prepare error: {exc!r}')
                log.set(p['id'], stage='failed', error=repr(exc))
                ready.put((p, None))
        ready.put(None)
    threading.Thread(target=preparer, daemon=True).start()
    while args.wait_log and 'exit ' not in (args.wait_log.read_text(encoding='utf8', errors='replace')
                                            if args.wait_log.exists() else ''):
        time.sleep(30)
    while (item := ready.get()) is not None:
        p, tiles = item
        if tiles is None:
            continue
        if (args.out / f"earthng_{p['id']}.zip").exists():   # built meanwhile by another run
            log.say(p['id'], 'zip exists, skip')
            continue
        try:
            build(p, tiles, log, args.out, args.jobs)
        except Exception as exc:
            log.say(p['id'], f'build error: {exc!r}')
            log.set(p['id'], stage='failed', error=repr(exc))


if __name__ == '__main__':
    main()
