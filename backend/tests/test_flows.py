"""R1, M1 and A1 (PLAN_60_V2 "Three flow-pressure tests"): the signals on small synthetic inputs."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import auction_calendar

from app.sandbox import flows as F

DAYS = pd.bdate_range("2026-01-01", "2026-03-31")


def test_threshold_signal_matches_the_papers_example_and_resets() -> None:
    idx = DAYS[:4]
    stock, bond = pd.Series([0.10, 0.0, 0.0, -0.10], index=idx), pd.Series(0.0, index=idx)
    one = F.threshold_signal(stock, bond, deltas=[0.02])
    # stocks +10%, bonds flat: 66 / 106 = 62.26% (the paper's footnote 8); the 2% band is hit, so it resets
    assert abs(one.iloc[0] - (66 / 106 - 0.60)) < 1e-12 and one.iloc[1] == one.iloc[2] == 0
    wide = F.threshold_signal(stock, bond, deltas=[0.05])  # never reset: the deviation stays, then unwinds
    assert wide.iloc[1] == wide.iloc[0] and abs(wide.iloc[3] - (59.4 / 99.4 - 0.60)) < 1e-12
    both = F.threshold_signal(stock, bond, deltas=[0.02, 0.05])
    assert np.allclose(both, (one + wide) / 2)
    assert len(F.DELTAS) == 26 and F.DELTAS[0] == 0 and F.DELTAS[-1] == 0.025


def test_rebalance_stream_trades_against_the_drift_one_day_later() -> None:
    idx = DAYS[:3]
    stock, bond = pd.Series([0.10, -0.02, 0.01], index=idx), pd.Series([0.0, 0.01, 0.0], index=idx)
    sig = pd.Series([0.015, 0.0, 0.0], index=idx)
    s = F.rebalance_stream(stock, bond, sig, cost=0.0)
    assert list(s["w"]) == [0.0, -1.0, 0.0]  # today's signal earns tomorrow's spread: no look-ahead
    assert np.allclose(s["ret"], [0.0, 0.03, 0.0])
    c = F.rebalance_stream(stock, bond, sig, cost=1e-4)
    assert np.allclose(s["ret"] - c["ret"], [0.0, 2e-4, 2e-4])  # in and out, two legs each
    assert list(F.rebalance_stream(stock, bond, sig, cost=0.0, lag=2)["w"]) == [0.0, 0.0, -1.0]


def test_month_end_days_last_three_and_no_cut_off_month() -> None:
    m = F.month_end_days(DAYS, 3)
    assert [d.date().isoformat() for d in DAYS[m.to_numpy()]] == [
        "2026-01-28", "2026-01-29", "2026-01-30", "2026-02-25", "2026-02-26", "2026-02-27"]  # March is the last month
    assert F.month_end_days(DAYS, 1).sum() == 2


def test_auction_windows_cluster_and_pre_wins() -> None:
    idx = pd.bdate_range("2026-02-02", periods=30)
    # 10-year on Tue 10 Feb, 30-year on Wed 11 Feb: one cluster dated 11 Feb; then a weekend-dated auction
    post, pre = F.auction_windows(idx, ["2026-02-10", "2026-02-11", "2026-02-21"])

    def iso(m: pd.Series) -> list[str]:
        return [d.date().isoformat() for d in idx[m.to_numpy()]]
    assert iso(pre)[:5] == ["2026-02-05", "2026-02-06", "2026-02-09", "2026-02-10", "2026-02-11"]
    # the second auction (Sat 21 Feb) moves to Mon 23 Feb; its pre window (17..23) takes 17 and 18 from the first post
    assert iso(post)[:3] == ["2026-02-12", "2026-02-13", "2026-02-16"] and "2026-02-17" not in iso(post)
    assert iso(pre)[5:] == ["2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-23"]
    assert not (post & pre).any() and post.sum() == 3 + 5


def test_overlay_costs_on_entry_and_exit() -> None:
    idx = DAYS[:6]
    ex = pd.Series(0.01, index=idx)
    on = pd.Series([False, True, True, False, True, False], index=idx)
    o = F.overlay(ex, on, cost=1e-4)
    assert np.allclose(o, [0, 0.0099, 0.0099, 0, 0.0098, 0])


def test_auction_calendar_keeps_nominal_10_and_30_year_only() -> None:
    items = [{"securityType": "Note", "originalSecurityTerm": "10-Year", "securityTerm": "9-Year 11-Month",
              "auctionDate": "2026-09-09T00:00:00", "tips": "No", "floatingRate": "No", "reopening": "Yes", "cusip": "A"},
             {"securityType": "Note", "originalSecurityTerm": "10-Year", "securityTerm": "10-Year",
              "auctionDate": "2026-07-23T00:00:00", "tips": "Yes", "floatingRate": "No", "cusip": "B"},
             {"securityType": "Note", "originalSecurityTerm": "7-Year", "securityTerm": "7-Year",
              "auctionDate": "2026-07-28T00:00:00", "tips": "No", "floatingRate": "No", "cusip": "C"},
             {"securityType": "Note", "originalSecurityTerm": "10-Year", "securityTerm": "10-Year",
              "auctionDate": "", "tips": "No", "floatingRate": "No", "cusip": "D"}]
    got = auction_calendar.rows(items, "Note")
    assert [(r["date"], r["cusip"], r["reopening"]) for r in got] == [("2026-09-09", "A", "Yes")]
    assert auction_calendar.rows(items, "Bond") == []


def _synthetic() -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    idx = pd.bdate_range("2006-01-03", "2026-09-25")
    rng = np.random.default_rng(0)
    rets = pd.DataFrame(rng.normal(0.0003, 0.01, (len(idx), 3)), index=idx, columns=["SPY", "IEF", "TLT"])
    closes = 100 * (1 + rets).cumprod()
    rf = pd.Series(0.02, index=idx)
    core = pd.Series(rng.normal(0.0005, 0.01, len(idx)), index=idx)
    return closes, rf, core


def test_result_functions_run_end_to_end_on_random_prices_and_do_not_pass() -> None:
    """The three runners on noise: every reported block is there, the windows are right, and nothing passes."""
    import flow_tests as T
    closes, rf, core = _synthetic()
    r1 = T.r1_result(closes, core)
    assert r1["window"] == ["2023-03-20", "2026-09-25"] and r1["pass"] is False
    assert {"1bp", "3bp", "diff_bp_per_day", "before_window", "one_day_later", "tlt_bond_leg", "by_year",
            "max_abs_w", "corr_with_core"} <= set(r1)
    assert r1["1bp"]["days"] == len(closes.loc["2023-03-20":"2026-09-25"])
    m1 = T.m1_result(closes, rf, core)
    assert m1["pass"] is False and m1["diff_bp_per_day"]["on_days"] == 3 * 92  # 92 full months, Jan 2019 - Aug 2026
    auctions = [d.date().isoformat() for d in pd.bdate_range("2009-01-14", "2026-09-10", freq="BMS") + pd.Timedelta(days=8)]
    a1 = T.a1_result(closes, rf, auctions, core)
    assert a1["pass"] is False and a1["auction_clusters"] > 140 and abs(a1["corr_with_m1"]) < 0.5
    e = a1["diff_bp_per_day"]
    assert e["on_days"] > 600 and e["off_days"] > 600  # post days against pre days only
