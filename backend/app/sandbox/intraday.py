"""The day-trading track's two pre-registered rules (docs/PLAN_60_V2.md "Day-trading track"), on 1-minute bars.

Bars are regular-hours minutes labelled by their start time in New York (scripts/intraday_data.py). Each rule gives
one trade (or none) per day and symbol, returned as that day's return on 1x capital after costs:
  D1 intraday momentum: sign(09:59 close / previous day's last close - 1) decides long or short at the 15:30 bar's
     open; exit at the 15:59 bar's close. Days without a 15:30 or 15:59 bar (half days) or without a previous close
     are skipped.
  D2 5-minute opening-range breakout: the 09:30-09:34 bars form the first 5-minute bar; up -> long, down -> short at
     the 09:35 bar's open; stop at the first bar's low (long) or high (short): the first later bar that trades through
     it exits at the stop, or at that bar's open if it opened beyond the stop; otherwise exit at the day's last close.
Nothing looks ahead: every decision uses bars that have closed before the order's bar opens.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _days(df: pd.DataFrame) -> dict[object, pd.DataFrame]:
    d = df.assign(day=df["ts"].dt.date, hm=df["ts"].dt.hour * 100 + df["ts"].dt.minute)
    return {k: g.set_index("hm") for k, g in d.groupby("day", sort=True)}


def d1_intraday_momentum(df: pd.DataFrame, cost: float) -> pd.Series:
    """Daily net return of D1 (cost per side, e.g. 0.0001 = 1 bp)."""
    out = {}
    prev_close = None
    for day, g in _days(df).items():
        if prev_close is not None and 959 in g.index and 1530 in g.index and 1559 in g.index:
            sig = np.sign(g.at[959, "close"] / prev_close - 1)
            if sig != 0:
                r = g.at[1559, "close"] / g.at[1530, "open"] - 1
                out[pd.Timestamp(day)] = float(sig * r - 2 * cost)
        prev_close = float(g["close"].iloc[-1])
    return pd.Series(out, dtype=float)


def d2_orb5(df: pd.DataFrame, cost: float) -> pd.Series:
    """Daily net return of D2 (cost per side)."""
    out = {}
    for day, g in _days(df).items():
        first = g.loc[(g.index >= 930) & (g.index <= 934)]
        if len(first) < 5 or 935 not in g.index:
            continue
        o, c = float(first["open"].iloc[0]), float(first["close"].iloc[-1])
        if c == o:
            continue
        side = 1 if c > o else -1
        stop = float(first["low"].min()) if side > 0 else float(first["high"].max())
        rest = g.loc[g.index >= 935]
        entry = float(rest["open"].iloc[0])
        exit_px = float(rest["close"].iloc[-1])
        for bo, lo, hi in rest[["open", "low", "high"]].itertuples(index=False):
            if side > 0 and lo <= stop:
                exit_px = min(bo, stop)
                break
            if side < 0 and hi >= stop:
                exit_px = max(bo, stop)
                break
        out[pd.Timestamp(day)] = float(side * (exit_px / entry - 1) - 2 * cost)
    return pd.Series(out, dtype=float)


def sharpe(r: pd.Series) -> float:
    return float(r.mean() / r.std() * np.sqrt(252)) if len(r) > 1 and r.std() > 0 else float("nan")


def block_ci(r: pd.Series, block: int = 21, n: int = 5000, seed: int = 0) -> tuple[float, float]:
    """95% block-bootstrap CI of the annualized Sharpe."""
    x = r.to_numpy()
    rng = np.random.default_rng(seed)
    nb = -(-len(x) // block)
    starts = rng.integers(0, max(1, len(x) - block), (n, nb))
    idx = (starts[:, :, None] + np.arange(block)).reshape(n, -1)[:, : len(x)]
    s = x[idx]
    sh = s.mean(1) / s.std(1) * np.sqrt(252)
    return float(np.percentile(sh, 2.5)), float(np.percentile(sh, 97.5))
