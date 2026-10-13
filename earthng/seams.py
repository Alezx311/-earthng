"""Do neighbouring streamed tiles meet? Road surfaces of two built tiles compared along their border.

    C:/js/Earth2Road/.venv/Scripts/python.exe earthng/seams.py out/region/kyiv-ns out/tiles/kyiv-ns 0 0 1 0

Both tiles contain the border band in their halo, so both worlds have full road surfaces there.
Every lane centre and wheel track inside the band (default +-20 m of the shared edge) is probed in
both worlds (Earth2Road streaming_probe.compare_surfaces); the result is the maximum height
difference and how many probes differ by more than 5 cm or are missing on one side.
"""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402,F401
import tiles  # noqa: E402


def border_band(frame, a, b, width):
    (i0, j0), (i1, j1) = a, b
    if abs(i0 - i1) + abs(j0 - j1) != 1:
        raise SystemExit('tiles must share an edge')
    r0, r1 = frame.rect(i0, j0), frame.rect(i1, j1)
    if i0 != i1:
        x = max(r0[0], r1[0])
        rect = (x - width, r0[1], x + width, r0[3])
    else:
        y = max(r0[1], r1[1])
        rect = (r0[0], y - width, r0[2], y + width)
    # inner box in lon/lat: corners of the band, shrunk to stay inside both halos
    return frame.geo_bbox(rect)


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('region')
    p.add_argument('work', help='tile work folder (<work>/<i>_<j>/world)')
    p.add_argument('tiles', type=int, nargs=4, metavar=('I0', 'J0', 'I1', 'J1'))
    p.add_argument('--width', type=float, default=20.0)
    a = p.parse_args()
    from akadem_maps.streaming_probe import compare_surfaces
    frame = tiles.Frame(a.region)
    t0, t1 = tuple(a.tiles[:2]), tuple(a.tiles[2:])
    bbox = border_band(frame, t0, t1, a.width)
    w0 = Path(a.work) / f'{t0[0]}_{t0[1]}' / 'world'
    w1 = Path(a.work) / f'{t1[0]}_{t1[1]}' / 'world'
    res = compare_surfaces(w0, w1, bbox=bbox)
    res2 = compare_surfaces(w1, w0, bbox=bbox)
    out = {'tiles': [t0, t1], 'band_m': a.width, 'bbox': bbox, 'a_vs_b': res, 'b_vs_a': res2}
    print(json.dumps(out, indent=1, default=str)[:4000])


if __name__ == '__main__':
    main()
