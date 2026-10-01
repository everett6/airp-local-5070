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


def test_d5_longs_losers_shorts_winners():
    rows = []
    for i in range(20):  # day 1 sets yesterday's close = 100 for all
        rows.append({"ts": pd.Timestamp("2024-07-01 15:30", tz="America/New_York"), "symbol": f"S{i}", "open": 100.0,
                     "close": 100.0})
    for i in range(20):  # day 2: ROD3 = i% (S0 worst); the losers rise 1% in the last half hour, the winners fall 1%
        rod = 100.0 * (1 + i / 100)
        lh = 1.01 if i < 2 else 0.99 if i >= 18 else 1.0
        rows += [{"ts": pd.Timestamp("2024-07-02 14:30", tz="America/New_York"), "symbol": f"S{i}", "open": 100.0,
                  "close": rod},
                 {"ts": pd.Timestamp("2024-07-02 15:30", tz="America/New_York"), "symbol": f"S{i}", "open": rod,
                  "close": rod * lh}]
    r = I.d5_eod_reversal(pd.DataFrame(rows), cost=0.0001)
    assert len(r) == 1 and r["names"].iloc[0] == 20
    assert abs(r["ret"].iloc[0] - (0.5 * 0.01 + 0.5 * 0.01 - 0.0002)) < 1e-9


def test_d5_skips_half_days_by_trade_count():
    rows = []
    for day, n in (("2024-07-02", 5000), ("2024-07-03", 6)):  # 3 Jul: after-hours prints only
        for i in range(20):
            rows += [{"ts": pd.Timestamp(f"{day} 14:30", tz="America/New_York"), "symbol": f"S{i}", "open": 100.0,
                      "close": 100.0 + i, "n": n},
                     {"ts": pd.Timestamp(f"{day} 15:30", tz="America/New_York"), "symbol": f"S{i}", "open": 100.0,
                      "close": 101.0, "n": n}]
    df = pd.DataFrame(rows)
    assert len(I.d5_eod_reversal(df.drop(columns="n"), cost=0.0)) == 1  # without counts the half day would trade
    assert len(I.d5_eod_reversal(df, cost=0.0)) == 0


def test_d6_box_long_from_the_bottom_to_the_middle():
    d0 = day("2024-03-04", {1559: 100.0}, highs={1000: 102.0}, lows={1100: 98.0})  # box 98..102, middle 100
    d1 = day("2024-03-05", {1000: 98.5, 1100: 100.0})  # 98.5 <= 99 -> long at 98.5; back at 100 -> out
    r = I.d6_box_theory(pd.concat([d0, d1]), cost=0.0001)
    assert list(r.index.strftime("%Y-%m-%d")) == ["2024-03-05"]
    assert abs(r.iloc[0] - (100.0 / 98.5 - 1 - 0.0002)) < 1e-9


def test_d6_gap_redraws_the_box_from_the_first_half_hour():
    d0 = day("2024-03-04", {1559: 100.0}, highs={1000: 102.0}, lows={1100: 98.0})
    d1 = day("2024-03-05", {1000: 112.0, 1200: 110.0}, default=110.0, highs={940: 112.0}, lows={950: 108.0})
    r = I.d6_box_theory(pd.concat([d0, d1]), cost=0.0)  # box 108..112: 112 -> short, 110 (middle) -> out
    assert abs(r.iloc[0] - (1 - 110.0 / 112.0)) < 1e-9


def test_d6_no_trade_in_the_middle():
    d0 = day("2024-03-04", {1559: 100.0}, highs={1000: 102.0}, lows={1100: 98.0})
    r = I.d6_box_theory(pd.concat([d0, day("2024-03-05", {1000: 100.5})]), cost=0.0001)
    assert r.iloc[0] == 0.0


def test_d7_longs_the_slot_winners():
    rows = []
    days = pd.bdate_range("2024-07-01", periods=17)
    for n, dt in enumerate(days):
        for i in range(20):
            r = i / 1000 if n < 16 else (0.01 if i >= 18 else -0.01 if i < 2 else 0.0)
            rows.append({"ts": pd.Timestamp(f"{dt.date()} 15:30", tz="America/New_York"), "symbol": f"S{i}",
                         "open": 100.0, "close": 100.0 * (1 + r)})
    r = I.d7_periodicity(pd.DataFrame(rows), cost=0.0001)
    assert len(r) == 2 and r["names"].iloc[-1] == 20  # days 16 and 17 have 15+ past days
    assert abs(r["ret"].iloc[-1] - (0.01 - 0.0002)) < 1e-9


