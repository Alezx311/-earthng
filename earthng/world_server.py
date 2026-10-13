"""Earth2Road companion for the BeamNG "planet" level (docs/BEAMNG_STREAMING.md).

    C:/js/Earth2Road/.venv/Scripts/python.exe earthng/world_server.py --tiles data/osm_tiles/kyiv-260918

The BeamNG mod (mod/, level e2r_world) talks to this process through files in
the BeamNG user folder:

  e2r_world/request_<seq>.json   written by the game: {"type": "view", ...} or {"type": "land", ...}
  e2r_world/pose.json            written by the game while driving: car position and velocity
  e2r_world/status.json          written here: heartbeat, landing job, stream (ready tiles), map view
  e2r_world/view_<seq>.png       the map picture for a view request
  e2r_stream/<region>/...        streamed tiles: tiles/<i>_<j>.json (written last), their binary
                                 ProceduralMesh pieces and materials, shared textures in art/

A landing streams the prepared region (earthng/region.py) that contains the point: the tile
under the point first, then every tile within --radius of the car and of where it will be in
15/35 s, built by --workers processes (earthng/tiles.py: roadgen v2 on the region's network and
elevation, so neighbouring tiles meet). Tiles already delivered are reused (a revisit is instant).
Map pictures come from the OpenStreetMap tile server (cached, identified by User-Agent,
attribution drawn on the picture); that is a development stand-in, not for heavy use.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
import traceback
import urllib.request
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402  (puts Earth2Road on sys.path)
import osm_tiles  # noqa: E402
import tiles  # noqa: E402

USER_AGENT = 'Earth2Road/0.1 (+https://github.com/Alezx311/earth2road)'
TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
LANDING_WORKERS = 4   # builders while the tile under the landing point is not ready
LEAD_S = 300   # look-ahead along the heading, seconds of driving (a tile takes ~4 min under load)


def merc(lat, lon, z):
    """World pixel of a point at zoom z (256 px tiles)."""
    n = 256 * 2 ** z
    s = math.sin(math.radians(max(-85.05, min(85.05, lat))))
    return (lon + 180) / 360 * n, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n


def unmerc(x, y, z):
    n = 256 * 2 ** z
    lon = x / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


def warm(generator='v2'):
    """Worker initializer: below-normal priority (the game's main thread must win the CPU: with
    normal priority, 10 builders gave 2-5 s frames in route-03; netconvert children inherit it),
    then import the generator once per process."""
    if os.name == 'nt':
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    if generator == 'v2':
        import direct
        direct.warm()
    else:
        import fast


class Server:
    def __init__(self, args):
        self.user = Path(args.userfolder)
        self.io = self.user / 'e2r_world'
        self.io.mkdir(parents=True, exist_ok=True)
        self.work = Path(args.work).resolve()
        (self.work / 'maptiles').mkdir(parents=True, exist_ok=True)
        self.tiles_src = args.tiles
        self.index = json.loads((Path(self.tiles_src) / 'index.json').read_text(encoding='utf8'))
        self.regions = Path(args.regions).resolve()
        self.cache = Path(args.cache).resolve()
        self.workers = args.workers
        self.radius = args.radius
        self.lock = threading.RLock()
        self.status = {'server': 'up', 'started': time.time(), 'tilesSource': self.index['source'],
                       'coverage': self.region_boxes(), 'job': None, 'view': None, 'stream': None}
        self.pool = ThreadPoolExecutor(6)       # map pictures
        self.builders = ProcessPoolExecutor(self.workers, initializer=warm,
                                           initargs=(getattr(args, 'generator', 'v2'),))
        self.stream, self.frame, self.pose, self.landing = None, None, None, None
        self.out_of_region = set()
        self.heading = None
        # The pose also arrives as a UDP datagram: writing pose.json once blocked the game's
        # frame for 5.2 s (fast-replay-game-05); a send to localhost never waits.
        import socket
        self.pose_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.pose_socket.bind(('127.0.0.1', 0))
        self.status['posePort'] = self.pose_socket.getsockname()[1]
        threading.Thread(target=self.receive_poses, daemon=True).start()

    def receive_poses(self):
        while True:
            try:
                data, _ = self.pose_socket.recvfrom(65536)
                self.accept_pose(json.loads(data.decode('utf8')))
            except (OSError, ValueError):
                time.sleep(0.05)

    def region_boxes(self):
        return [json.loads(p.read_text(encoding='utf8'))['bbox'] for p in sorted(self.regions.glob('*/region.json'))]

    # --- io -----------------------------------------------------------------------------------
    def write_status(self):
        """Replace status.json atomically. The game may hold it open for reading at that moment
        (WinError 5 on Windows, world-test-03), so retry briefly and otherwise leave it to the next
        heartbeat; a missed status write must never fail a landing."""
        with self.lock:
            self.status['heartbeat'] = time.time()
            self.status['stream'] = self.stream_status()
            text = json.dumps(self.status)
        # BeamNG may remove the folder while it starts up with a new profile (world-test-05)
        self.io.mkdir(parents=True, exist_ok=True)
        tmp = self.io / 'status.json.tmp'
        tmp.write_text(text, encoding='utf8')
        for _ in range(20):
            try:
                os.replace(tmp, self.io / 'status.json')
                return
            except PermissionError:
                time.sleep(0.025)

    def set_job(self, **kw):
        with self.lock:
            self.status['job'] = {**(self.status['job'] or {}), **kw}
        self.write_status()

    # --- map pictures ---------------------------------------------------------------------------
    def map_tile(self, z, x, y):
        n = 2 ** z
        x %= n
        if not 0 <= y < n:
            return None
        path = self.work / 'maptiles' / str(z) / str(x) / f'{y}.png'
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(TILE_URL.format(z=z, x=x, y=y), headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read()
            path.write_bytes(data)
        return Image.open(path).convert('RGB')

    def render_view(self, req):
        z = int(req['zoom'])
        w, h = int(req['w']), int(req['h'])
        cx, cy = merc(req['lat'], req['lon'], z)
        x0, y0 = cx - w / 2, cy - h / 2
        img = Image.new('RGB', (w, h), (170, 190, 205))
        keys = [(tx, ty) for tx in range(math.floor(x0 / 256), math.floor((x0 + w) / 256) + 1)
                for ty in range(math.floor(y0 / 256), math.floor((y0 + h) / 256) + 1)]
        futures = {k: self.pool.submit(self.map_tile, z, *k) for k in keys}
        for (tx, ty), f in futures.items():
            try:
                tile = f.result()
            except Exception:
                tile = None
            if tile:
                img.paste(tile, (round(tx * 256 - x0), round(ty * 256 - y0)))
        over = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(over)

        def px(lat, lon):
            x, y = merc(lat, lon, z)
            return x - x0, y - y0
        # where a landing works: the prepared regions
        for c in self.status['coverage']:
            (ax, ay), (bx, by) = px(c[3], c[0]), px(c[1], c[2])
            if bx < 0 or by < 0 or ax > w or ay > h:
                continue
            d.rectangle([ax, ay, bx, by], fill=(40, 170, 80, 38))
        if req.get('marker'):
            mlat, mlon = req['marker']
            mx, my = px(mlat, mlon)
            d.ellipse([mx - 6, my - 6, mx + 6, my + 6], fill=(220, 30, 30, 255), outline=(255, 255, 255, 255), width=2)
        d.rectangle([w - 190, h - 16, w, h], fill=(255, 255, 255, 190))
        d.text((w - 186, h - 14), '(c) OpenStreetMap contributors', fill=(40, 40, 40, 255))
        img = Image.alpha_composite(img.convert('RGBA'), over).convert('RGB')
        name = f"view_{req['seq']}.png"
        img.save(self.io / name)
        for old in sorted(self.io.glob('view_*.png'), key=lambda p: p.stat().st_mtime)[:-4]:
            old.unlink(missing_ok=True)
        with self.lock:
            self.status['view'] = {'seq': req['seq'], 'file': f'/e2r_world/{name}', 'lat': req['lat'],
                                   'lon': req['lon'], 'zoom': z, 'w': w, 'h': h}
        self.write_status()

    # --- landing and streaming --------------------------------------------------------------------
    def region_for(self, lat, lon):
        """A prepared region (earthng/region.py) whose bbox contains the point, as (dir, meta)."""
        for meta_path in sorted(self.regions.glob('*/region.json')):
            meta = json.loads(meta_path.read_text(encoding='utf8'))
            w, s, e, n = meta['bbox']
            if w <= lon <= e and s <= lat <= n and (meta_path.parent / 'elevation.pkl').exists():
                return meta_path.parent, meta
        return None, None

    def land(self, req):
        lat, lon = req['lat'], req['lon']
        job = {'id': f"j{int(time.time())}", 'seq': req['seq'], 'lat': lat, 'lon': lon, 'message': None, 'timings': {}}
        rdir, meta = self.region_for(lat, lon)
        if not rdir:
            names = ', '.join(p.name for p in self.regions.glob('*')) or 'none'
            self.set_job(**{**job, 'state': 'error', 'stage': 'source', 'progress': 0,
                            'message': f'no_data: no prepared region contains this point '
                                       f'(earthng/region.py; prepared: {names})'})
            return
        frame = tiles.Frame(rdir)
        tile = list(frame.tile_of(lon, lat))
        key = f'{tile[0]}_{tile[1]}'
        with self.lock:
            if not (self.stream and self.stream['region'] == meta['id']):
                dest = self.user / 'e2r_stream' / meta['id'] / 'tiles'
                ready = {p.stem: 1 for p in dest.glob('*_*.json')
                         if re.fullmatch(r'-?\d+_-?\d+', p.stem)} if dest.exists() else {}
                self.stream = {'region': meta['id'], 'dir': str(rdir), 'tile': tiles.TILE, 'ready': ready,
                               'failed': {}, 'inflight': {}, 'built': 0, 'buildSeconds': 0.0, 'queued': 0,
                               'bounds': meta['bbox']}
                self.frame = frame
                self.out_of_region = set()
            self.pose, self.heading = None, None
            self.landing = {'seq': req['seq'], 'tile': tile, 'lat': lat, 'lon': lon, 'at': time.monotonic()}
            cached = key in self.stream['ready']
        common = dict(region=meta['id'], tileSize=tiles.TILE, tile=tile)
        if cached:
            self.set_job(**job, **common, state='ready', stage='ready', progress=1.0,
                         manifest=f"/e2r_stream/{meta['id']}/tiles/{key}.json", cached=True)
        else:
            self.set_job(**job, **common, state='working', stage='перший тайл (roadgen v2)', progress=0.05)
        self.schedule()

    def tile_wanted(self, i, j):
        """Inside the region (halo included) and covered by the OSM source tiles."""
        if (i, j) in self.out_of_region:
            return False
        bbox = self.frame.geo_bbox(self.frame.rect(i, j, tiles.HALO))
        w, s, e, n = self.stream['bounds']
        ok = w <= bbox[0] and s <= bbox[1] and bbox[2] <= e and bbox[3] <= n and osm_tiles.tile_for(self.index, bbox)
        if not ok:
            self.out_of_region.add((i, j))
        return bool(ok)

    def schedule(self):
        """Keep the builder pool busy with the most wanted missing tiles: around the landing point
        until the car is down, then around the car and along its heading up to LEAD_S of driving
        (the last heading is kept while the car stands, e.g. waiting for a tile)."""
        with self.lock:
            st = self.stream
            if not st:
                return
            # (x, y, radius, penalty): the car and its surroundings first, then single tiles far
            # along the heading. A tile takes minutes to build, so the look-ahead must reach where
            # the car will be by then (speed x LEAD_S), not where it is.
            if self.pose:
                p = self.pose
                vx, vy = p.get('vx', 0), p.get('vy', 0)
                speed = math.hypot(vx, vy)
                foci = [(p['x'], p['y'], self.radius, 0)]
                if p.get('ahead'):
                    # the planned route (autopilot / navigation): build along the road ahead
                    for ax, ay, along in p['ahead']:
                        foci.append((ax, ay, 300, along * 0.25))
                    self.heading = None
                elif speed > 1:
                    self.heading = (vx / speed, vy / speed, speed)
                if self.heading and not p.get('ahead'):
                    ux, uy, v = self.heading
                    reach = min(4000, max(1000, v * LEAD_S))
                    for along in range(250, int(reach) + 1, 250):
                        foci.append((p['x'] + ux * along, p['y'] + uy * along, 400, along * 0.35))
            elif self.landing:
                ti, tj = self.landing['tile']
                foci = [((ti + .5) * tiles.TILE, (tj + .5) * tiles.TILE, self.radius, 0)]
            else:
                return
            cand = {}
            for fx, fy, radius, penalty in foci:
                ci, cj = math.floor(fx / tiles.TILE), math.floor(fy / tiles.TILE)
                reach = int(math.ceil(radius / tiles.TILE)) + 1
                for i in range(ci - reach, ci + reach + 1):
                    for j in range(cj - reach, cj + reach + 1):
                        key = f'{i}_{j}'
                        if key in st['ready'] or key in st['inflight'] or st['failed'].get(key, 0) >= 2:
                            continue
                        d = math.dist(((i + .5) * tiles.TILE, (j + .5) * tiles.TILE), (fx, fy))
                        if d <= radius and (key not in cand or d + penalty < cand[key][0]):
                            cand[key] = (d + penalty, i, j)
            todo = [c for c in sorted(cand.values()) if self.tile_wanted(c[1], c[2])]
            st['queued'] = len(todo)
            free = self.workers - len(st['inflight'])
            if self.landing and f"{self.landing['tile'][0]}_{self.landing['tile'][1]}" not in st['ready']:
                # the landing tile first, with little competition for the CPU
                free = min(free, LANDING_WORKERS - len(st['inflight']))
            for _, i, j in todo[:max(0, free)]:
                key = f'{i}_{j}'
                center = None
                if self.landing and [i, j] == self.landing['tile']:
                    center = (self.landing['lon'], self.landing['lat'])
                fut = self.builders.submit(tiles.run, st['dir'], i, j, str(self.work / 'tiles' / st['region']),
                                           str(self.user / 'e2r_stream'), str(self.cache), center)
                st['inflight'][key] = time.monotonic()
                fut.add_done_callback(lambda f, key=key, region=st['region']: self.tile_done(region, key, f))

    def tile_done(self, region, key, fut):
        with self.lock:
            st = self.stream
            if not st or st['region'] != region:
                return
            st['inflight'].pop(key, None)
            try:
                res = fut.result()
                st['ready'][key] = 1
                st['built'] += 1
                st['buildSeconds'] += res['timings']['total']
                print('tile', key, json.dumps(res['timings']), res['items'], 'items', res['triangles'], 'tris', flush=True)
            except Exception as e:
                st['failed'][key] = st['failed'].get(key, 0) + 1
                print('tile', key, 'FAILED', repr(e), flush=True)
                traceback.print_exception(e)
            landing = self.landing
            job = self.status['job'] or {}
            first = landing and key == f"{landing['tile'][0]}_{landing['tile'][1]}" and \
                job.get('seq') == landing['seq'] and job.get('state') == 'working'
        if first:
            if key in self.stream['ready']:
                self.set_job(state='ready', stage='ready', progress=1.0,
                             manifest=f"/e2r_stream/{region}/tiles/{key}.json",
                             timings={'first_tile': round(time.monotonic() - landing['at'], 1)})
            elif self.stream['failed'].get(key, 0) >= 2:
                self.set_job(state='error', message=f'tile {key} failed twice; see the companion log')
        try:
            self.schedule()
        except Exception:
            traceback.print_exc()

    def read_pose(self):
        try:
            pose = json.loads((self.io / 'pose.json').read_text(encoding='utf8'))
        except (OSError, json.JSONDecodeError):
            return
        self.accept_pose(pose)

    def accept_pose(self, pose):
        # the newest of file and datagram wins (t: game clock)
        if self.pose and pose.get('seq', 0) < self.pose.get('seq', 0):
            return
        with self.lock:
            if self.stream and pose.get('region') == self.stream['region'] and pose.get('state') == 'driving':
                self.pose = pose

    def stream_status(self):
        st = self.stream
        if not st:
            return None
        return {'region': st['region'], 'tile': st['tile'], 'ready': dict(st['ready']), 'readyCount': len(st['ready']),
                'building': len(st['inflight']), 'queued': st['queued'], 'failed': dict(st['failed']),
                'built': st['built'], 'meanBuildSeconds': round(st['buildSeconds'] / st['built'], 1) if st['built'] else None}

    # --- loop ----------------------------------------------------------------------------------
    def serve(self):
        print(f'Earth2Road companion: user folder {self.user}, coverage areas {len(self.status["coverage"])}, '
              f'{self.workers} tile builders', flush=True)
        last_beat = 0
        while True:
            for path in sorted(self.io.glob('request_*.json'), key=lambda p: int(p.stem.split('_')[1])):
                try:
                    req = json.loads(path.read_text(encoding='utf8'))
                except (json.JSONDecodeError, OSError):
                    continue   # still being written; next round
                path.unlink(missing_ok=True)
                print('request', req, flush=True)
                try:
                    if req.get('type') == 'view':
                        self.render_view(req)
                    elif req.get('type') == 'land':
                        self.land(req)
                except Exception as e:
                    traceback.print_exc()
                    if req.get('type') == 'land':
                        self.set_job(seq=req['seq'], state='error', message=str(e))
            self.read_pose()
            try:
                self.schedule()
            except Exception:
                traceback.print_exc()
            if time.time() - last_beat > 0.5:
                try:
                    self.write_status()
                except OSError:
                    traceback.print_exc()
                last_beat = time.time()
            time.sleep(0.1)


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--tiles', default=str(paths.TILES), help='OSM source tiles (osm_tiles.py), a directory')
    p.add_argument('--userfolder', default=str(Path(os.environ['LOCALAPPDATA']) / 'BeamNG/BeamNG.drive/current'))
    p.add_argument('--work', default=str(paths.OUT / 'world'))
    p.add_argument('--cache', default=str(paths.OUT / 'world' / 'cache'), help='generator source cache (DEM tiles)')
    p.add_argument('--regions', default=str(paths.OUT / 'region'), help='prepared regions (earthng/region.py)')
    p.add_argument('--workers', type=int, default=8, help='tile builder processes')
    p.add_argument('--radius', type=float, default=800, help='tiles are generated within this distance of the car, m')
    p.add_argument('--generator', choices=('v2', 'fast'), default='v2', help='fast is experimental until acceptance gates pass')
    args = p.parse_args()
    if args.workers < 1:
        p.error('--workers must be positive')
    if args.generator == 'fast':
        from fast_server import server_class
        server_class(Server)(args).serve()
    else:
        Server(args).serve()


if __name__ == '__main__':
    main()
