"""Look of the fast generator: materials from the game's own tileable textures, UV scale per
surface kind, and a facade per building. No textures are shipped; all paths are BeamNG's
/assets (resolved to their .dds). Kinds are the Mesh face groups; material = 'e2rf_' + kind."""
import math
import zlib

TEXTURES = {
    'asphalt': {
        'baseColorMap': '/assets/materials/tileable/road/m_road_asphalt_rough_01/t_road_asphalt_rough_01_b.color.png',
        'normalMap': '/assets/materials/tileable/road/m_road_asphalt_rough_01/t_road_asphalt_rough_01_nm.normal.png',
        'roughnessMap': '/assets/materials/tileable/road/m_road_asphalt_rough_01/t_road_asphalt_rough_01_r.data.png',
        'ambientOcclusionMap': '/assets/materials/tileable/road/m_road_asphalt_rough_01/t_road_asphalt_rough_01_ao.data.png',
    },
    'asphalt_major': {
        'baseColorMap': '/assets/materials/tileable/road/m_asphalt_new_01/t_asphalt_02_b.color.png',
        'normalMap': '/assets/materials/tileable/road/m_asphalt_new_01/t_asphalt_02_nm.normal.png',
        'ambientOcclusionMap': '/assets/materials/tileable/road/m_asphalt_new_01/t_asphalt_02_ao.data.png',
    },
    'sidewalk': {
        'baseColorMap': '/assets/materials/tileable/concrete/sidewalk1/t_sidewalk1_b.color.png',
        'normalMap': '/assets/materials/tileable/concrete/sidewalk1/t_sidewalk1_nm.normal.png',
        'roughnessMap': '/assets/materials/tileable/concrete/sidewalk1/t_sidewalk1_r.data.png',
    },
    'grass': {
        'baseColorMap': '/assets/materials/terrain/grass/groundmesh_grass2/groundmesh_grass_b.color.png',
    },
    'concrete': {
        'baseColorMap': '/assets/materials/tileable/concrete/concrete_plain/t_concrete_plain_b.color.png',
        'normalMap': '/assets/materials/tileable/concrete/concrete_plain/t_concrete_plain_nm.normal.png',
        'roughnessMap': '/assets/materials/tileable/concrete/concrete_plain/t_concrete_plain_r.data.png',
        'ambientOcclusionMap': '/assets/materials/tileable/concrete/concrete_plain/t_concrete_plain_ao.data.png',
    },
    'panel': {
        'baseColorMap': '/assets/materials/tileable/concrete/slabs_brutalist/t_slabs_brutalist_b.color.png',
        'normalMap': '/assets/materials/tileable/concrete/slabs_brutalist/t_slabs_brutalist_nm.normal.png',
        'roughnessMap': '/assets/materials/tileable/concrete/slabs_brutalist/t_slabs_brutalist_r.data.png',
        'ambientOcclusionMap': '/assets/materials/tileable/concrete/slabs_brutalist/t_slabs_brutalist_ao.data.png',
    },
    'precast': {
        'baseColorMap': '/assets/materials/tileable/concrete/concrete_precast1/t_concrete_precast1_b.color.png',
        'normalMap': '/assets/materials/tileable/concrete/concrete_precast1/t_concrete_precast1_nm.normal.png',
        'roughnessMap': '/assets/materials/tileable/concrete/concrete_precast1/t_concrete_precast1_r.data.png',
        'ambientOcclusionMap': '/assets/materials/tileable/concrete/concrete_precast1/t_concrete_precast1_ao.data.png',
    },
    'brick': {
        'baseColorMap': '/assets/materials/tileable/brick/brick_plain/t_brick_plain_b.color.png',
        'normalMap': '/assets/materials/tileable/brick/brick_plain/t_bricks_red_01_nm.normal.png',
        'roughnessMap': '/assets/materials/tileable/brick/brick_plain/t_bricks_red_01_r.data.png',
        'ambientOcclusionMap': '/assets/materials/tileable/brick/brick_plain/t_bricks_red_01_ao.data.png',
    },
    'brick_red': {
        'baseColorMap': '/assets/materials/tileable/brick/brick_plain/t_bricks_red_01_b.color.png',
        'normalMap': '/assets/materials/tileable/brick/brick_plain/t_bricks_red_01_nm.normal.png',
        'roughnessMap': '/assets/materials/tileable/brick/brick_plain/t_bricks_red_01_r.data.png',
        'ambientOcclusionMap': '/assets/materials/tileable/brick/brick_plain/t_bricks_red_01_ao.data.png',
    },
    'brick_white': {
        'baseColorMap': '/assets/materials/tileable/brick/t_brick_white_paint_01/t_brick_white_paint_01_b.color.png',
        'normalMap': '/assets/materials/tileable/brick/t_brick_white_paint_01/t_brick_white_paint_01_nm.normal.png',
        'roughnessMap': '/assets/materials/tileable/brick/t_brick_white_paint_01/t_brick_white_paint_01_r.data.png',
        'ambientOcclusionMap': '/assets/materials/tileable/brick/t_brick_white_paint_01/t_brick_white_paint_01_ao.data.png',
    },
    'plaster': {
        'baseColorMap': '/assets/materials/tileable/plaster/m_plaster_worn_01/t_plaster_worn_01_b.color.png',
        'normalMap': '/assets/materials/tileable/plaster/m_plaster_worn_01/t_plaster_worn_01_nm.normal.png',
        'roughnessMap': '/assets/materials/tileable/plaster/m_plaster_worn_01/t_plaster_worn_01_r.data.png',
        'ambientOcclusionMap': '/assets/materials/tileable/plaster/m_plaster_worn_01/t_plaster_worn_01_ao.data.png',
    },
    'stucco': {
        'baseColorMap': '/assets/materials/tileable/plaster/m_stucco_white/t_stucco_white_b.color.png',
        'normalMap': '/assets/materials/tileable/plaster/m_stucco_white/t_stucco_white_nm.normal.png',
        'roughnessMap': '/assets/materials/tileable/plaster/m_stucco_white/t_stucco_white_r.data.png',
        'ambientOcclusionMap': '/assets/materials/tileable/plaster/m_stucco_white/t_stucco_white_ao.data.png',
    },
    'glass': {
        'baseColorMap': '/assets/materials/tileable/glass/modernstripwindow/t_window_strips_01_b.color.png',
        'normalMap': '/assets/materials/tileable/glass/modernstripwindow/t_window_strips_01_nm.normal.png',
        'roughnessMap': '/assets/materials/tileable/glass/modernstripwindow/t_window_strips_01_r.data.png',
    },
    'windows': {
        'baseColorMap': '/assets/materials/tileable/glass/windows1/t_bld_ind_windows_b.color.png',
        'normalMap': '/assets/materials/tileable/glass/windows1/t_bld_ind_windows_nm.normal.png',
        'roughnessMap': '/assets/materials/tileable/glass/windows1/t_bld_ind_windows_r.data.png',
    },
    'block': {
        'baseColorMap': '/assets/materials/tileable/brick/ind_blockwall/t_concrete_cinderblock_01_b.color.png',
        'normalMap': '/assets/materials/tileable/brick/ind_blockwall/t_concrete_cinderblock_01_nm.normal.png',
        'roughnessMap': '/assets/materials/tileable/brick/ind_blockwall/t_concrete_cinderblock_01_r.data.png',
        'ambientOcclusionMap': '/assets/materials/tileable/brick/ind_blockwall/t_concrete_cinderblock_01_ao.data.png',
    },
    'roof': {
        'baseColorMap': '/assets/materials/tileable/roofing/asphalt_roof/t_asphalt_roof_b.color.png',
        'normalMap': '/assets/materials/tileable/roofing/asphalt_roof/t_asphalt_roof_nm.normal.png',
        'roughnessMap': '/assets/materials/tileable/roofing/asphalt_roof/t_asphalt_roof_r.data.png',
        'ambientOcclusionMap': '/assets/materials/tileable/roofing/asphalt_roof/t_asphalt_roof_ao.data.png',
    },
    'roof_tile': {
        'baseColorMap': '/assets/materials/tileable/roofing/t_italy_bld_roof_tiles/t_italy_bld_roof_tiles_b.color.png',
        'normalMap': '/assets/materials/tileable/roofing/t_italy_bld_roof_tiles/t_italy_bld_roof_tiles_nm.normal.png',
        'roughnessMap': '/assets/materials/tileable/roofing/t_italy_bld_roof_tiles/t_italy_bld_roof_tiles_r.data.png',
    },
    'roof_metal': {
        'baseColorMap': '/assets/materials/tileable/metal/corrugated_modern/t_metal_corrugated_01_b.color.png',
        'normalMap': '/assets/materials/tileable/metal/corrugated_modern/t_metal_corrugated_01_nm.normal.png',
        'ambientOcclusionMap': '/assets/materials/tileable/metal/corrugated_modern/t_metal_corrugated_01_ao.data.png',
    },
    'dirt': {
        'baseColorMap': '/assets/materials/terrain/soil/dirt_crumbly/t_dirt_crumbly_b.color.png',
        'normalMap': '/assets/materials/terrain/soil/dirt_crumbly/t_dirt_crumbly_nm.normal.png',
        'roughnessMap': '/assets/materials/terrain/soil/dirt_crumbly/t_dirt_crumbly_r.data.png',
        'ambientOcclusionMap': '/assets/materials/terrain/soil/dirt_crumbly/t_dirt_crumbly_ao.data.png',
    },
}

