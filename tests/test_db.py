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
        conn.execute(text("DELETE FROM shelters"))
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
        assert {"segments", "hazard", "forecast_runs", "saved_routes", "shelters"} <= tables
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


def _two_shelters() -> gpd.GeoDataFrame:
    from shapely.geometry import Point
    return gpd.GeoDataFrame(
        {"osmid": ["s1", "s2"], "name": ["Cove Harbor Elementary", "Kings Point Fire Dept"],
         "kind": ["school", "fire station"]},
        geometry=[Point(-73.71, 40.80), Point(-73.70, 40.81)], crs=4326)


def test_load_shelters_round_trip(engine):
    from tidestep import db
    n = db.load_shelters(engine, _two_shelters())
    assert n == 2
    with engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM shelters")).scalar_one()
    assert count == 2


def test_load_shelters_replaces_previous_run(engine):
    from tidestep import db
    db.load_shelters(engine, _two_shelters())
    db.load_shelters(engine, _two_shelters().iloc[[0]])
    with engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM shelters")).scalar_one()
    assert count == 1


def test_shelter_points_returns_lat_lon_for_every_loaded_shelter(engine):
    from tidestep import db
    db.load_shelters(engine, _two_shelters())
    rows = db.shelter_points(engine)
    assert len(rows) == 2
    names = {r["name"] for r in rows}
    assert names == {"Cove Harbor Elementary", "Kings Point Fire Dept"}
    school = next(r for r in rows if r["name"] == "Cove Harbor Elementary")
    assert school["lat"] == pytest.approx(40.80)
    assert school["lon"] == pytest.approx(-73.71)
    assert school["kind"] == "school"


def test_shelter_points_empty_when_nothing_loaded(engine):
    from tidestep import db
    assert db.shelter_points(engine) == []


def test_nearest_shelter_finds_closest_within_radius(engine):
    from tidestep import db
    db.load_shelters(engine, _two_shelters())
    # right on top of the school (-73.71, 40.80); the fire dept is ~1.3 km away
    row = db.nearest_shelter(engine, 40.8001, -73.7101, max_m=200)
    assert row is not None
    assert row["name"] == "Cove Harbor Elementary"
    assert row["distance_m"] < 200


def test_nearest_shelter_returns_none_outside_radius(engine):
    from tidestep import db
    db.load_shelters(engine, _two_shelters())
    row = db.nearest_shelter(engine, 40.8001, -73.7101, max_m=1)
    assert row is None


def test_nearest_shelter_returns_none_when_table_empty(engine):
    from tidestep import db
    assert db.nearest_shelter(engine, 40.80, -73.71, max_m=1000) is None


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


# --- gaps found via `pytest --cov=tidestep --cov-report=term-missing` after
# the DB integration suite was first run against a live Postgres (2026-09):
# get_engine()'s env-var default, load_segments()'s near_inlet fallback,
# segments_geojson(), always_safe_nodes()'s validation/empty-hours guards,
# and the legacy hazard-primary-key migration path were all still
# genuinely untested even with a real database available. ----------------

def test_get_engine_falls_back_to_database_url_env_var(monkeypatch):
    """db.get_engine() with no explicit url reads DATABASE_URL itself
    (every other fixture in this suite bypasses that by calling
    sqlalchemy.create_engine directly with an already-resolved URL)."""
    from tidestep import db
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    eng = db.get_engine()
    with eng.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar_one() == 1
    eng.dispose()


def test_get_engine_falls_back_to_default_url_when_no_env_var_set(monkeypatch):
    from tidestep import db
    monkeypatch.delenv("DATABASE_URL", raising=False)
    eng = db.get_engine()
    assert str(eng.url) == db.DEFAULT_URL.replace("tidestep:tidestep", "tidestep:***")


def test_load_segments_defaults_near_inlet_false_when_column_missing(engine):
    """A segments GeoDataFrame built without a near_inlet column (e.g. an
    older segments.py that predates the near-inlet flag) must load with
    every segment defaulted to not-near-inlet, rather than erroring on
    the missing column."""
    from tidestep import db
    segs = _two_segments().drop(columns=["near_inlet"])
    assert "near_inlet" not in segs.columns
    n = db.load_segments(engine, segs)
    assert n == 3
    with engine.connect() as conn:
        flagged = conn.execute(text("SELECT count(*) FROM segments WHERE near_inlet")).scalar_one()
    assert flagged == 0


def test_segments_geojson_returns_static_segment_feature_collection(engine):
    """Used by the historical-replay endpoints (api.py's _segments_fc()) to
    get segment geometry/attributes with no hazard join at all -- never
    exercised directly against a real DB anywhere else in this suite (the
    replay API tests mock it out to isolate replay.py's own logic)."""
    from tidestep import db
    db.load_segments(engine, _two_segments())
    fc = db.segments_geojson(engine)
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 3
    ids = {f["properties"]["segment_id"] for f in fc["features"]}
    assert ids == {0, 1, 2}
    for f in fc["features"]:
        assert f["geometry"]["type"] == "LineString"
        assert "flooded" not in f["properties"]   # no hazard join -- static only


def test_always_safe_nodes_rejects_unknown_profile(engine):
    from tidestep import db
    with pytest.raises(ValueError, match="unknown profile"):
        db.always_safe_nodes(engine, [0, 1], "dog")


