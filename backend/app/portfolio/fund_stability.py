"""The autopilot's stability rules (docs/PLAN_60_V2.md, "Autopilot stability changes K1, N1, C1"): a beta hedge with a
tech-ETF basket (N1), fractional Kelly per horizon from the autopilot's own closed lots (K1), the combined fund view
(C1), and a rebalancing band so price ticks do not turn into orders."""
from __future__ import annotations

from typing import Any

import numpy as np

BASKET = ("QQQ", "QQQM", "XLK", "VGT")  # QQQ and QQQM track the same index; one asset is capped at 25% of equity
LEG_CAP = 0.24  # per hedge ETF, under the account risk check's 25%


def beta(stock: list[float], index: list[float], min_n: int = 60) -> float:
    """OLS beta of daily returns over the last 120 common closes (both lists end on the same day); 1.0 if too short."""
    n = min(len(stock), len(index), 121)
    if n - 1 < min_n:
        return 1.0
    s, i = np.diff(np.log(stock[-n:])), np.diff(np.log(index[-n:]))
    var = float(np.var(i))
    return float(np.cov(s, i, ddof=0)[0, 1] / var) if var > 0 else 1.0


def hedge(w: dict[str, float], betas: dict[str, float], gross: float,
          basket: tuple[str, ...] = BASKET) -> dict[str, float]:
    """Stock weights plus a basket short (or long) of minus their beta-weighted net, scaled together to fit `gross`
    and the per-leg cap."""
    h = -sum(x * betas.get(s, 1.0) for s, x in w.items())
    total = sum(abs(x) for x in w.values()) + abs(h)
    scale = min(1.0, gross / total) if total > 0 else 1.0
    if abs(h) * scale / len(basket) > LEG_CAP:
        scale = LEG_CAP * len(basket) / abs(h)
    out = {s: x * scale for s, x in w.items()}
    for e in basket:
        out[e] = out.get(e, 0.0) + h * scale / len(basket)
    return out


def hedged_return(lot: dict[str, Any], cost: float = 0.0) -> float | None:
    """A closed lot's return over its holding period minus beta x QQQ's return over the same dates, minus `cost`
    (a round trip; review #33: K1 sizes on net returns)."""
    need = ("entry_price", "exit_price", "entry_qqq", "exit_qqq")
    if any(not lot.get(k) for k in need):
        return None
    stock = lot["exit_price"] / lot["entry_price"] - 1
    market = lot["exit_qqq"] / lot["entry_qqq"] - 1
    return float(lot["side"] * (stock - lot.get("beta", 1.0) * market)) - cost


def kelly(lots: list[dict[str, Any]], base: dict[str, float], min_n: int = 100, frac: float = 0.5,
          cap: float = 2.0) -> dict[str, dict[str, Any]]:
    """Per horizon: multiplier 1 until `min_n` priced closed lots, then clip(frac x mean/variance / base, 0, cap)."""
    out: dict[str, dict[str, Any]] = {}
    for h, b in base.items():
        r = [x["hedged_return"] for x in lots if x["horizon"] == h and x.get("hedged_return") is not None]
        if len(r) < min_n:
            out[h] = {"mult": 1.0, "n": len(r), "mean": float(np.mean(r)) if r else None}
            continue
        mean, var = float(np.mean(r)), float(np.var(r, ddof=1))
        m = 0.0 if mean <= 0 or var <= 0 else min(cap, max(0.0, frac * mean / var / b))
        out[h] = {"mult": m, "n": len(r), "mean": mean}
    return out


def banded(orders: list[dict[str, Any]], prices: dict[str, float], equity: float,
           band: float = 0.005) -> list[dict[str, Any]]:
    """Drop resizes smaller than `band` of equity; new positions, exits and flips always go."""
    keep = []
    for o in orders:
        cur, tgt = o["from"], o["to"]
        if cur == 0 or tgt == 0 or cur * tgt < 0 or abs(tgt - cur) * prices.get(o["symbol"], 0.0) >= band * equity:
            keep.append(o)
    return keep


def combined(main: dict[str, Any], auto: dict[str, Any]) -> dict[str, Any]:
    """Two Alpaca daily portfolio histories ({timestamp, equity}) as one fund: the sum per day, the correlation of
    daily changes and the drawdown of the sum."""
    a = {t: v for t, v in zip(main.get("timestamp") or [], main.get("equity") or []) if v}
    b = {t: v for t, v in zip(auto.get("timestamp") or [], auto.get("equity") or []) if v}
    days = sorted(set(a) & set(b))
    total = [float(a[t]) + float(b[t]) for t in days]
    out: dict[str, Any] = {"timestamp": days, "main": [float(a[t]) for t in days], "auto": [float(b[t]) for t in days],
                           "total": total, "correlation": None, "drawdown": None}
    if len(days) >= 3:
        ra, rb = np.diff(out["main"]), np.diff(out["auto"])
        if np.std(ra) > 0 and np.std(rb) > 0:
            out["correlation"] = float(np.corrcoef(ra, rb)[0, 1])
    if total:
        peak = np.maximum.accumulate(total)
        out["drawdown"] = float(min(np.array(total) / peak - 1))
    return out


def theme_cap(w: dict[str, float], theme_of: dict[str, list[str]], per_name: float, net_cap: float = 0.30,
              gross_cap: float = 0.50) -> dict[str, float]:
    """R1: each theme's net at most `net_cap` and gross at most `gross_cap` of equity; the weight removed goes pro rata
    to the stocks in no capped theme (each under `per_name`), so the book's gross does not fall."""
    scale = dict.fromkeys(w, 1.0)
    for theme in {t for ts in theme_of.values() for t in ts}:
        names = [s for s in w if theme in theme_of.get(s, [])]
        net, gross = sum(w[s] for s in names), sum(abs(w[s]) for s in names)
        k = min(1.0, net_cap / abs(net) if net else 1.0, gross_cap / gross if gross else 1.0)
        for s in names:
            scale[s] = min(scale[s], k)
    out = {s: x * scale[s] for s, x in w.items()}
    freed = sum(abs(x) for x in w.values()) - sum(abs(x) for x in out.values())
    free = [s for s in out if scale[s] == 1.0 and s not in theme_of and out[s]]
    for _ in range(5):  # a name that hits per_name passes its share on to the others
        room = {s: per_name - abs(out[s]) for s in free if per_name - abs(out[s]) > 1e-12}
        base = sum(abs(out[s]) for s in room)
        if freed <= 1e-12 or base <= 0:
            break
        given = 0.0
        for s, r in room.items():
            add = min(r, freed * abs(out[s]) / base)
            out[s] += add if out[s] > 0 else -add
            given += add
        freed -= given
    return out
