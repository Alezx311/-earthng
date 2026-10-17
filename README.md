# EarthNG

**Новий стандартний мод:** `earthng_planet.zip` + `earthng_<країна>.zip` у звичайній
папці `mods` BeamNG. Базовий мод знаходить активні пакети через VFS, показує їх на
офлайн-мапі та генерує світ у Lua під час гри. Користувач сам запускає BeamNG;
окремий Python-компаньйон для цього режиму не потрібен.

[Формат пакетів, збирання та перевірки](docs/NATIVE_MODS.md). Міграція йде в гілці
`codex/official-mod-packages`; старий шлях нижче лишається для порівняння (`--legacy`).
Старі архіви з Python треба перезібрати, вони не є пакетами країн нового формату.

**Пак будь-якої країни:** `Country.bat` (`earthng/country_ui.py`). Готовий `earthng_<країна>.zip`
можна покласти в `mods`, якщо він уже є на диску або в `earthng/ready_packs.json`.
Інакше вікно качає витяг регіону з Geofabrik і висоти Terrarium та збирає пак саме.
Блок ассетів обирається за країною (пострадянські панелі, жовта розмітка США, хвойні
півночі). Асфальт, розмітку, узбіччя, фасади без тега, дерева й вуличні моделі можна
змінити до збірки. У пак пишуться ці ідентифікатори. Дороги й рельєф входять завжди.
«Refresh planet code» підміняє Lua і матеріали в уже зібраному `earthng_planet.zip`,
щоб гра прочитала вигляд. Моделі лишаються в пакеті планети і в установленій грі.

## Попередній шлях з компаньйоном

**Пакет для тестувальників (уся Україна, генератор fast):** `earthng/package.py` збирає
`out/package/EarthNG-Ukraine-<дата>.zip` (~4,5 ГБ: Python, компаньйон, мод, 34 946 тайлів OSM, DEM і готовий арт).
Отримувачу досить розпакувати й запустити `start.bat`; інструкція всередині — `ІНСТРУКЦІЯ.txt`. Потрібні
`data/osm_tiles/ukraine-260918` (`earthng/osm_tiles_country.py`) і DEM (`earthng/fast_dem.py --download`).
`--tiles data/osm_tiles/california-261013` — пакет усього штату Каліфорнія (`EarthNG-California`; тайли з
`california-261013.osm.pbf` Geofabrik через `osm_tiles_country.py`, DEM — `fast_dem.py --download`).
`--tiles data/osm_tiles/united-kingdom-261013` — уся Велика Британія з Північною Ірландією (`EarthNG-UK`, ~25 ГБ;
публічно — 9 регіональних паків `uk-<регіон>-261013`, `EarthNG-UK-<Регіон>`: жорсткі посилання на тайли країни),
`--tiles data/osm_tiles/new-york-261013` — штат Нью-Йорк (`EarthNG-NewYork`),
`--tiles data/osm_tiles/zanzibar-261013` — Занзібар, острови Унгуджа й Пемба (`EarthNG-Zanzibar`),
`--tiles data/osm_tiles/romania-261014` — уся Румунія (`EarthNG-Romania`).
Пакети Великої Британії, Каліфорнії та Нью-Йорка — повністю англійською (`earthng/lang.py`): вікно запуску, мод
(`build_mod.py --lang en`), повідомлення компаньйона, `start.ps1` і `README.txt` з `earthng/package/en/`.

У грі: **трафік** (кнопки в міні-вікні; граф для AI будується з тайлів, `fast.nav_segments` → `map.reset`), **погода й
час доби** — штатне меню гри «Середовище» (рівень має `TimeOfDay`, `Precipitation`, `ForestWindEmitter`).

Поточна незавершена робота над генератором для 300 км/год: **[передача Claude, заміри й наступні кроки](docs/HANDOFF_FAST_STREAMING.md)**. Типовий режим запуску — `fast` по всій Україні; `-Generator v2` — старий генератор у підготовленому регіоні Києва.

Мод для BeamNG.drive: обрати будь-яку точку на мапі, «висадитися» і їхати реальними вулицями,
згенерованими з OpenStreetMap. Зараз дані підготовлені для Києва.

Генератор світу — [Earth2Road](https://github.com/Alezx311/earth2road) (`akadem_maps`, roadgen-v2,
BeamNG-експортер). EarthNG — усе, що потрібно для стрімінгу в гру: мод, компаньйон, тайли
джерел, заміри й експерименти.

## Запуск

**`EarthNG.bat`** — вікно запуску (`earthng/launcher.py`, Tk) на Python з `.venv` цього репозиторія:
тека профілю BeamNG, пакет тайлів OSM, генератор `fast`/`v2`, процеси генерації, далекий план
(`EARTHNG_FAR_R`), радіус тайлів `v2`, запуск гри й перезбирання мода. Шлях до Earth2Road з’являється
лише коли обрано старий генератор `v2`. Налаштування — у `launcher.json` (не в git), журнал компаньйона — у вікні,
«Зупинити» або закриття вікна гасять компаньйон разом зі збирачами. У пакеті для тестувальників те саме вікно
відкриває `EarthNG.bat` у корені пакета.

