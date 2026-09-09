"""Stage 5: FastAPI backend.

    uvicorn tidestep.api:app --reload

Endpoints
---------
GET  /api/hours                         list of forecast hours (index + valid_time + water level)
GET  /api/risk?hour=H[&bbox=s,w,n,e]    GeoJSON of segment hazard states at hour H
GET  /api/route?olat&olon&dlat&dlon&profile&hour[&time_aware=true]
                                         flood-avoiding route (GeoJSON Feature). time_aware=true
                                         checks hazard at each segment's own arrival hour instead
                                         of once at departure -- see tidestep/routing.py
                                         Router.route_time_aware.
GET  /api/route/advisory?olat&olon&dlat&dlon&profile
                                         hour-by-hour safe/unsafe forecast for this specific trip's
                                         usual path, for "when today is it safe to make this
                                         trip" trip planning (Router.route_advisory)
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

from . import config, db, hazard, routing, streets

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
            "flood_thresholds_m_navd88": config.FLOOD_THRESHOLDS_M_NAVD88}


@app.get("/api/hours")
def get_hours():
    sql = """SELECT forecast_hour, MIN(valid_time) AS valid_time,
                    MAX(water_level_m) AS water_level_m,
                    COUNT(*) FILTER (WHERE flooded) AS flooded_segments
             FROM hazard GROUP BY forecast_hour ORDER BY forecast_hour"""
    with engine().connect() as conn:
        rows = conn.execute(text(sql)).all()
        run = conn.execute(text(
            "SELECT run_time, ofs_bias_m FROM forecast_runs ORDER BY run_id DESC LIMIT 1")).first()
    return {"run_time": run[0].isoformat() if run else None,
            "ofs_bias_m": run[1] if run else None,
            "hours": [{"hour": r[0], "valid_time": r[1].isoformat(),
                       "water_level_m": r[2], "flooded_segments": r[3]} for r in rows]}


MAX_HOUR = config.MAX_HOUR  # kept as a local alias; the actual value now lives in config.py


@app.get("/api/risk")
def get_risk(hour: int = Query(0, ge=0, le=MAX_HOUR), bbox: str | None = None):
    box = None
    if bbox:
        try:
            box = tuple(float(x) for x in bbox.split(","))
            assert len(box) == 4
        except Exception:
            raise HTTPException(400, "bbox must be south,west,north,east")
    return JSONResponse(db.risk_geojson(engine(), hour, box))


@app.get("/api/route")
def get_route(olat: float, olon: float, dlat: float, dlon: float,
              profile: str = "adult", hour: int = Query(0, ge=0, le=MAX_HOUR),
              time_aware: bool = Query(
                  False, description="check hazard at each segment's own arrival hour "
                                     "(based on travel time) instead of once at departure")):
    if profile not in hazard.PROFILES:
        raise HTTPException(400, f"profile must be one of {hazard.PROFILES}")
    if time_aware:
        res = router().route_time_aware((olat, olon), (dlat, dlon), profile, hour)
        if res is None:
            return JSONResponse({"type": "Feature", "geometry": None,
                                 "properties": {"error": "no safe route at this hour",
                                                "time_aware": True}},
                                status_code=200)
        return router().time_aware_route_geojson(res)
    res = router().route((olat, olon), (dlat, dlon), profile, hour)
    if res is None:
        return JSONResponse({"type": "Feature", "geometry": None,
                             "properties": {"error": "no safe route at this hour"}},
                            status_code=200)
    return router().route_geojson(res)


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
