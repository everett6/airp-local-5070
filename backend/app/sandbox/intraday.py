"""The day-trading track's two pre-registered rules (docs/PLAN_60_V2.md "Day-trading track"), on 1-minute bars.

Bars are regular-hours minutes labelled by their start time in New York (scripts/intraday_data.py). Each rule gives
one trade (or none) per day and symbol, returned as that day's return on 1x capital after costs:
  D1 intraday momentum: sign(09:59 close / previous day's last close - 1) decides long or short at the 15:30 bar's
     open; exit at the 15:59 bar's close. Days without a 15:30 or 15:59 bar (half days) or without a previous close
     are skipped.
  D2 5-minute opening-range breakout: the 09:30-09:34 bars form the first 5-minute bar; up -> long, down -> short at
     the 09:35 bar's open; stop at the first bar's low (long) or high (short): the first later bar that trades through
     it exits at the stop, or at that bar's open if it opened beyond the stop; otherwise exit at the day's last close.
  D3 noise area + VWAP stop (round 2): checks at 10:00..15:30 against bounds built from the previous 14 days' average
     absolute move from the open at that minute; see d3_noise_vwap.
  D4 rest-of-day momentum (round 2): like D1, but the signal is yesterday's close to the 15:29 bar's close.
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


def d4_rod_momentum(df: pd.DataFrame, cost: float) -> pd.Series:
    """Daily net return of D4 (cost per side): sign(15:29 close / previous close - 1), 15:30 open to 15:59 close."""
    out = {}
    prev_close = None
    for day, g in _days(df).items():
        if prev_close is not None and 1529 in g.index and 1530 in g.index and 1559 in g.index:
            sig = np.sign(g.at[1529, "close"] / prev_close - 1)
            if sig != 0:
                out[pd.Timestamp(day)] = float(sig * (g.at[1559, "close"] / g.at[1530, "open"] - 1) - 2 * cost)
        prev_close = float(g["close"].iloc[-1])
    return pd.Series(out, dtype=float)


CHECKS = [h * 100 + m for h in range(10, 16) for m in (0, 30)]  # 10:00 .. 15:30


def _hm_minus_1(hm: int) -> int:
    return hm - 41 if hm % 100 == 0 else hm - 1  # 1000 -> 959, 1030 -> 1029


def d3_noise_vwap(df: pd.DataFrame, cost: float, lookback: int = 14) -> pd.Series:
    """Daily net return of D3 (cost per side per entry and exit); days before `lookback` history are skipped."""
    days = _days(df)
    keys = list(days)
    moves: dict[object, pd.Series] = {}  # |close / open - 1| by minute, per day
    out = {}
    prev_close = None
    for i, k in enumerate(keys):
        g = days[k]
        o = float(g["open"].iloc[0])
        moves[k] = (g["close"] / o - 1).abs()
        hist = [moves[x] for x in keys[max(0, i - lookback): i]]
        if prev_close is None or len(hist) < lookback or 930 not in g.index or 1559 not in g.index:
            prev_close = float(g["close"].iloc[-1])
            continue
        sigma = pd.concat(hist, axis=1).mean(axis=1)
        vwap = ((g["high"] + g["low"] + g["close"]) / 3 * g["volume"]).cumsum() / g["volume"].cumsum()
        hi_ref, lo_ref = max(o, prev_close), min(o, prev_close)
        pos, entry, ret = 0, 0.0, 0.0
        for hm in CHECKS:
            t = _hm_minus_1(hm)
            if t not in g.index or hm not in g.index or t not in sigma.index or pd.isna(sigma[t]):
                continue
            px, sg, vw = float(g.at[t, "close"]), float(sigma[t]), float(vwap[t])
            ub, lb = hi_ref * (1 + sg), lo_ref * (1 - sg)
            if (pos > 0 and px < max(ub, vw)) or (pos < 0 and px > min(lb, vw)):
                ret += pos * (float(g.at[hm, "open"]) / entry - 1) - cost
                pos = 0
            if pos == 0:
                new = 1 if px > ub else -1 if px < lb else 0
                if new:
                    pos, entry = new, float(g.at[hm, "open"])
                    ret -= cost
        if pos:
            ret += pos * (float(g.at[1559, "close"]) / entry - 1) - cost
        out[pd.Timestamp(k)] = ret
        prev_close = float(g["close"].iloc[-1])
    return pd.Series(out, dtype=float)


def d5_eod_reversal(df: pd.DataFrame, cost: float, frac: float = 0.10, min_names: int = 20) -> pd.DataFrame:
    """D5, end-of-day reversal (cross-section): daily net return of long the lowest-ROD3 decile / short the highest,
    dollar neutral (each leg half the capital). `df`: ts (New York), symbol, open, close for the 14:30 and 15:30
    30-minute bars. ROD3 = previous day's 15:30-bar close to today's 14:30-bar close; trade = today's 15:30-bar open
    to its close. Half days are dropped first: with a trade-count column `n`, a day whose median 15:30-bar count is
    under 100 (after-hours prints only; regular days have thousands). Returns a frame by day: ret (net), long, short
    (each leg's gross return), names."""
    if "n" in df:
        last = df[(df["ts"].dt.hour == 15) & (df["ts"].dt.minute == 30)]
        med = last.groupby(last["ts"].dt.normalize())["n"].median()
        df = df[~df["ts"].dt.normalize().isin(med.index[med < 100])]
    d = df.assign(day=df["ts"].dt.normalize().dt.tz_localize(None), hm=df["ts"].dt.hour * 100 + df["ts"].dt.minute)
    c1430 = d[d["hm"] == 1430].pivot_table(index="day", columns="symbol", values="close")
    b = d[d["hm"] == 1530]
    o1530 = b.pivot_table(index="day", columns="symbol", values="open")
    c1530 = b.pivot_table(index="day", columns="symbol", values="close")
    days = o1530.index.intersection(c1430.index)
    prev = c1530.shift(1).reindex(days)  # yesterday's last close (the previous row with a 15:30 bar)
    rod3 = c1430.reindex(days) / prev - 1
    lh = c1530.reindex(days) / o1530.reindex(days) - 1
    out = {}
    for day in days:
        x = pd.concat([rod3.loc[day], lh.loc[day]], axis=1, keys=["s", "r"]).dropna()
        if len(x) < min_names:
            continue
        k = max(1, int(len(x) * frac))
        x = x.sort_values("s")
        lo, hi = float(x["r"].iloc[:k].mean()), float(x["r"].iloc[-k:].mean())
        out[day] = {"ret": 0.5 * lo - 0.5 * hi - 2 * cost, "long": lo, "short": -hi, "names": len(x)}
    return pd.DataFrame.from_dict(out, orient="index")


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
