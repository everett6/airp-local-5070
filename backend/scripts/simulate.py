"""Paper-trade every strategy of a finished walk-forward run and compare with buying SPY.

    python scripts/simulate.py v7_phase_f                 # all arms, top 10 stocks, $100k
    python scripts/simulate.py v7_phase_f --arms llm_fund_lp,kronos --top-k 20

Uses the run's own predictions (made point-in-time, before each week's outcome) and free Yahoo daily OHLCV bars.
Scored from the run's warm-up date so learning strategies are judged only after they had data to learn from.
Each strategy is simulated with `--seeds` different random tie-breaks (many models give lots of stocks the same
score); the table shows the median run and the 10th-90th percentile of its return. Every strategy also gets a
Deflated Sharpe Ratio: the probability its Sharpe beats the best of N strategies that have no skill (N = the number
simulated here). Writes results/sim_<tag>.json. No real orders are placed.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from dataclasses import replace

import numpy as np

from app.portfolio.simulator import (
    SimConfig,
    SimResult,
    beta_alpha,
    buy_and_hold,
    config_dict,
    excess_vs,
    simulate,
)
from app.sandbox.ohlcv import load_ohlcv
from app.sandbox.scoring import deflated_sharpe

# arms that are constant or a single rule give every stock nearly the same score: "top 10" would just be the
# alphabetically first tickers, which says nothing about the model, so they are skipped
SKIP = {"always_up", "base_rate"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--arms", default=None, help="comma-separated arms (default: every arm in the run)")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--cash", type=float, default=100_000.0)
    ap.add_argument("--slippage-bps", type=float, default=5.0)
    ap.add_argument("--min-prob", type=float, default=None)
    ap.add_argument("--ohlcv", default="data/ohlcv_pit_2025-06-02_top100.csv")
    ap.add_argument("--seeds", type=int, default=25, help="random tie-breaks per strategy")
    args = ap.parse_args()

    results = BACKEND / "results"
    report = json.loads((results / f"walkforward_{args.tag}.json").read_text())
    warm = date.fromisoformat(report["warmup_from"])
    ohlcv_path = BACKEND / args.ohlcv
    if not ohlcv_path.exists():
        raise SystemExit(f"missing {args.ohlcv}; fetch it free with: python scripts/fetch_ohlcv.py "
                         f"<the run's price csv> {args.ohlcv}")
    bars = load_ohlcv(ohlcv_path)
    missing = set(report["tickers"]) - set(bars)
    if missing:
        raise SystemExit(f"OHLCV file lacks {len(missing)} of the run's tickers, e.g. {sorted(missing)[:5]}")

    by_arm: dict[str, list[dict[str, object]]] = {}
    for line in (results / f"walkforward_{args.tag}_predictions.jsonl").read_text().splitlines():
        r = json.loads(line)
        if date.fromisoformat(r["cutoff"][:10]) >= warm:
            by_arm.setdefault(r["arm"], []).append(r)
    arms = args.arms.split(",") if args.arms else [a for a in by_arm if a not in SKIP]
    cfg = SimConfig(initial_cash=args.cash, top_k=args.top_k, slippage_bps=args.slippage_bps, min_prob=args.min_prob)
    universe = set(report["tickers"])
    horizon = int(report["horizon_days"])
    last_signal = max(date.fromisoformat(str(r["cutoff"])[:10]) for rs in by_arm.values() for r in rs)
    # hold the last picks for one more horizon, then stop (outcomes after that were never part of the run)
    end_idx = min(len(sorted(bars["SPY"])) - 1,
                  [b[0] for b in bars["SPY"]].index(last_signal) + horizon)
    end = bars["SPY"][end_idx][0]

    runs: dict[str, list[SimResult]] = {}
    for a in arms:
        if a in by_arm:
            runs[a] = [simulate(by_arm[a], bars, replace(cfg, tie_seed=k), name=a, universe=universe, end=end)
                       for k in range(args.seeds)]
    # the representative run for each strategy is the one with the median final equity
    sims = {a: sorted(rs, key=lambda r: r.equity[-1])[len(rs) // 2] for a, rs in runs.items()}
    start = min(s.days[0] for s in sims.values())
    spy = buy_and_hold(bars, "SPY", start, end, args.cash, args.slippage_bps)
    equal = simulate([{"cutoff": r["cutoff"], "ticker": r["ticker"], "p": 0.5} for r in next(iter(by_arm.values()))],
                     bars, SimConfig(initial_cash=args.cash, top_k=len(universe), max_weight=1.0,
                                     slippage_bps=args.slippage_bps), name="equal_weight_all", universe=universe, end=end)

    out = {"tag": args.tag, "window": [start.isoformat(), end.isoformat()], "config": config_dict(cfg),
           "benchmarks": {"spy": spy.metrics(), "equal_weight_all": equal.metrics()}, "strategies": {},
           "equity": {"dates": [d.isoformat() for d in spy.days], "spy": [round(x, 2) for x in spy.equity]}}
    daily = {a: np.diff(s.equity) / np.asarray(s.equity[:-1]) for a, s in sims.items()}
    trial_sr = [float(r.mean() / r.std(ddof=1)) for r in daily.values() if r.std(ddof=1) > 0]
    for a, s in sims.items():
        rets = sorted(r.equity[-1] / r.equity[0] - 1 for r in runs[a])
        dsr = deflated_sharpe(daily[a], max(len(sims), 1), trial_sr)["dsr"]
        out["strategies"][a] = {**s.metrics(), "vs_spy": excess_vs(s, spy), "vs_equal_weight": excess_vs(s, equal),
                                "seeds": args.seeds,
                                "return_p10_pct": round(100 * float(np.percentile(rets, 10)), 2),
                                "return_p90_pct": round(100 * float(np.percentile(rets, 90)), 2),
                                "deflated_sharpe": None if dsr is None else round(dsr, 3),
                                "vs_market": beta_alpha(s, spy)}
        out["equity"][a] = [round(x, 2) for x in s.equity]
    (results / f"sim_{args.tag}.json").write_text(json.dumps(out, indent=2) + "\n")

    print(f"{args.tag}: ${args.cash:,.0f} paper money, top {args.top_k} stocks, {args.slippage_bps:g} bp per side, "
          f"{start} -> {end}")
    rows = [("SPY buy & hold", spy.metrics()), ("Equal weight, all 100", equal.metrics())]
    rows += sorted(((a, out["strategies"][a]) for a in sims), key=lambda r: -r[1]["total_return_pct"])
    print(f"{'strategy':22s} {'return':>7s} {'10-90% range':>15s} {'max DD':>7s} {'Sharpe':>6s} {'DSR':>5s} "
          f"{'ties':>5s} {'beta':>5s}  alpha/yr after beta [95% CI]")
    for name, m in rows:
        v = m.get("vs_market")
        vs = f"{v['alpha_ann_pct']:+6.1f}% [{v['alpha_ci_lo']:+.1f}, {v['alpha_ci_hi']:+.1f}]" if v else ""
        beta = f"{v['beta']:.2f}" if v else ""
        rng = f"{m['return_p10_pct']:+.0f}..{m['return_p90_pct']:+.0f}%" if "return_p10_pct" in m else ""
        dsr = f"{m['deflated_sharpe']:.2f}" if m.get("deflated_sharpe") is not None else ""
        ties = f"{m['tie_decided_pct']:.0f}%" if "vs_spy" in m else ""
        print(f"{name:22s} {m['total_return_pct']:>6.1f}% {rng:>15s} {m['max_drawdown_pct']:>6.1f}% {m['sharpe']:>6.2f} "
              f"{dsr:>5s} {ties:>5s} {beta:>5s}  {vs}")


if __name__ == "__main__":
    main()
