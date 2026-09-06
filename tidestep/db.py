"""Stage 4: PostGIS storage.

Tables
------
segments      one row per road segment; geom is a LineString (EPSG:4326)
hazard        one row per (segment_id, valid_time) for the current run
forecast_runs one row per hourly recompute (run_time, bias, hours)
saved_routes  user-saved origin/destination/profile for alerting (Stage 8)

Connection string comes from ``DATABASE_URL`` (default matches
docker-compose.yml).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import geopandas as gpd
import pandas as pd
from sqlalchemy import create_engine, text

DEFAULT_URL = "postgresql+psycopg://tidestep:tidestep@localhost:5432/tidestep"


def get_engine(url: str | None = None):
    return create_engine(url or os.environ.get("DATABASE_URL", DEFAULT_URL), future=True)


SCHEMA = """
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS segments (
    segment_id  INTEGER PRIMARY KEY,
    u           BIGINT, v BIGINT, key INTEGER, seg_idx INTEGER,
    name        TEXT, highway TEXT,
    length_m    REAL, ground_m REAL,
    near_inlet  BOOLEAN NOT NULL DEFAULT FALSE,
    geom        geometry(LineString, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS segments_geom_idx ON segments USING GIST (geom);
CREATE INDEX IF NOT EXISTS segments_uv_idx ON segments (u, v, key);

CREATE TABLE IF NOT EXISTS forecast_runs (
    run_id      SERIAL PRIMARY KEY,
    run_time    TIMESTAMPTZ NOT NULL,
    ofs_bias_m  REAL,
    hours       INTEGER
);

CREATE TABLE IF NOT EXISTS hazard (
    segment_id          INTEGER NOT NULL REFERENCES segments(segment_id),
    forecast_hour       INTEGER NOT NULL,
    valid_time          TIMESTAMPTZ NOT NULL,
    water_level_m       REAL,
    depth_cm            INTEGER NOT NULL,
    flooded             BOOLEAN NOT NULL,
    safe_child          BOOLEAN NOT NULL,
    safe_adult          BOOLEAN NOT NULL,
    safe_vehicle_small  BOOLEAN NOT NULL,
    safe_vehicle_large  BOOLEAN NOT NULL,
    safe_vehicle_4wd    BOOLEAN NOT NULL,
    PRIMARY KEY (segment_id, valid_time)
);
CREATE INDEX IF NOT EXISTS hazard_hour_idx ON hazard (forecast_hour);

CREATE TABLE IF NOT EXISTS saved_routes (
    route_id    SERIAL PRIMARY KEY,
    label       TEXT,
    contact     TEXT NOT NULL,
    profile     TEXT NOT NULL,
    origin      geometry(Point, 4326) NOT NULL,
    destination geometry(Point, 4326) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    last_checked TIMESTAMPTZ
);
"""


def init_schema(engine) -> None:
    with engine.begin() as conn:
        for stmt in SCHEMA.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))


def load_segments(engine, segments: gpd.GeoDataFrame) -> int:
    """Replace the segments table with ``segments`` (from build_segments)."""
    cols = ["segment_id", "u", "v", "key", "seg_idx", "name", "highway",
            "length_m", "ground_m", "near_inlet", "geometry"]
    gdf = segments[[c for c in cols if c in segments.columns]].copy()
    if "near_inlet" not in gdf:
        gdf["near_inlet"] = False
    gdf = gdf.rename(columns={"geometry": "geom"}).set_geometry("geom")
    gdf = gdf.set_crs(4326, allow_override=True)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM hazard"))
        conn.execute(text("DELETE FROM segments"))
    gdf.to_postgis("segments", engine, if_exists="append", index=False)
    return len(gdf)


def load_hazard(engine, table: pd.DataFrame, ofs_bias_m: float = 0.0) -> int:
    """Replace the hazard table with a new run."""
    run_time = datetime.now(timezone.utc)
    cols = ["segment_id", "forecast_hour", "valid_time", "water_level_m",
            "depth_cm", "flooded", "safe_child", "safe_adult",
            "safe_vehicle_small", "safe_vehicle_large", "safe_vehicle_4wd"]
    df = table[cols].copy()
    df["valid_time"] = pd.to_datetime(df["valid_time"], utc=True)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM hazard"))
        conn.execute(text(
            "INSERT INTO forecast_runs (run_time, ofs_bias_m, hours) VALUES (:t, :b, :h)"),
            {"t": run_time, "b": float(ofs_bias_m), "h": int(df.forecast_hour.max()) + 1})
    df.to_sql("hazard", engine, if_exists="append", index=False, chunksize=5000, method="multi")
    return len(df)


def valid_times(engine) -> list[datetime]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT DISTINCT valid_time FROM hazard ORDER BY valid_time")).all()
    return [r[0] for r in rows]


def risk_geojson(engine, forecast_hour: int, bbox=None) -> dict:
    """GeoJSON FeatureCollection of every segment with its hazard state at
    ``forecast_hour``. ``bbox`` = (south, west, north, east) optional."""
    where = "WHERE h.forecast_hour = :h"
    params = {"h": forecast_hour}
    if bbox:
        where += " AND s.geom && ST_MakeEnvelope(:w, :s, :e, :n, 4326)"
        params.update(s=bbox[0], w=bbox[1], n=bbox[2], e=bbox[3])
    sql = f"""
    SELECT json_build_object(
      'type','FeatureCollection',
      'features', COALESCE(json_agg(json_build_object(
        'type','Feature',
        'geometry', ST_AsGeoJSON(s.geom, 6)::json,
        'properties', json_build_object(
          'segment_id', s.segment_id, 'name', s.name, 'highway', s.highway,
          'ground_m', s.ground_m, 'near_inlet', s.near_inlet,
          'valid_time', h.valid_time, 'water_level_m', h.water_level_m,
          'depth_cm', h.depth_cm, 'flooded', h.flooded,
          'safe_child', h.safe_child, 'safe_adult', h.safe_adult,
          'safe_vehicle_small', h.safe_vehicle_small,
          'safe_vehicle_large', h.safe_vehicle_large,
          'safe_vehicle_4wd', h.safe_vehicle_4wd))), '[]'::json))
    FROM segments s JOIN hazard h USING (segment_id) {where}
    """
    with engine.connect() as conn:
        row = conn.execute(text(sql), params).scalar_one()
    return row if isinstance(row, dict) else json.loads(row)


def unsafe_edges(engine, forecast_hour: int, profile: str) -> set[tuple]:
    """(u, v, key) of every graph edge that has at least one unsafe segment
    for ``profile`` at ``forecast_hour``. Used by the router."""
    col = f"safe_{profile}"
    sql = f"""
    SELECT DISTINCT s.u, s.v, s.key FROM segments s JOIN hazard h USING (segment_id)
    WHERE h.forecast_hour = :h AND NOT h.{col}
    """
    with engine.connect() as conn:
        return {tuple(r) for r in conn.execute(text(sql), {"h": forecast_hour}).all()}


def edge_hazard(engine, forecast_hour: int) -> pd.DataFrame:
    """Per-edge max depth and min safety at an hour (for route annotation)."""
    sql = """
    SELECT s.u, s.v, s.key, MAX(h.depth_cm) AS depth_cm,
           BOOL_AND(h.safe_child) AS safe_child, BOOL_AND(h.safe_adult) AS safe_adult,
           BOOL_AND(h.safe_vehicle_small) AS safe_vehicle_small,
           BOOL_AND(h.safe_vehicle_large) AS safe_vehicle_large,
           BOOL_AND(h.safe_vehicle_4wd) AS safe_vehicle_4wd
    FROM segments s JOIN hazard h USING (segment_id)
    WHERE h.forecast_hour = :h GROUP BY s.u, s.v, s.key
    """
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params={"h": forecast_hour})


# --- saved routes (Stage 8) ---------------------------------------------------

def add_saved_route(engine, label, contact, profile, origin, destination) -> int:
    sql = """
    INSERT INTO saved_routes (label, contact, profile, origin, destination)
    VALUES (:l, :c, :p, ST_SetSRID(ST_MakePoint(:olon, :olat), 4326),
            ST_SetSRID(ST_MakePoint(:dlon, :dlat), 4326))
    RETURNING route_id
    """
    with engine.begin() as conn:
        return conn.execute(text(sql), {
            "l": label, "c": contact, "p": profile,
            "olon": origin[1], "olat": origin[0],
            "dlon": destination[1], "dlat": destination[0]}).scalar_one()


def list_saved_routes(engine) -> list[dict]:
    sql = """
    SELECT route_id, label, contact, profile, created_at, last_blocked, last_checked,
           ST_Y(origin) AS olat, ST_X(origin) AS olon,
           ST_Y(destination) AS dlat, ST_X(destination) AS dlon
    FROM saved_routes ORDER BY route_id
    """
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql)).all()]


def update_route_state(engine, route_id: int, blocked: bool) -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE saved_routes SET last_blocked=:b, last_checked=now() WHERE route_id=:r"),
            {"b": blocked, "r": route_id})


def delete_saved_route(engine, route_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM saved_routes WHERE route_id=:r"), {"r": route_id})
