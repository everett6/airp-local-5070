"""The day-trading track's pre-registered test (docs/PLAN_60_V2.md "Day-trading track"): D1 and D2, one trial each.

    python scripts/daytrade_test.py      # needs data/intraday/{SPY,QQQ}_1min.parquet (scripts/intraday_data.py)

Pass (each rule): on its post-publication window, at 1 bp a side, the annualized Sharpe of the daily P&L (SPY and
QQQ, equal capital) is >= 0.5 AND its 95% block-bootstrap CI is above 0. Writes results/daytrade_test.json.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd

from app.sandbox.dsr import register
from app.sandbox.intraday import block_ci, d1_intraday_momentum, d2_orb5, sharpe

DATA = BACKEND / "data" / "intraday"
RULES = {"D1": (d1_intraday_momentum, "2019-01-02", "daytrade_intraday_momentum"),
         "D2": (d2_orb5, "2023-07-01", "daytrade_orb5")}
END = "2026-09-25"


def stats(r: pd.Series) -> dict:
    lo, hi = block_ci(r)
    m = (1 + r).groupby(r.index.to_period("M")).prod() - 1
    return {"days": len(r), "cagr": round(float((1 + r).prod() ** (252 / len(r)) - 1), 4),
            "sharpe": round(sharpe(r), 3), "ci": [round(lo, 3), round(hi, 3)],
            "hit_rate": round(float((r > 0).mean()), 3), "worst_month": round(float(m.min()), 4)}


def main() -> None:
    bars = {s: pd.read_parquet(DATA / f"{s}_1min.parquet") for s in ("SPY", "QQQ")}
    core = pd.read_parquet(BACKEND / "results" / "planner" / "track_returns.parquet")["core"]
    out: dict = {}
    for name, (fn, start, trial) in RULES.items():
        res: dict = {"window": [start, END]}
        for cost_bp in (1, 5):
            per = {s: fn(b, cost_bp / 1e4) for s, b in bars.items()}
            both = pd.concat(per, axis=1).fillna(0.0).mean(axis=1)  # equal capital; a symbol with no trade earns 0
            test = both.loc[start:END]
            res[f"{cost_bp}bp"] = {"both": stats(test), **{s: stats(r.loc[start:END]) for s, r in per.items()}}
            if cost_bp == 1:
                res["before_window"] = stats(both.loc[:start].iloc[:-1]) if len(both.loc[:start]) > 30 else None
                res["corr_with_core"] = round(float(test.corr(core.reindex(test.index))), 3)
        t = res["1bp"]["both"]
        res["pass"] = bool(t["sharpe"] >= 0.5 and t["ci"][0] > 0)
        register({"trial": trial, "date": time.strftime("%Y-%m-%d"), "kind": "daytrade", "sharpe_ann": t["sharpe"],
                  "window": f"{start}..{END}", "result": "pass" if res["pass"] else "fail"})
        out[name] = res
        print(f"{name} {trial}: Sharpe {t['sharpe']} CI {t['ci']} CAGR {t['cagr']:.1%} hit {t['hit_rate']:.0%} "
              f"(5 bps: Sharpe {res['5bp']['both']['sharpe']}) -> {'PASS' if res['pass'] else 'FAIL'}", flush=True)
    (BACKEND / "results" / "daytrade_test.json").write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
