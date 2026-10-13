# EarthNG

Мод для BeamNG.drive: обрати будь-яку точку на мапі, «висадитися» і їхати реальними вулицями,
згенерованими з OpenStreetMap. Зараз дані підготовлені для Києва.

Генератор світу — [Earth2Road](https://github.com/Alezx311/earth2road) (`akadem_maps`, roadgen-v2,
BeamNG-експортер). EarthNG — усе, що потрібно для стрімінгу в гру: мод, компаньйон, тайли
джерел, заміри й експерименти.

## Запуск

```powershell
powershell -ExecutionPolicy Bypass -File C:\js\EarthNG\run_world.ps1
```

Скрипт збирає мод і ставить його в профіль BeamNG, запускає гру через Steam і компаньйон. У грі:
Freeroam → «Earth2Road - Планета». Вікно скрипта тримати відкритим. Параметри: `-NoGame`
(гра вже відкрита), `-Workers N` (процеси-збирачі тайлів, типово 8), `-Earth2Road <шлях>`.

Висадка працює всередині підготовленого регіону (зелена зона на мапі). Регіон Київ
(Оболонь…Голосіїв) готується раз, ~20 хв:

```powershell
C:/js/Earth2Road/.venv/Scripts/python.exe earthng/region.py --pbf <ukraine-260918-kyiv.osm.pbf> --bbox 30.40 50.36 30.62 50.55 --out out/region/kyiv-ns
```

Після висадки світ генерується тайлами 500 м довкола авто й попереду за напрямком руху
(roadgen v2, шви між тайлами збігаються — див. `docs/BEAMNG_STREAMING.md`, «Тайли без швів»).
Згенеровані тайли лишаються в `e2r_stream/` профілю гри: повторний приїзд миттєвий.

## Залежність від Earth2Road

- Python — з `.venv` Earth2Road; шлях до checkout — змінна `EARTH2ROAD` (типово
  `C:\js\Earth2Road`), див. `earthng/paths.py`.
- EarthNG використовує необов'язкові гачки в ядрі Earth2Road, які поки лише в його робочому
  дереві (не закомічені): `context.skip_surface_audit`, `context.region` і `context.stop_after`
  (`akadem_maps/core/prepare.py`) та `export_map(..., sink=...)`
  (`akadem_maps/adapters/beamng/export_beamng.py`).

## Структура

| Шлях | Що це |
|---|---|
| `mod/` | Lua-мод: рівень-носій, вікно вибору (`lua/ge/extensions/e2r/world.lua`), стрімінг шматків |
| `earthng/world_server.py` | компаньйон: мапа, висадка, пул збирачів і планувальник тайлів за позою авто |
| `earthng/region.py` | підготовка регіону: мережа SUMO, профілі висот і рамка, спільні для всіх тайлів |
| `earthng/tiles.py` | тайл 500 м: виріз мережі регіону, roadgen v2 з ореолом 80 м, власні шматки |
| `earthng/seams.py`, `earthng/route.py` | перевірка швів між сусідніми тайлами; маршрут для автопілота тесту |
| `earthng/direct.py` | прямий шлях збірки в шматки без DAE/ZIP (`StreamSink`) |
| `earthng/osm_tiles.py` | нарізка OSM-пакета на статичні тайли (сітка 0,05°, буфер 0,01°) |
| `earthng/s1.py`, `earthng/e2rstream.lua` | спайки S1–S3 (рантайм-тайли, ProceduralMesh, колізія) |
| `earthng/build_mod.py`, `earthng/test_world.py` | збирання мода; наскрізний тест в ізольованому профілі |
| `earthng/profile_land.py`, `earthng/compare_worlds.py` | заміри висадки; побайтовий оракул для прискорень |
| `docs/BEAMNG_STREAMING.md` | дизайн, усі заміри, пастки BeamNG |
| `data/osm_tiles/kyiv-260918` | 162 тайли Києва (71 МБ), не в git |
| `out/` | результати експериментів і прогонів, регіони (`out/region/`), не в git |