# kind: (texture, fallback colour, groundType, metres per texture repeat, uv mode)
# uv mode 'plan' projects from above; 'wall' runs u along the wall and v up it.
KINDS = {
    'road':        ('asphalt',       [.18, .19, .20, 1], 'ASPHALT', 6.0, 'plan'),
    'road_major':  ('asphalt_major', [.16, .17, .18, 1], 'ASPHALT', 6.0, 'plan'),
    'sidewalk':    ('sidewalk',      [.55, .55, .52, 1], 'ASPHALT', 3.0, 'plan'),
    'mark':        (None,            [.92, .91, .86, 1], 'ASPHALT', 1.0, 'plan'),
    'ground':      ('grass',         [.29, .36, .22, 1], 'GRASS',   8.0, 'plan'),
    'paved':       ('concrete',      [.45, .45, .43, 1], 'ASPHALT', 6.0, 'plan'),
    'water':       (None,            [.12, .29, .38, 1], 'MUD',     8.0, 'plan'),
    'roof':        ('roof',          [.32, .31, .30, 1], 'ROCK',    6.0, 'plan'),
    'forest':      ('grass',         [.20, .30, .16, 1], 'GRASS',   8.0, 'plan'),
    'curb':        ('concrete',      [.70, .70, .68, 1], 'ASPHALT', 1.0, 'plan'),
}
ART = '/e2r_stream/fast-art/v10'      # fast_art.ensure writes the facades there
WALLS = ('panel', 'precast', 'brick', 'brick_red', 'brick_white', 'plaster', 'stucco', 'glass', 'windows', 'block')
for w in WALLS:
    # walls carry their own UVs (one 3 m window cell per repeat, floors from the base)
    KINDS['wall_' + w] = (w, [.58, .55, .49, 1], 'ROCK', 3.0, 'wall')
