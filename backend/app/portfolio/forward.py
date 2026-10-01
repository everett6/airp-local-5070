"""Forward paper test of the master allocator (SPY core + BTC/ETH trend sleeve), one manual run at a time.

Each run (scripts/forward_allocator.py) does, for every book (the allocator and two benchmarks):
  1. fill the orders decided at the previous run, at the open of the first SPY trading day strictly AFTER the UTC
     date of that run (so the fill price was set after the decision, for stocks and for crypto, whose Yahoo daily
     bar opens at 00:00 UTC); if that open is not in the data yet, the orders stay pending;
  2. mark the book at the latest complete close (bars dated before today are complete; today's bar is ignored);
  3. decide new targets from those closes only and leave them pending for the next run.
Paper money only: nothing here places a real order. Books: "master" (allocate() with no stock picks),
"master+brakes" (the same with the drawdown brakes on its own equity), "SPY" (98% SPY, bought once),
"80/20 SPY/BTC" (rebalanced at each run), and "aggressive 2.5x" (docs/PLAN_60_V2.md "Aggressive book, 2.5x", the
user's decision of 2026-09-30): 2.5 times the "master+brakes" weights on borrowed paper money, with interest on the
borrowed cash and its own mandate limits. It is a separate book: a fault in it is reported and never stops the others.
Safety (app/portfolio/guard.py): targets are gated against the mandate when decided and again before they fill (fail
closed, the whole set is rejected and logged); with the kill switch on, books are marked but nothing fills or is decided.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.portfolio.guard import MANDATE, gate
from app.portfolio.guard import reduce_only as cap_to_current
from app.portfolio.master import MasterConfig, allocate, crypto_state

FRACTIONAL = ("BTC-USD", "ETH-USD")
COST_BPS = 5.0  # assumed one-way simulator trading cost
AGGRESSIVE = "aggressive 2.5x"
LEVERAGE = {AGGRESSIVE: 2.5}  # book -> multiple of the master+brakes weights; these books may borrow
BORROW_RATE = 0.05            # a year, on negative cash, per calendar day between runs (fixed in the spec)


@dataclass
class Book:
    name: str
    cash: float = 100_000.0
    positions: dict[str, float] = field(default_factory=dict)
    pending: dict[str, float] | None = None       # target weights decided at `decided_at`
    decided_at: str | None = None                 # ISO UTC time of the decision
    trades: int = 0
    costs: float = 0.0
    peak: float = 0.0                             # highest equity marked so far (drawdown brakes)
    interest: float = 0.0                         # interest paid on borrowed cash so far (leveraged books)
    interest_through: str | None = None           # the date interest was last charged up to
    wiped: bool = False                           # a leveraged book whose equity reached zero: closed for good
    reducing: bool = False                        # a leveraged book past its own drawdown limit: it may only sell


def fill_day(days: pd.DatetimeIndex, decided_at: str) -> pd.Timestamp | None:
    """First trading day whose date is strictly after the decision's UTC date."""
    d = datetime.fromisoformat(decided_at).date()
    later = days[days.date > d]
    return later[0] if len(later) else None


