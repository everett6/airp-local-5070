"""
Market data / options chain connector. Ships with a deterministic mock
adapter (`MockMarketDataConnector`) so the whole pipeline runs without a paid
data license; swap in `PolygonMarketDataConnector` / `IEXMarketDataConnector`
etc. behind the same `DataConnector` interface for production.

Both mock connectors are sandbox-aware: they compute prices from the
ticker + an explicit `as_of` date via `sandbox.synthetic_market`, and check
`sandbox.clock.enforce_point_in_time` before returning anything. Ask this
connector for a date beyond an active sandbox's cutoff and it raises
`LookaheadViolation` instead of quietly answering — that's what makes the
backtest harness's "can't see the future" guarantee real rather than a
documentation promise. See app/sandbox/clock.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.data_ingestion.base_connector import DataConnector, ValidationIssue
from app.sandbox.clock import current_effective_time, enforce_point_in_time
from app.sandbox.synthetic_market import deterministic_price


@dataclass(frozen=True)
class Bar:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class Quote:
    ticker: str
    price: float
    bid: float
    ask: float
    volume: int
    as_of: str


@dataclass(frozen=True)
class OptionContract:
    ticker: str
    strike: float
    expiry: str
    option_type: str  # "call" | "put"
    bid: float
    ask: float
    implied_volatility: float
    open_interest: int
    volume: int
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None


def _resolve_requested_time(kwargs: dict[str, Any]) -> datetime:
    """A caller may ask for a specific date (`as_of=...`) — e.g. a backtest
    grading step asking "what was the price on 2026-11-01" — or omit it, in
    which case we use whatever time is currently in effect (the sandbox
    cutoff if one is active, real now otherwise). Either way this is the
    ONLY place "what date are we asking about" gets decided, so it's the
    only place that needs to enforce the point-in-time rule."""
    requested = kwargs.get("as_of")
    if requested is None:
        return current_effective_time()
    if isinstance(requested, str):
        return datetime.fromisoformat(requested)
    if isinstance(requested, datetime):
        return requested
    raise TypeError(f"as_of must be a datetime or ISO string, got {type(requested).__name__}")


class MarketDataConnector(DataConnector[Quote]):
    name = "market_data"
    default_ttl_seconds = 15  # quotes are hot; short TTL

    def _source_id(self, **kwargs: Any) -> str:
        return f"quote:{kwargs['ticker']}:{kwargs.get('as_of', 'live')}"

    def _validate(self, data: Quote) -> list[ValidationIssue]:
        issues = []
        if data.bid > data.ask:
            issues.append(ValidationIssue("bid_ask", "error", "bid greater than ask"))
        if data.price <= 0:
            issues.append(ValidationIssue("price", "error", "non-positive price"))
        return issues

    def _normalize(self, raw: dict[str, Any]) -> Quote:
        return Quote(
            ticker=raw["ticker"], price=float(raw["price"]), bid=float(raw["bid"]),
            ask=float(raw["ask"]), volume=int(raw["volume"]), as_of=raw["as_of"],
        )

    async def _fetch_raw(self, **kwargs: Any) -> dict[str, Any]:  # pragma: no cover - override in prod subclass
        raise NotImplementedError("Wire a real provider here (Polygon/IEX/etc).")


class MockMarketDataConnector(MarketDataConnector):
    """Deterministic synthetic quotes for local dev/tests and for exercising
    the sandbox/backtest mechanism — never used to justify a real trade
    proposal (the report template refuses to render a 'live' recommendation
    section if any MARKET_DATA evidence came from a connector name starting
    with 'mock')."""

    name = "mock_market_data"

    async def _fetch_raw(self, **kwargs: Any) -> dict[str, Any]:
        ticker = kwargs["ticker"]
        requested_time = _resolve_requested_time(kwargs)
        enforce_point_in_time(requested_time, source=f"{self.name}:{ticker}")

        price = deterministic_price(ticker, requested_time)
        return {
            "ticker": ticker, "price": price, "bid": round(price - 0.05, 2),
            "ask": round(price + 0.05, 2), "volume": 1_000_000,
            "as_of": requested_time.isoformat(),
        }


class OptionsChainConnector(DataConnector[list[OptionContract]]):
    name = "mock_options_chain"
    default_ttl_seconds = 30

    def _source_id(self, **kwargs: Any) -> str:
        return f"chain:{kwargs['ticker']}:{kwargs.get('expiry', 'all')}:{kwargs.get('as_of', 'live')}"

    def _validate(self, data: list[OptionContract]) -> list[ValidationIssue]:
        issues = []
        for c in data:
            if c.bid > c.ask:
                issues.append(ValidationIssue(f"{c.strike}_{c.option_type}", "error", "bid > ask"))
            if c.implied_volatility <= 0 or c.implied_volatility > 5:
                issues.append(
                    ValidationIssue(f"{c.strike}_{c.option_type}", "warning", "IV out of sane range")
                )
        return issues

    def _normalize(self, raw: list[dict[str, Any]]) -> list[OptionContract]:
        return [OptionContract(**r) for r in raw]

    async def _fetch_raw(self, **kwargs: Any) -> list[dict[str, Any]]:
        ticker = kwargs["ticker"]
        requested_time = _resolve_requested_time(kwargs)
        enforce_point_in_time(requested_time, source=f"{self.name}:{ticker}")

        spot = deterministic_price(ticker, requested_time)
        strikes = [spot * m for m in (0.9, 0.95, 1.0, 1.05, 1.1)]
        out = []
        for k in strikes:
            for opt_type in ("call", "put"):
                out.append({
                    "ticker": ticker, "strike": round(k, 2),
                    "expiry": kwargs.get("expiry", "2026-12-18"),
                    "option_type": opt_type, "bid": 1.0, "ask": 1.1,
                    "implied_volatility": 0.3, "open_interest": 500, "volume": 50,
                })
        return out
