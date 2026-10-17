# Аудит природи й рельєфу EarthNG

Досліджено 2026-10-17. Робоча гілка `codex/official-mod-packages`, HEAD `7ddb6f43ea223d2c084118c572e91414c2b9e589` (команди `git branch --show-current`, `git rev-parse HEAD`). Продуктовий код не змінювався. Початкові untracked: `%LOCALAPPDATA%/`, `earthng/graphify-out/`. Основний предмет — нативний Lua-режим; Python fast використано тільки для порівняння. Нижче «встановлено кодом» означає статичний аудит; «виконано» — власний запуск поза грою. Нових вимірювань FPS або візуальної перевірки в BeamNG немає.

## Що зараз обмежує природні краєвиди?

### Takeaway

Найсерйозніша проблема — відсутність рельєфу в нативному далекому шарі. Удосконалення текстур і дерев без виправлення цієї геометричної прогалини не відновить силуети гір. Другий системний недолік — мала кількість типів природної поверхні, третій — пласкі нормалі трикутників.

### Cited Findings

- Нативний мод уже має правильну основу для офлайн-ландшафту: пакети країн містять компактні джерела, Lua читає їх через VFS і генерує ProceduralMesh без компаньйона. Це не забороняє підготовку кращих DEM/land-cover під час збирання пакетів. [Архітектура](C:/js/EarthNG/docs/NATIVE_MODS.md:27).
- **Далекий режим не генерує землі.** Цикл землі вимкнений через `for gy = 0, options.far and -1 or count - 1`; ділянки `water/paved/forest/park` також пропущено. У far залишаються будівлі, розріджені дерева, море за висотою DEM. Це навмисно зафіксовано тестом `test_far_buildings_are_coarse_decoration_without_ground_or_roads`. [Земля](C:/js/EarthNG/mod/lua/ge/extensions/e2r/generator.lua:1439), [покриття](C:/js/EarthNG/mod/lua/ge/extensions/e2r/generator.lua:1430), [тест](C:/js/EarthNG/earthng/test_generator.py:326).
- Планувальник використовує near 1000 м, far 3000 м і випередження до 2000 м. Нові far-тайли не будуються при швидкості понад 25 м/с (90 км/год). Це непридатне правило для майбутнього безперервного силуету гір: декоративні об'єкти можна пропускати, географічний горизонт має залишатись. [Радіуси та умови](C:/js/EarthNG/mod/lua/ge/extensions/e2r/native.lua:465), [пріоритети](C:/js/EarthNG/mod/lua/ge/extensions/e2r/native.lua:473), [рендерер](C:/js/EarthNG/mod/lua/ge/extensions/e2r/world.lua:771).
- Замість географічного горизонту створюється одна пласка площина 40 × 40 км. За відсутності `horizonZ` у регіоні native ставить її на висоту спавну мінус 40 м. На високій посадці це може давати неприродне підвищене плато, а не далекі долини. [Площина](C:/js/EarthNG/mod/lua/ge/extensions/e2r/world.lua:1269), [висота](C:/js/EarthNG/mod/lua/ge/extensions/e2r/native.lua:439). Останнє — висновок із коду, не власний знімок гри.
- **Попередній Python fast у цьому аспекті має більше функцій:** `build_far` малює DEM-сітку з кроком 100 м, великі дороги, водні полігони. Не слід описувати його як поточну нативну реалізацію. [Константа](C:/js/EarthNG/earthng/fast_far.py:19), [земля](C:/js/EarthNG/earthng/fast_far.py:72), [вода](C:/js/EarthNG/earthng/fast_far.py:39).
- Джерело висот — AWS Terrarium `elevation-tiles-prod/terrarium/12/x/y.png`, zoom 12, PNG 256 × 256. Декодування `R*256+G+B/256-32768`, bilinear між сусідніми PNG, плюс світове зміщення 20 м. Відсутній DEM викликає явну помилку. Це конкретне джерело, а не доведений «чистий SRTM». [Завантаження](C:/js/EarthNG/earthng/fast_dem.py:30), [читання](C:/js/EarthNG/earthng/fast_sources.py:186), [зміщення](C:/js/EarthNG/earthng/fast_sources.py:27).
- Відстань між пікселями цього Web Mercator zoom розрахунково близько 38,22 м на екваторі, 27,02 м на 45°, 25,07 м на 49°, 24,34 м у Києві (50,45°): `40075016.68557849*cos(lat)/(256*4096)`. Це геометричний крок сітки, **не заявлена точність вихідного DEM**. Формула відповідає проєкції у [sample()](C:/js/EarthNG/earthng/fast_sources.py:228).
- Пакувальник робить джерельну комірку 1000 м, сітку 25 м (41 × 41 висота), feature halo 100 м; сам terrain rect не розширений цим halo. Висоти terrain квантуються до дециметра. Більша густота меша сама по собі не створить нових деталей DEM. [Константи](C:/js/EarthNG/earthng/build_country.py:32), [сітка](C:/js/EarthNG/earthng/build_country.py:242), [формат](C:/js/EarthNG/earthng/country_format.py:20).
- `Despiked` замінює точку медіаною 5 × 5 сусідів через 25 м, якщо різниця понад 40 м. Це потрібне виправлення артефактів Занзібару, але для справжніх вузьких хребтів/скель потрібна окрема перевірка збереження геометрії. Поточний тест перевіряє ізольовані піки/ями і рівний схил 20%, не гострий справжній гребінь. [Алгоритм](C:/js/EarthNG/earthng/build_country.py:139), [тест](C:/js/EarthNG/earthng/test_country.py:267).
- Near-меш має крок 25 м і 800 базових трикутників на 500 × 500 м без урахування доріг і покриттів. Висоти bilinear із source grid, потім земля тягнеться до профілів доріг: повний вплив до 12 м від краю, нульовий після 70 м, smoothstep, опускання під полотном. Це корисно для їзди, але потребує гірських тестів, щоб не зрізати схил над серпантином. [TUNE](C:/js/EarthNG/mod/lua/ge/extensions/e2r/generator.lua:14), [висоти](C:/js/EarthNG/mod/lua/ge/extensions/e2r/generator.lua:657), [підтягування](C:/js/EarthNG/mod/lua/ge/extensions/e2r/generator.lua:673), [земля](C:/js/EarthNG/mod/lua/ge/extensions/e2r/generator.lua:704).
- Collector обчислює одну нормаль із векторного добутку на трикутник і використовує її для всіх трьох кутів. Зварювання позицій не усуває пласку освітленість. Немає гладких нормалей землі із центральної різниці DEM. UV — проєкція XY; повтор ground 24 м, інших типів, зокрема forest/park, за замовчуванням 6 м. Це варто узгодити з photo-sheet, розрахованим на 24 м. [Нормалі й UV](C:/js/EarthNG/mod/lua/ge/extensions/e2r/generator.lua:342), [розмір аркуша](C:/js/EarthNG/earthng/fast_photo.py:220).
- Матеріали photo ground уже мають base color і normal map: «додати нормалі текстур з нуля» — неправильний діагноз. Бракує різноманіття великих поверхонь, змішування, скель, поля/луки/сухої землі/піску/снігової лінії та роботи на крутих схилах. Поточний OSM-класифікатор залишає paved, forest, park, residential; невпізнані farmland/meadow/scrub/bare_rock/beach тощо не доходять у пакет як потрібні природні класи. [Матеріали](C:/js/EarthNG/earthng/fast_style.py:288), [класифікатор](C:/js/EarthNG/earthng/fast_style.py:172), [відсів у reader](C:/js/EarthNG/earthng/fast_sources.py:580).
- Рослинність — чотири stock-моделі листяних дерев, незалежно від регіону/висоти/ухилу. Near: jittered global lattice 9 м для forest, 14 м park, 22 м residential, ліміт 900 на тайл, deterministic hash, перевірка доріг/будинків/води. Far: крок 80 м, максимум 40 дерев/тайл, окремий seed `far:`, отже не вкладена підмножина near. [Асортимент](C:/js/EarthNG/mod/lua/ge/extensions/e2r/generator.lua:21), [near](C:/js/EarthNG/mod/lua/ge/extensions/e2r/generator.lua:1722), [far](C:/js/EarthNG/mod/lua/ge/extensions/e2r/generator.lua:1641).
- Кожне дерево — TSStatic з `useInstanceRenderData=true`; не слід стверджувати, що інстансинг зовсім відсутній. Але створення/видалення об'єктів залишається окремою роботою: код обмежує видалення до 60 об'єктів/кадр. Лісові дерева без колізії. [Рендерер](C:/js/EarthNG/mod/lua/ge/extensions/e2r/world.lua:305), [видалення](C:/js/EarthNG/mod/lua/ge/extensions/e2r/world.lua:343), [static manifest](C:/js/EarthNG/mod/lua/ge/extensions/e2r/generator.lua:1589).
- Море — плоскі полігони на `seaLevel + .3`, а озера/річкові OSM-полігони drape-яться по трикутниках ґрунту як інші covers: потенційно виходять схилені/горбисті водойми. У far ці прісноводні полігони відсутні. [Вода](C:/js/EarthNG/mod/lua/ge/extensions/e2r/generator.lua:1406), [drape](C:/js/EarthNG/mod/lua/ge/extensions/e2r/generator.lua:1250).
- Сцена вже має ScatterSky, CloudLayer, TimeOfDay, precipitation, wind, тіні 1400 м; `visibleDistance=7000`, `fogDensity=.00019`. Для 15–30 км гірської панорами потрібно явно розширювати видимість і налаштовувати повітряну перспективу. [Оточення](C:/js/EarthNG/earthng/build_mod.py:27), [дальність](C:/js/EarthNG/earthng/build_mod.py:49).