def execute(book: Book, opens: pd.Series, cost_bps: float = COST_BPS, reduce_only: bool = False) -> dict[str, Any]:
    """Trade the pending targets at these opens: sells first, whole shares except crypto, cash never negative,
    except that a leveraged book (LEVERAGE) may borrow up to (its multiple - 1) times its equity.
    `reduce_only` (trading state REDUCING): no quantity may grow, so only sells happen."""
    assert book.pending is not None
    s = cost_bps / 1e4
    px = {a: float(v) for a, v in opens.items() if pd.notna(v)}
    if any(a not in px for a in book.positions):  # can't value the book at this open: wait for the next run
        return {"fills": [], "skipped": "no open price for a held asset; orders stay pending"}
    equity = book.cash + sum(q * px[a] for a, q in book.positions.items())
    if book.name in LEVERAGE and equity <= 0:  # the loan exceeds the holdings at this open: closed for good
        book.positions, book.cash, book.pending, book.decided_at, book.wiped = {}, 0.0, None, None, True
        return {"fills": [], "wiped_out": True}
    credit = max(0.0, (LEVERAGE.get(book.name, 1.0) - 1) * equity)  # how far cash may go below zero
    want = {a: (equity * w / (px[a] * (1 + s))) for a, w in book.pending.items() if a in px}
    want = {a: (q if a in FRACTIONAL else math.floor(q)) for a, q in want.items()}
    if reduce_only:
        want = {a: min(q, book.positions.get(a, 0.0)) for a, q in want.items()}
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
            afford = max(0.0, book.cash + credit) / (px[a] * (1 + s))
            q = min(delta, afford if a in FRACTIONAL else math.floor(afford))
            if q > 0:
                book.cash -= q * px[a] * (1 + s)
                book.costs += q * px[a] * s
                book.positions[a] = book.positions.get(a, 0.0) + q
                book.trades += 1
                fills.append({"asset": a, "qty": round(q, 8), "price": px[a]})
    book.positions = {a: q for a, q in book.positions.items() if q > 1e-12}
    assert book.cash >= -credit - 1e-6
    book.pending = None
    return {"fills": fills}


PARK, PARK_MIN, INVESTED = "SGOV", 0.05, 0.98  # T-bill ETF; park only real idle cash, not the 2% buffer


def brake_multiplier(equity: float, peak: float) -> float:
    """The adopted drawdown brakes (docs/PLAN_60.md): 2/3 exposure from 10% below the peak, 1/2 from 20%."""
    dd = 1 - equity / peak if peak > 0 else 0.0
    return 0.5 if dd >= 0.20 else (2 / 3 if dd >= 0.10 else 1.0)


def targets(name: str, close: pd.DataFrame, day: pd.Timestamp, cfg: MasterConfig, held: dict[str, float],
            brake: float = 1.0) -> tuple[dict[str, float] | None, dict[str, Any]]:
    if name == "master+brakes":
        a = allocate([], crypto_state(close, day, cfg.crypto_assets), cfg)
        t = {k: w * brake for k, w in a.weights.items()}
        idle = INVESTED - sum(t.values())
        if idle >= PARK_MIN:  # cash the brakes leave idle earns T-bill yield (user, 2026-09-29)
            t[PARK] = round(idle, 4)
        return t, {"dropped": a.dropped, "brake": round(brake, 3), "parked": round(t.get(PARK, 0.0), 4)}
    if name in LEVERAGE:  # `brake` here is the master+brakes book's multiplier, not this book's own
        a = allocate([], crypto_state(close, day, cfg.crypto_assets), cfg)
        lev = LEVERAGE[name]
        return ({k: round(w * brake * lev, 6) for k, w in a.weights.items()},
                {"dropped": a.dropped, "brake": round(brake, 3), "leverage": lev})
    if name == "master":
        a = allocate([], crypto_state(close, day, cfg.crypto_assets), cfg)
        return a.weights, {"dropped": a.dropped}
    if name == "SPY":
        return (None if held else {"SPY": 0.98}), {}
    return {"SPY": 0.78, "BTC-USD": 0.20}, {}


