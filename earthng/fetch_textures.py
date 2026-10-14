"""Download the CC0 ambientCG sets the season packs add (fast_art: winter, autumn) into
data/textures/<set>/{albedo,normal,roughness}.jpg, with their sources in data/textures/manifest.json
(as Earth2Road's tools/fetch_textures.py does for the rest).

    python earthng/fetch_textures.py
"""
import hashlib
import io
import json
import subprocess
import zipfile

import paths

DEST = paths.PROJECT / 'data' / 'textures'
MAPS = {'Color': 'albedo', 'NormalGL': 'normal', 'Roughness': 'roughness'}
SETS = {
    'snow_fresh': 'Snow005',          # clean fluffy snow: lawns, roofs
    'snow_trodden': 'Snow010C',       # footsteps: yards and paths
    'snow_dirty': 'Snow012',          # grey molten snow: verges by the road
    'leaves_orange': 'ScatteredLeaves007',
    'leaves_brown': 'ScatteredLeaves002',
    'leaves_forest': 'Ground070',
}


def main():
    manifest_path = DEST / 'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    for name, asset in SETS.items():
        folder = DEST / name
        if manifest.get(name, {}).get('asset') == asset and all((folder / f'{m}.jpg').exists() for m in MAPS.values()):
            continue
        url = f'https://ambientcg.com/get?file={asset}_1K-JPG.zip'
        blob = subprocess.run(['curl', '-fsSL', '--retry', '2', '--max-time', '300', url], check=True, capture_output=True).stdout
        folder.mkdir(parents=True, exist_ok=True)
        files = {}
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for entry in z.namelist():
                for key, out in MAPS.items():
                    if entry.endswith(f'_{key}.jpg'):
                        data = z.read(entry)
                        (folder / f'{out}.jpg').write_bytes(data)
                        files[out] = hashlib.sha256(data).hexdigest()
        missing = set(MAPS.values()) - set(files)
        if missing:
            raise RuntimeError(f'{asset}: missing maps {missing}')
        manifest[name] = {'asset': asset, 'source': url, 'license': 'CC0-1.0 (ambientCG)',
                          'zip_sha256': hashlib.sha256(blob).hexdigest(), 'files': files}
        print('texture', name, asset)
    manifest_path.write_text(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
