"""Compile source features into an ordinary native BeamNG country ZIP.

    python earthng/build_country.py --id ua --title Ukraine --version 0.1.0 \
        --region out/work/fast-index/<id> --bbox -1500 -1500 1500 1500 --out out/mods

Repeat --region for independently centred source indexes. --bbox is an optional
local-metre export window; complete intersecting cells are exported. Without it,
all fully covered cells of each index are exported. To prepare an index first use
--sources <OSM package> --dem <DEM cache> --lat 50.45 --lon 30.52 --work out/author.

The ZIP contains road centre lines/profiles, OSM polygons/tags and elevation grids,
not finished meshes. BeamNG's Lua runtime creates geometry while playing. No
Python, SQLite, OSM PBF, launcher, server or generated mesh is shipped to players.
"""
import argparse
from datetime import date
import json
import math
import os
from pathlib import Path
import re
import tempfile
import zipfile

import numpy as np

import fast_sources as sources


FORMAT = 'earthng-features-v1'
CELL = 1000
HALO = 100
GRID = 25
ID = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,94}[a-z0-9])?\Z')
SOURCE_ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,95}\Z')


def _identifier(value, label):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError(f'{label} must use lowercase letters, digits or hyphens')
    return value


def _json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'cannot read JSON {path}: {exc}') from exc


def _finite(values, size, label):
    if not isinstance(values, (list, tuple)) or len(values) != size or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError(f'{label} must contain {size} finite numbers')
    return values


def _bounds(values, label):
    _finite(values, 4, label)
    if values[0] >= values[2] or values[1] >= values[3]:
        raise ValueError(f'{label} must have positive width and height')
    return values


def _encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(',', ':')).encode('utf8')


def _xyz(store, xy, height=None):
    xy = np.asarray(xy, dtype=float)[:, :2]
    z = store.terrain.sample(xy) if height is None else np.full(len(xy), height)
    result = np.column_stack([xy, z])
    if not np.isfinite(result).all():
        raise ValueError('source geometry contains non-finite coordinates or elevations')
    return result.tolist()


def _parts(geometry, kind):
    if geometry.geom_type == kind:
        yield geometry
    elif hasattr(geometry, 'geoms'):
        for part in geometry.geoms:
            yield from _parts(part, kind)


def _rings(store, polygon, height=None):
    # Preserve the full outer boundary and every hole. Lua performs triangulation.
    return [_xyz(store, ring.coords, height) for ring in [polygon.exterior, *polygon.interiors]]


def road_feature(fid, data):
    """Preserve every bend/profile knot, adding samples along long segments."""
    xy = np.asarray(data['xy'], dtype=float)[:, :2]
    profile = np.asarray(data['prof'], dtype=float)
    if len(xy) < 2 or profile.ndim != 2 or profile.shape[0] != 2 or profile.shape[1] < 2:
        raise ValueError(f'road {fid}: invalid centre line or height profile')
    if not np.isfinite(xy).all() or not np.isfinite(profile).all():
        raise ValueError(f'road {fid}: non-finite centre line or height profile')
    distance = np.r_[0., np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))]
    if distance[-1] <= 0 or np.any(np.diff(profile[0]) < 0):
        raise ValueError(f'road {fid}: invalid profile distances')
    knots = np.unique(np.r_[distance, profile[0], np.arange(0., distance[-1], 20.)])
    knots = knots[(knots >= 0.) & (knots <= distance[-1])]
    points = np.column_stack([np.interp(knots, distance, xy[:, 0]),
                              np.interp(knots, distance, xy[:, 1]),
                              np.interp(knots, profile[0], profile[1])]).tolist()
    width = float(data['width'])
    if not math.isfinite(width) or width <= 0:
        raise ValueError(f'road {fid}: invalid width')
    return {'id': str(fid), 'points': points, 'width': width,
            'tags': data['tags'], 'nodes': [str(n) for n in data['nodes']]}


