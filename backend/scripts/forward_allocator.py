"""Stage D: forward paper test of the SPY + crypto-trend allocator. Run it BY HAND, about once a week.

    python scripts/forward_allocator.py            # fetch prices, fill last run's orders, decide, log, git commit
    python scripts/forward_allocator.py --status   # print the ledger so far, change nothing

Paper money only. Free Yahoo prices. Nothing runs on its own (no service, no timer). Each run appends one line to
results/forward/allocator/ledger.jsonl and saves the books in state.json; both are committed to git by the run itself
so a result can't be edited after the fact (--no-commit to skip; pushing is left to you). Timing rules are in
app/portfolio/forward.py: orders are filled at the first open after the run's UTC date, never at a price seen before
the decision. Gate after 3 months (docs/PLAN_V2.md): the allocator's forward return and drawdown vs SPY within what
the 2018-2026 backtest's weekly returns allow.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import pandas as pd
import yfinance as yf

from app.portfolio.forward import books_from_json, books_to_json, new_books, step
from app.portfolio.master import MasterConfig
from app.sandbox.events import Prices

DIR = BACKEND / "results" / "forward" / "allocator"
ASSETS = ("SPY", "BTC-USD", "ETH-USD")


def fetch(now: datetime) -> Prices:
    frames = []
    for t in ASSETS:
        df = yf.download(t, start=(now - timedelta(days=400)).date().isoformat(),
                         end=(now + timedelta(days=1)).date().isoformat(), auto_adjust=True, progress=False,
                         multi_level_index=False)
        if df.empty:
            raise SystemExit(f"no prices for {t} from Yahoo; try again later")
        frames.append(df[["Open", "High", "Low", "Close", "Volume"]].assign(Ticker=t))
    long = pd.concat(frames).rename_axis("Date").reset_index()
    long["Date"] = pd.to_datetime(long["Date"]).dt.date.astype(str)
    return Prices.from_long(long)


def status() -> None:
    ledger = DIR / "ledger.jsonl"
    if not ledger.exists():
        print("no runs yet")
        return
    runs = [json.loads(x) for x in ledger.read_text().splitlines()]
    print(f"{len(runs)} runs, first {runs[0]['run_at_utc']}, last {runs[-1]['run_at_utc']}")
    for r in runs:
        eq = "  ".join(f"{k}: {v['equity']:>11,.2f}" for k, v in r["books"].items())
        print(f"{r['data_through']}  {eq}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--no-commit", action="store_true")
    args = ap.parse_args()
    if args.status:
        status()
        return
    DIR.mkdir(parents=True, exist_ok=True)
    state_path, ledger = DIR / "state.json", DIR / "ledger.jsonl"
    books = books_from_json(json.loads(state_path.read_text())) if state_path.exists() else new_books()
    now = datetime.now(UTC)
    if ledger.exists():
        last = json.loads(ledger.read_text().splitlines()[-1])
        if last["run_at_utc"][:10] == now.date().isoformat():
            raise SystemExit(f"already ran today ({last['run_at_utc']}); run again on a later day")
    p = fetch(now)
    rec = step(books, p.open, p.close, now, MasterConfig())
    with ledger.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    state_path.write_text(json.dumps(books_to_json(books), indent=1) + "\n")
    print(json.dumps(rec, indent=1))
    if not args.no_commit:
        repo = BACKEND.parent
        subprocess.run(["git", "-C", str(repo), "add", str(DIR)], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m",
                        f"forward allocator run {rec['run_at_utc']} (data through {rec['data_through']})"], check=True)
        print("committed to git (push when you like)")


if __name__ == "__main__":
    main()
