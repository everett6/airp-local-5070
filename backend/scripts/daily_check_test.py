"""PLAN_60_V2 Optimization 2: B0 with the trend rule checked daily instead of weekly (spec in the plan, fixed first).

    python scripts/daily_check_test.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from master_portfolio import load_crypto
from trend_sleeve import block_boot, sharpe, stats

from app.portfolio.master import MasterConfig, allocate, crypto_state, simulate_weights
from app.sandbox.dsr import register
from app.sandbox.events import Prices


def book(px: Prices, days: list, step: int) -> tuple[pd.Series, int, float]:
    cfg = MasterConfig()
    t = {d.date(): allocate([], crypto_state(px.close, d, cfg.crypto_assets), cfg).weights for d in days[::step]}
    s = simulate_weights(t, px.open, px.close, days[0].date(), days[-1].date(), cost_bps=10.0)
    return pd.Series(s.equity, index=pd.to_datetime(s.days)).pct_change().dropna(), s.trades, s.costs


def main() -> None:
    px = Prices.from_long(load_crypto("2026-09-26"))
    days = [d for d in px.close.index if d.date() >= date(2018, 1, 2)]
    a, ta, ca = book(px, days, 5)
    b, tb, cb = book(px, days, 1)
    both = pd.concat([a.rename("a"), b.rename("b")], axis=1, join="inner").dropna()
    a, b = both["a"], both["b"]
    d = block_boot([a.to_numpy(), b.to_numpy()], lambda x, y: sharpe(y) - sharpe(x))
    lo, hi = np.percentile(d, [5, 95])
    sa, sb, sbv = stats(a), stats(b), stats(b * (a.std() / b.std()))
    out = {"weekly": sa, "daily": sb, "daily_vol_matched": sbv, "trades": {"weekly": ta, "daily": tb},
           "costs": {"weekly": round(ca), "daily": round(cb)},
           "sharpe_diff": round(sharpe(b.to_numpy()) - sharpe(a.to_numpy()), 2),
           "sharpe_diff_ci90": [round(float(lo), 2), round(float(hi), 2)],
           "pass": bool(sbv["cagr_pct"] > sa["cagr_pct"] and lo > 0)}
    (BACKEND / "results" / "daily_check_test.json").write_text(json.dumps(out, indent=1) + "\n")
    register({"trial": "b0_daily_trend_check", "date": time.strftime("%Y-%m-%d"), "kind": "allocation",
              "sharpe_ann": sb["sharpe"], "window": "2018-2026", "result": "pass" if out["pass"] else "fail"})
    for k in ("weekly", "daily", "daily_vol_matched"):
        v = out[k]
        print(f"{k:18s} CAGR {v['cagr_pct']}%  vol {v['vol_pct']}%  Sharpe {v['sharpe']}  maxDD {v['max_drawdown_pct']}%")
    print("trades", out["trades"], "costs", out["costs"])
    print(f"Sharpe diff {out['sharpe_diff']} 90% CI {out['sharpe_diff_ci90']} -> {'PASS' if out['pass'] else 'FAIL'}")


if __name__ == "__main__":
    main()
