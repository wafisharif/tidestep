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


# --- build_seed_mask()'s water_gdf branch ---------------------------------
# Every existing test above (and every call in tests/test_validate.py) uses
# water_gdf=None, so this whole branch -- reprojecting real OSM water
# polygons into the DEM's CRS and rasterizing them into the seed mask --
# had never been exercised by any test, despite being real spatial-transform
# logic every live pipeline run (fetch_all.py -> build_hazard.py /
# validate_stage9.py, which both pass a real water.gpkg when present) goes
# through. A CRS or rasterization bug here would silently miss real bay
# area as a flood seed, or seed somewhere physically wrong.

def _small_flat_dem(tmp_path, elevation_m: float):
    """A tiny grid entirely at one elevation, above config.SEED_ELEVATION_M
    and with no nodata cells -- so build_seed_mask finds ZERO seeds from
    elevation/nodata alone, and any seed pixel in the result can only have
    come from the water_gdf branch. transform matches make_dem()'s
    ~1 m/pixel WGS84-ish convention."""
    dem = np.full((20, 20), elevation_m, dtype="float32")
    tr = from_origin(-73.75, 40.84, 1.2e-5, 9e-6)
    return dem, tr


def test_build_seed_mask_incorporates_water_polygon_seeds(tmp_path):
    from shapely.geometry import Polygon
    dem, tr = _small_flat_dem(tmp_path, elevation_m=0.5)   # above SEED_ELEVATION_M, no nodata
    seeds_no_water = floodfill.build_seed_mask(dem, tr)
    assert not seeds_no_water.any()    # confirms the "no natural seeds" setup

    # a small square covering roughly the DEM's top-left corner (row 0-2,
    # col 0-2), same CRS as the DEM (EPSG:4326) -- the simplest case
    def lonlat(col, row):
        return (tr.c + col * tr.a, tr.f + row * tr.e)
    square = Polygon([lonlat(0, 0), lonlat(3, 0), lonlat(3, 3), lonlat(0, 3)])
    water = gpd.GeoDataFrame({"natural": ["water"]}, geometry=[square], crs=4326)

    seeds = floodfill.build_seed_mask(dem, tr, water, dem_crs=4326)
    assert seeds[0:3, 0:3].any()       # the polygon's footprint is now seeded
    assert not seeds[15:, 15:].any()   # far corner, untouched by the polygon, still isn't


def test_build_seed_mask_reprojects_water_gdf_to_dem_crs(tmp_path):
    """water_gdf supplied in a DIFFERENT real CRS than the DEM (UTM 18N,
    the correct zone for this study area's longitude, vs. the DEM's
    EPSG:4326) must still burn into the right pixels -- proving
    `w.to_crs(dem_crs)` actually runs, not just that passing dem_crs=None
    happens to work by accident."""
    from shapely.geometry import Polygon
    dem, tr = _small_flat_dem(tmp_path, elevation_m=0.5)

    def lonlat(col, row):
        return (tr.c + col * tr.a, tr.f + row * tr.e)
    square_4326 = Polygon([lonlat(0, 0), lonlat(3, 0), lonlat(3, 3), lonlat(0, 3)])
    # build it in 4326 first, then reproject to UTM 18N -- same real
    # geometry, different CRS, exactly what a caller loading a UTM-CRS
    # water.gpkg would hand build_seed_mask
    water_utm = gpd.GeoDataFrame({"natural": ["water"]}, geometry=[square_4326], crs=4326) \
        .to_crs(32618)
    assert water_utm.crs.to_epsg() == 32618   # confirms the fixture itself really is in UTM

    seeds = floodfill.build_seed_mask(dem, tr, water_utm, dem_crs=4326)
    assert seeds[0:3, 0:3].any()       # correctly reprojected back and burned in the right spot
    assert not seeds[15:, 15:].any()


def test_build_seed_mask_ignores_non_polygon_water_geometries(tmp_path):
    """A stream centerline (LineString) in the water layer -- real OSM
    water data mixes waterway=* lines with natural=water polygons -- must
    be filtered out rather than crash rasterize() or get silently treated
    as a seed some other way."""
    from shapely.geometry import LineString
    dem, tr = _small_flat_dem(tmp_path, elevation_m=0.5)

    def lonlat(col, row):
        return (tr.c + col * tr.a, tr.f + row * tr.e)
    line = LineString([lonlat(0, 0), lonlat(5, 5)])
    water = gpd.GeoDataFrame({"natural": ["water"]}, geometry=[line], crs=4326)

    seeds = floodfill.build_seed_mask(dem, tr, water, dem_crs=4326)
    assert not seeds.any()   # no polygons -> nothing rasterized -> no new seeds, no crash


def test_build_seed_mask_empty_water_gdf_is_a_noop(tmp_path):
    dem, tr = _small_flat_dem(tmp_path, elevation_m=0.5)
    empty = gpd.GeoDataFrame({"natural": []}, geometry=[], crs=4326)
    seeds = floodfill.build_seed_mask(dem, tr, empty, dem_crs=4326)
    assert not seeds.any()


def test_build_seed_mask_no_seeds_anywhere_returns_all_false(tmp_path):
    """Every cell above SEED_ELEVATION_M, no nodata, no water_gdf at all --
    the n == 0 early-return branch in build_seed_mask."""
    dem, tr = _small_flat_dem(tmp_path, elevation_m=0.5)
    seeds = floodfill.build_seed_mask(dem, tr)
    assert seeds.shape == dem.shape
    assert not seeds.any()


def test_flag_near_inlet_with_only_polygon_water_geometries_returns_all_false():
    """flag_near_inlet looks specifically for LineString/MultiLineString
    waterway features (culverts/streams/channels) -- a water_gdf that has
    real rows but only Polygon geometries (e.g. bay/lake outlines, no
    waterway lines at all for this bbox) must degrade to 'nothing flagged'
    rather than crash sjoin_nearest on an empty lines frame. The
    build_hazard.refresh_near_inlet tests already cover the main
    near/far-by-distance logic through this same function; this is the
    one branch none of them happen to exercise (a real water_gdf with
    zero line geometries in it)."""
    from shapely.geometry import Point, LineString
    road = LineString([(-73.710, 40.800), (-73.700, 40.800)])
    segs = gpd.GeoDataFrame({"segment_id": [0]}, geometry=[road], crs=4326)
    bay_only = gpd.GeoDataFrame(
        {"natural": ["water"]}, geometry=[Point(-73.705, 40.800).buffer(0.01)], crs=4326)
    near = hazard.flag_near_inlet(segs, bay_only)
    assert not near.any()


def test_connected_flood_mask_water_level_below_every_cell_returns_all_false(tmp_path):
    """The n == 0 early-return branch in connected_flood_mask: an extreme
    low-tide water level below every DEM cell (including any seed water)
    means nothing at all is flooded, not a crash from an empty label
    array."""
    dem, tr = _small_flat_dem(tmp_path, elevation_m=0.5)
    seeds = np.zeros_like(dem, dtype=bool)
    mask = floodfill.connected_flood_mask(dem, seeds, water_level_m=-10.0)
    assert mask.shape == dem.shape
    assert not mask.any()