def compile_cell(store, rect):
    """Source records with a halo; all XY and Z share the source index's frame."""
    padded = [rect[0]-HALO, rect[1]-HALO, rect[2]+HALO, rect[3]+HALO]
    cell = {name: [] for name in ('roads', 'junctions', 'buildings', 'areas', 'lines', 'points')}
    seen = set()
    for fid, kind, data, geometry in store.query(padded):
        identity = (kind, fid)
        if identity in seen:
            continue
        seen.add(identity)
        if kind == 'road':
            cell['roads'].append(road_feature(fid, data))
        elif kind == 'junction':
            # The source's solved node h includes the old surface lift. Lua adds
            # its own road lift, so export the same underlying road datum here.
            height = float(data['h']) - sources.ROAD_LIFT
            for n, part in enumerate(_parts(geometry, 'Polygon')):
                cell['junctions'].append({'id': f'{fid}:{n}',
                                          'rings': _rings(store, part, height), 'height': height})
    seen.clear()
    for fid, kind, data, geometry in store.scenery(padded):
        if (kind, fid) in seen:
            continue
        seen.add((kind, fid))
        tags = data.get('tags', {})
        for n, part in enumerate(_parts(geometry, 'Polygon')):
            feature = {'id': f'{fid}:{n}', 'kind': kind, 'rings': _rings(store, part), 'tags': tags}
            if kind == 'building':
                height = max(2.5, min(250., sources.number(tags.get('height'),
                             sources.number(tags.get('building:levels'), 3) * 3)))
                feature.update(height=height, minHeight=max(0., sources.number(tags.get('min_height'), 0.)))
                cell['buildings'].append(feature)
            else:
                cell['areas'].append(feature)
        for n, part in enumerate(_parts(geometry, 'LineString')):
            cell['lines'].append({'id': f'{fid}:{n}', 'kind': kind,
                                  'points': _xyz(store, part.coords), 'tags': tags})
        for n, part in enumerate(_parts(geometry, 'Point')):
            cell['points'].append({'id': f'{fid}:{n}', 'kind': kind,
                                   'pos': _xyz(store, [[part.x, part.y]])[0], 'tags': tags})
    for features in cell.values():
        features.sort(key=lambda feature: feature['id'])
    xs = np.arange(rect[0], rect[2]+GRID, GRID, dtype=float)
    ys = np.arange(rect[1], rect[3]+GRID, GRID, dtype=float)
    positions = np.array([(x, y) for y in ys for x in xs])
    heights = np.asarray(store.terrain.sample(positions), dtype=float)
    if not np.isfinite(heights).all():
        raise ValueError('non-finite terrain grid')
    cell['terrain'] = {'rect': list(rect), 'nx': len(xs), 'ny': len(ys), 'heights': heights.tolist()}
    return cell


def _source_meta(directory):
    meta = _json(Path(directory) / 'region.json')
    raw_id = meta.get('id')
    if not isinstance(raw_id, str) or not SOURCE_ID.fullmatch(raw_id):
        raise ValueError('source region id must use lowercase letters, digits, underscores or hyphens')
    if meta.get('generator') != sources.VERSION:
        raise ValueError(f'{raw_id}: unsupported source index; expected {sources.VERSION}')
    _bounds(meta.get('bbox'), f'{raw_id}: source bbox')
    spec = meta.get('frame')
    if not isinstance(spec, dict) or not isinstance(spec.get('crs'), str):
        raise ValueError(f'{raw_id}: source index needs its geographic frame')
    _finite(spec.get('origin'), 2, f'{raw_id}: frame origin')
    return meta


def _local_bbox(frame, bbox):
    x, y = frame.local([bbox[0], bbox[0], bbox[2], bbox[2]], [bbox[1], bbox[3], bbox[1], bbox[3]])
    return [float(min(x)), float(min(y)), float(max(x)), float(max(y))]


def _provenance(meta, index):
    """Keep the source snapshot identity and attribution, without authoring paths."""
    original = index.get('source') or meta.get('source') or meta['id']
    basename = str(original).replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]
    source_id = re.sub(r'(?i)(?:\.osm)?\.pbf$', '', basename)
    result = {'source': 'OpenStreetMap contributors', 'license': 'ODbL-1.0', 'sourceId': source_id}
    match = re.search(r'(?:^|-)(\d{2})(\d{2})(\d{2})$', source_id)
    if match:
        try:
            result['sourceDate'] = date(2000 + int(match[1]), int(match[2]), int(match[3])).isoformat()
        except ValueError:
            pass  # A package name is evidence of a date only when it is valid.
    return result


def _write_json(archive, path, value):
    # Stable member timestamps make the same sources produce the same archive.
    info = zipfile.ZipInfo(path, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, _encode(value))


