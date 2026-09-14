"""
When forward-test decisions are due, derived from real trading days (SPY bars).

  cutoff    the last trading day of a week, once its close has settled (16:15 ET)
  deadline  09:30 ET on the next weekday after the cutoff. If that weekday is a
            market holiday, the true open is later, so this is conservative:
            it can only reject a decision that would have been valid, never
            accept one made after the open.
  resolve   the close `horizon` trading days after the cutoff (settled at 16:15 ET)
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SETTLE = time(16, 15)
OPEN = time(9, 30)


def settled(d: date, now: datetime) -> bool:
    return now >= datetime.combine(d, SETTLE, NY)


def deadline(cutoff: date) -> datetime:
    nxt = cutoff + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return datetime.combine(nxt, OPEN, NY)


def weekly_cutoffs(trading_days: list[date], now: datetime) -> list[date]:
    """Last settled trading day of each completed week, oldest first."""
    days = sorted(d for d in trading_days if settled(d, now))
    out = []
    for i, d in enumerate(days):
        nxt = days[i + 1] if i + 1 < len(days) else None
        if nxt is not None:
            if nxt.isocalendar()[:2] != d.isocalendar()[:2]:
                out.append(d)
        else:
            # the newest settled day ends its week if it's a Friday or that week's Friday close has passed
            friday = d + timedelta(days=4 - d.weekday())
            if d.weekday() == 4 or settled(friday, now):
                out.append(d)
    return out


def resolve_day(cutoff: date, trading_days: list[date], horizon: int, now: datetime) -> date | None:
    later = sorted(d for d in trading_days if d > cutoff)
    if len(later) < horizon:
        return None
    d = later[horizon - 1]
    return d if settled(d, now) else None
