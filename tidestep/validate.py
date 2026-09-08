"""Stage 9: retroactive validation against real recorded water levels.

The model's actual claim is narrow and checkable: "when the water level at
Kings Point exceeds a given NWS flood stage, the connected-flood-fill model
predicts flooding on at least one street segment; when it stays well below
every stage, the model predicts none." This module checks exactly that
claim against NOAA's own historical record — no manual news-archive digging
required, because NOAA's gauge readings are themselves the ground truth for
"did the water reach flood stage," which is the thing our hazard
classification is built on top of.

This does NOT independently verify "were these the exact streets that
flooded" (that would need local news/DOT road-closure records for specific
storms, cited as future work in docs/LIMITATIONS.md) — it verifies that the
DEM + connectivity + threshold pipeline responds correctly to real,
recorded high-water events at the reference gauge, which is the part of
the model most likely to have a silent unit or datum bug.

Usage (needs data/segments.gpkg, data/dem_1m.tif, data/water.gpkg from a
prior `scripts/fetch_all.py` + `scripts/build_hazard.py` run, and network
access to NOAA — run from a normal terminal, not a sandbox without NOAA
egress):

    python scripts/validate_stage9.py --days 30
    python scripts/validate_stage9.py --dates 2025-01-10,2025-12-25
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import config, coops, floodfill, hazard


@dataclass
class DayResult:
    date: str
    peak_wl_m: float
    exceeds_minor: bool
    exceeds_moderate: bool
    exceeds_major: bool
    flooded_segments_at_peak: int
    max_depth_cm_at_peak: int
    correct: bool   # flooded_segments > 0 iff exceeds_minor (the core check)


def pick_sample_dates(n: int, start: datetime | None = None,
                      end: datetime | None = None, seed: int = 0) -> list[datetime]:
    """n dates spread across [start, end) (default: the last 15 months up to
    yesterday, so verified hourly_height data is available for all of it)."""
    end = end or (datetime.now(timezone.utc) - timedelta(days=1))
    start = start or (end - timedelta(days=455))
    span_days = (end - start).days
    rng = np.random.default_rng(seed)
    offsets = sorted(rng.choice(span_days, size=min(n, span_days), replace=False))
    return [start + timedelta(days=int(o)) for o in offsets]


def validate_day(day: datetime, dem: np.ndarray, seeds: np.ndarray,
                 segments) -> DayResult:
    """Pull that day's observed water level, run it through the real hazard
    pipeline (same segments.py/floodfill.py/hazard.py the live app uses),
    and classify the result against NWS flood stage."""
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    obs = coops.fetch_observed(start, 24)
    if obs.empty:
        raise ValueError(f"no observed data for {day.date()}")
    peak = float(obs.max())
    peak_time = obs.idxmax()

    # score every hour, keep the peak — cheaper than a single-timestep call
    # and it also sanity-checks the whole day's shape, not just the max
    table = hazard.hazard_table(segments, dem, seeds, obs)
    at_peak = table[table.valid_time == peak_time]
    flooded_n = int(at_peak.flooded.sum())
    max_depth = int(at_peak.depth_cm.max()) if len(at_peak) else 0

    thr = config.FLOOD_THRESHOLDS_M_NAVD88
    exceeds_minor = peak >= thr["nws_minor"]
    exceeds_moderate = peak >= thr["nws_moderate"]
    exceeds_major = peak >= thr["nws_major"]
    correct = (flooded_n > 0) == exceeds_minor

    return DayResult(date=str(day.date()), peak_wl_m=round(peak, 3),
                     exceeds_minor=exceeds_minor, exceeds_moderate=exceeds_moderate,
                     exceeds_major=exceeds_major, flooded_segments_at_peak=flooded_n,
                     max_depth_cm_at_peak=max_depth, correct=correct)


def summarize(results: list[DayResult]) -> dict:
    df = pd.DataFrame([r.__dict__ for r in results])
    minor_days = df[df.exceeds_minor]
    calm_days = df[~df.exceeds_minor]
    sensitivity = (minor_days.flooded_segments_at_peak > 0).mean() if len(minor_days) else None
    specificity = (calm_days.flooded_segments_at_peak == 0).mean() if len(calm_days) else None
    return {
        "n_days": len(df),
        "n_exceeded_minor": int(df.exceeds_minor.sum()),
        "n_exceeded_moderate": int(df.exceeds_moderate.sum()),
        "n_exceeded_major": int(df.exceeds_major.sum()),
        "sensitivity": sensitivity,   # of days that reached minor stage, fraction the model also flooded
        "specificity": specificity,   # of calm days, fraction the model correctly left dry
        "overall_accuracy": df.correct.mean(),
        "table": df,
    }
