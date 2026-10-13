"""Look of the fast generator: materials from the game's own tileable textures, UV scale per
surface kind, and a facade per building. No textures are shipped; all paths are BeamNG's
/assets (resolved to their .dds). Kinds are the Mesh face groups; material = 'e2rf_' + kind."""
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
ART = '/e2r_stream/fast-art/v5'      # fast_art.ensure writes the facades there
WALLS = ('panel', 'precast', 'brick', 'brick_red', 'brick_white', 'plaster', 'stucco', 'glass', 'windows', 'block')
for w in WALLS:
    # walls carry their own UVs (one 3 m window cell per repeat, floors from the base)
    KINDS['wall_' + w] = (w, [.58, .55, .49, 1], 'ROCK', 3.0, 'wall')


# baseColorFactor per kind: the game's grass is dry West Coast grass; Kyiv's is green
TINT = {'ground': [.55, .85, .42, 1], 'forest': [.36, .58, .28, 1], 'paved': [.62, .62, .60, 1]}

# OSM areas that change the ground under them (anything else stays grass)
PAVED = {'industrial', 'commercial', 'retail', 'railway', 'garages', 'construction', 'depot'}
WOODS = {'forest', 'wood'}


PARKS = {'park', 'garden', 'recreation_ground', 'cemetery', 'village_green'}


def ground_kind(tags):
    """'paved', 'forest', 'park' or None for an OSM area's tags."""
    if tags.get('landuse') in PAVED or tags.get('amenity') == 'parking' or tags.get('place') == 'square':
        return 'paved'
    if tags.get('landuse') in WOODS or tags.get('natural') in WOODS:
        return 'forest'
    if tags.get('leisure') in PARKS or tags.get('landuse') in PARKS:
        return 'park'
    return None


# the game's own tree shapes (precompiled .cdae next to each .dae); no collision, so a forest
# adds nothing to be:reloadCollision
TREES = ['/assets/meshes/foliage/trees_library/beech/tree_beech_forest_a.dae',
         '/assets/meshes/foliage/trees_library/beech/tree_beech_small_b.dae',
         '/assets/meshes/foliage/trees_library/birch/tree_aspen_forest_a.dae',
         '/assets/meshes/foliage/trees_library/birch/tree_aspen_small_b.dae',
         '/assets/meshes/foliage/trees_library/oak/tree_oak_forest_a.dae',
         '/assets/meshes/foliage/trees_library/oak/tree_oak_sml_a.dae']
LAMP = '/art/shapes/objects/lamp1.dae'     # the street lamp roadgen-v2 used
TREE_SPACING = {'forest': 9.0, 'park': 16.0}
TREES_PER_TILE = 400


def material(kind):
    tex, colour, ground, _, _ = KINDS[kind]
    if kind.startswith('wall_'):
        w = kind[5:]
        stage = {'baseColorMap': f'{ART}/real/{w}.color.png'}
        if 'normalMap' in TEXTURES[w]:
            stage['normalMap'] = TEXTURES[w]['normalMap']
    else:
        stage = dict(TEXTURES[tex]) if tex else {'diffuseColor': colour}
    if kind in TINT:
        stage['baseColorFactor'] = TINT[kind]
    return {'name': 'e2rf_' + kind, 'mapTo': 'e2rf_' + kind, 'class': 'Material', 'version': 1.5,
            'groundType': ground, 'Stages': [stage, {}, {}, {}], 'doubleSided': False}


MATERIALS = {'e2rf_' + k: material(k) for k in KINDS}
# The tree shapes' own materials (game textures under /assets): this level does not define
# them, and without them every tree rendered as the orange NO MATERIAL (fast-game-detail-03).
import json as _json
from pathlib import Path as _Path
MATERIALS.update(_json.loads((_Path(__file__).with_name('fast_tree_materials.json')).read_text(encoding='utf8')))

INDUSTRIAL = {'industrial', 'warehouse', 'factory', 'garages', 'garage', 'shed', 'hangar', 'service', 'storage_tank'}
OFFICE = {'office', 'commercial', 'retail', 'supermarket', 'mall', 'hotel'}
HOUSE = {'house', 'detached', 'semidetached_house', 'terrace', 'bungalow', 'cabin', 'farm', 'hut'}


def wall(tags, height, fid):
    """Facade for a building: by its tags and height, a stable hash picks among the similar."""
    b = tags.get('building', 'yes')
    h = zlib.crc32(str(fid).encode())
    if b in INDUSTRIAL:
        return 'wall_block'
    # glass only by use: a tall block without tags is a residential panel high-rise here
    # (fast-game-detail-02 dressed Akademmistechko's 16-storey blocks as offices)
    if b in OFFICE or tags.get('shop') or tags.get('office'):
        return 'wall_glass' if height > 12 or h % 2 else 'wall_windows'
    if b in HOUSE or height < 8:
        return 'wall_' + ('brick', 'brick_red', 'brick_white', 'plaster', 'stucco')[h % 5]
    if height >= 14:                       # five floors and up: the city's panel blocks
        return 'wall_' + ('panel', 'precast', 'panel', 'brick_white')[h % 4]
    return 'wall_' + ('plaster', 'brick', 'precast', 'stucco')[h % 4]
