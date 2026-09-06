"""NOAA CO-OPS water level client (Stage 1).

Three products from https://api.tidesandcurrents.noaa.gov (free, no key):

* ``ofs_water_level``  – NYOFS model guidance. Includes wind setup and
  short-term surge. This is the water surface the flood model uses.
* ``predictions``      – astronomical tide only. Clear-weather baseline.
* ``datums``           – station datum sheet, used to sanity-check the
  hardcoded MLLW→NAVD88 offset in config.

All levels are requested relative to MLLW in metres and converted to metres
above NAVD88 before they leave this module, so downstream code only ever
sees NAVD88.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from . import config

API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
MDAPI = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations"
TIMEOUT = 30


def _get(params: dict) -> dict:
    base = {
        "station": config.STATION_ID,
        "datum": "MLLW",
        "units": "metric",
        "time_zone": "gmt",
        "format": "json",
        "application": "tidestep",
    }
    r = requests.get(API, params={**base, **params}, timeout=TIMEOUT)
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(f"CO-OPS error: {body['error'].get('message')}")
    return body


def _to_series(body: dict, key: str) -> pd.Series:
    """Turn a CO-OPS JSON payload into a UTC-indexed float Series (m MLLW)."""
    rows = body.get(key) or body.get("data") or []
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows)
    df["t"] = pd.to_datetime(df["t"], utc=True)
    df["v"] = pd.to_numeric(df["v"], errors="coerce")
    return df.set_index("t")["v"].dropna()


def to_hourly_navd88(series_mllw_m: pd.Series) -> pd.Series:
    """Resample to hourly (max within each hour) and convert to m NAVD88.

    Max, not mean: the flood model cares about the peak water surface within
    the hour, and NYOFS output is 6-minute so an hourly mean would shave the
    top off every high tide.
    """
    if series_mllw_m.empty:
        return series_mllw_m
    hourly = series_mllw_m.resample("1h").max().dropna()
    return hourly.map(config.mllw_to_navd88_m).rename("wl_navd88_m")


def _window(start: datetime | None, hours: int) -> dict:
    start = start or datetime.now(timezone.utc)
    start = start.replace(minute=0, second=0, microsecond=0)
    return {
        "begin_date": start.strftime("%Y%m%d %H:%M"),
        "range": hours,
    }


def fetch_ofs_forecast(start: datetime | None = None,
                       hours: int = config.FORECAST_HOURS) -> pd.Series:
    """NYOFS water level guidance, hourly, m NAVD88. ``start`` is UTC."""
    body = _get({"product": "ofs_water_level", **_window(start, hours)})
    return to_hourly_navd88(_to_series(body, "data"))


def fetch_predictions(start: datetime | None = None,
                      hours: int = config.FORECAST_HOURS) -> pd.Series:
    """Astronomical tide predictions, hourly, m NAVD88. ``start`` is UTC."""
    body = _get({"product": "predictions", "interval": "h",
                 **_window(start, hours)})
    return to_hourly_navd88(_to_series(body, "predictions"))


def fetch_observed(start: datetime, hours: int) -> pd.Series:
    """Observed water level (6-minute preliminary, hourly max), m NAVD88.

    ``water_level`` covers the last ~30 days including today; the verified
    ``hourly_height`` product lags by weeks, so for historical Stage 9 runs
    older than a month switch to ``product="hourly_height"``.
    """
    product = "water_level" if start > datetime.now(timezone.utc) - timedelta(days=28) \
        else "hourly_height"
    body = _get({"product": product, **_window(start, hours)})
    return to_hourly_navd88(_to_series(body, "data"))


def recent_ofs_bias(hours: int = 48) -> float:
    """Mean (OFS - observed) over the last ``hours``, in metres.

    On 2026-09-06 this was +0.26 m at Kings Point: NYOFS ran high against
    the gauge while observations were 0.38 m above astronomical
    predictions. A constant offset that size is a third of the gap between
    MHHW and minor flood stage, so we remove it. Simple mean-bias removal
    is the standard first correction for OFS guidance; it does not fix
    timing errors (see docs/PIPELINE.md Stage 10).
    """
    start = datetime.now(timezone.utc) - timedelta(hours=hours)
    obs = fetch_observed(start, hours)
    ofs = fetch_ofs_forecast(start, hours)
    d = (ofs - obs).dropna()
    return float(d.mean()) if len(d) else 0.0


def fetch_forecast_frame(start: datetime | None = None,
                         hours: int = config.FORECAST_HOURS,
                         bias_correct: bool = True) -> pd.DataFrame:
    """Both curves side by side plus the surge/wind component.

    Columns: ofs_raw_m, ofs_bias_m, ofs_navd88_m (bias-corrected, the
    value the flood model uses), pred_navd88_m, nontidal_m (ofs - pred).
    """
    ofs = fetch_ofs_forecast(start, hours)
    pred = fetch_predictions(start, hours)
    bias = recent_ofs_bias() if bias_correct else 0.0
    df = pd.concat({"ofs_raw_m": ofs, "pred_navd88_m": pred}, axis=1)
    df["ofs_bias_m"] = bias
    df["ofs_navd88_m"] = df["ofs_raw_m"] - bias
    df["nontidal_m"] = df["ofs_navd88_m"] - df["pred_navd88_m"]
    return df[["ofs_raw_m", "ofs_bias_m", "ofs_navd88_m", "pred_navd88_m", "nontidal_m"]]


def fetch_datums() -> dict[str, float]:
    """Station datums in feet above STND, from the metadata API."""
    r = requests.get(f"{MDAPI}/{config.STATION_ID}/datums.json", timeout=TIMEOUT)
    r.raise_for_status()
    return {d["name"]: d["value"] for d in r.json()["datums"]}


def check_datums(tolerance_ft: float = 0.01) -> bool:
    """Confirm the live datum sheet matches config. Run once per session."""
    live = fetch_datums()
    for name in ("MLLW", "NAVD88", "MHHW"):
        if abs(live[name] - config.DATUMS_FT_STND[name]) > tolerance_ft:
            raise RuntimeError(
                f"Datum {name} changed: live={live[name]} "
                f"config={config.DATUMS_FT_STND[name]}. Update config.py.")
    return True
