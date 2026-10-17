"""Validate a native style gallery and its packaged art; write style-validation.json."""
import argparse
import json
from pathlib import Path
import zipfile

import numpy as np
from PIL import Image

import fast_art
import fast_style


def validate(run, archive):
    failures, images = [], {}
    with zipfile.ZipFile(archive) as z:
        names = set(z.namelist())
        pack_path = fast_style.ART.lstrip('/') + '/packs.json'
        packs = json.loads(z.read(pack_path))

        def check_paths(value):
            if isinstance(value, dict):
                for item in value.values():
                    check_paths(item)
            elif isinstance(value, list):
                for item in value:
                    check_paths(item)
            elif isinstance(value, str) and value.startswith('/e2r_stream/fast-art/'):
                if not value.startswith(fast_style.ART + '/') or value.lstrip('/') not in names:
                    failures.append('missing or stale packaged art: ' + value)

        check_paths(packs)
        check_paths(json.loads(z.read('earthng/planet/materials.json')))
        if 'pixel' not in packs['order']:
            failures.append('pixel missing from picker')
        if not set('e2rf_' + k for k in fast_style.KINDS) <= set(packs['fields']['pixel']):
            failures.append('incomplete pixel materials')
    report = json.loads((run / 'report.json').read_text(encoding='utf8'))
    game = report['game']
    shots = game.get('shotState', {})
    checked = 0
    for name, state in shots.items():
        pack = name.split('-')[0]
        if pack not in packs['environment']:
            continue
        checked += 1
        env = packs['environment'][pack]
        if env.get('authoredSun') and abs(float(state[4]) - float(env['objects']['sunsky']['elevation'])) > .05:
            failures.append(name + ': sun overwritten after pack application')
        if 'exposure' in env and (state[0] != 'false' or abs(state[1] - env['exposure']) > .01):
            failures.append(name + ': wrong exposure')
        image = run / f'e2r-{name}.png'
        with Image.open(image) as im:
            a = np.asarray(im.convert('RGB'), dtype=float)
        lum = a @ np.array([.2126, .7152, .0722])
        images[name] = {'mean': round(float(lum.mean()), 2),
                        'clipped_pct': round(float((a.min(axis=2) >= 250).mean() * 100), 3),
                        'black_pct': round(float((lum < 5).mean() * 100), 3)}
        if pack in ('night', 'rain', 'cyber'):
            if images[name]['clipped_pct'] > 1:
                failures.append(name + ': more than 1% clipped white')
            if images[name]['black_pct'] > 95:
                failures.append(name + ': fewer than 5% visible pixels')
    if not checked:
        failures.append('no style gallery measurements')
    for failure in game.get('failures', []):
        if failure != 'frame_over_100ms':
            failures.append(failure)
    if game.get('status') not in ('passed', 'failed'):
        failures.append('incomplete game run')
    result = {'status': 'failed' if failures else 'passed', 'art': fast_art.VERSION,
              'failures': failures, 'gallery_views': checked, 'images': images,
              'performance_status': report.get('performance_status', 'unknown')}
    (run / 'style-validation.json').write_text(json.dumps(result, indent=2), encoding='utf8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--zip', type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.run, args.zip)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['status'] == 'passed' else 1)


if __name__ == '__main__':
    main()
