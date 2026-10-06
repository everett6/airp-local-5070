"""HS1 locked prospective vintages. Price proxies, no broker orders or promotion."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

HOLD = {"day": 0, "medium": 21, "long": 63}
CAPS = {"day": 0.1, "medium": 0.3, "long": 0.5}


def validate(weights: dict[str, dict[str, float]]) -> None:
    if set(weights) != set(HOLD):
        raise ValueError("Invalid horizon plan")
    for h, values in weights.items():
        if any(isinstance(w, bool) or not math.isfinite(w) or w < 0 for w in values.values()):
            raise ValueError("Invalid virtual weight")
        if sum(values.values()) > CAPS[h] + 1e-9:
            raise ValueError("Horizon cap exceeded")
    for asset in {a for values in weights.values() for a in values}:
        if sum(values.get(asset, 0) for values in weights.values()) > 0.1 + 1e-9:
            raise ValueError("Company cap exceeded")


def evaluate(plan: dict[str, Any], sessions: list[dict[str, Any]], bars: dict[str, dict[str, dict[str, float]]],
             now: datetime) -> dict[str, Any]:
    decided = datetime.fromisoformat(plan["decided_at"])
    if decided.tzinfo is None or now.tzinfo is None or plan.get("protocol") != "HS1":
        raise ValueError("HS1 requires timezone-aware decision and observation times")
    for weights in plan["arms"].values():
        validate(weights)
    dates = [s["date"] for s in sessions]
    if dates != sorted(set(dates)):
        raise ValueError("Duplicate or unsorted exchange sessions")
    times = [(datetime.fromisoformat(s["open_at"]), datetime.fromisoformat(s["close_at"])) for s in sessions]
    if any(o.tzinfo is None or c.tzinfo is None or o >= c for o, c in times):
        raise ValueError("Invalid exchange session timestamps")
    entry = next((i for i, (o, _) in enumerate(times) if o > decided), None)
    outcomes: dict[str, Any] = {}
    for horizon, hold in HOLD.items():
        selected = {a for arm in plan["arms"].values() for a, w in arm[horizon].items() if w > 0}
        base: dict[str, Any] = {"status": "pending", "net_return": None, "incremental_net_return": None,
                                "uncertainty": None, "missing": [], "arms": {}}
        outcomes[horizon] = base
        if entry is None or entry + hold >= len(sessions):
            continue
        o, c = times[entry]
        exit_at = c if horizon == "day" else times[entry + hold][0]
        base.update(entry_at=o.isoformat(), exit_at=exit_at.isoformat())
        if horizon == "day" and (c - o).total_seconds() < 6 * 3600:
            base["status"] = "excluded_half_day"
            continue
        if o > now or exit_at > now:
            continue
        ratios = {}
        for asset in selected:
            start = bars.get(asset, {}).get(dates[entry], {}).get("open")
            end = bars.get(asset, {}).get(dates[entry + hold], {}).get("close" if horizon == "day" else "open")
            if start is None or end is None or not all(math.isfinite(v) and v > 0 for v in (start, end)):
                base["missing"].append(asset)
            else:
                ratios[asset] = end / start
        if base["missing"]:
            base["status"] = "missing_prices"
            continue  # identical coverage: never score the easier arm alone
        base["status"] = "evaluated_price_proxy"
        for name, weights in plan["arms"].items():
            w = weights[horizon]
            returns = {str(bp): sum(weight * (ratios[a] * (1 - bp / 10000) - (1 + bp / 10000))
                                   for a, weight in w.items() if weight > 0) for bp in (10, 20)}
            base["arms"][name] = {"net_return": returns["10"], "stress_net_return": returns["20"],
                                   "turnover": sum(weight * (1 + ratios[a]) for a, weight in w.items() if weight > 0),
                                   "gross_planned_exposure": sum(w.values()), "weights": w}
        base["incremental_net_return"] = base["arms"]["ai"]["net_return"] - base["arms"]["no_ai"]["net_return"]
    return {"protocol": "HS1", "decided_at": decided.isoformat(), "observed_at": now.isoformat(), "horizons": outcomes,
            "winner": None, "orders_submitted": 0,
            "basis": "IEX daily price proxies with assumed 10/20 bp per side. Vintages are separate; no account-wide return or statistical qualification."}
