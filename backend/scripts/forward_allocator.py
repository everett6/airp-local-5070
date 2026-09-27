"""Stage D: forward paper test of the SPY + crypto-trend allocator. Run it BY HAND, about once a week.

    python scripts/forward_allocator.py            # fetch prices, fill last run's orders, decide, log, git commit
    python scripts/forward_allocator.py --status   # print the ledger so far, change nothing
    python scripts/forward_allocator.py --halt "reason"   # kill switch on: later runs mark the books, trade nothing
    python scripts/forward_allocator.py --reduce "reason"  # reduce-only: positions may shrink, never grow
    python scripts/forward_allocator.py --resume   # kill switch off (the only way to turn it off)

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
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import httpx
import pandas as pd
import yfinance as yf

from app.data_ingestion import bars
from app.portfolio.forward import books_from_json, books_to_json, new_books, step
from app.portfolio.guard import HALT, halt, halt_info, state
from app.portfolio.master import MasterConfig
from app.sandbox.events import Prices

DIR = BACKEND / "results" / "forward" / "allocator"
ASSETS = ("SPY", "BTC-USD", "ETH-USD")


SOURCES: dict[str, str] = {}


def fallback(t: str, start: date, end: date) -> tuple[pd.DataFrame, str]:
    """When Yahoo returns nothing: Binance's public klines for crypto; for SPY, Alpaca (IEX feed) then Polygon, if the
    user has put their own free keys in backend/.env. The source used is written into the run's ledger record."""
    tries = [("binance", bars.binance_daily)] if t in bars.BINANCE_PAIRS else [("alpaca-iex", bars.alpaca_daily),
                                                                              ("polygon", bars.polygon_daily)]
    why = []
    for name, fn in tries:
        try:
            df = fn(t, start, end)
            if not df.empty:
                return df, name
            why.append(f"{name}: empty")
        except (bars.NoKeyError, httpx.HTTPError) as e:
            why.append(f"{name}: {e}")
    raise SystemExit(f"no prices for {t} from Yahoo or any fallback ({'; '.join(why)}); try again later")


def fetch(now: datetime) -> Prices:
    frames = []
    start, end = (now - timedelta(days=400)).date(), (now + timedelta(days=1)).date()
    for t in ASSETS:
        df = yf.download(t, start=start.isoformat(), end=end.isoformat(), auto_adjust=True, progress=False,
                         multi_level_index=False)
        SOURCES[t] = "yahoo"
        if df.empty:
            df, SOURCES[t] = fallback(t, start, end)
            print(f"Yahoo had no prices for {t}; using {SOURCES[t]}")
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
    ap.add_argument("--halt", metavar="REASON", help="turn the kill switch on and exit")
    ap.add_argument("--reduce", metavar="REASON", help="reduce-only mode on and exit")
    ap.add_argument("--resume", action="store_true", help="turn the kill switch off and exit")
    args = ap.parse_args()
    if args.status:
        status()
        print("kill switch:", halt_info() or "off")
        return
    if args.halt:
        halt(args.halt, by="forward_allocator.py --halt")
        print("kill switch ON:", halt_info())
        return
    if args.reduce:
        halt(args.reduce, by="forward_allocator.py --reduce", mode="REDUCING")
        print("trading state:", state(), halt_info())
        return
    if args.resume:
        info = halt_info()
        HALT.unlink(missing_ok=True)
        print("kill switch OFF" + (f" (was: {info})" if info else " (it was not on)"))
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
    mode = state()
    if mode != "ACTIVE":
        print(f"trading state {mode}:", "marking only, nothing fills or is decided." if mode == "HALTED" else
              "orders may only shrink positions.", halt_info())
    rec = step(books, p.open, p.close, now, MasterConfig(), halted=mode == "HALTED", reducing=mode == "REDUCING")
    rec["price_sources"] = dict(SOURCES)
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
