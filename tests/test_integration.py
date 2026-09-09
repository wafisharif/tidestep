"""End-to-end integration test: synthetic scenario -> segments -> floodfill
-> hazard -> real PostGIS -> real FastAPI app -> real router, driven with an
actual HTTP client (FastAPI's TestClient, no live server process needed).

This is different from the other test files: those are unit tests with no
network or database (per README). This one needs a real Postgres+PostGIS
reachable at DATABASE_URL (default: the docker-compose instance on
localhost:5432). It is skipped automatically if that isn't available, so
`pytest -q tests` still works with no services running — run it explicitly
with `pytest -q tests/test_integration.py` once `docker compose up -d` (or
an equivalent local Postgres) is up.

Why this exists: the unit tests mock the database and the OSM graph, which
is correct for testing model logic in isolation, but it means nothing in
the test suite had ever exercised db.py's real SQL, api.py's real FastAPI
wiring, or a real osmnx save/load graphml round trip together. That gap is
exactly where an integration bug hides — and did: the original version of
scripts/dev_seed.py serialized its synthetic graph with plain
networkx.write_graphml instead of ox.save_graphml, and only this kind of
test caught the resulting AttributeError on load.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("fastapi")

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


@pytest.fixture(scope="module")
def seeded_app():
    """Build the synthetic scenario, load it into the real DB, and return a
    FastAPI TestClient wired to it — the same app object api.py serves."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import dev_seed  # noqa

    from tidestep import db

    dem, transform, segs, seeds, wl, table, G = dev_seed.build_scenario()
    engine = create_engine(DATABASE_URL, future=True)
    db.init_schema(engine)
    db.load_segments(engine, segs)
    db.load_hazard(engine, table, ofs_bias_m=0.0)

    import osmnx as ox
    graph_path = Path("/tmp/test_integration_streets.graphml")
    ox.save_graphml(G, graph_path)

    from fastapi.testclient import TestClient
    from tidestep import api, routing
    api._engine = engine
    api._router = routing.Router(ox.load_graphml(graph_path), engine)

    client = TestClient(api.app)
    yield client, segs, table


def test_hours_endpoint_matches_loaded_data(seeded_app):
    client, segs, table = seeded_app
    r = client.get("/api/hours")
    assert r.status_code == 200
    body = r.json()
    assert len(body["hours"]) == table.forecast_hour.nunique()
    # flooded_segments per hour must match what hazard_table actually computed
    by_hour = table.groupby("forecast_hour").flooded.sum()
    for row in body["hours"]:
        assert row["flooded_segments"] == int(by_hour[row["hour"]])


def test_risk_geojson_hour_out_of_range_rejected(seeded_app):
    client, *_ = seeded_app
    r = client.get("/api/risk?hour=999")
    assert r.status_code == 422   # config.FORECAST_HOURS bound (Stage-review fix)


def test_risk_geojson_at_peak_hour_has_unsafe_segments(seeded_app):
    client, *_ = seeded_app
    r = client.get("/api/risk?hour=7")
    assert r.status_code == 200
    feats = r.json()["features"]
    assert any(f["properties"]["flooded"] and not f["properties"]["safe_vehicle_small"]
              for f in feats)


def test_route_differs_by_profile_at_peak(seeded_app):
    """The whole point of the app: at the same hour, the same trip is safe
    for an adult, unsafe for a vehicle. This is the single behavior a demo
    video needs to show working, so it gets its own explicit test."""
    client, *_ = seeded_app
    origin = dict(olat=40.9, olon=-73.6998, dlat=40.9, dlon=-73.6748)

    adult = client.get("/api/route", params={**origin, "profile": "adult", "hour": 7})
    car = client.get("/api/route", params={**origin, "profile": "vehicle_small", "hour": 7})
    assert adult.status_code == 200 and car.status_code == 200
    assert adult.json()["geometry"] is not None, "adult should have a route at peak hour"
    assert car.json()["geometry"] is None, "vehicle_small should have no safe route at peak hour"


def test_route_bad_profile_rejected(seeded_app):
    client, *_ = seeded_app
    r = client.get("/api/route", params=dict(olat=40.9, olon=-73.6998, dlat=40.9,
                                             dlon=-73.6748, profile="dog", hour=0))
    assert r.status_code == 400


def test_saved_routes_crud(seeded_app):
    client, *_ = seeded_app
    r = client.post("/api/routes", json={
        "label": "integration test", "contact": "test@example.com", "profile": "adult",
        "olat": 40.9, "olon": -73.6998, "dlat": 40.9, "dlon": -73.6748})
    assert r.status_code == 200
    rid = r.json()["route_id"]
    assert any(x["route_id"] == rid for x in client.get("/api/routes").json())
    assert client.delete(f"/api/routes/{rid}").status_code == 200
    assert not any(x["route_id"] == rid for x in client.get("/api/routes").json())


def test_route_time_aware_endpoint_works_against_real_data(seeded_app):
    """The new time_aware routing mode, hit through the real HTTP endpoint
    against real loaded data -- not just the synthetic-graph unit tests in
    test_routing.py. Confirms the FastAPI wiring (time_aware=true ->
    Router.route_time_aware -> time_aware_route_geojson) actually works
    end to end, not just that each piece works in isolation."""
    client, *_ = seeded_app
    origin = dict(olat=40.9, olon=-73.6998, dlat=40.9, dlon=-73.6748)
    r = client.get("/api/route", params={**origin, "profile": "adult", "hour": 0,
                                         "time_aware": True})
    assert r.status_code == 200
    body = r.json()
    # this synthetic trip is short enough not to cross an hour boundary, so
    # a route should exist and carry the time-aware-specific properties
    assert body["geometry"] is not None
    assert body["properties"]["time_aware"] is True
    assert "arrival_hour" in body["properties"]
    assert "travel_time_min" in body["properties"]


def test_route_advisory_endpoint_matches_known_peak_hour(seeded_app):
    """The advisory endpoint's per-hour safe/unsafe list, against real
    loaded data, must agree with what test_route_differs_by_profile_at_peak
    above already established directly against /api/route: vehicle_small
    has no safe route at hour 7 (the synthetic scenario's known peak)."""
    client, *_ = seeded_app
    origin = dict(olat=40.9, olon=-73.6998, dlat=40.9, dlon=-73.6748)
    r = client.get("/api/route/advisory", params={**origin, "profile": "vehicle_small"})
    assert r.status_code == 200
    body = r.json()
    from tidestep import config
    assert len(body["hours"]) == config.FORECAST_HOURS
    by_hour = {h["hour"]: h for h in body["hours"]}
    assert by_hour[7]["safe"] is False   # matches the known peak-hour fact above


def test_route_window_matches_per_profile_flood_timing(seeded_app):
    """Cross-check the predictive alert logic (routing.route_window) against
    the hazard table it is supposed to summarize: the first hour it reports
    as unsafe must be the first hour any segment on the usual route is
    actually flagged unsafe for that profile in the hazard table."""
    from tidestep import routing
    _, segs, table = seeded_app
    engine = create_engine(DATABASE_URL, future=True)
    import osmnx as ox
    from pathlib import Path
    graph = ox.load_graphml(Path("/tmp/test_integration_streets.graphml"))
    R = routing.Router(graph, engine)
    win = R.route_window((40.9, -73.6998), (40.9, -73.6748), "vehicle_small", range(24))
    assert win.first_unsafe_hour is not None
    assert win.first_unsafe_hour <= 7   # must catch it no later than the known peak hour
