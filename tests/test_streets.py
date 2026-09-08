"""Stage 1 street graph + water features. No network: osmnx's own network
calls (``ox.graph_from_bbox`` / ``ox.features_from_bbox``) are monkeypatched
out; everything downstream of those two calls is exercised for real.
"""
from __future__ import annotations

import geopandas as gpd
import networkx as nx
import osmnx as ox
import pytest
from shapely.geometry import LineString, Point

from tidestep import streets


def _tiny_graph() -> nx.MultiDiGraph:
    G = nx.MultiDiGraph(crs="epsg:4326")
    G.add_node(1, x=-73.71, y=40.80)
    G.add_node(2, x=-73.70, y=40.80)
    G.add_node(3, x=-73.70, y=40.81)
    G.add_edge(1, 2, key=0, length=100.0, highway="residential", name="Shore Rd",
              osmid=111, geometry=LineString([(-73.71, 40.80), (-73.70, 40.80)]))
    G.add_edge(2, 1, key=0, length=100.0, highway="residential", name="Shore Rd",
              osmid=111, geometry=LineString([(-73.70, 40.80), (-73.71, 40.80)]))
    G.add_edge(2, 3, key=0, length=120.0, highway="footway", osmid=222,
              geometry=LineString([(-73.70, 40.80), (-73.70, 40.81)]))
    return G


def test_edges_gdf_keeps_expected_columns_and_row_count():
    G = _tiny_graph()
    edges = streets.edges_gdf(G)
    assert len(edges) == 3
    for col in ("u", "v", "key", "highway", "length", "geometry"):
        assert col in edges.columns
    assert set(edges["highway"]) == {"residential", "footway"}


def test_fetch_graph_uses_cache_and_skips_network(tmp_path, monkeypatch):
    monkeypatch.setattr(streets, "DATA_DIR", tmp_path)
    graph_path = tmp_path / "streets.graphml"
    monkeypatch.setattr(streets, "GRAPH_PATH", graph_path)
    ox.save_graphml(_tiny_graph(), graph_path)

    calls = []
    monkeypatch.setattr(streets.ox, "graph_from_bbox", lambda *a, **k: calls.append(1))
    G = streets.fetch_graph()
    assert calls == []             # never hit the network path
    assert set(G.nodes) == {1, 2, 3}


def test_fetch_graph_force_refetches_even_if_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(streets, "DATA_DIR", tmp_path)
    graph_path = tmp_path / "streets.graphml"
    monkeypatch.setattr(streets, "GRAPH_PATH", graph_path)
    ox.save_graphml(_tiny_graph(), graph_path)

    fresh = nx.MultiDiGraph(crs="epsg:4326")
    fresh.add_node(9, x=0, y=0)
    calls = []

    def fake_fetch(bbox, network_type, simplify, retain_all):
        calls.append((bbox, network_type, retain_all))
        return fresh

    monkeypatch.setattr(streets.ox, "graph_from_bbox", fake_fetch)
    monkeypatch.setattr(streets.ox, "add_edge_speeds", lambda g: g)
    monkeypatch.setattr(streets.ox, "add_edge_travel_times", lambda g: g)
    G = streets.fetch_graph(force=True)
    assert len(calls) == 1
    # bbox passed to osmnx must be (left, bottom, right, top) = (west, south, east, north)
    (west, south, east, north), network_type, retain_all = calls[0]
    from tidestep import config
    assert (west, south, east, north) == (config.BBOX[1], config.BBOX[0],
                                          config.BBOX[3], config.BBOX[2])
    assert retain_all is True      # required so the west shore is never dropped
    assert set(G.nodes) == {9}


def test_fetch_water_renames_id_to_osmid_for_osmnx_2x(tmp_path, monkeypatch):
    """osmnx >= 2.0 indexes features_from_bbox results by (element, id)
    instead of a plain 'osmid' column; fetch_water must normalize that back
    to 'osmid' so downstream code (hazard.flag_near_inlet etc.) has a
    consistent column name regardless of osmnx version."""
    monkeypatch.setattr(streets, "DATA_DIR", tmp_path)
    water_path = tmp_path / "water.gpkg"
    monkeypatch.setattr(streets, "WATER_PATH", water_path)

    # simulate the osmnx 2.x shape: no 'osmid' column, an 'id' column instead
    # (fetch_water itself does the .reset_index() osmnx 2.x needs; what it
    # must handle here is the missing 'osmid' column that reset_index alone
    # doesn't fix).
    bay = gpd.GeoDataFrame(
        {"id": [555], "natural": ["water"],
         "geometry": [Point(-73.70, 40.80).buffer(0.001)]},
        crs=4326,
    )
    monkeypatch.setattr(streets.ox, "features_from_bbox", lambda *a, **k: bay)
    gdf = streets.fetch_water()
    assert "osmid" in gdf.columns
    assert gdf["osmid"].iloc[0] == "555"
    assert water_path.exists()


def test_fetch_water_uses_cache_and_skips_network(tmp_path, monkeypatch):
    monkeypatch.setattr(streets, "DATA_DIR", tmp_path)
    water_path = tmp_path / "water.gpkg"
    monkeypatch.setattr(streets, "WATER_PATH", water_path)
    gpd.GeoDataFrame({"osmid": ["1"], "geometry": [Point(0, 0).buffer(0.01)]}, crs=4326) \
        .to_file(water_path, driver="GPKG")

    calls = []
    monkeypatch.setattr(streets.ox, "features_from_bbox", lambda *a, **k: calls.append(1))
    gdf = streets.fetch_water()
    assert calls == []
    assert len(gdf) == 1
