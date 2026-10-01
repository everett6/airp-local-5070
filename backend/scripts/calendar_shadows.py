"""Forward-only T1 / O1 shadows (docs/PLAN_60_V2.md, fixed 2026-09-29).

    python scripts/calendar_shadows.py   # print the no-money forward summary; changes nothing

The frozen calendar_fx and overnight functions run on adjusted SPY daily bars. Only dates from FORWARD_FROM are
reported; earlier bars are present solely to supply each rule's required history.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd

from app.sandbox import intraday

FORWARD_FROM, VERDICT_ON, COST = "2026-09-30", date(2028, 9, 30), 1e-4


def spy_daily(start: str, end: str) -> pd.DataFrame:
    """Download adjusted SPY Open and Close bars on a timezone-naive date index."""
    import yfinance as yf  # type: ignore[import-untyped]

    raw = cast(pd.DataFrame, yf.download("SPY", start=start, end=end, auto_adjust=True,
                                         progress=False, multi_level_index=False))
    daily = raw.loc[:, ["Open", "Close"]].copy()
    idx = pd.DatetimeIndex(pd.to_datetime(daily.index))
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    daily.index = idx.normalize()
    return daily.sort_index()


def _summary(s: pd.Series, active_days: int) -> dict[str, Any]:
    n = len(s)
    return {
        "days": n,
        "active_days": active_days,
        "cum_net_excess_pct": round(100 * (float((1 + s.to_numpy(dtype=float)).prod()) - 1), 2) if n else None,
        "sharpe": round(float(intraday.sharpe(s)), 2) if n >= 20 else None,
        "ci95": [round(float(v), 2) for v in intraday.block_ci(s)] if n >= 60 else None,
    }


def compute(daily: pd.DataFrame, rf_annual: pd.Series, today: date) -> dict[str, Any]:
    """Compute frozen shadow returns from supplied data; this function does no I/O."""
    from app.sandbox import calendar_fx, overnight

    prices = daily.copy()
    prices.index = pd.DatetimeIndex(pd.to_datetime(prices.index)).tz_localize(None).normalize()
    prices = prices.loc[prices.index < pd.Timestamp(today)]
    rf = rf_annual.copy()
    rf.index = pd.DatetimeIndex(pd.to_datetime(rf.index)).tz_localize(None).normalize()

    if len(prices):
        excess = prices["Close"].pct_change() - rf.reindex(prices.index, method="ffill") / 252
        overlay = calendar_fx.tom_overlay(excess.dropna(), COST)
        t1 = overlay.loc[overlay.index >= FORWARD_FROM, "ret"]
        t1_active = int(overlay.loc[overlay.index >= FORWARD_FROM, "tom"].sum())
        legs = overnight.overnight_legs(prices["Open"], prices["Close"])
        rf_daily = (rf / 252).reindex(legs.index, method="ffill")
        o1_all = overnight.overnight_excess(legs, rf_daily, COST)
        o1 = o1_all.loc[o1_all.index >= FORWARD_FROM]
    else:
        t1, o1, t1_active = pd.Series(dtype=float), pd.Series(dtype=float), 0
    return {"forward_from": FORWARD_FROM, "verdict_due": today >= VERDICT_ON,
            "T1": _summary(t1, t1_active), "O1": _summary(o1, int((o1 != 0).sum()))}


def forward(today: date | None = None) -> dict[str, Any]:
    from vol_target_b0 import tbill

    today = today or datetime.now(UTC).date()
    start = (date.fromisoformat(FORWARD_FROM) - timedelta(days=45)).isoformat()
    daily = spy_daily(start, today.isoformat())
    daily = daily.loc[daily.index < pd.Timestamp(today)]
    return compute(daily, tbill(), today)


if __name__ == "__main__":
    print(json.dumps(forward(), indent=1))