### Inferences

- Порядок причин: відсутній далекий рельєф → слабкі природні класи/макроколір → фасетне освітлення → одноманітний ліс/переходи → місцева деталізація. Це архітектурний висновок, а не кількісна оцінка внеску в картинку.
- Прикручування високодетального DEM без LOD, акуратного road shaping і гладких нормалей може лише збільшити витрати й виразність артефактів.
- Для нового класифікатора доведеться перезібрати джерельні пакети: відкинуті природні полігони неможливо відновити однією заміною Lua. Гладкі нормалі та перший far terrain можуть працювати зі вже наявними v2-висотами.

### Gaps

- Не виміряно відповідність нинішніх висот реальним вершинам, джерельна точність окремих Terrarium-тайлів, абсолютний vertical datum, фактична картинка гір і вартість GPU.
- У цьому аудиті немає підтвердження доступності конкретних високодетальних stock conifer/shrub/grass ассетів у версії гри користувача. Перелік слід отримати із встановленої гри, а не вигадувати шляхи.
- Obsidian-картка й стан EarthNG датовані 13–14 жовтня і ще описують компаньйон. Для поточної архітектури перевагу надано коду та docs 16–17 жовтня. [Картка](<C:/Users/Alezx311/Documents/Obsidian Vault/Портфоліо/EarthNG.md>), [стан](<C:/Users/Alezx311/Documents/Obsidian Vault/EarthNG/Стан.md>).

