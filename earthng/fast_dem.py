"""Audit/download raw Terrarium sources. No road profiles or geometry are prepared here."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import time
import urllib.request


def audit(index, cache):
    """DEM under every source tile's coverage, not the package box: a country's box is half
    sea and neighbours (Ukraine: OSM tiles only where the data is)."""
    tile=lambda lon,lat: ((lon+180)/360*4096,(1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*4096)
    keys=set()
    for t in index['tiles'].values():
        w,s,e,n=t['coverage']
        x0,y0=tile(w,n); x1,y1=tile(e,s)
        # One extra tile handles complete OSM features and filtered edge samples.
        keys.update((x,y) for x in range(math.floor(x0)-1,math.floor(x1)+2)
                    for y in range(math.floor(y0)-1,math.floor(y1)+2))
    return [Path(cache)/'terrain/12'/str(x)/f'{y}.png' for x,y in sorted(keys)]


def fetch(path):
    from PIL import Image
    if path.exists():
        return
    url='https://s3.amazonaws.com/elevation-tiles-prod/terrarium/12/'+path.parent.name+'/'+path.name
    req=urllib.request.Request(url,headers={'User-Agent':'EarthNG/0.1 (+https://github.com/Alezx311/earth2road)'})
    with urllib.request.urlopen(req,timeout=45) as response:
        data=response.read()
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.png.part'); tmp.write_bytes(data)
    with Image.open(tmp) as image:
        if image.size!=(256,256):
            raise ValueError(f'invalid DEM {url}')
        image.verify()
    for attempt in range(10):   # an antivirus may hold the fresh .part for a moment (WinError 32)
        try:
            os.replace(tmp,path); return
        except PermissionError:
            if attempt==9: raise
            time.sleep(1)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',required=True); p.add_argument('--cache',required=True)
    p.add_argument('--download',action='store_true')
    a=p.parse_args(); index=json.loads((Path(a.sources)/'index.json').read_text())
    paths=audit(index,a.cache); missing=[p for p in paths if not p.exists()]
    print(f'DEM: {len(paths)-len(missing)}/{len(paths)} local; {len(missing)} missing',flush=True)
    if a.download:
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(fetch,missing))
        print('Raw DEM sources ready; no derived data generated.',flush=True)
    elif missing:
        raise SystemExit(1)


if __name__=='__main__':
    main()