def test_always_safe_nodes_returns_empty_set_for_no_hours(engine):
    """An empty ``hours`` iterable is a degenerate "safe at zero hours"
    request -- must short-circuit to an empty set without ever issuing
    the ANY(:hours)/COUNT(*) = :n SQL (which would be meaningless, and
    for an empty array parameter behaves inconsistently across drivers)."""
    from tidestep import db
    assert db.always_safe_nodes(engine, [], "adult") == set()


def test_always_safe_nodes_requires_every_requested_hour_present(engine):
    """A node whose only segment is safe at hour 0 but has NO hazard row
    at all for hour 1 must NOT count as "always safe across [0, 1]" --
    the COUNT(*) = :n guard in the SQL exists specifically so a missing
    row isn't vacuously treated as safe. Only the node reachable via a
    segment safe at BOTH requested hours should come back."""
    from tidestep import db
    segs = _two_segments()
    db.load_segments(engine, segs)
    # segment 0 (u=1,v=2): safe at hour 0, but no row at all for hour 1
    # segment 2 (u=2,v=3): safe at both hour 0 and hour 1
    h0 = _hazard_for(segs, 0, flooded_ids=set())
    h1 = _hazard_for(segs, 1, flooded_ids=set())
    combined = pd.concat([h0, h1[h1.segment_id == 2]], ignore_index=True)
    db.load_hazard(engine, combined)
    nodes = db.always_safe_nodes(engine, [0, 1], "adult")
    assert nodes == {2, 3}   # only segment 2's endpoints
    assert 1 not in nodes    # segment 0's node -- missing an hour-1 row


def test_shelter_points_returns_empty_list_when_table_is_missing(engine):
    """shelter_points() must never raise for a database created before
    Stage 11 added the shelters table at all -- route_to_safety() falls
    back to plain dry-street routing in that case, so this has to degrade
    to [] rather than a 500. init_schema() (called by the engine fixture
    for every other test) always creates the table, so this drops it to
    simulate a genuinely pre-Stage-11 database; the fixture's next
    init_schema() call recreates it for whichever test runs after this."""
    from tidestep import db
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS shelters"))
    assert db.shelter_points(engine) == []


def test_nearest_shelter_returns_none_when_table_is_missing(engine):
    from tidestep import db
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS shelters"))
    assert db.nearest_shelter(engine, 40.80, -73.71, max_m=1000) is None


def test_init_schema_migrates_legacy_hazard_primary_key(engine):
    """Before Stage 11 added sea-level-rise scenarios, hazard's primary
    key was (segment_id, valid_time) with no scenario_cm column at all.
    init_schema()'s MIGRATIONS block plus the _HAZARD_PK_HAS_SCENARIO
    check must upgrade a database still on that legacy schema -- add the
    new columns with safe defaults, rebuild the primary key to include
    scenario_cm, and do it WITHOUT losing the pre-existing row -- and
    running init_schema() a second time afterwards must be a no-op, not
    an error (every server restart calls init_schema() unconditionally)."""
    from tidestep import db
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS hazard"))
        conn.execute(text("""
            CREATE TABLE hazard (
                segment_id         INTEGER NOT NULL REFERENCES segments(segment_id),
                forecast_hour      INTEGER NOT NULL,
                valid_time         TIMESTAMPTZ NOT NULL,
                water_level_m      REAL,
                depth_cm           INTEGER NOT NULL,
                flooded            BOOLEAN NOT NULL,
                safe_child         BOOLEAN NOT NULL,
                safe_adult         BOOLEAN NOT NULL,
                safe_vehicle_small BOOLEAN NOT NULL,
                safe_vehicle_large BOOLEAN NOT NULL,
                safe_vehicle_4wd   BOOLEAN NOT NULL,
                PRIMARY KEY (segment_id, valid_time)
            )
        """))
        conn.execute(text("""
            INSERT INTO segments (segment_id, u, v, key, seg_idx, name, highway,
                length_m, ground_m, near_inlet, geom)
            VALUES (1, 1, 2, 0, 0, 'Test St', 'residential', 10.0, 0.5, false,
                ST_GeomFromText('LINESTRING(-73.7 40.9, -73.699 40.9)', 4326))
        """))
        conn.execute(text("""
            INSERT INTO hazard (segment_id, forecast_hour, valid_time, water_level_m,
                depth_cm, flooded, safe_child, safe_adult, safe_vehicle_small,
                safe_vehicle_large, safe_vehicle_4wd)
            VALUES (1, 0, '2026-09-07T00:00:00Z', 0.3, 10, true, false, true, true, true, true)
        """))
        cols = {r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'hazard'")).all()}
    assert "scenario_cm" not in cols and "safe_wheelchair" not in cols   # legacy confirmed

    db.init_schema(engine)   # the migration under test

    with engine.connect() as conn:
        cols = {r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'hazard'")).all()}
        assert {"scenario_cm", "safe_wheelchair"} <= cols
        pk_cols = {r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.key_column_usage "
            "WHERE table_name = 'hazard' AND constraint_name = 'hazard_pkey'")).all()}
        assert pk_cols == {"scenario_cm", "segment_id", "valid_time"}
        row = conn.execute(text(
            "SELECT scenario_cm, safe_wheelchair, depth_cm, flooded "
            "FROM hazard WHERE segment_id = 1")).first()
    assert tuple(row) == (0, True, 10, True)   # pre-existing row preserved, defaults applied

    db.init_schema(engine)   # idempotency: must not raise the second time
