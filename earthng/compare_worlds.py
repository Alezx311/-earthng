"""Byte-level oracle for generator speed-ups: same inputs must give the same world and export.

    C:/js/Earth2Road/.venv/Scripts/python.exe earthng/compare_worlds.py out/streaming/profile-v2-plain out/streaming/profile-v2-try1

Both arguments are profile_land.py outputs. For every window present in both it compares
world/index.json, world/tiles/*.json and world/network.net.xml byte for byte, and the set of
exported DAE files (their names carry a content digest) plus the level materials. Prints the
first differences and exits 1 when anything differs.
"""
import argparse
import hashlib
from pathlib import Path
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files(root, patterns):
    out = {}
    for pattern in patterns:
        for p in root.glob(pattern):
            if p.is_file():
                out[p.relative_to(root).as_posix()] = digest(p)
    return out


def level_files(beamng):
    levels = list((beamng / 'mod/levels').iterdir()) if (beamng / 'mod/levels').exists() else []
    if not levels:
        return {}
    level = levels[0]
    found = {p.name: digest(p) for p in (level / 'art/shapes').glob('*.dae')}
    mats = level / 'art/kyiv/main.materials.json'
    if mats.exists():
        found['main.materials.json'] = digest(mats)
    return found


def compare(a, b, label):
    bad = 0
    for key in sorted(set(a) | set(b)):
        if a.get(key) != b.get(key):
            bad += 1
            if bad <= 8:
                state = 'missing in new' if key not in b else 'new only' if key not in a else 'differs'
                print(f'  {label}: {key} {state}')
    return bad


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('base', type=Path)
    p.add_argument('new', type=Path)
    a = p.parse_args()
    total = 0
    for window in sorted(d.name for d in a.base.iterdir() if d.is_dir() and (a.new / d.name).is_dir()):
        wa, wb = a.base / window, a.new / window
        bad = compare(files(wa / 'world', ['index.json', 'network.net.xml', 'tiles/*.json']),
                      files(wb / 'world', ['index.json', 'network.net.xml', 'tiles/*.json']), 'world')
        bad += compare(level_files(wa / 'beamng'), level_files(wb / 'beamng'), 'export')
        print(f'{window}: {"identical" if not bad else f"{bad} differences"}')
        total += bad
    sys.exit(1 if total else 0)


if __name__ == '__main__':
    main()