def step(books: dict[str, Book], opens: pd.DataFrame, closes: pd.DataFrame, now_utc: datetime, cfg: MasterConfig,
         mandate: Path = MANDATE, halted: bool = False, reducing: bool = False) -> dict[str, Any]:
    """One forward run. `opens`/`closes` are on the SPY trading calendar; bars dated on or after today's UTC date
    are dropped (possibly incomplete). `halted`: the kill switch is on (mark only). `reducing`: orders may only shrink
    positions (targets capped at current weights, at decision and again at fill)."""
    today = now_utc.date()
    opens = opens[pd.DatetimeIndex(opens.index).date < today]
    closes = closes[pd.DatetimeIndex(closes.index).date < today]
    days = pd.DatetimeIndex(closes.index)
    last = days[-1]
    rec: dict[str, Any] = {"run_at_utc": now_utc.isoformat(timespec="seconds"),
                           "data_through": last.date().isoformat(), "books": {}}
    if halted:
        rec["halted"] = True
    elif reducing:
        rec["reducing"] = True
    brakes: dict[str, float] = {}
    for name, b in books.items():
        r: dict[str, Any] = {}
        if name in LEVERAGE:
            if b.wiped:
                rec["books"][name] = {"equity": 0.0, "positions": {}, "wiped_out": True}
                continue
            through = date.fromisoformat(b.interest_through) if b.interest_through else today
            if b.cash < 0 and today > through:  # interest on the borrowed cash for the days since the last run
                charge = -b.cash * BORROW_RATE * (today - through).days / 365
                b.cash -= charge
                b.interest += charge
            b.interest_through = today.isoformat()
        if b.pending is not None and not halted and (bad := gate(b.pending, mandate, name)):
            r["rejected_at_fill"], b.pending, b.decided_at = bad, None, None  # fail closed: keep what is held
        if halted and b.pending is not None:
            r["held_by_kill_switch"] = True  # orders stay pending; nothing fills while halted
        elif b.pending is not None and b.decided_at is not None:
            fd = fill_day(days, b.decided_at)
            if fd is not None:
                r["filled_on"] = fd.date().isoformat()
                r.update(execute(b, pd.Series(opens.loc[fd]), reduce_only=reducing or b.reducing))
            else:
                r["waiting_for_open_after"] = datetime.fromisoformat(b.decided_at).date().isoformat()
        if b.wiped:
            rec["books"][name] = {"equity": 0.0, "positions": {}, "wiped_out": True}
            continue
        # each position at its last known close (a missing bar must not drop the position from equity)
        px = {a: float(closes[a].loc[:last].dropna().iloc[-1]) for a in b.positions}
        r["equity"] = round(b.cash + sum(q * px[a] for a, q in b.positions.items()), 2)
        if name in LEVERAGE:
            if r["equity"] <= 0:  # the loan is larger than the holdings: the book is closed and stays closed
                b.positions, b.cash, b.pending, b.decided_at, b.wiped = {}, 0.0, None, None, True
                rec["books"][name] = {"equity": 0.0, "positions": {}, "wiped_out": True}
                continue
            r["gross"] = round(sum(q * px[a] for a, q in b.positions.items()) / r["equity"], 3)
            r["borrowed"] = round(max(0.0, -b.cash), 2)
            r["interest_paid"] = round(b.interest, 2)
        b.peak = max(b.peak, r["equity"])
        r["positions"] = {a: round(q, 6) for a, q in b.positions.items()}
        brakes[name] = brake_multiplier(r["equity"], b.peak)
        if b.pending is None and not halted:
            t, info = targets(name, closes, last, cfg, b.positions,
                              brakes.get("master+brakes", 1.0) if name in LEVERAGE else brakes[name])
            if t is not None and (reducing or b.reducing):
                t = cap_to_current(t, b.positions, b.cash, px)
            if b.reducing:
                r["reducing"] = True  # this book only: past its own drawdown limit until the user resumes
            if t is not None and (bad := gate(t, mandate, name)):
                r["rejected"] = bad  # fail closed: nothing is left pending
            elif t is not None:
                b.pending, b.decided_at = t, now_utc.isoformat(timespec="seconds")
                r["new_targets"] = {a: round(w, 4) for a, w in t.items()}
                r.update(info)
        rec["books"][name] = r
    return rec


def books_to_json(books: dict[str, Book]) -> dict[str, Any]:
    return {k: asdict(v) for k, v in books.items()}


BOOKS = ("master", "master+brakes", "SPY", "80/20 SPY/BTC", AGGRESSIVE)  # leveraged books last: they read the brake


def books_from_json(d: dict[str, Any]) -> dict[str, Book]:
    """Saved books, plus any book added since (it starts fresh at the next run)."""
    books = {k: Book(**v) for k, v in d.items()}
    return books | {n: Book(n) for n in BOOKS if n not in books}


def new_books() -> dict[str, Book]:
    return {n: Book(n) for n in BOOKS}


def first_run_date(ledger: list[dict[str, Any]]) -> date | None:
    return date.fromisoformat(ledger[0]["run_at_utc"][:10]) if ledger else None
