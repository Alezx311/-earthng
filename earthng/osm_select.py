"""What a cut of OSM keeps besides roads: landmark relations/nodes and point features.

The selection rules of Earth2Road's OSM extraction (landmark categories, point features,
relation completeness), kept here so that cutting tiles and running the fast generator need
nothing from Earth2Road.
"""

# Landmark categories: a relation or node with one of these tags is kept. Name alone does
# not make a point a landmark.
LANDMARK_TAGS = {'historic': {'monument', 'memorial', 'castle', 'ruins'},
                 'amenity': {'place_of_worship', 'university', 'theatre'},
                 'building': {'church', 'cathedral', 'stadium', 'train_station', 'university'},
                 'railway': {'station'}, 'shop': {'mall'}, 'leisure': {'park', 'stadium'},
                 'place': {'square'}, 'man_made': {'bridge', 'tower', 'works'},
                 'tourism': {'museum', 'attraction'}}
LANDMARK_WEIGHT = {'monument': .85, 'memorial': .55, 'castle': .85, 'ruins': .60,
                   'place_of_worship': .65, 'church': .65, 'cathedral': .85,
                   'university': .65, 'theatre': .7, 'train_station': .85, 'station': .85,
                   'mall': .65, 'park': .55, 'stadium': .75, 'square': .8, 'bridge': .75,
                   'tower': .6, 'works': .55, 'museum': .7, 'attraction': .65}

# Tagged points a way-only selection would miss (fuel stations, large shops).
POI_TAGS = {'amenity': {'fuel'},
            'shop': {'supermarket', 'hypermarket', 'mall', 'doityourself', 'department_store'}}


def landmark_category(tags):
    """The landmark category of an OSM object's tags (the weightiest), or None."""
    values = [tags.get(k) for k, choices in LANDMARK_TAGS.items() if tags.get(k) in choices]
    return max(values, key=lambda v: (LANDMARK_WEIGHT[v], v)) if values else None


def poi(tags):
    return any(tags.get(key) in values for key, values in POI_TAGS.items())


def drop_incomplete(relations, members, written):
    """Selected relation ids whose members (recursively) are all in ``written`` / the selection."""
    keep = set(relations)
    changed = True
    while changed:
        bad = {rid for rid in keep if rid not in members or any(
            (kind == 'r' and ref not in keep) or (kind in written and ref not in written[kind])
            for kind, ref in members[rid])}
        keep -= bad
        changed = bool(bad)
    return keep
