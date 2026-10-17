# EarthNG native mod migration

## 2026-10-18: country pack window

- `Country.bat` lists every Geofabrik extract. A ready zip can be installed from
  disk or from `earthng/ready_packs.json`. Otherwise the window downloads that
  extract, cuts tiles, fetches Terrarium and builds `earthng_<id>.zip`.
- The country manifest stores `look` (asset block and six slots) and `include`
  (which OSM layers were kept). Roads and the elevation grid always stay.
- The generator reads `look`: worn/fresh asphalt, white/yellow/no markings,
  verge material, untagged facade family, tree mix, lamps and stops.
  `mark_yellow` is a solid-colour material. Tagged buildings still follow OSM.
- Unit checks: `test_country_setup.py` 12, `test_generator.py` 34, `test_styles.py` 8.
  The window was not driven inside BeamNG.
- `Country.bat`, `EarthNG.bat` and the country build use this checkout's `.venv`
  (`requirements-fast.txt`). Earth2Road stays only for the optional `v2` generator.
- The country-pack window, its log and the asset-block labels are English.

## 2026-10-17: one Forest, tiles paint again

- A Forest per tile is never frustum-culled (global bounds), so each loaded
  tile walked its own crowns on the main thread. The GPU sat near 30% and the
  frame sat near 20 FPS on one core. Crowns now share one `e2rForest`.
- `rebuildForestManagerInstanceData` reuploads every crown. It used to run
  every frame that planted or removed a tree, which stalled the tile queue.
  It now runs at most every 0.4 s. Ground meshes sort ahead of plants, so a
  nearer tile's crowns no longer block the next tile's road.
- The density panel shows the last rebuild time. Collidable stays off. The
  Forest object is still never deleted.

## 2026-10-17: grass blades are 20–30 cm

- The stock tufts are about 26 cm (short) and 52 cm (long). A shared scale of
  9–14 made them several metres tall, above the pickup. Scale is now the wanted
  blade height divided by that mesh span, so each tuft is 20–30 cm. The extra
  0.35 m lift is gone. The lowest vertex sits on the ground.
- Density is unchanged: town grass still 2.6 m, cap 4000. The old solid hedge
  was those same tufts blown up until they overlapped.
- Unit checks after the height change: `test_nature.py` 16, `test_generator.py` 33.

## 2026-10-17: frame time against vegetation

- A far cap of 2400 filled about 240k crowns and held the frame around 160 ms.
  Far default is 800 again (hard 1200). Near forest stays 2400. Forest adds
  per frame are 80.
- Residential trees are a 14 m lattice at chance 0.40, parks 12 m at 0.70.
  Grass spends its cap in town: residential and park at 2.6 m, cap 4000
  (hard 4800). Forest floor grass stays thin. Yard bushes are off.
- Unit checks: `test_nature.py` 16, `test_generator.py` 33, `test_styles.py` 8,
  `test_native.py` 22, `test_widget.py` 7.

## 2026-10-17: denser yards, lawns and canopy

- Forest step is 5 m, open ground 6 m, park and residential 8 m. Near and far
  tree caps are both 2400. Plants are round-robin across 25 m cells, so one
  grove cannot spend the whole tile. Open-ground keep chance is 0.94–1.00.
  Far grass stays off. Forest items are added in batches of 160.
- Grass scale is 10–16, default cap 1800 (hard 3200), and the road pad for tufts
  is 2.2 m. Bushes also fill ground, residential and park. The widget defaults
  and `clampTune` match these numbers, so Apply does not thin the world back.
- Unit checks: `test_nature.py` 16, `test_generator.py` 33, `test_styles.py` 8,
  `test_native.py` 22, `test_widget.py` 7.

## 2026-10-17: forests actually spawn, grass and stones show

