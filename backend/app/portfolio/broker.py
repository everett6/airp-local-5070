"""Paper broker mirror (docs/DEV_PLAN_AUTONOMOUS.md Phase A): the forward book's decisions are also sent to an
Alpaca PAPER account, and each broker fill is recorded next to the simulator's fill for the same decision.

The simulator stays the book of record. The broker adds real order handling (auctions, rejections, market hours)
without real money. Rules:
- paper only: the base URL is fixed to paper-api.alpaca.markets and anything else is refused, so a live-account key
  can never trade here;
- keys come only from backend/.env (ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY), which the user fills in; without
  them every call is skipped;
- one plan per decision (`decided_at`), sized from the broker account's own equity; each leg has a client order id
  derived from the decision, so a re-run never sends an order twice;
- stock legs go as market "day" orders sent before the open (19:00-09:28 ET), so they fill at the open the simulator
  uses. They were "opg" (market-on-open) until 30 Sep 2026, but Alpaca's paper simulator never runs a real opening
  auction and let both opg legs of the first AI pick expire unfilled; a queued day order fills at the open instead.
  A leg decided at 18:00 still waits for the next run inside the window.
  Crypto trades around the clock and goes at once as a market order;
- HALTED: nothing is sent and open orders are cancelled; REDUCING: legs that would buy are dropped;
- a gap over 0.5% between the broker's and the simulator's fill price, or a rejected / expired order, is an alert.
The planning and reconciling functions take plain data, so they are tested without the network.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.data_ingestion.bars import key

PAPER = "https://paper-api.alpaca.markets/v2"
DATA = "https://data.alpaca.markets"
NY = ZoneInfo("America/New_York")
CRYPTO = {"BTC-USD": "BTC/USD", "ETH-USD": "ETH/USD"}
STOCKS = ("SPY", "SGOV", "QQQ", "TLT")  # the book's stock assets: sold when a decision drops them
GAP_ALERT = 0.005
MIN_NOTIONAL = 10.0  # smaller legs are skipped (Alpaca's crypto minimum is about $1; a $10 floor avoids dust)
OPG_CLOSED = (time(9, 28), time(19, 0))  # stock legs are sent only outside [09:28, 19:00) ET, i.e. before the open
STOCK_TIF = "day"  # queued market order that fills at the open; paper "opg" orders expire (see above)


class BrokerError(RuntimeError):
    pass


@dataclass
class Leg:
    asset: str
    symbol: str
    side: str
    qty: float
    tif: str               # STOCK_TIF (stock, at the open) or "gtc" (crypto, now)
    client_order_id: str
    ref_price: float
    status: str = "planned"  # planned → submitted → filled | rejected | canceled | expired | skipped
    order_id: str | None = None
    filled_price: float | None = None
    filled_at: str | None = None
    sim_price: float | None = None
    gap: float | None = None
    note: str = ""
    alerted: bool = False


def symbol(asset: str) -> str:
    return CRYPTO.get(asset, asset)


def position_asset(sym: str) -> str:
    """Alpaca's position symbol ('BTCUSD', 'SPY') back to the book's asset name."""
    for a, s in CRYPTO.items():
        if sym.replace("/", "") == s.replace("/", ""):
            return a
    return sym


def client_id(decided_at: str, asset: str) -> str:
    return "airp-" + re.sub(r"[^0-9A-Za-z]", "", decided_at)[:14] + "-" + re.sub(r"[^0-9A-Za-z]", "", asset)


def opg_open(now: datetime) -> bool:
    """True when a stock leg sent now would wait for (and fill at) the next open."""
    t = now.astimezone(NY).time()
    return not OPG_CLOSED[0] <= t < OPG_CLOSED[1]


def plan(decided_at: str, targets: dict[str, float], equity: float, positions: dict[str, float],
         prices: dict[str, float], reduce_only: bool = False) -> list[Leg]:
    """Legs that move the broker account from `positions` to `targets` (weights of `equity`): sells first, whole
    shares for stocks, 6 decimals for crypto, legs under $10 skipped. Assets held but absent from the targets are
    sold (the book's universe only; anything else in the account is left alone)."""
    legs = []
    for a in sorted(set(targets) | {x for x in positions if x in targets or x in CRYPTO or x in STOCKS}):
        if a not in prices:
            raise BrokerError(f"no price for {a}")
        want = equity * targets.get(a, 0.0) / prices[a]
        want = round(want, 6) if a in CRYPTO else float(math.floor(want))
        delta = want - positions.get(a, 0.0)
        if a in CRYPTO:
            delta = math.floor(abs(delta) * 1e6) / 1e6 * (1 if delta > 0 else -1)
        if abs(delta) * prices[a] < MIN_NOTIONAL or (reduce_only and delta > 0):
            continue
        legs.append(Leg(a, symbol(a), "buy" if delta > 0 else "sell", abs(delta), "gtc" if a in CRYPTO else STOCK_TIF,
                        client_id(decided_at, a), prices[a]))
    return sorted(legs, key=lambda x: x.side != "sell")


def reconcile(leg: Leg, sim_fills: dict[str, float]) -> list[str]:
    """Fill in the simulator's price for this leg's asset and return the alerts it raises."""
    alerts: list[str] = []
    if leg.alerted:
        return alerts
    if leg.status in ("rejected", "canceled", "expired"):
        alerts.append(f"broker order {leg.client_order_id} {leg.status} {leg.note}".strip())
    if leg.status == "filled" and leg.asset in sim_fills and leg.filled_price and leg.gap is None:
        leg.sim_price = sim_fills[leg.asset]
        leg.gap = round(leg.filled_price / leg.sim_price - 1, 6)
        if abs(leg.gap) > GAP_ALERT:
            alerts.append(f"broker fill {leg.asset} {leg.filled_price} vs simulator {leg.sim_price} "
                          f"(gap {100 * leg.gap:+.2f}%)")
    leg.alerted = bool(alerts)
    return alerts


def leg_from(d: dict[str, Any]) -> Leg:
    return Leg(**{k: v for k, v in d.items() if k in Leg.__dataclass_fields__})


def leg_dict(leg: Leg) -> dict[str, Any]:
    return asdict(leg)


class Alpaca:
    """The few paper-trading calls the mirror needs. Never logs or returns the keys."""

    def __init__(self, key_id: str, secret: str, base: str = PAPER, transport: httpx.BaseTransport | None = None):
        if base.rstrip("/") != PAPER:
            raise BrokerError("only the Alpaca PAPER endpoint is allowed")
        h = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
        self.c = httpx.Client(headers=h, timeout=20.0, transport=transport)

    @classmethod
    def from_env(cls) -> Alpaca | None:
        k, s = key("ALPACA_API_KEY_ID", "APCA_API_KEY_ID"), key("ALPACA_API_SECRET_KEY", "APCA_API_SECRET_KEY")
        base = key("ALPACA_BASE_URL") or PAPER
        return cls(k, s, base) if k and s else None

    def _get(self, url: str, **params: Any) -> Any:
        r = self.c.get(url, params=params or None)
        if r.status_code != 200:
            raise BrokerError(f"GET {url.split('.markets')[-1]}: HTTP {r.status_code} {r.text[:120]}")
        return r.json()

    def account(self) -> dict[str, Any]:
        a = self._get(f"{PAPER}/account")
        if a.get("status") != "ACTIVE" or a.get("trading_blocked") or a.get("account_blocked"):
            raise BrokerError(f"account not tradable (status {a.get('status')})")
        return dict(a)

    def positions(self) -> dict[str, float]:
        return {position_asset(p["symbol"]): float(p["qty"]) for p in self._get(f"{PAPER}/positions")}

    def prices(self, assets: list[str]) -> dict[str, float]:
        out = {}
        stocks = [a for a in assets if a not in CRYPTO]
        if stocks:
            t = self._get(f"{DATA}/v2/stocks/trades/latest", symbols=",".join(stocks), feed="iex")["trades"]
            out.update({a: float(t[a]["p"]) for a in stocks if a in t})
        coins = [a for a in assets if a in CRYPTO]
        if coins:
            t = self._get(f"{DATA}/v1beta3/crypto/us/latest/trades", symbols=",".join(CRYPTO[a] for a in coins))
            out.update({a: float(t["trades"][CRYPTO[a]]["p"]) for a in coins if CRYPTO[a] in t["trades"]})
        return out

    def submit(self, leg: Leg) -> None:
        body = {"symbol": leg.symbol, "qty": str(leg.qty), "side": leg.side, "type": "market",
                "time_in_force": leg.tif, "client_order_id": leg.client_order_id}
        r = self.c.post(f"{PAPER}/orders", json=body)
        if r.status_code in (200, 201):
            leg.status, leg.order_id = "submitted", r.json().get("id")
        elif r.status_code == 422 and "client_order_id" in r.text:  # sent before (a crash after submit): look it up
            self.refresh(leg)
        else:
            leg.status, leg.note = "rejected", f"HTTP {r.status_code} {r.text[:160]}"

    def refresh(self, leg: Leg) -> None:
        o = self._get(f"{PAPER}/orders:by_client_order_id", client_order_id=leg.client_order_id)
        leg.order_id = o.get("id")
        st = o.get("status", "")
        if st == "filled":
            leg.status, leg.filled_price, leg.filled_at = "filled", float(o["filled_avg_price"]), o.get("filled_at")
        elif st in ("rejected", "canceled", "expired"):
            leg.status = st
        else:
            leg.status = "submitted"

    def cancel_all(self) -> None:
        r = self.c.delete(f"{PAPER}/orders")
        if r.status_code not in (200, 207):
            raise BrokerError(f"cancel all: HTTP {r.status_code}")
