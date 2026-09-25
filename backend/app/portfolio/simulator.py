"""
Paper-trading simulator: turn a model's stock predictions into trades with fake money and see what happens.

Each signal date the model gives every stock a score `p` (its probability the stock goes up). The simulator:
  1. buys the `top_k` highest-scored stocks, equal weight, capped at `max_weight` each;
  2. trades at the NEXT trading day's OPEN, never at the close the model already saw (no lookahead);
  3. charges slippage on every trade and holds only whole shares;
  4. marks the portfolio to market at every close and halts (sells everything, stops trading) if it falls
     `drawdown_halt` below its peak;
  5. checks every day that cash is never negative and the books balance.

Anti-hallucination guard: a model output is only acted on if it is a finite probability in [0, 1], for a stock
the simulator has prices for, dated on a trading day, and strictly before the day it would execute. Anything else
is rejected and counted in the result, never traded.

Prices come from free Yahoo daily bars (`scripts/fetch_ohlcv.py`). No real orders are ever placed.
"""
from __future__ import annotations

import math
import random
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import numpy as np

from app.quant.risk import drawdown_analysis, sharpe_ratio
from app.sandbox.ohlcv import Bar

TRADING_DAYS = 252


@dataclass(frozen=True)
class SimConfig:
    initial_cash: float = 100_000.0
    top_k: int = 10                  # how many stocks to hold
    max_weight: float = 0.10         # cap per stock, as a share of equity
    cash_buffer: float = 0.02        # always keep this share of equity in cash
    slippage_bps: float = 5.0        # cost per side: buys fill this much above the open, sells below
    min_prob: float | None = None    # only buy stocks scored above this (None = take the top_k regardless)
    drawdown_halt: float = 0.20      # sell everything and stop trading after this fall from the peak
    min_trade_value: float = 25.0    # skip rebalancing trades smaller than this (avoids churn)
    # Many models give lots of stocks the SAME score (a rule that outputs only 0.49/0.51, an LLM that says 0.52 for
    # 40% of stocks). Breaking those ties alphabetically would quietly turn "the model's top 10" into "tickers
    # starting with A". Ties are broken by a random draw seeded with (tie_seed, date); run several seeds and look at
    # the spread (scripts/simulate.py does).
    tie_seed: int = 0


@dataclass
class Trade:
    day: date
    ticker: str
    shares: int                      # positive = buy, negative = sell
    price: float                     # fill price, after slippage
    reason: str                      # "rebalance" | "halt"


@dataclass
class SimResult:
    name: str
    config: SimConfig
    days: list[date]
    equity: list[float]
    trades: list[Trade] = field(default_factory=list)
    rejected: dict[str, int] = field(default_factory=dict)
    halted_on: date | None = None
    costs_paid: float = 0.0
    rebalances: int = 0
    picks_total: int = 0
    picks_by_tiebreak: int = 0       # picks that depended on the random tie-break, not on the model's score

    def metrics(self) -> dict[str, Any]:
        eq = np.asarray(self.equity, dtype=float)
        daily = eq[1:] / eq[:-1] - 1 if len(eq) > 1 else np.array([0.0])
        years = max(len(eq) - 1, 1) / TRADING_DAYS
        traded = sum(abs(t.shares) * t.price for t in self.trades)
        return {
            "final_equity": round(float(eq[-1]), 2),
            "total_return_pct": round((eq[-1] / eq[0] - 1) * 100, 2),
            "cagr_pct": round(((eq[-1] / eq[0]) ** (1 / years) - 1) * 100, 2),
            "ann_vol_pct": round(float(daily.std(ddof=1) * math.sqrt(TRADING_DAYS) * 100), 2) if len(daily) > 1 else 0.0,
            "sharpe": round(sharpe_ratio(daily.tolist(), periods_per_year=TRADING_DAYS), 2) if len(daily) > 1 else 0.0,
            "max_drawdown_pct": round(drawdown_analysis(eq.tolist()).max_drawdown * 100, 2),
            "costs_paid": round(self.costs_paid, 2),
            "annual_turnover_x": round(traded / float(eq.mean()) / years, 2),
            "n_trades": len(self.trades),
            "rebalances": self.rebalances,
            "tie_decided_pct": round(100 * self.picks_by_tiebreak / self.picks_total, 1) if self.picks_total else 0.0,
            "rejected_signals": dict(self.rejected),
            "halted_on": self.halted_on.isoformat() if self.halted_on else None,
            "days": len(eq),
        }