# photographs written into the art folder by fast_art (ambientCG paving and earth, Panelka fence)
TEXTURES['paving'] = {'baseColorMap': f'{ART}/real/paving.color.png', 'normalMap': f'{ART}/real/paving.normal.png'}
TEXTURES['trodden'] = {'baseColorMap': f'{ART}/real/trodden.color.png', 'normalMap': f'{ART}/real/trodden.normal.png'}
TEXTURES['fence_po2'] = {'baseColorMap': f'{ART}/real/fence_po2.color.png'}
TEXTURES['fence_mesh'] = {
    'baseColorMap': '/assets/materials/tileable/metal/chainlink/t_chainlink_fence_b.color.png',
    'normalMap': '/assets/materials/tileable/metal/chainlink/t_chainlink_fence_nm.normal.png',
    'opacityMap': f'{ART}/real/chainlink_o.data.png',
}
CHAINLINK_OPACITY = '/assets/materials/tileable/metal/chainlink/t_chainlink_fence_o.data.png'
TEXTURES['car'] = {'baseColorMap': f'{ART}/real/cars.color.png'}
# material fields beyond the stage: see-through fences, both sides of a single quad
EXTRA = {'fence_mesh': {'alphaTest': True, 'alphaRef': 80, 'doubleSided': True, 'castShadows': False},
         'fence_wall': {'doubleSided': True}}
