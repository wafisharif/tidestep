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
