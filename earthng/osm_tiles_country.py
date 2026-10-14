"""Cut a country .osm.pbf (Ukraine: 877 MB) into the static tile archive of earthng/osm_tiles.py.

    C:/js/Earth2Road/.venv/Scripts/python.exe earthng/osm_tiles_country.py C:/js/Earth2Road/out/districts/.inputs/geofabrik/ukraine-260918.osm.pbf --out data/osm_tiles/ukraine-260918

osm_tiles.cut keeps a tile bitmask per node: fine for an oblast (2725 tiles), not for the ~25 000
tiles of a country. One extract (osm_extract_pbf.py) reads the whole country in ~6.5 min, so
blocks are not cut from the country one by one either. Instead a tree: the country is extracted
into groups of at most FANOUT blocks, each group into its blocks, and each block (BLOCK degrees,
aligned to the cell grid) is cut by osm_tiles.cut with the block's buffered box as the package
box. An extract of an extract selects the same data (every way touching the child box touches the
parent box and is complete there), and a block's package box admits exactly its own cells, so the
tiles equal those of one cut of the whole country.

Resumable: a finished block leaves <work>/blocks/<bx>_<by>/index.json; extracts are deleted once
their children are done. The final index.json lists every tile, as osm_tiles.cut writes it.
"""
import argparse
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
import json
import math
from pathlib import Path
import shutil
import sys
import time

import osmium

sys.path.insert(0, str(Path(__file__).resolve().parent))
import osm_tiles  # noqa: E402
from osm_tiles import BUFFER, CELL  # noqa: E402

FANOUT = 16


def block_box(cells, package_box):
    """Buffered box of a range of cells, clipped to the package (osm_tiles.coverage's rounding)."""
    x0, y0, x1, y1 = cells
    pw, ps, pe, pn = package_box
    return [max(pw, round(x0 * CELL - BUFFER, 6)), max(ps, round(y0 * CELL - BUFFER, 6)),
            min(pe, round(x1 * CELL + BUFFER, 6)), min(pn, round(y1 * CELL + BUFFER, 6))]


def split(n, parts):
    """n items into `parts` runs of near-equal length, as (start, stop)."""
    edges = [round(i * n / parts) for i in range(parts + 1)]
    return [(a, b) for a, b in zip(edges, edges[1:]) if b > a]


def plan(package_box, block):
    """Tree of extracts: {'box', 'blocks': [(bx, by)], 'children': [...]}; leaves are single blocks."""
    step = round(block / CELL)
    pw, ps, pe, pn = package_box
    bx0, by0 = math.floor(pw / block), math.floor(ps / block)
    bx1, by1 = math.floor(pe / block) + 1, math.floor(pn / block) + 1

    def cells(bxa, bya, bxb, byb):
        return bxa * step, bya * step, bxb * step, byb * step

    def node(bxa, bya, bxb, byb):
        out = {'box': block_box(cells(bxa, bya, bxb, byb), package_box),
               'blocks': [(x, y) for x in range(bxa, bxb) for y in range(bya, byb)]}
        nx, ny = bxb - bxa, byb - bya
        if nx * ny == 1:
            out['children'] = []
        elif nx * ny <= FANOUT:
            out['children'] = [node(x, y, x + 1, y + 1) for x in range(bxa, bxb) for y in range(bya, byb)]
        else:
            per = math.isqrt(FANOUT)
            out['children'] = [node(bxa + a, bya + c, bxa + b, bya + d)
                               for a, b in split(nx, math.ceil(nx / per)) for c, d in split(ny, math.ceil(ny / per))]
        return out
    return node(bx0, by0, bx1, by1)


def name(n):
    x0, y0 = min(n['blocks']); x1, y1 = max(n['blocks'])
    return f'{x0}_{y0}' if (x0, y0) == (x1, y1) else f'{x0}_{y0}-{x1}_{y1}'


def run_extract(src, dst, box):
    import osm_extract_pbf
    if not dst.exists():
        osm_extract_pbf.extract(src, dst, box)
    return dst


