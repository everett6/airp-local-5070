"""The three flow-pressure tests (docs/PLAN_60_V2.md "Three flow-pressure tests", specs fixed 2026-10-01): R1
rebalancing pressure, M1 month-end Treasury returns, A1 Treasury auction cycle. CPU only, daily closes.

    python scripts/flow_tests.py --rules R1,M1,A1     # A1 needs scripts/auction_calendar.py first

Each rule is one registered trial: run it once. Results merge into results/daytrade_test.json next to the other
strategy tests. Pass (each): on its window after the source paper's sample, at 1 bp a side, the annualized Sharpe is
>= 0.5 with its 95% block-bootstrap CI above 0, AND the effect-specific difference has its CI on the stated side.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd
from daytrade_test import stats

from app.sandbox.calendar_fx import diff_ci
from app.sandbox.dsr import register
from app.sandbox.flows import (
    auction_windows,
    month_end_days,
    overlay,
    rebalance_stream,
    threshold_signal,
)
from app.sandbox.intraday import sharpe

OUT = BACKEND / "results" / "daytrade_test.json"
R1 = ("2023-03-20", "2026-09-25", "rebalance_threshold")
M1 = ("2019-01-02", "2026-08-31", "treasury_month_end")
A1 = ("2014-01-02", "2026-09-25", "treasury_auction_cycle")


def _by_year(r: pd.Series) -> dict[str, float]:
    return {str(y): round(sharpe(g), 2) for y, g in r.groupby(r.index.year) if len(g) > 20 and g.std() > 0}


def _diff(x: pd.Series, on: pd.Series) -> dict[str, Any]:
    """Mean of x on the marked days minus the other days, in bp a day, with its block-bootstrap 95% CI."""
    point, lo, hi = diff_ci(x, on, block=21, n=5000, seed=0, level=0.95)
    return {"point": round(point * 1e4, 2), "ci": [round(lo * 1e4, 2), round(hi * 1e4, 2)],
            "on_mean": round(float(x[on].mean()) * 1e4, 2), "off_mean": round(float(x[~on].mean()) * 1e4, 2),
            "on_days": int(on.sum()), "off_days": int((~on).sum())}


def r1_result(closes: pd.DataFrame, core: pd.Series, window: tuple[str, str] = R1[:2]) -> dict[str, Any]:
    ret = closes[["SPY", "IEF", "TLT"]].pct_change().dropna()
    a, b = window
    sig = threshold_signal(ret["SPY"], ret["IEF"])
    s = rebalance_stream(ret["SPY"], ret["IEF"], sig, 1e-4)
    res: dict[str, Any] = {"window": [a, b], "1bp": stats(s["ret"].loc[a:b]),
                           "3bp": stats(rebalance_stream(ret["SPY"], ret["IEF"], sig, 3e-4)["ret"].loc[a:b])}
    d = pd.DataFrame({"x": ret["SPY"] - ret["IEF"], "s": sig.shift(1)}).loc[a:b].dropna()
    d = d[d["s"] != 0]
    res["diff_bp_per_day"] = _diff(d["x"], d["s"] > 0)  # after stocks overweight minus after stocks underweight
    res["before_window"] = stats(s["ret"].loc["2007-01-03":"2023-03-17"])
    res["one_day_later"] = stats(rebalance_stream(ret["SPY"], ret["IEF"], sig, 1e-4, lag=2)["ret"].loc[a:b])
    tlt = rebalance_stream(ret["SPY"], ret["TLT"], threshold_signal(ret["SPY"], ret["TLT"]), 1e-4)
    res["tlt_bond_leg"] = stats(tlt["ret"].loc[a:b])
    res["by_year"] = _by_year(s["ret"].loc["2007-01-03":b])
    res["max_abs_w"] = round(float(s["w"].loc[a:b].abs().max()), 2)
    res["mean_abs_w"] = round(float(s["w"].loc[a:b].abs().mean()), 3)
    res["corr_with_core"] = round(float(s["ret"].loc[a:b].corr(core.reindex(s.loc[a:b].index))), 3)
    t, e = res["1bp"], res["diff_bp_per_day"]
    res["pass"] = bool(t["sharpe"] >= 0.5 and t["ci"][0] > 0 and e["ci"][1] < 0)
    return res


def _excess(closes: pd.DataFrame, rf_annual: pd.Series, asset: str) -> pd.Series:
    px = closes[asset].dropna()
    rf = rf_annual.reindex(px.index, method="ffill").fillna(0.0) / 252
    return (px.pct_change() - rf).dropna()


def m1_result(closes: pd.DataFrame, rf_annual: pd.Series, core: pd.Series, window: tuple[str, str] = M1[:2]
              ) -> dict[str, Any]:
    a, b = window
    ex = _excess(closes, rf_annual, "TLT")
    on = month_end_days(pd.DatetimeIndex(ex.index), 3)
    o = overlay(ex, on, 1e-4)
    res: dict[str, Any] = {"window": [a, b], "1bp": stats(o.loc[a:b]), "3bp": stats(overlay(ex, on, 3e-4).loc[a:b])}
    res["diff_bp_per_day"] = _diff(ex.loc[a:b], on.loc[a:b])
    res["before_window"] = stats(o.loc["2006-01-03":"2018-12-31"])
    res["diff_before_window"] = _diff(ex.loc["2006-01-03":"2018-12-31"], on.loc["2006-01-03":"2018-12-31"])
    ief = _excess(closes, rf_annual, "IEF")
    res["ief"] = stats(overlay(ief, month_end_days(pd.DatetimeIndex(ief.index), 3), 1e-4).loc[a:b])
    for n in (1, 5):
        res[f"last_{n}_days"] = stats(overlay(ex, month_end_days(pd.DatetimeIndex(ex.index), n), 1e-4).loc[a:b])
    res["by_year"] = _by_year(o.loc["2006-01-03":b])
    res["corr_with_core"] = round(float(o.loc[a:b].corr(core.reindex(o.loc[a:b].index))), 3)
    t, e = res["1bp"], res["diff_bp_per_day"]
    res["pass"] = bool(t["sharpe"] >= 0.5 and t["ci"][0] > 0 and e["ci"][0] > 0)
    return res


def a1_result(closes: pd.DataFrame, rf_annual: pd.Series, auctions: list[str], core: pd.Series,
              window: tuple[str, str] = A1[:2]) -> dict[str, Any]:
    a, b = window
    ex = _excess(closes, rf_annual, "TLT")
    post, pre = auction_windows(pd.DatetimeIndex(ex.index), auctions)
    o = overlay(ex, post, 1e-4)
    res: dict[str, Any] = {"window": [a, b], "1bp": stats(o.loc[a:b]), "3bp": stats(overlay(ex, post, 3e-4).loc[a:b])}

    def post_minus_pre(lo: str, hi: str) -> dict[str, Any]:
        sel = (post | pre).loc[lo:hi]
        x = ex.loc[lo:hi][sel]
        return _diff(x, post.loc[lo:hi][sel])
    res["diff_bp_per_day"] = post_minus_pre(a, b)  # post days minus pre days (the paper's measure)
    res["before_window"] = stats(o.loc["2009-01-02":"2013-12-31"])
    res["diff_before_window"] = post_minus_pre("2009-01-02", "2013-12-31")
    ief = _excess(closes, rf_annual, "IEF")
    res["ief"] = stats(overlay(ief, post.reindex(ief.index).fillna(False), 1e-4).loc[a:b])
    res["by_year"] = _by_year(o.loc["2009-01-02":b])
    res["corr_with_core"] = round(float(o.loc[a:b].corr(core.reindex(o.loc[a:b].index))), 3)
    m1 = overlay(ex, month_end_days(pd.DatetimeIndex(ex.index), 3), 1e-4)
    res["corr_with_m1"] = round(float(o.loc[a:b].corr(m1.loc[a:b])), 3)
    res["auction_clusters"] = int((post & ~post.shift(1, fill_value=False)).loc[a:b].sum())
    t, e = res["1bp"], res["diff_bp_per_day"]
    res["pass"] = bool(t["sharpe"] >= 0.5 and t["ci"][0] > 0 and e["ci"][0] > 0)
    return res


def run(name: str) -> dict[str, Any]:
    """One rule's one registered run on the real data."""
    from vol_target_b0 import tbill
    closes = pd.read_parquet(BACKEND / "data" / "trend" / "etf_closes.parquet")
    core = pd.read_parquet(BACKEND / "results" / "planner" / "track_returns.parquet")["core"]
    if name == "R1":
        res, (a, b, trial) = r1_result(closes, core), R1
    elif name == "M1":
        res, (a, b, trial) = m1_result(closes, tbill(), core), M1
    else:
        cal = pd.read_csv(BACKEND / "data" / "macro" / "treasury_auctions.csv", dtype=str)
        res, (a, b, trial) = a1_result(closes, tbill(), cal["date"].tolist(), core), A1
    t, e = res["1bp"], res["diff_bp_per_day"]
    register({"trial": trial, "date": time.strftime("%Y-%m-%d"), "kind": "calendar", "sharpe_ann": t["sharpe"],
              "window": f"{a}..{b}", "result": "pass" if res["pass"] else "fail"})
    print(f"{name} {trial}: Sharpe {t['sharpe']} CI {t['ci']} CAGR {t['cagr']:.1%}; effect {e['point']} bp/day "
          f"CI {e['ci']} -> {'PASS' if res['pass'] else 'FAIL'}", flush=True)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rules", required=True, help="e.g. R1,M1,A1")
    a = ap.parse_args()
    out: dict[str, Any] = json.loads(OUT.read_text()) if OUT.exists() else {}
    for name in a.rules.split(","):
        if name not in ("R1", "M1", "A1"):
            raise SystemExit(f"unknown rule {name}")
        if name in out:
            raise SystemExit(f"{name} was already run (one trial each); see {OUT.name}")
        out[name] = run(name)
        OUT.write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