Консольний запасний варіант:

```powershell
powershell -ExecutionPolicy Bypass -File C:\js\EarthNG\run_world.ps1
```

Скрипт збирає мод і ставить його в профіль BeamNG, запускає гру через Steam і компаньйон. У грі:
Freeroam → «Earth2Road - Планета». Вікно скрипта тримати відкритим. Параметри: `-NoGame`
(гра вже відкрита), `-Workers N` (процеси-збирачі тайлів, типово 8), `-Earth2Road <шлях>`.

**Linux (експериментально):** `bash run_world.sh` — те саме для BeamNG нативно або під Proton (профіль
шукається в обох, `--userfolder` / `BEAMNG_USERFOLDER` — вручну). Python для `fast` — `.venv-linux/`
з `requirements-fast.txt`. Генератор `v2` досі потребує checkout Earth2Road (`--earth2road`, `EARTH2ROAD`,
типово `~/Earth2Road`). Тайли й DEM у `data/` (не в git) — як для Windows.
Параметри: `--tiles`, `--workers`, `--generator fast|v2`, `--nogame`, `--help`. У пакеті для тестувальників —
`start.sh`. Вікно запуску (`launcher.py`) поки лише Windows.

Висадка працює всередині підготовленого регіону (зелена зона на мапі). Регіон Київ
(Оболонь…Голосіїв) готується раз, ~20 хв:

```powershell
C:/js/Earth2Road/.venv/Scripts/python.exe earthng/region.py --pbf <ukraine-260918-kyiv.osm.pbf> --bbox 30.40 50.36 30.62 50.55 --out out/region/kyiv-ns
```

Після висадки світ генерується тайлами 500 м довкола авто й попереду за напрямком руху
(roadgen v2, шви між тайлами збігаються — див. `docs/BEAMNG_STREAMING.md`, «Тайли без швів»).
Згенеровані тайли лишаються в `e2r_stream/` профілю гри: повторний приїзд миттєвий.

## Python

Вікно країни (`Country.bat`), вікно запуску і генератор `fast` беруть інтерпретатор з цього
репозиторія: `python\python.exe` у пакеті для тестувальників або `.venv` (його немає в git).
Створити його: `py -3.13 -m venv .venv`, потім `.venv\Scripts\python.exe -m pip install -r requirements-fast.txt`.
На Linux те саме робить `run_world.sh` у `.venv-linux/`.

Старий генератор `v2` досі читає Earth2Road (`earthng/paths.py`, `-Generator v2`): необов'язкові
гачки `context.skip_surface_audit`, `context.region`, `context.stop_after` і `export_map(..., sink=...)`
лежать у його робочому дереві. Для пака країни і для `fast` цей checkout не потрібен.

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
| `earthng/osm_tiles_country.py` | те саме для країни: дерево витягів (країна → групи → блоки 1,5°) і `osm_tiles` на кожен блок; тайли побайтово ті самі |
| `earthng/fast.py`, `earthng/fast_far.py` | генератор `fast`: дороги, сцена (будівлі, ґрунт, доріжки, паркани, дерева, авто), далекий план |
| `earthng/fast_style.py`, `earthng/fast_photo.py`, `earthng/fast_art.py` | вигляд: матеріали, вибір фасаду за тегами OSM, фото-фасади й ґрунт, текстур-паки |
| `earthng/fast_packs.py`, `earthng/fetch_textures.py` | паки на основі фото: ніч, дощовий вечір, кіберпанк, зима, осінь; CC0-сніг і листя з ambientCG |
| `earthng/fast_dds.py` | кодер BC1 DDS з mip-ланцюгом і sRGB-заголовком (гра не «готує» PNG) |
| `data/textures`, `data/art/<версія>` | CC0-фото (Panelka pack, ambientCG) і зібраний з них арт, не в git |
| `earthng/s1.py`, `earthng/e2rstream.lua` | спайки S1–S3 (рантайм-тайли, ProceduralMesh, колізія) |
| `earthng/build_mod.py`, `earthng/test_world.py` | збирання мода; наскрізний тест в ізольованому профілі |
| `earthng/profile_land.py`, `earthng/compare_worlds.py` | заміри висадки; побайтовий оракул для прискорень |
| `docs/BEAMNG_STREAMING.md` | дизайн, усі заміри, пастки BeamNG |
| `data/osm_tiles/kyiv-260918` | 162 тайли Києва (71 МБ), не в git |
| `data/osm_tiles/ukraine-260918` | 34 946 тайлів усієї України (3,3 ГБ, ~70 хв на 6 процесах), не в git; `run_world.ps1` бере їх типово |
| `out/` | результати експериментів і прогонів, регіони (`out/region/`), не в git |