def run_cut(src, block_dir, box):
    """Cut one block; the per-block index.json is the done marker, written last."""
    tmp = block_dir.with_name(block_dir.name + '.part')
    if tmp.exists():
        shutil.rmtree(tmp)
    osm_tiles.cut(src, tmp, box)
    tmp.replace(block_dir)
    return block_dir


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('pbf', type=Path)
    p.add_argument('--out', type=Path, required=True, help='new directory: the tile archive')
    p.add_argument('--work', type=Path, help='intermediate extracts and blocks (default <out>.work)')
    p.add_argument('--block', type=float, default=1.5, help='block side in degrees, a multiple of the cell')
    p.add_argument('--jobs', type=int, default=4)
    p.add_argument('--package-box', type=float, nargs=4, metavar=('W', 'S', 'E', 'N'))
    a = p.parse_args()
    if a.out.exists():
        raise SystemExit(f'{a.out} exists')
    if abs(a.block / CELL - round(a.block / CELL)) > 1e-9:
        raise SystemExit(f'--block must be a multiple of {CELL}')
    t0 = time.monotonic()
    package_box = a.package_box
    if package_box is None:
        box = osmium.io.Reader(str(a.pbf), osmium.osm.osm_entity_bits.NOTHING).header().box()
        package_box = [box.bottom_left.lon, box.bottom_left.lat, box.top_right.lon, box.top_right.lat]
    work = a.work or a.out.with_name(a.out.name + '.work')
    (work / 'blocks').mkdir(parents=True, exist_ok=True)
    (work / 'extracts').mkdir(exist_ok=True)
    root = plan(package_box, a.block)

    def leaves(n):
        return [n] if not n['children'] else [l for c in n['children'] for l in leaves(c)]

    def done(n):
        return all((work / 'blocks' / name(l) / 'index.json').exists() for l in leaves(n))

    total = len(leaves(root))
    print(f'{total} blocks of {a.block} deg, package {package_box}', flush=True)
    with ProcessPoolExecutor(a.jobs) as pool:
        running = {}

        def start(n, src):
            """n's data is in src: cut it (a block) or extract its children from it."""
            if done(n):
                return
            if not n['children']:
                f = pool.submit(run_cut, src, work / 'blocks' / name(n), n['box'])
                running[f] = (n, src, 'cut')
            else:
                for c in n['children']:
                    if not done(c):
                        dst = work / 'extracts' / f'{name(c)}.osm.pbf'
                        f = pool.submit(run_extract, src, dst, c['box'])
                        running[f] = (c, src, 'extract')
            n['pending'] = sum(1 for c in n['children'] if not done(c))

        parents = {}

        def index_parents(n, src):
            for c in n['children']:
                parents[id(c)] = (n, src)
                index_parents(c, work / 'extracts' / f'{name(c)}.osm.pbf')
        index_parents(root, a.pbf)

        def finished(n):
            """n and its subtree are done: its extract is no longer read; tell the parent."""
            src = work / 'extracts' / f'{name(n)}.osm.pbf'
            if src.exists():
                src.unlink()
            parent = parents.get(id(n))
            if parent:
                pn, _ = parent
                pn['pending'] -= 1
                if pn['pending'] == 0:
                    finished(pn)

        start(root, a.pbf)
        blocks_done = sum(1 for l in leaves(root) if done(l))
        while running:
            ready, _ = wait(running, return_when=FIRST_COMPLETED)
            for f in ready:
                n, src, kind = running.pop(f)
                f.result()
                if kind == 'extract':
                    n['pending'] = 0
                    start(n, work / 'extracts' / f'{name(n)}.osm.pbf')
                else:
                    blocks_done += 1
                    print(f'block {name(n)} cut: {blocks_done}/{total} ({time.monotonic()-t0:.0f} s)', flush=True)
                    finished(n)

    index = {'cell': CELL, 'buffer': BUFFER, 'source': a.pbf.name, 'package_box': package_box,
             'block': a.block, 'tiles': {}}
    tmp = a.out.with_name(a.out.name + '.part')
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    for leaf in leaves(root):
        block = work / 'blocks' / name(leaf)
        part = json.loads((block / 'index.json').read_text(encoding='utf8'))
        for key, tile in part['tiles'].items():
            if key in index['tiles']:
                raise SystemExit(f'tile {key} in two blocks')
            (block / tile['file']).replace(tmp / tile['file'])
            index['tiles'][key] = tile
    index['tiles'] = dict(sorted(index['tiles'].items()))
    index['seconds'] = round(time.monotonic() - t0, 1)
    (tmp / 'index.json').write_text(json.dumps(index, indent=1), encoding='utf8')
    tmp.replace(a.out)
    shutil.rmtree(work)
    sizes = [t['bytes'] for t in index['tiles'].values()]
    print(f"written {len(sizes)} tiles to {a.out}, {sum(sizes)/2**30:.2f} GB total, "
          f"max {max(sizes)/2**20:.2f} MB ({index['seconds']} s)", flush=True)


if __name__ == '__main__':
    main()
