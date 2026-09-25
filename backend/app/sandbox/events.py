"""
Earnings events: when a trade on an 8-K could first happen, what it returned against its sector, and how to score
a signal on thousands of events.

Timing (the only way lookahead could enter; tested in tests/test_events.py):
  - SEC acceptance times are UTC. A release accepted before 13:00 UTC on a trading day (8:00 New York in winter,
    9:00 in summer, both before the 9:30 open) can be traded at that day's open; anything later trades at the
    next trading day's open.
  - Outcomes run open-to-open from the entry: horizon H means open[entry + H] / open[entry] - 1, minus the same
    for the company's SPDR sector ETF (so a rising market or sector can't fake skill).
  - The code-only baseline, earnings-announcement drift (EAR), needs the market's reaction on the entry day,
    so it trades one day later: signal = close[entry] / close[entry - 1] - 1 minus the sector's, entry at open
    [entry + 1].

Scoring: rank IC between the signal and the excess return within each calendar month of entries, averaged over
months with a bootstrap CI over months; plus the top-minus-bottom quintile spread.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

PREOPEN_CUTOFF_UTC_HOUR = 13


@dataclass
class Prices:
    open: pd.DataFrame   # trading days x tickers
    close: pd.DataFrame

    @classmethod
    def from_long(cls, df: pd.DataFrame, calendar: str = "SPY") -> Prices:
        d = df.copy()
        d["Date"] = pd.to_datetime(d["Date"])
        o = d.pivot(index="Date", columns="Ticker", values="Open").sort_index()
        c = d.pivot(index="Date", columns="Ticker", values="Close").sort_index()
        days = c[calendar].dropna().index
        return cls(open=o.reindex(days), close=c.reindex(days))


def entry_index(days: pd.DatetimeIndex, accepted_utc: datetime) -> int | None:
    """Index of the first trading day whose open comes after the acceptance time; None if beyond the data."""
    day = pd.Timestamp(accepted_utc.date())
    i = int(days.searchsorted(day))
    if i < len(days) and days[i] == day and accepted_utc.hour < PREOPEN_CUTOFF_UTC_HOUR:
        return i
    i = int(days.searchsorted(day, side="right"))
    return i if i < len(days) else None


def fwd_excess(p: Prices, ticker: str, etf: str, i: int, h: int) -> float | None:
    if i + h >= len(p.open.index) or ticker not in p.open.columns or etf not in p.open.columns:
        return None
    s0, s1 = p.open[ticker].iloc[i], p.open[ticker].iloc[i + h]
    e0, e1 = p.open[etf].iloc[i], p.open[etf].iloc[i + h]
    if any(pd.isna(x) or x <= 0 for x in (s0, s1, e0, e1)):
        return None
    return float((s1 / s0 - 1) - (e1 / e0 - 1))


def reaction(p: Prices, ticker: str, etf: str, i: int) -> float | None:
    """Announcement-day return vs sector, known at the close of the entry day."""
    if i < 1 or ticker not in p.close.columns or etf not in p.close.columns:
        return None
    s0, s1 = p.close[ticker].iloc[i - 1], p.close[ticker].iloc[i]
    e0, e1 = p.close[etf].iloc[i - 1], p.close[etf].iloc[i]
    if any(pd.isna(x) or x <= 0 for x in (s0, s1, e0, e1)):
        return None
    return float((s1 / s0 - 1) - (e1 / e0 - 1))


def monthly_ic(df: pd.DataFrame, signal: str, outcome: str, month: str = "month", min_n: int = 20,
               n_boot: int = 5000, seed: int = 0) -> dict[str, float | int | None]:
    d = df.dropna(subset=[signal, outcome])
    ics = [float(g[signal].rank().corr(g[outcome].rank())) for _, g in d.groupby(month)
           if len(g) >= min_n and g[signal].nunique() > 1]
    if not ics:
        return {"months": 0, "events": len(d), "mean_ic": None, "ci_lo": None, "ci_hi": None}
    a = np.array(ics)
    rng = np.random.default_rng(seed)
    boots = a[rng.integers(0, len(a), size=(n_boot, len(a)))].mean(axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"months": len(a), "events": len(d), "mean_ic": round(float(a.mean()), 4),
            "ci_lo": round(float(lo), 4), "ci_hi": round(float(hi), 4)}


def quintile_spread(df: pd.DataFrame, signal: str, outcome: str, month: str = "month", n_boot: int = 5000,
                    seed: int = 0) -> dict[str, float | None]:
    """Mean excess return of the top fifth minus the bottom fifth of each month's events, averaged over months."""
    d = df.dropna(subset=[signal, outcome])
    spreads = []
    for _, g in d.groupby(month):
        if len(g) < 20:
            continue
        q = g[signal].rank(pct=True)
        spreads.append(float(g.loc[q > 0.8, outcome].mean() - g.loc[q <= 0.2, outcome].mean()))
    if not spreads:
        return {"spread_pct": None, "ci_lo": None, "ci_hi": None}
    a = np.array(spreads)
    rng = np.random.default_rng(seed)
    boots = a[rng.integers(0, len(a), size=(n_boot, len(a)))].mean(axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"spread_pct": round(100 * float(a.mean()), 3), "ci_lo": round(100 * float(lo), 3),
            "ci_hi": round(100 * float(hi), 3)}


