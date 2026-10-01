"""Pairs trading P1 (docs/PLAN_60_V2.md "Pairs trading P1"; Gatev, Goetzmann and Rouwenhorst, RFS 2006).

Each month start: over the previous 252 days, pick the 20 same-sector pairs with the smallest sum of squared
differences of normalized prices. For the next 126 days, trade each pair: a close more than 2 formation standard
deviations away opens the pair at the NEXT close (long the cheaper, short the dearer, $1 each side of the pair's
committed $1); the first close where the spread crosses 0 closes it, as does the period's end; a pair can reopen.
A portfolio's daily return is the mean over its 20 pairs; the book is the mean of the (up to 6) running portfolios.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FORM, TRADE, N_PAIRS, K = 252, 126, 20, 2.0


def select_pairs(px: pd.DataFrame, sectors: dict[str, str], n: int = N_PAIRS) -> list[tuple[str, str, float]]:
    """The n same-sector pairs with the smallest SSD of prices normalized to 1 at the first row; (a, b, sd of the
    normalized spread). Stocks with any missing price are left out."""
    px = px.dropna(axis=1)
    norm = px / px.iloc[0]
    cands: list[tuple[float, str, str]] = []
    for sec in sorted({sectors[c] for c in norm.columns if c in sectors}):
        cols = [c for c in norm.columns if sectors.get(c) == sec]
        if len(cols) < 2:
            continue
        m = norm[cols].to_numpy(float)
        sq = (m * m).sum(0)
        ssd = sq[:, None] + sq[None, :] - 2 * m.T @ m
        i, j = np.triu_indices(len(cols), 1)
        cands += [(float(ssd[a, b]), cols[a], cols[b]) for a, b in zip(i, j, strict=True)]
    cands.sort()
    return [(a, b, float((norm[a] - norm[b]).std())) for _, a, b in cands[:n]]


def trade_pair(pa: np.ndarray, pb: np.ndarray, sd: float, cost: float) -> np.ndarray:
    """Daily P&L (on $1 committed) of one pair over a trading period; prices from the period's first day."""
    na, nb = pa / pa[0], pb / pb[0]
    spread = na - nb
    pnl = np.zeros(len(pa))
    pos, ea, eb, last = 0, 0.0, 0.0, 0.0  # pos +1: long a / short b; last = open trade's value so far
    pending = 0  # the side to open at this close (signal seen at the previous close)
    for t in range(len(pa)):
        if pos:
            val = pos * ((pa[t] / ea - 1) - (pb[t] / eb - 1))
            pnl[t] += val - last
            last = val
            if pos * spread[t] >= 0 or t == len(pa) - 1:  # crossed 0 (pos is -sign of the opening spread)
                pnl[t] -= 2 * cost
                pos, last = 0, 0.0
                continue
        if pending and not pos:
            pos, ea, eb, last = pending, pa[t], pb[t], 0.0
            pnl[t] -= 2 * cost
            pending = 0
            if t == len(pa) - 1:  # opened on the last day: closed at once
                pnl[t] -= 2 * cost
                pos = 0
            continue
        if not pos and sd > 0 and abs(spread[t]) > K * sd and t < len(pa) - 1:
            pending = -1 if spread[t] > 0 else 1  # a dear: short a, long b
    return pnl


def pairs_ggr(closes: pd.DataFrame, sectors: dict[str, str], cost: float = 0.001,
              form: int = FORM, trade: int = TRADE, n: int = N_PAIRS) -> pd.DataFrame:
    """The book's daily returns: ret, portfolios (running), invested (share of pairs open)."""
    closes = closes.sort_index()
    days = closes.index
    starts = [i for i in range(form, len(days)) if days[i].month != days[i - 1].month]
    total = pd.Series(0.0, index=days)
    count = pd.Series(0, index=days)
    opened = pd.Series(0.0, index=days)
    for s in starts:
        pairs = select_pairs(closes.iloc[s - form:s], sectors, n)
        if not pairs:
            continue
        seg = closes.iloc[s: s + trade].ffill()
        port = np.zeros(len(seg))
        live = np.zeros(len(seg))
        for a, b, sd in pairs:
            p = trade_pair(seg[a].to_numpy(float), seg[b].to_numpy(float), sd, cost)
            port += p / len(pairs)
            live += (p != 0) / len(pairs)
        total.iloc[s: s + len(seg)] += port
        count.iloc[s: s + len(seg)] += 1
        opened.iloc[s: s + len(seg)] += live
    ok = count > 0
    return pd.DataFrame({"ret": total[ok] / count[ok], "portfolios": count[ok], "invested": opened[ok] / count[ok]})