- BeamNG 0.39 has `ForestItemData`, not `TSForestItemData`. The old call
  failed once per tree, so every crown was a TSStatic and the canopy flickered.
  A failed class is remembered for the session. Forest items refresh the
  forest manager after each batch. The far canopy is removed when the near
  scene of that tile starts, so the same trees are not drawn twice.
- Grass and rocks are queued before trees. Grass scale is 6.5–11 and the tuft
  is lifted 0.35 m. Slope stones start at about 20° (`nz` 0.94) instead of
  45°, with a lower chance on gentler slopes. Flat ground still has none.
- Forest groves fill at 0.80–1.00. Open ground fills at 0.88–1.00. The far
  tree cap default is 640 (hard 800). Far grass stays off.
- The density panel copies slider values even when ImGui does not return
  true, rebuilds when the slider is released, and shows the numbers that are
  actually in the world plus live grass, rock and crown counts.
- Unit checks: `test_nature.py` 16, `test_generator.py` 33, `test_styles.py` 8,
  `test_native.py` 22.

## 2026-10-17: dense woodland and a density panel

- Untagged ground gets its own tree lattice (8 m, about 40% Scots pine, the
  rest East Coast oak, beech and aspen). Forest polygons use a 6 m step.
  Near cap 1800, far cap 320. Far stays a prefix of the same sorted list.
  Far grass stays off.
- Grass and rocks spawn as TSStatic again. Forest is trees and bushes only,
  because an alpha tuft placed as a Forest item was not drawing.
- The driving widget has «Щільність і дистанція»: radii, caps, spacing, and
  an apply button that rebuilds near and far tiles. The horizon stays.
  Any camera that is not the cockpit or the hood, once it is 100 m from the
  car, owns the detail bubble. The panel shows the live camera name.
- Unit checks: `test_nature.py` 16, `test_generator.py` 33, `test_styles.py` 8,
  `test_native.py` 22.

## 2026-10-17: Yaremche draw distance, fences, Forest

- The near bubble follows a free or orbit camera once it is more than 100 m from
  the car. Velocity is zero in that mode, so look-ahead does not drop the tiles
  behind the lens. Cockpit driving stays on the vehicle.
- Far tiles draw fences. Paths and the horizon do not.
- Trees, bushes, grass and loose rocks spawn as non-colliding Forest items,
  one Forest per tile, returned to a pool after `removeItem`. Three failed
  creates fall back to TSStatic for the rest of the session. Deleting a
  colliding Forest is avoided: that hang is real.
- Open ground (no meadow polygon in the current Ukraine pack) gets grass.
  Slopes steeper than about 45° get rocks. Untagged ground keeps the ground
  material, so a far hill stays one mesh. Far grass stays off. Far rocks stay
  a subset of the near list.
- Unit checks: `test_nature.py` 16, `test_generator.py` 33, `test_styles.py` 8.

## 2026-10-17: P2 countryside scatter and road grades

- Grass tufts, forest understory and loose rock are TSStatic decor on the P1
  cover classes. Far keeps rocks only, as a subset of the near list. No Forest,
  GroundCover or TerrainBlock.
- Shapes and their materials stay in the player's BeamNG install (Italy pines,
  East Coast USA grass and rocks) and load through VFS.
- Rural carriageways take a grade 0–5 from surface, tracktype and smoothness.
  Grade 0–1 keeps the surveyed profile. Grade ≥ 2 undulates and drops sidewalks.
  Untagged city asphalt is unchanged. `highway=track` becomes a drivable ribbon
  and stays out of the navgraph.
- Planet manifest in source is 0.4.0; `natureVersion` stays 1. Country packs do
  not need a recompile. `earthng_planet.zip` is still the previous build until
  `build_mod.py` is run, so the five new road materials are missing in game.
- Unit checks in the Earth2Road venv: `test_nature.py` 15, `test_generator.py`
  32, `test_styles.py` 8. No in-game drive for this slice.
- Detail: `docs/NATURE.md`, section P2.

