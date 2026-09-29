import pandas as pd

from app.sandbox import intraday as I


def day(date: str, path: dict[int, float], default: float = 100.0, lows: dict[int, float] | None = None,
        highs: dict[int, float] | None = None, end: int = 1559) -> pd.DataFrame:
    """Minute bars 09:30..end; close at minute hm is path.get(hm) else the last one set."""
    rows, px = [], default
    t = pd.Timestamp(f"{date} 09:30", tz="America/New_York")
    while t.hour * 100 + t.minute <= end:
        hm = t.hour * 100 + t.minute
        o = px
        px = path.get(hm, px)
        lo = (lows or {}).get(hm, min(o, px))
        hi = (highs or {}).get(hm, max(o, px))
        rows.append({"ts": t, "open": o, "high": hi, "low": lo, "close": px, "volume": 1})
        t += pd.Timedelta(minutes=1)
    return pd.DataFrame(rows)


def test_d1_uses_previous_close_and_the_last_half_hour():
    d0 = day("2024-03-04", {1559: 100.0})
    d1 = day("2024-03-05", {959: 101.0, 1529: 102.0, 1559: 104.04})  # up by 10:00 -> long 15:30..15:59: +2%
    d2 = day("2024-03-06", {959: 103.0, 1559: 100.98})                # down vs 104.04 -> short from 103
    r = I.d1_intraday_momentum(pd.concat([d0, d1, d2]), cost=0.0001)
    assert list(r.index.strftime("%Y-%m-%d")) == ["2024-03-05", "2024-03-06"]
    assert abs(r.iloc[0] - (0.02 - 0.0002)) < 1e-9 and abs(r.iloc[1] - (1 - 100.98 / 103.0 - 0.0002)) < 1e-9


def test_d1_skips_half_days():
    d0 = day("2024-11-27", {1559: 100.0})
    half = day("2024-11-29", {959: 101.0}, end=1259)
    assert I.d1_intraday_momentum(pd.concat([d0, half]), 0.0).empty


def test_d2_long_exits_at_close_or_stop():
    up = day("2024-03-05", {934: 101.0, 1559: 103.02}, lows={930: 99.5})  # first bar up; no stop hit
    r = I.d2_orb5(up, cost=0.0)
    assert abs(r.iloc[0] - (103.02 / 101.0 - 1)) < 1e-9
    stopped = day("2024-03-06", {934: 101.0, 1000: 98.0, 1559: 105.0}, lows={930: 99.5, 1000: 97.0})
    r2 = I.d2_orb5(stopped, cost=0.0)
    assert abs(r2.iloc[0] - (99.5 / 101.0 - 1)) < 1e-9  # stopped at the first bar's low, not the later rally


def test_d2_short_and_flat_first_bar():
    down = day("2024-03-05", {934: 99.0, 1559: 97.02}, highs={930: 100.5})
    assert abs(I.d2_orb5(down, 0.0).iloc[0] - (1 - 97.02 / 99.0)) < 1e-9
    flat = day("2024-03-06", {1559: 101.0})
    assert I.d2_orb5(flat, 0.0).empty


def test_sharpe_ci_brackets_the_estimate():
    r = pd.Series([0.001, -0.0005, 0.0008, 0.0002] * 200)
    lo, hi = I.block_ci(r)
    assert lo < I.sharpe(r) < hi


def test_d4_uses_the_rest_of_day_return():
    d0 = day("2024-03-04", {1559: 100.0})
    d1 = day("2024-03-05", {959: 99.0, 1529: 102.0, 1559: 104.04})  # down at 10:00 but up by 15:29 -> long
    r = I.d4_rod_momentum(pd.concat([d0, d1]), cost=0.0001)
    assert abs(r.iloc[0] - (0.02 - 0.0002)) < 1e-9


def test_d3_breakout_then_vwap_stop():
    quiet = [day(str(d.date()), {931: 100.5}) for d in pd.bdate_range("2024-02-01", periods=15)]
    test = day("2024-02-22", {931: 100.5, 959: 102.0, 1159: 103.0, 1229: 100.5})
    r = I.d3_noise_vwap(pd.concat([*quiet, test]), cost=0.0001)
    assert list(r.index.strftime("%Y-%m-%d"))[-1] == "2024-02-22" and (r.iloc[:-1] == 0).all()
    # long at the 10:00 open (102) after 102 > 100.5 * 1.005; out at the 12:30 open (100.5), below the VWAP stop
    assert abs(r.iloc[-1] - (100.5 / 102.0 - 1 - 0.0002)) < 1e-9
