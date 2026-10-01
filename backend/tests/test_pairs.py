import numpy as np
import pandas as pd

from app.sandbox import pairs as P


def test_select_pairs_picks_the_closest_same_sector_pair():
    idx = pd.bdate_range("2020-01-01", periods=30)
    base = np.linspace(100, 110, 30)
    px = pd.DataFrame({"A": base, "B": base * 1.001 + np.sin(np.arange(30)) * 0.01, "C": base[::-1], "D": base},
                      index=idx)
    px.loc[idx[3], "D"] = np.nan  # missing -> left out
    got = P.select_pairs(px, {"A": "x", "B": "x", "C": "x", "D": "x"}, n=1)
    assert [(a, b) for a, b, _ in got] == [("A", "B")]
    assert P.select_pairs(px, {"A": "x", "B": "y", "C": "z"}, n=1) == []  # no same-sector pair


def test_trade_pair_opens_next_close_and_closes_on_the_cross():
    pa = np.array([100, 100, 104, 103, 100, 100], float)  # a jumps dear at t=2
    pb = np.array([100, 100, 100, 100, 100, 100], float)
    pnl = P.trade_pair(pa, pb, sd=0.01, cost=0.001)
    # signal at t=2 (spread +0.04 > 0.02), open at t=3's close (short a at 103), cross at t=4 (a back to 100)
    assert pnl[:3].sum() == 0
    assert abs(pnl[3] + 0.002) < 1e-12
    assert abs(pnl[4] - (-(100 / 103 - 1) - 0.002)) < 1e-12 and pnl[5] == 0


def test_trade_pair_closes_at_period_end():
    pa = np.array([100, 105, 106, 107], float)
    pb = np.full(4, 100.0)
    pnl = P.trade_pair(pa, pb, sd=0.01, cost=0.0)
    assert abs(pnl.sum() - (-(107 / 106 - 1))) < 1e-12  # opened at t=2 (short a), closed at the end


def test_book_averages_overlapping_portfolios():
    idx = pd.bdate_range("2019-01-01", periods=300)
    rng = np.random.default_rng(0)
    common = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300)))
    px = pd.DataFrame({f"S{i}": common * np.exp(rng.normal(0, 0.005, 300)) for i in range(4)}, index=idx)
    r = P.pairs_ggr(px, {f"S{i}": "x" for i in range(4)}, cost=0.0, form=60, trade=40, n=2)
    assert r["portfolios"].max() >= 2 and r.index.min() > idx[59] and r["ret"].notna().all()
