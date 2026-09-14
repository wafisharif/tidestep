"""tidestep/shelters.py -- unit tests for the parts that don't need network
access (Overpass/osmnx fetch itself is exercised for real only by running
the module against the laptop's live network; see docs/STATUS.md). These
cover the two things that are actually easy to get subtly wrong without a
real Postgres+PostGIS instance or a live OSM fetch: centroid collapsing for
non-point OSM geometries, and the amenity -> kind label filter.

fetch_shelters() itself was, until this pass, tested only indirectly (its
helper _centroid_points() and KIND_LABELS above, but never the function's
own body: caching, the osmnx 2.x id->osmid rename, the amenity filter, the
missing-name fallback, the defensive "no amenity column at all" branch).
Following the exact pattern tests/test_streets.py already established for
streets.fetch_water() (same fetch-and-cache shape, same osmnx call),
ox.features_from_bbox is monkeypatched out and everything downstream of it
is exercised for real -- no network, but real logic, not just a mock
returning what the test expects.
"""
from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

from tidestep import config, shelters


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


# --- fetch_shelters() itself -- no network, ox.features_from_bbox mocked --

def test_fetch_shelters_uses_cache_and_skips_network(tmp_path, monkeypatch):
    monkeypatch.setattr(shelters, "DATA_DIR", tmp_path)
    path = tmp_path / "shelters.gpkg"
    monkeypatch.setattr(shelters, "SHELTERS_PATH", path)
    gpd.GeoDataFrame({"osmid": ["1"], "name": ["Test School"], "kind": ["school"],
                      "amenity": ["school"], "geometry": [Point(-73.71, 40.80)]}, crs=4326) \
        .to_file(path, driver="GPKG")

    calls = []
    monkeypatch.setattr(shelters.ox, "features_from_bbox", lambda *a, **k: calls.append(1))
    gdf = shelters.fetch_shelters()
    assert calls == []            # never hit the network path
    assert len(gdf) == 1


def test_fetch_shelters_force_refetches_even_if_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(shelters, "DATA_DIR", tmp_path)
    path = tmp_path / "shelters.gpkg"
    monkeypatch.setattr(shelters, "SHELTERS_PATH", path)
    gpd.GeoDataFrame({"osmid": ["1"], "name": ["Old"], "kind": ["school"],
                      "amenity": ["school"], "geometry": [Point(-73.71, 40.80)]}, crs=4326) \
        .to_file(path, driver="GPKG")

    fresh = gpd.GeoDataFrame(
        {"osmid": [999], "amenity": ["hospital"], "geometry": [Point(-73.70, 40.81)]}, crs=4326)
    calls = []

    def fake_fetch(*a, **k):
        calls.append(1)
        return fresh
    monkeypatch.setattr(shelters.ox, "features_from_bbox", fake_fetch)
    gdf = shelters.fetch_shelters(force=True)
    assert len(calls) == 1
    assert gdf["kind"].iloc[0] == "hospital"   # the fresh row, not the stale cached one


def test_fetch_shelters_renames_id_to_osmid_for_osmnx_2x(tmp_path, monkeypatch):
    """Same osmnx >= 2.0 shape streets.fetch_water() already has to handle
    (features_from_bbox indexes by (element, id), not a plain 'osmid'
    column) -- fetch_shelters() has its own copy of this rename, never
    exercised before this pass."""
    monkeypatch.setattr(shelters, "DATA_DIR", tmp_path)
    path = tmp_path / "shelters.gpkg"
    monkeypatch.setattr(shelters, "SHELTERS_PATH", path)
    raw = gpd.GeoDataFrame(
        {"id": [777], "amenity": ["fire_station"], "geometry": [Point(-73.70, 40.80)]}, crs=4326)
    monkeypatch.setattr(shelters.ox, "features_from_bbox", lambda *a, **k: raw)
    gdf = shelters.fetch_shelters()
    assert "osmid" in gdf.columns
    assert gdf["osmid"].iloc[0] == "777"
    assert gdf["kind"].iloc[0] == "fire station"


