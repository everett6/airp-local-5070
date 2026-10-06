"""Day-trading feasibility screen (user request, 4 Oct 2026): which stocks can be day-traded CHEAPLY.

This is not a trading rule and makes no return forecast. Every tested intraday rule (box theory, Darvas box,
opening-range breakout, VWAP, momentum, reversals) failed after costs on this universe (docs/PLAN_60_V2.md); they are
not re-run per stock, because picking the stocks where a failed rule happened to work is curve-fitting.
The screen only asks whether a typical day's move is large next to the cost of a round trip, and whether the stock
is liquid enough to trade size without moving it. The AI's day calls are traded only on stocks that pass.

Per stock: median dollar volume (60 sessions), median intraday range (high-low over open, 20 sessions), 14-day ATR
as a share of price, median opening gap, and the quoted spread. Round-trip cost = spread + 2 x slippage.
Feasible: dollar volume >= $50M, spread <= 15 bp, and median range >= 8 round-trip costs.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any


@dataclass(frozen=True)
class Rules:
    min_dollar_volume: float = 50e6
    max_spread_bp: float = 15.0
    min_range_to_cost: float = 8.0
    slippage_bp: float = 2.0  # per side


def metrics(bars: list[dict[str, float]], spread_bp: float | None, dollar_volume: float | None) -> dict[str, Any]:
    """bars: daily o/h/l/c, oldest first (at least 21)."""
    if len(bars) < 21:
        return {"error": "not enough daily bars"}
    last = bars[-20:]
    rng = median((b["h"] - b["l"]) / b["o"] for b in last if b["o"] > 0) * 1e4
    gaps = median(abs(b["o"] / p["c"] - 1) for p, b in zip(bars[-21:-1], last, strict=True) if p["c"] > 0) * 1e4
    trs = [max(b["h"] - b["l"], abs(b["h"] - p["c"]), abs(b["l"] - p["c"])) for p, b in zip(bars[-15:-1], bars[-14:], strict=True)]
    atr = sum(trs) / len(trs) / bars[-1]["c"] * 1e4
    return {"range_bp": round(rng, 1), "gap_bp": round(gaps, 1), "atr_bp": round(atr, 1),
            "spread_bp": None if spread_bp is None else round(spread_bp, 2),
            "dollar_volume": None if dollar_volume is None else round(dollar_volume)}


RULES = Rules()


def verdict(m: dict[str, Any], rules: Rules = RULES) -> dict[str, Any]:
    if m.get("error"):
        return {**m, "feasible": False, "why": m["error"]}
    spread = m["spread_bp"] if m["spread_bp"] is not None else rules.max_spread_bp  # unknown: assume the limit
    cost = spread + 2 * rules.slippage_bp
    ratio = m["range_bp"] / cost if cost > 0 else 0.0
    why = []
    if m["dollar_volume"] is None or m["dollar_volume"] < rules.min_dollar_volume:
        why.append("too little volume")
    if m["spread_bp"] is None or m["spread_bp"] > rules.max_spread_bp:
        why.append("spread too wide or unknown")
    if ratio < rules.min_range_to_cost:
        why.append(f"daily range only {ratio:.1f}x the round-trip cost")
    return {**m, "cost_bp": round(cost, 2), "range_to_cost": round(ratio, 1), "feasible": not why,
            "why": "; ".join(why) or "liquid, tight spread, range well above cost"}
