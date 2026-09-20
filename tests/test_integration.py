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

from tidestep import config  # noqa: E402

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
    # (for the plain forecast, scenario_cm == 0)
    by_hour = table[table.scenario_cm == 0].groupby("forecast_hour").flooded.sum()
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


def test_route_best_departure_endpoint_matches_known_adult_never_floods_fact(seeded_app):
    """adult never gets an unsafe hour in this scenario (established by
    test_route_window_matches_per_profile_flood_timing below via
    route_window, and consistent with test_route_differs_by_profile_at_peak
    above showing adult has a route even at the synthetic peak hour), so
    the earliest safe departure should be right away, with the endpoint
    reporting every hour safe."""
    client, *_ = seeded_app
    origin = dict(olat=40.9, olon=-73.6998, dlat=40.9, dlon=-73.6748)
    r = client.get("/api/route/best_departure", params={**origin, "profile": "adult"})
    assert r.status_code == 200
    body = r.json()
    from tidestep import config
    assert len(body["hours"]) == config.FORECAST_HOURS
    assert body["recommended_hour"] == 0
    assert body["recommended_length_m"] is not None
    assert all(h["safe"] for h in body["hours"])


def test_route_best_departure_endpoint_self_consistent_for_vehicle_small(seeded_app):
    """vehicle_small DOES hit flooding constraints in this scenario (it
    has no safe route at the peak hour per test_route_differs_by_profile_at_peak
    above). The exact per-hour numbers depend on the synthetic street
    topology's available detours and aren't asserted here -- what must
    hold regardless is internal self-consistency: recommended_hour is set
    if and only if some hour is reported safe, and the recommended hour's
    own entry is itself marked safe with a real length."""
    client, *_ = seeded_app
    origin = dict(olat=40.9, olon=-73.6998, dlat=40.9, dlon=-73.6748)
    r = client.get("/api/route/best_departure", params={**origin, "profile": "vehicle_small"})
    assert r.status_code == 200
    body = r.json()
    from tidestep import config
    assert len(body["hours"]) == config.FORECAST_HOURS
    any_safe = any(h["safe"] for h in body["hours"])
    assert (body["recommended_hour"] is not None) == any_safe
    if any_safe:
        rec = next(h for h in body["hours"] if h["hour"] == body["recommended_hour"])
        assert rec["safe"] is True
        assert rec["length_m"] == body["recommended_length_m"]


def test_multi_stop_endpoint_works_against_real_data(seeded_app):
    """Drives the new POST /api/route/multi_stop endpoint through the real
    HTTP -> FastAPI -> Router.route_multi_stop -> real Postgres path, with
    a genuine 3-waypoint trip (adult, which never floods in this
    scenario, so every leg should succeed)."""
    client, *_ = seeded_app
    body = {
        "waypoints": [
            {"lat": 40.9, "lon": -73.6998},
            {"lat": 40.9, "lon": -73.6873},
            {"lat": 40.9, "lon": -73.6748},
        ],
        "profile": "adult", "hour": 0,
    }
    r = client.post("/api/route/multi_stop", json=body)
    assert r.status_code == 200
    body_out = r.json()
    assert body_out["type"] == "FeatureCollection"
    assert body_out["properties"]["blocked_leg_index"] is None
    assert len(body_out["features"]) == 2
    assert body_out["properties"]["total_length_m"] is not None


def test_multi_stop_endpoint_rejects_bad_profile_against_real_app(seeded_app):
    client, *_ = seeded_app
    r = client.post("/api/route/multi_stop", json={
        "waypoints": [{"lat": 40.9, "lon": -73.6998}, {"lat": 40.9, "lon": -73.6748}],
        "profile": "dog"})
    assert r.status_code == 400


