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


# --- the exchange calendar and the entry session of an earnings release (PLAN_60_V2, "One entry-time rule") ---

PREOPEN_CUTOFF_UTC_HOUR = 13  # the scoring rule's cutoff (app/sandbox/events.py imports it): always before 09:30 NY


def _easter(year: int) -> date:
    """Western Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def _nth(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th `weekday` (Mon=0) of a month; n = -1 is the last one."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def nyse_holidays(year: int) -> set[date]:
    """The New York Stock Exchange's full-day holidays of a year (its Rule 7.2). A holiday on a Sunday is kept on
    the Monday; one on a Saturday on the Friday before, except New Year's Day (the Friday would be the year's last
    trading day, which stays open). Juneteenth since 2022. Unscheduled closures (storms, days of mourning) are not
    knowable in advance and are not here."""
    fixed = [date(year, 1, 1), date(year, 7, 4), date(year, 12, 25)] + ([date(year, 6, 19)] if year >= 2022 else [])
    out = {_nth(year, 1, 0, 3), _nth(year, 2, 0, 3), _easter(year) - timedelta(days=2), _nth(year, 5, 0, -1),
           _nth(year, 9, 0, 1), _nth(year, 11, 3, 4)}
    for d in fixed:
        if d.weekday() == 6:
            out.add(d + timedelta(days=1))
        elif d.weekday() == 5:
            if (d.month, d.day) != (1, 1):
                out.add(d - timedelta(days=1))
        else:
            out.add(d)
    return out


def is_session(d: date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year)


def entry_session(accepted_utc: datetime) -> date:
    """The trading session at whose open a release can first be traded, by the scoring rule every backtest uses
    (app/sandbox/events.entry_index): the day of the acceptance if it is a session and the acceptance (UTC) is
    before 13:00, else the next session."""
    d = accepted_utc.date()
    if is_session(d) and accepted_utc.hour < PREOPEN_CUTOFF_UTC_HOUR:
        return d
    d += timedelta(days=1)
    while not is_session(d):
        d += timedelta(days=1)
    return d
