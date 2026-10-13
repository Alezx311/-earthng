"""Streaming spike S1/S3: load exported tiles into a running BeamNG level (docs/BEAMNG_STREAMING.md).

    C:/js/Earth2Road/.venv/Scripts/python.exe earthng/s1.py C:/js/Earth2Road/out/beamng-opt-tiny-v2/levels/kyiv_tiny --out out/beamng-s1-01

Runs BeamNG in an isolated user directory (``--out``/user) with the empty level ``e2r_void``.
Only after the game reports ``request_tiles`` does this script copy the tile DAEs and materials
into the user folder, so the run proves that files written after launch are picked up. The
extension spawns them in rings around the spawn (S3: collision rebuild cost per batch), teleports
the player car onto them and drives straight. The user's own BeamNG profile is not touched.
S2: ``--mode proc --format bin --max-tris 1500`` delivers the same tiles as binary pieces built
into ProceduralMesh objects in Lua instead of DAEs. Tiles are lifted ``--lift`` m (default 20)
because the empty level has an invisible collision plane at z=0.
"""
import argparse
import json
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402,F401  (puts Earth2Road and its tools/ on sys.path)
from beamng_bench import EXE, log_metrics, memory  # noqa: E402

LEVEL = 'e2r_void'
STREAM = 'e2r_stream/s1'
PAD = (-3000.0, 0.0)
RINGS = [('ring200', 200.0), ('ring450', 450.0), ('rest', math.inf)]
TILE = 250.0


def lines(path):
    text = path.read_text(encoding='utf8') if path.exists() else ''
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def yaw(matrix):
    return math.atan2(matrix[3], matrix[0]) if matrix else 0.0


def triangles(dae):
    return sum(int(n) for n in re.findall(r'<triangles[^>]*count="(\d+)"', dae.read_text(encoding='utf8')))


