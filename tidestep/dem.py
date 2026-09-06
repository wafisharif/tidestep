"""USGS 3DEP elevation for the study bbox (Stage 1).

Uses the 3DEP Elevation ImageServer ``exportImage`` endpoint, which returns
a bare-earth GeoTIFF clipped to a bbox, in metres above NAVD88. That is the
simplest way to get only the study area onto a laptop; the server caps a
single export at ~4000 px per side, so the bbox is tiled and merged.

Alternative (same data): stream the 1 m COG tiles from the USGS 3DEP AWS
Registry of Open Data bucket (``s3://prd-tnm/StagedProducts/Elevation/1m/``)
with rasterio's windowed reads. Swap ``fetch_dem`` for that if the
ImageServer is slow.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.merge import merge

from . import config

IMAGE_SERVER = ("https://elevation.nationalmap.gov/arcgis/rest/services/"
                "3DEPElevation/ImageServer/exportImage")
MAX_PX = 4000
DATA_DIR = Path(os.environ.get("TIDESTEP_DATA", "data"))


def _deg_per_m(lat: float) -> tuple[float, float]:
    """Approximate degrees of latitude / longitude per metre at ``lat``."""
    return 1 / 111_320, 1 / (111_320 * math.cos(math.radians(lat)))


def _export_tile(bbox, width, height, out: Path) -> Path:
    south, west, north, east = bbox
    params = {
        "bbox": f"{west},{south},{east},{north}",
        "bboxSR": 4326,
        "imageSR": 4326,
        "size": f"{width},{height}",
        "format": "tiff",
        "pixelType": "F32",
        "noData": -9999,
        "interpolation": "RSP_BilinearInterpolation",
        "f": "image",
    }
    r = requests.get(IMAGE_SERVER, params=params, timeout=300)
    r.raise_for_status()
    if not r.content.startswith((b"II*\x00", b"MM\x00*")):
        raise RuntimeError(f"3DEP did not return a TIFF: {r.text[:200]}")
    out.write_bytes(r.content)
    return out


def fetch_dem(bbox=config.BBOX, resolution_m: float = 1.0,
              out_path: Path | None = None) -> Path:
    """Download the DEM for ``bbox`` at ``resolution_m`` and save a GeoTIFF.

    Returns the path. Skips the download if the file already exists.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = out_path or DATA_DIR / f"dem_{int(resolution_m)}m.tif"
    if out_path.exists():
        return out_path

    south, west, north, east = bbox
    dlat, dlon = _deg_per_m((south + north) / 2)
    total_w = math.ceil((east - west) / (dlon * resolution_m))
    total_h = math.ceil((north - south) / (dlat * resolution_m))
    nx, ny = math.ceil(total_w / MAX_PX), math.ceil(total_h / MAX_PX)

    tiles = []
    for iy in range(ny):
        for ix in range(nx):
            t_west = west + (east - west) * ix / nx
            t_east = west + (east - west) * (ix + 1) / nx
            t_south = south + (north - south) * iy / ny
            t_north = south + (north - south) * (iy + 1) / ny
            w = math.ceil(total_w / nx)
            h = math.ceil(total_h / ny)
            tile = DATA_DIR / f"_tile_{iy}_{ix}.tif"
            tiles.append(_export_tile((t_south, t_west, t_north, t_east), w, h, tile))

    if len(tiles) == 1:
        tiles[0].rename(out_path)
        return out_path

    srcs = [rasterio.open(t) for t in tiles]
    mosaic, transform = merge(srcs, nodata=-9999)
    meta = srcs[0].meta.copy()
    meta.update(height=mosaic.shape[1], width=mosaic.shape[2],
                transform=transform, nodata=-9999, compress="deflate")
    for s in srcs:
        s.close()
    with rasterio.open(out_path, "w", **meta) as dst:
        dst.write(mosaic)
    for t in tiles:
        t.unlink()
    return out_path


def load_dem(path: Path) -> tuple[np.ndarray, rasterio.Affine, dict]:
    """Return (elevation array m NAVD88 with NaN nodata, affine, meta)."""
    with rasterio.open(path) as src:
        arr = src.read(1).astype("float32")
        nodata = src.nodata
        if nodata is not None:
            arr[arr == nodata] = np.nan
        return arr, src.transform, src.meta.copy()
