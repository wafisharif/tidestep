"""Regression test for a real bug found by auditing scripts/hourly_update.py
against docs/PIPELINE.md's Stage 8 (the hourly operational loop) after the
scripts/build_hazard.py near_inlet caching bug was fixed there.

The bug: ``main()`` only called ``db.load_segments`` -- the only thing that
pushes segments.gpkg's actual column values (near_inlet, ground_m, tags,
geometry) into PostGIS -- when the row *count* in the segments table
differed from ``len(segs)``. Fixing a segment attribute on disk (exactly
what the near_inlet caching fix does) doesn't change how many segments
there are, so once the count matched, the hourly cron loop would silently
keep serving whatever was already in the database forever, even though
segments.gpkg on disk had the correction. This is the same failure class
as the build_hazard.py bug, one layer further downstream, and arguably
worse: that one was caught by reading the code, but this one would only
show up as "the fix didn't actually reach the live map/API," a lot further
from its cause.

Needs a real Postgres+PostGIS reachable at DATABASE_URL -- same
skip-if-unavailable pattern as tests/test_db.py.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hourly_update  # noqa: E402

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://tidestep:tidestep@localhost:5432/tidestep")


def _db_available() -> bool:
    try:
        eng = create_engine(DATABASE_URL, future=True)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason=f"no Postgres reachable at {DATABASE_URL} (start it with `docker compose up -d`)")


@pytest.fixture()
def engine():
    from tidestep import db
    eng = create_engine(DATABASE_URL, future=True)
    db.init_schema(eng)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM hazard"))
        conn.execute(text("DELETE FROM segments"))
    yield eng


def _one_segment(near_inlet: bool) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({
        "segment_id": [0], "u": [1], "v": [2], "key": [0], "seg_idx": [0],
        "name": ["Shore Rd"], "highway": ["residential"],
        "length_m": [15.0], "ground_m": [0.2], "near_inlet": [near_inlet],
        "geometry": [LineString([(-73.71, 40.80), (-73.7099, 40.80)])],
    }, crs=4326)


def _hazard_for(segs: gpd.GeoDataFrame, hour: int = 0) -> pd.DataFrame:
    t = pd.Timestamp("2026-09-07", tz="UTC") + pd.Timedelta(hours=hour)
    return pd.DataFrame([{
        "segment_id": int(sid), "forecast_hour": hour, "valid_time": t,
        "water_level_m": 1.0, "depth_cm": 0, "flooded": False,
        "safe_child": True, "safe_adult": True, "safe_vehicle_small": True,
        "safe_vehicle_large": True, "safe_vehicle_4wd": True,
    } for sid in segs["segment_id"]])


def test_sync_db_reloads_segments_even_when_row_count_is_unchanged(engine):
    """The exact scenario the old count-based skip got wrong: same number
    of segments before and after, but a column value changed on disk."""
    stale = _one_segment(near_inlet=False)
    hourly_update.sync_db(engine, stale, _hazard_for(stale), bias=0.1)
    with engine.connect() as conn:
        before = conn.execute(text("SELECT near_inlet FROM segments WHERE segment_id=0")).scalar_one()
    assert before is False

    # same row count (1), corrected near_inlet -- this is what a re-run of
    # the now-fixed build_hazard.py against a segments.gpkg that used to be
    # cached stale would look like.
    fixed = _one_segment(near_inlet=True)
    hourly_update.sync_db(engine, fixed, _hazard_for(fixed), bias=0.1)
    with engine.connect() as conn:
        after = conn.execute(text("SELECT near_inlet FROM segments WHERE segment_id=0")).scalar_one()
        count = conn.execute(text("SELECT count(*) FROM segments")).scalar_one()
    assert after is True     # not stuck at the old value
    assert count == 1        # and not duplicated by the reload