class PriceBook:
    """Daily opens and closes per ticker, from OHLCV bars, with the trading calendar they define."""

    def __init__(self, bars: dict[str, list[Bar]], calendar_ticker: str | None = None) -> None:
        self.open: dict[date, dict[str, float]] = {}
        self.close: dict[date, dict[str, float]] = {}
        for t, rows in bars.items():
            for d, o, _h, _l, c, _v in rows:
                if o > 0 and c > 0 and math.isfinite(o) and math.isfinite(c):
                    self.open.setdefault(d, {})[t] = o
                    self.close.setdefault(d, {})[t] = c
        if calendar_ticker and calendar_ticker in bars:
            self.days = sorted(b[0] for b in bars[calendar_ticker])
        else:
            self.days = sorted(self.close)
        self.tickers = set(bars)

    def next_day(self, d: date) -> date | None:
        for x in self.days:  # calendars here are a few hundred days; clarity over bisect
            if x > d:
                return x
        return None


def _validate(signals: Iterable[dict[str, Any]], book: PriceBook, universe: set[str]
              ) -> tuple[dict[date, dict[str, float]], dict[str, int]]:
    """Group usable signals by date; count everything rejected by reason (never silently dropped)."""
    days = set(book.days)
    by_day: dict[date, dict[str, float]] = {}
    rejected: dict[str, int] = {}

    def reject(why: str) -> None:
        rejected[why] = rejected.get(why, 0) + 1

    for s in signals:
        try:
            d = s["cutoff"] if isinstance(s["cutoff"], date) else date.fromisoformat(str(s["cutoff"])[:10])
            p = float(s["p"])
            t = str(s["ticker"])
        except (KeyError, TypeError, ValueError):
            reject("malformed")
            continue
        if not math.isfinite(p) or not 0.0 <= p <= 1.0:
            reject("probability_out_of_range")
        elif t not in universe:
            reject("unknown_ticker")
        elif d not in days:
            reject("not_a_trading_day")
        elif t not in book.close.get(d, {}):
            reject("no_price_on_signal_day")
        else:
            by_day.setdefault(d, {})[t] = p
    return by_day, rejected


def _targets(scores: dict[str, float], cfg: SimConfig, day: date) -> tuple[dict[str, float], int]:
    """Target weights: top_k by score, ties broken by a seeded random draw, equal weight, capped.
    Also returns how many picks were decided by the tie-break (tied with the first stock left out)."""
    order = sorted(scores)
    random.Random(f"{cfg.tie_seed}|{day.isoformat()}").shuffle(order)
    tiebreak = {t: i for i, t in enumerate(order)}
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], tiebreak[kv[0]]))
    if cfg.min_prob is not None:
        ranked = [kv for kv in ranked if kv[1] > cfg.min_prob]
    picks = ranked[: cfg.top_k]
    if not picks:
        return {}, 0
    tied = 0
    if len(ranked) > len(picks):
        first_out = ranked[len(picks)][1]
        tied = sum(1 for _, p in picks if p == first_out)
    w = min(cfg.max_weight, (1.0 - cfg.cash_buffer) / len(picks))
    return dict.fromkeys((t for t, _ in picks), w), tied


