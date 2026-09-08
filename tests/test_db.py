"""Stage 4 PostGIS storage layer, unit-tested directly against db.py's
functions (not through the API, unlike test_integration.py). Needs a real
Postgres+PostGIS reachable at DATABASE_URL; skipped automatically if none is
available, same pattern as test_integration.py.

This covers things test_integration.py's API-level tests never reach
directly: unsafe_edges' profile-validation guard (the actual SQL-injection
boundary), edge_hazard's BOOL_AND aggregation across multiple segments on
one edge, valid_times, and the saved-route update/delete paths.
"""
from __future__ import annotations

import os

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text  # noqa: E402

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
    # start every test from a clean slate: saved_routes has no FK to
    # segments/hazard, so it needs its own explicit wipe
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM hazard"))
        conn.execute(text("DELETE FROM segments"))
        conn.execute(text("DELETE FROM saved_routes"))
    yield eng


def _two_segments() -> gpd.GeoDataFrame:
    """Two segments on the SAME edge (u=1,v=2,key=0) plus one on a second
    edge (u=2,v=3,key=0), so edge_hazard's GROUP BY / BOOL_AND has something
    real to aggregate over."""
    return gpd.GeoDataFrame({
        "segment_id": [0, 1, 2],
        "u": [1, 1, 2], "v": [2, 2, 3], "key": [0, 0, 0], "seg_idx": [0, 1, 0],
        "name": ["Shore Rd", "Shore Rd", "Cove Rd"],
        "highway": ["residential", "residential", "residential"],
        "length_m": [15.0, 15.0, 15.0],
        "ground_m": [0.1, 0.3, 0.5],
        "near_inlet": [False, False, True],
        "geometry": [
            LineString([(-73.71, 40.80), (-73.7099, 40.80)]),
            LineString([(-73.7099, 40.80), (-73.7098, 40.80)]),
            LineString([(-73.7098, 40.80), (-73.7098, 40.8001)]),
        ],
    }, crs=4326)


def _hazard_for(segs: gpd.GeoDataFrame, hour: int, flooded_ids: set[int],
                depth_cm: int = 40) -> pd.DataFrame:
    t = pd.Timestamp("2026-09-07", tz="UTC") + pd.Timedelta(hours=hour)
    rows = []
    for sid in segs["segment_id"]:
        flooded = sid in flooded_ids
        rows.append({
            "segment_id": sid, "forecast_hour": hour, "valid_time": t,
            "water_level_m": 1.0, "depth_cm": depth_cm if flooded else 0,
            "flooded": flooded,
            "safe_child": not flooded, "safe_adult": not flooded,
            "safe_vehicle_small": not flooded, "safe_vehicle_large": not flooded,
            "safe_vehicle_4wd": not flooded,
        })
    return pd.DataFrame(rows)


def test_init_schema_creates_all_tables_and_postgis(engine):
    with engine.connect() as conn:
        tables = {r[0] for r in conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))}
        assert {"segments", "hazard", "forecast_runs", "saved_routes"} <= tables
        assert conn.execute(text("SELECT postgis_version()")).scalar_one()


def test_load_segments_round_trip(engine):
    from tidestep import db
    segs = _two_segments()
    n = db.load_segments(engine, segs)
    assert n == 3
    with engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM segments")).scalar_one()
        near_inlet_count = conn.execute(
            text("SELECT count(*) FROM segments WHERE near_inlet")).scalar_one()
    assert count == 3
    assert near_inlet_count == 1


def test_load_segments_replaces_previous_run(engine):
    from tidestep import db
    db.load_segments(engine, _two_segments())
    smaller = _two_segments().iloc[[0]]
    n = db.load_segments(engine, smaller)
    assert n == 1
    with engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM segments")).scalar_one()
    assert count == 1   # old rows gone, not accumulated


def test_load_hazard_and_valid_times(engine):
    from tidestep import db
    segs = _two_segments()
    db.load_segments(engine, segs)
    hz0 = _hazard_for(segs, 0, flooded_ids=set())
    hz1 = _hazard_for(segs, 1, flooded_ids={0, 1})
    table = pd.concat([hz0, hz1], ignore_index=True)
    n = db.load_hazard(engine, table, ofs_bias_m=0.15)
    assert n == 6
    times = db.valid_times(engine)
    assert len(times) == 2
    with engine.connect() as conn:
        bias = conn.execute(text(
            "SELECT ofs_bias_m FROM forecast_runs ORDER BY run_id DESC LIMIT 1")).scalar_one()
    assert bias == pytest.approx(0.15)


