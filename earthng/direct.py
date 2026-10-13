"""A landing built straight for streaming, in a warm process (plan phase 1).

The same generator as `earth2road build` + `export --target beamng` (roadgen v2 when the config
asks for it), minus the work a streamed landing does not need:
- no surface audit (diagnostic report only; the world is identical without it),
- no input bundle copies, SHA-256 manifest or validate_world,
- no DAE files for generated meshes: export_map(sink=StreamSink) turns each mesh straight
  into binary ProceduralMesh pieces (the format mod/lua/ge/extensions/e2r/world.lua reads),
- no level validation, preview image, licence plates or ZIP.
Map exports keep using build_world/export_world and are not affected.
"""
import copy
import json
import math
from pathlib import Path
import re
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402,F401  (puts Earth2Road on sys.path)

import s1  # noqa: E402

MAX_TRIS = 1500
NAMES = re.compile(r'\b(?:kyiv_[A-Za-z0-9_]+|Kyiv[A-Za-z0-9_]+)\b')


def warm():
    """Import the heavy modules once (process pool initializer)."""
    from akadem_maps.runtime import check_sumo
    from akadem_maps.core import prepare  # noqa: F401
    from akadem_maps.adapters.beamng import export_beamng  # noqa: F401
    check_sumo('netconvert')


def scoped(level_id):
    """The material rename of namespace.py: own kyiv_* names get the level prefix."""
    def rename(name):
        return NAMES.sub(lambda m: m.group(0) if m.group(0) == level_id or m.group(0).startswith(level_id + '_')
                         else level_id + '__' + m.group(0), name)
    return rename


def mesh_subs(mesh, origin, uv_scales):
    """Mesh -> submeshes with shared vertices, the same normals and UVs as Mesh.write (non-compact)."""
    from akadem_maps.adapters.beamng.beamng_geometry import cross, normal, sub
    ox, oy, oz = origin
    subs = []
    for mat in sorted(mesh.faces):
        faces = mesh.faces[mat]
        if not faces:
            continue
        explicit = mesh.uvs[mat]
        scale = uv_scales.get(mat, 1.)
        p, n, t, idx, lookup = [], [], [], [], {}
        for i, tri in enumerate(faces):
            nv = normal(cross(sub(tri[1], tri[0]), sub(tri[2], tri[0])))
            nkey = (round(nv[0], 6), round(nv[1], 6), round(nv[2], 6))
            vertical, side = abs(nv[2]) < .5, abs(nv[0]) < abs(nv[1])
            fixed = explicit[i] if i < len(explicit) else None
            for corner, q in enumerate(tri):
                if fixed:
                    u, v = fixed[corner]
                elif vertical:
                    u, v = (q[0] if side else q[1]) * scale, q[2] * scale
                else:
                    u, v = q[0] * scale, q[1] * scale
                key = (round(q[0] - ox, 6), round(q[1] - oy, 6), round(q[2] - oz, 6), *nkey, round(u, 6), round(v, 6))
                k = lookup.get(key)
                if k is None:
                    k = lookup[key] = len(lookup)
                    p.extend(key[:3])
                    n.extend(key[3:6])
                    t.extend(key[6:])
                idx.append(k)
        subs.append({'mat': mat, 'p': p, 'n': n, 't': t, 'i': idx})
    return subs


class StreamSink:
    """export_map(sink=...) target: static meshes become binary pieces under <level>/proc."""

    def __init__(self, level_id):
        self.rename = scoped(level_id)
        self.pieces = 0
        self.seconds = 0.0

    def static(self, level, level_id, name, mesh, origin, collision=True, parent='KyivGenerated', *,
               collision_mesh=None, category='props'):
        from akadem_maps.adapters.beamng.export_beamng import UV, scene_object
        started = time.perf_counter()
        subs = mesh_subs(mesh, origin, UV)
        for s in subs:
            s['mat'] = self.rename(s['mat'])
            s['i'] = s1._wind(s['i'])          # ProceduralMesh winds opposite to COLLADA (run 05)
        files, tris = [], []
        for k, piece in enumerate(s1.split(subs, MAX_TRIS)):
            rel = f'proc/{name}_{k}.bin'
            path = Path(level) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(s1.write_bin(piece))
            files.append(rel)
            tris.append({'triangles': sum(len(x['i']) // 3 for x in piece),
                         'mats': sorted({x['mat'] for x in piece}),
                         'zmin': min(min(x['p'][2::3]) for x in piece)})
        self.pieces += len(files)
        self.seconds += time.perf_counter() - started
        # Visible-mesh collision only: a ProceduralMesh collides with what it draws, so a
        # separate collision mesh (bevelled kerbs) is not carried over.
        return scene_object(name, 'TSStatic', parent, position=list(origin), shapeName='',
                            e2rPieces=files, e2rPieceInfo=tris, e2rCategory=category,
                            collisionType='Visible Mesh Final' if collision else 'None')


def land(cfg, jobdir, cache, emit=lambda *a, **k: None):
    """Build + stream-export one area. Returns (level_dir, timings)."""
    from akadem_maps.context import BuildContext, RESOURCES, write_json
    from akadem_maps.world import validate_config
    from akadem_maps.core import prepare
    from akadem_maps.adapters.beamng.export_beamng import export_map
    timings = {}
    jobdir = Path(jobdir)
    cfg = validate_config(copy.deepcopy(cfg))
    world = jobdir / 'world'
    world.mkdir(parents=True)
    context = BuildContext(world, Path(cache).resolve(), jobdir, False, RESOURCES, emit=emit,
                           diagnostics=jobdir / 'logs')
    context.skip_surface_audit = True
    t = time.perf_counter()
    source = prepare.fetch(cfg, context)
    prepare.build(cfg, source, context)
    write_json(world / 'config.json', cfg)
    timings['build'] = round(time.perf_counter() - t, 2)
    t = time.perf_counter()
    sink = StreamSink(cfg['id'])
    export_map(cfg['id'], jobdir / 'level', level_id=cfg['id'], world_dir=world, namespace=True,
               package_zip=False, emit=emit, sink=sink)
    timings['export'] = round(time.perf_counter() - t, 2)
    timings['export_pieces'] = round(sink.seconds, 2)
    return jobdir / 'level/levels' / cfg['id'], timings