# photo facades (fast_photo: Panelka pack): a 6 m repeat, the II-68 loggia sheet 33 m
import fast_photo as _photo
for w, (_family, _colour, _metres) in _photo.PHOTO_WALLS.items():
    KINDS['wall_' + w] = (None, [*_colour, 1], 'ROCK', _metres, 'wall')
# roofs: the parapet's inner face and pitched tile roofs of houses
KINDS['parapet'] = ('concrete', [.55, .55, .53, 1], 'ROCK', 3.0, 'wall')
KINDS['roof_tile'] = ('roof_tile', [.45, .22, .16, 1], 'ROCK', 4.0, 'plan')
KINDS['roof_metal'] = ('roof_metal', [.36, .42, .40, 1], 'ROCK', 4.0, 'plan')
# footpaths and fences (decoration, no collision)
KINDS['footway'] = ('paving', [.50, .48, .46, 1], 'ASPHALT', 2.0, 'plan')
KINDS['footway_asphalt'] = ('asphalt', [.20, .21, .22, 1], 'ASPHALT', 6.0, 'plan')
KINDS['trodden'] = ('trodden', [.42, .36, .28, 1], 'DIRT', 3.0, 'plan')
KINDS['fence_wall'] = ('fence_po2', [.62, .62, .60, 1], 'ROCK', 2.5, 'wall')
KINDS['fence_mesh'] = ('fence_mesh', [.30, .32, .30, 1], 'METAL', 2.0, 'wall')
KINDS['car'] = ('car', [.6, .6, .62, 1], 'METAL', 1.0, 'wall')       # parked cars carry their own UVs
# the photo ground covers a 24 m sheet (fast_photo.GROUND_M)
for _k in ('ground', 'forest'):
    KINDS[_k] = KINDS[_k][:3] + (_photo.GROUND_M,) + KINDS[_k][4:]
KINDS['park'] = ('grass', [.30, .40, .22, 1], 'GRASS', _photo.GROUND_M, 'plan')


# baseColorFactor per kind: the game's grass is dry West Coast grass; Kyiv's is green
TINT = {'ground': [.55, .85, .42, 1], 'forest': [.36, .58, .28, 1], 'paved': [.62, .62, .60, 1]}

# OSM areas that change the ground under them (anything else stays grass)
PAVED = {'industrial', 'commercial', 'retail', 'railway', 'garages', 'construction', 'depot'}
WOODS = {'forest', 'wood'}


PARKS = {'park', 'garden', 'recreation_ground', 'cemetery', 'village_green'}


def ground_kind(tags):
    """'paved', 'forest', 'park', 'residential' or None for an OSM area's tags."""
    if tags.get('landuse') in PAVED or tags.get('amenity') == 'parking' or tags.get('place') == 'square':
        return 'paved'
    if tags.get('landuse') in WOODS or tags.get('natural') in WOODS:
        return 'forest'
    if tags.get('leisure') in PARKS or tags.get('landuse') in PARKS:
        return 'park'
    if tags.get('landuse') == 'residential':
        return 'residential'          # yards: trees between the blocks, ground stays grass
    return None


