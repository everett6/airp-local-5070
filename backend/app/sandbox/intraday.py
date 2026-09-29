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
  D6 box theory (round 4): fade the edges of yesterday's high-low range, target its middle; see d6_box_theory.
  D7 intraday periodicity (round 4, 30-minute cross-section) and D8 Darvas box (round 4, daily bars): see below.
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


def _drop_half_days(df: pd.DataFrame) -> pd.DataFrame:
    """30-minute bars without a day whose median 15:30-bar trade count `n` is under 100 (after-hours prints only)."""
    if "n" not in df:
        return df
    last = df[(df["ts"].dt.hour == 15) & (df["ts"].dt.minute == 30)]
    med = last.groupby(last["ts"].dt.normalize())["n"].median()
    return df[~df["ts"].dt.normalize().isin(med.index[med < 100])]


def d5_eod_reversal(df: pd.DataFrame, cost: float, frac: float = 0.10, min_names: int = 20) -> pd.DataFrame:
    """D5, end-of-day reversal (cross-section): daily net return of long the lowest-ROD3 decile / short the highest,
    dollar neutral (each leg half the capital). `df`: ts (New York), symbol, open, close for the 14:30 and 15:30
    30-minute bars. ROD3 = previous day's 15:30-bar close to today's 14:30-bar close; trade = today's 15:30-bar open
    to its close. Half days are dropped first: with a trade-count column `n`, a day whose median 15:30-bar count is
    under 100 (after-hours prints only; regular days have thousands). Returns a frame by day: ret (net), long, short
    (each leg's gross return), names."""
    d = _drop_half_days(df)
    d = d.assign(day=df["ts"].dt.normalize().dt.tz_localize(None), hm=df["ts"].dt.hour * 100 + df["ts"].dt.minute)
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


def d6_box_theory(df: pd.DataFrame, cost: float) -> pd.Series:
    """Daily net return of D6 (cost per side): at most one trade a day. Box = yesterday's high/low (today's
    09:30-09:59 range after a gap outside it); close in the bottom quarter -> long, top quarter -> short, at the next
    bar's open; exit at the next open once the close reaches the middle or leaves the box by a quarter of its width,
    else at the 15:59 close. Entries are decided on bars up to 15:29."""
    out = {}
    prev = None
    for day, g in _days(df).items():
        if prev is None or 930 not in g.index or 1559 not in g.index:
            prev = g
            continue
        hi, lo = float(prev["high"].max()), float(prev["low"].min())
        o = float(g.at[930, "open"])
        start = 930
        if not lo <= o <= hi:
            first = g.loc[(g.index >= 930) & (g.index <= 959)]
            hi, lo, start = float(first["high"].max()), float(first["low"].min()), 1000
        prev = g
        w = hi - lo
        if w <= 0:
            continue
        mid, q = (hi + lo) / 2, 0.25 * w
        hms, opens, closes = g.index.to_numpy(), g["open"].to_numpy(float), g["close"].to_numpy(float)
        pos, entry, ret = 0, 0.0, None
        for k in range(len(hms) - 1):
            if hms[k] < start:
                continue
            c = closes[k]
            if pos == 0:
                if hms[k] > 1529:
                    break
                side = 1 if c <= lo + q else -1 if c >= hi - q else 0
                if side:
                    pos, entry = side, opens[k + 1]
            elif (pos > 0 and (c >= mid or c < lo - q)) or (pos < 0 and (c <= mid or c > hi + q)):
                ret = pos * (opens[k + 1] / entry - 1) - 2 * cost
                break
        if pos and ret is None:
            ret = pos * (closes[-1] / entry - 1) - 2 * cost
        out[pd.Timestamp(day)] = ret if ret is not None else 0.0
    return pd.Series(out, dtype=float)


