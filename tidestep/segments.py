"""Stage 2a: break OSM edges into short segments and sample ground elevation.

Each graph edge (u, v, key) is split into pieces of about SEGMENT_LENGTH_M.
For every piece we sample the DEM at ~1 m spacing along the line and keep
the MINIMUM — water reaches the low point of a segment first, so the low
point decides when it floods.
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import rowcol
from shapely.geometry import LineString
from shapely.ops import substring

from . import config

# Metric CRS for Long Island: NAD83 / New York Long Island (US ft is 2263;
# 32118 is the metre version).
METRIC_CRS = 32118


def split_line(line: LineString, seg_len: float) -> list[LineString]:
    """Split a metric-CRS LineString into pieces of ~seg_len metres."""
    n = max(1, int(round(line.length / seg_len)))
    step = line.length / n
    return [substring(line, i * step, (i + 1) * step) for i in range(n)]


def segment_edges(edges: gpd.GeoDataFrame,
                  seg_len: float = config.SEGMENT_LENGTH_M) -> gpd.GeoDataFrame:
    """Return one row per segment with edge keys, index within edge, geometry
    (WGS84) and length_m. Input must be the edges_gdf from streets.py."""
    metric = edges.to_crs(METRIC_CRS)
    rows = []
    for _, e in metric.iterrows():
        for i, piece in enumerate(split_line(e.geometry, seg_len)):
            rows.append({"u": e.u, "v": e.v, "key": e.key, "seg_idx": i,
                         "highway": str(e.get("highway", "")),
                         "name": str(e.get("name", "")),
                         "length_m": piece.length, "geometry": piece})
    gdf = gpd.GeoDataFrame(rows, crs=METRIC_CRS)
    gdf.insert(0, "segment_id", np.arange(len(gdf)))
    return gdf.to_crs(4326)


def _sample_points(line: LineString, spacing: float = 1.0) -> np.ndarray:
    n = max(2, int(line.length / spacing) + 1)
    d = np.linspace(0, line.length, n)
    return np.array([line.interpolate(x).coords[0] for x in d])


def sample_min_elevation(segments: gpd.GeoDataFrame, dem_path) -> pd.DataFrame:
    """Minimum DEM value (m NAVD88) along each segment and the DEM pixel
    (row, col) where it occurs. ground_m is NaN if the segment is off-DEM.

    The pixel is kept so the flood-fill stage can ask "is this segment's low
    point inside the connected flooded region" with a single array lookup.
    """
    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype("float32")
        if src.nodata is not None:
            dem[dem == src.nodata] = np.nan
        transform, crs = src.transform, src.crs
        H, W = dem.shape
        metric = segments.to_crs(METRIC_CRS)
        in_dem_crs = segments.to_crs(crs)
        out = np.full((len(segments), 3), np.nan, dtype="float64")
        for i, (geom_m, geom_d) in enumerate(zip(metric.geometry, in_dem_crs.geometry)):
            # sample at 1 m spacing in metric space, then map those fractions
            # onto the DEM-CRS geometry to get pixel coordinates
            n = max(2, int(geom_m.length) + 1)
            fr = np.linspace(0, 1, n)
            pts = np.array([geom_d.interpolate(f, normalized=True).coords[0] for f in fr])
            r, c = rowcol(transform, pts[:, 0], pts[:, 1])
            r, c = np.asarray(r), np.asarray(c)
            ok = (r >= 0) & (r < H) & (c >= 0) & (c < W)
            if not ok.any():
                continue
            r, c = r[ok], c[ok]
            vals = dem[r, c]
            if np.isfinite(vals).any():
                j = int(np.nanargmin(vals))
                out[i] = (vals[j], r[j], c[j])
    df = pd.DataFrame(out, index=segments.index,
                      columns=["ground_m", "min_row", "min_col"])
    df["min_row"] = df["min_row"].astype("Int64")
    df["min_col"] = df["min_col"].astype("Int64")
    return df


def build_segments(edges: gpd.GeoDataFrame, dem_path) -> gpd.GeoDataFrame:
    segs = segment_edges(edges)
    samp = sample_min_elevation(segs, dem_path)
    for col in samp.columns:
        segs[col] = samp[col].values
    return segs
