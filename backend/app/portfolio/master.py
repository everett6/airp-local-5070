"""
Master agent: turns the sub-agents' candidates into one portfolio (paper money only).

Following airp's rule "LLMs reason and write, code calculates", every number here is code:
  1. Calibration. A sub-agent's raw score (e.g. Bonsai's BUY log-odds) becomes P(beats its benchmark over the
     holding period) through a logistic fit on PAST candidates whose outcome was already known on the decision
     day (`Calibrator`). No outcome from the future can enter: a row is usable only from its `known_at` date.
  2. Sizing. Fractional Kelly on the calibrated edge: weight = kelly_fraction * (2p - 1) * edge_scale / vol^2,
     then caps per name, per sector, per sleeve (stocks, crypto), and a cash buffer.
  3. Core. Whatever the satellites don't use goes to the core index (SPY) instead of idle cash.
  4. Risk. When the portfolio is more than `dd_throttle` below its peak, the satellites are halved (not a permanent
     stop: over years a permanent stop simply ends every run in the first bear market).
An optional LLM review (scripts/master_portfolio.py --review) may only REMOVE positions it flags as too risky, with
a written reason; it can never add or enlarge one.

The crypto sub-agent is a documented rule, not a model: time-series momentum (Liu & Tsyvinski 2021, "Risks and
Returns of Cryptocurrency"): hold BTC / ETH while their 4-week return is positive and price is above the 100-day
average, sized by inverse volatility within the crypto cap. Only BTC and ETH: choosing coins that are big today
(SOL, ...) would pick winners with hindsight.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from app.learning.linear import fit_logistic_newton_np


@dataclass(frozen=True)
class MasterConfig:
    crypto_cap: float = 0.20          # at most this share in crypto
    satellite_cap: float = 0.60       # at most this share in individual stock picks
    name_cap: float = 0.05            # per stock
    sector_cap: float = 0.25          # per sector, stock picks only
    p_min: float = 0.55               # calibrated P(beats benchmark) needed to buy
    kelly_fraction: float = 0.25      # quarter Kelly
    edge_scale: float = 0.05          # typical excess return of a winner over the holding period
    cash_buffer: float = 0.02
    core: str = "SPY"                 # unused satellite money goes here
    dd_throttle: float = 0.15
    crypto_assets: tuple[str, ...] = ("BTC-USD", "ETH-USD")


class Calibrator:
    """P(outcome = 1 | score), refitted on rows already resolved at the query date."""

    def __init__(self, min_rows: int = 200) -> None:
        self.rows: list[tuple[date, float, int]] = []  # (known_at, score, beat)
        self.min_rows = min_rows
        self._fit_on: date | None = None
        self._w: tuple[float, float] | None = None

    def add(self, known_at: date, score: float, beat: bool) -> None:
        self.rows.append((known_at, float(score), int(beat)))

    def _fit(self, asof: date) -> None:
        past = [(s, y) for k, s, y in self.rows if k <= asof]
        if len(past) < self.min_rows or len({y for _, y in past}) < 2:
            self._w = None
        else:
            xs = np.array([s for s, _ in past])
            mu, sd = float(xs.mean()), float(xs.std() or 1.0)
            w = fit_logistic_newton_np([[(s - mu) / sd] for s, _ in past], [y for _, y in past], l2=0.01)
            self._w = (float(w[0]) - float(w[1]) * mu / sd, float(w[1]) / sd)
        self._fit_on = asof

    def prob(self, score: float, asof: date) -> float | None:
        """None until enough outcomes are known: then the candidate is simply not bought."""
        if self._fit_on is None or (asof - self._fit_on).days >= 28:  # refit monthly
            self._fit(asof)
        if self._w is None:
            return None
        z = self._w[0] + self._w[1] * score
        return 1.0 / (1.0 + math.exp(-max(-50.0, min(50.0, z))))


def crypto_state(close: pd.DataFrame, day: pd.Timestamp, assets: tuple[str, ...]) -> dict[str, dict[str, float]]:
    """Trend rule per asset from closes on or before `day`: on/off and 60-day annualized volatility."""
    out = {}
    for a in assets:
        if a not in close.columns:
            continue
        c = close[a].loc[:day].dropna()
        if len(c) < 120:
            continue
        on = c.iloc[-1] / c.iloc[-21] - 1 > 0 and c.iloc[-1] > c.tail(100).mean()
        vol = float(c.pct_change().tail(60).std() * math.sqrt(252))
        out[a] = {"on": float(on), "vol": max(vol, 0.05)}
    return out


@dataclass
class Candidate:
    asset: str
    p: float        # calibrated probability of beating its benchmark over the holding period
    vol: float      # annualized volatility
    sector: str = ""
    source: str = ""
    until: date | None = None   # event positions are held until this day


@dataclass
class Allocation:
    weights: dict[str, float]
    reasons: dict[str, str] = field(default_factory=dict)
    dropped: dict[str, str] = field(default_factory=dict)


def allocate(stock_cands: list[Candidate], crypto: dict[str, dict[str, float]], cfg: MasterConfig,
             drawdown: float = 0.0) -> Allocation:
    alloc = Allocation(weights={})
    scale = 0.5 if drawdown > cfg.dd_throttle else 1.0
    # stock satellites: fractional Kelly on the calibrated edge, then caps
    raw: dict[str, float] = {}
    for c in stock_cands:
        if c.p < cfg.p_min:
            alloc.dropped[c.asset] = f"p={c.p:.2f} below {cfg.p_min}"
            continue
        k = cfg.kelly_fraction * (2 * c.p - 1) * cfg.edge_scale / max(c.vol, 0.05) ** 2
        raw[c.asset] = min(cfg.name_cap, max(0.0, k))
        alloc.reasons[c.asset] = f"{c.source}: p={c.p:.2f}, vol={c.vol:.0%}, kelly={k:.3f}"
    by_sector: dict[str, float] = {}
    for c in stock_cands:
        if c.asset in raw:
            by_sector[c.sector] = by_sector.get(c.sector, 0.0) + raw[c.asset]
    for c in stock_cands:
        if c.asset in raw and by_sector[c.sector] > cfg.sector_cap:
            raw[c.asset] *= cfg.sector_cap / by_sector[c.sector]
    total = sum(raw.values())
    if total > cfg.satellite_cap:
        raw = {a: w * cfg.satellite_cap / total for a, w in raw.items()}
    stocks = {a: w * scale for a, w in raw.items()}
    # crypto satellite: trend on/off, inverse-volatility weights within the cap
    on = {a: s for a, s in crypto.items() if s["on"] > 0}
    inv = {a: 1.0 / s["vol"] for a, s in on.items()}
    cryp = {a: cfg.crypto_cap * scale * v / sum(inv.values()) for a, v in inv.items()} if inv else {}
    for a in crypto:
        if a not in on:
            alloc.dropped[a] = "trend off (4-week return <= 0 or price below 100-day average)"
    weights = {**stocks, **cryp}
    core = max(0.0, 1.0 - cfg.cash_buffer - sum(weights.values()))
    if core > 0:
        weights[cfg.core] = weights.get(cfg.core, 0.0) + core
    alloc.weights = {a: w for a, w in weights.items() if w > 1e-6}
    return alloc


@dataclass
class WeightSim:
    days: list[date]
    equity: list[float]
    trades: int = 0
    costs: float = 0.0
    weights_log: list[dict[str, Any]] = field(default_factory=list)


def simulate_weights(targets: dict[date, dict[str, float]], opens: pd.DataFrame, closes: pd.DataFrame,
                     start: date, end: date, cash0: float = 100_000.0, cost_bps: float = 5.0,
                     fractional: tuple[str, ...] = ("BTC-USD", "ETH-USD")) -> WeightSim:
    """Targets decided on day d are traded at the NEXT day's open (asserted). Stocks and ETFs in whole shares,
    crypto fractional. Cash can't go negative; positions are marked at each close (last known if missing)."""
    days = [d for d in opens.index if start <= d.date() <= end]
    order_on = {}
    for d in sorted(targets):
        nxt = next((x for x in days if x.date() > d), None)
        if nxt is not None:
            order_on[nxt.date()] = d
    cash = cash0
    pos: dict[str, float] = {}
    last: dict[str, float] = {}
    sim = WeightSim(days=[], equity=[])
    s = cost_bps / 1e4
    for d in days:
        dd = d.date()
        if dd in order_on:
            sig = order_on[dd]
            assert sig < dd, "lookahead: traded on or before the decision day"
            tgt = targets[sig]
            row_o = opens.loc[d]
            px_open = {a: float(row_o[a]) for a in set(tgt) | set(pos) if a in opens.columns and pd.notna(row_o[a])}
            eq_open = cash + sum(q * px_open.get(a, last.get(a, 0.0)) for a, q in pos.items())
            want: dict[str, float] = {}
            for a, w in tgt.items():
                if a in px_open:
                    q = eq_open * w / (px_open[a] * (1 + s))
                    want[a] = q if a in fractional else math.floor(q)
            for a in sorted(set(pos) | set(want)):  # sells first
                delta = want.get(a, 0.0) - pos.get(a, 0.0)
                if delta < 0 and a in px_open:
                    cash += -delta * px_open[a] * (1 - s)
                    sim.costs += -delta * px_open[a] * s
                    pos[a] = pos.get(a, 0.0) + delta
                    sim.trades += 1
            for a in sorted(want):
                delta = want[a] - pos.get(a, 0.0)
                if delta > 0 and a in px_open:
                    afford = cash / (px_open[a] * (1 + s))
                    q = min(delta, afford if a in fractional else math.floor(afford))
                    if q > 0:
                        cash -= q * px_open[a] * (1 + s)
                        sim.costs += q * px_open[a] * s
                        pos[a] = pos.get(a, 0.0) + q
                        sim.trades += 1
            pos = {a: q for a, q in pos.items() if q > 1e-12}
            sim.weights_log.append({"day": dd.isoformat(), "targets": {a: round(w, 4) for a, w in tgt.items()}})
        row_c = closes.loc[d]
        for a in pos:
            if a in closes.columns and pd.notna(row_c[a]):
                last[a] = float(row_c[a])
        assert cash >= -1e-6, f"negative cash on {dd}"
        sim.days.append(dd)
        sim.equity.append(cash + sum(q * last.get(a, 0.0) for a, q in pos.items()))
    return sim
