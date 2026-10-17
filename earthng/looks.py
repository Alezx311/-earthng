"""Which assets a country pack asks the planet mod to use.

The pack stores short ids. Meshes and textures stay in earthng_planet.zip and in
the BeamNG install. A block is a shared default for places whose streets look
alike (post-Soviet panel housing, for one). The user can override any slot.
"""
from __future__ import annotations


# slot id -> {choice id: label shown in the window}
SLOTS = {
    'asphalt': {
        'smooth': 'Smooth',
        'worn': 'Worn',
        'fresh': 'Fresh',
    },
    'markings': {
        'white': 'White',
        'yellow': 'Yellow',
        'none': 'No markings',
    },
    'verge': {
        'grass': 'Grass',
        'dirt': 'Dirt',
        'dry': 'Dry grass',
        'gravel': 'Gravel',
    },
    'buildings': {
        'panel': 'Panel and loggias',
        'brick': 'Brick',
        'plaster': 'Plaster',
        'mixed': 'Mixed',
    },
    'trees': {
        'regional': 'By latitude',
        'broadleaf': 'Broadleaf',
        'mixed': 'Mixed',
        'conifer': 'Conifer',
    },
    'statics': {
        'street': 'Lamps and stops',
        'sparse': 'Stops only',
        'none': 'No street props',
    },
}

SLOT_LABELS = {
    'asphalt': 'Asphalt',
    'markings': 'Markings',
    'verge': 'Verge',
    'buildings': 'Untagged buildings',
    'trees': 'Trees',
    'statics': 'Street props',
}

# (key, label, default). Roads and the elevation grid are always in the pack.
LAYERS = (
    ('buildings', 'Buildings', True),
    ('water', 'Water', True),
    ('cover', 'Forest, field, park, yard', True),
    ('barriers', 'Fences and walls', True),
    ('treeRows', 'Tree rows', True),
    ('stops', 'Stops in the data', True),
)


def _block(block_id, title, ids, **look):
    missing = set(SLOTS) - set(look)
    extra = set(look) - set(SLOTS)
    if missing or extra:
        raise ValueError(f'{block_id}: slots {missing or extra}')
    for slot, value in look.items():
        if value not in SLOTS[slot]:
            raise ValueError(f'{block_id}.{slot}: {value}')
    return {'id': block_id, 'title': title, 'ids': frozenset(ids), 'look': dict(look)}


# Leaf id wins, then its parents. Ids are Geofabrik extract ids, not translated names.
# A slash id such as us/georgia contributes its prefix (us), not its last segment,
# so the US state does not inherit the country of Georgia.
BLOCKS = (
    _block('postsoviet', 'Post-Soviet',
           ('ukraine', 'belarus', 'moldova', 'russia', 'georgia', 'armenia', 'azerbaijan',
            'kazakhstan', 'uzbekistan', 'kyrgyzstan', 'tajikistan', 'turkmenistan',
            'estonia', 'latvia', 'lithuania', 'romania', 'bulgaria'),
           asphalt='worn', markings='white', verge='grass', buildings='panel',
           trees='regional', statics='street'),
    _block('nordic', 'Nordic',
           ('sweden', 'norway', 'finland', 'denmark', 'iceland', 'faroe-islands', 'greenland'),
           asphalt='fresh', markings='white', verge='grass', buildings='brick',
           trees='conifer', statics='street'),
    _block('british', 'British',
           ('united-kingdom', 'great-britain', 'ireland', 'britain-and-ireland', 'isle-of-man',
            'guernsey-jersey', 'ireland-and-northern-ireland'),
           asphalt='worn', markings='white', verge='grass', buildings='brick',
           trees='broadleaf', statics='street'),
    _block('western', 'Western Europe',
           ('germany', 'france', 'netherlands', 'belgium', 'austria', 'switzerland',
            'luxembourg', 'liechtenstein', 'monaco', 'andorra', 'alps', 'dach'),
           asphalt='smooth', markings='white', verge='grass', buildings='mixed',
           trees='regional', statics='street'),
    _block('central', 'Central Europe',
           ('poland', 'czech-republic', 'slovakia', 'hungary', 'croatia', 'slovenia',
            'serbia', 'bosnia-herzegovina', 'montenegro', 'macedonia', 'kosovo', 'albania'),
           asphalt='worn', markings='white', verge='grass', buildings='mixed',
           trees='regional', statics='street'),
    _block('mediterranean', 'Mediterranean',
           ('italy', 'spain', 'portugal', 'greece', 'turkey', 'cyprus', 'malta', 'azores'),
           asphalt='worn', markings='white', verge='dry', buildings='plaster',
           trees='broadleaf', statics='street'),
    _block('north-america', 'North America',
           ('us', 'canada', 'us-midwest', 'us-northeast', 'us-pacific', 'us-south', 'us-west'),
           asphalt='fresh', markings='yellow', verge='grass', buildings='mixed',
           trees='regional', statics='street'),
    _block('latin', 'Latin America',
           ('mexico', 'south-america', 'central-america'),
           asphalt='worn', markings='white', verge='grass', buildings='plaster',
           trees='broadleaf', statics='street'),
    _block('east-asia', 'East Asia',
           ('japan', 'china', 'south-korea', 'north-korea', 'taiwan', 'mongolia'),
           asphalt='smooth', markings='white', verge='grass', buildings='mixed',
           trees='regional', statics='street'),
    _block('south-asia', 'South Asia',
           ('india', 'pakistan', 'bangladesh', 'sri-lanka', 'nepal', 'bhutan', 'maldives'),
           asphalt='worn', markings='white', verge='dirt', buildings='plaster',
           trees='broadleaf', statics='street'),
    _block('southeast-asia', 'Southeast Asia',
           ('indonesia', 'philippines', 'thailand', 'vietnam', 'cambodia', 'laos', 'myanmar',
            'malaysia-singapore-brunei', 'east-timor', 'sea'),
           asphalt='worn', markings='white', verge='grass', buildings='mixed',
           trees='broadleaf', statics='street'),
    _block('middle-east', 'Middle East',
           ('iran', 'iraq', 'israel-and-palestine', 'jordan', 'lebanon', 'syria', 'yemen',
            'gcc-states', 'afghanistan'),
           asphalt='worn', markings='white', verge='dirt', buildings='plaster',
           trees='broadleaf', statics='sparse'),
    _block('africa', 'Africa',
           ('africa',),
           asphalt='worn', markings='white', verge='dirt', buildings='plaster',
           trees='broadleaf', statics='sparse'),
    _block('oceania', 'Oceania',
           ('australia-oceania', 'australia', 'new-zealand'),
           asphalt='smooth', markings='white', verge='grass', buildings='brick',
           trees='broadleaf', statics='street'),
    _block('general', 'General',
           (),
           asphalt='smooth', markings='white', verge='grass', buildings='mixed',
           trees='regional', statics='street'),
)

