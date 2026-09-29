"""Free-source bar parsers, key lookup and the cross-check comparison (no network)."""
import pandas as pd
import pytest

from app.data_ingestion import bars


def test_parse_binance_dates_by_utc_open_day():
    k = [[1758672000000, "112000.1", "113500", "111000", "113000.5", "1234.5", 1758758399999],
         [1758758400000, "113000.5", "114000", "112500", "113800", "999", 1758844799999]]
    df = bars.parse_binance(k)
    assert list(df.index.strftime("%Y-%m-%d")) == ["2025-09-24", "2025-09-25"]
    assert df.loc["2025-09-24", "Close"] == 113000.5 and list(df.columns) == bars.COLS


def test_parse_alpaca_and_polygon_date_by_new_york_day():
    a = bars.parse_alpaca([{"t": "2026-09-24T04:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}])
    assert a.index[0] == pd.Timestamp("2026-09-24") and a["Close"].iloc[0] == 1.5
    ms = int(pd.Timestamp("2026-09-24 00:00", tz="America/New_York").timestamp() * 1000)
    p = bars.parse_polygon([{"t": ms, "o": 1, "h": 2, "l": 0.5, "c": 1.6, "v": 10}])
    assert p.index[0] == pd.Timestamp("2026-09-24") and p["Close"].iloc[0] == 1.6


def test_keyed_sources_refuse_without_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(bars, "BACKEND", tmp_path)  # no backend/.env
    for n in ("ALPACA_API_KEY_ID", "APCA_API_KEY_ID", "POLYGON_API_KEY", "MASSIVE_API_KEY"):
        monkeypatch.delenv(n, raising=False)
    with pytest.raises(bars.NoKeyError):
        bars.alpaca_daily("SPY", pd.Timestamp("2026-01-01").date(), pd.Timestamp("2026-02-01").date())
    with pytest.raises(bars.NoKeyError):
        bars.polygon_daily("SPY", pd.Timestamp("2026-01-01").date(), pd.Timestamp("2026-02-01").date())
    (tmp_path / ".env").write_text("# comment\nPOLYGON_API_KEY='abc'\n")
    assert bars.key("POLYGON_API_KEY", "MASSIVE_API_KEY") == "abc"


def test_compare_flags_mixed_adjustment():
    idx = pd.bdate_range("2026-01-01", periods=60)
    raw = pd.Series(range(100, 160), index=idx, dtype=float)
    same = raw * 1.0001
    r = bars.compare(raw, same)
    assert r["days"] == 60 and r["max_abs_diff"] < 2e-4 and abs(r["ratio_drift"]) < 1e-9
    adjusted = raw * pd.Series([0.99] * 30 + [1.0] * 30, index=idx)  # a dividend adjustment before day 30
    assert abs(bars.compare(adjusted, raw)["ratio_drift"]) > 0.005