def test_fetch_shelters_filters_out_non_shelter_amenities(tmp_path, monkeypatch):
    """OSM's amenity=* tag covers far more than the five shelter-relevant
    kinds this module documents (restaurants, banks, ...) -- a raw Overpass
    result in the study bbox will include plenty of them, and they must
    never leak into the shelters table."""
    monkeypatch.setattr(shelters, "DATA_DIR", tmp_path)
    path = tmp_path / "shelters.gpkg"
    monkeypatch.setattr(shelters, "SHELTERS_PATH", path)
    raw = gpd.GeoDataFrame(
        {"osmid": [1, 2], "amenity": ["restaurant", "school"],
         "geometry": [Point(-73.71, 40.80), Point(-73.70, 40.81)]}, crs=4326)
    monkeypatch.setattr(shelters.ox, "features_from_bbox", lambda *a, **k: raw)
    gdf = shelters.fetch_shelters()
    assert len(gdf) == 1
    assert gdf["kind"].iloc[0] == "school"


def test_fetch_shelters_handles_missing_amenity_column_without_crashing(tmp_path, monkeypatch):
    """Defensive branch (gdf.get("amenity") is added as all-None when the
    column is absent entirely) -- rare, but a raw Overpass result filtered
    only by tag keys can in principle come back without the tag column
    itself in some osmnx/Overpass edge cases. Must degrade to 'no real
    shelters found', not raise."""
    monkeypatch.setattr(shelters, "DATA_DIR", tmp_path)
    path = tmp_path / "shelters.gpkg"
    monkeypatch.setattr(shelters, "SHELTERS_PATH", path)
    raw = gpd.GeoDataFrame({"osmid": [1], "geometry": [Point(-73.71, 40.80)]}, crs=4326)
    monkeypatch.setattr(shelters.ox, "features_from_bbox", lambda *a, **k: raw)
    gdf = shelters.fetch_shelters()
    assert len(gdf) == 0


def test_fetch_shelters_fills_missing_name_with_titled_kind(tmp_path, monkeypatch):
    """A shelter building with no OSM 'name' tag (common for e.g. a small
    fire station) must still get a usable, human-readable label instead of
    a blank annotation on the map/API."""
    monkeypatch.setattr(shelters, "DATA_DIR", tmp_path)
    path = tmp_path / "shelters.gpkg"
    monkeypatch.setattr(shelters, "SHELTERS_PATH", path)
    raw = gpd.GeoDataFrame(   # no "name" column at all
        {"osmid": [1], "amenity": ["community_centre"], "geometry": [Point(-73.71, 40.80)]},
        crs=4326)
    monkeypatch.setattr(shelters.ox, "features_from_bbox", lambda *a, **k: raw)
    gdf = shelters.fetch_shelters()
    assert gdf["name"].iloc[0] == "Community Center"


def test_fetch_shelters_drops_rows_with_null_geometry(tmp_path, monkeypatch):
    monkeypatch.setattr(shelters, "DATA_DIR", tmp_path)
    path = tmp_path / "shelters.gpkg"
    monkeypatch.setattr(shelters, "SHELTERS_PATH", path)
    raw = gpd.GeoDataFrame(
        {"osmid": [1, 2], "amenity": ["school", "hospital"],
         "geometry": [None, Point(-73.70, 40.81)]}, crs=4326)
    monkeypatch.setattr(shelters.ox, "features_from_bbox", lambda *a, **k: raw)
    gdf = shelters.fetch_shelters()
    assert len(gdf) == 1
    assert gdf["kind"].iloc[0] == "hospital"


def test_fetch_shelters_passes_bbox_in_west_south_east_north_order(tmp_path, monkeypatch):
    """Same bbox-order footgun tests/test_streets.py's fetch_graph test
    already guards against -- config.BBOX is stored (south, west, north,
    east), but osmnx's features_from_bbox wants (west, south, east,
    north). Getting this backwards silently queries the wrong location
    instead of raising, so it needs a real assertion, not just "did it
    run"."""
    monkeypatch.setattr(shelters, "DATA_DIR", tmp_path)
    path = tmp_path / "shelters.gpkg"
    monkeypatch.setattr(shelters, "SHELTERS_PATH", path)
    calls = []

    def fake_fetch(bbox, tags):
        calls.append((bbox, tags))
        return gpd.GeoDataFrame({"osmid": [1], "amenity": ["school"],
                                 "geometry": [Point(-73.71, 40.80)]}, crs=4326)
    monkeypatch.setattr(shelters.ox, "features_from_bbox", fake_fetch)
    shelters.fetch_shelters()
    (west, south, east, north), tags = calls[0]
    assert (west, south, east, north) == (
        config.BBOX[1], config.BBOX[0], config.BBOX[3], config.BBOX[2])
    assert set(tags["amenity"]) == set(shelters.KIND_LABELS.keys())
