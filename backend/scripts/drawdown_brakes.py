"""PLAN_60 Phase 2 item 2: book-level drawdown brakes on B0 (SPY core + crypto sleeve), 2018-26.

    python scripts/drawdown_brakes.py

Spec (docs/PLAN_60.md, fixed before the run): exposure for day d = 0.5 if the braked book's drawdown at the close of
d-1 is >= 20%, 2/3 if >= 10%, else 1; the cut part earns 0; cost 10 bps x |change in exposure|. Pass: the max drawdown
is smaller AND the braked book, scaled to B0's realized vol, loses no more than 1 point of CAGR.
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
from trend_sleeve import master_b0, stats

from app.sandbox.dsr import register


def brake(r: pd.Series, cost_bps: float = 10.0) -> tuple[pd.Series, pd.Series]:
    eq, peak, m_prev = 1.0, 1.0, 1.0
    out, mult = [], []
    for x in r.to_numpy():
        dd = 1 - eq / peak
        m = 0.5 if dd >= 0.20 else (2 / 3 if dd >= 0.10 else 1.0)
        y = m * x - abs(m - m_prev) * cost_bps / 1e4
        eq *= 1 + y
        peak = max(peak, eq)
        out.append(y)
        mult.append(m)
        m_prev = m
    return pd.Series(out, index=r.index), pd.Series(mult, index=r.index)


def main() -> None:
    b0 = master_b0("2018-01-02", "2026-09-26")
    br, mult = brake(b0)
    scaled = br * (b0.std() / br.std())
    s0, s1, s1v = stats(b0), stats(br), stats(scaled)
    ok = s1["max_drawdown_pct"] < s0["max_drawdown_pct"] and s1v["cagr_pct"] >= s0["cagr_pct"] - 1.0
    out = {"B0": s0, "braked": s1, "braked_vol_matched": s1v, "days_braked_pct": round(100 * float((mult < 1).mean()), 1),
           "pass": bool(ok)}
    (BACKEND / "results" / "drawdown_brakes.json").write_text(json.dumps(out, indent=1))
    register({"trial": "drawdown_brakes_b0", "date": time.strftime("%Y-%m-%d"), "kind": "overlay",
              "sharpe_ann": s1["sharpe"], "window": "2018-2026", "result": "pass" if ok else "fail"})
    for k in ("B0", "braked", "braked_vol_matched"):
        v = out[k]
        print(f"{k:19s} CAGR {v['cagr_pct']}%  vol {v['vol_pct']}%  Sharpe {v['sharpe']}  maxDD {v['max_drawdown_pct']}%  "
              f"worst year {v['worst_year_pct']}%")
    print(f"braked on {out['days_braked_pct']}% of days -> {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main()