PATHS = {'footway': 2.0, 'path': 1.2, 'pedestrian': 4.0, 'cycleway': 2.0, 'steps': 2.0, 'bridleway': 1.5,
         'track': 3.0}
UNPAVED = {'unpaved', 'dirt', 'ground', 'earth', 'grass', 'sand', 'gravel', 'fine_gravel', 'compacted', 'mud', 'wood'}


def line_kind(tags):
    """'path', 'fence' or 'tree_row' for an OSM way drawn as a line, else None."""
    h = tags.get('highway')
    if h in PATHS and tags.get('area') != 'yes' and tags.get('footway') not in ('sidewalk', 'crossing')             and tags.get('bridge') in (None, 'no') and tags.get('tunnel') in (None, 'no') and tags.get('layer', '0') in ('0', ''):
        return 'path'
    if tags.get('natural') == 'tree_row':
        return 'tree_row'
    if tags.get('barrier') in ('fence', 'wall'):
        return 'fence'
    return None


def point_kind(tags):
    """'stop' or 'signal' for an OSM node, else None."""
    # traffic signals: the game's trafficlight_city1.dae is a head without a pole (it lay at the
    # curb, vis-13-signals); placing is written (fast.add_street_points), off until a pole model
    if tags.get('highway') == 'traffic_signals' and SIGNALS:
        return 'signal'
    if tags.get('highway') == 'bus_stop' or (tags.get('public_transport') == 'platform' and
                                              any(tags.get(k) == 'yes' for k in ('bus', 'trolleybus', 'tram'))):
        return 'stop'
    return None


SIGNALS = False
BUS_STOP = '/art/shapes/objects/s_busstop_jri.dae'
TRAFFIC_LIGHT = '/art/shapes/objects/trafficlight_city1.dae'
# the shapes' own facing: the yaw that turns their front to +x of the world (measured in game)
BUS_STOP_YAW = math.pi       # vis-12-stops: the open side faced away from the road
TRAFFIC_LIGHT_YAW = 0.0


def path_kind(tags):
    """Material of a footpath: paving tiles in the city, trodden earth on paths and tracks."""
    surface = tags.get('surface')
    if surface in UNPAVED or (surface is None and tags.get('highway') in ('path', 'track', 'bridleway')):
        return 'trodden'
    if surface == 'asphalt' or tags.get('highway') == 'cycleway':
        return 'footway_asphalt'
    return 'footway'


# the game's own tree shapes (precompiled .cdae next to each .dae); no collision, so a forest
# adds nothing to be:reloadCollision
TREES = ['/assets/meshes/foliage/trees_library/beech/tree_beech_forest_a.dae',
         '/assets/meshes/foliage/trees_library/beech/tree_beech_small_b.dae',
         '/assets/meshes/foliage/trees_library/birch/tree_aspen_forest_a.dae',
         '/assets/meshes/foliage/trees_library/birch/tree_aspen_small_b.dae',
         '/assets/meshes/foliage/trees_library/oak/tree_oak_forest_a.dae',
         '/assets/meshes/foliage/trees_library/oak/tree_oak_sml_a.dae']