BLOCK_BY_ID = {block['id']: block for block in BLOCKS}


def tokens(place_id, by_id):
    """Geofabrik id, its slash-prefix, then parents. Order is most specific first."""
    out, seen, current = [], set(), place_id
    while current and current not in seen:
        seen.add(current)
        out.append(current)
        if '/' in current:
            prefix = current.split('/', 1)[0]
            if prefix not in seen:
                seen.add(prefix)
                out.append(prefix)
        parent = (by_id.get(current) or {}).get('parent') or ''
        current = parent or None
    return out


def block_for(place_id, by_id):
    """The first block that names this extract or an ancestor. Unknown places use general."""
    for token in tokens(place_id, by_id):
        for block in BLOCKS:
            if token in block['ids']:
                return block
    return BLOCK_BY_ID['general']


def recommended(place_id, by_id):
    """Default look for a Geofabrik extract."""
    block = block_for(place_id, by_id)
    return canonical({'block': block['id'], **block['look']})


def canonical(look):
    """A manifest look: block id plus one known choice per slot."""
    if not isinstance(look, dict):
        raise ValueError('look must be an object')
    block = BLOCK_BY_ID.get(look.get('block') or '')
    if block is None:
        raise ValueError(f"unknown asset block: {look.get('block')}")
    out = {'block': block['id']}
    for slot, choices in SLOTS.items():
        value = look.get(slot) or block['look'][slot]
        if value not in choices:
            raise ValueError(f'unknown {slot}: {value}')
        out[slot] = value
    return out


def canonical_include(include):
    """Layer switches. Missing keys stay at their default. Roads are not a switch."""
    if include is None:
        return None
    if not isinstance(include, dict):
        raise ValueError('include must be an object')
    out = {}
    allowed = {key for key, _label, _default in LAYERS}
    unknown = set(include) - allowed
    if unknown:
        raise ValueError(f'unknown layers: {", ".join(sorted(unknown))}')
    for key, _label, default in LAYERS:
        value = include.get(key, default)
        if not isinstance(value, bool):
            raise ValueError(f'{key} must be true or false')
        out[key] = value
    return out


def keep_scenery(kind, include):
    """False drops one OSM scenery kind from the cell. Roads always stay."""
    if not include:
        return True
    if kind == 'building':
        return bool(include.get('buildings', True))
    if kind == 'water':
        return bool(include.get('water', True))
    if kind in ('fence', 'wall'):
        return bool(include.get('barriers', True))
    if kind == 'tree_row':
        return bool(include.get('treeRows', True))
    if kind == 'stop':
        return bool(include.get('stops', True))
    if kind in ('path', 'signal', 'road', 'junction'):
        return True
    return bool(include.get('cover', True))
