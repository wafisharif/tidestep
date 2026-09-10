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
