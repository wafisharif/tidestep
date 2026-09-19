"""Sea-level-rise scenarios, wheelchair profile, historical replay, NWS
alerts, and the street-level validation helpers. No network, no DB."""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest
import geopandas as gpd
from shapely.geometry import LineString

from tidestep import config, hazard, nws, replay, routing, streets
from tests.test_floodmodel import make_dem   # synthetic beach / ridge / basin DEM


# --- scenarios -----------------------------------------------------------------

def _segments_on_beach(tmp_path):
    from tidestep import segments
    path, dem, tr = make_dem(tmp_path)
    def lonlat(col, row):
        return (tr.c + (col + 0.5) * tr.a, tr.f + (row + 0.5) * tr.e)
    road = LineString([lonlat(12, 50), lonlat(95, 50)])
    edges = gpd.GeoDataFrame({"u": [1], "v": [2], "key": [0], "osmid": [1],
                              "highway": ["residential"], "name": ["Shore Rd"],
                              "length": [83.0], "geometry": [road]}, crs=4326)
    segs = segments.build_segments(edges, path)
    segs["near_inlet"] = False
    return segs, dem, tr


def test_hazard_table_scenarios_add_offset_and_flood_more(tmp_path):
    from tidestep import floodfill
    segs, dem, tr = _segments_on_beach(tmp_path)
    seeds = floodfill.build_seed_mask(dem, tr)
    wl = pd.Series([0.2, 0.4], index=pd.date_range("2026-09-06", periods=2, freq="h", tz="UTC"))
    table = hazard.hazard_table(segs, dem, seeds, wl, scenarios_cm=(0, 50))
    assert set(table.scenario_cm.unique()) == {0, 50}
    assert len(table) == 2 * 2 * len(segs)
    base = table[table.scenario_cm == 0]
    up = table[table.scenario_cm == 50]
    # the offset is applied to the stored water level and floods at least as much
    assert up.water_level_m.iloc[0] == pytest.approx(base.water_level_m.iloc[0] + 0.5)
    assert up.flooded.sum() >= base.flooded.sum()
    assert up.flooded.sum() > 0
    assert "safe_wheelchair" in table.columns


def test_default_scenario_is_zero_only(tmp_path):
    from tidestep import floodfill
    segs, dem, tr = _segments_on_beach(tmp_path)
    seeds = floodfill.build_seed_mask(dem, tr)
    wl = pd.Series([0.3], index=pd.date_range("2026-09-06", periods=1, freq="h", tz="UTC"))
    table = hazard.hazard_table(segs, dem, seeds, wl)
    assert table.scenario_cm.unique().tolist() == [0]


# --- wheelchair ------------------------------------------------------------------

def test_wheelchair_depth_limit_is_strictest_pedestrian_limit():
    assert config.DEPTH_LIMIT_M["wheelchair"] < config.DEPTH_LIMIT_M["child"]
    flags = hazard.classify(np.array([0.1, 0.2]), np.array([False, False]))
    assert flags["wheelchair"].tolist() == [True, False]
    assert flags["child"].tolist() == [True, True]


def test_wheelchair_grade_limit_only_affects_wheelchair():
    depth = np.zeros(3)
    grade = np.array([2.0, 12.0, np.nan])      # gentle, too steep, unknown
    flags = hazard.classify(depth, np.zeros(3, dtype=bool), grade)
    assert flags["wheelchair"].tolist() == [True, False, True]
    assert flags["adult"].tolist() == [True, True, True]


def test_segments_carry_grade_pct(tmp_path):
    segs, _, _ = _segments_on_beach(tmp_path)
    assert "grade_pct" in segs.columns
    # the synthetic beach rises 3 cm per metre -> ~3 % grade
    assert segs.grade_pct.dropna().between(1.0, 6.0).all()


def test_wheelchair_cannot_use_steps_but_can_use_footway():
    assert not routing.edge_allowed("wheelchair", {"highway": "steps"})
    assert not routing.edge_allowed("wheelchair", {"highway": ["footway", "steps"]})
    assert routing.edge_allowed("wheelchair", {"highway": "footway"})
    assert routing.edge_allowed("adult", {"highway": "steps"})
    assert "wheelchair" in config.WALK_SPEED_MPS


# --- replay ----------------------------------------------------------------------

def test_replay_parse_date_rejects_today_and_future():
    with pytest.raises(ValueError):
        replay.parse_date(datetime.now().strftime("%Y-%m-%d"))
    assert replay.parse_date("2022-12-23").year == 2022
    with pytest.raises(ValueError):
        replay.parse_date("not-a-date")