def d7_periodicity(df: pd.DataFrame, cost: float, frac: float = 0.10, lookback: int = 20, min_obs: int = 15,
                   min_names: int = 20) -> pd.DataFrame:
    """D7, intraday periodicity (cross-section): long the stocks whose 15:30-bar return (open to close) averaged
    highest over their previous `lookback` full days, short the lowest, dollar neutral, today's 15:30 bar. Same
    input and output as d5_eod_reversal."""
    d = _drop_half_days(df)
    d = d.assign(day=d["ts"].dt.normalize().dt.tz_localize(None), hm=d["ts"].dt.hour * 100 + d["ts"].dt.minute)
    b = d[d["hm"] == 1530]
    r = (b.pivot_table(index="day", columns="symbol", values="close")
         / b.pivot_table(index="day", columns="symbol", values="open") - 1)
    sig = r.shift(1).rolling(lookback, min_periods=min_obs).mean()
    out = {}
    for day in r.index:
        x = pd.concat([sig.loc[day], r.loc[day]], axis=1, keys=["s", "r"]).dropna()
        if len(x) < min_names:
            continue
        k = max(1, int(len(x) * frac))
        x = x.sort_values("s")
        lo, hi = float(x["r"].iloc[:k].mean()), float(x["r"].iloc[-k:].mean())
        out[day] = {"ret": 0.5 * hi - 0.5 * lo - 2 * cost, "long": hi, "short": -lo, "names": len(x)}
    return pd.DataFrame.from_dict(out, orient="index")


def d9_open_reversal(daily: pd.DataFrame, symbols: list[str], cost: float, frac: float = 0.10,
                     min_names: int = 20) -> pd.DataFrame:
    """D9, opening-auction reversal (daily bars Date/Ticker/Open/Close): short the stocks with the highest overnight
    return (open / previous close - 1), long the lowest, open to close, dollar neutral. Returns by day: ret (net),
    long, short (each leg's gross return), names."""
    d = daily[daily["Ticker"].isin(symbols)]
    o = d.pivot_table(index="Date", columns="Ticker", values="Open").sort_index()
    c = d.pivot_table(index="Date", columns="Ticker", values="Close").sort_index()
    sig, r = (o / c.shift(1) - 1).to_numpy(float), (c / o - 1).to_numpy(float)
    out = {}
    for i, day in enumerate(o.index):
        ok = np.isfinite(sig[i]) & np.isfinite(r[i])
        n = int(ok.sum())
        if n < min_names:
            continue
        k = max(1, int(n * frac))
        order = np.argsort(sig[i][ok], kind="stable")
        rr = r[i][ok][order]
        lo, hi = float(rr[:k].mean()), float(rr[-k:].mean())
        out[pd.Timestamp(day)] = {"ret": 0.5 * lo - 0.5 * hi - 2 * cost, "long": lo, "short": -hi, "names": n}
    return pd.DataFrame.from_dict(out, orient="index")


def darvas_signals(h: np.ndarray, lo: np.ndarray, c: np.ndarray, confirm: int = 3, year: int = 252
                   ) -> tuple[dict[int, tuple[float, float]], dict[int, float]]:
    """Darvas boxes of one stock's daily bars. Returns ({day: (top, bottom)} for breakout days (close above a
    complete box's top), {day: bottom} for days a box completed). A box starts at a 52-week high; its top is fixed
    once `confirm` later highs stay below it; its bottom is the lowest low since, fixed once `confirm` later lows stay
    above it; a close below the bottom before a breakout cancels it."""
    brk: dict[int, tuple[float, float]] = {}
    done: dict[int, float] = {}
    top = bot = None  # candidate values
    tcnt = bcnt = 0
    top_set = bot_set = False
    for i in range(year, len(h)):
        new_high = h[i] >= h[i - year:i].max()
        if bot_set:  # a complete box: wait for a breakout or a cancel
            if c[i] > top:  # type: ignore[operator]
                brk[i] = (float(top), float(bot))  # type: ignore[arg-type]
            if c[i] > top or c[i] < bot:  # type: ignore[operator]
                top_set = bot_set = False
                top, tcnt = (float(h[i]), 0) if new_high else (None, 0)
            continue
        if top_set:  # looking for the bottom
            if h[i] > top:  # type: ignore[operator]
                top_set, top, tcnt = False, float(h[i]), 0
            elif lo[i] < bot:  # type: ignore[operator]
                bot, bcnt = float(lo[i]), 0
            else:
                bcnt += 1
                if bcnt >= confirm:
                    bot_set = True
                    done[i] = float(bot)  # type: ignore[arg-type]
            continue
        if top is not None and h[i] < top:
            tcnt += 1
            if tcnt >= confirm:
                top_set = True
                k = i - confirm + 1 + int(np.argmin(lo[i - confirm + 1: i + 1]))
                bot, bcnt = float(lo[k]), i - k
                if bcnt >= confirm:
                    bot_set = True
                    done[i] = float(bot)
        elif new_high:
            top, tcnt = float(h[i]), 0
    return brk, done


