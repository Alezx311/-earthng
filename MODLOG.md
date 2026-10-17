# EarthNG native mod migration

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
