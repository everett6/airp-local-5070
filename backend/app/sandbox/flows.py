"""Flow-pressure rules R1, M1 and A1 (docs/PLAN_60_V2.md "Three flow-pressure tests", specs fixed 2026-10-01).
Pure functions of dates and returns; every signal for day t+1 uses data through the close of day t only.
"""
from __future__ import annotations

from collections.abc import Iterable

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