# ---------------- reader checks: numbers the model extracted must be in the release ----------------

_WS = re.compile(r"\s+")
_NUM = re.compile(r"\(?-?\$?\d[\d,]*(?:\.\d+)?\)?")


def _norm(s: str) -> str:
    return _WS.sub(" ", s.replace("’", "'").replace("—", "-").replace("–", "-")).strip().lower()


def number_in_quote(value: float, quote: str, scaled: bool) -> bool:
    """True if the quote writes `value` (or, for amounts in millions, the same amount in billions or thousands)."""
    cands: list[float] = []
    for m in _NUM.findall(quote):
        neg = m.startswith("(") and m.endswith(")") or "-" in m
        x = float(m.strip("()$-").replace("$", "").replace(",", ""))
        cands.append(-x if neg else x)
    targets = [value, -value, abs(value)]
    if scaled:
        targets += [value / 1000, value * 1000]
    return any(abs(c - t) <= max(0.005, 0.0005 * abs(t)) for c in cands for t in targets)


def plausible(key: str, kept: dict[str, float]) -> tuple[dict[str, float], list[str]]:
    """Quote-checked numbers can still be the wrong number from the right sentence (a growth rate read as revenue):
    revenue must be positive and within 0.3x-3x of a year earlier; EPS within +-100 per share."""
    kept = dict(kept)
    bad: list[str] = []
    for part in list(kept):
        v = kept[part]
        if (key == "revenue" and v <= 0) or (key != "revenue" and abs(v) > 100):
            bad.append(f"{key}.{part}")
            del kept[part]
    if key == "revenue" and {"q", "prior"} <= set(kept) and not 0.3 < kept["q"] / kept["prior"] < 3:
        bad += [f"{key}.q", f"{key}.prior"]
        kept = {}
    return kept, bad


def check(raw: dict[str, Any] | None, text: str) -> dict[str, Any]:
    """Keep only numbers whose quote is really in the release and really contains them."""
    raw = raw or {}
    body = _norm(text)
    out: dict[str, Any] = {"parsed": bool(raw), "rejected": []}
    for key, scaled in (("revenue", True), ("eps", False), ("adj_eps", False)):
        got = raw.get(key)
        f: dict[str, Any] = got if isinstance(got, dict) else {}
        quote = str(f.get("quote") or "")
        in_text = bool(quote) and _norm(quote) in body
        kept: dict[str, float] = {}
        for part in ("q", "prior"):
            v = f.get(part)
            if isinstance(v, int | float) and not isinstance(v, bool):
                if in_text and number_in_quote(float(v), quote, scaled):
                    kept[part] = float(v)
                else:
                    out["rejected"].append(f"{key}.{part}")
        kept, bad = plausible(key, kept)
        out["rejected"] += bad
        out[key] = kept
    g = str(raw.get("guidance", "none")).lower()
    gq = str(raw.get("guidance_quote") or "")
    out["guidance"] = g if g in ("raised", "lowered", "maintained", "initiated", "withdrawn", "none") else "none"
    if out["guidance"] not in ("none",) and not (gq and _norm(gq) in body):
        out["rejected"].append("guidance")
        out["guidance"] = "unverified"
    t = str(raw.get("tone", "neutral")).lower()
    out["tone"] = t if t in ("positive", "neutral", "negative") else "neutral"
    got_hl = raw.get("highlights")
    hl: list[Any] = got_hl if isinstance(got_hl, list) else []
    out["highlights"] = [h for h in (str(x)[:300] for x in hl) if _norm(h) in body][:3]
    out["period_end"] = raw.get("period_end") if isinstance(raw.get("period_end"), str) else None
    return out
