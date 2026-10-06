"""What the AI's choice of stocks adds, measured apart from everything else (docs/PLAN_60_V2.md, "Outside review,
second part", new records). A description, not a test: it decides nothing.

    python scripts/ai_contribution.py                # weekly review: write results/forward/ai_contribution.json
    python scripts/ai_contribution.py --shuffles 2000

The AI-picks sleeve is replayed over the forward ledger, run by run, three ways. Everything except the choice is
identical: the same releases, run times, slots, sizing, 5-day holds, opens and costs.
  - **AI**: the judge's real scores (this must reproduce the live book; the report says whether it does);
  - **shuffled**: the same scores dealt out at random among the on-time decisions (500 seeded deals), so the same
    number of releases qualify but which ones is chance;
  - **every release**: each on-time decision qualifies while a slot is free.
Reported for each: net return, pairs, turnover, mean gross exposure; and for the AI against the shuffles: the
difference, the share of shuffles it beats, and the range the shuffles span. Whole-book returns cannot show this:
a hedged pair can make or lose money for reasons that have nothing to do with which stock was picked.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd

from app.forward.ledger import Ledger
from app.portfolio import sleeve

TAKE_ALL = 99.0  # a score above any threshold


def run_times(recs: list[dict[str, Any]]) -> list[tuple[datetime, list[dict[str, Any]]]]:
    """Each completed run with the decisions the sleeve could see at it (everything written before its `run` line)."""
    out, known = [], []
    for r in recs:
        if r.get("type") == "decision":
            known.append(r)
        elif r.get("type") == "run":
            out.append((datetime.fromisoformat(r["as_of"]), list(known)))
    return out


def replay(runs: list[tuple[datetime, list[dict[str, Any]]]], opens: pd.DataFrame, closes: pd.DataFrame,
           etf_of: dict[str, str], score: dict[str, float] | None, end: datetime) -> dict[str, Any]:
    """The sleeve's state after stepping through every run (and once more at `end`), with `score` replacing the
    judge's log-odds per accession when given. Prices at a run are the bars dated before that run's day, as live."""
    st = sleeve.new_state()
    days = pd.DatetimeIndex(opens.index)
    for now, decisions in [*runs, (end, runs[-1][1] if runs else [])]:
        n = int(days.searchsorted(pd.Timestamp(now.date())))
        ds = decisions if score is None else [{**d, "logodds": score.get(d["accession"], d.get("logodds"))}
                                              for d in decisions]
        sleeve.step(st, ds, opens.iloc[:n], closes.iloc[:n], etf_of, now)
    return st


def measure(st: dict[str, Any], n_days: int) -> dict[str, Any]:
    pairs = [p for p in st["pairs"] if p["status"] in ("open", "closed")]
    closed = [p for p in pairs if p["status"] == "closed"]
    traded = sum(p["qty"] * p["entry_open"] + p["etf_qty"] * p["etf_entry_open"] for p in pairs) \
        + sum(p["qty"] * p["exit_open"] + p["etf_qty"] * p["etf_exit_open"] for p in closed)
    held = sum((p["qty"] * p["entry_open"] + p["etf_qty"] * p["etf_entry_open"])
               * (sleeve.HOLD if p["status"] == "closed" else min(sleeve.HOLD, max(1, n_days))) for p in pairs)
    return {"net_return": round(st["equity"] / sleeve.CAPITAL - 1, 5), "pairs": len(pairs), "closed": len(closed),
            "skipped": sum(p["status"] == "skipped" for p in st["pairs"]),
            "mean_ret_per_pair": round(float(np.mean([p["ret"] for p in closed])), 5) if closed else None,
            "turnover": round(traded / sleeve.CAPITAL, 3),
            "mean_gross_exposure": round(held / (max(1, n_days) * sleeve.CAPITAL), 3),
            "costs": round(sleeve.COST * traded, 2)}


def same_book(replayed: dict[str, Any], live: dict[str, Any]) -> bool:
    def key(st: dict[str, Any]) -> list[tuple[Any, ...]]:
        return sorted((p["accession"], p["status"], p["qty"], p["etf_qty"]) for p in st.get("pairs", []))
    return key(replayed) == key(live)


def differences(replayed: dict[str, Any], live: dict[str, Any]) -> list[dict[str, Any]]:
    """Actual discrepancies, without changing the book or forcing the replay to match."""
    by = {p["accession"]: p for p in replayed.get("pairs", [])}
    actual = {p["accession"]: p for p in live.get("pairs", [])}
    return [{"accession": a, "replayed": {k: by.get(a, {}).get(k) for k in ("status", "qty", "etf_qty", "note")},
             "live": {k: actual.get(a, {}).get(k) for k in ("status", "qty", "etf_qty", "note")}}
            for a in sorted(set(by) | set(actual)) if any(by.get(a, {}).get(k) != actual.get(a, {}).get(k)
                                                         for k in ("status", "qty", "etf_qty"))]


def contribution(recs: list[dict[str, Any]], opens: pd.DataFrame, closes: pd.DataFrame, etf_of: dict[str, str],
                 end: datetime, shuffles: int = 500, seed: int = 0, live_book: dict[str, Any] | None = None
                 ) -> dict[str, Any]:
    runs = run_times(recs)
    if not runs:
        return {"at": end.isoformat(timespec="seconds"), "note": "no completed run yet"}
    days = pd.DatetimeIndex(opens.index)
    n_days = int(days.searchsorted(pd.Timestamp(end.date()))) - int(days.searchsorted(pd.Timestamp(runs[0][0].date())))
    eligible = [d for d in runs[-1][1] if d.get("on_time") and d.get("source") == "bonsai"]
    accs = [d["accession"] for d in eligible]
    scores = [float(d.get("logodds") or 0.0) for d in eligible]
    ai_state = replay(runs, opens, closes, etf_of, None, end)
    ai = measure(ai_state, n_days)
    every = measure(replay(runs, opens, closes, etf_of, dict.fromkeys(accs, TAKE_ALL), end), n_days)
    rng = np.random.default_rng(seed)
    rets, pairs = [], []
    for _ in range(shuffles if len(accs) > 1 else 0):
        deal = dict(zip(accs, rng.permutation(scores), strict=True))
        m = measure(replay(runs, opens, closes, etf_of, deal, end), n_days)
        rets.append(m["net_return"])
        pairs.append(m["pairs"])
    out: dict[str, Any] = {
        "at": end.isoformat(timespec="seconds"), "trading_days": n_days, "on_time_bonsai_decisions": len(accs),
        "qualifying": sum(s >= sleeve.THRESHOLD for s in scores), "ai": ai, "every_release": every,
        "replay_matches_live_book": None if live_book is None else same_book(ai_state, live_book),
        "note": "The sleeve is untested. With few closed pairs every number here is noise; read the range.",
    }
    if rets:
        r = np.array(rets)
        out["shuffled"] = {"deals": len(rets), "mean_net_return": round(float(r.mean()), 5),
                           "p05": round(float(np.percentile(r, 5)), 5), "p95": round(float(np.percentile(r, 95)), 5),
                           "mean_pairs": round(float(np.mean(pairs)), 2)}
        out["ai_minus_shuffled"] = round(ai["net_return"] - float(r.mean()), 5)
        # ties count half: with one or two pairs many deals are the AI's own book
        out["share_of_deals_ai_beats"] = round(float((r < ai["net_return"]).mean() + 0.5 * (r == ai["net_return"]).mean()), 3)
        out["ai_minus_every_release"] = round(ai["net_return"] - every["net_return"], 5)
    matched = out["replay_matches_live_book"]
    out["comparison_valid"] = matched is not False
    out["basis"] = "hypothetical simulation replay; not actual broker returns"
    out["reconciliation"] = differences(ai_state, live_book) if live_book is not None else []
    if matched is False:
        out["note"] = "Comparison blocked: revised prices or missing run inputs do not reproduce the live simulator. Hypothetical arm metrics are not evidence of live AI contribution."
        for key in ("ai_minus_shuffled", "share_of_deals_ai_beats", "ai_minus_every_release"):
            out[key] = None
    return out


def prospective(rows: list[dict[str, Any]], etf_of: dict[str, str], live: dict[str, Any],
                shuffles: int = 500) -> dict[str, Any]:
    """Common legacy starting holdings; only newly observed decisions are randomized."""
    from app.portfolio.sleeve_replay import replay as exact_replay
    from app.portfolio.sleeve_replay import state as replay_state
    actual = exact_replay(rows, etf_of)
    if replay_state(actual) != replay_state(live):
        raise ValueError("Prospective inputs do not reproduce the current simulator state")
    initial = rows[0]["before"]
    seen = set(initial["seen"])
    new = {d["accession"]: d for row in rows for d in row["decisions"]
           if d.get("type") == "decision" and d["accession"] not in seen and d.get("on_time") and d.get("source") == "bonsai"}
    history_days = max(1, len(actual["history"]) - len(initial["history"]))
    baseline = measure(initial, history_days)

    def measured(book: dict[str, Any]) -> dict[str, Any]:
        result = measure(book, history_days)
        result["net_return"] = book["equity"] / initial["equity"] - 1
        result["turnover"] = result["turnover"] - baseline["turnover"]
        result["costs"] = result["costs"] - baseline["costs"]
        result["mean_gross_exposure"] = None  # legacy/intraday holdings need a complete dated exposure series
        return result

    ai = measured(actual)
    all_state = exact_replay(rows, etf_of, dict.fromkeys(new, TAKE_ALL))
    every = measured(all_state)
    rng = np.random.default_rng(0)
    scores = [float(d.get("logodds") or 0) for d in new.values()]
    rets = [measured(exact_replay(rows, etf_of, dict(zip(new, rng.permutation(scores), strict=True))))["net_return"]
            for _ in range(shuffles if len(scores) > 1 else 0)]
    return {"scope": "Exact prospective inputs; all arms inherit identical legacy holdings. Only new decisions differ.",
            "start": rows[0]["as_of"], "steps": len(rows), "matches_live_state": True,
            "eligible_new_decisions": len(new), "ai": ai, "every_release": every,
            "ai_minus_every_release": ai["net_return"] - every["net_return"],
            "ai_minus_shuffled": ai["net_return"] - float(np.mean(rets)) if rets else None,
            "shuffle_range": [float(np.percentile(rets, 5)), float(np.percentile(rets, 95))] if rets else None,
            "uncertainty": None, "qualification": "descriptive only; no statistical qualification or broker-return claim"}


def main() -> None:
    from event_eval import SECTOR_ETF

    from app.sandbox.events import Prices
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--events", default="results/forward/events")
    ap.add_argument("--picks", default="results/forward/ai_picks")
    ap.add_argument("--out", default="results/forward/ai_contribution.json")
    ap.add_argument("--shuffles", type=int, default=500)
    a = ap.parse_args()
    ev = BACKEND / a.events
    if not (ev / "ledger.jsonl").exists() or not (ev / "prices.parquet").exists():
        print("ai contribution: no event ledger or prices yet")
        return
    p = Prices.from_long(pd.read_parquet(ev / "prices.parquet"))
    book = BACKEND / a.picks / "book.json"
    live = json.loads(book.read_text()) if book.exists() else None
    rep = contribution(Ledger(ev / "ledger.jsonl").records(), p.open, p.close, SECTOR_ETF, datetime.now(UTC),
                       a.shuffles, live_book=live)
    journal = book.parent / "replay_inputs.jsonl"
    if journal.exists():
        rows = Ledger(journal).verify()
        try:
            if live is None:
                raise ValueError("No live book to reconcile")
            rep["prospective_replay"] = prospective(rows, SECTOR_ETF, live, a.shuffles)
        except ValueError as exc:
            rep["prospective_replay"] = {"steps": len(rows), "matches_live_state": False, "error": str(exc)}
    out = BACKEND / a.out
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(rep, indent=1) + "\n")
    tmp.replace(out)
    if "ai" not in rep:
        print("ai contribution:", rep["note"])
        return
    print(f"ai contribution (a description, not a test): {rep['on_time_bonsai_decisions']} on-time decisions, "
          f"{rep['qualifying']} above the threshold, {rep['trading_days']} trading days")
    for name in ("ai", "every_release"):
        m = rep[name]
        print(f"  {name.replace('_', ' '):14} net {100 * m['net_return']:+.2f}%  pairs {m['pairs']} "
              f"(closed {m['closed']})  turnover {m['turnover']}x  mean gross {m['mean_gross_exposure']}x")
    if "shuffled" in rep and rep["comparison_valid"]:
        s = rep["shuffled"]
        print(f"  shuffled       net {100 * s['mean_net_return']:+.2f}% on average "
              f"({100 * s['p05']:+.2f}% to {100 * s['p95']:+.2f}% in 9 of 10 deals; {s['deals']} deals)")
        print(f"  AI minus shuffled: {100 * rep['ai_minus_shuffled']:+.2f} points; the AI beats "
              f"{100 * rep['share_of_deals_ai_beats']:.0f}% of the deals")
    if rep["replay_matches_live_book"] is False:
        print("LEARN ALERT: ai contribution: the replay of the AI's own scores does not reproduce the live sleeve book")


if __name__ == "__main__":
    main()
