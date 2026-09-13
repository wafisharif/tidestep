"""tidestep/shelters.py -- unit tests for the parts that don't need network
access (Overpass/osmnx fetch itself is exercised for real only by running
the module against the laptop's live network; see docs/STATUS.md). These
cover the two things that are actually easy to get subtly wrong without a
real Postgres+PostGIS instance or a live OSM fetch: centroid collapsing for
non-point OSM geometries, and the amenity -> kind label filter.
"""
from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

from tidestep import shelters


def test_kind_labels_cover_the_five_documented_amenities():
    assert set(shelters.KIND_LABELS) == {
        "school", "hospital", "community_centre", "police", "fire_station"}
    assert shelters.KIND_LABELS["fire_station"] == "fire station"
    assert shelters.KIND_LABELS["community_centre"] == "community center"


def test_centroid_points_leaves_a_point_geometry_unchanged():
    gdf = gpd.GeoDataFrame({"name": ["Test School"]}, geometry=[Point(-73.71, 40.80)], crs=4326)
    out = shelters._centroid_points(gdf)
    assert out.geometry.iloc[0].equals_exact(Point(-73.71, 40.80), tolerance=1e-9)


def test_centroid_points_collapses_a_polygon_to_its_metric_centroid():
    # A small square building footprint -- its WGS84-degree centroid and its
    # metric-CRS centroid should agree closely at this tiny scale, but the
    # real point of this test is that a Polygon comes back as a Point at
    # all, since Router.route_to_safety() snaps shelters onto graph nodes
    # by a single (lon, lat), not a boundary.
    square = Polygon([(-73.7101, 40.8000), (-73.7099, 40.8000),
                      (-73.7099, 40.8002), (-73.7101, 40.8002)])
    gdf = gpd.GeoDataFrame({"name": ["Test Hospital"]}, geometry=[square], crs=4326)
    out = shelters._centroid_points(gdf)
    pt = out.geometry.iloc[0]
    assert pt.geom_type == "Point"
    assert pt.x == pytest.approx(-73.7100, abs=1e-4)
    assert pt.y == pytest.approx(40.8001, abs=1e-4)


def test_centroid_points_handles_a_multipolygon_campus():
    from shapely.geometry import MultiPolygon
    a = Polygon([(-73.711, 40.800), (-73.7105, 40.800), (-73.7105, 40.8005), (-73.711, 40.8005)])
    b = Polygon([(-73.709, 40.800), (-73.7085, 40.800), (-73.7085, 40.8005), (-73.709, 40.8005)])
    gdf = gpd.GeoDataFrame({"name": ["Campus"]}, geometry=[MultiPolygon([a, b])], crs=4326)
    out = shelters._centroid_points(gdf)
    assert out.geometry.iloc[0].geom_type == "Point"
