"""PLAN_60_V2 Optimization 1: a 20% volatility target on B0 (SPY core + crypto sleeve), 2018-26.

    python scripts/vol_target_b0.py

Spec (docs/PLAN_60_V2.md, fixed before the run): exposure e_d = min(1.5, 0.20 / 20-day realized vol of B0 up to d-1),
changed only when the new value is more than 0.10 away; cost 10 bps x |change|; the borrowed part (e - 1 > 0) pays
rf + 1.5%/yr (FRED DTB3). Pass: vol-matched CAGR above B0's AND the 90% block-bootstrap CI of the Sharpe difference
above 0. Writes results/vol_target_b0.json.
"""
from __future__ import annotations

import json
import math
import sys
import time
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from drawdown_brakes import brake
from trend_sleeve import block_boot, master_b0, sharpe, stats

from app.sandbox.dsr import register

TARGET, CAP, BAND, COST, SPREAD = 0.20, 1.5, 0.10, 10.0, 0.015


def tbill() -> pd.Series:
    f = BACKEND / "data" / "fred_dtb3.csv"
    if not f.exists():
        req = urllib.request.Request("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3",
                                     headers={"User-Agent": "Mozilla/5.0"})
        f.write_bytes(urllib.request.urlopen(req, timeout=60).read())
    df = pd.read_csv(f)
    s = pd.to_numeric(df.iloc[:, 1], errors="coerce")
    return pd.Series(s.to_numpy() / 100, index=pd.to_datetime(df.iloc[:, 0])).ffill()


def managed(r: pd.Series, rf: pd.Series) -> tuple[pd.Series, pd.Series]:
    vol = r.rolling(20).std().shift(1) * math.sqrt(252)
    rf = rf.reindex(r.index, method="ffill").fillna(0.0)
    e_prev, out, ex = 1.0, [], []
    for d, x in r.items():
        v = vol.get(d)
        e = e_prev if v is None or np.isnan(v) or v <= 0 else min(CAP, TARGET / v)
        if abs(e - e_prev) <= BAND:
            e = e_prev
        y = e * x - abs(e - e_prev) * COST / 1e4 - max(0.0, e - 1) * (rf[d] + SPREAD) / 252
        out.append(y)
        ex.append(e)
        e_prev = e
    return pd.Series(out, index=r.index), pd.Series(ex, index=r.index)


def compare(a: pd.Series, b: pd.Series) -> dict:
    d = block_boot([a.to_numpy(), b.to_numpy()], lambda x, y: sharpe(y) - sharpe(x))
    lo, hi = np.percentile(d, [5, 95])
    sa, sb, sbv = stats(a), stats(b), stats(b * (a.std() / b.std()))
    return {"B0": sa, "managed": sb, "managed_vol_matched": sbv,
            "sharpe_diff": round(sharpe(b.to_numpy()) - sharpe(a.to_numpy()), 2),
            "sharpe_diff_ci90": [round(float(lo), 2), round(float(hi), 2)],
            "pass": bool(sbv["cagr_pct"] > sa["cagr_pct"] and lo > 0)}


def main() -> None:
    b0 = master_b0("2018-01-02", "2026-09-26")
    m, ex = managed(b0, tbill())
    out = {"plain": compare(b0, m), "with_brakes": compare(brake(b0)[0], brake(m)[0]),
           "exposure": {"mean": round(float(ex.mean()), 2), "min": round(float(ex.min()), 2),
                        "max": round(float(ex.max()), 2), "levered_days_pct": round(100 * float((ex > 1).mean()), 1),
                        "changes": int((ex.diff().abs() > 0).sum())}}
    out["pass"] = out["plain"]["pass"]
    (BACKEND / "results" / "vol_target_b0.json").write_text(json.dumps(out, indent=1) + "\n")
    register({"trial": "vol_target_b0_20pct", "date": time.strftime("%Y-%m-%d"), "kind": "overlay",
              "sharpe_ann": out["plain"]["managed"]["sharpe"], "window": "2018-2026",
              "result": "pass" if out["pass"] else "fail"})
    print("exposure", out["exposure"])
    for k in ("plain", "with_brakes"):
        c = out[k]
        print(k)
        for n in ("B0", "managed", "managed_vol_matched"):
            v = c[n]
            print(f"  {n:20s} CAGR {v['cagr_pct']}%  vol {v['vol_pct']}%  Sharpe {v['sharpe']}  maxDD "
                  f"{v['max_drawdown_pct']}%  worst year {v['worst_year_pct']}%")
        print(f"  Sharpe diff {c['sharpe_diff']} 90% CI {c['sharpe_diff_ci90']} -> {'PASS' if c['pass'] else 'FAIL'}")
    print("verdict (pre-registered, plain):", "PASS" if out["pass"] else "FAIL")


if __name__ == "__main__":
    main()
