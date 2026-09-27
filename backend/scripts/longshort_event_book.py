"""PLAN_60 Phase 1 item 2: long-short 1-week earnings-event book from Bonsai's log-odds.

    python scripts/longshort_event_book.py

Spec (docs/PLAN_60.md, fixed before the run): releases grouped by the calendar week of their entry day (weeks with
fewer than 10 releases stay in cash); long the week's top fifth by Bonsai's 1-week log-odds, short the bottom fifth,
equal weight, 1 unit per side; each position from the entry-day open to the open 5 trading days later. Primary: raw
stock returns, dollar-neutral, 4 x cost per week. Secondary: sector-ETF-hedged (8 x cost). Weekly returns incl. cash
weeks, annualized by sqrt(52); Sharpe CI by a 13-week circular block bootstrap. Pass: 95% CI above 0 in both years at
10 bps. Writes results/events/longshort_event_book.json.
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from event_eval import SECTOR_ETF
from secchk_eval import load

from app.sandbox.dsr import register
from app.sandbox.events import Prices, entry_index, fwd_excess

EV = BACKEND / "results" / "events"
YEARS = {"2024": ("factsheet2024_secchk", "data/events/events_sp500_2024.csv"),
         "2025-26": ("factsheet_secchk", "data/events/events_sp500_2025.csv")}
H = 5


def raw_fwd(p: Prices, t: str, i: int, h: int) -> float | None:
    if i + h >= len(p.open.index) or t not in p.open.columns:
        return None
    s0, s1 = p.open[t].iloc[i], p.open[t].iloc[i + h]
    return None if pd.isna(s0) or pd.isna(s1) or s0 <= 0 else float(s1 / s0 - 1)


def weekly(df: pd.DataFrame, col: str, legs_cost: float) -> pd.Series:
    out = {}
    for wk, g in df.groupby("week"):
        g = g.dropna(subset=[col])
        if len(g) < 10:
            out[wk] = 0.0
            continue
        q = g["logodds"].rank(pct=True, method="first")
        out[wk] = float(g.loc[q > 0.8, col].mean() - g.loc[q <= 0.2, col].mean() - legs_cost)
    s = pd.Series(out).sort_index()
    full = pd.period_range(s.index.min(), s.index.max(), freq="W")
    return s.reindex(full, fill_value=0.0)


def boot_ci(x: np.ndarray, reps: int = 2000, block: int = 13, seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    n, k = len(x), math.ceil(len(x) / block)
    vals = []
    for _ in range(reps):
        idx = (rng.integers(0, n, k)[:, None] + np.arange(block)[None, :]).ravel()[:n] % n
        y = x[idx]
        vals.append(y.mean() / y.std() * math.sqrt(52) if y.std() > 0 else 0.0)
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return float(lo), float(hi)


def summary(s: pd.Series) -> dict:
    x = s.to_numpy()
    lo, hi = boot_ci(x)
    eq = np.cumprod(1 + x)
    return {"weeks": len(x), "traded_weeks": int((x != 0).sum()),
            "ann_return_pct": round(100 * (eq[-1] ** (52 / len(x)) - 1), 2),
            "ann_vol_pct": round(100 * x.std() * math.sqrt(52), 2),
            "sharpe": round(float(x.mean() / x.std() * math.sqrt(52)), 2), "ci95": [round(lo, 2), round(hi, 2)],
            "max_drawdown_pct": round(100 * float((1 - eq / np.maximum.accumulate(eq)).max()), 1)}


def main() -> None:
    p = Prices.from_long(pd.read_parquet(BACKEND / "data/events/ohlcv_2023-01-01_2026-09-25.parquet"))
    days = pd.DatetimeIndex(p.open.index)
    out: dict = {}
    for year, (tag, events) in YEARS.items():
        ev = pd.read_csv(BACKEND / events).merge(load(tag, H), on="accession")
        rows = []
        for r in ev.itertuples():
            t = str(r.ticker).replace(".", "-")
            i = entry_index(days, datetime.fromisoformat(str(r.accepted_utc)))
            etf = SECTOR_ETF.get(str(r.sector))
            if i is None or etf is None:
                continue
            rows.append({"week": days[i].to_period("W"), "logodds": r.logodds, "raw": raw_fwd(p, t, i, H),
                         "hedged": fwd_excess(p, t, etf, i, H)})
        df = pd.DataFrame(rows)
        res = {}
        for bps in (0, 10, 25):
            res[f"raw_{bps}bps"] = summary(weekly(df, "raw", 4 * bps / 1e4))
            res[f"hedged_{bps}bps"] = summary(weekly(df, "hedged", 8 * bps / 1e4))
        res["releases"] = int(df["raw"].notna().sum())
        out[year] = res
    out["pass"] = all(out[y]["raw_10bps"]["ci95"][0] > 0 for y in YEARS)
    (EV / "longshort_event_book.json").write_text(json.dumps(out, indent=1) + "\n")
    register({"trial": "longshort_event_book_1w", "date": time.strftime("%Y-%m-%d"), "kind": "sleeve",
              "sharpe_ann": out["2025-26"]["raw_10bps"]["sharpe"], "window": "2024 and 2025-26",
              "result": "pass" if out["pass"] else "fail"})
    for y in YEARS:
        print(f"{y}: {out[y]['releases']} releases")
        for k in ("raw_0bps", "raw_10bps", "raw_25bps", "hedged_0bps", "hedged_10bps"):
            v = out[y][k]
            print(f"  {k:13s} ret {v['ann_return_pct']:7.2f}%  vol {v['ann_vol_pct']:6.2f}%  Sharpe {v['sharpe']:5.2f} "
                  f"CI {v['ci95']}  maxDD {v['max_drawdown_pct']}%  traded weeks {v['traded_weeks']}/{v['weeks']}")
    print("PASS" if out["pass"] else "FAIL", "(rule: raw 10 bps Sharpe CI above 0 in both years)")


if __name__ == "__main__":
    main()
