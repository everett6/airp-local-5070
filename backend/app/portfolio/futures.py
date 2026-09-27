"""
Futures in the paper-trading simulator (PLAN_60_V2 Stage 2: leverage through futures, not margin). Paper money only.

Free data has no history of individual futures contracts, so each contract is priced from its underlying by cost of
carry: F = S * scale * exp((rf + carry_extra) * tau), tau = years to expiry. carry_extra is minus the dividend yield
for an index, and the basis over rf for bitcoin (CME bitcoin futures have mostly traded above spot). This is the
textbook price, not a market quote: real futures also drift from it, so a result that depends on a few bps of basis
is not a result.

Mechanics, as an exchange does them:
  - Orders decided on day d fill at d+1's open (asserted), in whole contracts, at cost_bps per side of notional.
  - Daily settlement: every open and close, the change in the contract price times multiplier times contracts moves
    into or out of cash (variation margin). The account holds no position value; equity is cash.
  - Cash (the collateral) earns rf. With carry priced in, a fully collateralized 1x position therefore earns about
    the underlying's total return, and leverage pays rf + carry_extra on the extra notional through the price.
  - Margin: after each close, if equity is below the maintenance margin of the open contracts, the position is cut at
    that close to what the equity supports at the initial margin (a margin call). Equity at or below 0 ends the run.
  - Rolls: when the held contract is within `roll_days` calendar days of expiry, it is closed and the next one opened
    at the same notional, paying cost_bps on both legs. MES expires quarterly (third Friday of Mar/Jun/Sep/Dec), CME
    micro bitcoin monthly (last Friday).
"""
from __future__ import annotations

import calendar
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class FutureSpec:
    symbol: str
    underlying: str        # column in the opens/closes frames
    scale: float           # underlying price x scale = the index the contract is on (SPY x 10 ~ S&P 500)
    multiplier: float      # dollars per index point per contract
    init_margin: float     # fraction of notional
    maint_margin: float
    cycle: str             # "quarterly" or "monthly"
    carry_extra: float     # annual carry on top of rf in the futures price
    cost_bps: float = 1.5  # per side: commission + slippage
    roll_days: int = 7


MES = FutureSpec("MES", "SPY", 10.0, 5.0, 0.06, 0.055, "quarterly", carry_extra=-0.013, cost_bps=1.5)
MBT = FutureSpec("MBT", "BTC-USD", 1.0, 0.1, 0.40, 0.36, "monthly", carry_extra=0.05, cost_bps=5.0)


def _third_friday(y: int, m: int) -> date:
    d = date(y, m, 1)
    return d + timedelta(days=(4 - d.weekday()) % 7 + 14)


def _last_friday(y: int, m: int) -> date:
    d = date(y, m, calendar.monthrange(y, m)[1])
    return d - timedelta(days=(d.weekday() - 4) % 7)


def next_expiry(spec: FutureSpec, d: date) -> date:
    """The first expiry of the contract cycle that is more than roll_days after d."""
    y, m = d.year, d.month
    for _ in range(24):
        if spec.cycle == "monthly" or m in (3, 6, 9, 12):
            e = _third_friday(y, m) if spec.cycle == "quarterly" else _last_friday(y, m)
            if (e - d).days > spec.roll_days:
                return e
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    raise ValueError(f"no expiry found after {d}")


def fut_price(spec: FutureSpec, spot: float, rf: float, d: date, expiry: date) -> float:
    tau = max(0, (expiry - d).days) / 365.0
    return spot * spec.scale * math.exp((rf + spec.carry_extra) * tau)


@dataclass
class _Pos:
    n: int = 0
    expiry: date | None = None
    last: float = 0.0  # price of the last settlement


@dataclass
class FuturesSim:
    days: list[date] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    costs: float = 0.0
    trades: int = 0
    rolls: int = 0
    margin_calls: list[dict[str, Any]] = field(default_factory=list)
    blown_up: bool = False
    log: list[dict[str, Any]] = field(default_factory=list)


