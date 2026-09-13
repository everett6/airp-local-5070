"""
Point-in-time access to REAL daily closes (loaded from a CSV — see
`scripts/fetch_prices.py`), for the walk-forward simulation.

The guarantee is structural, not a filter someone has to remember to call:

  - `PriceTable` holds the full history and lives only in the trusted
    orchestrator process. It never crosses into the agent jail.
  - `PriceTable.view(as_of)` returns a `PointInTimeView` built from COPIES of
    only the rows dated <= `as_of`. Rows after the cutoff are not hidden
    behind a check — they are simply not in the object, so no bug in the
    agent can read them.
  - `PriceTable.outcome()` (the grading lookup) calls
    `clock.enforce_point_in_time` on the date it reads, so calling it from
    inside a `sandbox_scope` raises `LookaheadViolation`, same as the
    connectors.
  - `anonymize()` strips ticker and calendar dates and rebases prices to 100,
    following Glasserman & Lin (2023, arXiv:2309.17322): an LLM that sees
    "AAPL, 2025-03-14" can recall what happened next from its training data;
    one that sees "Asset, day -1, price 103.2" cannot.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from app.sandbox.clock import LookaheadViolation, enforce_point_in_time


def _as_dt(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


@dataclass(frozen=True)
class PointInTimeView:
    as_of: date
    dates: tuple[date, ...]
    closes: dict[str, tuple[float, ...]]

    def __post_init__(self) -> None:
        # Defence in depth: refuse to even construct a view carrying a row
        # after its own cutoff.
        if self.dates and self.dates[-1] > self.as_of:
            raise LookaheadViolation(
                as_of=_as_dt(self.as_of), attempted_timestamp=_as_dt(self.dates[-1]),
                source="PointInTimeView",
            )

    def history(self, ticker: str, lookback: int) -> list[float]:
        return list(self.closes[ticker][-lookback:])

    def anonymize(self, ticker: str, market: str, lookback: int) -> dict[str, list[float]]:
        """Rebased-to-100 series with no ticker and no dates."""
        def rebase(xs: list[float]) -> list[float]:
            base = xs[0]
            return [round(100.0 * x / base, 3) for x in xs]
        return {
            "asset": rebase(self.history(ticker, lookback)),
            "market": rebase(self.history(market, lookback)),
        }


class PriceTable:
    def __init__(self, dates: list[date], closes: dict[str, list[float]]) -> None:
        self.dates = dates
        self.closes = closes

    @classmethod
    def from_csv(cls, path: Path) -> PriceTable:
        with path.open() as f:
            reader = csv.reader(f)
            header = next(reader)
            tickers = header[1:]
            dates: list[date] = []
            closes: dict[str, list[float]] = {t: [] for t in tickers}
            for row in reader:
                if not row or not row[0][:4].isdigit():
                    continue  # yfinance writes extra header rows
                vals = [float(v) if v else math.nan for v in row[1:]]
                if any(math.isnan(v) for v in vals):
                    continue
                dates.append(date.fromisoformat(row[0][:10]))
                for t, v in zip(tickers, vals, strict=True):
                    closes[t].append(v)
        return cls(dates, closes)

    @property
    def tickers(self) -> list[str]:
        return list(self.closes)

    def index_on_or_before(self, d: date) -> int:
        lo, hi = 0, len(self.dates) - 1
        if self.dates[0] > d:
            raise ValueError(f"no data on or before {d}")
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.dates[mid] <= d:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def view(self, as_of: date) -> PointInTimeView:
        end = self.index_on_or_before(as_of) + 1
        return PointInTimeView(
            as_of=as_of,
            dates=tuple(self.dates[:end]),
            closes={t: tuple(v[:end]) for t, v in self.closes.items()},
        )

    def outcome(self, ticker: str, as_of: date, horizon: int) -> tuple[date, float]:
        """(resolution date, forward return over `horizon` trading days).
        Raises LookaheadViolation if called inside an active sandbox scope."""
        i = self.index_on_or_before(as_of)
        j = i + horizon
        if j >= len(self.dates):
            raise ValueError(f"outcome for {as_of}+{horizon}d not yet known")
        enforce_point_in_time(_as_dt(self.dates[j]), source=f"PriceTable.outcome:{ticker}")
        return self.dates[j], self.closes[ticker][j] / self.closes[ticker][i] - 1.0
