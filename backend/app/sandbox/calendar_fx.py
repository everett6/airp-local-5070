"""Turn-of-the-month T1 (docs/PLAN_60_V2.md "Turn-of-the-month T1"; Lakonishok and Smidt 1988, McConnell and Xu 2008).

TOM days: the last trading day of each month and the first 3 of the next (held from the close of the 2nd-last
trading day to the close of the 3rd). The dates come from the price index itself, so they are known in advance.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def tom_days(index: pd.DatetimeIndex, before: int = 1, after: int = 3) -> pd.Series:
    """True on the last `before` and first `after` trading days of each month in `index`. The index's first and
    last months may be cut off, so their first / last days are only marked when a month before / after exists."""
    idx = pd.DatetimeIndex(index)
    month = pd.Series(idx.to_period("M"), index=idx)
    first = month.groupby(month).cumcount()  # 0 = first trading day of the month
    last = month.groupby(month).cumcount(ascending=False)  # 0 = last trading day
    has_prev, has_next = month > month.iloc[0], month < month.iloc[-1]
    return pd.Series((((first < after) & has_prev) | ((last < before) & has_next)).to_numpy(), index=idx)


def tom_overlay(excess: pd.Series, cost: float = 1e-4) -> pd.DataFrame:
    """Overlay: the excess return on TOM days, 0 otherwise, less `cost` on each entry and exit day."""
    tom = tom_days(pd.DatetimeIndex(excess.index))
    prev, nxt = tom.shift(1, fill_value=False), tom.shift(-1, fill_value=False)
    entry, exit_ = tom & ~prev, tom & ~nxt
    ret = excess.where(tom, 0.0) - cost * (entry.astype(float) + exit_.astype(float))
    return pd.DataFrame({"ret": ret, "tom": tom})


def diff_ci(x: pd.Series, tom: pd.Series, block: int = 21, n: int = 5000, seed: int = 0,
            level: float = 0.95) -> tuple[float, float, float]:
    """Mean of x on TOM days minus the mean on other days, and its block-bootstrap CI (pairs resampled together)."""
    xv, tv = x.to_numpy(float), tom.to_numpy(bool)
    rng = np.random.default_rng(seed)
    nb = -(-len(xv) // block)
    starts = rng.integers(0, max(1, len(xv) - block), (n, nb))
    idx = (starts[:, :, None] + np.arange(block)).reshape(n, -1)[:, : len(xv)]
    xs, ts = xv[idx], tv[idx]
    d = (xs * ts).sum(1) / ts.sum(1) - (xs * ~ts).sum(1) / (~ts).sum(1)
    tail = (1 - level) / 2 * 100
    point = float(xv[tv].mean() - xv[~tv].mean())
    return point, float(np.percentile(d, tail)), float(np.percentile(d, 100 - tail))