def simulate_futures(targets: dict[date, dict[str, float]], opens: pd.DataFrame, closes: pd.DataFrame,
                     rf: pd.Series, specs: list[FutureSpec], start: date, end: date,
                     cash0: float = 1_000_000.0) -> FuturesSim:
    """targets[d][symbol] = wanted notional as a multiple of equity (1.5 = 1.5x long), decided at d's close and
    traded at the next day's open. rf: annual rate by day (e.g. FRED DTB3 / 100), forward-filled."""
    by_sym = {s.symbol: s for s in specs}
    days = [d for d in opens.index if start <= d.date() <= end]
    rf_d = rf.reindex(pd.DatetimeIndex(days), method="ffill").fillna(0.0)
    order_on: dict[date, date] = {}
    for d in sorted(targets):
        nxt = next((x for x in days if x.date() > d), None)
        if nxt is not None:
            order_on[nxt.date()] = d
    cash = cash0
    pos = {s.symbol: _Pos() for s in specs}
    sim = FuturesSim()
    prev: date | None = None

    def px(s: FutureSpec, frame: pd.DataFrame, d: pd.Timestamp, r: float, expiry: date | None) -> float | None:
        v: Any = frame.at[d, s.underlying] if s.underlying in frame.columns else float("nan")
        return None if expiry is None or pd.isna(v) else fut_price(s, float(v), r, d.date(), expiry)

    def settle(p: _Pos, s: FutureSpec, price: float) -> None:
        nonlocal cash
        cash += p.n * (price - p.last) * s.multiplier
        p.last = price

    def trade(p: _Pos, s: FutureSpec, price: float, n_new: int) -> None:
        nonlocal cash
        dn = n_new - p.n
        if dn:
            c = abs(dn) * price * s.multiplier * s.cost_bps / 1e4
            cash -= c
            sim.costs += c
            sim.trades += 1
            p.n = n_new

    for d in days:
        dd, r = d.date(), float(rf_d[d])
        if prev is not None:
            cash *= 1 + r * (dd - prev).days / 365.0  # collateral earns rf
        # open: settle to the open, roll if near expiry, then fill today's orders
        for sym, p in pos.items():
            s = by_sym[sym]
            if p.expiry is None:
                p.expiry = next_expiry(s, dd)
            if p.n and (p.expiry - dd).days <= s.roll_days:
                old = px(s, opens, d, r, p.expiry)
                if old is not None:
                    settle(p, s, old)
                    n0 = p.n
                    trade(p, s, old, 0)
                    p.expiry = next_expiry(s, dd)
                    new = px(s, opens, d, r, p.expiry)
                    assert new is not None
                    p.last = new
                    trade(p, s, new, round(n0 * old / new))
                    sim.rolls += 1
            elif not p.n:
                p.expiry = next_expiry(s, dd)
            po = px(s, opens, d, r, p.expiry)
            if po is not None:
                settle(p, s, po)
        if dd in order_on:
            sig = order_on[dd]
            assert sig < dd, "lookahead: traded on or before the decision day"
            eq = cash
            for sym, w in targets[sig].items():
                s, p = by_sym[sym], pos[sym]
                po = px(s, opens, d, r, p.expiry)
                if po is not None:
                    trade(p, s, po, round(w * eq / (po * s.multiplier)))
            sim.log.append({"day": dd.isoformat(), "contracts": {k: v.n for k, v in pos.items()}})
        # close: settle, then check margin
        req = 0.0
        for sym, p in pos.items():
            s = by_sym[sym]
            pc = px(s, closes, d, r, p.expiry)
            if pc is not None:
                settle(p, s, pc)
            req += abs(p.n) * p.last * s.multiplier * s.maint_margin
        if cash <= 0:
            sim.blown_up = True
            sim.days.append(dd)
            sim.equity.append(cash)
            break
        if cash < req:
            cut = {}
            for sym, p in pos.items():
                s = by_sym[sym]
                if p.n:
                    share = abs(p.n) * p.last * s.multiplier * s.maint_margin / req
                    keep = int(cash * share // (p.last * s.multiplier * s.init_margin))
                    cut[sym] = (p.n, int(math.copysign(keep, p.n)))
                    trade(p, s, p.last, cut[sym][1])
            sim.margin_calls.append({"day": dd.isoformat(), "equity": round(cash, 2), "maint": round(req, 2),
                                     "contracts": cut})
        sim.days.append(dd)
        sim.equity.append(cash)
        prev = dd
    return sim