## 2026-10-17: P1 nature implementation

P1 land-cover classes, WorldCover and TSStatic trees are in tree. The note below
is the original working record.

- Starting from Grok P0 commit `11a7d67`; preserve its DEM horizon and road shaping.
- Route: existing native ProceduralMesh/TSStatic; v2 area strings carry new natural
  classes, optional offline WorldCover raster import, no companion or format fork.
- Verified stock rock/sand PBR in `content/assets/materials/terrain.zip` and Scots
  pine shapes in `content/levels/italy.zip`; load stock materials through VFS.
- Test profiles/packages go under `out/p1-*`; regular saves/profile are untouched.
- Baseline: generator 32, country 14, native 22 tests passed in Earth2Road venv.

2026-10-15, branch `codex/official-mod-packages`.

- User selected runtime Lua generation and compact country data, rather than
  distributing baked geometry or requiring a Python companion.
- Native entrypoint: `scripts/earth2road_world/modScript.lua`; GE extension
  `e2r_world`; carrier level `e2r_world`. Public ZIP: `earthng_planet.zip`.
- Countries: mounted VFS `/earthng/countries/<id>/manifest.json`. Disabled and
  unmounted mods must never contribute landing coverage.
- Country compiler exports source features and elevation grids from prepared
  OSM indexes. No game assets, interpreter, SQLite database or baked mesh shipped.
- Shared distributable art uses our drawings/CC0 photo sources. BeamNG assets
  are referenced from the player's installation, rather than copied.
- Installed test game: Steam 284160, build 20972 (integrity metadata dated
  2026-08-07), `C:/Program Files (x86)/Steam/steamapps/common/BeamNG.drive`.
- Tests run in fresh `out/native-*/user` profiles; regular game profile is not
  modified. No keyboard/mouse automation or profile/save migration is needed.
- Implementation and format: `docs/NATIVE_MODS.md`.

Validation: 57 unit/regression checks pass. The empty native planet starts in the
real game. Two-country native generation/landing/collision/25m driving work with
no companion process or request/pose files. Earlier feature run confirmed four
moving AI cars, rain and all four base texture packs. Native game test 03 still
fails the unchanged 100ms frame gate (p99 195.5ms, worst295.85ms); performance and
full generator parity remain release work, documented in `docs/NATIVE_MODS.md`.

Fixes verified during migration: road-only landing selection, source-cell building
datum continuity, safe country-button landing points, reset while generation is
pending, pinned startup geometry until touchdown, and release/unload of Lua mesh
arrays after engine upload. Exact UTM is tested against pyproj in both hemispheres.

## 2026-10-17: якість доріг і зависання з трафіком

- Полігон центру Києва 10 x 10 км (`out/polygon/earthng_kyiv10.zip`) і стенд
  `earthng/road_quality.py` з метриками дефектів. Генератор доведено за ними:
  земля притиснута під асфальт, суцільні стрічки доріг із мітрою, тротуари врівень
  і обрізані по чужому асфальту, узбіччя, покриття на землі, перехрестя-зірки.
  Перехрестя виводяться з кінців доріг у рантаймі: пакети v2 не перезбираються.
  Усі 400 тайлів: тротуар на дорозі 53,7 -> 1,9 м2/км, земля над асфальтом
  556 -> 7,6 м2/км, перепади на краю понад 30 см 44,6 -> 8,9 на км.
- Паркани йдуть по землі й мають товщину; автобусні зупинки стоять біля дороги.
- Вічне зависання під час трафіку: ребра навграфа нульової довжини зациклювали
  `graphpath.getRandomPathG` під час респавну. Виправлено в генераторі й `navBuild`.
- Сторож зависань пише стек Lua у `e2r-hang.txt`; `test_world.py` під'єднує
  моди жорсткими посиланнями.
- Подробиці: `docs/NATIVE_MODS.md`, розділи «Якість доріг» і «Трафік і зависання».
