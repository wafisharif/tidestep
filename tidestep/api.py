"""Stage 5: FastAPI backend.

    uvicorn tidestep.api:app --reload

Endpoints
---------
GET  /api/config                        station, bbox, profiles, and safety thresholds -- static
                                         reference data a client fetches once on startup (no DB
                                         hit), so it can render labels/limits before the first
                                         forecast call lands
GET  /api/hours[?slr_cm=0|30|60|100]    list of forecast hours (index + valid_time + water level);
                                         slr_cm selects a sea-level-rise scenario
GET  /api/risk?hour=H[&bbox=s,w,n,e][&slr_cm=..]
                                         GeoJSON of segment hazard states at hour H
GET  /api/alerts                        active NWS alerts at the gauge (cached 10 min)
GET  /api/replay                        list of past days already replayed (cached)
GET  /api/replay/{YYYY-MM-DD}/hours     replay a past day on OBSERVED water levels
GET  /api/replay/{YYYY-MM-DD}/risk?hour=H
GET  /api/route?olat&olon&dlat&dlon&profile&hour[&time_aware=true]
                                         flood-avoiding route (GeoJSON Feature). time_aware=true
                                         checks hazard at each segment's own arrival hour instead
                                         of once at departure -- see tidestep/routing.py
                                         Router.route_time_aware.
GET  /api/route/advisory?olat&olon&dlat&dlon&profile
                                         hour-by-hour safe/unsafe forecast for this specific trip's
                                         usual path, for "when today is it safe to make this
                                         trip" trip planning (Router.route_advisory)
GET  /api/route/best_departure?olat&olon&dlat&dlon&profile
                                         hour-by-hour ACTUAL best route (not just safe/unsafe of
                                         one fixed path) plus the earliest hour a real route
                                         exists -- can find a safe detour at an hour
                                         /api/route/advisory would call unsafe because its usual
                                         path floods (Router.route_best_departure)
POST /api/route/multi_stop              flood-avoiding route through an ordered list of 2+
                                         waypoints (JSON body), where each leg's hazard check
                                         starts from the PREVIOUS leg's actual arrival hour, not
                                         hour 0 repeated for every leg (Router.route_multi_stop).
                                         optimize_order=true additionally searches for the best
                                         order to visit the intermediate stops in, instead of the
                                         order given (Router.route_multi_stop_optimized)
GET  /api/route/to_safety?olat&olon&profile&hour&prefer_shelters
                                         evacuation-style routing: nearest reachable point that
                                         stays flood-safe for the rest of the forecast window, no
                                         destination required. prefer_shelters (default true)
                                         prefers a real shelter building (school, hospital,
                                         fire/police station, community center) over an
                                         arbitrary dry street when that data is loaded
                                         (Router.route_to_safety, tidestep/shelters.py)
GET  /api/network/chokepoints?profile=adult
                                         network-wide resilience analysis: which street
                                         segments are structural single points of failure
                                         (no other path exists at all, not just a longer
                                         one) for `profile`'s usable network, ranked by how
                                         many nodes they'd isolate times how many forecast
                                         hours they're actually unsafe (tidestep/resilience.py)
GET  /api/routes                        saved routes
POST /api/routes                        save a route for alerting
DELETE /api/routes/{id}
GET  /                                  the Leaflet frontend (frontend/index.html)
"""
from __future__ import annotations

from pathlib import Path

import osmnx as ox
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text

from . import config, db, hazard, nws, replay, resilience, routing, streets

app = FastAPI(title="TideStep", version="0.1")
FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "index.html"
app.mount("/static", StaticFiles(directory=FRONTEND.parent / "vendor"), name="static")

_engine = None
_router = None


def engine():
    global _engine
    if _engine is None:
        _engine = db.get_engine()
    return _engine


def router() -> routing.Router:
    global _router
    if _router is None:
        G = ox.load_graphml(streets.GRAPH_PATH)
        _router = routing.Router(G, engine())
    return _router


@app.get("/")
def index():
    return FileResponse(FRONTEND)


@app.get("/api/config")
def get_config():
    return {"station": config.STATION_NAME, "station_id": config.STATION_ID,
            "bbox": config.BBOX, "profiles": list(hazard.PROFILES),
            "depth_limit_m": config.DEPTH_LIMIT_M,
            "wheelchair_max_grade_pct": config.WHEELCHAIR_MAX_GRADE_PCT,
            "flood_thresholds_m_navd88": config.FLOOD_THRESHOLDS_M_NAVD88,
            "slr_scenarios_cm": config.SLR_SCENARIOS_CM,
            "slr_scenario_labels": {str(k): v for k, v in config.SLR_SCENARIO_LABELS.items()}}


