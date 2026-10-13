"""Region preparation for streamed tiles (docs/BEAMNG_STREAMING.md, "Регіон").

    C:/js/Earth2Road/.venv/Scripts/python.exe earthng/region.py --pbf <kyiv.osm.pbf> --bbox 30.40 50.36 30.62 50.55 --out out/region/kyiv-ns

roadgen v2 decisions depend on far context (netconvert junction joining, elevation strokes):
independently built windows disagree by up to 1.27 m. A region therefore computes those inputs
once, without any geometry: the road-only OSM of the region, its SUMO network and the road
elevation profiles. Every tile then reads the same network (cut with netconvert, no
renormalisation) and the same profiles, so neighbouring tiles agree.

Output (out/region/<id>/): roads.osm, build/network.net.xml, elevation.pkl, region.json.
"""
import argparse
import json
import pickle
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402,F401  (puts Earth2Road on sys.path)

ROAD_KEYS = ('highway', 'railway')


def road_osm(pbf, out, bbox):
    """Ways tagged highway/railway with a node inside bbox, all their nodes (with tags: signals,
    crossings) and the restriction relations among them, as OSM XML."""
    import osmium
    x0, y0, x1, y1 = bbox

    class Ways(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.ways, self.refs, self.rels = set(), set(), set()

        def way(self, w):
            if not any(k in w.tags for k in ROAD_KEYS):
                return
            inside = False
            for n in w.nodes:
                if n.location.valid() and x0 <= n.location.lon <= x1 and y0 <= n.location.lat <= y1:
                    inside = True
                    break
            if inside:
                self.ways.add(w.id)
                self.refs.update(n.ref for n in w.nodes)

        def relation(self, r):
            if r.tags.get('type') == 'restriction':
                self.rels.add(r.id)

    first = Ways()
    first.apply_file(str(pbf), locations=True)

    class Write(osmium.SimpleHandler):
        def __init__(self, writer):
            super().__init__()
            self.w = writer

        def node(self, n):
            if n.id in first.refs:
                self.w.add_node(n)

        def way(self, w):
            if w.id in first.ways:
                self.w.add_way(w)

        def relation(self, r):
            if r.id in first.rels and any(m.type == 'w' for m in r.members) and all(
                    (m.type == 'w' and m.ref in first.ways) or (m.type == 'n' and m.ref in first.refs)
                    for m in r.members):
                self.w.add_relation(r)

    out.unlink(missing_ok=True)
    writer = osmium.SimpleWriter(str(out))
    try:
        Write(writer).apply_file(str(pbf))
    finally:
        writer.close()
    return len(first.ways), len(first.refs)


def config(rid, bbox, center):
    return {'id': rid, 'name': rid, 'bbox': list(bbox), 'center': list(center), 'region_profile': 'ukraine',
            'road_geometry': {'mode': 'v2'}}


def prepare(pbf, bbox, out, cache):
    from akadem_maps.context import BuildContext, RESOURCES
    from akadem_maps.world import validate_config
    from akadem_maps.core import prepare as prep
    from akadem_maps import sources
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    timings = {}
    t = time.perf_counter()
    osm = out / 'roads.osm'
    ways, nodes = road_osm(pbf, osm, bbox) if not osm.exists() else (None, None)
    timings['osm'] = round(time.perf_counter() - t, 1)
    print(f'roads.osm: {ways} ways, {nodes} nodes, {osm.stat().st_size / 1e6:.1f} MB, {timings["osm"]} s', flush=True)
    center = [round((bbox[0] + bbox[2]) / 2, 6), round((bbox[1] + bbox[3]) / 2, 6)]
    cfg = validate_config(config(out.name.replace('-', '_'), bbox, center))
    world = out / 'world'
    world.mkdir(exist_ok=True)
    context = BuildContext(world, Path(cache).resolve(), out, False, RESOURCES, diagnostics=out / 'logs')
    context.stop_after = 'elevation'
    t = time.perf_counter()
    root = sources.validate_osm(osm)
    res = prep.build(cfg, root, context)
    timings['network_elevation'] = round(time.perf_counter() - t, 1)
    print(f'network + elevation: {timings["network_elevation"]} s', flush=True)
    net = out / 'network.net.xml'
    Path(res['net']).replace(net)
    with open(out / 'elevation.pkl', 'wb') as f:
        pickle.dump({k: res[k] for k in ('profiles', 'ground_nodes', 'node_heights', 'way_refs',
                                         'profile_report', 'elevation_report')}, f, protocol=5)
    meta = {'id': out.name, 'bbox': list(bbox), 'center': center, 'base': res['base'], 'net': 'network.net.xml',
            'pbf': str(pbf), 'timings': timings, 'ways': ways, 'nodes': nodes}
    (out / 'region.json').write_text(json.dumps(meta, indent=1), encoding='utf8')
    return meta


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--pbf', required=True)
    p.add_argument('--bbox', type=float, nargs=4, required=True, metavar=('W', 'S', 'E', 'N'))
    p.add_argument('--out', required=True)
    p.add_argument('--cache', default=str(paths.OUT / 'world' / 'cache'))
    a = p.parse_args()
    print(json.dumps(prepare(a.pbf, a.bbox, a.out, a.cache), indent=1))


if __name__ == '__main__':
    main()
