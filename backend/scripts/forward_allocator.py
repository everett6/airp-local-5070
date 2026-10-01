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
import math
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
from app.portfolio.forward import (
    COST_BPS,
    LEVERAGE,
    Book,
    books_from_json,
    books_to_json,
    new_books,
    step,
)
from app.portfolio.guard import HALT, apply_drawdown_limit, drawdown_status, halt, halt_info, state
from app.portfolio.master import MasterConfig
from app.sandbox.events import Prices

DIR = BACKEND / "results" / "forward" / "allocator"
ASSETS = ("SPY", "BTC-USD", "ETH-USD", "SGOV")


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


REAL_BOOK = ("master+brakes", "master")  # the book the drawdown limit watches (the first one present)


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


def validate_prices(p: Prices, now: datetime) -> None:
    """Reject missing, nonpositive or stale closes before making allocator decisions."""
    complete = p.close[pd.DatetimeIndex(p.close.index).date < now.date()]
    if complete.empty:
        raise ValueError("allocator data check: no completed price bars")
    for asset in ASSETS:
        if asset not in complete:
            raise ValueError(f"allocator data check: {asset} price column absent")
        s = complete[asset].dropna()
        if s.empty or not math.isfinite(float(s.iloc[-1])) or float(s.iloc[-1]) <= 0:
            raise ValueError(f"allocator data check: {asset} close missing or invalid")
        last = pd.Timestamp(s.index[-1]).date()
        if (now.date() - last).days > 5:
            raise ValueError(f"allocator data check: {asset} close stale ({last})")


def validate_result(rec: dict, books: dict[str, Book]) -> None:
    """Reject invalid book output before persisting state or a clean ledger line."""
    if not rec.get("books") or not rec.get("price_sources"):
        raise ValueError("allocator result check: book or price sources absent")
    for name, result in rec["books"].items():
        if name in LEVERAGE:  # the user's aggressive book is separate: its faults are reported, never fatal
            bad = [k for k in ("error", "rejected", "rejected_at_fill", "wiped_out") if k in result]
            if bad:
                rec.setdefault("aggressive_issues", []).append(f"{name}: {', '.join(bad)}")
                print(f"AGGRESSIVE BOOK: {name}: {bad} "
                      f"{result.get('error') or result.get('rejected') or result.get('rejected_at_fill') or ''}")
            continue
        if "rejected" in result or "rejected_at_fill" in result:
            raise ValueError(f"allocator result check: {name} target weights rejected")
        equity = result.get("equity")
        if not isinstance(equity, (int, float)) or not math.isfinite(equity) or equity <= 0:
            raise ValueError(f"allocator result check: {name} equity invalid")
        cost = books[name].costs
        if not math.isfinite(cost) or cost < 0:
            raise ValueError(f"allocator result check: {name} trading cost invalid")


def resume_books(state_path: Path) -> list[str]:
    """--resume also lets a book that passed its own drawdown limit buy again. Returns the books it cleared."""
    if not state_path.exists():
        return []
    saved = json.loads(state_path.read_text())
    cleared = [n for n, b in saved.items() if b.get("reducing")]
    for n in cleared:
        saved[n]["reducing"] = False
    if cleared:
        state_path.write_text(json.dumps(saved, indent=1) + "\n")
    return cleared


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
    ap.add_argument("--dir", default="", help="a separate books/ledger folder for dry runs (never the real books)")
    ap.add_argument("--halt", metavar="REASON", help="turn the kill switch on and exit")
    ap.add_argument("--reduce", metavar="REASON", help="reduce-only mode on and exit")
    ap.add_argument("--resume", action="store_true", help="turn the kill switch off and exit")
    args = ap.parse_args()
    global DIR
    dry = bool(args.dir)
    if dry:
        DIR = BACKEND / args.dir
        if DIR.resolve() == (BACKEND / "results" / "forward" / "allocator").resolve():
            raise SystemExit("--dir is for dry runs: give it a folder of its own")
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
        cleared = resume_books(DIR / "state.json")
        if cleared:
            print("may buy again:", ", ".join(cleared))
        return
    DIR.mkdir(parents=True, exist_ok=True)
    state_path, ledger = DIR / "state.json", DIR / "ledger.jsonl"
    books = books_from_json(json.loads(state_path.read_text())) if state_path.exists() else new_books()
    now = datetime.now(UTC)
    if ledger.exists():
        history = [json.loads(line) for line in ledger.read_text().splitlines()]
        if not history:
            raise ValueError("allocator ledger check: empty ledger")
        last = history[-1]
        if last["run_at_utc"][:10] == now.date().isoformat():
            raise SystemExit(f"already ran today ({last['run_at_utc']}); run again on a later day")
    p = fetch(now)
    validate_prices(p, now)
    mode = state()
    if mode != "ACTIVE":
        print(f"trading state {mode}:", "marking only, nothing fills or is decided." if mode == "HALTED" else
              "orders may only shrink positions.", halt_info())
    rec = step(books, p.open, p.close, now, MasterConfig(), halted=mode == "HALTED", reducing=mode == "REDUCING")
    rec["price_sources"] = dict(SOURCES)
    rec["assumed_cost_bps"] = COST_BPS
    validate_result(rec, books)
    name = next((n for n in REAL_BOOK if n in rec["books"]), None)
    if name is not None:
        rec["drawdown"] = apply_drawdown_limit(name, rec["books"][name]["equity"], books[name].peak,
                                               path=DIR / "HALT" if dry else HALT)
        if rec["drawdown"].get("action"):
            print("DRAWDOWN", rec["drawdown"])
    # the aggressive book against its own limits (mandate "books" section). At its limit only THIS book stops buying
    # (its own flag): the kill switch is shared, and the frozen books must never change because of this book.
    for name in LEVERAGE:
        if "equity" in rec["books"].get(name, {}) and not rec["books"][name].get("wiped_out"):
            dd = drawdown_status(name, rec["books"][name]["equity"], books[name].peak)
            rec.setdefault("drawdown_leveraged", {})[name] = dd
            if dd.get("action") == "REDUCING":
                books[name].reducing = True
            if dd.get("action"):
                print("DRAWDOWN", dd)
    with ledger.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    state_path.write_text(json.dumps(books_to_json(books), indent=1) + "\n")
    print(json.dumps(rec, indent=1))
    if not args.no_commit and not dry:
        repo = BACKEND.parent
        subprocess.run(["git", "-C", str(repo), "add", str(DIR)], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m",
                        f"forward allocator run {rec['run_at_utc']} (data through {rec['data_through']})"], check=True)
        print("committed to git (push when you like)")


if __name__ == "__main__":
    main()
