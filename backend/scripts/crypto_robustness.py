"""Robustness of the crypto trend sleeve's two parameters (optimization research, not a trial: nothing is selected).

    python scripts/crypto_robustness.py

B0 (SPY core + BTC/ETH trend sleeve, 20% cap), 2018-01-02 .. 2026-09-24, re-run with the trend rule's lookback
(21 trading days now) and moving average (100 days now) moved over a grid. A rule whose result only holds near its
chosen values is fitted to the past; a plateau is sturdier. The live rule is NOT changed by this, whatever the grid
shows (changing it would be a new trial with its own spec). Writes results/crypto_robustness.json.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from master_portfolio import load_crypto
from trend_sleeve import stats

from app.portfolio.master import MasterConfig, allocate, simulate_weights
from app.sandbox.events import Prices

LOOKBACKS = (10, 15, 21, 30, 42, 63)
AVERAGES = (50, 75, 100, 150, 200)


def state(close: pd.DataFrame, day: pd.Timestamp, assets: tuple[str, ...], lb: int, ma: int) -> dict:
    out = {}
    for a in assets:
        c = close[a].loc[:day].dropna()
        if len(c) < max(120, ma + 1, lb + 1):
            continue
        on = c.iloc[-1] / c.iloc[-lb] - 1 > 0 and c.iloc[-1] > c.tail(ma).mean()
        vol = float(c.pct_change().tail(60).std() * math.sqrt(252))
        out[a] = {"on": float(on), "vol": max(vol, 0.05)}
    return out


def main() -> None:
    px = Prices.from_long(load_crypto("2026-09-26"))
    cfg = MasterConfig()
    days = [d for d in px.close.index if d.date() >= date(2018, 1, 2)]
    grid: dict[str, dict] = {}
    for lb in LOOKBACKS:
        for ma in AVERAGES:
            t = {d.date(): allocate([], state(px.close, d, cfg.crypto_assets, lb, ma), cfg).weights for d in days[::5]}
            sim = simulate_weights(t, px.open, px.close, days[0].date(), days[-1].date())
            r = pd.Series(sim.equity, index=pd.to_datetime(sim.days)).pct_change().dropna()
            s = stats(r)
            grid[f"{lb}x{ma}"] = {k: s[k] for k in ("cagr_pct", "vol_pct", "sharpe", "max_drawdown_pct")}
            print(f"lookback {lb:3d}  MA {ma:3d}  CAGR {s['cagr_pct']:6.2f}%  Sharpe {s['sharpe']:.2f}  "
                  f"maxDD {s['max_drawdown_pct']}%", flush=True)
    sh = np.array([v["sharpe"] for v in grid.values()])
    live = grid["21x100"]["sharpe"]
    out = {"grid": grid, "live_rule": "21x100", "live_sharpe": live,
           "sharpe_min": float(sh.min()), "sharpe_median": float(np.median(sh)), "sharpe_max": float(sh.max()),
           "live_rank_pct": round(100 * float((sh < live).mean()), 1)}
    (BACKEND / "results" / "crypto_robustness.json").write_text(json.dumps(out, indent=1) + "\n")
    print({k: v for k, v in out.items() if k != "grid"})


if __name__ == "__main__":
    main()