def _check_slr(slr_cm: int) -> int:
    if slr_cm not in config.SLR_SCENARIOS_CM:
        raise HTTPException(400, f"slr_cm must be one of {config.SLR_SCENARIOS_CM}")
    return slr_cm


@app.get("/api/hours")
def get_hours(slr_cm: int = 0):
    """Forecast hours with the per-hour flooded-segment count. ``slr_cm``
    selects a sea-level-rise scenario (0 = plain forecast); the water level
    reported already includes the offset."""
    _check_slr(slr_cm)
    sql = """SELECT forecast_hour, MIN(valid_time) AS valid_time,
                    MAX(water_level_m) AS water_level_m,
                    COUNT(*) FILTER (WHERE flooded) AS flooded_segments
             FROM hazard WHERE scenario_cm = :sc
             GROUP BY forecast_hour ORDER BY forecast_hour"""
    with engine().connect() as conn:
        rows = conn.execute(text(sql), {"sc": slr_cm}).all()
        run = conn.execute(text(
            "SELECT run_time, ofs_bias_m FROM forecast_runs ORDER BY run_id DESC LIMIT 1")).first()
        scenarios = db.scenarios_available(engine())
    if not rows and slr_cm:
        raise HTTPException(404, f"scenario +{slr_cm} cm is not loaded; "
                                 f"available: {scenarios}. Re-run build_hazard + load_db.")
    return {"run_time": run[0].isoformat() if run else None,
            "ofs_bias_m": run[1] if run else None,
            "slr_cm": slr_cm,
            "scenarios_available": scenarios,
            "hours": [{"hour": r[0], "valid_time": r[1].isoformat(),
                       "water_level_m": r[2], "flooded_segments": r[3]} for r in rows]}


MAX_HOUR = config.MAX_HOUR  # kept as a local alias; the actual value now lives in config.py


def _parse_bbox(bbox: str | None):
    if not bbox:
        return None
    try:
        box = tuple(float(x) for x in bbox.split(","))
        assert len(box) == 4
        return box
    except Exception:
        raise HTTPException(400, "bbox must be south,west,north,east")


@app.get("/api/risk")
def get_risk(hour: int = Query(0, ge=0, le=MAX_HOUR), bbox: str | None = None,
             slr_cm: int = 0, flooded_only: bool = False):
    _check_slr(slr_cm)
    box = _parse_bbox(bbox)      # validate before any database work
    return JSONResponse(db.risk_geojson(engine(), hour, box,
                                        scenario_cm=slr_cm, flooded_only=flooded_only))


@app.get("/api/route")
def get_route(olat: float, olon: float, dlat: float, dlon: float,
              profile: str = "adult", hour: int = Query(0, ge=0, le=MAX_HOUR),
              time_aware: bool = Query(
                  False, description="check hazard at each segment's own arrival hour "
                                     "(based on travel time) instead of once at departure"),
              slr_cm: int = Query(0, description="sea-level-rise scenario in cm; "
                                                 "only the plain (non time-aware) route "
                                                 "supports non-zero values")):
    if profile not in hazard.PROFILES:
        raise HTTPException(400, f"profile must be one of {hazard.PROFILES}")
    _check_slr(slr_cm)
    if time_aware and slr_cm:
        raise HTTPException(400, "time_aware routing is only available for slr_cm=0")
    if time_aware:
        res = router().route_time_aware((olat, olon), (dlat, dlon), profile, hour)
        if res is None:
            return JSONResponse({"type": "Feature", "geometry": None,
                                 "properties": {"error": "no safe route at this hour",
                                                "time_aware": True}},
                                status_code=200)
        return router().time_aware_route_geojson(res)
    res = router().route((olat, olon), (dlat, dlon), profile, hour, scenario_cm=slr_cm)
    if res is None:
        return JSONResponse({"type": "Feature", "geometry": None,
                             "properties": {"error": "no safe route at this hour",
                                            "slr_cm": slr_cm}},
                            status_code=200)
    out = router().route_geojson(res)
    out["properties"]["slr_cm"] = slr_cm
    return out


@app.get("/api/route/advisory")
def get_route_advisory(olat: float, olon: float, dlat: float, dlon: float,
                       profile: str = "adult"):
    """Hour-by-hour safe/unsafe forecast for this specific trip's usual
    (flood-blind) path, across the whole forecast window -- lets the
    frontend show "safe to leave now" vs. "wait until 6pm" for the exact
    trip a user is planning, not just a single hour's snapshot."""
    if profile not in hazard.PROFILES:
        raise HTTPException(400, f"profile must be one of {hazard.PROFILES}")
    adv = router().route_advisory((olat, olon), (dlat, dlon), profile, range(config.FORECAST_HOURS))
    return {
        "baseline_length_m": None if adv.baseline_length_m is None
        else round(adv.baseline_length_m, 1),
        "hours": [{"hour": h.hour, "safe": h.safe, "max_depth_cm": h.max_depth_cm}
                 for h in adv.hours],
    }