## Які зміни дадуть найбільшу користь без втрати native-режиму?

### Takeaway

Перший вертикальний зріз — реальний безперервний рельєф до горизонту, гладкі нормалі, 4–6 природних типів покриття на одному гірському полігоні. Не потрібно починати з мережевого streaming або перебудови всього рендерера.

### Cited Findings

- Поточні кеш-ліміти: 32 cells, 160 tiles, 80000 triangles, 240000 vertices для geometry, що ще не передана renderer; це **не ліміт усіх GPU-полігонів**. Після доставки масиви meshes звільняються. Бюджет native update за замовчуванням 2 мс, дозволений параметр 0,1–8 мс; синхронний `createMesh` не переривається yield. [Ліміти](C:/js/EarthNG/mod/lua/ge/extensions/e2r/native.lua:8), [облік](C:/js/EarthNG/mod/lua/ge/extensions/e2r/native.lua:515), [update](C:/js/EarthNG/mod/lua/ge/extensions/e2r/native.lua:623), [release](C:/js/EarthNG/mod/lua/ge/extensions/e2r/native.lua:729), [createMesh](C:/js/EarthNG/mod/lua/ge/extensions/e2r/world.lua:294).
- Документовані попередні виміри, не повторені тут: native replay 83 м/с на 10,2 км — mean 6,6 мс, p99 35 мс, 7 кадрів понад 100 мс; `reloadCollision` 55–80 мс. Це орієнтир старого тесту, не гарантований бюджет нової природи. [Таблиця](C:/js/EarthNG/docs/NATIVE_MODS.md:226), [колізії](C:/js/EarthNG/docs/NATIVE_MODS.md:239).

