"""Forward paper test of the master allocator (SPY core + BTC/ETH trend sleeve), one manual run at a time.

Each run (scripts/forward_allocator.py) does, for every book (the allocator and two benchmarks):
  1. fill the orders decided at the previous run, at the open of the first SPY trading day strictly AFTER the UTC
     date of that run (so the fill price was set after the decision, for stocks and for crypto, whose Yahoo daily
     bar opens at 00:00 UTC); if that open is not in the data yet, the orders stay pending;
  2. mark the book at the latest complete close (bars dated before today are complete; today's bar is ignored);
  3. decide new targets from those closes only and leave them pending for the next run.
Paper money only: nothing here places a real order. Books: "master" (allocate() with no stock picks),
"SPY" (98% SPY, bought once), "80/20 SPY/BTC" (rebalanced at each run).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

from app.portfolio.master import MasterConfig, allocate, crypto_state

FRACTIONAL = ("BTC-USD", "ETH-USD")


@dataclass
class Book:
    name: str
    cash: float = 100_000.0
    positions: dict[str, float] = field(default_factory=dict)
    pending: dict[str, float] | None = None       # target weights decided at `decided_at`
    decided_at: str | None = None                 # ISO UTC time of the decision
    trades: int = 0
    costs: float = 0.0


def fill_day(days: pd.DatetimeIndex, decided_at: str) -> pd.Timestamp | None:
    """First trading day whose date is strictly after the decision's UTC date."""
    d = datetime.fromisoformat(decided_at).date()
    later = days[days.date > d]
    return later[0] if len(later) else None


def execute(book: Book, opens: pd.Series, cost_bps: float = 5.0) -> dict[str, Any]:
    """Trade the pending targets at these opens: sells first, whole shares except crypto, cash never negative."""
    assert book.pending is not None
    s = cost_bps / 1e4
    px = {a: float(v) for a, v in opens.items() if pd.notna(v)}
    if any(a not in px for a in book.positions):  # can't value the book at this open: wait for the next run
        return {"fills": [], "skipped": "no open price for a held asset; orders stay pending"}
    equity = book.cash + sum(q * px[a] for a, q in book.positions.items())
    want = {a: (equity * w / (px[a] * (1 + s))) for a, w in book.pending.items() if a in px}
    want = {a: (q if a in FRACTIONAL else math.floor(q)) for a, q in want.items()}
    fills = []
    for a in sorted(set(book.positions) | set(want)):
        delta = want.get(a, 0.0) - book.positions.get(a, 0.0)
        if delta < 0 and a in px:
            book.cash += -delta * px[a] * (1 - s)
            book.costs += -delta * px[a] * s
            book.positions[a] = book.positions.get(a, 0.0) + delta
            book.trades += 1
            fills.append({"asset": a, "qty": round(delta, 8), "price": px[a]})
    for a in sorted(want):
        delta = want[a] - book.positions.get(a, 0.0)
        if delta > 0:
            afford = book.cash / (px[a] * (1 + s))
            q = min(delta, afford if a in FRACTIONAL else math.floor(afford))
            if q > 0:
                book.cash -= q * px[a] * (1 + s)
                book.costs += q * px[a] * s
                book.positions[a] = book.positions.get(a, 0.0) + q
                book.trades += 1
                fills.append({"asset": a, "qty": round(q, 8), "price": px[a]})
    book.positions = {a: q for a, q in book.positions.items() if q > 1e-12}
    assert book.cash >= -1e-6
    book.pending = None
    return {"fills": fills}


def targets(name: str, close: pd.DataFrame, day: pd.Timestamp, cfg: MasterConfig, held: dict[str, float]
            ) -> tuple[dict[str, float] | None, dict[str, Any]]:
    if name == "master":
        a = allocate([], crypto_state(close, day, cfg.crypto_assets), cfg)
        return a.weights, {"dropped": a.dropped}
    if name == "SPY":
        return (None if held else {"SPY": 0.98}), {}
    return {"SPY": 0.78, "BTC-USD": 0.20}, {}


def step(books: dict[str, Book], opens: pd.DataFrame, closes: pd.DataFrame, now_utc: datetime, cfg: MasterConfig
         ) -> dict[str, Any]:
    """One forward run. `opens`/`closes` are on the SPY trading calendar; bars dated on or after today's UTC date
    are dropped (possibly incomplete)."""
    today = now_utc.date()
    opens = opens[pd.DatetimeIndex(opens.index).date < today]
    closes = closes[pd.DatetimeIndex(closes.index).date < today]
    days = pd.DatetimeIndex(closes.index)
    last = days[-1]
    rec: dict[str, Any] = {"run_at_utc": now_utc.isoformat(timespec="seconds"),
                           "data_through": last.date().isoformat(), "books": {}}
    for name, b in books.items():
        r: dict[str, Any] = {}
        if b.pending is not None and b.decided_at is not None:
            fd = fill_day(days, b.decided_at)
            if fd is not None:
                r["filled_on"] = fd.date().isoformat()
                r.update(execute(b, pd.Series(opens.loc[fd])))
            else:
                r["waiting_for_open_after"] = datetime.fromisoformat(b.decided_at).date().isoformat()
        # each position at its last known close (a missing bar must not drop the position from equity)
        px = {a: float(closes[a].loc[:last].dropna().iloc[-1]) for a in b.positions}
        r["equity"] = round(b.cash + sum(q * px[a] for a, q in b.positions.items()), 2)
        r["positions"] = {a: round(q, 6) for a, q in b.positions.items()}
        if b.pending is None:
            t, info = targets(name, closes, last, cfg, b.positions)
            if t is not None:
                b.pending, b.decided_at = t, now_utc.isoformat(timespec="seconds")
                r["new_targets"] = {a: round(w, 4) for a, w in t.items()}
                r.update(info)
        rec["books"][name] = r
    return rec


def books_to_json(books: dict[str, Book]) -> dict[str, Any]:
    return {k: asdict(v) for k, v in books.items()}


def books_from_json(d: dict[str, Any]) -> dict[str, Book]:
    return {k: Book(**v) for k, v in d.items()}


def new_books() -> dict[str, Book]:
    return {n: Book(n) for n in ("master", "SPY", "80/20 SPY/BTC")}


def first_run_date(ledger: list[dict[str, Any]]) -> date | None:
    return date.fromisoformat(ledger[0]["run_at_utc"][:10]) if ledger else None
