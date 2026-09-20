"""Stage 5 FastAPI input-validation paths that never touch the database, so
these run in plain ``pytest -q tests`` with no services up at all.

test_integration.py already covers the happy paths end to end against a
real DB; this file covers the request-validation edge cases that reject a
bad request *before* api.py ever calls engine()/router() — malformed bbox,
out-of-range hour, unknown profile, missing required params — plus the
DB-free /api/config endpoint. Confirming these never reach the database is
itself part of what each test checks (a monkeypatched engine() that raises
would fail the test if the validation guard didn't short-circuit first).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from tidestep import api, config, hazard  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    """A TestClient wired to an engine()/router() that both raise if
    called, so any test that accidentally reaches the database fails
    loudly instead of hanging on a real connection attempt."""
    def boom():
        raise AssertionError("this request should never reach the database")
    monkeypatch.setattr(api, "engine", boom)
    monkeypatch.setattr(api, "router", boom)
    return TestClient(api.app)


def test_config_endpoint_needs_no_database(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body["station_id"] == config.STATION_ID
    assert body["profiles"] == list(hazard.PROFILES)
    assert body["depth_limit_m"]["child"] == config.DEPTH_LIMIT_M["child"]
    assert tuple(body["bbox"]) == config.BBOX


def test_risk_rejects_hour_above_forecast_window(client):
    r = client.get(f"/api/risk?hour={config.FORECAST_HOURS}")   # one past MAX_HOUR
    assert r.status_code == 422


def test_risk_rejects_negative_hour(client):
    r = client.get("/api/risk?hour=-1")
    assert r.status_code == 422


def test_risk_accepts_hour_at_the_upper_bound_without_422(client, monkeypatch):
    # MAX_HOUR itself must be valid (off-by-one check on the Query bound);
    # let it through validation and only then hit our "no DB" guard.
    with pytest.raises(AssertionError):
        client.get(f"/api/risk?hour={config.FORECAST_HOURS - 1}")


def test_risk_rejects_malformed_bbox_before_touching_the_database(client):
    r = client.get("/api/risk?hour=0&bbox=not,a,valid,bbox")
    assert r.status_code == 400


def test_risk_rejects_bbox_with_wrong_number_of_fields(client):
    r = client.get("/api/risk?hour=0&bbox=40.8,-73.7,40.81")   # only 3 values
    assert r.status_code == 400


def test_route_rejects_unknown_profile_before_touching_the_database(client):
    r = client.get("/api/route", params=dict(
        olat=40.80, olon=-73.71, dlat=40.81, dlon=-73.70, profile="dog", hour=0))
    assert r.status_code == 400
    assert "profile" in r.json()["detail"]


def test_route_requires_all_coordinates(client):
    r = client.get("/api/route", params=dict(olat=40.80, olon=-73.71, profile="adult"))
    assert r.status_code == 422   # dlat/dlon missing


def test_save_route_rejects_unknown_profile_before_touching_the_database(client):
    r = client.post("/api/routes", json={
        "label": "test", "contact": "a@example.com", "profile": "dog",
        "olat": 40.80, "olon": -73.71, "dlat": 40.81, "dlon": -73.70})
    assert r.status_code == 400


def test_save_route_requires_a_contact(client):
    r = client.post("/api/routes", json={
        "label": "test", "profile": "adult",
        "olat": 40.80, "olon": -73.71, "dlat": 40.81, "dlon": -73.70})
    assert r.status_code == 422


def test_route_time_aware_still_rejects_unknown_profile(client):
    """The time_aware flag must not skip the same validation the plain
    route does -- it's an extra parameter on the same endpoint, not a
    separate code path that could accidentally forget the checks."""
    r = client.get("/api/route", params=dict(
        olat=40.80, olon=-73.71, dlat=40.81, dlon=-73.70,
        profile="dog", hour=0, time_aware=True))
    assert r.status_code == 400
    assert "profile" in r.json()["detail"]


def test_route_time_aware_rejects_hour_above_forecast_window(client):
    r = client.get("/api/route", params=dict(
        olat=40.80, olon=-73.71, dlat=40.81, dlon=-73.70,
        profile="adult", hour=config.FORECAST_HOURS, time_aware=True))
    assert r.status_code == 422


def test_route_time_aware_valid_request_reaches_the_router(client):
    """A valid time_aware request passes every validation check and
    reaches router() (which then raises via the no-DB fixture) -- proving
    the flag is actually wired to route_time_aware(), not silently
    ignored."""
    with pytest.raises(AssertionError):
        client.get("/api/route", params=dict(
            olat=40.80, olon=-73.71, dlat=40.81, dlon=-73.70,
            profile="adult", hour=0, time_aware=True))


def test_route_advisory_rejects_unknown_profile_before_touching_the_database(client):
    r = client.get("/api/route/advisory", params=dict(
        olat=40.80, olon=-73.71, dlat=40.81, dlon=-73.70, profile="dog"))
    assert r.status_code == 400
    assert "profile" in r.json()["detail"]


def test_route_advisory_requires_all_coordinates(client):
    r = client.get("/api/route/advisory", params=dict(olat=40.80, olon=-73.71, profile="adult"))
    assert r.status_code == 422   # dlat/dlon missing


def test_route_advisory_valid_request_reaches_the_router(client):
    with pytest.raises(AssertionError):
        client.get("/api/route/advisory", params=dict(
            olat=40.80, olon=-73.71, dlat=40.81, dlon=-73.70, profile="adult"))


def test_route_best_departure_rejects_unknown_profile_before_touching_the_database(client):
    r = client.get("/api/route/best_departure", params=dict(
        olat=40.80, olon=-73.71, dlat=40.81, dlon=-73.70, profile="dog"))
    assert r.status_code == 400
    assert "profile" in r.json()["detail"]


def test_route_best_departure_requires_all_coordinates(client):
    r = client.get("/api/route/best_departure", params=dict(olat=40.80, olon=-73.71, profile="adult"))
    assert r.status_code == 422   # dlat/dlon missing


def test_route_best_departure_valid_request_reaches_the_router(client):
    with pytest.raises(AssertionError):
        client.get("/api/route/best_departure", params=dict(
            olat=40.80, olon=-73.71, dlat=40.81, dlon=-73.70, profile="adult"))


def test_multi_stop_rejects_unknown_profile_before_touching_the_database(client):
    r = client.post("/api/route/multi_stop", json={
        "waypoints": [{"lat": 40.80, "lon": -73.71}, {"lat": 40.81, "lon": -73.70}],
        "profile": "dog"})
    assert r.status_code == 400
    assert "profile" in r.json()["detail"]


def test_multi_stop_rejects_fewer_than_two_waypoints(client):
    r = client.post("/api/route/multi_stop", json={
        "waypoints": [{"lat": 40.80, "lon": -73.71}], "profile": "adult"})
    assert r.status_code == 422


def test_multi_stop_rejects_hour_above_forecast_window(client):
    r = client.post("/api/route/multi_stop", json={
        "waypoints": [{"lat": 40.80, "lon": -73.71}, {"lat": 40.81, "lon": -73.70}],
        "profile": "adult", "hour": config.FORECAST_HOURS})
    assert r.status_code == 422


def test_multi_stop_valid_request_reaches_the_router(client):
    with pytest.raises(AssertionError):
        client.post("/api/route/multi_stop", json={
            "waypoints": [{"lat": 40.80, "lon": -73.71}, {"lat": 40.81, "lon": -73.70},
                         {"lat": 40.82, "lon": -73.69}],
            "profile": "adult"})


def test_route_to_safety_rejects_unknown_profile_before_touching_the_database(client):
    r = client.get("/api/route/to_safety", params=dict(olat=40.80, olon=-73.71, profile="dog"))
    assert r.status_code == 400
    assert "profile" in r.json()["detail"]


def test_route_to_safety_requires_coordinates(client):
    r = client.get("/api/route/to_safety", params=dict(olat=40.80, profile="adult"))
    assert r.status_code == 422   # olon missing


def test_route_to_safety_rejects_hour_above_forecast_window(client):
    r = client.get("/api/route/to_safety", params=dict(
        olat=40.80, olon=-73.71, profile="adult", hour=config.FORECAST_HOURS))
    assert r.status_code == 422


def test_route_to_safety_valid_request_reaches_the_router(client):
    with pytest.raises(AssertionError):
        client.get("/api/route/to_safety", params=dict(olat=40.80, olon=-73.71, profile="adult"))


def test_route_to_safety_accepts_prefer_shelters_query_param(client):
    """Stage 11's prefer_shelters flag must be accepted (default true, and
    an explicit false) and still reach the router -- FastAPI query-param
    parsing failing here would show up as a 422 instead of our "no DB"
    AssertionError guard."""
    with pytest.raises(AssertionError):
        client.get("/api/route/to_safety", params=dict(
            olat=40.80, olon=-73.71, profile="adult", prefer_shelters=False))


def test_multi_stop_optimize_order_rejects_unknown_profile_before_touching_the_database(client):
    r = client.post("/api/route/multi_stop", json={
        "waypoints": [{"lat": 40.80, "lon": -73.71}, {"lat": 40.81, "lon": -73.70}],
        "profile": "dog", "optimize_order": True})
    assert r.status_code == 400
    assert "profile" in r.json()["detail"]


def test_multi_stop_optimize_order_rejects_too_many_intermediate_stops(client):
    """MAX_OPTIMIZE_STOPS (6) intermediate stops is the brute-force cap;
    this request has 7 (9 waypoints total) and must be rejected with a
    clear 400, not silently truncated or left to hang."""
    waypoints = [{"lat": 40.80 + i * 0.001, "lon": -73.71} for i in range(9)]
    r = client.post("/api/route/multi_stop", json={
        "waypoints": waypoints, "profile": "adult", "optimize_order": True})
    assert r.status_code == 400
    assert "intermediate stops" in r.json()["detail"]


def test_multi_stop_optimize_order_valid_request_reaches_the_router(client):
    with pytest.raises(AssertionError):
        client.post("/api/route/multi_stop", json={
            "waypoints": [{"lat": 40.80, "lon": -73.71}, {"lat": 40.81, "lon": -73.70},
                         {"lat": 40.82, "lon": -73.69}],
            "profile": "adult", "optimize_order": True})


def test_multi_stop_without_optimize_order_is_unaffected(client):
    """optimize_order defaults False -- a plain multi_stop request must
    still take the original, unoptimized code path (and therefore still
    hit the same no-DB guard the same way as before this feature existed)."""
    with pytest.raises(AssertionError):
        client.post("/api/route/multi_stop", json={
            "waypoints": [{"lat": 40.80, "lon": -73.71}, {"lat": 40.81, "lon": -73.70}],
            "profile": "adult"})


# --- GET /api/network/chokepoints -----------------------------------------
# Every other endpoint above has both a "rejects unknown profile before
# touching the database" test and a "valid request reaches the router"
# test -- this endpoint (added for the network-wide resilience analysis
# feature) had neither, found by re-checking test_api.py against api.py
# endpoint-by-endpoint. resilience.find_chokepoints() itself already has
# its own profile-validation test (test_resilience.py), but that doesn't
# prove api.py's OWN pre-check (the one that turns a bad profile into a
# clean 400 instead of resilience.py's ValueError leaking out as a 500)
# is actually wired up and reached first, which is exactly what every
# sibling endpoint's test in this file exists to prove for its own route.

def test_chokepoints_rejects_unknown_profile_before_touching_the_database(client):
    r = client.get("/api/network/chokepoints", params={"profile": "dog"})
    assert r.status_code == 400
    assert "profile" in r.json()["detail"]


def test_chokepoints_valid_request_reaches_the_router(client):
    """A valid profile passes api.py's own validation and reaches
    router() (which then raises via the no-DB fixture) -- proving the
    endpoint's profile check doesn't accidentally reject every request,
    and that it really does call through to router()/engine() rather
    than, say, silently returning an empty result."""
    with pytest.raises(AssertionError):
        client.get("/api/network/chokepoints", params={"profile": "adult"})


def test_chokepoints_defaults_to_adult_profile(client):
    """profile is optional (default "adult") -- confirm the default is
    itself a valid profile that reaches the router, not accidentally
    something hazard.PROFILES would reject if it were ever passed
    explicitly (would show up as an unexpected 400 instead of the no-DB
    AssertionError)."""
    with pytest.raises(AssertionError):
        client.get("/api/network/chokepoints")


# --- GET / (frontend) --------------------------------------------------------
# Never tested anywhere: not DB-dependent (it's a static FileResponse) so it
# belongs in this file, not test_integration.py.

def test_root_serves_the_frontend_without_touching_the_database(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "TideStep" in r.text


# --- engine() / router() singleton caching -----------------------------------
# test_integration.py's seeded_app fixture bypasses these two functions
# entirely (it assigns api._engine / api._router directly so it can point
# them at a synthetic scenario), so nothing anywhere else actually proves
# the lazy-singleton behavior these functions are written for: build the
# expensive engine/graph/router once, then reuse it on every later request.
# These monkeypatch api's module-level globals directly instead of using
# the `client` fixture (which replaces engine()/router() themselves).

def test_engine_is_built_once_and_cached_across_calls(monkeypatch):
    monkeypatch.setattr(api, "_engine", None)
    calls = []
    sentinel = object()

    def fake_get_engine():
        calls.append(1)
        return sentinel

    monkeypatch.setattr(api.db, "get_engine", fake_get_engine)
    first = api.engine()
    second = api.engine()
    assert first is sentinel
    assert second is sentinel
    assert len(calls) == 1, "engine() must not call db.get_engine() more than once"


def test_router_is_built_once_and_cached_across_calls(monkeypatch):
    monkeypatch.setattr(api, "_router", None)
    # give engine() a cheap cached value so router() doesn't also try to
    # build a real database engine as a side effect of this test
    monkeypatch.setattr(api, "_engine", "fake-engine-sentinel")
    load_calls = []
    ctor_calls = []
    fake_graph = object()
    fake_router = object()

    def fake_load_graphml(path):
        load_calls.append(path)
        return fake_graph

    def fake_router_ctor(G, eng):
        ctor_calls.append((G, eng))
        return fake_router

    monkeypatch.setattr(api.ox, "load_graphml", fake_load_graphml)
    monkeypatch.setattr(api.routing, "Router", fake_router_ctor)
    first = api.router()
    second = api.router()
    assert first is fake_router
    assert second is fake_router
    assert len(load_calls) == 1, "router() must not reload the graph more than once"
    assert len(ctor_calls) == 1, "router() must not rebuild the Router more than once"
    assert ctor_calls[0] == (fake_graph, "fake-engine-sentinel")


# --- GET /api/alerts ----------------------------------------------------------
# nws.active_alerts() has its own thorough unit tests (test_new_features.py);
# this only needs to prove the endpoint is wired up to it and returns its
# payload verbatim, without ever touching engine()/router() (a live NWS
# alert must show up even if the DB or street graph is unavailable).

def test_alerts_endpoint_returns_the_nws_payload_verbatim(client, monkeypatch):
    fake_payload = {"alerts": [{"id": "NWS-1", "event": "Coastal Flood Advisory"}],
                    "coastal_alerts": [{"id": "NWS-1", "event": "Coastal Flood Advisory"}],
                    "error": None}
    monkeypatch.setattr(api.nws, "active_alerts", lambda: fake_payload)
    r = client.get("/api/alerts")
    assert r.status_code == 200
    assert r.json() == fake_payload


# --- "no result" JSON-200 branches -------------------------------------------
# /api/route, /api/route (time_aware), and /api/route/to_safety all return
# HTTP 200 with geometry: null and an explanatory properties.error, rather
# than a 404, when the router legitimately finds no safe result -- this is
# deliberate (it's not an error, it's a true forecast answer: "nothing is
# safe at this hour") but nothing exercises the branch, since test_api.py's
# no-DB fixture never lets a request reach a router() call that returns,
# and test_integration.py's synthetic scenario has no reason to hit it.

def test_route_returns_200_with_null_geometry_when_no_safe_route_exists(client, monkeypatch):
    class FakeRouter:
        def route(self, o, d, profile, hour, scenario_cm=0):
            assert scenario_cm == 0
            return None
    monkeypatch.setattr(api, "router", lambda: FakeRouter())
    r = client.get("/api/route", params={"olat": 40.80, "olon": -73.71,
                                         "dlat": 40.81, "dlon": -73.70,
                                         "profile": "adult", "hour": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "Feature"
    assert body["geometry"] is None
    assert body["properties"]["error"] == "no safe route at this hour"
    assert body["properties"]["slr_cm"] == 0


def test_route_time_aware_returns_200_with_null_geometry_when_no_safe_route_exists(client, monkeypatch):
    class FakeRouter:
        def route_time_aware(self, o, d, profile, hour):
            return None
    monkeypatch.setattr(api, "router", lambda: FakeRouter())
    r = client.get("/api/route", params={"olat": 40.80, "olon": -73.71,
                                         "dlat": 40.81, "dlon": -73.70,
                                         "profile": "adult", "time_aware": True})
    assert r.status_code == 200
    body = r.json()
    assert body["geometry"] is None
    assert body["properties"]["error"] == "no safe route at this hour"
    assert body["properties"]["time_aware"] is True


def test_route_to_safety_returns_200_with_null_geometry_when_no_haven_found(client, monkeypatch):
    class FakeRouter:
        def route_to_safety(self, o, profile, hour, prefer_shelters=True):
            assert prefer_shelters is True
            return None
    monkeypatch.setattr(api, "router", lambda: FakeRouter())
    r = client.get("/api/route/to_safety",
                   params={"olat": 40.80, "olon": -73.71, "profile": "adult"})
    assert r.status_code == 200
    body = r.json()
    assert body["geometry"] is None
    assert "no reachable safe haven" in body["properties"]["error"]


def test_multi_stop_optimize_order_returns_400_when_router_raises_value_error(client, monkeypatch):
    """route_multi_stop_optimized() itself raises ValueError for a
    genuinely-infeasible request (as opposed to the too-many-stops case,
    which api.py rejects before ever calling it) -- api.py must translate
    that into a clean 400, not let it escape as a 500."""
    class FakeRouter:
        def route_multi_stop_optimized(self, pts, profile, hour):
            raise ValueError("no feasible visiting order found")
    monkeypatch.setattr(api, "router", lambda: FakeRouter())
    r = client.post("/api/route/multi_stop", json={
        "waypoints": [{"lat": 40.80, "lon": -73.71}, {"lat": 40.805, "lon": -73.705},
                      {"lat": 40.81, "lon": -73.70}],
        "profile": "adult", "optimize_order": True})
    assert r.status_code == 400
    assert "no feasible visiting order found" in r.json()["detail"]


# --- GET /api/replay* ---------------------------------------------------------
# The entire replay endpoint family is untested anywhere: test_integration.py
# never seeds replay data, and this file's no-DB fixture previously stopped
# short of these routes. replay.py's own functions (parse_date, replay_day,
# hours_summary, risk_features, cached_days) already have thorough unit
# tests in test_new_features.py, so these only need to prove api.py wires
# them together and maps their errors to the right HTTP status.

def test_replay_list_returns_cached_days_without_touching_the_database(client, monkeypatch):
    monkeypatch.setattr(api.replay, "cached_days", lambda: ["2023-12-23", "2024-01-13"])
    r = client.get("/api/replay")
    assert r.status_code == 200
    body = r.json()
    assert body["cached_days"] == ["2023-12-23", "2024-01-13"]
    assert "note" in body


def test_replay_hours_rejects_a_malformed_date_before_touching_replay_day(client, monkeypatch):
    def boom_replay_day(*a, **k):
        raise AssertionError("replay_day should never be reached for a bad date")
    monkeypatch.setattr(api.replay, "replay_day", boom_replay_day)
    r = client.get("/api/replay/not-a-date/hours")
    assert r.status_code == 400
    assert "bad date" in r.json()["detail"]


def test_replay_hours_returns_503_when_server_data_files_are_missing(client, monkeypatch):
    """replay_day() raises FileNotFoundError when data/dem_1m.tif or
    data/segments.gpkg aren't present on the server -- that's a server
    deployment/config problem, not a bad request, so it must map to 503
    (service unavailable), not a generic 500 or a client-error 4xx."""
    def fake_replay_day(d, water_levels=None):
        raise FileNotFoundError("data/dem_1m.tif not found")
    monkeypatch.setattr(api.replay, "replay_day", fake_replay_day)
    r = client.get("/api/replay/2024-01-13/hours")
    assert r.status_code == 503
    assert "data/dem_1m.tif" in r.json()["detail"]


def test_replay_hours_returns_404_when_no_observed_data_for_that_day(client, monkeypatch):
    def fake_replay_day(d, water_levels=None):
        raise ValueError("no observed water levels for this date")
    monkeypatch.setattr(api.replay, "replay_day", fake_replay_day)
    r = client.get("/api/replay/2024-01-13/hours")
    assert r.status_code == 404
    assert "no observed water levels" in r.json()["detail"]


def test_replay_hours_valid_date_returns_hours_summary(client, monkeypatch):
    fake_table = "fake-dataframe-sentinel"
    fake_summary = [{"hour": 0, "flooded_segments": 3}, {"hour": 1, "flooded_segments": 5}]

    def fake_replay_day(d, water_levels=None):
        assert d.year == 2024 and d.month == 1 and d.day == 13
        return fake_table

    def fake_hours_summary(table):
        assert table == fake_table
        return fake_summary

    monkeypatch.setattr(api.replay, "replay_day", fake_replay_day)
    monkeypatch.setattr(api.replay, "hours_summary", fake_hours_summary)
    r = client.get("/api/replay/2024-01-13/hours")
    assert r.status_code == 200
    body = r.json()
    assert body["date"] == "2024-01-13"
    assert body["replay"] is True
    assert body["hours"] == fake_summary


def test_replay_risk_rejects_malformed_bbox_before_replaying(client, monkeypatch):
    """bbox validation must happen before replay_day() is ever called --
    same "reject before doing expensive/DB work" contract as every other
    endpoint in this file."""
    def boom_replay_day(*a, **k):
        raise AssertionError("replay_day should never be reached for a bad bbox")
    monkeypatch.setattr(api.replay, "replay_day", boom_replay_day)
    r = client.get("/api/replay/2024-01-13/risk", params={"bbox": "not,valid"})
    assert r.status_code == 400


def test_replay_risk_valid_request_returns_risk_features(client, monkeypatch):
    monkeypatch.setattr(api, "_replay_segments_fc", None)
    fake_table = "fake-dataframe-sentinel"
    fake_fc = {"type": "FeatureCollection", "features": []}
    fake_result = {"type": "FeatureCollection", "features": [{"id": "seg-1"}]}

    def fake_replay_day(d, water_levels=None):
        return fake_table

    def fake_segments_geojson(eng):
        return fake_fc

    def fake_risk_features(table, hour, segments_fc, bbox=None, flooded_only=False):
        assert table == fake_table
        assert hour == 7
        assert segments_fc == fake_fc
        assert bbox is None
        assert flooded_only is False
        return fake_result

    monkeypatch.setattr(api, "engine", lambda: "fake-engine-sentinel")
    monkeypatch.setattr(api.replay, "replay_day", fake_replay_day)
    monkeypatch.setattr(api.db, "segments_geojson", fake_segments_geojson)
    monkeypatch.setattr(api.replay, "risk_features", fake_risk_features)
    r = client.get("/api/replay/2024-01-13/risk", params={"hour": 7})
    assert r.status_code == 200
    assert r.json() == fake_result


# --- GET /api/hours: recognized-but-not-yet-built scenario -------------------
# slr_cm=30 is a real entry in config.SLR_SCENARIOS_CM (so it passes
# _check_slr's membership check), but nothing guarantees build_hazard +
# load_db have actually been run for every scenario on a given server --
# this "recognized, just not built yet" case must 404 with a message
# naming what IS available, not silently return an empty hours list. The
# scripts/dev_seed.py fixture used by test_integration.py always loads
# every scenario in config.SLR_SCENARIOS_CM at once, so this branch can
# never be reached through the real DB there; it's exercised here instead
# with a minimal fake engine standing in for a partially-built server.

class _FakeConnection:
    """Just enough of a SQLAlchemy Connection to drive api.get_hours():
    inspects the compiled SQL text to decide which canned result to hand
    back, the same way a real PostGIS connection would answer each of the
    three distinct queries that function issues."""
    def __init__(self, loaded_scenarios):
        self._loaded = set(loaded_scenarios)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "DISTINCT scenario_cm" in sql:
            rows = [(s,) for s in sorted(self._loaded)]
        elif "forecast_runs" in sql:
            rows = []   # no run recorded -- fine, get_hours tolerates this
        elif "FROM hazard WHERE" in sql:
            sc = params["sc"]
            rows = [] if sc not in self._loaded else \
                [(0, datetime(2024, 1, 13, tzinfo=timezone.utc), 0.5, 3)]
        else:
            raise AssertionError(f"unexpected SQL in fake connection: {sql}")

        class _Result:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

            def first(self):
                return self._rows[0] if self._rows else None
        return _Result(rows)


class _FakeEngine:
    def __init__(self, loaded_scenarios):
        self._loaded = loaded_scenarios

    def connect(self):
        return _FakeConnection(self._loaded)


def test_hours_404s_for_a_recognized_but_unbuilt_scenario(client, monkeypatch):
    monkeypatch.setattr(api, "engine", lambda: _FakeEngine(loaded_scenarios=[0, 100]))
    r = client.get("/api/hours", params={"slr_cm": 30})
    assert r.status_code == 404
    body = r.json()["detail"]
    assert "+30 cm" in body
    assert "0" in body and "100" in body   # names what IS available


def test_hours_succeeds_for_a_loaded_non_zero_scenario(client, monkeypatch):
    """Same fake engine, but slr_cm=100 IS in the loaded set -- confirms
    the 404 branch's guard (`if not rows and slr_cm`) doesn't misfire for
    every non-zero scenario, only ones with genuinely no rows."""
    monkeypatch.setattr(api, "engine", lambda: _FakeEngine(loaded_scenarios=[0, 100]))
    r = client.get("/api/hours", params={"slr_cm": 100})
    assert r.status_code == 200
    assert r.json()["slr_cm"] == 100
