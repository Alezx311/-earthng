"""Cut a bbox out of a country .osm.pbf into a .osm.pbf, with the selection of Earth2Road's
akadem_maps.core.osm_extract.extract() (which writes OSM XML): every way with a node inside,
restriction/multipolygon/landmark relations with a member inside and their members, all nodes
of the selected ways, POI nodes inside. The input for earthng/osm_tiles.py.

    C:/js/Earth2Road/.venv/Scripts/python.exe earthng/osm_extract_pbf.py C:/js/Earth2Road/out/districts/.inputs/geofabrik/ukraine-260918.osm.pbf data/kyiv-oblast-260918.osm.pbf --bbox 29.2,49.15,32.2,51.6
"""
import argparse
from pathlib import Path
import sys
import time

import osmium

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402,F401  (puts Earth2Road on sys.path)
from akadem_maps.core.landmarks import category as landmark_category  # noqa: E402
from akadem_maps.core.osm_extract import drop_incomplete, poi  # noqa: E402


def extract(pbf, out, bbox):
    w, s, e, n = bbox
    inside = lambda loc: loc.valid() and w <= loc.lon <= e and s <= loc.lat <= n
    t = time.monotonic()
    ways, relations, member_nodes, inside_nodes = set(), set(), set(), set()
    relation_members, relevant = {}, set()
    processor = osmium.FileProcessor(str(pbf)).with_locations().with_filter(
        osmium.filter.EmptyTagFilter().enable_for(osmium.osm.NODE))
    for obj in processor:
        if obj.is_node():
            if inside(obj.location):
                inside_nodes.add(obj.id)
                if obj.tags and (poi(obj.tags) or landmark_category(obj.tags)):
                    member_nodes.add(obj.id)
        elif obj.is_way():
            if any(inside(nd.location) for nd in obj.nodes):
                ways.add(obj.id)
        elif obj.is_relation():
            relation_members[obj.id] = [(m.type, m.ref) for m in obj.members]
            if obj.tags.get('type') in ('restriction', 'multipolygon') or landmark_category(obj.tags):
                relevant.add(obj.id)
                for member in obj.members:
                    if member.type == 'n':
                        try:
                            if inside(processor.node_location_storage.get(member.ref)):
                                inside_nodes.add(member.ref)
                        except KeyError:
                            pass
    changed = True
    while changed:
        hits = {rid for rid, members in relation_members.items() if rid in relevant and any(
            (kind == 'w' and ref in ways) or (kind == 'n' and ref in inside_nodes)
            or (kind == 'r' and ref in relations) for kind, ref in members)}
        changed = bool(hits - relations)
        relations |= hits
    pending = list(relations)
    while pending:
        rid = pending.pop()
        for kind, ref in relation_members.get(rid, []):
            if kind == 'w':
                ways.add(ref)
            elif kind == 'n':
                member_nodes.add(ref)
            elif kind == 'r' and ref not in relations:
                relations.add(ref)
                pending.append(ref)
    print(f'pass 1: {len(ways)} ways, {len(relations)} relations ({time.monotonic()-t:.0f} s)', flush=True)
    nodes = set(member_nodes)
    for obj in osmium.FileProcessor(str(pbf), osmium.osm.WAY).with_filter(osmium.filter.IdFilter(ways)):
        nodes.update(nd.ref for nd in obj.nodes)
    print(f'pass 2: {len(nodes)} nodes ({time.monotonic()-t:.0f} s)', flush=True)
    out = Path(out)
    tmp = out.with_name(out.name + '.part.osm.pbf')
    tmp.parent.mkdir(parents=True, exist_ok=True)
    written = {'n': set(), 'w': set()}
    with osmium.SimpleWriter(osmium.io.File(str(tmp), 'pbf'), overwrite=True) as writer:
        for kind, key, ids, add in ((osmium.osm.NODE, 'n', nodes, writer.add_node), (osmium.osm.WAY, 'w', ways, writer.add_way)):
            for obj in osmium.FileProcessor(str(pbf), kind).with_filter(osmium.filter.IdFilter(ids)):
                add(obj)
                written[key].add(obj.id)
        complete = drop_incomplete(relations, relation_members, written)
        if len(complete) < len(relations):
            print(f'left out {len(relations)-len(complete)} relations with members outside the package', flush=True)
        for obj in osmium.FileProcessor(str(pbf), osmium.osm.RELATION).with_filter(osmium.filter.IdFilter(complete)):
            writer.add_relation(obj)
    tmp.replace(out)
    print(f'written {out} ({time.monotonic()-t:.0f} s)', flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('pbf'); p.add_argument('out')
    p.add_argument('--bbox', required=True, help='west,south,east,north')
    a = p.parse_args()
    extract(a.pbf, a.out, [float(v) for v in a.bbox.split(',')])
