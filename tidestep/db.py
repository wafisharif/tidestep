"""Stage 4: PostGIS storage.

Tables
------
segments      one row per road segment; geom is a LineString (EPSG:4326)
hazard        one row per (scenario_cm, segment_id, valid_time) for the current
              run; scenario_cm = 0 is the plain forecast, 30/60/100 are the
              sea-level-rise scenarios from config.SLR_SCENARIOS_CM
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

from . import config

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
    grade_pct   REAL,
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
    scenario_cm         INTEGER NOT NULL DEFAULT 0,
    segment_id          INTEGER NOT NULL REFERENCES segments(segment_id),
    forecast_hour       INTEGER NOT NULL,
    valid_time          TIMESTAMPTZ NOT NULL,
    water_level_m       REAL,
    depth_cm            INTEGER NOT NULL,
    flooded             BOOLEAN NOT NULL,
    safe_child          BOOLEAN NOT NULL,
    safe_adult          BOOLEAN NOT NULL,
    safe_wheelchair     BOOLEAN NOT NULL DEFAULT TRUE,
    safe_vehicle_small  BOOLEAN NOT NULL,
    safe_vehicle_large  BOOLEAN NOT NULL,
    safe_vehicle_4wd    BOOLEAN NOT NULL,
    PRIMARY KEY (scenario_cm, segment_id, valid_time)
);

CREATE TABLE IF NOT EXISTS shelters (
    shelter_id  SERIAL PRIMARY KEY,
    osmid       TEXT,
    name        TEXT,
    kind        TEXT NOT NULL,
    geom        geometry(Point, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS shelters_geom_idx ON shelters USING GIST (geom);

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


# Idempotent upgrades for databases created before a column existed. Each
# runs every init_schema() call and is a no-op once applied. The hazard
# primary key is rebuilt only if it does not yet include scenario_cm.
MIGRATIONS = """
ALTER TABLE segments ADD COLUMN IF NOT EXISTS grade_pct REAL;
ALTER TABLE hazard ADD COLUMN IF NOT EXISTS scenario_cm INTEGER NOT NULL DEFAULT 0;
ALTER TABLE hazard ADD COLUMN IF NOT EXISTS safe_wheelchair BOOLEAN NOT NULL DEFAULT TRUE;
DROP INDEX IF EXISTS hazard_hour_idx;
CREATE INDEX IF NOT EXISTS hazard_scen_hour_idx ON hazard (scenario_cm, forecast_hour);
CREATE INDEX IF NOT EXISTS hazard_segment_idx ON hazard (segment_id);
"""

_HAZARD_PK_HAS_SCENARIO = """
SELECT COUNT(*) FROM information_schema.key_column_usage
WHERE table_name = 'hazard' AND constraint_name = 'hazard_pkey'
  AND column_name = 'scenario_cm'
