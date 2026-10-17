"""Download a ready country pack, or build one from a Geofabrik extract.

    python earthng/country_fetch.py places --query berlin
    python earthng/country_fetch.py build --place ukraine --mods "%LOCALAPPDATA%/BeamNG/BeamNG.drive/current"
    python earthng/country_fetch.py install --zip out/native-packs/earthng_ukraine.zip --mods <profile>

Building downloads that extract's .osm.pbf, cuts tiles, fetches Terrarium heights and
writes earthng_<id>.zip. The look (asphalt, markings, verge, buildings, trees, statics)
and the layer switches are stored in the country manifest. Meshes stay in the planet mod.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import looks  # noqa: E402
import places  # noqa: E402

CATALOG = HERE / 'ready_packs.json'
USER_AGENT = places.USER_AGENT


def default_mods():
    base = Path(os.environ.get('LOCALAPPDATA', ''))
    for candidate in (base / 'BeamNG' / 'BeamNG.drive' / 'current', base / 'BeamNG.drive' / 'current'):
        if candidate.exists():
            return candidate
    return base / 'BeamNG' / 'BeamNG.drive' / 'current'


def catalog_entries():
    if not CATALOG.exists():
        return []
    payload = json.loads(CATALOG.read_text(encoding='utf-8'))
    entries = payload.get('packs') if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []
    out = []
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get('id'), str):
            url = entry.get('url') or ''
            out.append({'id': entry['id'], 'title': entry.get('title') or entry['id'],
                        'url': url if isinstance(url, str) and url.startswith('https://') else ''})
    return out


def local_folders(root=ROOT):
    return [Path(root) / 'out' / 'native-packs', Path(root) / 'out' / 'country-setup']


def find_zip(slug_id, folders):
    name = f'earthng_{slug_id}.zip'
    for folder in folders:
        path = Path(folder) / name
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def availability(slug_id, mods, root=ROOT):
    """installed | local | url | none, plus the path or url to use."""
    installed = Path(mods) / 'mods' / f'earthng_{slug_id}.zip'
    if installed.is_file() and installed.stat().st_size > 0:
        return {'state': 'installed', 'path': installed, 'url': ''}
    local = find_zip(slug_id, local_folders(root))
    if local is not None:
        return {'state': 'local', 'path': local, 'url': ''}
    for entry in catalog_entries():
        if entry['id'] == slug_id and entry['url']:
            return {'state': 'url', 'path': None, 'url': entry['url']}
    return {'state': 'none', 'path': None, 'url': ''}


def content_length(url, timeout=30):
    request = urllib.request.Request(url, method='HEAD', headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        length = response.headers.get('Content-Length')
    return int(length) if length and length.isdigit() else None


def download(url, dest, emit=print, cancel=None):
    """Resume a file into dest. Skips the transfer when dest already has the advertised size."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = content_length(url)
    if dest.exists() and total and dest.stat().st_size == total:
        emit(f'already have {dest.name} ({total} bytes)')
        return dest
    part = dest.with_suffix(dest.suffix + '.part')
    have = part.stat().st_size if part.exists() else 0
    headers = {'User-Agent': USER_AGENT}
    if have:
        headers['Range'] = f'bytes={have}-'
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        status = getattr(response, 'status', 200)
        if status == 200:
            have = 0
        elif status not in (200, 206):
            raise OSError(f'{url}: HTTP {status}')
        mode = 'ab' if status == 206 and have else 'wb'
        chunk = 1024 * 256
        last = time.monotonic()
        with open(part, mode) as handle:
            while True:
                if cancel is not None and cancel.is_set():
                    raise InterruptedError('download stopped')
                data = response.read(chunk)
                if not data:
                    break
                handle.write(data)
                have += len(data)
                now = time.monotonic()
                if now - last > 1.5:
                    if total:
                        emit(f'{dest.name}: {have / 2**20:.0f} / {total / 2**20:.0f} MB')
                    else:
                        emit(f'{dest.name}: {have / 2**20:.0f} MB')
                    last = now
    os.replace(part, dest)
    emit(f'downloaded {dest.name}')
    return dest


