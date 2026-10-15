EarthNG - real places in BeamNG.drive (test build)
==================================================

What it is
----------
A mod for BeamNG.drive: pick any point on the map (a region of the United Kingdom, the whole
state of California, the whole state of New York, Zanzibar or Romania, depending on the archive), "land" there and drive
real streets from
OpenStreetMap. Roads, terrain, buildings and trees are generated on the fly around the car by a
separate companion program (it is in this archive together with Python and the data).

Requirements
------------
- Windows 10/11, BeamNG.drive 0.39 from Steam.
- ~20 GB of free space (the archive, unpacked files and the tile cache), 16 GB RAM (32 is
  better), 4+ CPU cores (8+ is better: buildings are generated in parallel).
- Internet only for the map picture when picking a point (roads are generated offline).

Installation
------------
1. Unpack the archive into a folder with no spaces or non-Latin letters in its path,
   for example C:\EarthNG.
2. Nothing else to install: Python and all the data are already inside.

Linux (experimental)
--------------------
The bundled Python is Windows-only, so Linux needs the system python3 (3.10+) with venv
(Debian/Ubuntu: sudo apt install python3 python3-venv). In the package folder:
    bash start.sh
The first run installs the Python packages into venv-linux/ (needs internet). The BeamNG
profile is looked up for both the native build and Proton; if it is not found:
    bash start.sh --userfolder "/path/to/BeamNG.drive/current"
Other options: --workers N, --nogame. The launcher window (EarthNG.bat) is Windows-only for now.

Starting
--------
1. Double-click EarthNG.bat. A launcher window opens with the settings: the BeamNG user
   folder, the OSM data, the number of generation processes, the far view radius and whether
   to start the game. Settings are remembered (launcher.json). The "Start" button:
   - copies the mod to <user folder>\mods\earth2road_world.zip;
   - starts BeamNG.drive via Steam (if ticked);
   - starts the companion; its log is shown at the bottom of the window. Do NOT close the
     window while you play.
   Fallback without the window: start.bat (the same in a console, default settings).
2. In the game: Freeroam -> the level "Earth2Road - Planet".
3. A window with a map appears: pick a point (a click, coordinates or a city button:
   London, Bristol, Manchester, Leeds, Cardiff, Belfast, Edinburgh, Inverness, Glencoe... in the UK archives,
   each region its own; Los Angeles,
   San Francisco, San Diego, Sacramento... in the California archive; Times Square, Brooklyn,
   Buffalo, Niagara Falls... in the New York archive; Stone Town, Nungwi, Paje, Chake Chake...
   in the Zanzibar archive; Bucharest, Cluj-Napoca, Brasov, Transfagarasan... in the Romania archive) and
   press "Land here".
   A point on a road works best.
4. The first landing in a new area takes 40-70 s (indexing the roads around the point). After
   that, a few seconds. You can drive; the world is generated ahead of the car.

In the game
-----------
- The Earth2Road mini window: tile status, the "Pick another place" button and the style
  switch: Realistic / Cartoon / Mock-up / Synthwave / Night / Rain / Cyberpunk / Winter / Autumn.
  Night, Rain and Cyberpunk have street lights and lit windows; Rain and Winter have
  precipitation.
- The area around a landing is from ~55x55 km (big cities) to ~120x120 km (countryside).
  3 km before its edge a warning appears: there are no roads further, land at a new point
  ("Pick another place"). A new landing anywhere in the package takes 40-70 s again.
- The road appears first, buildings and trees follow a few seconds later.
- Traffic: the "Off / Light / Medium / Heavy" buttons in the mini window. The game's cars
  drive on the roads already generated around you (about 1 km) and reappear ahead.
- Weather and time of day: the game's usual Environment menu - time, clouds, rain, fog,
  wind. The sun stands as it does at the landing point.
- Known limitation: building styles (facades, apartment blocks) are still based on
  Ukrainian cities.

If something goes wrong
-----------------------
- A different BeamNG user folder:  the "BeamNG user folder" field in the launcher
                                   (or start.bat -UserFolder "D:\BeamNG\current")
- A weak PC / little memory:       "Generation processes" = 2, "Far view" = 1500
                                   (or start.bat -Workers 2)
- The game was not found for facade textures: set the BEAMNG_DIR variable to the game folder
  (where BeamNG.drive.exe is), otherwise facades are single-coloured.
- "The companion is not responding" in the game: check that the launcher window is open and
  its log shows no errors.
- For a problem report: a screenshot, the landing point and the files
  work\fast-index\*\region.json, plus the log from the launcher window.

Data: (c) OpenStreetMap contributors (ODbL), terrain - Terrarium (AWS Open Data / Mapzen).
