"""A driving route through a prepared region, for the autopilot of the route test.

    C:/js/Earth2Road/.venv/Scripts/python.exe earthng/route.py out/region/kyiv-ns --via 50.5070,30.4990 50.4475,30.5225 50.3920,30.5080 --out out/region/kyiv-ns/route.json

Shortest path on the region's SUMO network (passenger lanes) through the given points, as lane
centre lines in the region frame (= BeamNG x/y, metres from the region centre): on every edge
the rightmost lane that connects to the next edge, then the junction's internal lane of that
connection, so turns follow the junction curve instead of cutting the corner. Points closer than
1 m are merged.
"""
import argparse
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402,F401
import tiles  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('region')
    p.add_argument('--via', nargs='+', required=True, help='lat,lon points in order')
    p.add_argument('--out', required=True)
    a = p.parse_args()
    import sumolib
    frame = tiles.Frame(a.region)
    net = sumolib.net.readNet(str(Path(a.region) / frame.meta['net']), withInternal=True)
    stops = [tuple(map(float, v.split(','))) for v in a.via]

    def edge_near(lat, lon):
        x, y = frame.xy(lon, lat)
        for r in (30, 80, 200):
            cands = [(d, e) for e, d in net.getNeighboringEdges(x, y, r)
                     if e.allows('passenger') and e.getFunction() == '']
            if cands:
                # a major road wins over a service lane at the same distance
                return min(cands, key=lambda c: c[0] - 2 * c[1].getLaneNumber())[1]
        raise SystemExit(f'no road near {lat},{lon}')

    edges = []
    for (la0, lo0), (la1, lo1) in zip(stops, stops[1:]):
        e0, e1 = edge_near(la0, lo0), edge_near(la1, lo1)
        path, cost = net.getShortestPath(e0, e1, vClass='passenger')
        if not path:
            raise SystemExit(f'no path {la0},{lo0} -> {la1},{lo1}')
        edges += path if not edges else path[1:] if path[0] == edges[-1] else path
    pts = []

    def add(shape):
        for x, y in shape:
            q = (round(x - frame.cx, 2), round(y - frame.cy, 2))
            if not pts or math.dist(pts[-1], q) > 1.0:
                pts.append(q)
    for k, e in enumerate(edges):
        nxt = edges[k + 1] if k + 1 < len(edges) else None
        conns = sorted(e.getOutgoing().get(nxt, []), key=lambda c: c.getFromLane().getIndex()) if nxt else []
        lane = conns[0].getFromLane() if conns else next(l for l in e.getLanes() if l.allows('passenger'))
        add(lane.getShape())
        if conns:
            via = conns[0].getViaLaneID()
            while via:
                vl = net.getLane(via)
                add(vl.getShape())
                outs = vl.getOutgoing()
                via = outs[0].getViaLaneID() if outs else ''
    length = sum(math.dist(u, v) for u, v in zip(pts, pts[1:]))
    out = {'region': frame.meta['id'], 'via': stops, 'edges': [e.getID() for e in edges], 'points': pts,
           'length': round(length)}
    Path(a.out).write_text(json.dumps(out), encoding='utf8')
    print(f'{len(edges)} edges, {len(pts)} points, {length / 1000:.1f} km')


if __name__ == '__main__':
    main()