def install_zip(source, mods, slug_id):
    """Copy a country zip into the BeamNG mods folder."""
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    with zipfile.ZipFile(source) as archive:
        marker = f'earthng/countries/{slug_id}/manifest.json'
        if marker not in archive.namelist():
            raise ValueError(f'{source.name} does not contain {marker}')
    target_dir = Path(mods) / 'mods'
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f'earthng_{slug_id}.zip'
    temporary = target.with_suffix('.zip.part')
    shutil.copyfile(source, temporary)
    os.replace(temporary, target)
    return target


def tiles_dir(root, slug_id, box):
    name = f'{slug_id}-user'
    if box:
        digest = hashlib.sha1(','.join(f'{float(v):.5f}' for v in box).encode()).hexdigest()[:8]
        name = f'{name}-{digest}'
    return Path(root) / 'data' / 'osm_tiles' / name


def pbf_path(root, slug_id):
    return Path(root) / 'data' / 'pbf' / f'{slug_id}.osm.pbf'


def _python():
    """Interpreter that can import osmium. This checkout only."""
    try:
        import osmium  # noqa: F401
        return sys.executable
    except ImportError:
        pass
    if os.name == 'nt':
        candidates = [ROOT / 'python' / 'python.exe', ROOT / '.venv' / 'Scripts' / 'python.exe']
    else:
        candidates = [ROOT / '.venv-linux' / 'bin' / 'python', ROOT / '.venv' / 'bin' / 'python']
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError(
        'need a Python with osmium in this repository .venv (requirements-fast.txt)') from None


def build_place(place, mods, root=ROOT, look=None, include=None, box=None, jobs=None,
                landing=None, by_id=None, emit=print, cancel=None):
    """Download sources if needed and write the country zip, then copy it into mods."""
    import build_country
    import fast_dem

    slug_id = places.slug(place['id'])
    known = dict(by_id or {})
    known.setdefault(place['id'], place)
    look = looks.canonical(look or looks.recommended(place['id'], known))
    include = looks.canonical_include(include if include is not None else {})
    jobs = jobs or max(1, (os.cpu_count() or 2) - 1)
    if box is not None:
        if len(box) != 4 or not all(isinstance(v, (int, float)) for v in box):
            raise ValueError('the box is west, south, east, north in degrees')
        west, south, east, north = (float(v) for v in box)
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError('the box must stay on the map and run from west to east')
        box = [west, south, east, north]
    pbf = pbf_path(root, slug_id)
    emit(f"PBF {place['name']}: {place['pbf']}")
    download(place['pbf'], pbf, emit, cancel)
    if cancel is not None and cancel.is_set():
        raise InterruptedError('build stopped')
    tiles = tiles_dir(root, slug_id, box)
    if not (tiles / 'index.json').exists():
        emit(f'cutting tiles into {tiles.name}')
        command = [_python(), str(HERE / 'osm_tiles_country.py'), str(pbf), '--out', str(tiles),
                   '--jobs', str(min(jobs, 4))]
        if box:
            command += ['--package-box', *(str(v) for v in box)]
        subprocess.check_call(command, cwd=ROOT)
    index = json.loads((tiles / 'index.json').read_text(encoding='utf-8'))
    dem = Path(root) / 'out' / 'world' / 'cache'
    missing = [path for path in fast_dem.audit(index, dem) if not path.exists()]
    emit(f'DEM: {len(missing)} tiles')

    def one(path):
        if cancel is not None and cancel.is_set():
            return
        for attempt in range(6):
            try:
                fast_dem.fetch(path)
                return
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(2 + attempt * 2)
    if missing:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(one, missing))
    if cancel is not None and cancel.is_set():
        raise InterruptedError('build stopped')
    work = Path(root) / 'out' / 'world'
    emit('region index')
    regions = build_country.grid_regions(tiles, dem, work, emit=emit)
    out = Path(root) / 'out' / 'country-setup'
    emit(f"building earthng_{slug_id}.zip, block {look['block']}")
    archive = build_country.build_country(
        slug_id, place['name'], '0.4.0', regions, out, emit=emit, jobs=jobs,
        landing=landing, look=look, include=include)
    installed = install_zip(archive, mods, slug_id)
    emit(f'installed {installed}')
    return installed


