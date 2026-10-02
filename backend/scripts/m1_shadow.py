"""Forward shadow of M1, month-end Treasuries (docs/PLAN_60_V2.md, "M1 forward shadow"). No money, no orders.

    python scripts/m1_shadow.py            # record every finished month not yet in the ledger (downloads only then)
    python scripts/m1_shadow.py --status   # the record so far, change nothing
    python scripts/m1_shadow.py --verdict  # the registered verdict: once, at 24 months, run by Claude only

Rule: TLT over the last 3 trading days of each month, less the T-bill rate, 1 bp on the entry and the exit day.
One line per finished month from October 2026 in results/forward/m1/ledger.jsonl; lines are appended once and
never rewritten. Run by autorun as a side step of the live event runs: its exit code never counts.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import pandas as pd

from app.forward.ledger import jsonl_records, open_append
from app.sandbox.flows import month_end_records, month_end_verdict

OUT = BACKEND / "results" / "forward" / "m1"
FIRST_MONTH, MONTHS_TO_JUDGE, TRIAL = "2026-10", 24, "treasury_month_end_forward"
NY = ZoneInfo("America/New_York")
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3"


def months(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in recs if r.get("type") == "month"]


def due(recs: list[dict[str, Any]], now: datetime) -> list[str]:
    """The months from October 2026 to last month that have no line yet."""
    have = {r["month"] for r in months(recs)}
    last = pd.Period(now.astimezone(NY).strftime("%Y-%m"), "M") - 1
    return [str(m) for m in pd.period_range(FIRST_MONTH, last, freq="M") if str(m) not in have]


def closes(start: str, now: datetime) -> pd.Series:
    """TLT adjusted daily closes from Yahoo, whole days only (today's bar counts after 16:30 New York time)."""
    import yfinance as yf  # type: ignore[import-untyped]
    df = yf.download("TLT", start=start, auto_adjust=True, progress=False, multi_level_index=False)
    s: pd.Series = df["Close"].dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    et = now.astimezone(NY)
    whole = et.date() if (et.hour, et.minute) >= (16, 30) else (et - pd.Timedelta(days=1)).date()
    return s[s.index.date <= whole]


def tbill() -> pd.Series:
    """The 3-month T-bill rate (FRED DTB3) as a fraction a year."""
    req = urllib.request.Request(FRED, headers={"User-Agent": "Mozilla/5.0"})
    df = pd.read_csv(io.BytesIO(urllib.request.urlopen(req, timeout=60).read()))
    s = pd.to_numeric(df.iloc[:, 1], errors="coerce")
    return pd.Series(s.to_numpy() / 100, index=pd.to_datetime(df.iloc[:, 0])).ffill()


def collect(out: Path, now: datetime, close: pd.Series | None = None, rf: pd.Series | None = None) -> list[str]:
    """Append a line for every due month the data shows as finished. Returns the months written."""
    ledger = out / "ledger.jsonl"
    want = due(jsonl_records(ledger), now)
    if not want:
        return []
    start = (pd.Period(want[0], "M") - 1).start_time.date().isoformat()
    close = closes(start, now) if close is None else close
    if close.empty or not ((close > 0) & close.notna()).all():
        raise ValueError("m1 shadow data check: TLT closes missing or invalid")
    rf = tbill() if rf is None else rf
    new = [r for r in month_end_records(close, rf, FIRST_MONTH) if r["month"] in want]
    out.mkdir(parents=True, exist_ok=True)
    with open_append(ledger) as f:
        for r in new:
            f.write(json.dumps({"type": "month", **r, "written_at": now.isoformat(timespec="seconds")}) + "\n")
    return [r["month"] for r in new]


def status(out: Path) -> dict[str, Any]:
    recs = jsonl_records(out / "ledger.jsonl")
    ms = months(recs)
    net = [r["net"] for r in ms]
    return {"months": len(ms), "last": ms[-1]["month"] if ms else None,
            "mean_net_bp": round(1e4 * sum(net) / len(net), 1) if net else None,
            "hit_rate": round(sum(x > 0 for x in net) / len(net), 2) if net else None,
            "ready_to_judge": len(ms) >= MONTHS_TO_JUDGE,
            "verdict": next((r for r in recs if r.get("type") == "verdict"), None)}


def verdict(out: Path, now: datetime) -> dict[str, Any]:
    from app.sandbox.dsr import register
    ledger = out / "ledger.jsonl"
    recs = jsonl_records(ledger)
    if any(r.get("type") == "verdict" for r in recs):
        raise SystemExit("the verdict was already run (one run only)")
    ms = months(recs)[:MONTHS_TO_JUDGE]
    if len(ms) < MONTHS_TO_JUDGE:
        raise SystemExit(f"{len(ms)} of {MONTHS_TO_JUDGE} months: not judged earlier")
    v = month_end_verdict(ms)
    res = {"type": "verdict", **v, "pass": bool(v["sharpe"] >= 0.5 and v["diff_lo80_bp"] > 0),
           "written_at": now.isoformat(timespec="seconds")}
    with open_append(ledger) as f:
        f.write(json.dumps(res) + "\n")
    register({"trial": TRIAL, "date": now.strftime("%Y-%m-%d"), "kind": "forward", "sharpe_ann": v["sharpe"],
              "window": f"{ms[0]['month']}..{ms[-1]['month']}", "result": "pass" if res["pass"] else "fail"})
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="results/forward/m1")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--verdict", action="store_true")
    a = ap.parse_args()
    out, now = BACKEND / a.out, datetime.now(UTC)
    if a.verdict:
        print(json.dumps(verdict(out, now)))
        return
    if not a.status:
        new = collect(out, now)
        if new:
            print("month-end Treasuries (shadow): recorded", ", ".join(new))
    print("month-end Treasuries (shadow):", json.dumps(status(out)))


if __name__ == "__main__":
    main()