def test_replay_risk_features_merge_and_filters():
    table = pd.DataFrame({
        "scenario_cm": 0, "segment_id": [1, 2, 1, 2], "forecast_hour": [0, 0, 1, 1],
        "valid_time": pd.to_datetime(["2022-12-23 05:00"] * 2 + ["2022-12-23 06:00"] * 2, utc=True),
        "water_level_m": [1.0, 1.0, 2.3, 2.3], "depth_cm": [0, 0, 30, 0],
        "flooded": [False, False, True, False],
        **{f"safe_{p}": [True, True, p in ("adult",), True] for p in hazard.PROFILES},
    })
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[-73.70, 40.83], [-73.70, 40.831]]},
         "properties": {"segment_id": 1, "name": "Shore Road", "ground_m": 2.0}},
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[-73.75, 40.80], [-73.75, 40.801]]},
         "properties": {"segment_id": 2, "name": "Other", "ground_m": 5.0}},
    ]}
    out = replay.risk_features(table, 1, fc)
    assert len(out["features"]) == 2
    p1 = out["features"][0]["properties"]
    assert p1["flooded"] and p1["depth_cm"] == 30 and p1["safe_adult"] and not p1["safe_child"]
    assert p1["replay"] is True and p1["name"] == "Shore Road"
    only = replay.risk_features(table, 1, fc, flooded_only=True)
    assert [f["properties"]["segment_id"] for f in only["features"]] == [1]
    boxed = replay.risk_features(table, 1, fc, bbox=(40.79, -73.76, 40.81, -73.74))
    assert [f["properties"]["segment_id"] for f in boxed["features"]] == [2]
    hs = replay.hours_summary(table)
    assert [h["flooded_segments"] for h in hs] == [0, 1]
    assert hs[1]["water_level_m"] == pytest.approx(2.3)


# --- NWS alerts ------------------------------------------------------------------

class _Resp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


def test_nws_alerts_sorted_and_cached(monkeypatch):
    nws.clear_cache()
    calls = []
    payload = {"features": [
        {"properties": {"id": "a", "event": "Flood Watch", "headline": "watch"}},
        {"properties": {"id": "b", "event": "Coastal Flood Warning", "headline": "warn",
                        "severity": "Severe", "senderName": "NWS Upton NY"}},
        {"properties": {"id": "c", "event": "Wind Advisory"}},
    ]}
    def fake_get(url, params, headers, timeout):
        calls.append(params); assert "User-Agent" in headers
        return _Resp(payload)
    monkeypatch.setattr(nws.requests, "get", fake_get)
    out = nws.active_alerts()
    assert [a["event"] for a in out["alerts"]] == ["Coastal Flood Warning", "Flood Watch", "Wind Advisory"]
    assert [a["id"] for a in out["coastal_alerts"]] == ["b", "a"]
    assert out["error"] is None
    nws.active_alerts()
    assert len(calls) == 1          # served from cache
    nws.clear_cache()


def test_nws_alerts_never_raise(monkeypatch):
    nws.clear_cache()
    def boom(*a, **k): raise RuntimeError("offline")
    monkeypatch.setattr(nws.requests, "get", boom)
    out = nws.active_alerts()
    assert out["alerts"] == [] and "offline" in out["error"]
    nws.clear_cache()


def test_nws_alerts_falls_back_to_stale_cache_on_refresh_failure(monkeypatch):
    """A refresh that fails AFTER a good fetch already populated the cache
    must keep serving the last good alerts (with ``error`` set), not go
    back to an empty list -- an outage shouldn't make a real active alert
    disappear from the banner."""
    nws.clear_cache()
    good_payload = {"features": [
        {"properties": {"id": "a", "event": "Coastal Flood Advisory", "headline": "still active"}},
    ]}
    calls = []

    def flaky_get(url, params, headers, timeout):
        calls.append(1)
        if len(calls) == 1:
            return _Resp(good_payload)
        raise RuntimeError("network down")

    monkeypatch.setattr(nws.requests, "get", flaky_get)
    # max_age_s=0 means the cache is always treated as expired, so the
    # second call actually re-attempts the fetch instead of short-circuiting
    first = nws.active_alerts(max_age_s=0)
    assert first["error"] is None and len(first["alerts"]) == 1

    second = nws.active_alerts(max_age_s=0)
    assert len(calls) == 2
    assert second["alerts"] == first["alerts"]           # stale data kept, not wiped
    assert second["coastal_alerts"] == first["coastal_alerts"]
    assert second["error"] is not None and "network down" in second["error"]
    nws.clear_cache()