def d8_darvas(daily: pd.DataFrame, symbols: list[str], cost: float = 0.001, slots: int = 20) -> pd.DataFrame:
    """D8, Darvas box book (daily bars Date/Ticker/Open/High/Low/Close; must include SPY): long up to `slots`
    positions of 1/slots each (rebalanced to that weight daily), free slots in SPY. A breakout (close above a box
    top) buys at the next open, strongest close/top first when slots are short; the stop is the box bottom, raised to
    the bottom of each later box; a close below it sells at the next open. Cost per side on the slot's weight.
    Returns by day: ret (book), spy, excess (ret - spy), held."""
    px = {f: daily.pivot_table(index="Date", columns="Ticker", values=f) for f in ("Open", "High", "Low", "Close")}
    px = {f: v.sort_index() for f, v in px.items()}
    days = px["Close"].index
    names = [s for s in symbols if s in px["Close"]]
    brk: dict[str, dict[int, tuple[float, float]]] = {}
    done: dict[str, dict[int, float]] = {}
    for s in names:
        brk[s], done[s] = darvas_signals(px["High"][s].to_numpy(float), px["Low"][s].to_numpy(float),
                                         px["Close"][s].to_numpy(float))
    col = {s: j for j, s in enumerate(px["Close"].columns)}
    O, C = px["Open"].to_numpy(float), px["Close"].to_numpy(float)
    spy = np.nan_to_num(px["Close"]["SPY"].pct_change().to_numpy(float))
    w = 1.0 / slots

    def ret(a: float, b: float) -> float:
        return float(b / a - 1) if np.isfinite(a) and np.isfinite(b) and a > 0 else 0.0
    held: dict[str, float] = {}  # symbol -> stop
    buy: dict[str, float] = {}  # to buy at today's open, with its stop
    sell: set[str] = set()  # to sell at today's open
    out = {}
    for i in range(1, len(days)):
        r = 0.0
        for s in list(held):
            j = col[s]
            if s in sell:
                r += w * ret(C[i - 1, j], O[i, j]) - w * cost
                del held[s]
            else:
                r += w * ret(C[i - 1, j], C[i, j])
        for s, stop in buy.items():
            held[s] = stop
            r += w * ret(O[i, col[s]], C[i, col[s]]) - w * cost
        r += w * (slots - len(held)) * spy[i]
        sell = set()
        for s, stop in held.items():
            if i in done[s]:
                held[s] = stop = max(stop, done[s][i])
            if C[i, col[s]] < stop:
                sell.add(s)
        cands = sorted(((C[i, col[s]] / b[0], s, b[1]) for s in names
                        if s not in held and (b := brk[s].get(i)) is not None
                        and i + 1 < len(days) and np.isfinite(O[i + 1, col[s]])), reverse=True)
        buy = {s: bottom for _, s, bottom in cands[: slots - len(held)]}
        out[days[i]] = {"ret": r, "spy": float(spy[i]), "held": len(held)}
    res = pd.DataFrame.from_dict(out, orient="index")
    res.index = pd.to_datetime(res.index)
    res["excess"] = res["ret"] - res["spy"]
    return res


def sharpe(r: pd.Series) -> float:
    return float(r.mean() / r.std() * np.sqrt(252)) if len(r) > 1 and r.std() > 0 else float("nan")


def block_ci(r: pd.Series, block: int = 21, n: int = 5000, seed: int = 0, level: float = 0.95
             ) -> tuple[float, float]:
    """Block-bootstrap CI (95% by default) of the annualized Sharpe."""
    x = r.to_numpy()
    rng = np.random.default_rng(seed)
    nb = -(-len(x) // block)
    starts = rng.integers(0, max(1, len(x) - block), (n, nb))
    idx = (starts[:, :, None] + np.arange(block)).reshape(n, -1)[:, : len(x)]
    s = x[idx]
    sh = s.mean(1) / s.std(1) * np.sqrt(252)
    tail = (1 - level) / 2 * 100
    return float(np.percentile(sh, tail)), float(np.percentile(sh, 100 - tail))