def refresh_planet(mods, emit=print):
    """Replace Lua and materials.json inside an existing planet zip. Art files stay."""
    import fast_style

    zip_path = Path(mods) / 'mods' / 'earthng_planet.zip'
    if not zip_path.is_file():
        raise FileNotFoundError(f'missing {zip_path}. Build the planet first: earthng/build_mod.py')
    replacements = {}
    mod = ROOT / 'mod'
    for src in mod.rglob('*'):
        if src.is_file():
            replacements[src.relative_to(mod).as_posix()] = src.read_bytes()
    replacements['earthng/planet/materials.json'] = json.dumps(fast_style.MATERIALS).encode('utf-8')
    temporary = zip_path.with_suffix('.zip.tmp')
    replaced = 0
    with zipfile.ZipFile(zip_path) as source, zipfile.ZipFile(temporary, 'w') as dest:
        for info in source.infolist():
            data = replacements.pop(info.filename, None)
            if data is None:
                data = source.read(info.filename)
            else:
                replaced += 1
            stamp = info.date_time if info.date_time[0] >= 1980 else (2020, 1, 1, 0, 0, 0)
            copy = zipfile.ZipInfo(info.filename, date_time=stamp)
            copy.compress_type = info.compress_type
            copy.external_attr = info.external_attr
            dest.writestr(copy, data)
        for name, data in replacements.items():
            if name.startswith('lua/') or name == 'earthng/planet/materials.json':
                dest.writestr(name, data)
                replaced += 1
    os.replace(temporary, zip_path)
    emit(f'planet code updated ({replaced} files) in {zip_path.name}')
    return zip_path


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('command', choices=('places', 'build', 'install', 'refresh-planet'))
    parser.add_argument('--query', default='')
    parser.add_argument('--place', help='Geofabrik extract id, for example ukraine or berlin')
    parser.add_argument('--mods', type=Path, default=default_mods())
    parser.add_argument('--zip', type=Path)
    parser.add_argument('--box', type=float, nargs=4, metavar=('W', 'S', 'E', 'N'))
    parser.add_argument('--jobs', type=int, default=0)
    parser.add_argument('--block', default='')
    parser.add_argument('--landing', type=float, nargs=2, metavar=('LAT', 'LON'))
    parser.add_argument('--refresh-index', action='store_true')
    for slot in looks.SLOTS:
        parser.add_argument(f'--{slot}', default='')
    for key, _label, _default in looks.LAYERS:
        parser.add_argument(f'--no-{key}', dest=f'no_{key}', action='store_true')
    args = parser.parse_args()
    if args.command == 'places':
        found = places.search(places.load_index(ROOT, args.refresh_index), args.query)
        for place in found:
            print(f"{place['id']}\t{place['name']}\t{place['parent']}")
        return
    if args.command == 'refresh-planet':
        refresh_planet(args.mods)
        return
    if args.command == 'install':
        if not args.place or not args.zip:
            parser.error('install needs --place and --zip')
        print(install_zip(args.zip, args.mods, places.slug(args.place)))
        return
    if not args.place:
        parser.error('build needs --place')
    index = places.by_id(places.load_index(ROOT, args.refresh_index))
    place = index.get(args.place)
    if place is None:
        parser.error(f'unknown extract {args.place}')
    base = looks.recommended(place['id'], index)
    if args.block:
        block = looks.BLOCK_BY_ID.get(args.block)
        if block is None:
            parser.error(f'unknown asset block {args.block}')
        base = {'block': block['id'], **block['look']}
    for slot in looks.SLOTS:
        chosen = getattr(args, slot)
        if chosen:
            base[slot] = chosen
    include = {key: not getattr(args, f'no_{key}') for key, _label, _default in looks.LAYERS}
    built = build_place(place, args.mods, look=looks.canonical(base), include=include,
                        box=args.box, jobs=args.jobs or None, by_id=index,
                        landing=tuple(args.landing) if args.landing else None)
    print(built)


if __name__ == '__main__':
    main()
