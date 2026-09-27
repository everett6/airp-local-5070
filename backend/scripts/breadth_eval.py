"""Breadth test (docs/PLAN_60_V2.md "Breadth test"): Bonsai's 1-week book on S&P 400/600 releases, 2025-26.

    python scripts/breadth_eval.py

Rule 1: monthly rank IC of the log-odds vs the 5-day sector-excess return, 95% CI above 0.
Rule 2: master book + extremes-only satellite (z > 1.5) vs B0, adding rule at 10 bps.
Writes results/events/breadth_eval.json and registers one trial.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from event_eval import build
from master_portfolio import full_run
from secchk_eval import load
from trend_sleeve import block_boot, sharpe, stats

from app.sandbox.dsr import register
from app.sandbox.events import Prices, monthly_ic, quintile_spread

EV = BACKEND / "results" / "events"
EVENTS = "data/events/events_breadth_2025.csv"
PRICES = "data/events/ohlcv_2023-01-01_2026-09-25.parquet"
DECIDE = "results/events/decide_bonsai-27b_latest_breadth_h5.jsonl"
H = 5


def book(cost: float) -> dict:
    args = argparse.Namespace(events=EVENTS, prices=PRICES, start="2025-01-02", end="2026-09-25", crypto_cap=0.20,
                              decide=DECIDE, horizon=H, book=[], min_calibration=300, cost_bps=cost, sizing="zext",
                              pick_weight=0.025)
    meta, sims = full_run(args)
    ret = {k: pd.Series(v.equity, index=pd.to_datetime(v.days)).pct_change().dropna() for k, v in sims.items()}
    both = pd.concat([ret["base"].rename("a"), ret["master"].rename("b")], axis=1, join="inner").dropna()
    a, b = both["a"], both["b"]
    d = block_boot([a.to_numpy(), b.to_numpy()], lambda x, y: sharpe(y) - sharpe(x))
    lo, hi = np.percentile(d, [5, 95])
    sa, sb, sbv = stats(a), stats(b), stats(b * (a.std() / b.std()))
    return {"B0": sa, "B1": sb, "B1_vol_matched": sbv, "days_holding_stocks_pct": meta["days_holding_stocks_pct"],
            "sharpe_diff": round(sharpe(b.to_numpy()) - sharpe(a.to_numpy()), 2),
            "sharpe_diff_ci90": [round(float(lo), 2), round(float(hi), 2)],
            "pass": bool(sbv["cagr_pct"] > sa["cagr_pct"] and lo > 0)}


def main() -> None:
    p = Prices.from_long(pd.read_parquet(BACKEND / PRICES))
    df = build(pd.read_csv(BACKEND / EVENTS), p)
    df = df[df["scorable"]].merge(load("breadth", H), on="accession")
    ic = monthly_ic(df, "logodds", f"fwd{H}")
    out = {"releases_decided": len(df), "ic": ic, "quintile_spread": quintile_spread(df, "logodds", f"fwd{H}"),
           "rule1_pass": bool(ic["ci_lo"] is not None and ic["ci_lo"] > 0)}
    out["book_10bps"], out["book_25bps"] = book(10.0), book(25.0)
    out["rule2_pass"] = out["book_10bps"]["pass"]
    out["pass"] = out["rule1_pass"] and out["rule2_pass"]
    (EV / "breadth_eval.json").write_text(json.dumps(out, indent=1) + "\n")
    register({"trial": "breadth_1w_extremes", "date": time.strftime("%Y-%m-%d"), "kind": "sleeve",
              "sharpe_ann": out["book_10bps"]["B1"]["sharpe"], "window": "2025-2026",
              "result": "pass" if out["pass"] else "fail"})
    print(f"{out['releases_decided']} releases; IC {ic['mean_ic']} [{ic['ci_lo']}, {ic['ci_hi']}] "
          f"months={ic['months']} -> rule 1 {'PASS' if out['rule1_pass'] else 'FAIL'}")
    print("quintile spread", out["quintile_spread"])
    for k in ("book_10bps", "book_25bps"):
        c = out[k]
        print(k, f"stocks held {c['days_holding_stocks_pct']}% of days")
        for n in ("B0", "B1", "B1_vol_matched"):
            v = c[n]
            print(f"  {n:15s} CAGR {v['cagr_pct']}%  vol {v['vol_pct']}%  Sharpe {v['sharpe']}  maxDD {v['max_drawdown_pct']}%")
        print(f"  Sharpe diff {c['sharpe_diff']} 90% CI {c['sharpe_diff_ci90']} -> {'PASS' if c['pass'] else 'FAIL'}")
    print("verdict (pre-registered):", "PASS" if out["pass"] else "FAIL")


if __name__ == "__main__":
    main()
