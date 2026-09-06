"""OpenStreetMap street network and water features for the bbox (Stage 1).

* ``fetch_graph``   – drivable+walkable road graph via osmnx, cached as
  GraphML in ``data/``. This same graph is what Stage 6 routes on.
* ``fetch_water``   – water polygons and coastline lines from OSM. These
  seed the connected flood-fill in Stage 2 (open-water pixels).
"""
from __future__ import annotations

import os
from pathlib import Path

import geopandas as gpd
import networkx as nx
import osmnx as ox

from . import config

DATA_DIR = Path(os.environ.get("TIDESTEP_DATA", "data"))
GRAPH_PATH = DATA_DIR / "streets.graphml"
WATER_PATH = DATA_DIR / "water.gpkg"


def fetch_graph(bbox=config.BBOX, force: bool = False) -> nx.MultiDiGraph:
    """Road graph for the bbox. ``network_type='all'`` keeps footpaths so the
    child/adult profiles can route on sidewalks and paths, not only roads."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if GRAPH_PATH.exists() and not force:
        return ox.load_graphml(GRAPH_PATH)
    south, west, north, east = bbox
    # osmnx >= 2.0 takes bbox as (left, bottom, right, top)
    G = ox.graph_from_bbox(bbox=(west, south, east, north),
                           network_type="all", simplify=True, retain_all=False)
    G = ox.add_edge_speeds(G)
    G = ox.add_edge_travel_times(G)
    ox.save_graphml(G, GRAPH_PATH)
    return G


def edges_gdf(G: nx.MultiDiGraph) -> gpd.GeoDataFrame:
    """Edges as a GeoDataFrame (WGS84) with u, v, key, osmid, highway, length."""
    edges = ox.graph_to_gdfs(G, nodes=False, edges=True).reset_index()
    keep = [c for c in ("u", "v", "key", "osmid", "name", "highway",
                        "length", "geometry") if c in edges.columns]
    return edges[keep]


def fetch_water(bbox=config.BBOX, force: bool = False) -> gpd.GeoDataFrame:
    """OSM water bodies and coastline within the bbox (WGS84)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if WATER_PATH.exists() and not force:
        return gpd.read_file(WATER_PATH)
    south, west, north, east = bbox
    tags = {"natural": ["water", "coastline", "bay", "wetland"],
            "water": True, "waterway": True}
    gdf = ox.features_from_bbox(bbox=(west, south, east, north), tags=tags)
    gdf = gdf.reset_index()
    # osmnx 2.x indexes features by (element, id); older versions by osmid
    if "osmid" not in gdf.columns and "id" in gdf.columns:
        gdf = gdf.rename(columns={"id": "osmid"})
    keep = ["osmid", "geometry"] + [c for c in ("element", "natural", "water",
                                                "waterway", "tidal", "name")
                                    if c in gdf.columns]
    gdf = gdf[keep]
    gdf = gdf[gdf.geometry.notna()].to_crs(4326)
    gdf["osmid"] = gdf["osmid"].astype(str)
    gdf.to_file(WATER_PATH, driver="GPKG")
    return gdf
