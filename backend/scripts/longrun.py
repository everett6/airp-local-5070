"""Paper-trade the no-LLM strategies over 16 years on the point-in-time S&P 500 top 100 (free data, fake money).

    python scripts/longrun.py                                   # 2011-01 -> 2026-09, top 10, $100k
    python scripts/longrun.py --extra results/longrun_llm_web_signals.jsonl   # also simulate saved LLM signals

2010 is used only as a warm-up (features need a year of prices; feat_logit needs a year of resolved weeks).
Writes results/longrun_<tag>.json: every strategy's full-period metrics, beta/alpha vs SPY, Deflated Sharpe
across all strategies tried, and calendar-year returns next to SPY's. No real orders are placed.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import numpy as np
import pandas as pd

from app.portfolio.simulator import (
    SimConfig,
    SimResult,
    beta_alpha,
    buy_and_hold,
    config_dict,
    excess_vs,
    simulate,
)
from app.sandbox.longrun import Panel, bars_from_panel, signals
from app.sandbox.scoring import deflated_sharpe


def yearly(sim: SimResult) -> dict[int, float]:
    by: dict[int, list[float]] = defaultdict(list)
    for d, e in zip(sim.days, sim.equity, strict=True):
        by[d.year].append(e)
    out, prev = {}, sim.equity[0]
    for y in sorted(by):
        out[y] = round(100 * (by[y][-1] / prev - 1), 1)
        prev = by[y][-1]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="data/hist/universe_2010_2026_top100.csv")
    ap.add_argument("--ohlcv", default="data/hist/ohlcv_2010_2026_top100.parquet")
    ap.add_argument("--start", default="2011-01-01")
    ap.add_argument("--end", default="2026-09-15")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--cash", type=float, default=100_000.0)
    ap.add_argument("--slippage-bps", type=float, default=5.0)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--drawdown-halt", type=float, default=1.0,
                    help="stop trading after this loss from the peak (default off: over 16 years the market "
                         "itself fell 34%% in 2020, and a permanent stop would end every run there)")
    ap.add_argument("--extra", action="append", default=[], help="jsonl of saved signals {arm,cutoff,ticker,p}")
    ap.add_argument("--tag", default="no_llm")
    args = ap.parse_args()

    panel = Panel.load(pd.read_parquet(BACKEND / args.ohlcv), pd.read_csv(BACKEND / args.universe))
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    sigs = signals(panel, start, end)
    for path in args.extra:
        for line in (BACKEND / path).read_text().splitlines():
            r = json.loads(line)
            sigs.setdefault(r["arm"], []).append({"cutoff": date.fromisoformat(r["cutoff"][:10]),
                                                  "ticker": r["ticker"], "p": r["p"]})
    bars = bars_from_panel(panel)
    universe = set().union(*panel.universe.values())
    cfg = SimConfig(initial_cash=args.cash, top_k=args.top_k, slippage_bps=args.slippage_bps,
                    drawdown_halt=args.drawdown_halt)
    sim_end = max(d for d in (b[0] for b in bars["SPY"]) if d <= end)

    runs = {a: [simulate(s, bars, replace(cfg, tie_seed=k), name=a, universe=universe, end=sim_end)
                for k in range(args.seeds)] for a, s in sigs.items() if s}
    sims = {a: sorted(rs, key=lambda r: r.equity[-1])[len(rs) // 2] for a, rs in runs.items()}
    first = min(s.days[0] for s in sims.values())
    spy = buy_and_hold(bars, "SPY", first, sim_end, args.cash, args.slippage_bps)
    any_arm = next(iter(sigs.values()))
    equal = simulate([{**r, "p": 0.5} for r in any_arm], bars,
                     SimConfig(initial_cash=args.cash, top_k=120, max_weight=1.0, slippage_bps=args.slippage_bps,
                               drawdown_halt=args.drawdown_halt),
                     name="equal_weight_all", universe=universe, end=sim_end)

    daily = {a: np.diff(s.equity) / np.asarray(s.equity[:-1]) for a, s in sims.items()}
    trial_sr = [float(r.mean() / r.std(ddof=1)) for r in daily.values() if r.std(ddof=1) > 0]
    out = {"tag": args.tag, "window": [first.isoformat(), sim_end.isoformat()], "config": config_dict(cfg),
           "data": {"universe": args.universe, "ohlcv": args.ohlcv},
           "benchmarks": {"spy": {**spy.metrics(), "yearly": yearly(spy)},
                          "equal_weight_all": {**equal.metrics(), "yearly": yearly(equal)}},
           "strategies": {}}
    for a, s in sims.items():
        rets = sorted(r.equity[-1] / r.equity[0] - 1 for r in runs[a])
        dsr = deflated_sharpe(daily[a], max(len(sims), 1), trial_sr)["dsr"]
        out["strategies"][a] = {**s.metrics(), "vs_spy": excess_vs(s, spy), "vs_equal_weight": excess_vs(s, equal),
                                "vs_market": beta_alpha(s, spy), "seeds": args.seeds,
                                "return_min_pct": round(100 * rets[0], 1), "return_max_pct": round(100 * rets[-1], 1),
                                "deflated_sharpe": None if dsr is None else round(dsr, 3), "yearly": yearly(s)}
    (BACKEND / "results" / f"longrun_{args.tag}.json").write_text(json.dumps(out, indent=1, default=str) + "\n")

    print(f"{args.tag}: ${args.cash:,.0f} paper money, top {args.top_k}, {args.slippage_bps:g} bp/side, "
          f"{first} -> {sim_end}")
    rows = [("SPY buy & hold", out["benchmarks"]["spy"]), ("Equal weight, all", out["benchmarks"]["equal_weight_all"])]
    rows += sorted(out["strategies"].items(), key=lambda r: -r[1]["cagr_pct"])
    print(f"{'strategy':20s} {'CAGR':>6s} {'total':>8s} {'maxDD':>6s} {'Sharpe':>6s} {'DSR':>5s} {'beta':>5s}  "
          "alpha/yr [95% CI]")
    for name, m in rows:
        v = m.get("vs_market")
        vs = f"{v['alpha_ann_pct']:+5.1f}% [{v['alpha_ci_lo']:+.1f}, {v['alpha_ci_hi']:+.1f}]" if v else ""
        beta = f"{v['beta']:.2f}" if v else ""
        dsr = f"{m['deflated_sharpe']:.2f}" if m.get("deflated_sharpe") is not None else ""
        print(f"{name:20s} {m['cagr_pct']:>5.1f}% {m['total_return_pct']:>7.0f}% {m['max_drawdown_pct']:>5.1f}% "
              f"{m['sharpe']:>6.2f} {dsr:>5s} {beta:>5s}  {vs}")
    years = sorted(out["benchmarks"]["spy"]["yearly"])
    print("\ncalendar-year returns (%)\n" + f"{'':20s}" + "".join(f"{y:>6d}" for y in years))
    for name, m in rows:
        print(f"{name:20s}" + "".join(f"{m['yearly'].get(y, float('nan')):>6.1f}" for y in years))


if __name__ == "__main__":
    main()