def simulate(signals: Iterable[dict[str, Any]], bars: dict[str, list[Bar]], cfg: SimConfig | None = None,
             name: str = "strategy", universe: set[str] | None = None, calendar_ticker: str | None = "SPY",
             end: date | None = None) -> SimResult:
    cfg = cfg or SimConfig()
    book = PriceBook(bars, calendar_ticker)
    universe = (universe or book.tickers) - ({calendar_ticker} if calendar_ticker else set())
    by_day, rejected = _validate(signals, book, universe)
    if not by_day:
        raise ValueError(f"{name}: no usable signals (rejected: {rejected})")
    first_signal = min(by_day)
    start = book.next_day(first_signal)
    if start is None:
        raise ValueError(f"{name}: no trading day after the first signal {first_signal}")
    # orders decided on signal day d execute at the open of the next trading day
    orders: dict[date, date] = {}
    for d in by_day:
        nd = book.next_day(d)
        if nd is not None:
            orders[nd] = d
    days = [d for d in book.days if d >= start and (end is None or d <= end)]

    s = cfg.slippage_bps / 1e4
    cash = cfg.initial_cash
    shares: dict[str, int] = {}
    last_close: dict[str, float] = {}
    res = SimResult(name=name, config=cfg, days=[], equity=[], rejected=rejected)
    peak = cfg.initial_cash
    halt_pending = False

    def px(table: dict[date, dict[str, float]], d: date, t: str) -> float | None:
        v = table.get(d, {}).get(t)
        return v if v is not None else last_close.get(t)

    def fill(d: date, t: str, qty: int, reason: str) -> None:
        nonlocal cash
        o = px(book.open, d, t)
        if o is None or qty == 0:
            return
        price = o * (1 + s) if qty > 0 else o * (1 - s)
        cash -= qty * price
        res.costs_paid += abs(qty) * o * s
        shares[t] = shares.get(t, 0) + qty
        if shares[t] == 0:
            del shares[t]
        res.trades.append(Trade(d, t, qty, price, reason))

    for d in days:
        if halt_pending:
            for t, q in sorted(shares.items()):
                fill(d, t, -q, "halt")
            halt_pending = False
        elif res.halted_on is None and d in orders:
            signal_day = orders[d]
            assert signal_day < d, "lookahead: executing on or before the signal day"
            targets, tied = _targets(by_day[signal_day], cfg, signal_day)
            res.picks_total += len(targets)
            res.picks_by_tiebreak += tied
            equity_open = cash + sum(q * (px(book.open, d, t) or 0.0) for t, q in shares.items())
            want: dict[str, int] = {}
            for t, w in targets.items():
                o = px(book.open, d, t)
                if o:
                    want[t] = int(equity_open * w // (o * (1 + s)))
            # sells first, then buys (scaled down if cash runs short)
            for t in sorted(set(shares) | set(want)):
                delta = want.get(t, 0) - shares.get(t, 0)
                o = px(book.open, d, t) or 0.0
                if delta < 0 and (want.get(t, 0) == 0 or abs(delta) * o >= cfg.min_trade_value):
                    fill(d, t, delta, "rebalance")
            for t in sorted(want):
                delta = want[t] - shares.get(t, 0)
                o = px(book.open, d, t) or 0.0
                if delta > 0 and delta * o >= cfg.min_trade_value:
                    affordable = int(max(cash, 0.0) // (o * (1 + s)))
                    fill(d, t, min(delta, affordable), "rebalance")
            res.rebalances += 1
        for t in shares:
            c = book.close.get(d, {}).get(t)
            if c is not None:
                last_close[t] = c
        eq = cash + sum(q * last_close[t] for t, q in shares.items())
        # reconciliation: the books must balance every day
        assert cash >= -1e-6, f"{name}: negative cash {cash:.2f} on {d}"
        assert all(q > 0 for q in shares.values()), f"{name}: non-positive holding on {d}"
        res.days.append(d)
        res.equity.append(eq)
        peak = max(peak, eq)
        if res.halted_on is None and shares and eq < peak * (1 - cfg.drawdown_halt):
            res.halted_on = d
            halt_pending = True
    return res


def buy_and_hold(bars: dict[str, list[Bar]], ticker: str, start: date, end: date | None = None,
                 initial_cash: float = 100_000.0, slippage_bps: float = 5.0) -> SimResult:
    """Benchmark: buy `ticker` (e.g. SPY) at the open of `start` with whole shares and hold."""
    book = PriceBook({ticker: bars[ticker]}, ticker)
    days = [d for d in book.days if d >= start and (end is None or d <= end)]
    o = book.open[days[0]][ticker] * (1 + slippage_bps / 1e4)
    q = int(initial_cash // o)
    cash = initial_cash - q * o
    res = SimResult(name=f"buy_and_hold_{ticker}", config=SimConfig(initial_cash=initial_cash, top_k=1, max_weight=1.0,
                    slippage_bps=slippage_bps), days=days, equity=[cash + q * book.close[d][ticker] for d in days])
    res.trades.append(Trade(days[0], ticker, q, o, "buy_and_hold"))
    res.costs_paid = q * book.open[days[0]][ticker] * slippage_bps / 1e4
    return res


def excess_vs(a: SimResult, b: SimResult, n_boot: int = 2000, seed: int = 0) -> dict[str, float]:
    """Annualized excess daily return of `a` over `b` on their common days, with a bootstrap 95% CI over 5-day
    blocks (daily returns of two stock portfolios are correlated day to day)."""
    from app.sandbox.scoring import boot_indices

    ma, mb = dict(zip(a.days, a.equity, strict=True)), dict(zip(b.days, b.equity, strict=True))
    common = sorted(set(ma) & set(mb))
    ea = np.array([ma[d] for d in common])
    eb = np.array([mb[d] for d in common])
    diff = (ea[1:] / ea[:-1]) - (eb[1:] / eb[:-1])
    rng = np.random.default_rng(seed)
    idx = boot_indices(rng, len(diff), n_boot, block=5)
    boots = diff[idx].mean(axis=1) * TRADING_DAYS * 100
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"excess_ann_pct": round(float(diff.mean() * TRADING_DAYS * 100), 2),
            "ci_lo": round(float(lo), 2), "ci_hi": round(float(hi), 2), "days": len(diff)}


def beta_alpha(a: SimResult, market: SimResult, n_boot: int = 2000, seed: int = 0) -> dict[str, float]:
    """Market beta of `a`'s daily returns and its annualized alpha (the return NOT explained by riding the market),
    with a 5-day block-bootstrap 95% CI for alpha. A strategy that just holds riskier stocks in a rising market has
    beta > 1 and alpha near 0: its extra return is leverage on the market, not skill."""
    from app.sandbox.scoring import boot_indices

    ma, mm = dict(zip(a.days, a.equity, strict=True)), dict(zip(market.days, market.equity, strict=True))
    common = sorted(set(ma) & set(mm))
    ea, em = np.array([ma[d] for d in common]), np.array([mm[d] for d in common])
    ra, rm = ea[1:] / ea[:-1] - 1, em[1:] / em[:-1] - 1

    def fit(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
        vx = float(np.var(x))
        b = float(np.cov(x, y, bias=True)[0, 1] / vx) if vx > 0 else 0.0
        return b, float(y.mean() - b * x.mean())

    beta, alpha = fit(rm, ra)
    rng = np.random.default_rng(seed)
    idx = boot_indices(rng, len(ra), n_boot, block=5)
    boots = np.array([fit(rm[i], ra[i])[1] for i in idx]) * TRADING_DAYS * 100
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"beta": round(beta, 2), "alpha_ann_pct": round(alpha * TRADING_DAYS * 100, 2),
            "alpha_ci_lo": round(float(lo), 2), "alpha_ci_hi": round(float(hi), 2)}


def config_dict(cfg: SimConfig) -> dict[str, Any]:
    return asdict(cfg)
