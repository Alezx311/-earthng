"""Natural source classes shared by the country compiler and material builder.

These are ordinary v2 area kinds: the binary layout does not change. WorldCover
is a fallback, never a replacement for explicitly mapped OSM land use.
"""
VERSION = 1
NATURAL = ('meadow', 'farmland', 'orchard', 'vineyard', 'scrub', 'heath',
           'bare_rock', 'scree', 'beach', 'wetland', 'glacier', 'bare_ground')
WORLD_COVER = {10: 'forest', 20: 'scrub', 30: 'meadow', 40: 'farmland',
               60: 'bare_ground', 70: 'glacier', 80: 'water', 90: 'wetland',
               95: 'wetland', 100: 'heath'}
# Built-up (50) has no natural fallback. Class 60 does not distinguish rock/sand.
PRIORITY = ('paved', 'water', 'glacier', 'bare_rock', 'scree', 'beach', 'wetland',
            'orchard', 'vineyard', 'farmland', 'meadow', 'heath', 'scrub', 'forest', 'park', 'bare_ground', 'residential')


def partition(areas):
    """Disjoint mapped footprints prevent coplanar natural overlays in new packs.

    Clip in authoring, not in the game's frame loop. Keep original feature ids
    where possible and preserve holes. Unknown future classes pass through.
    """
    import shapely
    rank = {kind: n for n, kind in enumerate(PRIORITY)}
    claimed, result = None, []
    for feature in sorted(areas, key=lambda f: (rank.get(f['kind'], 100), f['id'])):
        if feature['kind'] not in rank:
            result.append(feature)
            continue
        rings = feature['rings']
        original = shapely.make_valid(shapely.Polygon(rings[0], rings[1:]))
        geometry = original if claimed is None else shapely.difference(original, claimed)
        claimed = original if claimed is None else shapely.union(claimed, original)
        def parts(g):
            if g.geom_type == 'Polygon':
                yield g
            elif hasattr(g, 'geoms'):
                for child in g.geoms:
                    yield from parts(child)
        if geometry.equals(original):
            result.append(feature)
            continue
        for n, part in enumerate(parts(geometry)):
            if part.is_empty or part.area < .01:
                continue
            # Heights on area boundaries are unused by the runtime; sampling
            # the final ground also avoids invalid interpolated Z at intersections.
            output = [[[float(p[0]), float(p[1]), 0.] for p in ring.coords]
                      for ring in [part.exterior, *part.interiors]]
            result.append(dict(feature, id=f"{feature['id']}:cover:{n}", rings=output))
    return result


def ground_kind(tags):
    land, natural = tags.get('landuse'), tags.get('natural')
    if natural in ('bare_rock', 'scree', 'beach', 'wetland', 'glacier', 'scrub', 'heath'):
        return natural
    if natural == 'shingle':
        return 'scree'
    if natural in ('sand', 'bare_ground'):
        return 'beach' if natural == 'sand' else 'bare_ground'
    if natural in ('grassland', 'fell') or land in ('meadow', 'grass'):
        return 'meadow'
    if land in ('farmland', 'farmyard', 'orchard', 'vineyard'):
        return 'bare_ground' if land == 'farmyard' else land
    return None