def _export_region(archive, country_id, directory, bbox, emit):
    meta = _source_meta(directory)
    region_id = meta['id'].replace('_', '-')
    _identifier(region_id, 'region id')
    store = sources.Store(directory)
    try:
        provenance = _provenance(meta, getattr(store, 'source_index', {}))
        frame = store.frame
        local = list(bbox) if bbox is not None else _local_bbox(frame, meta['bbox'])
        _bounds(local, 'local export bbox')
        base = f'earthng/countries/{country_id}/regions/{region_id}'
        cells, coverage = {}, []
        for j in range(math.floor(local[1]/CELL), math.ceil(local[3]/CELL)):
            for i in range(math.floor(local[0]/CELL), math.ceil(local[2]/CELL)):
                key = f'{i}_{j}'
                rect = [i*CELL, j*CELL, (i+1)*CELL, (j+1)*CELL]
                if not store.covered(rect):
                    continue
                emit(f'export {region_id} cell {key}')
                content = compile_cell(store, rect)
                path = f'{base}/cells/{key}.json'
                _write_json(archive, path, content)
                cells[key] = {'path': '/' + path, 'bounds': rect}
                coverage.append(frame.geo_bbox(rect))
        if not cells:
            raise ValueError(f'{region_id}: export contains no fully covered source cells')
        lon, lat = frame.geographic([0., 1., 0.], [0., 0., 1.])
        geo = [0., 0., float(lon[0]), float(lat[0]), float(lon[1]-lon[0]),
               float(lon[2]-lon[0]), float(lat[1]-lat[0]), float(lat[2]-lat[0])]
        _finite(geo, 8, 'geographic transform')
        bounds = [min(b[0] for b in coverage), min(b[1] for b in coverage),
                  max(b[2] for b in coverage), max(b[3] for b in coverage)]
        manifest = {'schemaVersion': 1, 'id': region_id, 'runtime': 'lua', 'sourceFormat': FORMAT,
                    'tileSize': sources.TILE, 'cellSize': CELL, 'frame': frame.spec(), 'geo': geo,
                    'bounds': bounds, 'coverage': coverage, 'cells': cells, **provenance}
        path = f'{base}/manifest.json'
        _write_json(archive, path, manifest)
        return {'id': region_id, 'manifest': '/' + path, 'coverage': coverage, **provenance}
    finally:
        store.db.close()


def build_country(country_id, title, version, regions, out, bbox=None, emit=print):
    """Stream source cells to ZIP64 and atomically replace the completed archive."""
    _identifier(country_id, 'country id')
    if not isinstance(title, str) or not title.strip() or not isinstance(version, str) or not version.strip():
        raise ValueError('country title and version must be nonempty strings')
    if not regions:
        raise ValueError('at least one --region or --sources package is required')
    if bbox is not None:
        _bounds(bbox, 'local export bbox')
    identifiers = [_source_meta(directory)['id'].replace('_', '-') for directory in regions]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError('duplicate region id after slug normalization')
    output = Path(out)
    output.mkdir(parents=True, exist_ok=True)
    target = output / f'earthng_{country_id}.zip'
    handle, temporary = tempfile.mkstemp(prefix=target.stem + '-', suffix='.zip.tmp', dir=output)
    os.close(handle)
    try:
        with zipfile.ZipFile(temporary, 'w', allowZip64=True) as archive:
            entries = [_export_region(archive, country_id, directory, bbox, emit) for directory in regions]
            provenance = {'source': 'OpenStreetMap contributors', 'license': 'ODbL-1.0'}
            snapshots = {entry['sourceId']: {key: entry[key] for key in ('sourceId', 'sourceDate') if key in entry}
                         for entry in entries}
            if len(snapshots) == 1:
                provenance.update(next(iter(snapshots.values())))
            else:
                provenance['sources'] = [snapshots[key] for key in sorted(snapshots)]
            _write_json(archive, f'earthng/countries/{country_id}/manifest.json', {
                'schemaVersion': 1, 'kind': 'earthng-country', 'id': country_id,
                'title': title.strip(), 'version': version.strip(), 'runtime': 'native',
                'sourceFormat': FORMAT, 'coverage': [b for r in entries for b in r['coverage']],
                'regions': entries, **provenance})
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--id', required=True)
    parser.add_argument('--title', required=True)
    parser.add_argument('--version', default='0.1.0')
    parser.add_argument('--region', action='append', default=[], help='prepared source-index directory')
    parser.add_argument('--out', required=True)
    parser.add_argument('--bbox', type=float, nargs=4, metavar=('X0', 'Y0', 'X1', 'Y1'))
    parser.add_argument('--sources', help='OSM source package to prepare before exporting')
    parser.add_argument('--dem', help='offline DEM cache for --sources')
    parser.add_argument('--work', help='authoring cache for --sources')
    parser.add_argument('--lat', type=float)
    parser.add_argument('--lon', type=float)
    args = parser.parse_args()
    try:
        if args.sources:
            if any(getattr(args, name) is None for name in ('dem', 'work', 'lat', 'lon')):
                parser.error('--sources requires --dem, --work, --lat and --lon')
            directory, _ = sources.prepare(args.sources, Path(args.work)/'fast-index', args.dem,
                                           point=(args.lon, args.lat))
            args.region.append(directory)
        result = build_country(args.id, args.title, args.version, args.region, args.out, args.bbox)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(result)


if __name__ == '__main__':
    main()