LAMP = '/art/shapes/objects/lamp1.dae'     # the street lamp roadgen-v2 used
TREE_SPACING = {'forest': 9.0, 'park': 14.0}
TREES_PER_TILE = 900
_T = '/assets/meshes/foliage/trees_library'
# which trees where: Kyiv's streets are lined with chestnuts and lindens (oak and beech stand in),
# its yards with birches, poplars and old broad trees
TREE_SETS = {
    'street': [f'{_T}/oak/tree_oak_sml_a.dae', f'{_T}/oak/tree_oak_sml_b.dae', f'{_T}/beech/tree_beech_small_b.dae',
               f'{_T}/beech/tree_beech_small_c.dae'],
    'row': [f'{_T}/beech/tree_beech_small_b.dae', f'{_T}/beech/tree_beech_small_c.dae', f'{_T}/birch/tree_aspen_small_a.dae'],
    'yard': [f'{_T}/birch/tree_aspen_large_a.dae', f'{_T}/birch/tree_aspen_small_b.dae', f'{_T}/oak/tree_oak_large_a.dae',
             f'{_T}/beech/tree_beech_large_c.dae', f'{_T}/oak/tree_oak_sml_b.dae', f'{_T}/birch/tree_aspen_small_d.dae'],
    'park': [f'{_T}/oak/tree_oak_large_b.dae', f'{_T}/beech/tree_beech_large_c.dae', f'{_T}/birch/tree_aspen_large_b.dae',
             f'{_T}/oak/tree_oak_sml_a.dae'],
    'forest': TREES,
}
ALL_TREES = sorted({t for v in TREE_SETS.values() for t in v})


PHOTO_GROUND = ('ground', 'park', 'forest')


def real_stage(kind):
    """Stage-0 maps of a kind in the 'real' pack."""
    tex, colour, _, _, _ = KINDS[kind]
    if kind.startswith('wall_') and kind[5:] in _photo.PHOTO_WALLS:
        w = kind[5:]
        stage = {'baseColorMap': f'{ART}/real/{w}.color.png'}
        if _photo.PHOTO_WALLS[w][0] == 'curtain':
            # glass that mirrors the sky: smooth and part metal (BeamNG PBR)
            stage.update(roughnessFactor=.12, metallicFactor=.55)
        if _photo.PHOTO_WALLS[w][0] in ('brick', 'end_brick'):
            stage['normalMap'] = f'{ART}/real/{w}.normal.png'
        return stage
    if kind.startswith('wall_'):
        w = kind[5:]
        stage = {'baseColorMap': f'{ART}/real/{w}.color.png'}
        if 'normalMap' in TEXTURES[w]:
            stage['normalMap'] = TEXTURES[w]['normalMap']
        return stage
    if kind in PHOTO_GROUND:
        return {'baseColorMap': f'{ART}/real/ground_{kind}.color.png', 'normalMap': f'{ART}/real/ground_{kind}.normal.png'}
    return dict(TEXTURES[tex]) if tex else {'diffuseColor': colour}


def material(kind):
    _, colour, ground, _, _ = KINDS[kind]
    stage = real_stage(kind)
    if kind in TINT and kind not in PHOTO_GROUND:
        stage['baseColorFactor'] = TINT[kind]
    return {'name': 'e2rf_' + kind, 'mapTo': 'e2rf_' + kind, 'class': 'Material', 'version': 1.5,
            'groundType': ground, 'Stages': [stage, {}, {}, {}], 'doubleSided': False, **EXTRA.get(kind, {})}