"""


def init_schema(engine) -> None:
    with engine.begin() as conn:
        for stmt in SCHEMA.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        for stmt in MIGRATIONS.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        if conn.execute(text(_HAZARD_PK_HAS_SCENARIO)).scalar_one() == 0:
            conn.execute(text("ALTER TABLE hazard DROP CONSTRAINT IF EXISTS hazard_pkey"))
            conn.execute(text(
                "ALTER TABLE hazard ADD PRIMARY KEY (scenario_cm, segment_id, valid_time)"))


def load_segments(engine, segments: gpd.GeoDataFrame) -> int:
    """Replace the segments table with ``segments`` (from build_segments)."""
    cols = ["segment_id", "u", "v", "key", "seg_idx", "name", "highway",
            "length_m", "ground_m", "near_inlet", "grade_pct", "geometry"]
    gdf = segments[[c for c in cols if c in segments.columns]].copy()
    if "near_inlet" not in gdf:
        gdf["near_inlet"] = False
    if "grade_pct" not in gdf:
        gdf["grade_pct"] = None
    gdf = gdf.rename(columns={"geometry": "geom"}).set_geometry("geom")
    gdf = gdf.set_crs(4326, allow_override=True)
    with engine.begin() as conn:
        # TRUNCATE, not DELETE: a row-by-row DELETE of 55k segments has to
        # check the hazard foreign key for each row, and with millions of
        # just-deleted hazard tuples not yet vacuumed that took >10 minutes.
        # Truncating both tables together satisfies the FK and frees the
        # space immediately.
        conn.execute(text("TRUNCATE hazard, segments"))
    gdf.to_postgis("segments", engine, if_exists="append", index=False)
    return len(gdf)


def load_hazard(engine, table: pd.DataFrame, ofs_bias_m: float = 0.0) -> int:
    """Replace the hazard table with a new run (all scenarios at once)."""
    run_time = datetime.now(timezone.utc)
    table = table.copy()
    if "scenario_cm" not in table:
        table["scenario_cm"] = 0
    if "safe_wheelchair" not in table:
        table["safe_wheelchair"] = True
    cols = ["scenario_cm", "segment_id", "forecast_hour", "valid_time", "water_level_m",
            "depth_cm", "flooded", "safe_child", "safe_adult", "safe_wheelchair",
            "safe_vehicle_small", "safe_vehicle_large", "safe_vehicle_4wd"]
    df = table[cols].copy()
    df["valid_time"] = pd.to_datetime(df["valid_time"], utc=True)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE hazard"))
        conn.execute(text(
            "INSERT INTO forecast_runs (run_time, ofs_bias_m, hours) VALUES (:t, :b, :h)"),
            {"t": run_time, "b": float(ofs_bias_m), "h": int(df.forecast_hour.max()) + 1})
    df["scenario_cm"] = df["scenario_cm"].astype(int)
    _bulk_insert(engine, "hazard", df)
    return len(df)


def _bulk_insert(engine, table: str, df: pd.DataFrame) -> None:
    """COPY-based insert (psycopg 3) with a to_sql fallback. With four SLR
    scenarios the hazard table is ~5.5 M rows for the full study area;
    COPY loads that in well under a minute where executemany takes many."""
    cols = list(df.columns)
    try:
        raw = engine.raw_connection()
        try:
            cur = raw.cursor()
            with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as copy:
                for row in df.itertuples(index=False, name=None):
                    copy.write_row(row)
            raw.commit()
        finally:
            raw.close()
    except AttributeError:   # driver without .copy (not psycopg 3)
        df.to_sql(table, engine, if_exists="append", index=False, chunksize=5000, method="multi")


def valid_times(engine) -> list[datetime]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT DISTINCT valid_time FROM hazard WHERE scenario_cm = 0 "
            "ORDER BY valid_time")).all()
    return [r[0] for r in rows]


def scenarios_available(engine) -> list[int]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT DISTINCT scenario_cm FROM hazard ORDER BY scenario_cm")).all()
    return [int(r[0]) for r in rows]


def segments_geojson(engine) -> dict:
    """Static segment geometry + attributes (no hazard), for callers that
    compute hazard themselves (historical replay, validation)."""
    sql = """
    SELECT json_build_object(
      'type','FeatureCollection',
      'features', COALESCE(json_agg(json_build_object(
        'type','Feature',
        'geometry', ST_AsGeoJSON(s.geom, 5)::json,
        'properties', json_build_object(
          'segment_id', s.segment_id, 'name', s.name, 'highway', s.highway,
          'ground_m', s.ground_m, 'near_inlet', s.near_inlet,
          'grade_pct', s.grade_pct))), '[]'::json))
    FROM segments s
    """
    with engine.connect() as conn:
        row = conn.execute(text(sql)).scalar_one()
    return row if isinstance(row, dict) else json.loads(row)


def risk_geojson(engine, forecast_hour: int, bbox=None, scenario_cm: int = 0,
                 flooded_only: bool = False) -> dict:
    """GeoJSON FeatureCollection of segments with their hazard state at
    ``forecast_hour`` under ``scenario_cm`` (0 = plain forecast).
    ``bbox`` = (south, west, north, east) optional. ``flooded_only`` drops
    dry segments: the full study area is ~55k segments (~28 MB), the
    flooded subset a few hundred, so the map fetches the flooded set for the
    whole area and the full set only for the visible viewport."""
    where = "WHERE h.forecast_hour = :h AND h.scenario_cm = :sc"
    params = {"h": forecast_hour, "sc": int(scenario_cm)}
    if flooded_only:
        where += " AND h.flooded"
    if bbox:
        where += " AND s.geom && ST_MakeEnvelope(:w, :s, :e, :n, 4326)"
        params.update(s=bbox[0], w=bbox[1], n=bbox[2], e=bbox[3])
    sql = f"""
    SELECT json_build_object(
      'type','FeatureCollection',
      'features', COALESCE(json_agg(json_build_object(
        'type','Feature',
        'geometry', ST_AsGeoJSON(s.geom, 5)::json,
        'properties', json_build_object(
          'segment_id', s.segment_id, 'name', s.name, 'highway', s.highway,
          'ground_m', s.ground_m, 'near_inlet', s.near_inlet,
          'grade_pct', s.grade_pct, 'scenario_cm', h.scenario_cm,
          'valid_time', h.valid_time, 'water_level_m', h.water_level_m,
          'depth_cm', h.depth_cm, 'flooded', h.flooded,
          'safe_child', h.safe_child, 'safe_adult', h.safe_adult,
          'safe_wheelchair', h.safe_wheelchair,
          'safe_vehicle_small', h.safe_vehicle_small,
          'safe_vehicle_large', h.safe_vehicle_large,
          'safe_vehicle_4wd', h.safe_vehicle_4wd))), '[]'::json))
    FROM segments s JOIN hazard h USING (segment_id) {where}
    """
    with engine.connect() as conn:
        row = conn.execute(text(sql), params).scalar_one()
    return row if isinstance(row, dict) else json.loads(row)


def unsafe_edges(engine, forecast_hour: int, profile: str,
                 scenario_cm: int = 0) -> set[tuple]:
    """(u, v, key) of every graph edge that has at least one unsafe segment
    for ``profile`` at ``forecast_hour``. Used by the router.

    ``profile`` is checked against the fixed set of known profiles before
    being interpolated into the column name below: the API layer already
    validates it against ``hazard.PROFILES``, but this is the actual SQL
    boundary, so it re-checks rather than trusting the caller.
    """
    if profile not in config.DEPTH_LIMIT_M:
        raise ValueError(f"unknown profile {profile!r}")
    col = f"safe_{profile}"
    sql = f"""
    SELECT DISTINCT s.u, s.v, s.key FROM segments s JOIN hazard h USING (segment_id)
    WHERE h.forecast_hour = :h AND h.scenario_cm = :sc AND NOT h.{col}
    """
    with engine.connect() as conn:
        return {tuple(r) for r in conn.execute(
            text(sql), {"h": forecast_hour, "sc": int(scenario_cm)}).all()}


def always_safe_nodes(engine, hours, profile: str) -> set[int]:
    """Every graph node that is an endpoint of at least one segment which
    stays safe for ``profile`` across EVERY hour in ``hours`` -- candidate
    "safe haven" points for Router.route_to_safety(): a node qualifies if
    standing on (or reaching) that one segment keeps a traveler safe for
    the rest of the modeled window, not just this instant.

    Requires an actual hazard row for every hour in ``hours`` per segment
    (``COUNT(*) = :n``), not just BOOL_AND over whatever rows happen to
    exist -- a segment missing a row for one of the requested hours must
    not be vacuously treated as "safe" that hour just because every row
    that *does* exist happens to be safe.

    Same profile-name validation as unsafe_edges(): the API layer already
    checks ``profile`` against the known set, but this is the actual SQL
    boundary, so it re-checks before interpolating it into a column name.
    """
    if profile not in config.DEPTH_LIMIT_M:
        raise ValueError(f"unknown profile {profile!r}")
    col = f"safe_{profile}"
    hour_list = list(hours)
    if not hour_list:
        return set()
    sql = f"""
    SELECT s.u, s.v FROM segments s
    WHERE s.segment_id IN (
        SELECT h.segment_id FROM hazard h
        WHERE h.forecast_hour = ANY(:hours) AND h.scenario_cm = 0
        GROUP BY h.segment_id
        HAVING COUNT(*) = :n AND BOOL_AND(h.{col})
    )
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"hours": hour_list, "n": len(hour_list)}).all()
    nodes: set[int] = set()
    for u, v in rows:
        nodes.add(u)
        nodes.add(v)
    return nodes


