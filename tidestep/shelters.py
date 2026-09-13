"""Real shelter-location data for Router.route_to_safety() (Stage 11).

Until now, ANY dry street segment has counted as a "safe haven" for
route_to_safety() -- honestly flagged as a simplification in
docs/LIMITATIONS.md ("get me to safety" finds the nearest dry pavement,
not an actual building). This module fetches real candidate shelter
buildings from OpenStreetMap via osmnx, the same fetch-and-cache pattern
tidestep/streets.py's fetch_water() already uses for water polygons, so
route_to_safety() can prefer routing an evacuee to an actual building
instead of an arbitrary dry intersection -- while still falling back to
the old "any dry street" behavior wherever no shelter data has been
loaded (see tidestep/routing.py's Router._shelter_preferred_targets()),
so this is a strict upgrade, never a new way to fail.

Kinds fetched (OSM ``amenity`` tag values), each mapped to a short
human-readable label surfaced in the API/UI:

    amenity=school            -> "school"
    amenity=hospital          -> "hospital"
    amenity=community_centre  -> "community center"
    amenity=police            -> "police station"
    amenity=fire_station      -> "fire station"

These five are the building types a real emergency-management shelter
list actually uses -- FEMA and American Red Cross public-shelter
guidance both designate schools and community centers as the standard
shelter-building stock, with hospitals and fire/police stations always
staffed and built to a higher structural standard. Deliberately NOT
included: OSM's ``amenity=social_facility`` / ``amenity=place_of_worship``
tags, which cover a much wider and less reliable range of buildings (many
church halls and social-service offices are tagged this way with no
indication they'd actually take in evacuees) -- there is no tag-only way
to filter those down to "this building is a real shelter," so they are
left out rather than guessed at.
"""
from __future__ import annotations

import os
from pathlib import Path

import geopandas as gpd
import osmnx as ox

from . import config

DATA_DIR = Path(os.environ.get("TIDESTEP_DATA", "data"))
SHELTERS_PATH = DATA_DIR / "shelters.gpkg"

KIND_LABELS = {
    "school": "school",
    "hospital": "hospital",
    "community_centre": "community center",
    "police": "police station",
    "fire_station": "fire station",
}


def fetch_shelters(bbox=config.BBOX, force: bool = False) -> gpd.GeoDataFrame:
    """Candidate shelter buildings in ``bbox`` as WGS84 points, cached as
    GPKG in ``data/`` -- same caching pattern as streets.fetch_water().

    OSM represents these amenities as a bare node (already a point), a
    building outline (a Polygon), or occasionally a multi-building campus
    (a MultiPolygon); ``_centroid_points()`` collapses all three to a
    single representative point per feature, since Router.route_to_safety()
    needs one (lon, lat) per shelter to snap onto the nearest street-graph
    node, not a boundary to route around.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SHELTERS_PATH.exists() and not force:
        return gpd.read_file(SHELTERS_PATH)
    south, west, north, east = bbox
    tags = {"amenity": list(KIND_LABELS.keys())}
    gdf = ox.features_from_bbox(bbox=(west, south, east, north), tags=tags)
    gdf = gdf.reset_index()
    # osmnx 2.x indexes features by (element, id); older versions by osmid
    if "osmid" not in gdf.columns and "id" in gdf.columns:
        gdf = gdf.rename(columns={"id": "osmid"})
    gdf = gdf[gdf.geometry.notna()].copy()
    if "amenity" not in gdf.columns:
        gdf["amenity"] = None
    gdf["kind"] = gdf["amenity"].map(KIND_LABELS)
    gdf = gdf[gdf["kind"].notna()].copy()
    gdf = _centroid_points(gdf)
    if "name" not in gdf.columns:
        gdf["name"] = None
    # a shelter with no OSM `name` tag still gets a usable label, e.g.
    # "School" instead of a blank popup/annotation
    gdf["name"] = gdf["name"].fillna(gdf["kind"].str.title())
    keep = ["osmid", "name", "kind", "amenity", "geometry"]
    gdf = gdf[[c for c in keep if c in gdf.columns]]
    gdf["osmid"] = gdf["osmid"].astype(str)
    gdf.to_file(SHELTERS_PATH, driver="GPKG")
    return gdf


def _centroid_points(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Replace every geometry with its centroid, computed in a metric CRS
    (not raw WGS84 degrees, which distorts centroid position for larger
    buildings/campuses) then reprojected back to WGS84. A Point's centroid
    is itself, so this uniformly handles the Point/Polygon/MultiPolygon mix
    osmnx can return for these tags without branching on geometry type."""
    from .segments import METRIC_CRS
    metric = gdf.to_crs(METRIC_CRS)
    centroids_m = gpd.GeoSeries(metric.geometry.centroid, crs=METRIC_CRS, index=gdf.index)
    out = gdf.copy()
    out["geometry"] = centroids_m.to_crs(4326)
    return out.set_crs(4326, allow_override=True)
