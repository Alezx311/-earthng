#!/usr/bin/env bash
# EarthNG on Linux (experimental): builds and installs the mod, starts the game and the companion.
# The Linux counterpart of run_world.ps1:
#   bash run_world.sh [--tiles DIR] [--userfolder DIR] [--workers N] [--generator fast|v2] [--earth2road DIR] [--nogame]
# In the game: Freeroam -> the "Earth2Road" planet level. Keep this terminal open while playing.
# Python: Earth2Road's .venv when it is there, else .venv-linux here with requirements-fast.txt.
# The fast generator needs no Earth2Road; v2 does (its .venv and SUMO).
# BeamNG runs natively or under Proton; the profile is looked up in both, BEAMNG_USERFOLDER overrides.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; }

earth2road="${EARTH2ROAD:-$HOME/Earth2Road}"
tiles=""
userfolder="${BEAMNG_USERFOLDER:-}"
workers=8
generator=fast
nogame=0
while [ $# -gt 0 ]; do
	case "$1" in
		--tiles) tiles="$2"; shift 2 ;;
		--userfolder) userfolder="$2"; shift 2 ;;
		--workers) workers="$2"; shift 2 ;;
		--generator) generator="$2"; shift 2 ;;
		--earth2road) earth2road="$2"; shift 2 ;;
		--nogame) nogame=1; shift ;;
		-h|--help) usage; exit 0 ;;
		*) echo "unknown option: $1 (see --help)" >&2; exit 1 ;;
	esac
done
case "$generator" in fast|v2) ;; *) echo '--generator must be fast or v2' >&2; exit 1 ;; esac
case "$workers" in ''|*[!0-9]*) echo '--workers must be a positive number' >&2; exit 1 ;; esac

# python
if [ -x "$earth2road/.venv/bin/python" ]; then
	py="$earth2road/.venv/bin/python"
	export EARTH2ROAD="$earth2road"
else
	if [ "$generator" = v2 ]; then
		echo "--generator v2 needs Earth2Road with its .venv: not found at $earth2road (--earth2road DIR)" >&2
		exit 1
	fi
	venv="$root/.venv-linux"
	if [ ! -x "$venv/bin/python" ]; then
		command -v python3 >/dev/null || { echo 'python3 not found: install it (Debian/Ubuntu: sudo apt install python3 python3-venv)' >&2; exit 1; }
		echo 'First run: creating .venv-linux (needs internet, a few minutes)...'
		python3 -m venv "$venv" || { echo 'venv failed: install python3-venv (Debian/Ubuntu) and run again' >&2; rm -rf "$venv"; exit 1; }
		"$venv/bin/pip" install --quiet --upgrade pip
		if ! "$venv/bin/pip" install --quiet -r "$root/requirements-fast.txt"; then
			echo 'pip failed. osmium without a ready wheel builds from source: it needs cmake, a C++ compiler,' >&2
			echo 'libboost-dev, libexpat1-dev, zlib1g-dev, libbz2-dev (or use python 3.10-3.13).' >&2
			rm -rf "$venv"
			exit 1
		fi
	fi
	py="$venv/bin/python"
	export EARTH2ROAD="$root/none"   # fast imports nothing from Earth2Road
fi
export PYTHONNOUSERSITE=1
unset PYTHONPATH

# OSM tiles: --tiles, else Ukraine, else Kyiv, else the first package in data/osm_tiles
if [ -z "$tiles" ]; then
	for d in "$root/data/osm_tiles/ukraine-260918" "$root/data/osm_tiles/kyiv-260918" "$root"/data/osm_tiles/*; do
		if [ -f "$d/index.json" ]; then tiles="$d"; break; fi
	done
fi
if [ -z "$tiles" ] || [ ! -f "$tiles/index.json" ]; then
	echo "No OSM tiles (data/osm_tiles/<name>/index.json). Cut them from a Geofabrik .osm.pbf:" >&2
	echo "  $py earthng/osm_tiles_country.py <country.osm.pbf> --out data/osm_tiles/<name>" >&2
	echo "  $py earthng/fast_dem.py --download ...   (DEM, see the script)" >&2
	exit 1
fi
tiles="$(cd "$tiles" && pwd)"

# BeamNG profile: native Linux build, then the Proton prefix in the usual Steam roots
if [ -z "$userfolder" ]; then
	proton='steamapps/compatdata/284160/pfx/drive_c/users/steamuser/AppData/Local'
	for c in \
		"$HOME/.local/share/BeamNG/BeamNG.drive/current" \
		"$HOME/.local/share/BeamNG.drive/current" \
		"$HOME/.steam/steam/$proton/BeamNG/BeamNG.drive/current" \
		"$HOME/.local/share/Steam/$proton/BeamNG/BeamNG.drive/current" \
		"$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam/$proton/BeamNG/BeamNG.drive/current" \
		"$HOME/.steam/steam/$proton/BeamNG.drive/current" \
		"$HOME/.local/share/Steam/$proton/BeamNG.drive/current"; do
		if [ -d "$c" ]; then userfolder="$c"; break; fi
	done
fi
if [ -z "$userfolder" ]; then
	echo 'BeamNG user folder not found. Start the game once, then run again,' >&2
	echo 'or pass it: --userfolder "/path/to/BeamNG.drive/current" (the folder with mods/ inside)' >&2
	exit 1
fi
echo "BeamNG user folder: $userfolder"
echo "OSM tiles: $tiles"

n=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)
if [ "$workers" -gt "$n" ]; then workers=$n; fi
if [ "$workers" -lt 1 ]; then workers=1; fi

cd "$root"
"$py" earthng/build_mod.py --legacy --out out/mod --install "$userfolder"
if [ "$nogame" -eq 0 ]; then
	echo 'Starting BeamNG.drive via Steam...'
	if command -v steam >/dev/null; then
		nohup steam steam://rungameid/284160 >/dev/null 2>&1 &
	elif command -v xdg-open >/dev/null; then
		xdg-open steam://rungameid/284160 >/dev/null 2>&1 || true
	else
		echo 'steam not found: start BeamNG.drive yourself.'
	fi
fi
echo "Companion is running ($workers workers, generator $generator). In BeamNG: Freeroam -> Earth2Road planet level. Ctrl+C here to stop."
exec "$py" earthng/world_server.py --tiles "$tiles" --userfolder "$userfolder" --workers "$workers" --generator "$generator"