def test_darvas_box_top_bottom_and_breakout():
    import numpy as np
    h = np.array([100.0] * 5 + [110, 108, 108, 108, 108, 108, 112])
    lo = np.array([99.0] * 5 + [105, 101, 100, 102, 103, 103, 108])
    c = np.array([100.0] * 5 + [109, 104, 103, 104, 105, 105, 111])
    brk, done = I.darvas_signals(h, lo, c, year=5)
    assert done == {10: 100.0}  # top 110 fixed on day 8; bottom 100 (day 7) held 3 days by day 10
    assert brk == {11: (110.0, 100.0)}


def test_d8_buys_the_breakout_and_sells_below_the_stop():
    n, p = 270, 260
    rows = []
    for i, dt in enumerate(pd.bdate_range("2020-01-01", periods=n)):
        o = h = lo = c = 100.0
        if i == p:
            h, c = 105.0, 104.0
        elif p < i <= p + 5:
            h, lo, c = 104.0, {p + 1: 101.0, p + 2: 100.0}.get(i, 102.0), 103.0
        elif i == p + 6:
            h, c = 106.5, 106.0  # breakout above 105
        elif i == p + 7:
            o, h, c = 106.0, 107.5, 107.0
        elif i == p + 8:
            o, h, lo, c = 107.0, 107.0, 99.0, 99.0  # below the 100 stop
        elif i == p + 9:
            o = h = lo = c = 98.0
        rows.append({"Date": dt, "Ticker": "A", "Open": o, "High": h, "Low": lo, "Close": c})
        rows.append({"Date": dt, "Ticker": "SPY", "Open": 100.0, "High": 100.0, "Low": 100.0, "Close": 100.0})
    r = I.d8_darvas(pd.DataFrame(rows), ["A"], cost=0.001, slots=20)
    days = pd.bdate_range("2020-01-01", periods=n)
    w = 1 / 20
    assert abs(r.loc[days[p + 7], "ret"] - (w * (107 / 106 - 1) - w * 0.001)) < 1e-12
    assert abs(r.loc[days[p + 8], "ret"] - w * (99 / 107 - 1)) < 1e-12
    assert abs(r.loc[days[p + 9], "ret"] - (w * (98 / 99 - 1) - w * 0.001)) < 1e-12
    assert r.loc[days[p + 9], "held"] == 0 and r["ret"].iloc[: p + 6].abs().max() == 0


def test_d9_longs_overnight_losers_shorts_winners():
    rows = []
    for i in range(20):  # day 1 close 100; day 2 opens at 100 + i (S19 gapped up most), losers bounce, winners fade
        rows.append({"Date": pd.Timestamp("2024-07-01"), "Ticker": f"S{i}", "Open": 100.0, "Close": 100.0})
        o = 100.0 + i
        rows.append({"Date": pd.Timestamp("2024-07-02"), "Ticker": f"S{i}", "Open": o,
                     "Close": o * (1.02 if i < 2 else 0.98 if i >= 18 else 1.0)})
    r = I.d9_open_reversal(pd.DataFrame(rows), [f"S{i}" for i in range(20)], cost=0.0001)
    assert len(r) == 1 and r["names"].iloc[0] == 20
    assert abs(r["ret"].iloc[0] - (0.5 * 0.02 + 0.5 * 0.02 - 0.0002)) < 1e-9


def test_d10_uses_the_premarket_price_not_the_open():
    rows, pre = [], []
    for i in range(20):
        rows.append({"Date": pd.Timestamp("2024-07-01"), "Ticker": f"S{i}", "Open": 100.0, "Close": 100.0})
        # the official opens are all 100 (no D9 signal); pre-market prices rank S19 highest
        rows.append({"Date": pd.Timestamp("2024-07-02"), "Ticker": f"S{i}", "Open": 100.0,
                     "Close": 102.0 if i < 2 else 98.0 if i >= 18 else 100.0})
        pre.append({"Date": pd.Timestamp("2024-07-02"), "Ticker": f"S{i}", "pre": 100.0 + i})
    syms = [f"S{i}" for i in range(21)]
    pre.append({"Date": pd.Timestamp("2024-07-02"), "Ticker": "S20", "pre": 50.0})  # no daily bars: ignored
    r = I.d10_premarket_reversal(pd.DataFrame(rows), pd.DataFrame(pre), syms, cost=0.0001)
    assert len(r) == 1 and r["names"].iloc[0] == 20
    assert abs(r["ret"].iloc[0] - (0.02 - 0.0002)) < 1e-9
