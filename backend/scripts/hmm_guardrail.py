"""H1: HMM leverage guardrail vs the autopilot's 50-day rule on leveraged QQQ (docs/PLAN_60_V2.md, "Regime guardrail
H1"). Registered trial: run once (Claude only).

    python scripts/hmm_guardrail.py
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.sandbox.dsr import register
from app.sandbox.hmm_regime import fit, panic_probability

TRIAL = "hmm_leverage_guardrail"
WINDOW = ("2016-01-04", "2026-09-25")
FIT_FROM = "2006-01-04"
CALM, PANIC, A_HIGH, A_LOW = 2.0, 0.5, 2.0, 1.0
COST, SPREAD = 0.0002, 0.005  # 2 bp per unit of leverage traded; borrowing at the bill + 0.5%
OUT = BACKEND / "results" / "hmm_guardrail.json"


def book(lev: pd.Series, q: pd.Series, bill: pd.Series) -> pd.Series:
    """Daily returns: leverage set at close t earns day t+1; cash earns the bill, borrowing costs the bill + 0.5%."""
    held = lev.shift(1)
    r = held * q - (held - 1).clip(lower=0) * (bill + SPREAD) / 252 + (1 - held).clip(lower=0) * bill / 252
    return (r - COST * held.diff().abs().fillna(0)).dropna()


def fifty_day(close: pd.Series) -> pd.Series:
    return pd.Series(np.where(close > close.rolling(50).mean(), A_HIGH, A_LOW), index=close.index)


def hmm_leverage(close: pd.Series, first_month: str) -> tuple[pd.Series, list[dict[str, Any]]]:
    """Refit on the first trading day of each month (returns up to the previous close); that month's parameters set
    leverage at each of its closes from the panic probability filtered over all returns so far."""
    logr = np.log(close).diff().dropna()
    logr = logr[logr.index >= FIT_FROM]
    lev = pd.Series(np.nan, index=close.index)
    fits = []
    months = sorted({d.to_period("M") for d in close.index if d >= pd.Timestamp(first_month)})
    for m in months:
        days = close.index[(close.index.to_period("M") == m)]
        train = logr[logr.index < days[0]]
        model = fit(train.to_numpy())
        upto = logr[logr.index <= days[-1]]
        p = pd.Series(panic_probability(upto.to_numpy(), model), index=upto.index).reindex(days)
        lev[days] = np.where(p > 0.5, PANIC, CALM)
        fits.append({"month": str(m), "iterations": model.iterations, "panic_vol_ann": (model.var[model.panic] * 252) ** 0.5,
                     "calm_vol_ann": (model.var[1 - model.panic] * 252) ** 0.5})
    return lev, fits


def stats(r: pd.Series) -> dict[str, float]:
    eq = (1 + r).cumprod()
    years = len(r) / 252
    return {"cagr": float(eq.iloc[-1] ** (1 / years) - 1), "sharpe": float(r.mean() / r.std() * 252 ** 0.5),
            "max_dd": float((eq / eq.cummax() - 1).min())}


def year_dd(r: pd.Series, year: int) -> float:
    eq = (1 + r[r.index.year == year]).cumprod()
    return float((eq / eq.cummax() - 1).min())


def bootstrap_diff(a: pd.Series, b: pd.Series, n: int = 2000, block: int = 21, seed: int = 0) -> list[float]:
    """95% CI of Sharpe(b) - Sharpe(a), circular block bootstrap on the paired days."""
    x, y = a.to_numpy(), b.to_numpy()
    rng, t = np.random.default_rng(seed), len(x)
    out = []
    for _ in range(n):
        starts = rng.integers(0, t, size=-(-t // block))
        idx = ((starts[:, None] + np.arange(block)) % t).ravel()[:t]
        xa, yb = x[idx], y[idx]
        out.append((yb.mean() / yb.std(ddof=1) - xa.mean() / xa.std(ddof=1)) * 252 ** 0.5)
    return [float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))]


def main() -> None:
    close = pd.read_parquet(BACKEND / "data" / "trend" / "etf_closes.parquet")["QQQ"].dropna()
    bill = pd.read_csv(BACKEND / "data" / "fred_dtb3.csv", index_col=0, parse_dates=True)["DTB3"]
    bill = (pd.to_numeric(bill, errors="coerce") / 100).reindex(close.index, method="ffill").ffill()
    q = close.pct_change()
    lev_b, fits = hmm_leverage(close, "2015-12-01")
    lev_a = fifty_day(close)
    lo, hi = WINDOW
    arms = {"A_fifty_day": book(lev_a, q, bill), "B_hmm": book(lev_b, q, bill),
            "static_2x": book(pd.Series(CALM, index=close.index), q, bill)}
    arms = {k: v[(v.index >= lo) & (v.index <= hi)] for k, v in arms.items()}
    res = {k: {**stats(v), "dd_2020": year_dd(v, 2020), "dd_2022": year_dd(v, 2022)} for k, v in arms.items()}
    lb = lev_b[(lev_b.index >= lo) & (lev_b.index <= hi)]
    a, b = res["A_fifty_day"], res["B_hmm"]
    passed = (b["max_dd"] - a["max_dd"]) >= 0.05 and b["sharpe"] >= a["sharpe"]
    out = {"trial": TRIAL, "run_at": datetime.now(UTC).isoformat(), "window": f"{lo}..{hi}", "arms": res,
           "sharpe_diff_ci95": bootstrap_diff(arms["A_fifty_day"], arms["B_hmm"]),
           "panic_share": float((lb == PANIC).mean()), "switches": int((lb.diff().abs() > 0).sum()),
           "fits": fits, "result": "pass" if passed else "fail",
           "rule": "B max drawdown >= 5 points smaller than A's AND B Sharpe >= A Sharpe"}
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    register({"trial": TRIAL, "date": datetime.now(UTC).date().isoformat(), "kind": "overlay",
              "sharpe_ann": round(b["sharpe"], 3), "window": out["window"], "result": out["result"],
              "max_dd": round(b["max_dd"], 4), "baseline_sharpe": round(a["sharpe"], 3),
              "baseline_max_dd": round(a["max_dd"], 4)})
    print(json.dumps({k: v for k, v in out.items() if k != "fits"}, indent=1))


if __name__ == "__main__":
    main()
