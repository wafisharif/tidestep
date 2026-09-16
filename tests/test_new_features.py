"""Sea-level-rise scenarios, wheelchair profile, historical replay, NWS
alerts, and the street-level validation helpers. No network, no DB."""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest
import geopandas as gpd
from shapely.geometry import LineString

from tidestep import config, hazard, nws, replay, routing
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