### Inferences

Пропонована послідовність патчів і критерії приймання:

1. **P0 — окремий render-only terrain horizon.** Точки: `generator.lua` біля 1439/1827, `native.lua` priorities/buildTile/cache, `world.lua` far lifecycle і makeHorizon. Спершу coarse mesh 100 м у вже існуючому far-кільці для швидкого контрольного доказу. Потім окремі DEM-only плитки/піраміда 5→15→30 км, без OSM-декору, колізій і правила вимкнення far на 90 км/год. Горизонт не має залежати від того, чи існують поблизу дороги/будівлі. Зберігати coarse coverage, поки fine готове; атомарна заміна шарів, межові вершини спільні, stitching/fan або skirt як тимчасовий страховий механізм. Near модифіковано дорогами, тому seam не забезпечується простим raw DEM: потрібен спільний boundary height або перехідний пояс. Приймання: гора у 2 км присутня відразу після готовності coarse шару, 5/15/30 км пізніше; no holes у погляді з висоти/серпантину, жодного повернення плаского spawn−40 горизонту; 90/150/300 км/год не прибирають гір.
2. **P0 — гладкі нормалі, правильні межі.** Точки: collector triangle/corner та ground(). Додати normals-per-corner лише землі/покриттям; нормаль із канонічних світових heights і стабільного кроку центральних різниць, з halo сусідніх source cells або precomputed boundary gradients. Зараз rawHeight поза доступним grid повертає 0 — не можна так рахувати нові крайові похідні. Не згладжувати бордюри/стіни/скелі автоматично. Приймання: скалярний добуток нормалей по шву близький до 1; відсутня світлова сітка 25 м за косого сонця; geometry-road regression без погіршення.
3. **P1 — природні покриття та slope/altitude rules.** Точки: fast_style.ground_kind, fast_sources.Reader.area, build_country/country_format, generator CLAIM/drape/material collector; fast_photo/fast_art. Зберегти теги meadow, farmland, orchard, vineyard, scrub, heath, bare_rock, scree, beach, wetland, glacier; при неповних OSM — land-cover raster як джерело макрокласу, не випадковий шум замість географії. Почати з простого material selection/макроколірних масок, перевірити API перед складним splat/triplanar shader. Для крутих поверхонь проєкція XY розтягує текстуру — потрібна slope-aware проєкція або підтриманий шейдер. Приймання: поля мають реальні границі, ліс/лука/скеля читаються з 2–5 км; масштаби near/far збігаються; всі нові класи мають offline fallback і фізичний groundType.
4. **P1 — екорегіональна рослинність і стабільний LOD.** Точки: TREES, candidates, far canopy, spawnItem. Conifer/mixed/deciduous, кущі, підлісок і grass ближче до камери, залежність від slope, altitude, water exclusion і виявленого land-cover. Один stable seed; far підмножина near або proxy canopy із тією ж щільністю. Не просто підняти 900→5000: спочатку профіль object creation і render batches, потім дослідити Forest/інший native instancing. Приймання: немає дубльованих дерев на границях, дерев на скелях/вище treeline/у воді, однаковий seed після повторного приїзду, зникнення near не оголює ліс різким стрибком.
5. **P1 — коректні водойми.** Відділити плоскі озера від річок зі спадаючим поздовжнім профілем; shoreline і русло поєднати із DEM, а воду не drape-ити як grass. Приймання: рівне озеро поперек кількох тайлів, узгоджені береги/near-far, відсутність води на гірському схилі; однаковий vertical datum і world lift.
6. **P2 — точніше DEM і атмосферний look.** Провести A/B існуючого zoom12 з доступним кращим DEM на гірському полігоні, виміряти силует/профіль/розмір ZIP. Для far достатньо downsampled pyramid; near 10/5 м тільки там, де джерело містить відповідну інформацію. Підтвердити, що despike не знищує справжній хребет; замість безумовного median використовувати source QA/void masks, slope-aware фільтр. Після геометрії — fog/sky/exposure/shadow-distance; screenshot з гри, фіксований час і погода.

