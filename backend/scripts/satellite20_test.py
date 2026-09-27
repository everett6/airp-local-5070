"""PLAN_60_V2 Mon 28 item 1: does Bonsai's 20-day earnings satellite improve the master book (B0) at equal risk?

    python scripts/satellite20_test.py

Spec: docs/PLAN_60_V2.md "Mon 28 item 1" (fixed before the run). Writes results/satellite20_test.json.
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
from master_portfolio import full_run
from trend_sleeve import block_boot, sharpe, stats

from app.sandbox.dsr import register

EV = BACKEND / "results" / "events"
MERGED = "results/events/decide_bonsai-27b_latest_factsheet_secchk_2024_2026.jsonl"


def merge() -> None:
    rows = {}
    for f in ("decide_bonsai-27b_latest_factsheet2024_secchk.jsonl", "decide_bonsai-27b_latest_factsheet_secchk.jsonl"):
        for line in (EV / f).read_text().splitlines():
            rows[json.loads(line)["accession"]] = line
    (BACKEND / MERGED).write_text("\n".join(rows.values()) + "\n")


def returns(sim) -> pd.Series:
    return pd.Series(sim.equity, index=pd.to_datetime(sim.days)).pct_change().dropna()


def run(cost: float) -> dict:
    args = argparse.Namespace(events="data/events/events_sp500_2024_2026.csv",
                              prices="data/events/ohlcv_2023-01-01_2026-09-25.parquet", start="2024-01-02",
                              end="2026-09-25", crypto_cap=0.20, decide=MERGED, horizon=20, book=[],
                              min_calibration=300, cost_bps=cost, sizing="kelly", pick_weight=0.025)
    meta, sims = full_run(args)
    a, b = returns(sims["base"]), returns(sims["master"])
    both = pd.concat([a.rename("a"), b.rename("b")], axis=1, join="inner").dropna()
    a, b = both["a"], both["b"]
    d = block_boot([a.to_numpy(), b.to_numpy()], lambda x, y: sharpe(y) - sharpe(x))
    lo, hi = np.percentile(d, [5, 95])
    sa, sb, sbv = stats(a), stats(b), stats(b * (a.std() / b.std()))
    return {"B0": sa, "B1": sb, "B1_vol_matched": sbv, "events_scored": meta["events_scored"],
            "days_holding_stocks_pct": meta["days_holding_stocks_pct"],
            "sharpe_diff": round(sharpe(b.to_numpy()) - sharpe(a.to_numpy()), 2),
            "sharpe_diff_ci90": [round(float(lo), 2), round(float(hi), 2)],
            "pass": bool(sbv["cagr_pct"] > sa["cagr_pct"] and lo > 0)}


def main() -> None:
    merge()
    out = {f"{c}bps": run(c) for c in (10, 25)}
    out["pass"] = out["10bps"]["pass"]
    (BACKEND / "results" / "satellite20_test.json").write_text(json.dumps(out, indent=1) + "\n")
    register({"trial": "bonsai_satellite_20d", "date": time.strftime("%Y-%m-%d"), "kind": "sleeve",
              "sharpe_ann": out["10bps"]["B1"]["sharpe"], "window": "2024-2026",
              "result": "pass" if out["pass"] else "fail"})
    for k in ("10bps", "25bps"):
        c = out[k]
        print(k, f"stocks held {c['days_holding_stocks_pct']}% of days, {c['events_scored']} releases")
        for n in ("B0", "B1", "B1_vol_matched"):
            v = c[n]
            print(f"  {n:15s} CAGR {v['cagr_pct']}%  vol {v['vol_pct']}%  Sharpe {v['sharpe']}  maxDD {v['max_drawdown_pct']}%")
        print(f"  Sharpe diff {c['sharpe_diff']} 90% CI {c['sharpe_diff_ci90']} -> {'PASS' if c['pass'] else 'FAIL'}")
    print("verdict (pre-registered, 10 bps):", "PASS" if out["pass"] else "FAIL")


if __name__ == "__main__":
    main()
