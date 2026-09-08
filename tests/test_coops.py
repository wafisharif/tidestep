import pandas as pd
import pytest

from tidestep import config, coops


def test_datum_offset_matches_scope_doc():
    assert config.MLLW_TO_NAVD88_FT == pytest.approx(4.21)
    assert config.MLLW_TO_NAVD88_M == pytest.approx(1.283, abs=1e-3)


def test_nws_minor_threshold_in_navd88():
    # 22.89 ft STND - 17.09 ft = 5.80 ft = 1.768 m
    assert config.FLOOD_THRESHOLDS_M_NAVD88["nws_minor"] == pytest.approx(1.768, abs=1e-3)


def test_to_series_and_hourly_max():
    body = {"data": [
        {"t": "2026-09-06 00:00", "v": "1.03"},
        {"t": "2026-09-06 00:06", "v": "1.10"},
        {"t": "2026-09-06 00:54", "v": "0.90"},
        {"t": "2026-09-06 01:00", "v": "0.80"},
        {"t": "2026-09-06 01:30", "v": ""},       # CO-OPS gaps are empty strings
    ]}
    s = coops._to_series(body, "data")
    assert len(s) == 4
    h = coops.to_hourly_navd88(s)
    assert list(h.index.hour) == [0, 1]
    # hour 0 keeps the 6-minute peak (1.10 m MLLW), then minus 1.283 m
    assert h.iloc[0] == pytest.approx(1.10 - config.MLLW_TO_NAVD88_M, abs=1e-3)
    assert h.iloc[1] == pytest.approx(0.80 - config.MLLW_TO_NAVD88_M, abs=1e-3)


def test_predictions_key():
    body = {"predictions": [{"t": "2026-09-06 03:00", "v": "2.5"}]}
    s = coops._to_series(body, "predictions")
    assert s.iloc[0] == 2.5


def test_empty_payload():
    assert coops.to_hourly_navd88(coops._to_series({"data": []}, "data")).empty


class _FakeResponse:
    """Minimal requests.Response stand-in for monkeypatching requests.get."""
    def __init__(self, json_body):
        self._json = json_body

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def test_get_raises_runtime_error_on_coops_error_body(monkeypatch):
    """CO-OPS reports upstream problems (bad station, bad date range, etc.)
    as HTTP 200 with an {"error": {...}} body, not an HTTP error status --
    _get must not silently hand that back as if it were real data."""
    monkeypatch.setattr(coops.requests, "get",
                        lambda *a, **k: _FakeResponse({"error": {"message": "No data was found."}}))
    with pytest.raises(RuntimeError, match="No data was found"):
        coops._get({"product": "ofs_water_level"})


def test_check_datums_passes_when_live_matches_config(monkeypatch):
    live = {"MLLW": config.DATUMS_FT_STND["MLLW"],
            "NAVD88": config.DATUMS_FT_STND["NAVD88"],
            "MHHW": config.DATUMS_FT_STND["MHHW"]}
    monkeypatch.setattr(coops, "fetch_datums", lambda: live)
    assert coops.check_datums() is True


def test_check_datums_raises_when_station_datum_sheet_changed(monkeypatch):
    """NOAA periodically re-derives station datums from a new tidal epoch;
    a stale hardcoded config.py would then silently mis-convert every water
    level. check_datums exists specifically to catch that instead of
    letting it fail silently downstream."""
    drifted = dict(config.DATUMS_FT_STND)
    drifted["NAVD88"] = config.DATUMS_FT_STND["NAVD88"] + 0.5   # well outside tolerance
    monkeypatch.setattr(coops, "fetch_datums", lambda: drifted)
    with pytest.raises(RuntimeError, match="NAVD88"):
        coops.check_datums()


def test_fetch_forecast_frame_bias_correction_math(monkeypatch):
    """Pure arithmetic check of the bias-correction columns, independent of
    any network call: ofs_navd88_m = ofs_raw_m - bias, and nontidal_m
    (the wind/surge component) = ofs_navd88_m - pred_navd88_m."""
    idx = pd.date_range("2026-09-06", periods=3, freq="1h", tz="UTC")
    ofs = pd.Series([1.0, 1.2, 1.5], index=idx)
    pred = pd.Series([0.8, 0.9, 1.0], index=idx)
    monkeypatch.setattr(coops, "fetch_ofs_forecast", lambda start=None, hours=24: ofs)
    monkeypatch.setattr(coops, "fetch_predictions", lambda start=None, hours=24: pred)
    monkeypatch.setattr(coops, "recent_ofs_bias", lambda hours=48: 0.25)

    df = coops.fetch_forecast_frame()
    assert list(df["ofs_navd88_m"]) == pytest.approx([0.75, 0.95, 1.25])
    assert list(df["nontidal_m"]) == pytest.approx([-0.05, 0.05, 0.25])
    assert (df["ofs_bias_m"] == 0.25).all()


def test_fetch_forecast_frame_bias_correct_false_skips_bias(monkeypatch):
    idx = pd.date_range("2026-09-06", periods=2, freq="1h", tz="UTC")
    ofs = pd.Series([1.0, 1.2], index=idx)
    pred = pd.Series([0.8, 0.9], index=idx)
    monkeypatch.setattr(coops, "fetch_ofs_forecast", lambda start=None, hours=24: ofs)
    monkeypatch.setattr(coops, "fetch_predictions", lambda start=None, hours=24: pred)

    def _boom(hours=48):
        raise AssertionError("recent_ofs_bias should not be called when bias_correct=False")
    monkeypatch.setattr(coops, "recent_ofs_bias", _boom)

    df = coops.fetch_forecast_frame(bias_correct=False)
    assert (df["ofs_bias_m"] == 0.0).all()
    assert list(df["ofs_navd88_m"]) == pytest.approx([1.0, 1.2])
