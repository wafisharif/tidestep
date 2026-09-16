"""Stage 3: depth and safety flags per (segment, forecast hour).

depth = water surface (m NAVD88) - segment ground (m NAVD88), only for
segments inside the connected flood region; 0 otherwise.

Safety uses still-water depth limits (config.DEPTH_LIMIT_M). Segments
flagged ``near_inlet`` get their limits multiplied by INLET_SAFETY_FACTOR
because local flow can be faster there; we do not compute velocity.

The wheelchair profile additionally fails any segment steeper than
config.WHEELCHAIR_MAX_GRADE_PCT (ADA 1:12), using the static ``grade_pct``
column from segments.py, so a too-steep ramp is unsafe at every hour.

Sea-level-rise scenarios: ``hazard_table`` can be asked for several
``scenarios_cm``; each adds a constant offset to every water level before
the flood-fill and is tagged in the ``scenario_cm`` column. Scenario 0 is
the plain forecast and is what every default query uses.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config, floodfill

PROFILES = ("child", "adult", "wheelchair", "vehicle_small", "vehicle_large", "vehicle_4wd")


def flag_near_inlet(segments, water_gdf, buffer_m: float = 30.0) -> np.ndarray:
    """True for segments within ``buffer_m`` of an OSM waterway line
    (culvert/stream/channel) or a bridge/culvert-tagged way."""
    from .segments import METRIC_CRS
    near = np.zeros(len(segments), dtype=bool)
    if water_gdf is None or not len(water_gdf):
        return near
    lines = water_gdf[water_gdf.geometry.geom_type.isin(["LineString", "MultiLineString"])]
    if not len(lines):
        return near
    segs_m = segments.to_crs(METRIC_CRS)
    lines_m = lines.to_crs(METRIC_CRS)
    hits = segs_m.sjoin_nearest(lines_m[["geometry"]], max_distance=buffer_m, how="inner")
    near[np.unique(hits.index)] = True
    return near


def classify(depth_m: np.ndarray, near_inlet: np.ndarray,
             grade_pct: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Boolean safe flags per profile for an array of depths.

    ``grade_pct`` (static per segment, NaN when unknown) only affects the
    wheelchair profile: a segment steeper than the ADA limit is unsafe
    regardless of water. Unknown grade is treated as passable so a DEM gap
    never blocks a route on its own.
    """
    factor = np.where(near_inlet, config.INLET_SAFETY_FACTOR, 1.0)
    flags = {p: depth_m <= config.DEPTH_LIMIT_M[p] * factor for p in PROFILES}
    if grade_pct is not None:
        g = np.asarray(grade_pct, dtype="float64")
        too_steep = np.isfinite(g) & (g > config.WHEELCHAIR_MAX_GRADE_PCT)
        flags["wheelchair"] = flags["wheelchair"] & ~too_steep
    return flags


def hazard_table(segments, dem: np.ndarray, seeds: np.ndarray,
                 water_levels: pd.Series,
                 scenarios_cm: tuple[int, ...] = (0,)) -> pd.DataFrame:
    """Long table: one row per (scenario_cm, segment_id, forecast_hour).

    Columns: scenario_cm, segment_id, forecast_hour, valid_time,
    water_level_m (offset already applied), depth_cm, flooded,
    safe_child, safe_adult, safe_wheelchair, safe_vehicle_small/large/4wd.
    """
    ground = segments["ground_m"].to_numpy(dtype="float64")
    near = segments["near_inlet"].to_numpy(dtype=bool) if "near_inlet" in segments else \
        np.zeros(len(segments), dtype=bool)
    grade = segments["grade_pct"].to_numpy(dtype="float64") if "grade_pct" in segments else None
    frames = []
    for scenario in scenarios_cm:
        offset = scenario / 100.0
        for hour_idx, (t, wl0) in enumerate(water_levels.items()):
            wl = float(wl0) + offset
            mask = floodfill.connected_flood_mask(dem, seeds, wl)
            flooded = floodfill.segment_flooded(segments["min_row"], segments["min_col"], mask)
            depth = np.where(flooded, np.maximum(wl - ground, 0.0), 0.0)
            depth = np.nan_to_num(depth, nan=0.0)
            flags = classify(depth, near, grade)
            frames.append(pd.DataFrame({
                "scenario_cm": int(scenario),
                "segment_id": segments["segment_id"].to_numpy(),
                "forecast_hour": hour_idx,
                "valid_time": t,
                "water_level_m": wl,
                "depth_cm": np.round(depth * 100).astype(int),
                "flooded": flooded,
                **{f"safe_{p}": v for p, v in flags.items()},
            }))
    return pd.concat(frames, ignore_index=True)