def dds(obj):
    """Our art paths with the .dds the art folder holds (fast_art writes no PNG): the game finds
    either, but warns for every '.png' it corrects."""
    if isinstance(obj, dict):
        return {k: dds(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [dds(v) for v in obj]
    if isinstance(obj, str) and obj.startswith('/e2r_stream/fast-art/') and obj.endswith('.png'):
        return obj[:-4] + '.dds'
    return obj


MATERIALS = dds({'e2rf_' + k: material(k) for k in KINDS})
# The tree shapes' own materials (game textures under /assets): this level does not define
# them, and without them every tree rendered as the orange NO MATERIAL (fast-game-detail-03).
import json as _json
from pathlib import Path as _Path
MATERIALS.update(_json.loads((_Path(__file__).with_name('fast_tree_materials.json')).read_text(encoding='utf8')))

INDUSTRIAL = {'industrial', 'warehouse', 'factory', 'garages', 'garage', 'shed', 'hangar', 'service', 'storage_tank'}
OFFICE = {'office', 'commercial', 'retail', 'supermarket', 'mall', 'hotel'}
HOUSE = {'house', 'detached', 'semidetached_house', 'terrace', 'bungalow', 'cabin', 'farm', 'hut'}


def wall(tags, height, fid, central=False):
    """Facade for a building by its tags, height and district; a stable hash picks among the
    similar. Photo walls (fast_photo) for housing, the game's textures for offices and sheds."""
    b = tags.get('building', 'yes')
    h = zlib.crc32(str(fid).encode())
    if b in INDUSTRIAL:
        return 'wall_block'
    # glass only by use: a tall block without tags is a residential panel high-rise here
    # (fast-game-detail-02 dressed Akademmistechko's 16-storey blocks as offices)
    word = _photo.colour_word(tags.get('building:colour'))
    if b in OFFICE or tags.get('office'):
        # towers in curtain glass, lower offices in ribbon windows; the old town keeps plaster
        if height > 20:
            return 'wall_' + (_photo.pick(['off_blue', 'off_green', 'off_grey'], word, h // 7) if h % 10 < 7 else 'off_ribbon')
        if central and h % 10 < 6:
            return 'wall_' + _photo.pick(['pls_yellow', 'pls_cream', 'pls_peach', 'pls_grey'], word, h // 7)
        return 'wall_' + ('off_ribbon' if h % 3 else 'off_ribbon_dark')
    material = tags.get('building:material', '')
    pnl = ['pnl_grey', 'pnl_beige', 'pnl_pink', 'pnl_blue', 'pnl_white', 'pnl_grey', 'pnl_beige']
    lgg = ['lgg_cream', 'lgg_grey', 'lgg_pink']
    brk = ['brk_red', 'brk_white', 'brk_white']
    pls = ['pls_yellow', 'pls_cream', 'pls_peach', 'pls_green', 'pls_blue', 'pls_grey']
    if material == 'brick':
        family = brk
    elif material in ('panel', 'concrete', 'reinforced_concrete'):
        family = lgg if height >= 26 and h % 3 else pnl
    elif material in ('plaster', 'stucco'):
        family = pls
    elif b in HOUSE or height < 8:
        family = brk if h % 5 < 2 else pls
    elif height >= 26:                     # nine storeys and up: panel or loggia blocks
        family = lgg if h % 9 < 4 else pnl
    elif height >= 14:                     # five to eight storeys: khrushchovky, panel and brick
        r = h % 20
        family = (pls if r < 12 else brk if r < 17 else pnl) if central else (pnl if r < 8 else brk if r < 15 else pls)
    else:                                  # three, four storeys: the old town and stalinky
        family = pls if h % 5 < 3 else brk
    return 'wall_' + _photo.pick(family, word, h // 7)


def shopfront(tags, height, fid, central, kind):
    """The storefront kind for the ground floor of a building in the old town (or tagged with a
    shop), or None."""
    b = tags.get('building', 'yes')
    h = zlib.crc32(str(fid).encode()) // 11
    family = kind[5:].split('_')[0]
    if family in ('off', 'lgg') or b in INDUSTRIAL or b in HOUSE or height < 9:
        return None
    if tags.get('shop') or tags.get('amenity') in ('cafe', 'restaurant', 'bank', 'pharmacy') or \
            (central and b in ('yes', 'apartments', 'residential', 'commercial', 'retail') and h % 10 < 6):
        return 'wall_' + ('shop_dark', 'shop_red', 'shop_green', 'shop_blue', 'shop_dark')[h % 5]
    return None


SHOP_HEIGHT = 4.2


def end_wall(kind):
    """The blank end-wall kind of a facade, or the facade itself."""
    end = kind + '_end'
    return end if end in KINDS else kind


def pitched(tags, height, area):
    """True for a pitched (gabled) roof: by roof:shape, else houses and small low buildings."""
    shape = tags.get('roof:shape')
    if shape:
        return shape in ('gabled', 'hipped', 'pyramidal', 'half-hipped', 'saltbox', 'mansard', 'gambrel')
    b = tags.get('building', 'yes')
    return (b in HOUSE or (height < 8 and area < 260)) and b not in INDUSTRIAL and area < 900
