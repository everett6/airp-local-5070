"""PLAN_60_V2 Stage 1 item 5: does a 35% crypto cap beat the 20% cap in the master book (B0), at equal risk?

    python scripts/crypto_cap_test.py

Spec (docs/PLAN_60_V2.md, fixed before the run): B0 with crypto_cap 0.20 and 0.35, same code, 2018-01-02 .. 2026-09-24.
Pass: the 35% book scaled to the 20% book's realized vol has the higher CAGR AND the 90% block-bootstrap CI of
Sharpe(35%) - Sharpe(20%) is above 0. Also reports raw stats and the same comparison with the drawdown brakes on.
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
from drawdown_brakes import brake
from master_portfolio import load_crypto
from trend_sleeve import block_boot, sharpe, stats

from app.portfolio.master import MasterConfig, allocate, crypto_state, simulate_weights
from app.sandbox.dsr import register
from app.sandbox.events import Prices


def book(cap: float, start: str = "2018-01-02", end: str = "2026-09-26") -> tuple[pd.Series, float]:
    px = Prices.from_long(load_crypto(end))
    cfg = MasterConfig(crypto_cap=cap)
    days = [d for d in px.close.index if d.date() >= date.fromisoformat(start)]
    targets, crypto_share = {}, []
    for d in days[::5]:
        w = allocate([], crypto_state(px.close, d, cfg.crypto_assets), cfg).weights
        targets[d.date()] = w
        crypto_share.append(sum(v for a, v in w.items() if a in cfg.crypto_assets))
    sim = simulate_weights(targets, px.open, px.close, days[0].date(), days[-1].date())
    eq = pd.Series(sim.equity, index=pd.to_datetime(sim.days))
    return eq.pct_change().dropna(), float(np.mean(crypto_share))


def compare(a: pd.Series, b: pd.Series) -> dict:
    scaled = b * (a.std() / b.std())
    d = block_boot([a.to_numpy(), b.to_numpy()], lambda x, y: sharpe(y) - sharpe(x))
    lo, hi = np.percentile(d, [5, 95])
    sa, sb, sbv = stats(a), stats(b), stats(scaled)
    return {"cap20": sa, "cap35": sb, "cap35_vol_matched": sbv,
            "sharpe_diff": round(sharpe(b.to_numpy()) - sharpe(a.to_numpy()), 2),
            "sharpe_diff_ci90": [round(float(lo), 2), round(float(hi), 2)],
            "pass": bool(sbv["cagr_pct"] > sa["cagr_pct"] and lo > 0)}


def main() -> None:
    r20, s20 = book(0.20)
    r35, s35 = book(0.35)
    both = pd.concat([r20.rename("a"), r35.rename("b")], axis=1, join="inner").dropna()
    out = {"avg_crypto_weight": {"cap20": round(s20, 3), "cap35": round(s35, 3)},
           "plain": compare(both["a"], both["b"]),
           "with_brakes": compare(brake(both["a"])[0], brake(both["b"])[0])}
    out["pass"] = out["plain"]["pass"]
    (BACKEND / "results" / "crypto_cap_test.json").write_text(json.dumps(out, indent=1))
    register({"trial": "crypto_cap_35", "date": time.strftime("%Y-%m-%d"), "kind": "allocation",
              "sharpe_ann": out["plain"]["cap35"]["sharpe"], "window": "2018-2026",
              "result": "pass" if out["pass"] else "fail"})
    print(f"average crypto weight: 20% cap {s20:.1%}, 35% cap {s35:.1%}")
    for k in ("plain", "with_brakes"):
        c = out[k]
        print(k)
        for n in ("cap20", "cap35", "cap35_vol_matched"):
            v = c[n]
            print(f"  {n:18s} CAGR {v['cagr_pct']}%  vol {v['vol_pct']}%  Sharpe {v['sharpe']}  maxDD "
                  f"{v['max_drawdown_pct']}%  worst year {v['worst_year_pct']}%")
        print(f"  Sharpe diff {c['sharpe_diff']} 90% CI {c['sharpe_diff_ci90']} -> {'PASS' if c['pass'] else 'FAIL'}")
    print("verdict (pre-registered, plain):", "PASS" if out["pass"] else "FAIL")


if __name__ == "__main__":
    main()