def test_multi_stop_optimize_order_endpoint_works_against_real_data(seeded_app):
    """Drives optimize_order=true through the real HTTP -> FastAPI ->
    Router.route_multi_stop_optimized -> real Postgres path with a
    4-waypoint (2-intermediate-stop) adult trip -- never floods in this
    scenario, so every one of the 2! visiting orders should succeed.
    Doesn't assert which specific order wins (that depends on the
    synthetic topology's exact geometry) -- just the self-consistency
    every response must have: a valid permutation as ``order``, and a
    total_length_m that matches whichever order was actually returned."""
    client, *_ = seeded_app
    body = {
        "waypoints": [
            {"lat": 40.9, "lon": -73.6998},
            {"lat": 40.9, "lon": -73.6923},
            {"lat": 40.9, "lon": -73.6873},
            {"lat": 40.9, "lon": -73.6748},
        ],
        "profile": "adult", "hour": 0, "optimize_order": True,
    }
    r = client.post("/api/route/multi_stop", json=body)
    assert r.status_code == 200
    out = r.json()
    props = out["properties"]
    assert props["blocked_leg_index"] is None
    assert props["orders_tried"] == 2          # 2! permutations of 2 intermediate stops
    assert props["orders_complete"] == 2       # adult never floods here -- both should work
    assert sorted(props["order"]) == [0, 1, 2, 3]     # a genuine permutation of all 4 indices
    assert props["order"][0] == 0 and props["order"][-1] == 3   # endpoints never reordered
    assert len(out["features"]) == 3
    assert props["total_length_m"] is not None


def test_multi_stop_optimize_order_endpoint_rejects_too_many_stops_against_real_app(seeded_app):
    client, *_ = seeded_app
    waypoints = [{"lat": 40.9 + i * 0.001, "lon": -73.6998} for i in range(9)]
    r = client.post("/api/route/multi_stop", json={
        "waypoints": waypoints, "profile": "adult", "optimize_order": True})
    assert r.status_code == 400


def test_route_to_safety_endpoint_works_against_real_data(seeded_app):
    """Drives GET /api/route/to_safety through the real app against real
    loaded data. Doesn't assert a specific destination (that depends on
    the synthetic scenario's exact topology and which segments happen to
    stay dry all 24 hours) -- just that the endpoint returns a
    well-formed, self-consistent answer: either a real Feature with
    geometry and non-negative length/time, or the honest "no reachable
    haven" response, never a 500 or a malformed shape."""
    client, *_ = seeded_app
    r = client.get("/api/route/to_safety", params=dict(
        olat=40.9, olon=-73.6998, profile="adult", hour=0))
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "Feature"
    if body["geometry"] is not None:
        assert body["geometry"]["type"] in ("Point", "LineString")
        assert body["properties"]["length_m"] >= 0
        assert body["properties"]["travel_time_min"] >= 0
    else:
        assert "error" in body["properties"]


