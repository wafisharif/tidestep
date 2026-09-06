"""Synthetic-terrain tests for Stages 2-3. No network."""
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
import geopandas as gpd
from shapely.geometry import LineString

from tidestep import floodfill, hazard, segments


def make_dem(tmp_path):
    """100 x 100 m grid at 1 m. Water (nodata) on the left 10 columns, a
    gentle beach rising to 3 m, a 2 m ridge at column 60, and an inland
    basin at column 70-80 that sits at 0.2 m (below tide, but cut off)."""
    dem = np.zeros((100, 100), dtype="float32")
    x = np.arange(100)
    dem[:] = np.clip((x - 10) * 0.03, 0, 3.0)   # beach: 0 m at col 10 -> 2.7 m at col 100
    dem[:, 60:62] = 2.5                          # ridge (never overtopped in test)
    dem[:, 70:80] = 0.2                          # cut-off basin
    dem[:, :10] = -9999                          # open water nodata
    path = tmp_path / "dem.tif"
    # WGS84-ish transform: put the grid at ~40.8N, 1 m ~ 9e-6 deg lat, 1.2e-5 lon
    tr = from_origin(-73.75, 40.84, 1.2e-5, 9e-6)
    with rasterio.open(path, "w", driver="GTiff", height=100, width=100, count=1,
                       dtype="float32", crs="EPSG:4326", transform=tr, nodata=-9999) as dst:
        dst.write(dem, 1)
    dem_f = dem.copy(); dem_f[dem_f == -9999] = np.nan
    return path, dem_f, tr


def test_connectivity_excludes_cut_off_basin(tmp_path):
    _, dem, _ = make_dem(tmp_path)
    seeds = floodfill.build_seed_mask(dem, None)
    assert seeds[:, :10].all() and not seeds[:, 20:].any()
    mask = floodfill.connected_flood_mask(dem, seeds, water_level_m=0.5)
    assert mask[50, 15]            # beach cell at 0.15 m, connected
    assert not mask[50, 75]        # basin at 0.2 m but behind the ridge
    tub = floodfill.bathtub_mask(dem, 0.5)
    assert tub[50, 75]             # naive model would flood it — the bug we avoid


def test_segments_min_elevation_and_hazard(tmp_path):
    path, dem, tr = make_dem(tmp_path)
    # one road running from the shoreline inland along row 50
    def lonlat(col, row):
        return (tr.c + (col + 0.5) * tr.a, tr.f + (row + 0.5) * tr.e)
    road = LineString([lonlat(12, 50), lonlat(95, 50)])
    edges = gpd.GeoDataFrame({"u": [1], "v": [2], "key": [0], "osmid": [1],
                              "highway": ["residential"], "name": ["Shore Rd"],
                              "length": [83.0], "geometry": [road]}, crs=4326)
    segs = segments.build_segments(edges, path)
    assert len(segs) >= 4
    assert segs["ground_m"].is_monotonic_increasing or segs["ground_m"].iloc[0] < segs["ground_m"].iloc[-1]
    assert segs["ground_m"].iloc[0] == pytest.approx(0.06, abs=0.05)

    seeds = floodfill.build_seed_mask(dem, tr)
    wl = pd.Series([0.0, 0.4, 0.9], index=pd.date_range("2026-09-06", periods=3, freq="h", tz="UTC"))
    segs["near_inlet"] = False
    table = hazard.hazard_table(segs, dem, seeds, wl)
    assert set(table.columns) >= {"segment_id", "forecast_hour", "depth_cm", "flooded",
                                  "safe_child", "safe_adult", "safe_vehicle_small"}
    h0 = table[table.forecast_hour == 0]
    h2 = table[table.forecast_hour == 2]
    assert not h0.flooded.any()                       # low tide: nothing floods
    assert h2.flooded.sum() > h0.flooded.sum()
    first = h2.sort_values("segment_id").iloc[0]
    assert first.flooded and first.depth_cm > 30
    assert not first.safe_vehicle_small and first.safe_adult   # 0.3 m < depth < 1.2 m


def test_inlet_factor_tightens_limits():
    depth = np.array([0.2, 0.2])
    flags = hazard.classify(depth, near_inlet=np.array([False, True]))
    assert flags["vehicle_small"][0] and not flags["vehicle_small"][1]  # 0.3 vs 0.15 limit