# --- replay: load_inputs / observed_day / replay_day caching -------------------
# The tests above cover risk_features/hours_summary/parse_date on
# hand-built DataFrames (no filesystem). These cover the parts of
# replay.py that touch disk and the network boundary (coops.fetch_observed),
# which is why the module sat at 62% before this pass: load_inputs(),
# observed_day(), and replay_day()'s cache read/write paths were only
# exercised end-to-end via the live API, never in the test suite. Built on
# the same synthetic-DEM-plus-one-road fixture test_floodmodel.py already
# established, so nothing here depends on the real data/ directory.

def test_replay_observed_day_converts_local_midnight_to_utc(monkeypatch):
    """observed_day() must ask CO-OPS for America/New_York midnight, not
    UTC midnight -- Kings Point is UTC-5 (EST) in December, so the request
    should start at 05:00 UTC, not 00:00 UTC."""
    captured = {}

    def fake_fetch_observed(start_utc, hours):
        captured["start_utc"] = start_utc
        captured["hours"] = hours
        return pd.Series([1.0], index=pd.DatetimeIndex([start_utc]), name="wl_navd88_m")

    monkeypatch.setattr(replay.coops, "fetch_observed", fake_fetch_observed)
    out = replay.observed_day(datetime(2022, 12, 23))
    assert captured["hours"] == 24
    assert captured["start_utc"].hour == 5 and captured["start_utc"].tzinfo is not None
    assert len(out) == 1


def _write_replay_inputs(tmp_path):
    """A synthetic DEM + segments.gpkg on disk, laid out the way
    load_inputs() expects to find them under DATA_DIR."""
    from tidestep import segments as segmod
    path, _, tr = make_dem(tmp_path)

    def lonlat(col, row):
        return (tr.c + (col + 0.5) * tr.a, tr.f + (row + 0.5) * tr.e)

    road = LineString([lonlat(12, 50), lonlat(95, 50)])
    edges = gpd.GeoDataFrame({"u": [1], "v": [2], "key": [0], "osmid": [1],
                              "highway": ["residential"], "name": ["Shore Rd"],
                              "length": [83.0], "geometry": [road]}, crs=4326)
    segs = segmod.build_segments(edges, path)
    segs["near_inlet"] = False

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "dem_1m.tif").write_bytes(path.read_bytes())
    segs.to_file(data_dir / "segments.gpkg", driver="GPKG")
    return data_dir


def test_replay_load_inputs_reads_dem_and_segments_once(tmp_path, monkeypatch):
    data_dir = _write_replay_inputs(tmp_path)
    monkeypatch.setattr(replay, "DATA_DIR", data_dir)
    monkeypatch.setattr(replay, "REPLAY_DIR", data_dir / "replay")
    monkeypatch.setattr(streets, "WATER_PATH", data_dir / "no_such_water.gpkg")
    replay._inputs.clear()
    replay._tables.clear()

    inp = replay.load_inputs()
    assert set(inp) == {"dem", "seeds", "segments", "crs"}
    assert len(inp["segments"]) > 0
    assert inp["dem"].shape == inp["seeds"].shape

    # a second call without force=True must reuse the same dict, not reload
    # from disk -- delete the backing files and confirm it still works
    (data_dir / "dem_1m.tif").unlink()
    assert replay.load_inputs() is inp


def test_replay_day_caches_to_disk_and_memory_and_skips_recompute(tmp_path, monkeypatch):
    data_dir = _write_replay_inputs(tmp_path)
    monkeypatch.setattr(replay, "DATA_DIR", data_dir)
    monkeypatch.setattr(replay, "REPLAY_DIR", data_dir / "replay")
    monkeypatch.setattr(streets, "WATER_PATH", data_dir / "no_such_water.gpkg")
    replay._inputs.clear()
    replay._tables.clear()

    date = datetime(2022, 12, 23)
    calls = []

    def fake_observed_day(d):
        calls.append(d)
        t = datetime(d.year, d.month, d.day, 12, tzinfo=replay.LOCAL_TZ)
        return pd.Series([0.9], index=pd.DatetimeIndex([t]), name="wl_navd88_m")

    monkeypatch.setattr(replay, "observed_day", fake_observed_day)
    assert replay.cached_days() == []

    table = replay.replay_day(date)   # use_cache=True, water_levels=None (default)
    assert len(calls) == 1
    assert not table.empty and "flooded" in table.columns
    cache_file = data_dir / "replay" / "2022-12-23.csv"
    assert cache_file.exists()
    assert replay.cached_days() == ["2022-12-23"]

    # second call: served straight from the in-memory _tables dict, so
    # observed_day() is never invoked again
    same = replay.replay_day(date)
    assert len(calls) == 1
    assert same is table

    # third call: drop the in-memory cache but keep the CSV -- must be
    # read back from disk rather than recomputed
    replay._tables.clear()
    from_disk = replay.replay_day(date)
    assert len(calls) == 1
    pd.testing.assert_frame_equal(
        from_disk.reset_index(drop=True), table.reset_index(drop=True), check_dtype=False)


