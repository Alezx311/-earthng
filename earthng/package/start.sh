#!/usr/bin/env bash
# EarthNG on Linux (experimental): installs the mod, starts the companion and the game.
# The package's python/ is Windows-only, so this uses the system python3 with a venv in ./venv-linux.
#   bash start.sh [--userfolder DIR] [--workers N] [--nogame]
# BeamNG runs natively or under Proton; the profile is looked up in both, BEAMNG_USERFOLDER overrides.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
userfolder="${BEAMNG_USERFOLDER:-}"
workers=0
nogame=0
while [ $# -gt 0 ]; do
	case "$1" in
		--userfolder) userfolder="$2"; shift 2 ;;
		--workers) workers="$2"; shift 2 ;;
		--nogame) nogame=1; shift ;;
		*) echo "unknown option: $1" >&2; exit 1 ;;
	esac
done
case "$workers" in ''|*[!0-9]*) echo '--workers must be a number' >&2; exit 1 ;; esac

# python: system python3 (3.10+) with numpy, shapely, pyproj, osmium, Pillow in a venv
venv="$root/venv-linux"
if [ ! -x "$venv/bin/python" ]; then
	command -v python3 >/dev/null || { echo 'python3 not found: install it (Debian/Ubuntu: sudo apt install python3 python3-venv)' >&2; exit 1; }
	echo 'First run: creating a Python environment (needs internet, a few minutes)...'
	python3 -m venv "$venv" || { echo 'venv failed: install python3-venv (Debian/Ubuntu) and run again' >&2; rm -rf "$venv"; exit 1; }
	"$venv/bin/pip" install --quiet --upgrade pip
	if ! "$venv/bin/pip" install --quiet numpy shapely pyproj osmium Pillow; then
		echo 'pip failed. osmium without a ready wheel builds from source: it needs cmake, a C++ compiler,' >&2
		echo 'libboost-dev, libexpat1-dev, zlib1g-dev, libbz2-dev (or use python 3.10-3.13).' >&2
		rm -rf "$venv"
		exit 1
	fi
fi
py="$venv/bin/python"

# packages built before Linux support: fast_art.py caught only OSError around `import winreg`,
# and world_server.py read LOCALAPPDATA unconditionally
sed -i 's/^    except OSError:$/    except (OSError, ImportError):/' "$root/earthng/fast_art.py"
export LOCALAPPDATA="${LOCALAPPDATA:-$HOME}"

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
	echo 'or pass it: bash start.sh --userfolder "/path/to/BeamNG.drive/current" (the folder with mods/ inside)' >&2
	exit 1
fi
echo "BeamNG user folder: $userfolder"

mkdir -p "$userfolder/mods"
cp -f "$root/mod/earthng_planet.zip" "$userfolder/mods/earthng_planet.zip"
echo 'Mod installed: mods/earthng_planet.zip'

tiles=""
for d in "$root"/data/osm_tiles/*/; do
	if [ -f "$d/index.json" ]; then tiles="${d%/}"; break; fi
done
[ -n "$tiles" ] || { echo 'No OSM tiles in data/osm_tiles' >&2; exit 1; }
echo "OSM data: $tiles"

if [ "$workers" -le 0 ]; then
	n=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)
	workers=$(( n / 2 ))
	if [ "$workers" -lt 2 ]; then workers=2; fi
	if [ "$workers" -gt 8 ]; then workers=8; fi
fi

export EARTH2ROAD="$root/none" PYTHONNOUSERSITE=1
unset PYTHONPATH
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
echo ''
echo "Companion is running ($workers workers). In game: Freeroam -> 'Earth2Road'."
echo 'Keep this terminal open while playing. Ctrl+C to stop.'
cd "$root"
exec "$py" "$root/earthng/world_server.py" \
	--tiles "$tiles" \
	--userfolder "$userfolder" --work "$root/work" --cache "$root/data/dem" \
	--regions "$root/work/regions" --workers "$workers" --generator fast
