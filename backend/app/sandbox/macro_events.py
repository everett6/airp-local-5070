"""Scheduled macro-announcement overlay for the pre-registered E1 trial."""
from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


def event_days(index: pd.DatetimeIndex, dates: Iterable[str]) -> pd.Series:
    """Mark event dates that coincide with the supplied trading-day index."""
    idx = pd.DatetimeIndex(index)
    event_dates = {pd.Timestamp(value).normalize() for value in dates}
    return pd.Series(idx.normalize().isin(event_dates), index=idx, dtype=bool)


def announcement_strategy(excess: pd.Series, events: pd.Series,
                          cost: float = 1e-4) -> pd.DataFrame:
    """Earn event-day excess return, paying cost on the first and last day of each run."""
    held = events.reindex(excess.index, fill_value=False).astype(bool)
    prev = held.shift(1, fill_value=False)
    nxt = held.shift(-1, fill_value=False)
    entry, exit_ = held & ~prev, held & ~nxt
    ret = excess.where(held, 0.0) - cost * (entry.astype(float) + exit_.astype(float))
    return pd.DataFrame({"ret": ret, "held": held})
