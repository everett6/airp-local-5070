"""A2: volatility-managed stock momentum, long-short, on the point-in-time top-100 S&P 500 universe.

    python scripts/momentum_sleeve.py [--cost-bps 10]

Spec and pass rules were written before the first run (docs/STRATEGY_RESEARCH.md, "A2"). Each month-end: rank that
year's universe by the return from t-252 to t-21, long the top 20% and short the bottom 20% (equal weight per side);
the book is scaled by 12% / the realized vol of the unscaled long-short's daily returns over the prior 126 days, capped
at 2x. Weights set at close t earn from t+1. Costs x turnover, 0.5%/yr borrow on shorts. Standalone window 2016-01 on;
adding rule against B0 (trend_sleeve.master_b0) 2018-26. Writes results/momentum_sleeve<tag>.json.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from trend_sleeve import block_boot, master_b0, sharpe, stats

HIST = BACKEND / "data" / "hist"


def sleeve_returns(cost_bps: float, target_vol: float = 0.12, cap: float = 2.0) -> tuple[pd.Series, pd.DataFrame]:
    px = pd.read_parquet(HIST / "ohlcv_2010_2026_top100.parquet")
    px["Date"] = pd.to_datetime(px["Date"])
    close = px.pivot(index="Date", columns="Ticker", values="Close").sort_index()
    close = close.loc[close["SPY"].dropna().index] if "SPY" in close else close.dropna(how="all")
    uni = pd.read_csv(HIST / "universe_2010_2026_top100.csv")
    members = {int(y): set(g["ticker"].str.replace(".", "-", regex=False)) for y, g in uni.groupby("year")}
    r = close.pct_change(fill_method=None)
    month_ends = close.index.to_series().groupby(close.index.to_period("M")).last()
    raw: dict[pd.Timestamp, pd.Series] = {}
    for t in month_ends:
        i = close.index.get_loc(t)
        if i < 252 or t.year not in members:
            continue
        names = [n for n in members[t.year] if n in close.columns]
        mom = (close[names].iloc[i - 21] / close[names].iloc[i - 252] - 1).dropna()
        mom = mom[close[mom.index].iloc[i].notna()]
        if len(mom) < 50:
            continue
        q = mom.rank(pct=True)
        longs, shorts = q[q > 0.8].index, q[q <= 0.2].index
        w = pd.Series(0.0, index=close.columns)
        w[longs] = 1.0 / len(longs)
        w[shorts] = -1.0 / len(shorts)
        raw[t] = w
    W = pd.DataFrame(raw).T.sort_index()
    daily_raw = W.reindex(close.index).ffill().shift(1).dropna(how="all")
    unscaled = (daily_raw * r.loc[daily_raw.index].fillna(0.0)).sum(axis=1)
    # scale at each month-end from the unscaled book's trailing 126-day vol (known at t)
    scale = {}
    for t in W.index:
        hist = unscaled.loc[:t].iloc[-126:]
        scale[t] = min(cap, target_vol / (hist.std() * math.sqrt(252))) if len(hist) >= 126 else np.nan
    scale = pd.Series(scale).dropna()
    Ws = W.loc[scale.index].mul(scale, axis=0)
    daily_w = Ws.reindex(close.index).ffill().shift(1).dropna(how="all")
    gross = (daily_w * r.loc[daily_w.index].fillna(0.0)).sum(axis=1)
    shorts = -daily_w.clip(upper=0).sum(axis=1)
    turnover = Ws.diff().abs().sum(axis=1)
    turnover.iloc[0] = Ws.iloc[0].abs().sum()
    cost = pd.Series(0.0, index=daily_w.index)
    for t, tv in turnover.items():
        nxt = daily_w.index[daily_w.index > t]
        if len(nxt):
            cost[nxt[0]] += tv * cost_bps / 1e4
    return (gross - 0.005 * shorts / 252 - cost).rename("momentum"), Ws


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--end", default="2026-09-26")
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    mom, W = sleeve_returns(args.cost_bps)
    post = mom.loc["2016-01-01":]
    ci = np.percentile(block_boot([post.to_numpy()], sharpe), [2.5, 97.5])
    out = {"cost_bps": args.cost_bps, "full": stats(mom), "post_2016": stats(post),
           "post_2016_sharpe_ci95": [round(float(x), 2) for x in ci],
           "turnover_per_year": round(float(W.diff().abs().sum(axis=1).mean() * 12), 2),
           "avg_gross": round(float(W.abs().sum(axis=1).mean()), 2), "standalone_pass": bool(ci[0] > 0)}
    b0 = master_b0("2018-01-02", args.end)
    both = pd.concat([b0, mom], axis=1, join="inner").dropna()
    b1 = both["B0"] + both["momentum"]
    scaled = b1 * (both["B0"].std() / b1.std())
    d = block_boot([both["B0"].to_numpy(), b1.to_numpy()], lambda a, b: sharpe(b) - sharpe(a))
    lo, hi = np.percentile(d, [5, 95])
    s0, s1, s1v = stats(both["B0"]), stats(b1), stats(scaled)
    out["book"] = {"B0": s0, "B1": s1, "B1_vol_matched": s1v,
                   "corr_sleeve_B0": round(float(both["B0"].corr(both["momentum"])), 2),
                   "sharpe_diff": round(sharpe(b1.to_numpy()) - sharpe(both["B0"].to_numpy()), 2),
                   "sharpe_diff_ci90": [round(float(lo), 2), round(float(hi), 2)]}
    out["adds_value_pass"] = bool(s1v["cagr_pct"] > s0["cagr_pct"] and lo > 0)
    path = BACKEND / "results" / f"momentum_sleeve{args.tag}.json"
    path.write_text(json.dumps(out, indent=1))
    print(f"momentum sleeve @ {args.cost_bps:g} bps  turnover {out['turnover_per_year']}x/yr  avg gross {out['avg_gross']}x")
    for name, s in (("full", out["full"]), ("2016-26", out["post_2016"])):
        print(f"  {name} {s['start']}..: CAGR {s['cagr_pct']}%  vol {s['vol_pct']}%  Sharpe {s['sharpe']}  maxDD "
              f"{s['max_drawdown_pct']}%  worst month {s['worst_month_pct']}%  worst year {s['worst_year_pct']}%")
    print(f"  2016-26 Sharpe 95% CI {out['post_2016_sharpe_ci95']} -> standalone "
          f"{'PASS' if out['standalone_pass'] else 'FAIL'}")
    b = out["book"]
    for name in ("B0", "B1", "B1_vol_matched"):
        s = b[name]
        print(f"  {name:15s} CAGR {s['cagr_pct']}%  vol {s['vol_pct']}%  Sharpe {s['sharpe']}  maxDD {s['max_drawdown_pct']}%")
    print(f"  corr {b['corr_sleeve_B0']}  Sharpe B1-B0 {b['sharpe_diff']} 90% CI {b['sharpe_diff_ci90']} -> adds value "
          f"{'PASS' if out['adds_value_pass'] else 'FAIL'}")
    print(f"  yearly: {out['full']['yearly_pct']}")


if __name__ == "__main__":
    main()
