"""Optional offline ESA WorldCover 2021 v200 GeoTIFF -> ordinary v2 areas.

Only the current kilometre is warped into a bounded, region-aligned 25 m grid.
Nearest-neighbour preserves class codes; NoData/built-up remain unknown. OSM
footprints override the fallback. Nothing here is imported by the game.
"""
import hashlib
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import shape

from nature import WORLD_COVER


def provenance(paths):
    try:
        import rasterio
    except ImportError as exc:
        raise ValueError('--landcover requires requirements-nature.txt in the authoring environment') from exc
    files = []
    for path in sorted({Path(p).resolve() for p in paths}):
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        with rasterio.open(path) as ds:
            files.append({'file': path.name, 'sha256': digest,
                          'crs': str(ds.crs), 'bounds': list(ds.bounds), 'pixelSize': list(ds.res)})
    return {'source': 'ESA WorldCover 2021 v200', 'license': 'CC-BY-4.0',
            'url': 'https://esa-worldcover.org/en/data-access',
            'attribution': '© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium',
            'nativeResolution': '10 m nominal', 'exportResolutionMetres': 25,
            'resampling': 'nearest', 'files': files}


class WorldCover:
    def __init__(self, paths, frame):
        try:
            import rasterio
        except ImportError as exc:
            raise ValueError('--landcover requires requirements-nature.txt in the authoring environment') from exc
        self.frame, self.datasets = frame, []
        try:
            for path in sorted({str(Path(p).resolve()) for p in paths}):
                ds = rasterio.open(path)
                self.datasets.append(ds)
                if not ds.crs or ds.count != 1 or ds.dtypes[0] != 'uint8':
                    raise ValueError('WorldCover must be a georeferenced single-band uint8 class raster')
        except Exception:
            self.close()
            raise

    def close(self):
        for ds in self.datasets:
            ds.close()

    def areas(self, rect, overrides):
        from affine import Affine
        from rasterio.features import shapes
        from rasterio.warp import reproject, Resampling
        import rasterio
        step = 25
        x0, y0, x1, y1 = rect
        width, height = round((x1-x0)/step), round((y1-y0)/step)
        if width <= 0 or height <= 0 or width * height > 100000:
            raise ValueError('landcover export window exceeds bounded cell size')
        transform = Affine(step, 0, x0+self.frame.cx, 0, -step, y1+self.frame.cy)
        values = np.zeros((height, width), dtype=np.uint8)
        for ds in self.datasets:
            tile = np.zeros_like(values)
            reproject(rasterio.band(ds, 1), tile, src_nodata=ds.nodata if ds.nodata is not None else 0,
                      dst_transform=transform, dst_crs=self.frame.crs, dst_nodata=0,
                      resampling=Resampling.nearest, num_threads=1, warp_mem_limit=16)
            known = np.isin(tile, list(WORLD_COVER))
            # Stable source ordering; unknown/built-up never erase known coverage.
            values[(values == 0) & known] = tile[(values == 0) & known]
        mask = np.isin(values, list(WORLD_COVER))
        exclude = shapely.union_all(shapely.make_valid(overrides)) if overrides else None
        local = Affine(step, 0, x0, 0, -step, y1)
        for geometry, code in shapes(values, mask=mask, transform=local):
            polygon = shape(geometry)
            if exclude is not None:
                polygon = shapely.difference(polygon, exclude)
            if not polygon.is_empty:
                yield WORLD_COVER[int(code)], polygon, {'source': 'worldcover-2021-v200', 'class': str(int(code))}
