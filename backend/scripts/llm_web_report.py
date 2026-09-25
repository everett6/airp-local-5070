"""Did the internet research help? Compare the LLM's picks with the screen's picks on the same candidates.

    python scripts/llm_web_report.py                    # clean window (after the model's training data)
    python scripts/llm_web_report.py --window memory_risk

Reads results/llm_web/*/*.json (from scripts/llm_web_backtest.py) and reports:
  1. Ranking skill: each month, the rank correlation between the LLM's P(up) and the candidates' realised
     20-day return (next open to the open 20 trading days later); mean over months with a bootstrap CI
     over months. The screen's own score gets the same test.
  2. Paper trading: top 10 of the 20 candidates by the LLM vs by the screen, bought at the next open with costs,
     $100k fake money; the LLM's excess over the screen with a block-bootstrap CI, and both vs SPY.
  3. Research coverage: how often each tool worked, how many decisions had news, filings, or neither.
Writes results/llm_web_report_<window>.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import numpy as np
import pandas as pd

from app.portfolio.simulator import SimConfig, beta_alpha, buy_and_hold, excess_vs, simulate
from app.sandbox.longrun import Panel, bars_from_panel

H = 20


def signal_p(rec: dict) -> float | None:
    if rec.get("logprob_mass", 0.0) > 0.05:
        return float(rec["p_up_logprob"])
    return rec.get("p_up")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", default="clean", choices=("clean", "memory_risk"))
    ap.add_argument("--universe", default="data/hist/universe_2010_2026_top100.csv")
    ap.add_argument("--ohlcv", default="data/hist/ohlcv_2010_2026_top100.parquet")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--boot", type=int, default=5000)
    args = ap.parse_args()

    recs = [json.loads(p.read_text()) for p in sorted((BACKEND / "results" / "llm_web").glob("*/*.json"))]
    recs = [r for r in recs if r.get("window") == args.window]
    if not recs:
        raise SystemExit(f"no {args.window} decisions yet")
    panel = Panel.load(pd.read_parquet(BACKEND / args.ohlcv), pd.read_csv(BACKEND / args.universe))
    opens = panel.open
    idx = opens.index

    rows = []
    for r in recs:
        d = pd.Timestamp(r["as_of"][:10])
        i = idx.searchsorted(d, side="right")  # next trading day: when the order would fill
        p = signal_p(r)
        fwd = None
        if i + H < len(idx) and r["ticker"] in opens.columns:
            o0, o1 = opens[r["ticker"]].iloc[i], opens[r["ticker"]].iloc[i + H]
            fwd = float(o1 / o0 - 1) if pd.notna(o0) and pd.notna(o1) else None
        rows.append({"day": d.date(), "ticker": r["ticker"], "p": p, "screen": r["screen_score"], "fwd": fwd,
                     "tools": r.get("tool_log", [])})
    df = pd.DataFrame(rows)

    def monthly_ic(col: str) -> tuple[list[float], dict]:
        ics = []
        for _, g in df.dropna(subset=["fwd", col]).groupby("day"):
            if len(g) >= 5 and g[col].nunique() > 1:
                ics.append(float(g[col].rank().corr(g["fwd"].rank())))
        if not ics:
            return ics, {"months": 0}
        a = np.array(ics)
        rng = np.random.default_rng(0)
        boots = a[rng.integers(0, len(a), size=(args.boot, len(a)))].mean(axis=1)
        lo, hi = np.percentile(boots, [2.5, 97.5])
        return ics, {"months": len(a), "mean_ic": round(float(a.mean()), 4), "ci_lo": round(float(lo), 4),
                     "ci_hi": round(float(hi), 4)}

    _, ic_llm = monthly_ic("p")
    _, ic_screen = monthly_ic("screen")
    diff = df.dropna(subset=["fwd", "p"]).groupby("day").apply(
        lambda g: g["p"].rank().corr(g["fwd"].rank()) - g["screen"].rank().corr(g["fwd"].rank())
        if len(g) >= 5 and g["p"].nunique() > 1 else np.nan, include_groups=False).dropna().to_numpy()
    rng = np.random.default_rng(1)
    b = diff[rng.integers(0, len(diff), size=(args.boot, len(diff)))].mean(axis=1) if len(diff) else np.array([0.0])
    ic_gain = {"mean": round(float(diff.mean()), 4) if len(diff) else None,
               "ci_lo": round(float(np.percentile(b, 2.5)), 4), "ci_hi": round(float(np.percentile(b, 97.5)), 4)}

    bars = bars_from_panel(panel)
    universe = set(df["ticker"])
    cfg = SimConfig(top_k=args.top_k, drawdown_halt=1.0)
    llm_sig = [{"cutoff": r.day, "ticker": r.ticker, "p": r.p} for r in df.itertuples() if r.p is not None]
    scr_sig = [{"cutoff": r.day, "ticker": r.ticker, "p": 0.45 + 0.1 * r.screen} for r in df.itertuples()]
    last = max(df["day"])
    j = idx.searchsorted(pd.Timestamp(last), side="right") + H
    end = idx[min(j, len(idx) - 1)].date()
    llm = simulate(llm_sig, bars, cfg, name="llm_web", universe=universe, end=end)
    scr = simulate(scr_sig, bars, cfg, name="screen_top10", universe=universe, end=end)
    spy = buy_and_hold(bars, "SPY", llm.days[0], end)

    tools = Counter()
    ok = Counter()
    had = Counter()
    for r in rows:
        names_ok = {e["tool"] for e in r["tools"] if e["ok"]}
        for e in r["tools"]:
            tools[e["tool"]] += 1
            ok[e["tool"]] += int(e["ok"])
        had["news"] += "news_as_of" in names_ok or "archived_page" in names_ok
        had["sec"] += bool(names_ok & {"sec_filings_as_of", "read_filing"})
        had["neither"] += not (names_ok & {"news_as_of", "archived_page", "sec_filings_as_of", "read_filing"})
    out = {"window": args.window, "decisions": len(df), "months": int(df["day"].nunique()),
           "ranking": {"llm": ic_llm, "screen": ic_screen, "llm_minus_screen": ic_gain},
           "paper_trading": {"period": [llm.days[0].isoformat(), end.isoformat()],
                             "spy": spy.metrics(), "llm_web": llm.metrics(), "screen_top10": scr.metrics(),
                             "llm_vs_screen": excess_vs(llm, scr), "llm_vs_market": beta_alpha(llm, spy),
                             "screen_vs_market": beta_alpha(scr, spy)},
           "tools": {t: {"calls": tools[t], "ok_pct": round(100 * ok[t] / tools[t], 1)} for t in sorted(tools)},
           "decisions_with": {k: round(100 * v / len(rows), 1) for k, v in had.items()},
           "distinct_p": int(df["p"].nunique())}
    (BACKEND / "results" / f"llm_web_report_{args.window}.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
