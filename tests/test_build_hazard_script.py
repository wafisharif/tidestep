"""Regression test for a real bug found by auditing scripts/build_hazard.py
against docs/PIPELINE.md: the script cached data/segments.gpkg (expensive:
DEM sampling) and, whenever that cache existed, silently skipped
recomputing ``near_inlet`` — a cheap spatial join against data/water.gpkg.

Concretely: the very common first-run sequence is fetch_all.py failing or
being skipped for water.gpkg (network flake, or run before the water layer
was fetchable), then build_hazard.py running anyway and caching
segments.gpkg with near_inlet all False. A later fetch_all.py run that
successfully brings in water.gpkg would have no effect at all on a repeat
build_hazard.py run: the stale cached segments.gpkg was read as-is and
near_inlet stayed all False forever, with no error and no obvious sign
anything was wrong — exactly the kind of bug that only shows up as "the
demo never shows a stricter inlet threshold anywhere," discovered too late
to fix live.

No network or database needed: this drives refresh_near_inlet() directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, Point

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_hazard  # noqa: E402


def _segs_near_a_waterway():
    """One segment that sits right next to a waterway line, one far away."""
    return gpd.GeoDataFrame({
        "segment_id": [0, 1],
        "u": [1, 3], "v": [2, 4], "key": [0, 0],
        "geometry": [
            LineString([(-73.700, 40.800), (-73.6999, 40.800)]),   # near the inlet
            LineString([(-73.650, 40.850), (-73.6499, 40.850)]),   # far away
        ],
    }, crs=4326)


def _waterway():
    return gpd.GeoDataFrame(
        {"osmid": ["1"], "waterway": ["stream"], "natural": [None]},
        geometry=[LineString([(-73.700, 40.7995), (-73.700, 40.8005)])],
        crs=4326)


def test_refresh_near_inlet_recomputes_even_when_cache_predates_water_layer(tmp_path):
    seg_path = tmp_path / "segments.gpkg"
    segs = _segs_near_a_waterway()

    # simulate the first run: water.gpkg didn't exist yet, so near_inlet was
    # written as all-False and cached to disk — this is the exact state a
    # real fresh-clone first run leaves behind.
    stale = segs.copy()
    stale["near_inlet"] = False
    stale.to_file(seg_path, driver="GPKG")

    # simulate a later run: the cached segments.gpkg is loaded (as
    # build_hazard.main() does when seg_path.exists()), but this time
    # water.gpkg is available.
    reloaded = gpd.read_file(seg_path)
    assert not reloaded["near_inlet"].any()   # confirms the stale state

    updated = build_hazard.refresh_near_inlet(reloaded, _waterway(), seg_path)

    assert updated.loc[updated["segment_id"] == 0, "near_inlet"].iloc[0]       # near -> True now
    assert not updated.loc[updated["segment_id"] == 1, "near_inlet"].iloc[0]   # far stays False

    # and the fix must persist to disk, not just the in-memory frame,
    # otherwise the next script in the pipeline (load_db.py) reads the
    # gpkg directly and would still load the stale flags into PostGIS.
    on_disk = gpd.read_file(seg_path)
    assert on_disk.loc[on_disk["segment_id"] == 0, "near_inlet"].iloc[0]


def test_refresh_near_inlet_skips_disk_write_when_nothing_changed(tmp_path):
    seg_path = tmp_path / "segments.gpkg"
    segs = _segs_near_a_waterway()
    segs["near_inlet"] = [True, False]   # already correct
    segs.to_file(seg_path, driver="GPKG")
    mtime_before = seg_path.stat().st_mtime_ns

    reloaded = gpd.read_file(seg_path)
    build_hazard.refresh_near_inlet(reloaded, _waterway(), seg_path)

    assert seg_path.stat().st_mtime_ns == mtime_before   # untouched: no-op write avoided


def test_refresh_near_inlet_with_no_water_layer_yet_leaves_everything_false(tmp_path):
    seg_path = tmp_path / "segments.gpkg"
    segs = _segs_near_a_waterway()
    updated = build_hazard.refresh_near_inlet(segs, None, seg_path)
    assert not updated["near_inlet"].any()