@app.get("/api/route/best_departure")
def get_route_best_departure(olat: float, olon: float, dlat: float, dlon: float,
                             profile: str = "adult"):
    """Per-hour ACTUAL best route across the forecast window (not just
    safe/unsafe of one fixed path -- see /api/route/advisory above), plus
    the earliest hour a real route exists at all. Lets the frontend say
    "you can still go now, just a bit longer" instead of only "wait until
    6pm", which /api/route/advisory would say whenever the *usual* path is
    what's flooded but a real detour exists."""
    if profile not in hazard.PROFILES:
        raise HTTPException(400, f"profile must be one of {hazard.PROFILES}")
    plan = router().route_best_departure((olat, olon), (dlat, dlon), profile,
                                         range(config.FORECAST_HOURS))
    return {
        "baseline_length_m": None if plan.baseline_length_m is None
        else round(plan.baseline_length_m, 1),
        "recommended_hour": plan.recommended_hour,
        "recommended_length_m": None if plan.recommended_length_m is None
        else round(plan.recommended_length_m, 1),
        "recommended_travel_time_min": plan.recommended_travel_time_min,
        "hours": [{"hour": h.hour, "safe": h.safe,
                   "length_m": None if h.length_m is None else round(h.length_m, 1),
                   "travel_time_min": h.travel_time_min,
                   "max_depth_cm_on_route": h.max_depth_cm_on_route}
                 for h in plan.hours],
    }


class Waypoint(BaseModel):
    lat: float
    lon: float


class MultiStopRequest(BaseModel):
    waypoints: list[Waypoint] = Field(
        ..., min_length=2, max_length=10,
        description="ordered stops: origin, any intermediate stops, final destination")
    profile: str = "adult"
    hour: int = Field(0, ge=0, le=config.MAX_HOUR, description="departure hour for the first leg")
    optimize_order: bool = Field(
        False, description="find the best order to visit the intermediate stops in "
                           "(origin and final destination stay fixed), instead of "
                           "visiting them in the order given -- capped at "
                           f"{routing.MAX_OPTIMIZE_STOPS} intermediate stops")


@app.post("/api/route/multi_stop")
def post_route_multi_stop(req: MultiStopRequest):
    """Flood-avoiding route through an ordered list of 2+ waypoints, where
    each leg is checked against the hazard state at the hour a traveler
    would actually START that leg -- the PREVIOUS leg's real arrival
    hour, not the trip's overall departure hour repeated for every leg
    independently (which would repeat, one level up, the exact mistake
    time-aware routing was built to fix for a single leg). See
    Router.route_multi_stop for the constructed case this matters for.

    ``optimize_order=true`` additionally searches for the best order to
    visit the intermediate stops in, rather than the order given -- see
    Router.route_multi_stop_optimized. The response gains ``order``
    (the waypoint-index visiting order actually used), ``optimized``,
    ``orders_tried``, and ``orders_complete`` properties in that case."""
    if req.profile not in hazard.PROFILES:
        raise HTTPException(400, f"profile must be one of {hazard.PROFILES}")
    pts = [(w.lat, w.lon) for w in req.waypoints]
    if req.optimize_order:
        # validated here, before router() (and therefore the database) is
        # ever touched -- same "reject a bad request before it can reach
        # the DB" contract every other endpoint in this file follows.
        # route_multi_stop_optimized() re-checks this same bound itself
        # (see its docstring / MAX_OPTIMIZE_STOPS), which is the actual
        # boundary this is defense in depth for.
        n_intermediate = len(req.waypoints) - 2
        if n_intermediate > routing.MAX_OPTIMIZE_STOPS:
            raise HTTPException(
                400, f"can only optimize up to {routing.MAX_OPTIMIZE_STOPS} "
                     f"intermediate stops ({n_intermediate} given)")
        try:
            opt = router().route_multi_stop_optimized(pts, req.profile, req.hour)
        except ValueError as e:
            raise HTTPException(400, str(e))
        gj = router().multi_stop_route_geojson(opt.plan)
        gj["properties"]["order"] = opt.order
        gj["properties"]["optimized"] = opt.optimized
        gj["properties"]["orders_tried"] = opt.orders_tried
        gj["properties"]["orders_complete"] = opt.orders_complete
        return gj
    plan = router().route_multi_stop(pts, req.profile, req.hour)
    return router().multi_stop_route_geojson(plan)