def edge_hazard(engine, forecast_hour: int, scenario_cm: int = 0) -> pd.DataFrame:
    """Per-edge max depth and min safety at an hour (for route annotation)."""
    sql = """
    SELECT s.u, s.v, s.key, MAX(h.depth_cm) AS depth_cm,
           BOOL_AND(h.safe_child) AS safe_child, BOOL_AND(h.safe_adult) AS safe_adult,
           BOOL_AND(h.safe_wheelchair) AS safe_wheelchair,
           BOOL_AND(h.safe_vehicle_small) AS safe_vehicle_small,
           BOOL_AND(h.safe_vehicle_large) AS safe_vehicle_large,
           BOOL_AND(h.safe_vehicle_4wd) AS safe_vehicle_4wd
    FROM segments s JOIN hazard h USING (segment_id)
    WHERE h.forecast_hour = :h AND h.scenario_cm = :sc GROUP BY s.u, s.v, s.key
    """
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params={"h": forecast_hour, "sc": int(scenario_cm)})


# --- shelters (Stage 11) -------------------------------------------------------
# Real candidate shelter buildings from tidestep/shelters.py's OSM fetch.
# This is an optional preference layer for Router.route_to_safety(), not a
# required table: every function below is written so a DB from before this
# feature (no shelters table at all) or one where the fetch was simply never
# run (empty table) degrades to "no shelter data available" rather than
# raising, so route_to_safety() falls all the way back to its original
# "any dry street" behavior. See routing.py's _shelter_preferred_targets().

