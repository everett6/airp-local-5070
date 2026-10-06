"""Account-wide pre-submission controls, independent of trading signals. Pure snapshot evaluation."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RiskPolicy:
    version: int = 1
    max_gross: float = 2.0
    max_asset: float = 1.8
    max_crypto: float = 0.45
    daily_loss: float = 0.05
    max_spread_bp: float = 100.0
    max_reference_gap: float = 0.10
    max_quote_age_s: float = 180.0

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version < 1:
            raise ValueError("Invalid risk policy version")
        for name in ("max_gross", "max_asset", "max_crypto", "daily_loss", "max_spread_bp",
                     "max_reference_gap", "max_quote_age_s"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"Invalid account risk policy: {name}")
        if self.daily_loss >= 1 or self.max_crypto > self.max_gross or self.max_asset > self.max_gross:
            raise ValueError("Inconsistent account risk limits")


def number(value: Any) -> float:
    if isinstance(value, bool):
        raise TypeError("Boolean is not an account quantity")
    out = float(value)
    if not math.isfinite(out):
        raise ValueError("Non-finite account quantity")
    return out


def evaluate(policy: RiskPolicy, account: dict[str, Any], positions: list[dict[str, Any]],
             orders: list[dict[str, Any]], quotes: dict[str, dict[str, Any]], symbol: str,
             side: str, qty: float, ref_price: float, now: datetime, is_crypto: bool,
             quote_floor: datetime) -> dict[str, Any]:
    """Worst gross over independent fills: do not net pending buys against pending sells."""
    errors: list[str] = []
    symbol = symbol.replace("/", "")
    positions = [{**p, "symbol": p["symbol"].replace("/", "")} for p in positions]
    orders = [{**o, "symbol": o["symbol"].replace("/", "")} for o in orders]
    quotes = {a.replace("/", ""): q for a, q in quotes.items()}
    if side not in ("buy", "sell") or number(qty) <= 0 or number(ref_price) <= 0:
        raise ValueError("Invalid order intent")
    equity = number(account["equity"])
    if equity <= 0:
        raise ValueError("Account equity must be positive")
    if account.get("status") != "ACTIVE" or account.get("trading_blocked") or account.get("account_blocked"):
        errors.append("account blocked")
    units = {p["symbol"]: number(p["qty"]) for p in positions}
    prices: dict[str, float] = {}
    for p in positions:
        q = number(p["qty"])
        px = number(p.get("current_price") or (abs(number(p["market_value"])) / abs(q) if q else 0))
        if q and px <= 0:
            raise ValueError("Position has no valid valuation")
        prices[p["symbol"]] = px
    buys: dict[str, float] = {}
    sells: dict[str, float] = {}
    for order in orders:
        remaining = max(0.0, number(order["qty"]) - number(order.get("filled_qty") or 0))
        if order.get("side") not in ("buy", "sell"):
            raise ValueError("Open order has no side")
        dest = buys if order["side"] == "buy" else sells
        dest[order["symbol"]] = dest.get(order["symbol"], 0.0) + remaining
    candidate_quote = quotes[symbol]
    bid, ask = number(candidate_quote["bp"]), number(candidate_quote["ap"])
    stamp = datetime.fromisoformat(str(candidate_quote["t"]))
    if stamp.tzinfo is None or bid <= 0 or ask < bid:
        raise ValueError("Invalid quote")
    mid = (bid + ask) / 2
    age = (now - stamp).total_seconds()
    if age < -5 or stamp < quote_floor:
        errors.append("stale or future quote")
    spread = (ask - bid) / mid * 10000
    if spread > policy.max_spread_bp:
        errors.append("spread exceeds limit")
    if abs(mid / ref_price - 1) > policy.max_reference_gap:
        errors.append("arrival price differs excessively from reference")
    prices[symbol] = mid
    assets = set(units) | set(buys) | set(sells) | {symbol}
    for asset in assets:
        if asset not in prices:
            quote = quotes[asset]
            prices[asset] = (number(quote["bp"]) + number(quote["ap"])) / 2
        if prices[asset] <= 0:
            raise ValueError("Pending order has no valid valuation")

    def worst(asset: str) -> float:
        u = units.get(asset, 0.0)
        return max(abs(u), abs(u + buys.get(asset, 0.0)), abs(u - sells.get(asset, 0.0))) * prices[asset]

    before = {a: worst(a) for a in assets}
    signed = qty if side == "buy" else -qty
    u = units.get(symbol, 0.0)
    reducing = (u * signed < 0 and abs(signed) <= abs(u)
                and (sells.get(symbol, 0) if side == "sell" else buys.get(symbol, 0)) + qty <= abs(u))
    dest = buys if side == "buy" else sells
    dest[symbol] = dest.get(symbol, 0.0) + qty
    after = {a: worst(a) for a in assets}
    # A close cannot reduce the pre-fill worst case (the order might not fill), but cannot increase it either.
    reducing = reducing and sum(after.values()) <= sum(before.values()) + 1e-6
    gross = sum(after.values()) / equity
    crypto = sum(v for a, v in after.items() if a.replace("/", "") in ("BTCUSD", "ETHUSD")) / equity
    last = number(account["last_equity"])
    if last <= 0:
        raise ValueError("Missing prior account equity")
    daily_loss = max(0.0, 1 - equity / last)
    if not reducing:
        if gross > policy.max_gross + 1e-9:
            errors.append("account gross exceeds limit including pending orders")
        if after[symbol] / equity > policy.max_asset + 1e-9:
            errors.append("asset concentration exceeds limit")
        if crypto > policy.max_crypto + 1e-9:
            errors.append("crypto concentration exceeds limit")
        if daily_loss >= policy.daily_loss:
            errors.append("daily account loss limit reached")
        power = number(account["non_marginable_buying_power"] if is_crypto else account["buying_power"])
        if qty * ask > power:
            errors.append("insufficient available buying power")
    return {"allowed": not errors, "reasons": errors, "reducing": reducing, "gross": gross,
            "crypto": crypto, "daily_loss": daily_loss, "bid": bid, "ask": ask, "mid": mid,
            "quote_at": stamp.isoformat(), "quote_age_s": age, "spread_bp": spread,
            "policy_version": policy.version}