@app.get("/api/route/to_safety")
def get_route_to_safety(olat: float, olon: float, profile: str = "adult",
                        hour: int = Query(0, ge=0, le=config.MAX_HOUR),
                        prefer_shelters: bool = True):
    """Evacuation-style routing: given only a starting point (no
    destination), find the nearest reachable point that stays flood-safe
    for ``profile`` across the rest of the forecast window and route
    there. Every other routing endpoint needs a destination the traveler
    already has in mind; this answers "where can I go that's safe" for
    someone who doesn't (see Router.route_to_safety).

    ``prefer_shelters`` (default true, Stage 11): prefer a haven near a
    real shelter building (school, hospital, fire/police station,
    community center -- tidestep/shelters.py) over an arbitrary dry
    street, whenever that data has been loaded. Silently has no effect if
    it hasn't -- see Router._shelter_preferred_targets()."""
    if profile not in hazard.PROFILES:
        raise HTTPException(400, f"profile must be one of {hazard.PROFILES}")
    res = router().route_to_safety((olat, olon), profile, hour, prefer_shelters=prefer_shelters)
    if res is None:
        return JSONResponse({"type": "Feature", "geometry": None,
                             "properties": {"error": "no reachable safe haven found "
                                                      "for this profile in the forecast window"}},
                            status_code=200)
    return router().safe_haven_geojson(res)


@app.get("/api/network/chokepoints")
def get_chokepoints(profile: str = "adult"):
    """Network-wide resilience analysis, not a point-to-point route: which
    street segments are structural single points of failure for
    ``profile``'s usable street network (removing one disconnects the
    network -- no other path exists at all, however long), ranked by
    ``priority_score`` = nodes isolated if it's lost x how many of this
    forecast's hours it's actually unsafe (see tidestep/resilience.py for
    why this is a genuinely different question from anything the routing
    endpoints above answer, and why it needs its own bridge-finding
    algorithm rather than reusing Router's shortest-path machinery)."""
    if profile not in hazard.PROFILES:
        raise HTTPException(400, f"profile must be one of {hazard.PROFILES}")
    return resilience.chokepoints_geojson(router().G, engine(), profile)


class SavedRoute(BaseModel):
    label: str = Field("", max_length=80)
    contact: str = Field(..., max_length=120, description="email or phone")
    profile: str
    olat: float
    olon: float
    dlat: float
    dlon: float


@app.get("/api/routes")
def list_routes():
    return db.list_saved_routes(engine())


@app.post("/api/routes")
def save_route(r: SavedRoute):
    if r.profile not in hazard.PROFILES:
        raise HTTPException(400, "bad profile")
    rid = db.add_saved_route(engine(), r.label, r.contact, r.profile,
                             (r.olat, r.olon), (r.dlat, r.dlon))
    return {"route_id": rid}


@app.delete("/api/routes/{route_id}")
def delete_route(route_id: int):
    db.delete_saved_route(engine(), route_id)
    return {"deleted": route_id}


# --- live NWS alerts ---------------------------------------------------------

@app.get("/api/alerts")
def get_alerts():
    """Active NWS alerts for the gauge location; ``coastal_alerts`` is the
    subset relevant to coastal flooding, most serious first."""
    return nws.active_alerts()


# --- historical replay -------------------------------------------------------

_replay_segments_fc: dict | None = None


def _segments_fc() -> dict:
    global _replay_segments_fc
    if _replay_segments_fc is None:
        _replay_segments_fc = db.segments_geojson(engine())
    return _replay_segments_fc


def _replay_table(date: str):
    try:
        d = replay.parse_date(date)
    except ValueError as e:
        raise HTTPException(400, f"bad date: {e}")
    try:
        return replay.replay_day(d)
    except FileNotFoundError as e:
        raise HTTPException(503, f"replay needs data/ inputs on the server: {e}")
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/replay")
def list_replays():
    return {"cached_days": replay.cached_days(),
            "note": "first request for a new day recomputes the flood model (~1 min)"}


@app.get("/api/replay/{date}/hours")
def replay_hours(date: str):
    table = _replay_table(date)
    return {"date": date, "replay": True, "hours": replay.hours_summary(table)}


@app.get("/api/replay/{date}/risk")
def replay_risk(date: str, hour: int = Query(0, ge=0, le=23), bbox: str | None = None,
                flooded_only: bool = False):
    box = _parse_bbox(bbox)
    table = _replay_table(date)
    return JSONResponse(replay.risk_features(table, hour, _segments_fc(),
                                             bbox=box, flooded_only=flooded_only))
