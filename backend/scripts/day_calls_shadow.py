"""D13 forward shadow (docs/PLAN_60_V2.md "Day-trading round 8"): every "day" call of the deep-research pipeline
on a day-feasible stock, scored after its entry day. No orders. A side step of the scheduled event run.

    python scripts/day_calls_shadow.py            # record new calls, score finished ones
    python scripts/day_calls_shadow.py --status
    python scripts/day_calls_shadow.py --verdict  # once, at 300 scored calls; Claude only (it calls register())

Entry day: the first NYSE session whose 09:30 New York open comes after the research's decision time. One call per
stock and entry day: the latest research decided before that open. Score: side x [(Close/Open - 1) - (SPY Close /
SPY Open - 1)] - 12 bp, from Alpaca's free IEX daily bars (read with the main paper keys; data only).
"""
from __future__ import annotations

import json
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np

from app.forward.ledger import Ledger, jsonl_records, write_atomic
from app.forward.schedule import is_session

NY = ZoneInfo("America/New_York")
FWD = BACKEND / "results" / "forward"
DIR = FWD / "day_calls"
COST, N_VERDICT, TRIAL = 0.0012, 300, "daytrade_ai_daycalls_forward"


def entry_day(decided_at: datetime) -> date:
    t = decided_at.astimezone(NY)
    d = t.date()
    if not (is_session(d) and (t.hour, t.minute) < (9, 30)):
        d += timedelta(days=1)
        while not is_session(d):
            d += timedelta(days=1)
    return d


def calls(records: list[dict[str, Any]], feasible: set[str]) -> dict[str, dict[str, Any]]:
    """{ticker:entry_day: call} from research records: the latest research before each open, feasible stocks only."""
    out: dict[str, dict[str, Any]] = {}
    for r in sorted(records, key=lambda x: x["decided_at"]):
        day = (r.get("call_support") or {}).get("day")
        if r.get("status") != "decided" or not isinstance(day, dict) or day.get("label") not in ("1", "2", "4", "5"):
            continue
        if r["ticker"] not in feasible:
            continue
        d = entry_day(datetime.fromisoformat(r["decided_at"]))
        out[f"{r['ticker']}:{d.isoformat()}"] = {
            "ticker": r["ticker"], "entry_day": d.isoformat(), "side": 1 if day["label"] in ("4", "5") else -1,
            "strong": day["label"] in ("1", "5"), "support": day.get("support"), "decided_at": r["decided_at"]}
    return out


def score(call: dict[str, Any], o: float, c: float, so: float, sc: float) -> float:
    return float(call["side"] * ((c / o - 1) - (sc / so - 1)) - COST)


def bootstrap(scored: list[dict[str, Any]], n: int = 5000, seed: int = 0) -> tuple[float, float]:
    """95% interval of the mean net return per call, resampling entry days (calls on one day move together)."""
    days: dict[str, list[float]] = {}
    for s in scored:
        days.setdefault(s["entry_day"], []).append(s["net"])
    groups = list(days.values())
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(n):
        pick = [groups[i] for i in rng.integers(0, len(groups), len(groups))]
        flat = [x for g in pick for x in g]
        means.append(sum(flat) / len(flat))
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def main() -> None:
    ledger = DIR / "calls.jsonl"
    rows = jsonl_records(ledger)
    recorded = {r["key"] for r in rows if r.get("type") == "call"}
    scored = {r["key"]: r for r in rows if r.get("type") == "score"}
    if "--status" in sys.argv or "--verdict" in sys.argv:
        s = list(scored.values())
        out: dict[str, Any] = {"calls": len(recorded), "scored": len(s), "needed": N_VERDICT}
        if s:
            out["mean_net_bp"] = round(1e4 * sum(x["net"] for x in s) / len(s), 2)
        if "--verdict" in sys.argv:
            if (DIR / "verdict.json").exists() or len(s) < N_VERDICT:
                raise SystemExit("verdict already given or fewer than 300 scored calls")
            from app.sandbox.dsr import register
            lo, hi = bootstrap(s)
            out |= {"ci95_bp": [round(lo * 1e4, 2), round(hi * 1e4, 2)], "pass": bool(out["mean_net_bp"] > 0 and lo > 0)}
            write_atomic(DIR / "verdict.json", json.dumps(out, indent=1) + "\n")
            register({"trial": TRIAL, "date": time.strftime("%Y-%m-%d"), "kind": "daytrade",
                      "mean_net_bp": out["mean_net_bp"], "result": "pass" if out["pass"] else "fail"})
        print(json.dumps(out))
        return
    records = []
    for f in (FWD / "deep_research").glob("*/*.json"):
        if f.name != "summary.json":
            try:
                r = json.loads(f.read_text())
            except ValueError:
                continue
            if r.get("call_support"):
                records.append(r)
    feasible = {r["ticker"] for r in json.loads((FWD / "day_feasibility.json").read_text())["stocks"] if r["feasible"]} \
        if (FWD / "day_feasibility.json").exists() else set()
    log = Ledger(ledger)
    new = 0
    for key, c in calls(records, feasible).items():
        if key not in recorded and c["entry_day"] >= datetime.now(UTC).astimezone(NY).date().isoformat():
            log.append("call", key=key, at=datetime.now(UTC).isoformat(), **c)  # only calls recorded before their day
            recorded.add(key)
            new += 1
    due = [r for r in rows if r.get("type") == "call" and r["key"] not in scored
           and r["entry_day"] < datetime.now(UTC).astimezone(NY).date().isoformat()]
    done = 0
    if due:
        from app.portfolio.broker import DATA, Alpaca
        client = Alpaca.from_env()
        if client is None:
            raise SystemExit("D13: no Alpaca keys for market data")
        try:
            start = min(r["entry_day"] for r in due)
            syms = sorted({r["ticker"].replace("-", ".") for r in due} | {"SPY"})
            got = client._get(f"{DATA}/v2/stocks/bars", symbols=",".join(syms), timeframe="1Day", start=start,
                              feed="iex", limit=10000)
            bars = {(s, b["t"][:10]): b for s, bs in (got.get("bars") or {}).items() for b in bs}
        finally:
            client.c.close()
        for r in due:
            b, spy = bars.get((r["ticker"].replace("-", "."), r["entry_day"])), bars.get(("SPY", r["entry_day"]))
            if b and spy:
                log.append("score", key=r["key"], entry_day=r["entry_day"], ticker=r["ticker"], side=r["side"],
                           strong=r["strong"], support=r["support"],
                           net=score(r, float(b["o"]), float(b["c"]), float(spy["o"]), float(spy["c"])))
                done += 1
    print(f"D13 day-call shadow: {new} new calls recorded, {done} scored, "
          f"{len(scored) + done} of {N_VERDICT} scored for the verdict")


if __name__ == "__main__":
    main()