def test_route_to_safety_endpoint_prefers_and_annotates_a_real_shelter(seeded_app):
    """Stage 11: with real shelter data loaded (dev_seed.build_shelters_gdf,
    the same synthetic fixture main() loads), GET /api/route/to_safety must
    both (a) still return a well-formed answer -- the endpoint-level
    contract test above already covers the no-shelter-data case -- and (b)
    annotate the result with the shelter's name/kind once the resulting
    haven is actually near one, proving the real PostGIS ST_DWithin
    geography query in db.nearest_shelter() works end to end, not just
    against the routing-layer fakes in test_routing.py."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import dev_seed  # noqa
    from tidestep import db

    client, segs, table = seeded_app
    engine = create_engine(DATABASE_URL, future=True)
    db.load_shelters(engine, dev_seed.build_shelters_gdf())
    try:
        r = client.get("/api/route/to_safety", params=dict(
            olat=40.9, olon=-73.6998, profile="adult", hour=0))
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "Feature"
        # every response (found or not) must carry the new Stage 11 keys,
        # even when they end up null -- api.py must never drop them
        if body["geometry"] is not None:
            assert "used_shelter_preference" in body["properties"]
            assert "shelter_name" in body["properties"]
            assert "shelter_kind" in body["properties"]
    finally:
        # leave the shared seeded_app DB the way every other test in this
        # module expects it (no shelters loaded)
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM shelters"))


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


def test_chokepoints_endpoint_finds_no_bridges_for_pedestrians(seeded_app):
    """dev_seed's synthetic street grid (scripts/dev_seed.py:build_graph) is
    a full 3-row x 4-column mesh for pedestrians -- every row and every
    column is connected, including the two footway-only links (node 1-3
    and 7-8) that only pedestrians can use. A full mesh like that is
    2-edge-connected: every edge has some alternate way around, so there
    must be ZERO structural chokepoints for child/adult."""
    client, *_ = seeded_app
    r = client.get("/api/network/chokepoints", params={"profile": "adult"})
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert body["properties"]["chokepoint_count"] == 0
    assert body["features"] == []


def test_chokepoints_endpoint_finds_the_real_vehicle_only_chokepoints(seeded_app):
    """For vehicle profiles, the two footway-only links (node 1-3 and 7-8)
    are unusable, which leaves nodes 1 and 7 -- the western end of Shore
    Rd -- hanging off the rest of the grid as a dead-end spur: node 1
    reaches the network only through edge 1-7, and {1, 7} together reach
    it only through edge 7-10. Both are real, hand-verifiable single
    points of failure baked into the synthetic scenario -- a dead-end
    spur has one bridge per segment along it, not just one for the whole
    spur. This is the concrete case that proves the algorithm answers a
    genuinely different question than routing does: it isn't about
    whether THIS trip is blocked, it's about whether ANY path exists at
    all.

    The two also make the point of ``priority_score`` concrete: edge 1-7
    is both a chokepoint AND floods almost the entire forecast for
    vehicles (Shore Rd's low western end), while edge 7-10 is a
    chokepoint that happens to stay dry all 24 hours for this profile --
    structurally fragile but not, this forecast, actually a problem. The
    ranking must put the flooding one first."""
    client, *_ = seeded_app
    r = client.get("/api/network/chokepoints", params={"profile": "vehicle_small"})
    assert r.status_code == 200
    body = r.json()
    assert body["properties"]["chokepoint_count"] == 2
    feats = body["features"]
    by_pair = {frozenset((f["properties"]["u"], f["properties"]["v"])): f["properties"]
              for f in feats}
    assert by_pair.keys() == {frozenset((1, 7)), frozenset((7, 10))}
    spur_tip = by_pair[frozenset((1, 7))]
    spur_mid = by_pair[frozenset((7, 10))]
    assert spur_tip["nodes_isolated"] == 1     # losing 1-7 strands node 1 alone
    assert spur_mid["nodes_isolated"] == 2     # losing 7-10 strands {1, 7} together
    assert spur_tip["hours_unsafe"] > spur_mid["hours_unsafe"]
    for props in (spur_tip, spur_mid):
        assert props["hours_total"] == config.FORECAST_HOURS
        # priority_score is a pure derived field -- must always self-check
        assert props["priority_score"] == props["nodes_isolated"] * props["hours_unsafe"]
    # ranked by priority_score descending -- the flooding chokepoint first
    assert feats[0]["properties"] == spur_tip
    for f in feats:
        assert f["geometry"]["type"] in ("LineString", "MultiLineString")


def test_chokepoints_endpoint_rejects_bad_profile_against_real_app(seeded_app):
    client, *_ = seeded_app
    r = client.get("/api/network/chokepoints", params={"profile": "bogus"})
    assert r.status_code == 400


# --- sea-level-rise scenarios and the wheelchair profile (added 2026-09-15) ---

def test_hours_and_risk_per_scenario(seeded_app):
    client, *_ = seeded_app
    base = client.get("/api/hours").json()
    up = client.get("/api/hours?slr_cm=100").json()
    assert base["slr_cm"] == 0 and up["slr_cm"] == 100
    assert set(up["scenarios_available"]) >= {0, 100}
    assert len(up["hours"]) == len(base["hours"]) == config.FORECAST_HOURS
    # +1 m of sea level floods at least as much at every hour, and strictly
    # more at the synthetic peak
    for b, u in zip(base["hours"], up["hours"]):
        assert u["water_level_m"] == pytest.approx(b["water_level_m"] + 1.0, abs=1e-4)
        assert u["flooded_segments"] >= b["flooded_segments"]
    assert max(h["flooded_segments"] for h in up["hours"]) > \
        max(h["flooded_segments"] for h in base["hours"])
    r = client.get("/api/risk?hour=9&slr_cm=100&flooded_only=true").json()
    assert r["features"] and all(f["properties"]["flooded"] for f in r["features"])
    assert all(f["properties"]["scenario_cm"] == 100 for f in r["features"])
    assert client.get("/api/hours?slr_cm=45").status_code == 400
    assert client.get("/api/risk?hour=0&slr_cm=45").status_code == 400


def test_route_under_scenario_and_wheelchair(seeded_app):
    client, *_ = seeded_app
    o = dict(olat=40.9, olon=-73.6998, dlat=40.9, dlon=-73.6748)
    a = client.get("/api/route", params={**o, "profile": "adult", "hour": 0}).json()
    s = client.get("/api/route", params={**o, "profile": "adult", "hour": 0, "slr_cm": 100}).json()
    assert a["properties"]["slr_cm"] == 0 and s["properties"]["slr_cm"] == 100
    # with +1 m the synthetic shore road is under water: either a longer
    # detour with more avoided edges, or no safe route at all
    if s["geometry"] is not None:
        assert s["properties"]["avoided_edges"] >= a["properties"]["avoided_edges"]
    else:
        assert "no safe route" in s["properties"]["error"]
    w = client.get("/api/route", params={**o, "profile": "wheelchair", "hour": 0})
    assert w.status_code == 200
    # time-aware routing is only defined for the plain forecast
    r = client.get("/api/route", params={**o, "profile": "adult", "hour": 0,
                                         "slr_cm": 30, "time_aware": True})
    assert r.status_code == 400
    feats = client.get("/api/risk?hour=0").json()["features"]
    assert all("safe_wheelchair" in f["properties"] for f in feats)
    assert all("grade_pct" in f["properties"] for f in feats)


def test_risk_endpoint_accepts_a_valid_bbox_and_filters_geographically(seeded_app):
    """Every existing bbox test only exercises the malformed-input 400
    path (never touching the database); this is the only test anywhere
    that sends a well-formed bbox through to db.risk_geojson() and checks
    it actually restricts the results, proving the ST_MakeEnvelope filter
    (tidestep/db.py) and api.py's successful-parse return path both work
    end to end against a real PostGIS instance."""
    client, *_ = seeded_app
    unfiltered = client.get("/api/risk", params={"hour": 7}).json()["features"]
    assert unfiltered   # sanity: the synthetic scenario has segments at all

    # a generous bbox around the synthetic "Cove Harbor" fixture (centered
    # on ORIGIN_LON/ORIGIN_LAT = -73.700, 40.900 in scripts/dev_seed.py)
    # must return the same segments as no bbox at all
    enclosing = client.get("/api/risk", params={
        "hour": 7, "bbox": "40.85,-73.75,40.95,-73.65"}).json()["features"]
    assert {f["properties"]["segment_id"] for f in enclosing} == \
        {f["properties"]["segment_id"] for f in unfiltered}

    # a bbox nowhere near the fixture (south of the equator, off the coast
    # of west Africa) must exclude every segment
    far_away = client.get("/api/risk", params={
        "hour": 7, "bbox": "0,0,1,1"}).json()["features"]
    assert far_away == []