def test_replay_day_with_explicit_water_levels_bypasses_cache(tmp_path, monkeypatch):
    """A validation run (scripts/validate_streets.py) passes water_levels
    directly and never wants the result written to data/replay/ -- that
    directory is for the "replay a real observed day" feature, not for
    arbitrary documented peaks."""
    data_dir = _write_replay_inputs(tmp_path)
    monkeypatch.setattr(replay, "DATA_DIR", data_dir)
    monkeypatch.setattr(replay, "REPLAY_DIR", data_dir / "replay")
    monkeypatch.setattr(streets, "WATER_PATH", data_dir / "no_such_water.gpkg")
    replay._inputs.clear()
    replay._tables.clear()

    date = datetime(2022, 12, 23)
    wl = replay.water_levels_from_peak(date, 0.9)
    assert len(wl) == 1 and wl.index[0].hour == 12

    table = replay.replay_day(date, water_levels=wl)
    assert not table.empty
    assert not (data_dir / "replay").exists()          # nothing cached to disk
    assert "2022-12-23" not in replay._tables            # nor in memory


def test_replay_day_raises_on_empty_observed_series(tmp_path, monkeypatch):
    """A CO-OPS gap (station offline, data not yet published) must surface
    as a clear error, not silently build an empty hazard table."""
    data_dir = _write_replay_inputs(tmp_path)
    monkeypatch.setattr(replay, "DATA_DIR", data_dir)
    monkeypatch.setattr(replay, "REPLAY_DIR", data_dir / "replay")
    monkeypatch.setattr(streets, "WATER_PATH", data_dir / "no_such_water.gpkg")
    monkeypatch.setattr(replay, "observed_day", lambda d: pd.Series([], dtype="float64"))
    replay._inputs.clear()
    replay._tables.clear()

    with pytest.raises(ValueError, match="no observed water levels"):
        replay.replay_day(datetime(2022, 12, 23))


def test_replay_cached_days_empty_when_directory_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "REPLAY_DIR", tmp_path / "never_created")
    assert replay.cached_days() == []


# --- street-level validation helpers ------------------------------------------

def test_validate_streets_helpers():
    import importlib.util, sys
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "validate_streets", Path(__file__).resolve().parents[1] / "scripts" / "validate_streets.py")
    vs = importlib.util.module_from_spec(spec); sys.modules["validate_streets"] = vs
    spec.loader.exec_module(vs)
    # 10.8 ft MLLW at Kings Point = (10.8 - 4.21) ft above NAVD88 = 2.009 m
    assert vs.ft_mllw_to_m_navd88(10.8) == pytest.approx(2.009, abs=1e-3)
    segs = gpd.GeoDataFrame({
        "name": ["Shore Road", "West Shore Road", "Shore Drive;Hemlock Drive", None, "Shore Road"],
        "geometry": [LineString([(-73.700, 40.83), (-73.701, 40.83)]),
                     LineString([(-73.750, 40.82), (-73.751, 40.82)]),
                     LineString([(-73.705, 40.82), (-73.706, 40.82)]),
                     LineString([(-73.705, 40.82), (-73.706, 40.82)]),
                     LineString([(-73.760, 40.81), (-73.761, 40.81)])]}, crs=4326)
    m = vs.street_mask(segs, "Shore Road", "Port Washington")
    assert m.tolist() == [True, False, False, False, False]   # exact name AND east shore only
    assert vs.street_mask(segs, "Hemlock Drive").tolist() == [False, False, True, False, False]
    results = [
        {"verdict": "hit", "expect_flooded": True}, {"verdict": "miss", "expect_flooded": True},
        {"verdict": "correct (dry)", "expect_flooded": False},
        {"verdict": "street not in segments", "expect_flooded": True, "street": "Nowhere Ln"},
    ]
    s = vs.summarize(results)
    assert s["hits"] == 1 and s["reported_streets"] == 2 and s["negatives_correct"] == 1
    assert s["unmatched_street_names"] == ["Nowhere Ln"]