def load_shelters(engine, shelters: gpd.GeoDataFrame) -> int:
    """Replace the shelters table with ``shelters`` (from
    shelters.fetch_shelters). Safe to never call at all -- an app that
    never loads shelter data just keeps the pre-Stage-11 behavior."""
    cols = ["osmid", "name", "kind", "geometry"]
    gdf = shelters[[c for c in cols if c in shelters.columns]].copy()
    gdf = gdf.rename(columns={"geometry": "geom"}).set_geometry("geom")
    gdf = gdf.set_crs(4326, allow_override=True)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM shelters"))
    gdf.to_postgis("shelters", engine, if_exists="append", index=False)
    return len(gdf)


def shelter_points(engine) -> list[dict]:
    """Every loaded shelter as ``{shelter_id, name, kind, lat, lon}`` --
    used by Router.route_to_safety() to snap shelters onto street-graph
    nodes in-process (ox.nearest_nodes), the same way an origin/destination
    point is snapped. Returns ``[]`` (never raises) if the shelters table
    doesn't exist yet -- e.g. a DB created before this feature -- or is
    simply empty, since this is an optional preference layer, not a
    required one."""
    sql = "SELECT shelter_id, name, kind, ST_Y(geom) AS lat, ST_X(geom) AS lon FROM shelters"
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(sql)).all()
    except Exception:
        return []
    return [dict(r._mapping) for r in rows]


def nearest_shelter(engine, lat: float, lon: float, max_m: float) -> dict | None:
    """Nearest loaded shelter within ``max_m`` meters of (lat, lon), or
    None -- used only to annotate a route_to_safety() result with a
    human-readable name/kind (e.g. "Kings Point Fire Dept" rather than a
    bare coordinate). Never raises; a missing/empty shelters table just
    means no annotation is added.

    ``::geography`` casts give an accurate meter-based distance regardless
    of latitude -- the table's geometry column is stored in EPSG:4326
    (degrees), which ST_Distance/ST_DWithin would otherwise treat as a
    flat-degree unit, exactly the accurate-proximity concern already
    documented for hazard.py's near-inlet buffering."""
    sql = """
    SELECT name, kind,
           ST_Distance(geom::geography,
                       ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography) AS distance_m
    FROM shelters
    WHERE ST_DWithin(geom::geography,
                     ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :max_m)
    ORDER BY distance_m ASC
    LIMIT 1
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(text(sql), {"lat": lat, "lon": lon, "max_m": max_m}).first()
    except Exception:
        return None
    return dict(row._mapping) if row else None


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
