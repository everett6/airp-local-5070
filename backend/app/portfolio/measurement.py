"""Descriptive execution costs and factor attribution. Does not select, fit, or activate trading rules."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def recorded_fills(backend: Path) -> list[dict[str, Any]]:
    """Existing broker fills also count as missing coverage if no arrival audit exists."""
    fwd = backend / "results/forward"
    picks_path, mirror_path = fwd / "ai_picks/book.json", fwd / "broker/orders.json"
    picks = json.loads(picks_path.read_text()) if picks_path.exists() else {}
    mirror = json.loads(mirror_path.read_text()) if mirror_path.exists() else {}
    legs = [leg for pair in picks.get("pairs", []) for leg in pair.get("legs", [])]
    legs += [leg for order in mirror.values() if isinstance(order, dict) for leg in order.get("legs", [])]
    out = {}
    for leg in legs:
        qty = leg.get("filled_qty") if leg.get("filled_qty") is not None else leg.get("qty") if leg.get("status") == "filled" else 0
        if leg.get("client_order_id") and float(qty or 0) > 0:
            out[leg["client_order_id"]] = {"client_order_id": leg["client_order_id"], "filled_qty": qty,
                                        "filled_price": leg.get("filled_price"), "filled_at": leg.get("filled_at")}
    return [out[k] for k in sorted(out)]


def cost_scope(backend: Path) -> str | None:
    audit = backend / "results/forward/execution/ledger.jsonl"
    if not audit.exists():
        return None
    legacy = recorded_fills(backend)
    body = audit.read_bytes() + (json.dumps(legacy, sort_keys=True).encode() if legacy else b"")
    return hashlib.sha256(body).hexdigest()


def preserve_closes(path: Path, closes: pd.DataFrame, now: datetime) -> None:
    """Retain already-fetched completed bars for attribution; no requests or changes to the trading matrix."""
    panel = closes.copy()
    panel.index = pd.to_datetime(panel.index).tz_localize(None).normalize()
    panel = panel[panel.index.date < now.date()]
    if path.exists():
        panel = panel.combine_first(pd.read_parquet(path))
    tmp = path.with_name(path.name + ".tmp")
    panel.sort_index().to_parquet(tmp)
    tmp.replace(path)


def execution(rows: list[dict[str, Any]], costs: dict[str, float] | None = None,
              legacy_fills: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    arrivals, submissions, fills = {}, {}, {}
    blocked: list[dict[str, Any]] = []
    for r in rows:
        cid = r.get("client_order_id")
        if r.get("type") == "risk":
            if r.get("allowed"):
                arrivals[cid] = r
            else:
                blocked.append({"id": cid, "reasons": r.get("reasons", [])})
        elif r.get("type") == "submission":
            submissions[cid] = r
        elif r.get("type") == "fill_snapshot":
            fills[cid] = r  # cumulative quantities: the last snapshot replaces earlier ones
    trades: list[dict[str, Any]] = []
    for fill in legacy_fills or []:
        fills.setdefault(fill["client_order_id"], fill)
    for cid, f in fills.items():
        a = arrivals.get(cid)
        qty, price = float(f.get("filled_qty") or 0), float(f.get("filled_price") or 0)
        if not a or qty <= 0 or price <= 0:
            continue
        sign = 1 if a["side"] == "buy" else -1
        slip = sign * (price / a["mid"] - 1) * 10000
        trades.append({"id": cid, "symbol": a["symbol"], "qty": qty, "notional": qty * price,
                       "arrival_shortfall_dollars": sign * qty * (price - a["mid"]),
                       "slippage_bp": slip, "spread_bp": a["spread_bp"], "feed": a.get("feed"),
                       "benchmark_at": a["quote_at"], "quote_age_s": a["quote_age_s"],
                       "latency_ms": submissions.get(cid, {}).get("latency_ms"), "filled_at": f.get("filled_at")})
    total = sum(t["notional"] for t in trades)
    observed_costs = None
    if costs is not None:
        if set(costs) != {"fees", "borrow", "financing"} or any(
                isinstance(v, bool) or not math.isfinite(v) or v < 0 for v in costs.values()):
            raise ValueError("Supply finite nonnegative period dollars for fees, borrow and financing")
        observed_costs = {**costs, "arrival_shortfall": sum(t["arrival_shortfall_dollars"] for t in trades)}
        observed_costs["total"] = sum(observed_costs.values())
    missing = [{"id": cid, "reason": "missing arrival quote" if cid not in arrivals else "invalid fill price",
                "filled_qty": f.get("filled_qty"), "filled_price": f.get("filled_price")}
               for cid, f in fills.items() if float(f.get("filled_qty") or 0) > 0 and cid not in {t["id"] for t in trades}]
    return {"orders_submitted": len(submissions), "fills_measured": len(trades), "blocked": blocked[-30:],
            "notional": total, "weighted_slippage_bp": sum(t["notional"] * t["slippage_bp"] for t in trades) / total if total else None,
            "unmeasured_fill_snapshots": len(missing), "unmeasured_fills": missing, "trades": trades[-100:],
            "cost_status": "reviewed_with_arrival_coverage" if costs is not None and trades and not missing else "incomplete",
            "period_cost_dollars": observed_costs,
            "stress": [{"extra_cost_bp_per_side": bp, "extra_dollars_on_measured_turnover": total * bp / 10000}
                       for bp in (5, 10, 25)],
            "cost_inputs": {"fees": "reviewed" if costs is not None else "unobserved", "borrow": "reviewed" if costs is not None else "unobserved", "financing": "reviewed" if costs is not None else "unobserved",
                            "impact": "stress only; paper fills do not measure market impact"},
            "note": "IEX is a single exchange, not a consolidated quote. Queued orders use an earlier closing quote. Fees, borrow and financing must be supplied before claiming total net execution cost."}


def factors(returns: pd.Series, factor_returns: pd.DataFrame, min_rows: int = 120,
            lag: int = 5) -> dict[str, Any]:
    """OLS with Newey-West covariance (Bartlett weights); dates joined without forward filling."""
    if lag < 0 or min_rows < 1 or factor_returns.columns.has_duplicates or "portfolio" in factor_returns.columns:
        raise ValueError("Invalid factor specification")
    if returns.index.has_duplicates or factor_returns.index.has_duplicates:
        raise ValueError("Duplicate return dates")
    data = pd.concat([returns.rename("portfolio"), factor_returns], axis=1, sort=False).sort_index().dropna()
    data = data.replace([np.inf, -np.inf], np.nan).dropna()
    n, k = len(data), len(factor_returns.columns) + 1
    base: dict[str, Any] = {"observations": n, "required": min_rows, "factors": list(factor_returns.columns),
                           "interpretation": "Descriptive factor exposure; an intercept is not proof of a tradable AI edge."}
    if n < max(min_rows, 2 * k):
        return base | {"status": "insufficient_data"}
    x = np.column_stack([np.ones(n), data[factor_returns.columns].to_numpy(float)])
    y = data["portfolio"].to_numpy(float)
    if np.linalg.matrix_rank(x) < k:
        return base | {"status": "collinear_factors"}
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    residual = y - x @ beta
    scores = x * residual[:, None]
    meat = scores.T @ scores
    used_lag = min(lag, n - 1)
    for j in range(1, used_lag + 1):
        cross = scores[j:].T @ scores[:-j]
        meat += (1 - j / (used_lag + 1)) * (cross + cross.T)
    bread = np.linalg.inv(x.T @ x)
    covariance = bread @ meat @ bread * n / (n - k)
    se = math.sqrt(max(0.0, float(covariance[0, 0])))
    variance = float(np.sum((y - y.mean()) ** 2))
    return base | {"status": "estimated", "intercept_per_period": float(beta[0]),
                   "intercept_95": [float(beta[0] - 1.96 * se), float(beta[0] + 1.96 * se)],
                   "loadings": dict(zip(factor_returns.columns, map(float, beta[1:]), strict=True)),
                   "r_squared": 1 - float(residual @ residual) / variance if variance else None,
                   "hac_lag": used_lag}
