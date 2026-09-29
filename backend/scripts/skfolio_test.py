"""skfolio test (docs/PLAN_60_V2.md "skfolio test", spec fixed before the run).

    python scripts/skfolio_test.py

Arm S: B0's rebalance days and trend rule, but the weights among SPY and the crypto assets whose trend is on come from
skfolio RiskBudgeting on CVaR (beta 0.95, equal budgets, long only), fitted on the trailing 252 daily returns (at least
120), scaled to 0.98. No crypto cap. Pass: vol-matched CAGR above B0 AND the 90% block-bootstrap CI of the Sharpe
difference above 0. Plus a descriptive copula stress test of the frozen book (no trial). Writes
results/skfolio_test.json and registers one trial.
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import cvxpy as cp
import numpy as np
import pandas as pd
from drawdown_brakes import brake
from master_portfolio import load_crypto
from skfolio import RiskMeasure
from skfolio.distribution import VineCopula
from skfolio.optimization import RiskBudgeting
from trend_sleeve import block_boot, master_b0, sharpe, stats

from app.portfolio.master import MasterConfig, allocate, crypto_state, simulate_weights
from app.sandbox.dsr import register
from app.sandbox.events import Prices

START, END = "2018-01-02", "2026-09-26"
LOOKBACK, MIN_OBS, INVESTED = 252, 120, 0.98
FROZEN = {"SPY": 0.78, "BTC-USD": 0.1159, "ETH-USD": 0.0841}


def risk_parity(close: pd.DataFrame, day: pd.Timestamp, assets: list[str]) -> dict[str, float] | None:
    """CVaR risk parity on the trailing returns of `assets` up to `day` (days where all of them have a close)."""
    if len(assets) == 1:
        return {assets[0]: INVESTED}
    x = close[assets].loc[:day].dropna().pct_change().dropna().tail(LOOKBACK)
    if len(x) < MIN_OBS:
        return None
    m = RiskBudgeting(risk_measure=RiskMeasure.CVAR, cvar_beta=0.95)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            m.fit(x.to_numpy())
        except cp.SolverError:  # 2 of 194 fits (2018-03-01, 2024-04-08): B0's weights, as with too little data
            return None
    w = np.clip(np.asarray(m.weights_, dtype=float), 0.0, None)
    return {a: INVESTED * float(v) / float(w.sum()) for a, v in zip(assets, w, strict=True)}


def arm_s() -> tuple[pd.Series, dict[str, float]]:
    px = Prices.from_long(load_crypto(END))
    cfg = MasterConfig()
    days = [d for d in px.close.index if d.date() >= date.fromisoformat(START)]
    targets, crypto_w, fallback = {}, [], 0
    for d in days[::5]:
        b0 = allocate([], crypto_state(px.close, d, cfg.crypto_assets), cfg).weights
        on = [a for a in cfg.crypto_assets if b0.get(a, 0.0) > 0]
        w = risk_parity(px.close, d, ["SPY", *on])
        if w is None:
            w, fallback = b0, fallback + 1
        targets[d.date()] = w
        crypto_w.append(sum(v for a, v in w.items() if a != "SPY"))
    sim = simulate_weights(targets, px.open, px.close, days[0].date(), days[-1].date())
    eq = pd.Series(sim.equity, index=pd.to_datetime(sim.days))
    cw = np.array(crypto_w)
    info = {"rebalances": len(targets), "b0_fallbacks": fallback, "crypto_weight_mean": round(float(cw.mean()), 3),
            "crypto_weight_max": round(float(cw.max()), 3),
            "crypto_weight_when_on_mean": round(float(cw[cw > 0].mean()), 3) if (cw > 0).any() else 0.0}
    return eq.pct_change().dropna().rename("S"), info


def compare(a: pd.Series, b: pd.Series) -> dict:
    j = pd.concat([a, b], axis=1, join="inner").dropna()
    a, b = j.iloc[:, 0], j.iloc[:, 1]
    d = block_boot([a.to_numpy(), b.to_numpy()], lambda x, y: sharpe(y) - sharpe(x))
    lo, hi = np.percentile(d, [5, 95])
    sa, sb, sbv = stats(a), stats(b), stats(b * (a.std() / b.std()))
    return {"B0": sa, "S": sb, "S_vol_matched": sbv,
            "sharpe_diff": round(sharpe(b.to_numpy()) - sharpe(a.to_numpy()), 2),
            "sharpe_diff_ci90": [round(float(lo), 2), round(float(hi), 2)],
            "pass": bool(sbv["cagr_pct"] > sa["cagr_pct"] and lo > 0)}


def stress() -> dict:
    """VineCopula on weekly returns; the frozen book's weekly loss in conditioned scenarios vs history."""
    px = Prices.from_long(load_crypto(END))
    c = px.close[["SPY", "BTC-USD", "ETH-USD"]].loc[START:].ffill().dropna()
    wk = c.resample("W-FRI").last().pct_change().dropna()
    w = np.array([FROZEN[a] for a in wk.columns])
    hist = wk.to_numpy() @ w
    vine = VineCopula(log_transform=True, n_jobs=1, random_state=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        vine.fit(wk.to_numpy())
    out: dict = {"weeks": len(wk), "history": {"worst_week_pct": round(100 * float(hist.min()), 2),
                                               "p1_week_pct": round(100 * float(np.percentile(hist, 1)), 2),
                                               "worst_weeks": {str(d.date()): round(100 * float(v), 2) for d, v in
                                                               pd.Series(hist, index=wk.index).nsmallest(5).items()}}}
    for name, cond in (("SPY_minus_10pct", {0: -0.10}), ("BTC_minus_25pct", {1: -0.25}),
                       ("unconditioned", None)):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            x = vine.sample(n_samples=20_000, conditioning=cond) if cond else vine.sample(n_samples=20_000)
        book = np.asarray(x) @ w
        out[name] = {"median_pct": round(100 * float(np.median(book)), 2),
                     "p5_pct": round(100 * float(np.percentile(book, 5)), 2),
                     "p1_pct": round(100 * float(np.percentile(book, 1)), 2),
                     "median_legs_pct": {a: round(100 * float(v), 2) for a, v in
                                         zip(wk.columns, np.median(np.asarray(x), axis=0), strict=True)}}
    return out


def main() -> None:
    b0 = master_b0(START, END)
    s, info = arm_s()
    out = {"arm_S": info, "plain": compare(b0, s), "with_brakes": compare(brake(b0)[0], brake(s)[0])}
    out["pass"] = out["plain"]["pass"]
    out["stress"] = stress()
    (BACKEND / "results" / "skfolio_test.json").write_text(json.dumps(out, indent=1) + "\n")
    register({"trial": "skfolio_cvar_risk_parity", "date": time.strftime("%Y-%m-%d"), "kind": "overlay",
              "sharpe_ann": out["plain"]["S"]["sharpe"], "window": "2018-2026",
              "result": "pass" if out["pass"] else "fail"})
    print("arm S", info)
    for k in ("plain", "with_brakes"):
        c = out[k]
        print(k)
        for n in ("B0", "S", "S_vol_matched"):
            v = c[n]
            print(f"  {n:14s} CAGR {v['cagr_pct']}%  vol {v['vol_pct']}%  Sharpe {v['sharpe']}  maxDD "
                  f"{v['max_drawdown_pct']}%  worst year {v['worst_year_pct']}%")
        print(f"  Sharpe diff {c['sharpe_diff']} 90% CI {c['sharpe_diff_ci90']} -> {'PASS' if c['pass'] else 'FAIL'}")
    print("verdict (pre-registered, plain):", "PASS" if out["pass"] else "FAIL")
    print("stress", json.dumps(out["stress"], indent=1))


if __name__ == "__main__":
    main()
