"""Historical replay: run the flood model on a past day's OBSERVED water
levels instead of the forecast.

Two uses:
* the "Replay a past day" control in the web app (``/api/replay/...``),
  which is the demo-video fallback when no king tide is due — pick
  2022-12-23 and watch Shore Road go under;
* Stage 9b street-level validation (``scripts/validate_streets.py``).

The heavy inputs (DEM, seed mask, segments) are loaded once per process
and reused. A finished replay is cached as ``data/replay/<date>.csv`` so
the second request for the same day is instant. The first request for a
new day takes about as long as ``build_hazard.py`` for one scenario
(roughly 30-60 s on a laptop for the full study area).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import geopandas as gpd
import numpy as np
import pandas as pd

from . import config, coops, floodfill, hazard, streets
from . import dem as demmod

DATA_DIR = Path(os.environ.get("TIDESTEP_DATA", "data"))
REPLAY_DIR = DATA_DIR / "replay"
LOCAL_TZ = ZoneInfo("America/New_York")

_inputs: dict = {}
_tables: dict[str, pd.DataFrame] = {}     # date -> hazard table, once loaded


def load_inputs(force: bool = False) -> dict:
    """DEM array, seed mask and segments, loaded once."""
    if _inputs and not force:
        return _inputs
    dem, transform, meta = demmod.load_dem(DATA_DIR / "dem_1m.tif")
    water = gpd.read_file(streets.WATER_PATH) if streets.WATER_PATH.exists() else None
    seeds = floodfill.build_seed_mask(dem, transform, water, meta["crs"])
    segs = gpd.read_file(DATA_DIR / "segments.gpkg")
    _inputs.update(dem=dem, seeds=seeds, segments=segs, crs=meta["crs"])
    return _inputs


def observed_day(date: datetime) -> pd.Series:
    """Hourly observed water level (m NAVD88) for the local calendar day
    ``date`` (America/New_York midnight to midnight)."""
    local_midnight = datetime(date.year, date.month, date.day, tzinfo=LOCAL_TZ)
    start_utc = local_midnight.astimezone(ZoneInfo("UTC"))
    return coops.fetch_observed(start_utc, 24)


def water_levels_from_peak(date: datetime, peak_m_navd88: float) -> pd.Series:
    """A single-hour series at a known peak, for validation runs that use a
    documented peak instead of fetching the gauge record."""
    t = datetime(date.year, date.month, date.day, 12, tzinfo=LOCAL_TZ)
    return pd.Series([peak_m_navd88], index=pd.DatetimeIndex([t]), name="wl_navd88_m")


def replay_day(date: datetime, water_levels: pd.Series | None = None,
               use_cache: bool = True) -> pd.DataFrame:
    """Hazard table (scenario 0) for ``date`` from observed levels."""
    key = date.strftime("%Y-%m-%d")
    cache = REPLAY_DIR / f"{key}.csv"
    if use_cache and water_levels is None:
        if key in _tables:
            return _tables[key]
        if cache.exists():
            _tables[key] = pd.read_csv(cache, parse_dates=["valid_time"])
            return _tables[key]
    inp = load_inputs()
    wl = water_levels if water_levels is not None else observed_day(date)
    if wl.empty:
        raise ValueError(f"no observed water levels for {key}")
    table = hazard.hazard_table(inp["segments"], inp["dem"], inp["seeds"], wl)
    if use_cache and water_levels is None:
        REPLAY_DIR.mkdir(parents=True, exist_ok=True)
        table.to_csv(cache, index=False)
        _tables[key] = table
    return table


def hours_summary(table: pd.DataFrame) -> list[dict]:
    g = table.groupby("forecast_hour").agg(
        valid_time=("valid_time", "first"), water_level_m=("water_level_m", "first"),
        flooded_segments=("flooded", "sum"))
    return [{"hour": int(h), "valid_time": pd.Timestamp(r.valid_time).isoformat(),
             "water_level_m": float(r.water_level_m),
             "flooded_segments": int(r.flooded_segments)} for h, r in g.iterrows()]


def _in_bbox(geom: dict, bbox) -> bool:
    s, w, n, e = bbox
    return any(w <= x <= e and s <= y <= n for x, y in geom["coordinates"])


def risk_features(table: pd.DataFrame, hour: int, segments_fc: dict,
                  bbox=None, flooded_only: bool = False) -> dict:
    """Merge one hour of a replay table onto static segment GeoJSON.
    ``bbox`` = (south, west, north, east) keeps only segments with a vertex
    inside; ``flooded_only`` drops dry segments."""
    rows = table[table.forecast_hour == hour]
    if flooded_only:
        rows = rows[rows.flooded]
    cols = ["depth_cm", "flooded"] + [f"safe_{p}" for p in hazard.PROFILES]
    by_id = dict(zip(rows["segment_id"].to_numpy(),
                     rows[cols].astype(object).itertuples(index=False, name=None)))
    valid_time = pd.Timestamp(rows["valid_time"].iloc[0]).isoformat() if len(rows) else None
    wl = float(rows["water_level_m"].iloc[0]) if len(rows) else None
    feats = []
    for f in segments_fc["features"]:
        vals = by_id.get(f["properties"]["segment_id"])
        if vals is None:
            continue
        if bbox is not None and not _in_bbox(f["geometry"], bbox):
            continue
        props = dict(f["properties"])
        props["valid_time"] = valid_time
        props["water_level_m"] = wl
        props["depth_cm"] = int(vals[0])
        props["flooded"] = bool(vals[1])
        for p, v in zip(hazard.PROFILES, vals[2:]):
            props[f"safe_{p}"] = bool(v)
        props["replay"] = True
        feats.append({"type": "Feature", "geometry": f["geometry"], "properties": props})
    return {"type": "FeatureCollection", "features": feats}


def cached_days() -> list[str]:
    if not REPLAY_DIR.exists():
        return []
    return sorted(p.stem for p in REPLAY_DIR.glob("*.csv"))


def parse_date(s: str) -> datetime:
    d = datetime.strptime(s, "%Y-%m-%d")
    if d.date() >= datetime.now(LOCAL_TZ).date():
        raise ValueError("replay dates must be in the past")
    return d
