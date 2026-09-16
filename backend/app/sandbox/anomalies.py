"""
Classical cross-sectional return predictors, computed point-in-time from a `PointInTimeView`.

Definitions follow the published anomalies re-derived by Open Source Asset Pricing (Chen & Zimmermann, 2022,
github.com/OpenSourceAP/CrossSection); the code here is written from the definitions, not copied:

  mom_12_1   return from 12 months ago to 1 month ago (Jegadeesh & Titman 1993), skipping the last month
  rev_1m     minus the last month's return (short-term reversal, Jegadeesh 1990)
  hi_52w     price / highest close of the last 52 weeks (George & Hwang 2004)
  ivol_60d   std of daily residuals after regressing on the market, last 60 days (Ang et al. 2006; low is good)

`anomaly_rank` averages each stock's within-week percentile ranks with the published signs, plus the SEC
earnings-surprise composite when available. Signs, weights, and the probability mapping are fixed here, before
any v7 run, and never fitted.

Point-in-time: every input is a slice of the view (rows <= cutoff); `tests/test_anomalies.py` checks that
changing prices after the cutoff cannot change any value.
"""
from __future__ import annotations

import math

from app.sandbox.pit_data import PointInTimeView

MONTH, YEAR = 21, 252
ANOMALY_KEYS = ["mom_12_1", "rev_1m", "hi_52w", "ivol_60d"]
# +1: higher value predicts higher return; -1: lower value predicts higher return
SIGNS = {"mom_12_1": 1.0, "rev_1m": 1.0, "hi_52w": 1.0, "ivol_60d": -1.0, "sue": 1.0}


def features(view: PointInTimeView, ticker: str, market: str) -> dict[str, float]:
    c = view.closes[ticker]
    m = view.closes[market]
    n = len(c)
    start = max(0, n - 1 - YEAR)  # short histories use what exists (recent listings)
    mom = c[-1 - MONTH] / c[start] - 1.0 if n > MONTH + 1 and n - 1 - MONTH > start else 0.0
    rev = -(c[-1] / c[-1 - MONTH] - 1.0) if n > MONTH else 0.0
    hi = c[-1] / max(c[-YEAR:])
    k = min(60, n - 1)
    if k >= 20:
        ra = [c[i] / c[i - 1] - 1.0 for i in range(n - k, n)]
        rm = [m[i] / m[i - 1] - 1.0 for i in range(n - k, n)]
        ma, mm = sum(ra) / k, sum(rm) / k
        var_m = sum((x - mm) ** 2 for x in rm)
        beta = sum((x - mm) * (y - ma) for x, y in zip(rm, ra, strict=True)) / var_m if var_m > 0 else 0.0
        res = [y - ma - beta * (x - mm) for x, y in zip(rm, ra, strict=True)]
        ivol = math.sqrt(sum(e * e for e in res) / (k - 2)) * math.sqrt(252)
    else:
        ivol = 0.0
    return {"mom_12_1": mom, "rev_1m": rev, "hi_52w": hi, "ivol_60d": ivol}


def sue_composite(fund: dict[str, float]) -> float | None:
    """Same recency weights as `walkforward.sue_rule`; None when the company has no usable filings yet."""
    if not fund.get("fund_ok"):
        return None
    return 0.4 * fund["sue_1"] + 0.3 * fund["sue_2"] + 0.2 * fund["sue_3"] + 0.1 * fund["sue_4"]


def _pct_ranks(values: dict[str, float]) -> dict[str, float]:
    """Percentile ranks in [0, 1] with average ranks for ties."""
    items = sorted(values.items(), key=lambda kv: kv[1])
    n = len(items)
    out: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        r = (i + j) / 2 / max(1, n - 1)
        for k in range(i, j + 1):
            out[items[k][0]] = r
        i = j + 1
    return out


def anomaly_scores(feats: dict[str, dict[str, float]], sue: dict[str, float | None]) -> dict[str, float]:
    """Mean signed percentile rank per stock, in [0, 1] (0.5 = middle). Stocks without SUE get 0.5 for it."""
    ids = list(feats)
    total = dict.fromkeys(ids, 0.0)
    for key in ANOMALY_KEYS:
        ranks = _pct_ranks({k: SIGNS[key] * feats[k][key] for k in ids})
        for k in ids:
            total[k] += ranks[k]
    have = {k: v for k, v in sue.items() if v is not None and k in total}
    sue_ranks = _pct_ranks(have) if len(have) >= 2 else {}
    for k in ids:
        total[k] += sue_ranks.get(k, 0.5)
    return {k: v / (len(ANOMALY_KEYS) + 1) for k, v in total.items()}


def anomaly_probability(score: float) -> float:
    """Fixed mapping of the composite rank to P(up): 0.45 at the bottom, 0.55 at the top."""
    return 0.45 + 0.10 * min(max(score, 0.0), 1.0)