def test_risk_geojson_shape_and_bbox_filter(engine):
    from tidestep import db
    segs = _two_segments()
    db.load_segments(engine, segs)
    db.load_hazard(engine, _hazard_for(segs, 0, flooded_ids={2}))

    fc = db.risk_geojson(engine, 0)
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 3
    flooded_ids = {f["properties"]["segment_id"] for f in fc["features"]
                  if f["properties"]["flooded"]}
    assert flooded_ids == {2}

    # bbox that only covers segment 2's location (around 40.8001, -73.7098)
    tight = db.risk_geojson(engine, 0, bbox=(40.80005, -73.71, 40.8002, -73.7097))
    ids = {f["properties"]["segment_id"] for f in tight["features"]}
    assert ids == {2}


def test_unsafe_edges_returns_edges_with_any_unsafe_segment(engine):
    from tidestep import db
    segs = _two_segments()
    db.load_segments(engine, segs)
    # segment 1 (on edge u=1,v=2) unsafe; segment 0 (same edge) safe;
    # segment 2 (edge u=2,v=3) safe
    hz = _hazard_for(segs, 0, flooded_ids={1})
    db.load_hazard(engine, hz)
    edges = db.unsafe_edges(engine, 0, "adult")
    assert (1, 2, 0) in edges     # the edge is unsafe because ONE segment is
    assert (2, 3, 0) not in edges


def test_unsafe_edges_rejects_unknown_profile(engine):
    from tidestep import db
    with pytest.raises(ValueError):
        db.unsafe_edges(engine, 0, "profile'; DROP TABLE segments; --")


def test_edge_hazard_aggregates_max_depth_and_bool_and_across_segments(engine):
    from tidestep import db
    segs = _two_segments()
    db.load_segments(engine, segs)
    t = pd.Timestamp("2026-09-07", tz="UTC")
    # edge (1,2,0) has two segments: one flooded 60 cm unsafe, one dry safe.
    # BOOL_AND must come out False (the edge as a whole isn't safe) and
    # depth_cm must be the MAX across the edge's segments (60), not the min.
    rows = [
        {"segment_id": 0, "forecast_hour": 0, "valid_time": t, "water_level_m": 1.0,
         "depth_cm": 60, "flooded": True, "safe_child": False, "safe_adult": False,
         "safe_vehicle_small": False, "safe_vehicle_large": False, "safe_vehicle_4wd": False},
        {"segment_id": 1, "forecast_hour": 0, "valid_time": t, "water_level_m": 1.0,
         "depth_cm": 0, "flooded": False, "safe_child": True, "safe_adult": True,
         "safe_vehicle_small": True, "safe_vehicle_large": True, "safe_vehicle_4wd": True},
        {"segment_id": 2, "forecast_hour": 0, "valid_time": t, "water_level_m": 1.0,
         "depth_cm": 0, "flooded": False, "safe_child": True, "safe_adult": True,
         "safe_vehicle_small": True, "safe_vehicle_large": True, "safe_vehicle_4wd": True},
    ]
    db.load_hazard(engine, pd.DataFrame(rows))
    edge_hz = db.edge_hazard(engine, 0).set_index(["u", "v", "key"])
    assert edge_hz.loc[(1, 2, 0), "depth_cm"] == 60
    assert edge_hz.loc[(1, 2, 0), "safe_adult"] == False  # noqa: E712
    assert edge_hz.loc[(2, 3, 0), "safe_adult"] == True   # noqa: E712


def test_saved_routes_crud_and_state_update(engine):
    from tidestep import db
    rid = db.add_saved_route(engine, "Home to school", "parent@example.com", "child",
                             origin=(40.80, -73.71), destination=(40.81, -73.70))
    routes = db.list_saved_routes(engine)
    assert len(routes) == 1
    r = routes[0]
    assert r["route_id"] == rid
    assert r["olat"] == pytest.approx(40.80) and r["olon"] == pytest.approx(-73.71)
    assert r["dlat"] == pytest.approx(40.81) and r["dlon"] == pytest.approx(-73.70)
    assert r["last_blocked"] is False

    db.update_route_state(engine, rid, True)
    routes = db.list_saved_routes(engine)
    assert routes[0]["last_blocked"] is True
    assert routes[0]["last_checked"] is not None

    db.delete_saved_route(engine, rid)
    assert db.list_saved_routes(engine) == []
