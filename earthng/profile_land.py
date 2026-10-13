"""Profile one landing (build + BeamNG export) per window, roadgen v2 or legacy (plan phase 0).

    C:/js/Earth2Road/.venv/Scripts/python.exe earthng/profile_land.py --out out/profile-01
    C:/js/Earth2Road/.venv/Scripts/python.exe earthng/profile_land.py --out out/profile-02 --roadgen legacy --only khreshchatyk

Every window runs in a fresh process (a landing starts cold too): import the static OSM tile
into a new cache, build_world under cProfile, export_world under cProfile. Writes per window
stages.json (build stages from the generator's events, export stages from its
performance.json), build.pstats / export.pstats, top.txt (40 functions by cumulative and by
own time) and a summary table for all windows in summary.json.
"""
import argparse
import cProfile
import io
import json
import math
from pathlib import Path
import pstats
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402  (puts Earth2Road on sys.path)

WINDOWS = {'khreshchatyk': (50.4475, 30.5225), 'obolon': (50.5040, 30.4985), 'bilychi': (50.4643, 30.3456)}


def window(lat, lon, size):
    dl = size / 2 / (111320 * math.cos(math.radians(lat)))
    dt = size / 2 / 110540
    return [round(lon - dl, 6), round(lat - dt, 6), round(lon + dl, 6), round(lat + dt, 6)]


def top(prof, sort, n=40):
    out = io.StringIO()
    pstats.Stats(prof, stream=out).strip_dirs().sort_stats(sort).print_stats(n)
    return out.getvalue()


def run_one(name, out, roadgen, size, tiles, profile=True, direct=False):
    import osm_tiles
    from akadem_maps.data import data_action
    from akadem_maps.world import build_world
    from akadem_maps.adapters.beamng.export import export_world
    lat, lon = WINDOWS[name]
    bbox = window(lat, lon, size)
    index = json.loads((Path(tiles) / 'index.json').read_text(encoding='utf8'))
    key, tile = osm_tiles.tile_for(index, bbox)
    out.mkdir(parents=True)
    cache = out / 'cache'
    timings, stages = {}, []
    t = time.perf_counter()
    data_action('import', {'id': 'saved_area', 'name': name, 'bbox': bbox}, cache,
                pbf=str(Path(tiles) / tile['file']), bounds=tile['coverage'])
    timings['import'] = time.perf_counter() - t

    def emit(event, **ev):
        if event == 'stage':
            stages.append({'stage': ev.get('stage'), 't': time.perf_counter()})
    cfg = {'id': f'prof_{name}', 'name': name, 'bbox': bbox, 'region_profile': 'ukraine',
           'road_geometry': {'mode': roadgen}}
    if direct:
        import direct as direct_path
        prof = cProfile.Profile()
        if profile:
            prof.enable()
        t, cpu = time.perf_counter(), time.process_time()
        level, parts = direct_path.land(cfg, out, cache, emit)
        timings['direct'] = time.perf_counter() - t
        # CPU time of this process: far less sensitive to other programs than wall time
        timings['direct_cpu'] = time.process_time() - cpu
        prof.disable()
        if profile:
            prof.dump_stats(out / 'direct.pstats')
            (out / 'top.txt').write_text(top(prof, 'cumulative') + '\n' + top(prof, 'tottime'), encoding='utf8')
        marks = stages + [{'stage': None, 't': time.perf_counter()}]
        stage_time = {}
        for a, b in zip(marks, marks[1:]):
            stage_time[a['stage']] = round(stage_time.get(a['stage'], 0) + b['t'] - a['t'], 3)
        pieces = list((level / 'proc').glob('*.bin'))
        result = {'window': name, 'roadgen': roadgen, 'direct': True, 'bbox': bbox,
                  'timings': {**{k: round(v, 2) for k, v in timings.items()}, **parts},
                  'stages': stage_time, 'pieces': len(pieces),
                  'pieces_mb': round(sum(p.stat().st_size for p in pieces) / 2**20, 2)}
        (out / 'stages.json').write_text(json.dumps(result, indent=1), encoding='utf8')
        return result
    prof = cProfile.Profile()
    t = time.perf_counter()
    if profile:
        prof.enable()
    build_world(cfg, out / 'world', config_root=out, cache=cache, mode='auto', emit=emit)
    prof.disable()
    end = time.perf_counter()
    timings['build'] = end - t
    build_stages = {}
    marks = stages + [{'stage': None, 't': end}]
    for a, b in zip(marks, marks[1:]):
        build_stages[a['stage']] = round(build_stages.get(a['stage'], 0) + b['t'] - a['t'], 3)
    report = ''
    if profile:
        prof.dump_stats(out / 'build.pstats')
        report = top(prof, 'cumulative') + '\n' + top(prof, 'tottime')
    prof = cProfile.Profile()
    t = time.perf_counter()
    if profile:
        prof.enable()
    export_world(out / 'world', out / 'beamng', level_id=f'prof_{name}')
    prof.disable()
    timings['export'] = time.perf_counter() - t
    if profile:
        prof.dump_stats(out / 'export.pstats')
        report += '\n===== EXPORT =====\n' + top(prof, 'cumulative') + '\n' + top(prof, 'tottime')
    perf = json.loads((out / 'beamng/reports/performance.json').read_text(encoding='utf8'))
    result = {'window': name, 'roadgen': roadgen, 'bbox': bbox, 'source_tile': key,
              'timings': {k: round(v, 2) for k, v in timings.items()},
              'build_stages': build_stages,
              'export_stages': {k: round(v, 3) for k, v in perf['stage_seconds'].items()},
              'export_elapsed': round(perf['elapsed_seconds'], 2),
              'triangles': {k: v['triangles'] for k, v in perf['categories'].items()}}
    (out / 'stages.json').write_text(json.dumps(result, indent=1), encoding='utf8')
    (out / 'top.txt').write_text(report, encoding='utf8')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--roadgen', choices=('v2', 'legacy'), default='v2')
    p.add_argument('--size', type=float, default=500)
    p.add_argument('--only', choices=sorted(WINDOWS))
    p.add_argument('--tiles', default=str(paths.TILES))
    p.add_argument('--direct', action='store_true', help='earthng/direct.py instead of build + export')
    p.add_argument('--no-profile', action='store_true', help='plain wall-clock timings (cProfile slows Python code)')
    p.add_argument('--one', help=argparse.SUPPRESS)
    a = p.parse_args()
    if a.one:
        run_one(a.one, a.out / a.one, a.roadgen, a.size, a.tiles, not a.no_profile, a.direct)
        return
    if a.out.exists():
        raise SystemExit(f'{a.out} exists')
    a.out.mkdir(parents=True)
    summary = []
    for name in [a.only] if a.only else WINDOWS:
        t = time.perf_counter()
        code = subprocess.call([sys.executable, __file__, '--out', str(a.out), '--roadgen', a.roadgen,
                                '--size', str(a.size), '--tiles', a.tiles, '--one', name] + (['--no-profile'] if a.no_profile else []) + (['--direct'] if a.direct else []), cwd=paths.PROJECT)
        wall = round(time.perf_counter() - t, 1)
        stages = a.out / name / 'stages.json'
        row = json.loads(stages.read_text(encoding='utf8')) if stages.exists() else {'window': name, 'error': code}
        row['process_wall'] = wall
        summary.append(row)
        print(json.dumps({k: row.get(k) for k in ('window', 'timings', 'process_wall', 'error')}), flush=True)
    (a.out / 'summary.json').write_text(json.dumps(summary, indent=1), encoding='utf8')


if __name__ == '__main__':
    main()
