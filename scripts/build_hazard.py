"""Stages 2-3: segments + connected flood-fill + hazard table.

Needs data/ from scripts/fetch_all.py. Writes:
  data/segments.gpkg   one row per ~15 m road segment with ground_m
  data/hazard.csv      one row per (segment, forecast hour)
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import geopandas as gpd
import pandas as pd

from tidestep import dem as demmod, floodfill, hazard, segments, streets  # noqa: E402

DATA = demmod.DATA_DIR


def main():
    t0 = time.time()
    dem_path = DATA / "dem_1m.tif"
    dem, transform, meta = demmod.load_dem(dem_path)
    print(f"DEM {dem.shape}, {meta['crs']}")

    G = streets.fetch_graph()
    water = gpd.read_file(streets.WATER_PATH) if streets.WATER_PATH.exists() else None
    if water is None:
        print("no data/water.gpkg yet: seeding from DEM only, near_inlet all False")
    edges = streets.edges_gdf(G)
    print(f"{len(edges)} edges")

    seg_path = DATA / "segments.gpkg"
    if seg_path.exists():
        segs = gpd.read_file(seg_path)
    else:
        segs = segments.build_segments(edges, dem_path)
        segs["near_inlet"] = hazard.flag_near_inlet(segs, water)
        segs.to_file(seg_path, driver="GPKG")
    n_off = segs["ground_m"].isna().sum()
    print(f"{len(segs)} segments, {n_off} off-DEM, {segs['near_inlet'].sum()} near inlet "
          f"({time.time()-t0:.0f}s)")

    seeds = floodfill.build_seed_mask(dem, transform, water, meta["crs"])
    print(f"seed pixels: {seeds.sum()}")

    wl = pd.read_csv(DATA / "water_levels.csv", index_col=0, parse_dates=True)["ofs_navd88_m"]
    table = hazard.hazard_table(segs, dem, seeds, wl)
    table.to_csv(DATA / "hazard.csv", index=False)
    summary = table.groupby("valid_time").agg(
        wl_m=("water_level_m", "first"), flooded=("flooded", "sum"),
        unsafe_child=("safe_child", lambda s: (~s).sum()),
        unsafe_car=("safe_vehicle_small", lambda s: (~s).sum()))
    print(summary.to_string())
    print(f"done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
