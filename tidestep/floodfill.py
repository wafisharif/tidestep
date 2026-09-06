"""Stage 2b: connected flood-fill.

A DEM cell is flooded at water level W only if (a) its elevation is below
W and (b) it is reachable from open water through a continuous path of
cells that are also below W. This is the NOAA Sea Level Rise Viewer
"hydrologically connected" rule; it stops inland low spots (that the tide
cannot physically reach) from being flagged.

Implementation: ``scipy.ndimage.label`` on the boolean array ``dem < W``
(4-connectivity), then keep only the components that overlap the seed
mask. That is a BFS over every component at once and runs in well under a
second for a 4400 x 4200 grid.

Seeds (open water) are the union of:
* DEM cells that are nodata (LiDAR returns nothing over water), and
* DEM cells at or below MLLW (m NAVD88), and
* pixels inside OSM water polygons (natural=water, bay, coastline areas).
Only seed components that touch the tidal water body are relevant; inland
ponds would also be seeds, so the OSM layer is filtered to tidal features
(``tidal`` tag or touching the bbox edge) in ``build_seed_mask``.
"""
from __future__ import annotations

import numpy as np
from rasterio import features
from scipy import ndimage

from . import config

MLLW_NAVD88_M = -config.MLLW_TO_NAVD88_M  # MLLW expressed in m NAVD88 (-1.283)

STRUCTURE_4 = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)


def build_seed_mask(dem: np.ndarray, transform, water_gdf=None,
                    dem_crs=None) -> np.ndarray:
    """Boolean mask of open-water pixels."""
    seeds = np.isnan(dem) | (dem <= MLLW_NAVD88_M)
    if water_gdf is not None and len(water_gdf):
        w = water_gdf
        if dem_crs is not None:
            w = w.to_crs(dem_crs)
        polys = w[w.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        if len(polys):
            burned = features.rasterize(
                ((g, 1) for g in polys.geometry), out_shape=dem.shape,
                transform=transform, fill=0, dtype="uint8")
            seeds |= burned.astype(bool)
    # keep only seed components that reach the raster edge (the bay opens to
    # the Sound at the bbox boundary) — drops isolated inland ponds
    lab, n = ndimage.label(seeds, structure=STRUCTURE_4)
    if n == 0:
        return seeds
    edge_labels = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    edge_labels = edge_labels[edge_labels > 0]
    return np.isin(lab, edge_labels)


def connected_flood_mask(dem: np.ndarray, seeds: np.ndarray,
                         water_level_m: float) -> np.ndarray:
    """Cells below ``water_level_m`` that are connected to ``seeds``."""
    below = (dem < water_level_m) | np.isnan(dem)
    lab, n = ndimage.label(below, structure=STRUCTURE_4)
    if n == 0:
        return np.zeros_like(below)
    hit = np.unique(lab[seeds & below])
    hit = hit[hit > 0]
    return np.isin(lab, hit)


def bathtub_mask(dem: np.ndarray, water_level_m: float) -> np.ndarray:
    """Naive comparison, kept only so the write-up can show the difference."""
    return dem < water_level_m


def segment_flooded(min_row, min_col, mask: np.ndarray) -> np.ndarray:
    """Look each segment's low-point pixel up in a flood mask."""
    r = np.asarray(min_row, dtype="float64")
    c = np.asarray(min_col, dtype="float64")
    ok = np.isfinite(r) & np.isfinite(c)
    out = np.zeros(len(r), dtype=bool)
    out[ok] = mask[r[ok].astype(int), c[ok].astype(int)]
    return out
