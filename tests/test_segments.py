"""Stage 2a helpers not already exercised by test_floodmodel.py's
end-to-end segment+hazard test: line splitting, OSM tag normalization, and
the segment_edges table shape. No network, no DEM needed."""
from __future__ import annotations

import numpy as np
import geopandas as gpd
import pytest
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
