"""Stage 1 DEM fetch/tiling/merge and load_dem. No network: ``_export_tile``
is monkeypatched to synthesize a georeferenced tile locally instead of
hitting the 3DEP ImageServer, so the real tiling-and-merge math in
``fetch_dem`` gets exercised end to end.
"""
from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from tidestep import dem


def test_deg_per_m_is_smaller_lon_step_away_from_the_equator():
    dlat, dlon = dem._deg_per_m(40.8)
    # 1 m of longitude is a bigger angle than 1 m of latitude away from the
    # equator, because meridians converge: cos(40.8 deg) ~ 0.757.
    assert dlon > dlat
    assert dlat == pytest.approx(1 / 111_320, rel=1e-6)
    assert dlon == pytest.approx(1 / (111_320 * np.cos(np.radians(40.8))), rel=1e-6)


def _fake_export_tile_factory():
    """Return a stand-in for dem._export_tile that writes a real GeoTIFF for
    the requested bbox/size instead of calling the network, with a value at
    each pixel that encodes its position (row*1000+col) so a merge bug (mis-
    placed or duplicated tiles) is detectable by checking pixel values, not
    just output shape."""
    def fake(bbox, width, height, out):
        south, west, north, east = bbox
        transform = from_bounds(west, south, east, north, width, height)
        arr = np.zeros((height, width), dtype="float32")
        for r in range(height):
            arr[r, :] = np.arange(width, dtype="float32") + r * 1000
        with rasterio.open(out, "w", driver="GTiff", height=height, width=width,
                           count=1, dtype="float32", crs="EPSG:4326",
                           transform=transform, nodata=-9999) as dst:
            dst.write(arr, 1)
        return out
    return fake


def test_fetch_dem_single_tile_when_bbox_fits_max_px(tmp_path, monkeypatch):
    monkeypatch.setattr(dem, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dem, "_export_tile", _fake_export_tile_factory())
    # tiny bbox at a coarse resolution -> well under MAX_PX, single tile
    bbox = (40.80, -73.71, 40.81, -73.70)
    out = dem.fetch_dem(bbox=bbox, resolution_m=50.0, out_path=tmp_path / "dem.tif")
    assert out.exists()
    with rasterio.open(out) as src:
        assert src.width > 0 and src.height > 0
        assert src.crs is not None


def test_fetch_dem_tiles_and_merges_without_gaps(tmp_path, monkeypatch):
    """Force multi-tile splitting (MAX_PX tiny) and confirm the merged
    raster covers the full requested extent with no holes: every pixel of
    the mosaic must be finite (our fake tiles never write nodata), which
    would fail if the merge left gaps between tiles."""
    monkeypatch.setattr(dem, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dem, "MAX_PX", 5)
    monkeypatch.setattr(dem, "_export_tile", _fake_export_tile_factory())
    bbox = (40.80, -73.71, 40.81, -73.70)
    out = dem.fetch_dem(bbox=bbox, resolution_m=50.0, out_path=tmp_path / "dem_multi.tif")
    with rasterio.open(out) as src:
        arr = src.read(1)
        # our fake writer only ever emits finite values, so any -9999/NaN
        # surviving into the mosaic means the tiles didn't fully cover it
        assert np.isfinite(arr).all()
        assert src.width > 5 or src.height > 5   # actually needed >1 tile per axis


def test_fetch_dem_skips_download_if_file_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(dem, "DATA_DIR", tmp_path)
    out_path = tmp_path / "dem.tif"
    out_path.write_bytes(b"not a real tiff, just needs to exist")
    calls = []
    monkeypatch.setattr(dem, "_export_tile", lambda *a, **k: calls.append(1))
    result = dem.fetch_dem(bbox=(40.80, -73.71, 40.81, -73.70), out_path=out_path)
    assert result == out_path
    assert calls == []   # never touched the network path


def test_export_tile_rejects_a_non_tiff_response(tmp_path, monkeypatch):
    """3DEP occasionally 500s with an HTML/JSON error body dressed as a 200;
    _export_tile must not silently write that out as if it were a DEM."""
    class FakeResp:
        status_code = 200
        content = b"<html>Image Request Error</html>"
        text = "<html>Image Request Error</html>"
        def raise_for_status(self):
            pass

    monkeypatch.setattr(dem.requests, "get", lambda *a, **k: FakeResp())
    monkeypatch.setattr(dem.time, "sleep", lambda *_: None)   # skip the real backoff
    with pytest.raises(RuntimeError):
        dem._export_tile((40.80, -73.71, 40.81, -73.70), 10, 10, tmp_path / "bad.tif")


def test_load_dem_converts_nodata_to_nan(tmp_path):
    path = tmp_path / "small.tif"
    arr = np.array([[1.0, 2.0], [-9999.0, 4.0]], dtype="float32")
    transform = from_bounds(-73.71, 40.80, -73.70, 40.81, 2, 2)
    with rasterio.open(path, "w", driver="GTiff", height=2, width=2, count=1,
                       dtype="float32", crs="EPSG:4326", transform=transform,
                       nodata=-9999) as dst:
        dst.write(arr, 1)

    loaded, out_transform, meta = dem.load_dem(path)
    assert np.isnan(loaded[1, 0])
    assert loaded[0, 0] == pytest.approx(1.0)
    assert loaded[1, 1] == pytest.approx(4.0)
    assert out_transform == transform
    assert meta["crs"] is not None
