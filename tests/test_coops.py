from datetime import datetime, timedelta, timezone

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


# --- _get(), _window(), fetch_observed()'s product choice, recent_ofs_bias() ---
# None of these had been exercised before this pass -- only _get()'s ERROR
# path was tested (above). The success path, the request-window builder,
# and fetch_observed()'s water_level/hourly_height product switch are all
# real logic a subtle bug could hide in, and fetch_observed()'s product
# choice specifically is exactly what the real Stage 9 --dates run (2026-
# 09-14, docs/STATUS.md fourteenth pass) depended on to get usable data
# for dates 11+ months in the past.

def test_get_merges_base_params_and_returns_body_on_success(monkeypatch):
    captured = {}

    def fake_get(url, params, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return _FakeResponse({"data": [{"t": "2026-09-06 00:00", "v": "1.0"}]})
    monkeypatch.setattr(coops.requests, "get", fake_get)

    body = coops._get({"product": "ofs_water_level", "range": 24})
    assert body == {"data": [{"t": "2026-09-06 00:00", "v": "1.0"}]}
    assert captured["url"] == coops.API
    assert captured["timeout"] == coops.TIMEOUT
    # base params (station/datum/units/...) must survive, with the caller's
    # own params merged in on top
    assert captured["params"]["station"] == config.STATION_ID
    assert captured["params"]["datum"] == "MLLW"
    assert captured["params"]["product"] == "ofs_water_level"
    assert captured["params"]["range"] == 24


def test_get_caller_params_override_base_params(monkeypatch):
    """A caller-supplied key must win over the base dict's default (the
    dict-merge order in _get is `{**base, **params}` -- easy to
    accidentally flip while refactoring, which would silently make every
    caller-specified override a no-op)."""
    captured = {}
    monkeypatch.setattr(coops.requests, "get",
                        lambda url, params, timeout: (captured.update(params),
                                                       _FakeResponse({"data": []}))[1])
    coops._get({"datum": "STND"})
    assert captured["datum"] == "STND"


def test_window_formats_begin_date_and_zeroes_sub_hour_fields():
    start = datetime(2026, 9, 6, 14, 37, 22, tzinfo=timezone.utc)
    w = coops._window(start, 24)
    assert w["begin_date"] == "20260906 14:00"   # minute/second dropped, not just displayed as 0
    assert w["range"] == 24


def test_window_defaults_to_now_when_start_is_none(monkeypatch):
    fixed = datetime(2026, 9, 6, 8, 0, tzinfo=timezone.utc)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed
    monkeypatch.setattr(coops, "datetime", _FixedDatetime)
    w = coops._window(None, 6)
    assert w["begin_date"] == "20260906 08:00"


def test_fetch_ofs_forecast_uses_ofs_water_level_product(monkeypatch):
    captured = {}

    def fake_get(params):
        captured.update(params)
        return {"data": [{"t": "2026-09-06 00:00", "v": "1.0"}]}
    monkeypatch.setattr(coops, "_get", fake_get)
    s = coops.fetch_ofs_forecast(datetime(2026, 9, 6, tzinfo=timezone.utc), hours=6)
    assert captured["product"] == "ofs_water_level"
    assert captured["range"] == 6
    assert not s.empty


def test_fetch_predictions_uses_h_interval(monkeypatch):
    captured = {}

    def fake_get(params):
        captured.update(params)
        return {"predictions": [{"t": "2026-09-06 00:00", "v": "1.0"}]}
    monkeypatch.setattr(coops, "_get", fake_get)
    coops.fetch_predictions(datetime(2026, 9, 6, tzinfo=timezone.utc), hours=6)
    assert captured["product"] == "predictions"
    assert captured["interval"] == "h"


def test_fetch_observed_uses_water_level_for_a_recent_date(monkeypatch):
    """Within the last 28 days: water_level (fast, ~30-day-covering
    preliminary product)."""
    captured = {}

    def fake_get(params):
        captured.update(params)
        return {"data": [{"t": "2026-09-06 00:00", "v": "1.0"}]}
    monkeypatch.setattr(coops, "_get", fake_get)
    recent = datetime.now(timezone.utc) - timedelta(days=5)
    coops.fetch_observed(recent, 24)
    assert captured["product"] == "water_level"


def test_fetch_observed_uses_hourly_height_for_an_older_date(monkeypatch):
    """Older than 28 days: hourly_height (the verified historical product
    -- water_level doesn't cover this far back). This exact branch is what
    the real 2021-10-26/2025-10-12 Stage 9 --dates run (docs/STATUS.md
    fourteenth pass) needed to get real data for dates 11+ months old."""
    captured = {}

    def fake_get(params):
        captured.update(params)
        return {"data": [{"t": "2021-10-26 00:00", "v": "1.0"}]}
    monkeypatch.setattr(coops, "_get", fake_get)
    old = datetime.now(timezone.utc) - timedelta(days=60)
    coops.fetch_observed(old, 24)
    assert captured["product"] == "hourly_height"


def test_fetch_observed_boundary_is_inclusive_of_28_days(monkeypatch):
    """Exactly 28 days ago: the code's own condition is `start > now - 28d`,
    so a start exactly on the boundary falls to the OLDER-date branch, not
    the recent one -- pin that exact behavior so it can't drift silently."""
    captured = {}
    monkeypatch.setattr(coops, "_get", lambda params: (captured.update(params),
                                                        {"data": []})[1])
    now = datetime.now(timezone.utc)
    exactly_28_days_ago = (now - timedelta(days=28)).replace(
        hour=now.hour, minute=0, second=0, microsecond=0)
    coops.fetch_observed(exactly_28_days_ago, 24)
    assert captured["product"] == "hourly_height"


def test_recent_ofs_bias_computes_mean_difference(monkeypatch):
    idx = pd.date_range("2026-09-06", periods=3, freq="1h", tz="UTC")
    obs = pd.Series([1.0, 1.0, 1.0], index=idx)
    ofs = pd.Series([1.2, 1.3, 1.1], index=idx)   # mean diff = 0.2
    monkeypatch.setattr(coops, "fetch_observed", lambda start, hours: obs)
    monkeypatch.setattr(coops, "fetch_ofs_forecast", lambda start, hours: ofs)
    assert coops.recent_ofs_bias(hours=3) == pytest.approx(0.2, abs=1e-9)


def test_recent_ofs_bias_returns_zero_when_no_overlapping_data(monkeypatch):
    """Graceful degradation, not a crash or a NaN leaking downstream into
    fetch_forecast_frame's bias-corrected column, if observed/OFS series
    don't overlap at all (e.g. a data gap)."""
    idx1 = pd.date_range("2026-09-06", periods=2, freq="1h", tz="UTC")
    idx2 = pd.date_range("2026-09-08", periods=2, freq="1h", tz="UTC")   # no overlap
    monkeypatch.setattr(coops, "fetch_observed",
                        lambda start, hours: pd.Series([1.0, 1.0], index=idx1))
    monkeypatch.setattr(coops, "fetch_ofs_forecast",
                        lambda start, hours: pd.Series([1.0, 1.0], index=idx2))
    assert coops.recent_ofs_bias(hours=48) == 0.0


def test_fetch_datums_hits_the_metadata_api_and_maps_name_to_value(monkeypatch):
    """fetch_datums() is a separate endpoint (the mdapi station metadata
    API, not the datagetter _get() every other fetch_* uses) -- its own
    URL-building and response-shape handling had never been exercised,
    only check_datums() with fetch_datums() itself mocked out."""
    captured = {}

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return _FakeResponse({"datums": [
            {"name": "MLLW", "value": 12.88}, {"name": "NAVD88", "value": 17.09}]})
    monkeypatch.setattr(coops.requests, "get", fake_get)
    d = coops.fetch_datums()
    assert d == {"MLLW": 12.88, "NAVD88": 17.09}
    assert captured["url"] == f"{coops.MDAPI}/{config.STATION_ID}/datums.json"
    assert captured["timeout"] == coops.TIMEOUT
