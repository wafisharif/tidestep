"""Stage 9 validation logic, tested against synthetic historical data (no
network) — proves validate_day/summarize classify correctly before it is
ever pointed at a real NOAA account, which needs network this sandbox
doesn't have. See tidestep/validate.py for what "correct" means here."""
from datetime import datetime, timedelta, timezone

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString

from tidestep import config, floodfill, segments, validate


@pytest.fixture
def synthetic_shore(tmp_path):
    """One shore road: ground rises from 1.4 m (just below NWS minor,
    1.768 m) to 2.0 m along its length. Water on the west edge."""
    dem = np.zeros((60, 60), dtype="float32")
    x = np.arange(60)
    dem[:] = 1.4 + (x - 5) * 0.02
    dem[:, :5] = np.nan
    path = tmp_path / "dem.tif"
    tr = from_origin(-73.75, 40.84, 1.2e-5, 9e-6)
    with rasterio.open(path, "w", driver="GTiff", height=60, width=60, count=1,
                       dtype="float32", crs="EPSG:4326", transform=tr, nodata=np.nan) as dst:
        dst.write(dem, 1)
    dem_f = dem.copy()

    def ll(col, row):
        return (tr.c + (col + 0.5) * tr.a, tr.f + (row + 0.5) * tr.e)
    road = LineString([ll(6, 30), ll(55, 30)])
    edges = gpd.GeoDataFrame({"u": [1], "v": [2], "key": [0], "osmid": [1],
                              "highway": ["residential"], "name": ["Shore Rd"],
                              "length": [83.0], "geometry": [road]}, crs=4326)
    segs = segments.build_segments(edges, path)
    segs["near_inlet"] = False
    seeds = floodfill.build_seed_mask(dem_f, tr)
    return dem_f, seeds, segs


def _fake_observed(peak_m: float):
    """A one-day series peaking at ``peak_m`` at hour 6."""
    def fetch_observed(start, hours):
        t = np.arange(hours)
        wl = peak_m * np.exp(-((t - 6) ** 2) / 4) + 0.1
        idx = pd.date_range(start, periods=hours, freq="h", tz="UTC")
        return pd.Series(wl, index=idx)
    return fetch_observed


def test_validate_day_correctly_flags_a_flood_day(monkeypatch, synthetic_shore):
    dem, seeds, segs = synthetic_shore
    minor = config.FLOOD_THRESHOLDS_M_NAVD88["nws_minor"]
    monkeypatch.setattr(validate.coops, "fetch_observed", _fake_observed(minor + 0.3))
    day = datetime(2025, 1, 10, tzinfo=timezone.utc)
    r = validate.validate_day(day, dem, seeds, segs)
    assert r.exceeds_minor
    assert r.flooded_segments_at_peak > 0
    assert r.correct


def test_validate_day_correctly_flags_a_calm_day(monkeypatch, synthetic_shore):
    dem, seeds, segs = synthetic_shore
    minor = config.FLOOD_THRESHOLDS_M_NAVD88["nws_minor"]
    monkeypatch.setattr(validate.coops, "fetch_observed", _fake_observed(minor - 0.6))
    day = datetime(2025, 1, 11, tzinfo=timezone.utc)
    r = validate.validate_day(day, dem, seeds, segs)
    assert not r.exceeds_minor
    assert r.flooded_segments_at_peak == 0
    assert r.correct


def test_validate_day_raises_on_no_data(monkeypatch, synthetic_shore):
    dem, seeds, segs = synthetic_shore
    monkeypatch.setattr(validate.coops, "fetch_observed",
                        lambda start, hours: pd.Series(dtype=float))
    with pytest.raises(ValueError):
        validate.validate_day(datetime(2025, 1, 1, tzinfo=timezone.utc), dem, seeds, segs)


def test_summarize_sensitivity_and_specificity():
    rows = [
        validate.DayResult("2025-01-01", 2.0, True, False, False, 5, 60, True),
        validate.DayResult("2025-01-02", 1.9, True, False, False, 0, 0, False),  # a miss
        validate.DayResult("2025-01-03", 0.5, False, False, False, 0, 0, True),
        validate.DayResult("2025-01-04", 0.4, False, False, False, 0, 0, True),
    ]
    s = validate.summarize(rows)
    assert s["n_days"] == 4
    assert s["n_exceeded_minor"] == 2
    assert s["sensitivity"] == pytest.approx(0.5)   # 1 of 2 minor-stage days caught
    assert s["specificity"] == pytest.approx(1.0)    # both calm days correctly dry
    assert s["overall_accuracy"] == pytest.approx(0.75)


def test_pick_sample_dates_deterministic_and_in_range():
    end = datetime(2026, 1, 1, tzinfo=timezone.utc)
    start = end - timedelta(days=100)
    d1 = validate.pick_sample_dates(10, start=start, end=end, seed=42)
    d2 = validate.pick_sample_dates(10, start=start, end=end, seed=42)
    assert d1 == d2   # same seed -> same sample, so a report is reproducible
    assert all(start <= d < end for d in d1)
    assert len(set(d1)) == 10