def proc_mesh(dae):
    """A DAE from our exporter as ProceduralMesh submeshes: flat arrays, one index per corner."""
    ns = {'c': 'http://www.collada.org/2005/11/COLLADASchema'}
    root = ET.parse(dae).getroot()
    subs = []
    for geom in root.iterfind('.//c:geometry', ns):
        mesh = geom.find('c:mesh', ns)
        arrays = {s.get('id'): s.find('c:float_array', ns).text.split() for s in mesh.iterfind('c:source', ns)}
        vertices = mesh.find('c:vertices/c:input', ns).get('source')[1:]
        for tri in mesh.iterfind('c:triangles', ns):
            inputs = {i.get('semantic'): i for i in tri.iterfind('c:input', ns)}
            if any(i.get('offset', '0') != '0' for i in inputs.values()):
                raise SystemExit(f'{dae.name}: multi-offset triangles are not supported')
            pos = arrays[vertices]
            nrm = arrays[inputs['NORMAL'].get('source')[1:]] if 'NORMAL' in inputs else ['0', '0', '1'] * (len(pos)//3)
            uv = arrays[inputs['TEXCOORD'].get('source')[1:]] if 'TEXCOORD' in inputs else ['0'] * (len(pos)//3*2)
            subs.append({'mat': tri.get('material'), 'p': [round(float(v), 3) for v in pos],
                         'n': [round(float(v), 3) for v in nrm], 't': [round(float(v), 4) for v in uv],
                         'i': _wind([int(v) for v in tri.find('c:p', ns).text.split()])})
    return subs


def split(subs, max_tris):
    """Cut submeshes into pieces of at most max_tris triangles, each with only the vertices it uses,
    so no single createMesh call is larger than the per-frame budget allows."""
    pieces, cur, cur_tris = [], [], 0
    for sub in subs:
        idx = sub['i']
        for start in range(0, len(idx), max_tris * 3):
            chunk = idx[start:start + max_tris * 3]
            if cur and cur_tris + len(chunk) // 3 > max_tris:
                pieces.append(cur)
                cur, cur_tris = [], 0
            remap, p, n, t = {}, [], [], []
            for v in chunk:
                if v not in remap:
                    remap[v] = len(remap)
                    p += sub['p'][3*v:3*v+3]
                    n += sub['n'][3*v:3*v+3]
                    t += sub['t'][2*v:2*v+2]
            cur.append({'mat': sub['mat'], 'p': p, 'n': n, 't': t, 'i': [remap[v] for v in chunk]})
            cur_tris += len(chunk) // 3
    if cur:
        pieces.append(cur)
    return pieces


def _wind(idx):
    """ProceduralMesh faces wind opposite to COLLADA (run 05: unwound tiles were invisible from above
    and the car stuck on one-sided collision); swap two corners of every triangle."""
    out = []
    for k in range(0, len(idx), 3):
        out += [idx[k], idx[k+2], idx[k+1]]
    return out


def write_bin(piece):
    """The binary piece e2rstream.lua readBin() decodes with LuaJIT FFI (little-endian).
    Every field is 4-byte aligned so Lua indexes typed arrays without pointer arithmetic."""
    out = [struct.pack('<I', len(piece))]
    for sub in piece:
        name = sub['mat'].encode('utf8')
        name += b'\0' * (-len(name) % 4)
        out += [struct.pack('<I', len(name)), name, struct.pack('<II', len(sub['p']) // 3, len(sub['i']))]
        for key in ('p', 'n', 't'):
            out.append(struct.pack(f"<{len(sub[key])}f", *sub[key]))
        out.append(struct.pack(f"<{len(sub['i'])}I", *sub['i']))
    return b''.join(out)


def tileset(level_dir, mode='dae', work=None, max_tris=10**9, fmt='json', box=None, spawn_name=None):
    """Objects of the exported level as streamable items, plus the files they need.

    box: keep only a square of 2*box metres around the spawn. Generated meshes are 250 m tiles
    placed at their tile centre, so a tile is kept when its square overlaps the box; props and
    signals are kept when their position is inside it."""
    level_id = level_dir.name
    shapes_prefix = f'/levels/{level_id}/art/shapes/'
    group_dir = level_dir / 'main/MissionGroup'
    spheres = [o for o in lines(group_dir / 'items.level.json') if o.get('class') == 'SpawnSphere']
    spawn = next(o for o in spheres if spawn_name in (None, o['name']))
    cx, cy = spawn['position'][:2]
    items, files = [], {}
    # older exports name groups KyivGenerated, newer ones <level>__KyivGenerated
    groups = [d for d in sorted(group_dir.iterdir()) if d.is_dir()
              and d.name.split('__')[-1] in ('KyivGenerated', 'KyivProps', 'KyivSignals')]
    for gdir in groups:
        reach = (box + TILE / 2) if gdir.name.endswith('KyivGenerated') and box else box
        for o in lines(gdir / 'items.level.json'):
            if o.get('class') != 'TSStatic':
                continue
            if reach and (abs(o['position'][0] - cx) > reach or abs(o['position'][1] - cy) > reach):
                continue
            shape, tris = o['shapeName'], 0
            a = yaw(o.get('rotationMatrix'))
            if o.get('e2rPieces'):
                # built straight into pieces by direct.StreamSink; positions as in the scene
                for k, (rel, info) in enumerate(zip(o['e2rPieces'], o['e2rPieceInfo'])):
                    files[rel] = level_dir / rel
                    items.append({'name': f"{o['name']}_{k}", 'kind': 'proc', 'shape': f'/{STREAM}/{rel}',
                                  'pos': o['position'], 'rot': [0, 0, 0, 1], 'triangles': info['triangles'],
                                  'mats': info['mats'], 'zmin': o['position'][2] + info['zmin'],
                                  'category': o.get('e2rCategory')})
                continue
            if shape.startswith(shapes_prefix):
                src = level_dir / 'art/shapes' / shape[len(shapes_prefix):]
                tris = triangles(src)
                if mode == 'proc':
                    for k, piece in enumerate(split(proc_mesh(src), max_tris)):
                        built = work / f'{src.stem}_{k}.{fmt}'
                        if fmt == 'bin':
                            built.write_bytes(write_bin(piece))
                        else:
                            built.write_text(json.dumps(piece, separators=(',', ':')), encoding='utf8')
                        files[f'proc/{built.name}'] = built
                        items.append({'name': f"{o['name']}_{k}", 'kind': 'proc', 'shape': f'/{STREAM}/proc/{built.name}',
                                      'pos': o['position'], 'rot': [0, 0, math.sin(a/2), math.cos(a/2)],
                                      'triangles': sum(len(sub['i']) // 3 for sub in piece),
                                      'mats': sorted({sub['mat'] for sub in piece}),
                                      'zmin': o['position'][2] + min(min(sub['p'][2::3]) for sub in piece)})
                    continue
                files[f'shapes/{src.name}'] = src
                shape = f'/{STREAM}/shapes/{src.name}'
            items.append({'name': o['name'], 'kind': 'static', 'shape': shape, 'pos': o['position'],
                          'rot': [0, 0, math.sin(a/2), math.cos(a/2)],
                          'collision': o.get('collisionType', 'Visible Mesh Final'),
                          'decal': o.get('decalType', 'None'), 'triangles': tris})
    materials = (level_dir / 'art/kyiv/main.materials.json').read_text(encoding='utf8')
    materials = materials.replace(f'/levels/{level_id}/art/kyiv/', f'/{STREAM}/art/')
    leftover = re.findall(rf'/levels/{level_id}/[^"]+', materials)
    if leftover:
        raise SystemExit(f'material paths still point into the level: {leftover[:3]}')
    for tex in (level_dir / 'art/kyiv').iterdir():
        if tex.suffix.lower() in ('.png', '.dds', '.jpg'):
            files[f'art/{tex.name}'] = tex
    return spawn, items, files, materials


def batches(items, spawn_pos):
    out, left = [], list(items)
    for name, radius in RINGS:
        inside = [it for it in left if math.dist(it['pos'][:2], spawn_pos[:2]) <= radius]
        left = [it for it in left if math.dist(it['pos'][:2], spawn_pos[:2]) > radius]
        if inside:
            out.append({'name': name, 'items': inside, 'triangles': sum(it['triangles'] for it in inside)})
    return out


def pad_dae(size=60.0):
    h = size / 2
    pos = f'{-h} {-h} 0 {h} {-h} 0 {h} {h} 0 {-h} {h} 0'
    return f'''<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
<asset><unit name="meter" meter="1"/><up_axis>Z_UP</up_axis></asset>
<library_effects><effect id="e2r_pad-fx"><profile_COMMON><technique sid="common"><lambert><diffuse><color>0.5 0.5 0.5 1</color></diffuse></lambert></technique></profile_COMMON></effect></library_effects>
<library_materials><material id="e2r_pad" name="e2r_pad"><instance_effect url="#e2r_pad-fx"/></material></library_materials>
<library_geometries><geometry id="g0" name="g0"><mesh>
<source id="g0-pos"><float_array id="g0-pos-array" count="12">{pos}</float_array><technique_common><accessor source="#g0-pos-array" count="4" stride="3"><param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/></accessor></technique_common></source>
<source id="g0-uv"><float_array id="g0-uv-array" count="8">0 0 8 0 8 8 0 8</float_array><technique_common><accessor source="#g0-uv-array" count="4" stride="2"><param name="S" type="float"/><param name="T" type="float"/></accessor></technique_common></source>
<vertices id="g0-vtx"><input semantic="POSITION" source="#g0-pos"/></vertices>
<triangles material="e2r_pad" count="2"><input semantic="VERTEX" source="#g0-vtx" offset="0"/><input semantic="TEXCOORD" source="#g0-uv" offset="0" set="0"/><p>0 1 2 0 2 3</p></triangles>
</mesh></geometry></library_geometries>
<library_visual_scenes><visual_scene id="Scene"><node id="base00" name="base00"><node id="start01" name="start01"><node id="pad" name="pad"><instance_geometry url="#g0"><bind_material><technique_common><instance_material symbol="e2r_pad" target="#e2r_pad"/></technique_common></bind_material></instance_geometry></node></node></node></visual_scene></library_visual_scenes>
<scene><instance_visual_scene url="#Scene"/></scene></COLLADA>
'''


def void_level(mod, sky):
    """An empty level: sky, a 60 m pad far from the tiles for the default car, nothing else."""
    root = mod / 'levels' / LEVEL
    (root / 'main/MissionGroup/Sky').mkdir(parents=True)
    (root / 'art').mkdir()
    (root / 'info.json').write_text(json.dumps({
        'title': 'Earth2Road stream (empty)', 'description': 'Streaming spike carrier level.',
        'authors': 'Earth2Road', 'defaultSpawnPointName': 'spawn_pad', 'size': [8192, 8192],
        'spawnPoints': [{'name': 'Pad', 'objectname': 'spawn_pad'}]}, indent=2), encoding='utf8')
    (root / 'main/items.level.json').write_text(json.dumps({'class': 'SimGroup', 'name': 'MissionGroup'}) + '\n',
                                                encoding='utf8')
    objects = [
        {'__parent': 'MissionGroup', 'class': 'SimGroup', 'name': 'Sky'},
        {'__parent': 'MissionGroup', 'class': 'SpawnSphere', 'dataBlock': 'SpawnSphereMarker', 'name': 'spawn_pad',
         'position': [PAD[0], PAD[1], 0.6], 'radius': 2},
        {'__parent': 'MissionGroup', 'class': 'TSStatic', 'name': 'e2r_pad_object', 'shapeName': f'/levels/{LEVEL}/art/pad.dae',
         'position': [PAD[0], PAD[1], 0.0], 'collisionType': 'Visible Mesh Final', 'decalType': 'Visible Mesh',
         'useInstanceRenderData': True},
    ]
    (root / 'main/MissionGroup/items.level.json').write_text(
        ''.join(json.dumps(o) + '\n' for o in objects), encoding='utf8')
    (root / 'main/MissionGroup/Sky/items.level.json').write_text(
        ''.join(json.dumps({**o, '__parent': 'Sky'}) + '\n' for o in sky), encoding='utf8')
    (root / 'art/pad.dae').write_text(pad_dae(), encoding='utf8')
    (root / 'art/main.materials.json').write_text(json.dumps({'e2r_pad': {
        'name': 'e2r_pad', 'mapTo': 'e2r_pad', 'class': 'Material', 'version': 1.5,
        'Stages': [{'baseColorMap': '/assets/materials/terrain/grass/groundmesh_grass2/groundmesh_grass_b.color.png',
                    'baseColorFactor': [0.55, 0.55, 0.5, 1], 'roughnessFactor': 0.9}, {}, {}, {}]}}, indent=2),
        encoding='utf8')


def stage(args, user):
    level_dir = args.level_dir.resolve()
    work = user.parent / 'proc_build'
    work.mkdir(parents=True)
    spawn, items, files, materials = tileset(level_dir, args.mode, work, args.max_tris, args.format, args.box, args.spawn)
    # The empty level has an invisible collision plane at z=0 (run 11: the car drove at z=0.30 with
    # no tiles loaded). Lifting everything proves the car rides on the streamed tiles themselves.
    for it in items:
        it['pos'] = [it['pos'][0], it['pos'][1], it['pos'][2] + args.lift]
    sp = list(spawn['position'])
    sp[2] += args.lift
    plan = batches(items, sp)
    profile = user / 'current'
    mod = profile / 'mods/unpacked/e2r_stream'
    (mod / 'lua/ge/extensions').mkdir(parents=True)
    shutil.copy2(HERE / 'e2rstream.lua', mod / 'lua/ge/extensions/e2rstream.lua')
    sky_dir = next(d for d in (level_dir / 'main/MissionGroup').iterdir() if d.name.split('__')[-1] == 'KyivSky')
    # Precipitation/wind emitters need datablocks of their own; the carrier level keeps light and sky
    sky = [o for o in lines(sky_dir / 'items.level.json') if o['class'] in ('ScatterSky', 'CloudLayer', 'LevelInfo', 'TimeOfDay')]
    void_level(mod, sky)
    (profile / 'settings').mkdir()
    (profile / 'settings/settings.json').write_text(json.dumps({
        'GraphicDisplayResolutions': args.resolution, 'GraphicFullscreen': False,
        'GraphicOverallQuality': args.quality, 'AudioMasterVol': 0,
        'fpsLimitBackgroundEnabled': False, 'PostFXMotionBlurEnabled': False}), encoding='utf8')
    (profile / 'e2r-stream-input.json').write_text(json.dumps({
        'timeout': args.timeout, 'settleSeconds': 6, 'spawnBudgetMs': args.budget, 'warmupSeconds': 6,
        'warmupMaterials': sorted({m for it in items for m in it.get('mats', [])}) if args.warmup else None, 'batchSettleSeconds': 4, 'driveSeconds': args.drive,
        'readyFile': f'/{STREAM}/ready.json', 'materials': f'/{STREAM}/main.materials.json',
        'overview': {'pos': [sp[0]-260, sp[1]-300, sp[2]+230], 'look': [sp[0]+40, sp[1]+40, sp[2]]},
        'spawn': {'pos': [sp[0], sp[1], sp[2]+0.5], 'yaw': yaw(spawn.get('rotationMatrix'))},
        'batches': plan}), encoding='utf8')
    return files, materials, plan


def deliver(profile, files, materials):
    """Write the tiles into the running game's user folder; ready.json goes last."""
    started = time.perf_counter()
    dest = profile / STREAM
    for rel, src in files.items():
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest / rel)
    (dest / 'main.materials.json').write_text(materials, encoding='utf8')
    (dest / 'ready.json').write_text(json.dumps({'files': len(files)}), encoding='utf8')
    return {'files': len(files), 'mb': round(sum(p.stat().st_size for p in dest.rglob('*')) / 2**20, 2),
            'copy_s': round(time.perf_counter() - started, 3)}


def run(args):
    out = args.out.resolve()
    if out.exists():
        raise SystemExit(f'{out} exists; every run writes a new directory')
    user = out / 'user'
    files, materials, plan = stage(args, user)
    profile = user / 'current'
    if args.seed_cache:
        # what the game cooked in an earlier run (shapes/*.cdae, art/*.dds under temp/e2r_stream/s1):
        # the warm case of revisiting a place, or textures/shapes precooked by the generator
        shutil.copytree(args.seed_cache, profile / 'temp' / STREAM)
    result_path = profile / 'e2r-stream-result.json'
    cmd = [str(EXE), '-userpath', str(user), '-level', LEVEL, '-onLevelLoad_ext', 'e2rstream', '-nosteam']
    started = time.perf_counter()
    proc = subprocess.Popen(cmd, cwd=str(EXE.parent))
    delivery, samples, status = None, [], None
    while proc.poll() is None:
        now = time.perf_counter() - started
        m = memory(proc.pid)
        if m:
            samples.append((round(now, 2), m[0], m[1]))
        if result_path.exists():
            try:
                status = json.loads(result_path.read_text(encoding='utf8')).get('status')
            except (json.JSONDecodeError, OSError):
                pass
        if status == 'request_tiles' and delivery is None:
            delivery = deliver(profile, files, materials)
            delivery['at_s'] = round(now, 1)
            print('delivered', delivery, flush=True)
        if now > args.timeout + 60:
            proc.kill()
            break
        time.sleep(.25)
    result = json.loads(result_path.read_text(encoding='utf8')) if result_path.exists() else {'status': 'no_result'}
    for png in sorted((profile / 'screenshots').rglob('s1-*.png')):
        shutil.copy2(png, out / png.name)
    if (profile / 'beamng.log').exists():
        shutil.copy2(profile / 'beamng.log', out / 'beamng.log')
    report = {'status': result.get('status'), 'exit_code': proc.returncode,
              'total_s': round(time.perf_counter() - started, 1), 'delivery': delivery,
              'plan': [{'name': b['name'], 'objects': len(b['items']), 'triangles': b['triangles']} for b in plan],
              'game': result, 'log': log_metrics(out / 'beamng.log'),
              'peak_private_mb': samples and round(max(s[1] for s in samples) / 2**20)}
    (out / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('level_dir', type=Path, help='unpacked exported level, e.g. out/.../levels/kyiv_tiny')
    p.add_argument('--out', type=Path, required=True, help='new output directory')
    p.add_argument('--timeout', type=int, default=300)
    p.add_argument('--drive', type=float, default=8)
    p.add_argument('--budget', type=float, default=0, help='spawn time per frame in ms; 0 = a whole batch in one frame')
    p.add_argument('--mode', choices=('dae', 'proc'), default='dae',
                   help='tiles as TSStatic DAEs, or as ProceduralMesh built in Lua from JSON (S2)')
    p.add_argument('--box', type=float, help='keep a square of 2*BOX m around the spawn (e.g. 250 for 500x500 m)')
    p.add_argument('--spawn', help='SpawnSphere name (default: the first one)')
    p.add_argument('--warmup', action='store_true', help='proc mode: show every tile material once before streaming')
    p.add_argument('--lift', type=float, default=20, help='raise all tiles by N m above the level''s z=0 plane')
    p.add_argument('--format', choices=('json', 'bin'), default='json', help='proc mode: piece file format')
    p.add_argument('--max-tris', type=int, default=10**9, help='proc mode: split tiles into pieces of at most N triangles')
    p.add_argument('--seed-cache', type=Path, help='an earlier run''s user/current/temp/e2r_stream/s1 (cooked .cdae and .dds; warm start)')
    p.add_argument('--resolution', default='1280 720')
    p.add_argument('--quality', default='Normal')
    run(p.parse_args())


if __name__ == '__main__':
    main()
