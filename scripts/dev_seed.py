"""Synthetic end-to-end fixture: a small, clearly-fake "Cove Harbor" scenario
that exercises every stage of the real pipeline (segments -> floodfill ->
hazard -> PostGIS -> API -> router) without needing network access to NOAA,
USGS, or Overpass.

Two uses:
1. Integration testing (tests/test_integration.py imports build_scenario()
   and loads it into a real PostGIS instance).
2. An offline demo fallback: if live data fetch is ever flaky right before
   a demo, `python scripts/dev_seed.py` gives you a fully working app
   (map, slider, routing) to show, clearly labelled as synthetic data.

Geography (all synthetic, in WGS84, centered near but not on the real
Kings Point bbox so it's obviously a fixture): a small peninsula with open
water on the west side, one low-lying shore road that floods first, a
finger cove that creates a near-inlet segment, and a cut-off inland basin
that must NOT flood (the same connectivity check as the unit tests, at
integration scale).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import geopandas as gpd
import networkx as nx
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, Polygon

from tidestep import db, floodfill, hazard, segments, streets  # noqa: E402

GRID = 300                      # 300 x 300 m tile, 1 m/px
ORIGIN_LON, ORIGIN_LAT = -73.700, 40.900   # nowhere near the real bbox, on purpose
DEG_PER_M_LAT = 1 / 111_320
DEG_PER_M_LON = 1 / (111_320 * np.cos(np.radians(ORIGIN_LAT)))


def build_dem() -> tuple[np.ndarray, "rasterio.Affine"]:
    dem = np.zeros((GRID, GRID), dtype="float32")
    x = np.arange(GRID)
    # elevation rises west->east from the water
    for row in range(GRID):
        dem[row, :] = np.clip((x - 20) * 0.025, -0.2, 4.0)
    # a finger cove (models a culvert/inlet) cutting inland around row 150
    dem[145:155, 20:90] = np.clip((x[20:90] - 20) * 0.010, -0.2, 1.0)
    # a cut-off inland basin near row 220 — below tide but walled off by a
    # ridge, must never flood (this is the connectivity regression check)
    dem[210:230, 150:170] = 0.3
    dem[205:235, 145:148] = 2.5   # ridge on the water-facing side
    dem[:, :15] = np.nan          # open water (nodata, like real 3DEP)
    transform = from_origin(ORIGIN_LON, ORIGIN_LAT, DEG_PER_M_LON, DEG_PER_M_LAT)
    return dem, transform


def build_water_gdf() -> gpd.GeoDataFrame:
    def ll(col, row):
        return (ORIGIN_LON + col * DEG_PER_M_LON, ORIGIN_LAT - row * DEG_PER_M_LAT)
    bay = Polygon([ll(0, 0), ll(15, 0), ll(15, GRID), ll(0, GRID)])
    inlet = LineString([ll(20, 150), ll(90, 150)])   # the cove, tagged waterway
    return gpd.GeoDataFrame(
        {"osmid": ["synbay", "syninlet"], "natural": ["water", None],
         "waterway": [None, "stream"], "tidal": ["yes", "yes"]},
        geometry=[bay, inlet], crs=4326)


def build_graph() -> nx.MultiDiGraph:
    """A small street grid: Shore Rd (row 50, low) along the water, three
    cross streets running inland, and Cove Rd crossing the inlet at row 150
    (so it should end up flagged near_inlet)."""
    def ll(col, row):
        return (ORIGIN_LON + col * DEG_PER_M_LON, ORIGIN_LAT - row * DEG_PER_M_LAT)

    G = nx.MultiDiGraph(crs="epsg:4326")
    nodes = {
        1: (18, 50), 2: (280, 50),      # Shore Rd, west->east
        3: (18, 150), 4: (280, 150),    # Cove Rd, crosses the inlet
        5: (18, 220), 6: (280, 220),    # Basin Rd, inland, near cut-off basin
        7: (60, 50), 8: (60, 150), 9: (60, 220),     # cross street A
        10: (200, 50), 11: (200, 150), 12: (200, 220),  # cross street B
    }
    for n, (c, r) in nodes.items():
        lon, lat = ll(c, r)
        G.add_node(n, x=lon, y=lat)

    def add(u, v, hw="residential"):
        lon1, lat1 = G.nodes[u]["x"], G.nodes[u]["y"]
        lon2, lat2 = G.nodes[v]["x"], G.nodes[v]["y"]
        L = Point(lon1, lat1).distance(Point(lon2, lat2)) * 111_320
        for a, b in ((u, v), (v, u)):
            G.add_edge(a, b, key=0, length=L, highway=hw, name="synthetic")

    add(1, 7); add(7, 10); add(10, 2)          # Shore Rd
    add(3, 8); add(8, 11); add(11, 4)          # Cove Rd
    add(5, 9); add(9, 12); add(12, 6)          # Basin Rd
    add(1, 3, "footway"); add(3, 5)            # cross A (west)
    add(7, 8, "footway"); add(8, 9)            # cross A-mid
    add(10, 11); add(11, 12)                   # cross B
    add(2, 4); add(4, 6)                       # cross C (east)
    return G


def synthetic_tide(hours: int = 24) -> pd.Series:
    """A plausible 24 h semidiurnal-ish tide: two highs, peak at hour 9
    reaching 1.0 m NAVD88 (floods Shore Rd + Cove Rd but not Basin Rd)."""
    t = np.arange(hours)
    wl = 0.35 + 0.65 * np.sin(2 * np.pi * (t - 3) / 12.42) ** 2 * np.exp(-((t - 9) ** 2) / 60)
    wl = 0.2 + 0.6 * (0.5 - 0.5 * np.cos(2 * np.pi * t / 12.42)) \
        + 0.35 * np.exp(-((t - 9) ** 2) / 8)   # extra surge bump at hour 9
    idx = pd.date_range("2026-09-07", periods=hours, freq="h", tz="UTC")
    return pd.Series(wl, index=idx, name="wl_navd88_m")


def edges_gdf_from_graph(G: nx.MultiDiGraph) -> gpd.GeoDataFrame:
    rows = []
    for u, v, k, d in G.edges(keys=True, data=True):
        geom = LineString([(G.nodes[u]["x"], G.nodes[u]["y"]),
                           (G.nodes[v]["x"], G.nodes[v]["y"])])
        rows.append({"u": u, "v": v, "key": k, "highway": d["highway"],
                    "name": d.get("name"), "length": d["length"], "geometry": geom})
    return gpd.GeoDataFrame(rows, crs=4326)


def build_scenario():
    """Returns (dem, transform, segs_gdf, seeds, water_levels, G)."""
    dem, transform = build_dem()
    water = build_water_gdf()
    G = build_graph()
    edges = edges_gdf_from_graph(G)

    import rasterio
    tmp_dem = Path("/tmp/dev_seed_dem.tif")
    with rasterio.open(tmp_dem, "w", driver="GTiff", height=GRID, width=GRID,
                       count=1, dtype="float32", crs="EPSG:4326",
                       transform=transform, nodata=np.nan) as dst:
        dst.write(dem, 1)

    segs = segments.build_segments(edges, tmp_dem)
    segs["near_inlet"] = hazard.flag_near_inlet(segs, water)

    seeds = floodfill.build_seed_mask(dem, transform, water, "EPSG:4326")
    wl = synthetic_tide()
    table = hazard.hazard_table(segs, dem, seeds, wl)
    return dem, transform, segs, seeds, wl, table, G


def write_demo_graph(G: nx.MultiDiGraph, graph_path: Path | None = None) -> Path:
    """Write ``G`` to ``graph_path`` (default ``streets.GRAPH_PATH``) so
    ``tidestep/api.py``'s ``router()`` — which always loads that hardcoded
    path, with no way to know a demo is running — picks up the synthetic
    graph automatically. Without this, a fresh clone has no graph file at
    all (``/api/route`` 500s), and a machine with real fetched data already
    present would silently route against real Long Island coordinates while
    segments/hazard came from the synthetic scenario centered miles away.

    If a real graph is already at that path, it is backed up once to
    ``<path>.real-backup`` (never overwritten by a repeat run, so this can't
    destroy the one real copy) rather than silently deleted.
    """
    import osmnx as ox
    graph_path = graph_path or streets.GRAPH_PATH
    graph_path.parent.mkdir(parents=True, exist_ok=True)

    real_backup = graph_path.with_suffix(".graphml.real-backup")
    if graph_path.exists() and not real_backup.exists():
        graph_path.rename(real_backup)
        print(f"backed up existing {graph_path} -> {real_backup} "
              f"(restore it after the demo with a plain file rename)")

    # must round-trip through osmnx's own save/load (not plain
    # networkx.write_graphml) — osmnx's loader expects the string-typed
    # attribute encoding ox.save_graphml produces, and chokes on native
    # GraphML numeric types from a plain nx.write_graphml file.
    ox.save_graphml(G, graph_path)
    print(f"wrote synthetic street graph -> {graph_path} "
          f"(this is what tidestep/api.py will load for routing)")
    return graph_path


def main():
    dem, transform, segs, seeds, wl, table, G = build_scenario()
    print(f"segments: {len(segs)}, near_inlet: {segs.near_inlet.sum()}")
    print(f"seed pixels: {seeds.sum()} / {seeds.size}")
    print(wl.round(2).to_string())
    summary = table.groupby("valid_time").agg(
        wl_m=("water_level_m", "first"), flooded=("flooded", "sum"),
        unsafe_child=("safe_child", lambda s: (~s).sum()),
        unsafe_car=("safe_vehicle_small", lambda s: (~s).sum()))
    print(summary.to_string())

    write_demo_graph(G)

    engine = db.get_engine()
    db.init_schema(engine)
    db.load_segments(engine, segs)
    db.load_hazard(engine, table, ofs_bias_m=0.0)
    print(f"loaded into PostGIS: {len(segs)} segments, {len(table)} hazard rows")
    print("hours in DB:", len(db.valid_times(engine)))


if __name__ == "__main__":
    main()
