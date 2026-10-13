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
    p.add_argument('--lat', type=float, required=True)
    p.add_argument('--lon', type=float, required=True)
    p.add_argument('--tiles', default=str(paths.TILES))
    p.add_argument('--timeout', type=int, default=420)
    p.add_argument('--route', type=Path, help='route.json (earthng/route.py) for the autopilot')
    p.add_argument('--speed', type=float, default=13.0, help='autopilot cruise speed, m/s')
    p.add_argument('--drive-seconds', type=float, default=8)
    p.add_argument('--workers', type=int, default=8)
    p.add_argument('--stream-cache', type=Path, help='copy an earlier e2r_stream folder in (warm tiles)')
    a = p.parse_args()
    out = a.out.resolve()
    if out.exists():
        raise SystemExit(f'{out} exists')
    user = out / 'user'
    profile = user / 'current'
    (profile / 'mods').mkdir(parents=True)
    shutil.copy2(build_mod.build(out / 'mod'), profile / 'mods' / build_mod.ZIP)
    (profile / 'settings').mkdir()
    (profile / 'settings/settings.json').write_text(json.dumps({
        'GraphicDisplayResolutions': '1280 720', 'GraphicFullscreen': False, 'GraphicOverallQuality': 'Normal',
        'AudioMasterVol': 0, 'fpsLimitBackgroundEnabled': False, 'onlineFeatures': 'disable', 'telemetry': 'disable', 'PostFXMotionBlurEnabled': False}), encoding='utf8')
    test = {'lat': a.lat, 'lon': a.lon, 'driveSeconds': a.drive_seconds}
    if a.route:
        shutil.copy2(a.route, profile / 'e2r-route.json')
        test.update(route='/e2r-route.json', speed=a.speed)
    if a.stream_cache:
        shutil.copytree(a.stream_cache, profile / 'e2r_stream')
    (profile / 'e2r-world-test.json').write_text(json.dumps(test), encoding='utf8')
    server = subprocess.Popen([sys.executable, str(HERE / 'world_server.py'), '--tiles', a.tiles,
                               '--userfolder', str(profile), '--work', str(out / 'work'),
                               '--workers', str(a.workers)],
                              cwd=paths.PROJECT, stdout=open(out / 'server.log', 'w'), stderr=subprocess.STDOUT)
    started = time.perf_counter()
    game = subprocess.Popen([str(EXE), '-userpath', str(user), '-level', build_mod.LEVEL, '-nosteam'], cwd=str(EXE.parent))
    peak = 0
    try:
        test_file = profile / 'e2r-world-test.json'
        res_path = profile / 'e2r-world-result.json'
        while game.poll() is None and time.perf_counter() - started < a.timeout:
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
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(server.pid)], capture_output=True)
    res_path = profile / 'e2r-world-result.json'
    result = json.loads(res_path.read_text(encoding='utf8')) if res_path.exists() else {'status': 'no_result'}
    for png in sorted((profile / 'screenshots').rglob('e2r-*.png')):
        shutil.copy2(png, out / png.name)
    if (profile / 'beamng.log').exists():
        shutil.copy2(profile / 'beamng.log', out / 'beamng.log')
    report = {'status': result.get('status'), 'exit_code': game.returncode,
              'total_s': round(time.perf_counter() - started, 1), 'peak_private_mb': round(peak / 2**20),
              'game': result, 'log': log_metrics(out / 'beamng.log')}
    (out / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
