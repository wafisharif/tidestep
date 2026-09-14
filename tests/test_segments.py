"""Stage 2a helpers not already exercised by test_floodmodel.py's
end-to-end segment+hazard test: line splitting, OSM tag normalization, and
the segment_edges table shape. No network, no DEM needed."""
from __future__ import annotations

import numpy as np
import pandas as pd
import geopandas as gpd
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString

from tidestep import segments


def test_split_line_exact_multiple_of_seg_len():
    line = LineString([(0, 0), (60, 0)])
    pieces = segments.split_line(line, seg_len=15)
    assert len(pieces) == 4
    assert sum(p.length for p in pieces) == pytest.approx(60)
    for p in pieces:
        assert p.length == pytest.approx(15)


def test_split_line_shorter_than_seg_len_stays_one_piece():
    line = LineString([(0, 0), (5, 0)])
    pieces = segments.split_line(line, seg_len=15)
    assert len(pieces) == 1
    assert pieces[0].length == pytest.approx(5)


def test_split_line_rounds_to_nearest_whole_number_of_pieces():
    # 40 m at a 15 m target: 40/15 = 2.67 -> rounds to 3 pieces of ~13.3 m
    line = LineString([(0, 0), (40, 0)])
    pieces = segments.split_line(line, seg_len=15)
    assert len(pieces) == 3
    for p in pieces:
        assert p.length == pytest.approx(40 / 3)


def test_split_line_degenerate_zero_length_line_does_not_crash():
    line = LineString([(5, 5), (5, 5)])
    pieces = segments.split_line(line, seg_len=15)
    assert len(pieces) == 1
    assert pieces[0].length == pytest.approx(0)


@pytest.mark.parametrize("value,expected", [
    (None, None),
    ("Main St", "Main St"),
    (["Main St", "Broadway"], "Main St;Broadway"),
    (("a", "b"), "a;b"),
    (float("nan"), None),
    (42, "42"),
])
def test_tag_normalizes_osm_values(value, expected):
    assert segments._tag(value) == expected


def _edges_gdf():
    road = LineString([(-73.71, 40.80), (-73.70881332, 40.80)])   # ~100 m east-west at 40.80N
    return gpd.GeoDataFrame({
        "u": [1], "v": [2], "key": [0], "osmid": [123],
        "highway": ["residential"], "name": ["Shore Rd"],
        "length": [100.0], "geometry": [road],
    }, crs=4326)


def test_segment_edges_assigns_sequential_ids_and_wgs84_output():
    edges = _edges_gdf()
    segs = segments.segment_edges(edges, seg_len=15)
    assert list(segs["segment_id"]) == list(range(len(segs)))
    assert segs.crs.to_epsg() == 4326
    assert (segs["u"] == 1).all() and (segs["v"] == 2).all()
    assert list(segs["seg_idx"]) == list(range(len(segs)))   # one edge -> 0..n-1
    assert segs["name"].iloc[0] == "Shore Rd"


def test_segment_edges_length_sums_to_original_edge_length():
    edges = _edges_gdf()
    segs = segments.segment_edges(edges, seg_len=15)
    # reprojected to a metric CRS internally; lengths should sum back close
    # to the ~100 m original (small distortion from the WGS84 round trip)
    assert segs["length_m"].sum() == pytest.approx(100.0, rel=0.05)


def test_segment_edges_handles_multiple_edges_independently():
    a = LineString([(-73.71, 40.80), (-73.7085, 40.80)])   # ~130 m
    b = LineString([(-73.70, 40.81), (-73.70, 40.8115)])    # ~166 m
    edges = gpd.GeoDataFrame({
        "u": [1, 3], "v": [2, 4], "key": [0, 0], "osmid": [1, 2],
        "highway": ["residential", "footway"], "name": ["A St", None],
        "length": [130.0, 166.0], "geometry": [a, b],
    }, crs=4326)
    segs = segments.segment_edges(edges, seg_len=15)
    assert set(segs["u"]) == {1, 3}
    # each edge's own seg_idx restarts at 0
    for u in (1, 3):
        idx = segs.loc[segs["u"] == u, "seg_idx"].tolist()
        assert idx == list(range(len(idx)))
    # segment_id is globally unique and sequential across both edges
    assert list(segs["segment_id"]) == list(range(len(segs)))


def _tiny_dem(tmp_path):
    """A small DEM covering roughly lon [-73.711, -73.709], lat [40.799,
    40.801] -- test_floodmodel.py's end-to-end test only ever samples
    segments that fall INSIDE a DEM this size; nothing there exercises a
    segment entirely outside it."""
    dem = np.full((20, 20), 1.0, dtype="float32")
    tr = from_origin(-73.711, 40.801, 1e-4, 1e-4)
    path = tmp_path / "small_dem.tif"
    with rasterio.open(path, "w", driver="GTiff", height=20, width=20, count=1,
                       dtype="float32", crs="EPSG:4326", transform=tr, nodata=-9999) as dst:
        dst.write(dem, 1)
    return path


def test_sample_min_elevation_returns_nan_ground_for_a_segment_entirely_off_dem(tmp_path):
    """A road segment far outside the cached DEM tile's extent (documented
    in sample_min_elevation's own docstring: 'ground_m is NaN if the
    segment is off-DEM') -- this exact case had never been exercised: the
    only existing caller (test_floodmodel.py) only ever samples segments
    that fall inside the DEM. Must degrade to NaN, not raise an index
    error from rowcol() landing outside the raster."""
    dem_path = _tiny_dem(tmp_path)
    # a road several degrees away from the tiny DEM's ~0.002 deg extent
    far_away = LineString([(10.0, 10.0), (10.001, 10.0)])
    segs = gpd.GeoDataFrame({"geometry": [far_away]}, crs=4326)
    out = segments.sample_min_elevation(segs, dem_path)
    assert len(out) == 1
    # column-first indexing keeps ground_m's own float64 dtype -- .iloc[0]
    # on the whole row upcasts across the mixed float64/Int64(min_row,
    # min_col) columns and turns NaN into pandas' NA, which np.isnan()
    # can't evaluate
    assert np.isnan(out["ground_m"].iloc[0])
    assert out["min_row"].iloc[0] is pd.NA
    assert out["min_col"].iloc[0] is pd.NA


def test_sample_min_elevation_finds_the_real_minimum_for_an_on_dem_segment(tmp_path):
    """Sanity check the same function's normal path with a controlled,
    non-flat DEM (a two-value split, min on the left) so the picked
    ground_m is provably the minimum sampled value, not just "some
    finite number"."""
    dem = np.full((20, 20), 5.0, dtype="float32")
    dem[:, :10] = 0.5   # left half is lower
    tr = from_origin(-73.711, 40.801, 1e-4, 1e-4)
    path = tmp_path / "split_dem.tif"
    with rasterio.open(path, "w", driver="GTiff", height=20, width=20, count=1,
                       dtype="float32", crs="EPSG:4326", transform=tr, nodata=-9999) as dst:
        dst.write(dem, 1)
    # a road crossing the whole DEM west to east, so it samples both halves
    road = LineString([(-73.7109, 40.8001), (-73.7091, 40.8001)])
    segs = gpd.GeoDataFrame({"geometry": [road]}, crs=4326)
    out = segments.sample_min_elevation(segs, path)
    assert out.iloc[0]["ground_m"] == pytest.approx(0.5, abs=1e-3)
