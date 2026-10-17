"""End-to-end test of the planet mod in an isolated BeamNG user folder.

    C:/js/Earth2Road/.venv/Scripts/python.exe earthng/test_world.py --out out/world-test-01 --lat 50.4475 --lon 30.5225

Builds the mod into <out>/user/current/mods, starts world_server.py for that user folder and
BeamNG.drive with the level e2r_world. e2r-world-test.json makes the extension open the picker,
pick the point, land, drive (straight for --drive-seconds, or along --route with the extension's
autopilot while tiles stream in) and quit; screenshots e2r-*.png and e2r-world-result.json are
copied to <out>. The user's own BeamNG profile is not touched.

    ... test_world.py --out out/world-test-route-01 --lat 50.5070 --lon 30.4990 --route out/region/kyiv-ns/route.json --drive-seconds 2400
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402  (puts Earth2Road and its tools/ on sys.path)
from beamng_bench import EXE, log_metrics, memory  # noqa: E402
import build_mod  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--lat', type=float, help='landing point; with --replay the route start by default')
    p.add_argument('--lon', type=float)
    p.add_argument('--tiles', default=str(paths.TILES))
    p.add_argument('--timeout', type=int, default=420)
    p.add_argument('--route', type=Path, help='route.json (earthng/route.py) for the autopilot')
    p.add_argument('--speed', type=float, default=13.0, help='autopilot cruise speed, m/s')
    p.add_argument('--drive-seconds', type=float, default=8)
    p.add_argument('--packs', action='store_true', help='after the drive: an overview shot in every texture pack')
    p.add_argument('--gallery', action='store_true', help='after the drive: fixed views round the car (e2r-g*.png)')
    p.add_argument('--pack', help='texture pack to land and drive in (default real)')
    p.add_argument('--pack-gallery', help='comma list of packs: the gallery once in each (implies --gallery)')
    p.add_argument('--gallery-wait', type=float, default=1, help='seconds before the gallery (far tiles keep loading)')
    p.add_argument('--view', action='append', default=[], help='extra gallery view name:x,y,z:tx,ty,tz (world metres)')
    p.add_argument('--manual', action='store_true', help='land, then hand the car to the player; runs until the game is closed')
    p.add_argument('--min-distance', type=float, default=20, help='physics test fails below this, m')
    p.add_argument('--workers', type=int, default=8)
    p.add_argument('--generator', choices=('v2','fast'), default='v2')
    p.add_argument('--native', action='store_true', help='only BeamNG + country ZIPs; never start the companion')
    p.add_argument('--picker-only', action='store_true', help='verify native picker without landing')
    p.add_argument('--expected-countries', type=int, default=0, help='country count for --picker-only')
    p.add_argument('--picker-zoom', type=int, help='--picker-only: centre the map on --lat/--lon at this zoom')
    p.add_argument('--picker-hold', type=float, default=4, help='--picker-only: seconds the picker stays open')
    p.add_argument('--replay', action='store_true', help='camera/pose replay at constant speed; not a physics test')
    p.add_argument('--index-cache', type=Path, help='reuse a source index; report is NOT a cold-start test')
    p.add_argument('--traffic', type=int, help="after landing: the game's AI traffic, this many cars")
    p.add_argument('--rain', type=int, help="after landing: the game's own rain (core_environment numOfDrops)")
    p.add_argument('--mod', action='append', default=[], type=Path, help='another mod ZIP for the test profile (e.g. Agent Traffic)')
    p.add_argument('--stream-cache', type=Path, help='copy an earlier e2r_stream folder in (warm tiles)')
    p.add_argument('--hop', action='append', default=[], metavar='LAT,LON[,STAGE]',
                   help='after landing, teleport here (from driving, or STAGE spawning/warmup mid-descent); repeatable')
    a = p.parse_args()
    route_frame = None
    native_route = a.native and a.route and 'geo' in json.loads(a.route.read_text(encoding='utf8'))
    if native_route and a.lat is None:
        # a native route is lat/lon ({"geo": [[lat, lon], ...]}); the game projects it itself
        a.lat, a.lon = json.loads(a.route.read_text(encoding='utf8'))['geo'][0]
    if a.route and a.generator == 'fast' and not native_route:
        # a fast route is in the frame it was recorded in (route['frame']; before fast-9 the
        # Kyiv one); the stream is in the landing's frame
        import fast_sources
        route = json.loads(a.route.read_text(encoding='utf8'))
        spec = route.get('frame')
        route_frame = fast_sources.Frame.of({'frame': spec if isinstance(spec, dict) else None})
        if a.replay and a.lat is None:
            lon, lat = route_frame.geographic(*route['points'][0][:2])     # land where it starts
            a.lat, a.lon = float(lat), float(lon)
    if a.picker_only:
        a.lat, a.lon = a.lat or 0, a.lon or 0
    if a.lat is None or a.lon is None:
        p.error('--lat and --lon are required')
    out = a.out.resolve()
    if out.exists():
        raise SystemExit(f'{out} exists')
    if route_frame:
        import numpy as np
        land = fast_sources.Frame.of(fast_sources.source_identity(a.tiles, paths.OUT / 'world' / 'cache', (a.lon, a.lat))[1])
        pts = np.asarray(route['points'], dtype=float)
        lon, lat = route_frame.geographic(pts[:, 0], pts[:, 1])
        pts[:, 0], pts[:, 1] = land.local(lon, lat)
        out.mkdir(parents=True)
        a.route = out / 'route-landing.json'
        a.route.write_text(json.dumps(dict(route, points=pts.round(3).tolist(), frame=land.spec())), encoding='utf8')
    user = out / 'user'
    profile = user / 'current'
    (profile / 'mods').mkdir(parents=True)
    shutil.copy2(build_mod.build(out / 'mod', legacy=not a.native), profile / 'mods' / build_mod.ZIP)
    for extra in a.mod:
        try:
            # big country packs: a hard link on the same drive, not gigabytes of copies
            os.link(extra, profile / 'mods' / extra.name)
        except OSError:
            shutil.copy2(extra, profile / 'mods' / extra.name)
    (profile / 'settings').mkdir()
    (profile / 'settings/settings.json').write_text(json.dumps({
        'GraphicDisplayResolutions': '1280 720', 'GraphicFullscreen': False, 'GraphicOverallQuality': 'Normal',
        'AudioMasterVol': 0, 'fpsLimitBackgroundEnabled': False, 'onlineFeatures': 'disable', 'telemetry': 'disable', 'PostFXMotionBlurEnabled': False,
        # the first-run "Input Changes" page pauses the simulation (fast-game-02 never moved under it)
        'showedInputLayoutPopupV37': True}), encoding='utf8')
    test = {'lat': a.lat, 'lon': a.lon, 'driveSeconds': a.drive_seconds, 'skipMap': True, 'minDistance': a.min_distance}
    if a.picker_only:
        test.update(pickerOnly=True, expectedCountries=a.expected_countries, pickerHold=a.picker_hold)
        if a.picker_zoom:
            test['pickerZoom'] = a.picker_zoom
    if a.hop:
        hops = []
        for h in a.hop:
            parts = h.split(',')
            hops.append([float(parts[0]), float(parts[1])] + ([parts[2]] if len(parts) > 2 else []))
        test['hops'] = hops
    if a.route:
        shutil.copy2(a.route, profile / 'e2r-route.json')
        test.update(route='/e2r-route.json', speed=a.speed)
    if a.replay:
        if not a.route or (a.generator != 'fast' and not native_route):
            p.error('--replay requires --route and --generator fast (or a native geo route)')
        test['replay'] = True
    if a.packs:
        test['packs'] = True
    if a.pack:
        test['pack'] = a.pack
    if a.pack_gallery:
        a.gallery = True
        test['packGallery'] = [s for s in a.pack_gallery.split(',') if s]
    if a.gallery:
        test['gallery'] = True
        test['galleryWait'] = a.gallery_wait
        test['views'] = [{'name': v.split(':')[0], 'cam': [float(c) for c in v.split(':')[1].split(',')],
                          'at': [float(c) for c in v.split(':')[2].split(',')]} for v in a.view]
    if a.traffic:
        test['traffic'] = a.traffic
    if a.rain:
        test['rain'] = a.rain
    if a.manual:
        test['manual'] = True
        a.timeout = 10**9
    if a.index_cache:
        shutil.copytree(a.index_cache, out / 'work/fast-index')
    if a.stream_cache:
        shutil.copytree(a.stream_cache, profile / 'e2r_stream')
    (profile / 'e2r-world-test.json').write_text(json.dumps(test), encoding='utf8')
    if a.native:
        test['skipMap'] = True  # native picker has an offline vector map instead of downloaded textures
        test_file = profile / 'e2r-world-test.json'
        test_file.write_text(json.dumps(test), encoding='utf8')
    server = None if a.native else subprocess.Popen([sys.executable, str(HERE / 'world_server.py'), '--tiles', a.tiles,
                               '--userfolder', str(profile), '--work', str(out / 'work'),
                               '--workers', str(a.workers), '--generator', a.generator],
                              cwd=paths.PROJECT, stdout=open(out / 'server.log', 'w'), stderr=subprocess.STDOUT)
    started = time.perf_counter()
    game = subprocess.Popen([str(EXE), '-userpath', str(user), '-level', build_mod.LEVEL, '-nosteam'], cwd=str(EXE.parent))
    peak = 0
    try:
        test_file = profile / 'e2r-world-test.json'
        res_path = profile / 'e2r-world-result.json'
        finished = None
        while game.poll() is None and time.perf_counter() - started < a.timeout:
            # the test ends with the game's shutdown(), which sometimes leaves the game open
            # (native-final-kyiv, native-hops-03): a final status 15 s old ends it from here
            try:
                status = json.loads(res_path.read_text(encoding='utf8')).get('status') if res_path.exists() else None
            except (OSError, ValueError):
                status = None
            if status in ('passed', 'failed'):
                finished = finished or time.perf_counter()
                if time.perf_counter() - finished > 15:
                    break
            else:
                finished = None
            # the game's first start in a new profile removes unknown files; keep putting it back
            if not test_file.exists() and not res_path.exists():
                test_file.write_text(json.dumps(test), encoding='utf8')
                if a.route and not (profile / 'e2r-route.json').exists():
                    shutil.copy2(a.route, profile / 'e2r-route.json')
            m = memory(game.pid)
            if m:
                peak = max(peak, m[0])
            time.sleep(0.5)
        if game.poll() is None:
            game.kill()
    finally:
        # the companion's tile builders are child processes: end the whole tree
        if server:
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(server.pid)], capture_output=True)
    res_path = profile / 'e2r-world-result.json'
    result = json.loads(res_path.read_text(encoding='utf8')) if res_path.exists() else {'status': 'no_result'}
    for png in sorted((profile / 'screenshots').rglob('e2r-*.png')):
        shutil.copy2(png, out / png.name)
    if (profile / 'beamng.log').exists():
        shutil.copy2(profile / 'beamng.log', out / 'beamng.log')
    report = {'status': result.get('status'), 'exit_code': game.returncode,
              'native': a.native, 'cold_index': a.index_cache is None, 'physics_test': not a.replay and not a.picker_only,
              'total_s': round(time.perf_counter() - started, 1), 'peak_private_mb': round(peak / 2**20),
              'game': result, 'log': log_metrics(out / 'beamng.log')}
    if 'failures' in result:
        functional = [f for f in result['failures'] if f != 'frame_over_100ms']
        report['functional_status'] = 'failed' if functional else 'passed'
        report['performance_status'] = 'failed' if 'frame_over_100ms' in result['failures'] else 'passed'
    (out / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps({k: v for k, v in report.items() if k != 'game'}, indent=2))
    print('status', report['status'], 'failures', result.get('failures'), 'travelled_m', result.get('travelledM'),
          'drive_frames', result.get('driveFrames'))
    sys.exit(0 if report['status'] == 'passed' else 1)


if __name__ == '__main__':
    main()
