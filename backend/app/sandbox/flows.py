"""Flow-pressure rules R1, M1 and A1 (docs/PLAN_60_V2.md "Three flow-pressure tests", specs fixed 2026-10-01).
Pure functions of dates and returns; every signal for day t+1 uses data through the close of day t only.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd

TARGET = 0.60
DELTAS = tuple(round(0.001 * k, 3) for k in range(26))  # 0%, 0.1%, ..., 2.5%
SCALE = 0.015


def threshold_signal(stock: pd.Series, bond: pd.Series, deltas: Iterable[float] = DELTAS) -> pd.Series:
    """R1 (Harvey, Mazzoleni and Melone 2025, eq. B.1 and 2): the stock-weight deviation from 60% of a 60/40
    portfolio that drifts with daily returns and is set back to 60/40 at any close where the deviation's size is at
    least delta; averaged over the bands. Value at day t = deviation at the close of t (before that reset)."""
    rs, rb = stock.to_numpy(float), bond.to_numpy(float)
    ds = np.array(list(deltas))
    w = np.full(len(ds), TARGET)
    out = np.empty(len(rs))
    for i in range(len(rs)):
        up = w * (1 + rs[i])
        w = up / (up + (1 - w) * (1 + rb[i]))
        dev = w - TARGET
        out[i] = dev.mean()
        w = np.where(np.abs(dev) >= ds, TARGET, w)
    return pd.Series(out, index=stock.index)


def rebalance_stream(stock: pd.Series, bond: pd.Series, signal: pd.Series, cost: float = 1e-4, lag: int = 1
                     ) -> pd.DataFrame:
    """r_t = w_{t-lag} * (stock - bond return on day t), w = -signal / 1.5%; cost per unit of weight change on each
    of the two legs, charged on the day the new weight starts to earn."""
    w = (-signal / SCALE).shift(lag).fillna(0.0)
    turn = w.diff().abs().fillna(w.abs())
    return pd.DataFrame({"ret": w * (stock - bond) - 2 * cost * turn, "w": w})


def month_end_days(index: pd.DatetimeIndex, n: int = 3) -> pd.Series:
    """True on the last n trading days of each month of `index`. The index's last month may be cut off, so it is
    marked only when a later month exists."""
    idx = pd.DatetimeIndex(index)
    month = pd.Series(idx.to_period("M"), index=idx)
    last = month.groupby(month).cumcount(ascending=False)
    return pd.Series(((last < n) & (month < month.iloc[-1])).to_numpy(), index=idx)


def auction_windows(index: pd.DatetimeIndex, auctions: Iterable[str | pd.Timestamp], n: int = 5
                    ) -> tuple[pd.Series, pd.Series]:
    """A1: (post, pre) day masks. Auctions on consecutive trading days form one cluster, dated by its last auction
    day A. Pre = the n trading days ending on A; post = the n trading days after A; a day in both is pre. An auction
    on a non-trading day is moved to the next trading day."""
    idx = pd.DatetimeIndex(index)
    pos = sorted({int(idx.searchsorted(pd.Timestamp(a))) for a in auctions} - {len(idx)})
    last = [p for i, p in enumerate(pos) if i + 1 == len(pos) or pos[i + 1] != p + 1]
    post, pre = np.zeros(len(idx), bool), np.zeros(len(idx), bool)
    for a in last:
        pre[max(0, a - n + 1): a + 1] = True
        post[a + 1: a + 1 + n] = True
    return pd.Series(post & ~pre, index=idx), pd.Series(pre, index=idx)


def overlay(excess: pd.Series, on: pd.Series, cost: float = 1e-4) -> pd.Series:
    """The excess return on the marked days, 0 otherwise, less `cost` on each entry day and each exit day."""
    on = on.reindex(excess.index).fillna(False).astype(bool)
    entry = on & ~on.shift(1, fill_value=False)
    exit_ = on & ~on.shift(-1, fill_value=False)
    return excess.where(on, 0.0) - cost * (entry.astype(float) + exit_.astype(float))


def month_end_records(close: pd.Series, rf_annual: pd.Series, first_month: str, cost: float = 1e-4,
                      n: int = 3) -> list[dict[str, Any]]:
    """M1's rule month by month, for the forward shadow. `close`: adjusted daily closes; `rf_annual`: the T-bill
    rate as a fraction a year. One record per finished month from `first_month` ("2026-10") on: the last `n`
    trading days, each day's excess return, the month's net overlay return (their sum less `cost` on the entry and
    on the exit day) and the sum and count of the excess returns on the month's other days. A month is finished
    only when `close` holds a later month."""
    close = close.dropna().sort_index()
    idx = pd.DatetimeIndex(close.index)
    excess = close.pct_change() - rf_annual.reindex(idx, method="ffill").fillna(0.0) / 252
    on = month_end_days(idx, n)
    month = pd.Series(idx.to_period("M").astype(str), index=idx)
    out: list[dict[str, Any]] = []
    for m in sorted(set(month[on.to_numpy()])):
        mine = (month == m).to_numpy()
        days, other = excess[mine & on.to_numpy()], excess[mine & ~on.to_numpy()].dropna()
        if m < first_month or len(days) != n or days.isna().any():
            continue
        out.append({"month": m, "days": [d.date().isoformat() for d in days.index],
                    "excess": [round(float(x), 6) for x in days],
                    "net": round(float(days.sum()) - 2 * cost, 6),
                    "other_sum": round(float(other.sum()), 6), "other_n": len(other)})
    return out


def month_end_verdict(recs: list[dict[str, Any]], draws: int = 5000, seed: int = 0, n: int = 3
                      ) -> dict[str, float]:
    """The shadow's two numbers over its months: the net overlay's annualized Sharpe over all days (0 on the other
    days) and the mean excess on month-end days minus the mean on other days, with its 80% one-sided lower bound
    (months resampled whole)."""
    me = np.array([sum(r["excess"]) for r in recs], dtype=float)
    net = np.array([r["net"] for r in recs], dtype=float)
    osum = np.array([r["other_sum"] for r in recs], dtype=float)
    on_ = np.array([r["other_n"] for r in recs], dtype=float)
    days = float(on_.sum() + n * len(recs))
    cost = (me - net) / 2  # per entry/exit day
    daily = np.concatenate([np.array(r["excess"], dtype=float) for r in recs])
    daily[0::n] -= cost
    daily[n - 1::n] -= cost
    mean = daily.sum() / days
    var = ((daily - mean) ** 2).sum() / days + (days - len(daily)) / days * mean ** 2
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(recs), (draws, len(recs)))
    diff = me[pick].sum(1) / (n * len(recs)) - osum[pick].sum(1) / on_[pick].sum(1)
    return {"months": len(recs), "sharpe": round(float(mean / np.sqrt(var) * np.sqrt(252)), 3),
            "diff_bp": round(float(me.sum() / (n * len(recs)) - osum.sum() / on_.sum()) * 1e4, 2),
            "diff_lo80_bp": round(float(np.percentile(diff, 20)) * 1e4, 2)}