Оцінка тільки геометрії, **не результат FPS**: для круглого terrain диска `triangles ≈ 2πR²/s²` без transition geometry. 100 м: R=5 км ≈15 708, 15 км ≈141 372, 30 км ≈565 487. 250 м: 30 км ≈90 478. Варіант кілець 0–1 км/25 м, 1–5/50 м, 5–15/100 м, 15–30/250 м ≈263 894 трикутники. Межі 25→50→100→250 не всі кратні як power-of-two LOD; для реалізації зручніше узгоджена ієрархія 25→50→100→200/400 з окремим підрахунком. Coarse terrain дешевий у полігонах, але source IO, Lua tables, шви й синхронний createMesh можуть бути дорожчими за самі трикутники. Не слід наївно збільшувати старе 500-метрове far-кільце до 30 км: це порядку 11 тисяч source/decor tiles замість 160 у нинішньому кеші.

### Gaps

- Не підтверджено працездатність конкретного custom triplanar/splat shader, Forest instance API або TerrainBlock dynamic streaming у поточній версії BeamNG. Це короткі технічні прототипи, не залежність першого P0.
- Не оцінені трудодні: потрібний вибір одного полігона, доступний DEM, перевірка ассетів і перший runtime-profile. Порядок пріоритетів підтверджений кодом; строки не доведені.

## Чим підтверджено висновки й як перевіряти результат?

### Takeaway

Відсутність далекої землі відтворена виконанням справжнього Lua-генератора, а не лише прочитана в коді. Наявні тести зелені саме тому, що наразі допускають цю обмежену архітектуру; нові критерії потрібні для силуетів, LOD і природної географії.

### Cited Findings

- **Виконано 2026-10-17:** `python -m unittest discover -s earthng -p test_generator.py -q`: 25 tests, OK, 0.889 s. `python -m unittest discover -s earthng -p test_native.py -q`: 20 tests, OK, 0.237 s. Це unit tests у LuaJIT через lupa, не запуски гри. [Test harness](C:/js/EarthNG/earthng/test_generator.py:20), [native harness](C:/js/EarthNG/earthng/test_native.py:1).
- `python -m unittest discover -s earthng -p test_country.py -q` не зміг імпортувати модуль: `ModuleNotFoundError: No module named 'shapely'` у системному `C:/Python314/python.exe`. Залежності не встановлювалися. Це середовищний блок, не тестовий дефект country export. [Імпорт](C:/js/EarthNG/earthng/test_country.py:11).
- **Виконана синтетична перевірка:** terrain 1000 × 1000 м, сітка 3 × 3, центр 400 м, решта 20 м. Tile (0,0), без road/features, defaults. Near: 800 `e2rf_ground` triangles, Z=19.75…399.75. Far: 0 ground triangles, 0 triangles загалом. Джерело execution — той самий [generator](C:/js/EarthNG/mod/lua/ge/extensions/e2r/generator.lua:601), [helper build](C:/js/EarthNG/earthng/test_generator.py:40).
- Наявний seam test порівнює road/terrain across tiles на пласкому DEM; country test перевіряє grid seam після quantization. Окремого набору mountain LOD/horizon/smooth-normal тестів у переглянутих файлах немає. [Terrain seam](C:/js/EarthNG/earthng/test_generator.py:86), [binary seam](C:/js/EarthNG/earthng/test_country.py:118).
- Стенд `bench_native.py` уже вимірює tile ms, decode, Lua heap/tables, time slices; `test_world.py --native` використовує тільки гру з country ZIP, підтримує replay і галерею. `--replay` явно не є physics test. [Bench](C:/js/EarthNG/earthng/bench_native.py:212), [режими](C:/js/EarthNG/earthng/test_world.py:49), [native без server](C:/js/EarthNG/earthng/test_world.py:156). Road-quality стенд варто лишити regression guard після terrain changes. [Опис](C:/js/EarthNG/docs/NATIVE_MODS.md:248).

