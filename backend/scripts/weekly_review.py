"""Weekly review of the forward test (docs/PLAN_60_V2.md, Stage 3). Run by hand; reads the ledgers, changes nothing in
them, and writes results/forward/review_<date>.md.

    python scripts/weekly_review.py

Sections:
  books     each allocator book's equity, return and drawdown since the start, and its brake state
  shadows   the braked master book at 1.0x / 1.5x / 2.0x: levered from its own run-to-run returns, the borrowed part
            paying rf + 1.5% a year (FRED DTB3). Paper only: nothing is traded at these sizes.
  events    the 1-week Bonsai book's scoreboard (forward_events.py): decisions on time, missed, outcomes, IC per
            source (Bonsai and the lite fallback are never mixed)
  failures  weekdays with no allocator or event run, and every missed decision with its reason
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd

from app.forward.ledger import Ledger
from app.portfolio.forward import brake_multiplier

FWD = BACKEND / "results" / "forward"
SPREAD = 0.015


def weekdays(a: date, b: date) -> list[date]:
    return [a + timedelta(days=i) for i in range((b - a).days + 1) if (a + timedelta(days=i)).weekday() < 5]


def shadows(eq: pd.Series, rf: pd.Series) -> dict[float, float]:
    out = {}
    for lev in (1.0, 1.5, 2.0):
        v = 1.0
        for (d0, e0), (d1, e1) in zip(eq.items(), list(eq.items())[1:], strict=False):
            days = (d1 - d0).days
            r = float(rf.asof(pd.Timestamp(d0))) if len(rf) else 0.0
            v *= 1 + lev * (e1 / e0 - 1) - (lev - 1) * (r + SPREAD) * days / 365
        out[lev] = v
    return out


def main() -> None:
    lines = [f"# Forward test review, {datetime.now(UTC).date().isoformat()}", ""]
    alloc = FWD / "allocator" / "ledger.jsonl"
    runs = [json.loads(x) for x in alloc.read_text().splitlines()] if alloc.exists() else []
    lines += ["## Books", ""]
    run_days: set[date] = set()
    if runs:
        run_days = {date.fromisoformat(r["run_at_utc"][:10]) for r in runs}
        lines += ["| Book | Start | Now | Return | Max drawdown | Brake |", "|---|---|---|---|---|---|"]
        names = sorted({k for r in runs for k in r["books"]})
        for n in names:
            pts = [(date.fromisoformat(r["data_through"]), r["books"][n]["equity"]) for r in runs if n in r["books"]]
            eq = pd.Series({d: e for d, e in pts})
            dd = float((1 - eq / eq.cummax()).max())
            lines.append(f"| {n} | {eq.iloc[0]:,.0f} | {eq.iloc[-1]:,.0f} | {eq.iloc[-1] / eq.iloc[0] - 1:+.2%} | "
                         f"{dd:.1%} | {brake_multiplier(eq.iloc[-1], eq.max()):.2f} |")
        base = "master+brakes" if any("master+brakes" in r["books"] for r in runs) else "master"
        eq = pd.Series({date.fromisoformat(r["data_through"]): r["books"][base]["equity"] for r in runs
                        if base in r["books"]})
        rf_f = BACKEND / "data" / "fred_dtb3.csv"
        rf = pd.Series(dtype=float)
        if rf_f.exists():
            df = pd.read_csv(rf_f)
            rf = pd.Series(pd.to_numeric(df.iloc[:, 1], errors="coerce").to_numpy() / 100,
                           index=pd.to_datetime(df.iloc[:, 0])).ffill().dropna()
        lines += ["", f"## Shadow books ({base}, levered on paper; borrowing at rf + 1.5%)", ""]
        lines += [f"- {lev:.1f}×: {v - 1:+.2%}" for lev, v in shadows(eq, rf).items()]
    else:
        lines.append("No allocator runs yet.")

    lines += ["", "## 1-week Bonsai book (shadow, 0 weight)", ""]
    led = Ledger(FWD / "events" / "ledger.jsonl")
    recs = led.verify()
    if recs:
        from forward_events import score
        s = score(recs)
        lines += [f"- {k}: {v}" for k, v in s.items()]
        run_days |= {date.fromisoformat(r["as_of"][:10]) for r in recs if r["type"] == "run"}
    else:
        lines.append("No event runs yet.")

    lines += ["", "## Failures", ""]
    if run_days:
        gap = [d.isoformat() for d in weekdays(min(run_days), datetime.now(UTC).date()) if d not in run_days]
        lines.append(f"- Weekdays with no run: {len(gap)}" + (f" ({', '.join(gap[-10:])})" if gap else ""))
    missed = [r for r in recs if r["type"] == "missed"]
    lines.append(f"- Missed decisions: {len(missed)}")
    reasons = pd.Series([r["reason"] for r in missed]).value_counts() if missed else pd.Series(dtype=int)
    lines += [f"  - {n} × {why}" for why, n in reasons.items()]
    out = FWD / f"review_{datetime.now(UTC).date().isoformat()}.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwritten to {out.relative_to(BACKEND)}")


if __name__ == "__main__":
    main()