Відтворення синтетичного результату з PowerShell у `C:\js\EarthNG` (нічого не записує в код):

```powershell
@'
import sys
sys.path.insert(0, 'earthng')
from test_generator import GeneratorTests
x = GeneratorTests(); x.setUp()
cell = {'terrain': {'rect': [0,0,1000,1000], 'nx': 3, 'ny': 3,
                   'heights': [20,20,20,20,400,20,20,20,20]}}
for far in (False, True):
    road, scene, distant, nav = x.build([cell], far=far)
    m = distant if far else road
    ts = list(x.triangles(m, 'e2rf_ground'))
    zs = [p[2] for t in ts for p in t]
    print(far, len(ts), (min(zs), max(zs)) if zs else None, m.triangles)
'@ | python -
```

### Inferences

Мінімальна матриця для наступної реалізації:

- Synthetic hill/valley/ridge: exact world-space heights; far обов'язково має ground, увігнутий профіль не перетворюється на flat horizon. Near/far межа перевіряється і там, де дорога змінила DEM.
- Shared boundaries у двох незалежно завантажених source cells та на negative coordinates: однакові XYZ/нормалі; усі mesh positions/UV/normals finite; skirt не перекриває дорогу.
- Два сусідні рівні LOD: deterministic edge samples, немає T-junction щілин у рендері; при русі/телепорті coarse живе до готового fine; не одночасно два співплощинні шари, що дають z-fighting.
- Real fixtures: карпатська лісова долина й серпантин, високий кам'янистий хребет, відкриті поля, берег із озером, тропічне узбережжя. Для кожного одна камера/погода/час, скріни з салону/узбіччя/оглядової висоти, видимий горизонт у 5/15/30 км, аналіз осі рельєфу та профілів дороги.
- Road shaping: не погіршити road_quality metrics, не утворити широкий траншейний слід 70 м на схилі; tree/grass placements користуються остаточною surface height.
- Perf: same hardware/profile/settings, cold/warm окремо; mean/p95/p99 frame time, >50/100/200 мс, peak Lua heap, pending geometry, draw calls/triangles, object churn, collision rebuild ms, ready lead time. Fixed native update budget не піднімати для маскування повільної генерації. Для початку target — не гірше baseline p99 більш ніж на 10% і відсутність нових пропусків near collision deadlines; це запропонований поріг, не вже досягнутий результат.
- Performance replay 90/150/300 км/год + окремий фізичний проїзд у горах; replay не доводить driveability.

### Gaps

- BeamNG у цій дослідницькій підзадачі не запускався; немає нового FPS, GPU memory, screenshot A/B або physics результату. Числа з попередніх docs навмисно відокремлено від власних unit/synthetic runs.
- До реалізації потрібно вибрати/підготувати гірський source-pack із достатнім покриттям горизонту; 5 × 5 км demo-пакет принципово не підтвердить 15–30 км дальність.
