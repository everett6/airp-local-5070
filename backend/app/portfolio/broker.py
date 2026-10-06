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
- a gap over 0.5% between the broker's and the simulator's fill price, or a rejected / expired order, is an alert;
- the quantity an order filled is kept whatever its final status: an order that filled 4 of 10 and was then
  cancelled leaves 4 shares in the account, and `held()` says so.
The planning and reconciling functions take plain data, so they are tested without the network.
"""
from __future__ import annotations

import fcntl
import json
import math
import re
import time as clock_time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.data_ingestion.bars import key
from app.forward.ledger import Ledger
from app.portfolio.account_risk import RiskPolicy, evaluate

PAPER = "https://paper-api.alpaca.markets/v2"
DATA = "https://data.alpaca.markets"
NY = ZoneInfo("America/New_York")
CRYPTO = {"BTC-USD": "BTC/USD", "ETH-USD": "ETH/USD"}
STOCKS = ("SPY", "SGOV", "QQQ", "TLT")  # the book's stock assets: sold when a decision drops them
GAP_ALERT = 0.005
MIN_NOTIONAL = 10.0  # smaller legs are skipped (Alpaca's crypto minimum is about $1; a $10 floor avoids dust)
BACKEND = Path(__file__).resolve().parents[2]
EXECUTION = BACKEND / "results" / "forward" / "execution"
DEFAULT_RISK = RiskPolicy()
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
    filled_qty: float | None = None  # what the broker filled so far; None on records written before 1 Oct 2026
    filled_price: float | None = None
    filled_at: str | None = None
    sim_price: float | None = None
    gap: float | None = None
    note: str = ""
    alerted: bool = False


UNFILLED = ("rejected", "canceled", "expired")  # final without a full fill (part of it may still have filled)


def held_qty(status: str, qty: float, filled_qty: float | None) -> float:
    """The quantity an order put into the account: its recorded fill, or (older records) all of it if it filled."""
    if filled_qty is not None:
        return float(filled_qty)
    return float(qty) if status == "filled" else 0.0


def held(leg: Leg) -> float:
    return held_qty(leg.status, leg.qty, leg.filled_qty)


def symbol(asset: str) -> str:
    """The book's asset name as Alpaca spells it: coins from the table, class shares with a dot (BRK-B -> BRK.B)."""
    return CRYPTO.get(asset, asset.replace("-", "."))


def position_asset(sym: str) -> str:
    """Alpaca's position symbol ('BTCUSD', 'SPY') back to the book's asset name."""
    for a, s in CRYPTO.items():
        if sym.replace("/", "") == s.replace("/", ""):
            return a
    return sym.replace(".", "-")


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
    if leg.status in UNFILLED:
        part = f" after filling {held(leg):g} of {leg.qty:g}" if held(leg) > 0 else ""
        alerts.append(f"broker order {leg.client_order_id} {leg.status}{part} {leg.note}".strip())
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

    def __init__(self, key_id: str, secret: str, base: str = PAPER, transport: httpx.BaseTransport | None = None,
                 risk_policy: RiskPolicy | None = DEFAULT_RISK, audit_path: Path = EXECUTION):
        if base.rstrip("/") != PAPER:
            raise BrokerError("only the Alpaca PAPER endpoint is allowed")
        h = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
        self.c = httpx.Client(headers=h, timeout=20.0, transport=transport)
        self.risk_policy, self.audit_path = risk_policy, audit_path

    def _audit(self, kind: str, **fields: Any) -> None:
        self.audit_path.mkdir(parents=True, exist_ok=True)
        with (self.audit_path / "audit.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            Ledger(self.audit_path / "ledger.jsonl").verify()
            Ledger(self.audit_path / "ledger.jsonl").append(kind, **fields)

    @classmethod
    def from_env(cls) -> Alpaca | None:
        k, s = key("ALPACA_API_KEY_ID", "APCA_API_KEY_ID"), key("ALPACA_API_SECRET_KEY", "APCA_API_SECRET_KEY")
        base = key("ALPACA_BASE_URL") or PAPER
        if not (k and s):
            return None
        try:
            policy = RiskPolicy(**json.loads((BACKEND / "config" / "account_risk.json").read_text()))
        except (OSError, ValueError, TypeError) as exc:
            raise BrokerError("account risk policy is missing or invalid") from exc
        return cls(k, s, base, risk_policy=policy)

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
            t = self._get(f"{DATA}/v2/stocks/trades/latest", symbols=",".join(symbol(a) for a in stocks),
                          feed="iex")["trades"]
            out.update({a: float(t[symbol(a)]["p"]) for a in stocks if symbol(a) in t})
        coins = [a for a in assets if a in CRYPTO]
        if coins:
            t = self._get(f"{DATA}/v1beta3/crypto/us/latest/trades", symbols=",".join(CRYPTO[a] for a in coins))
            out.update({a: float(t["trades"][CRYPTO[a]]["p"]) for a in coins if CRYPTO[a] in t["trades"]})
        return out

    def submit(self, leg: Leg) -> None:
        if self.risk_policy is not None:
            self.audit_path.mkdir(parents=True, exist_ok=True)
            with (self.audit_path / "submit.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                # Recover a lost acknowledgement before considering a new order or new limits.
                existing = self.c.get(f"{PAPER}/orders:by_client_order_id", params={"client_order_id": leg.client_order_id})
                if existing.status_code == 200:
                    self.refresh(leg)
                    return
                if existing.status_code != 404:
                    raise BrokerError(f"order lookup: HTTP {existing.status_code}; no order sent")
                try:
                    verdict = self.pretrade(leg)
                except (BrokerError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    # Local validation text is safe and actionable; broker HTTP bodies
                    # remain excluded from diagnostics.
                    detail = str(exc)[:120] if isinstance(exc, ValueError) else type(exc).__name__
                    verdict = {"allowed": False, "reasons": [f"risk snapshot unavailable: {detail}"]}
                self._audit("risk", client_order_id=leg.client_order_id,
                    symbol=leg.symbol, side=leg.side, qty=leg.qty, ref_price=leg.ref_price,
                    at=datetime.now(UTC).isoformat(), **verdict)
                if not verdict["allowed"]:
                    leg.status, leg.note = "rejected", "ACCOUNT RISK: " + "; ".join(verdict["reasons"])
                    return
                self._submit(leg)
            return
        self._submit(leg)  # explicit disabled policy is reserved for isolated simulation fixtures

    def pretrade(self, leg: Leg) -> dict[str, Any]:
        if self.risk_policy is None:
            raise BrokerError("risk policy disabled")
        now = datetime.now(UTC)
        # Independent reads overlap; submission still waits for every risk input.
        with ThreadPoolExecutor(max_workers=3) as pool:
            account_job = pool.submit(self.account)
            positions_job = pool.submit(self._get, f"{PAPER}/positions")
            orders_job = pool.submit(self._get, f"{PAPER}/orders", status="open", limit=500)
            account, positions, orders = account_job.result(), positions_job.result(), orders_job.result()
        if len(orders) >= 500:
            raise BrokerError("open order snapshot may be truncated")
        names = {leg.symbol} | {p["symbol"] for p in positions} | {o["symbol"] for o in orders}
        coins = {n: next((s for s in CRYPTO.values() if n.replace("/", "") == s.replace("/", "")), "") for n in names}
        stocks = [n for n in names if not coins[n]]
        quotes: dict[str, dict[str, Any]] = {}
        if stocks:
            quotes.update(self._get(f"{DATA}/v2/stocks/quotes/latest", symbols=",".join(sorted(stocks)), feed="iex")["quotes"])
        if any(coins.values()):
            got = self._get(f"{DATA}/v1beta3/crypto/us/latest/quotes", symbols=",".join(sorted({s for s in coins.values() if s})))["quotes"]
            quotes.update({n: got[s] for n, s in coins.items() if s})
        floor = now - timedelta(seconds=self.risk_policy.max_quote_age_s)
        if not coins[leg.symbol]:
            market = self._get(f"{PAPER}/clock")
            if not market["is_open"]:
                calendar = self._get(f"{PAPER}/calendar", start=(now.astimezone(NY).date() - timedelta(days=10)).isoformat(),
                                     end=now.astimezone(NY).date().isoformat())
                closes = [datetime.fromisoformat(f"{d['date']}T{d['close']}").replace(tzinfo=NY) for d in calendar]
                floor = max(t for t in closes if t <= now) - timedelta(minutes=15)
        verdict = evaluate(self.risk_policy, account, positions, orders, quotes, leg.symbol, leg.side, leg.qty,
                           leg.ref_price, now, bool(coins[leg.symbol]), floor)
        if leg.side == "sell" and not coins[leg.symbol] and not verdict["reducing"]:
            asset = self._get(f"{PAPER}/assets/{leg.symbol}")
            if not asset.get("tradable") or not asset.get("shortable"):
                verdict["allowed"] = False
                verdict["reasons"].append("asset is not tradable and shortable")
        verdict["feed"] = "alpaca_crypto" if coins[leg.symbol] else "iex"
        return verdict

    def _submit(self, leg: Leg) -> None:
        body = {"symbol": leg.symbol, "qty": str(leg.qty), "side": leg.side, "type": "market",
                "time_in_force": leg.tif, "client_order_id": leg.client_order_id}
        start = clock_time.monotonic()
        r = self.c.post(f"{PAPER}/orders", json=body)
        if r.status_code in (200, 201):
            leg.status, leg.order_id = "submitted", r.json().get("id")
        elif r.status_code == 422 and "client_order_id" in r.text:  # sent before (a crash after submit): look it up
            self.refresh(leg)
        else:
            leg.status, leg.note = "rejected", f"HTTP {r.status_code} {r.text[:160]}"
        if self.risk_policy is not None:
            self._audit("submission", client_order_id=leg.client_order_id,
                at=datetime.now(UTC).isoformat(), latency_ms=1000 * (clock_time.monotonic() - start),
                status=leg.status, symbol=leg.symbol, side=leg.side, qty=leg.qty, ref_price=leg.ref_price)

    def cancel_leg(self, leg: Leg) -> None:
        """Cancel only this owned entry order; a racing fill is read by refresh next."""
        order = self._get(f"{PAPER}/orders:by_client_order_id", client_order_id=leg.client_order_id)
        response = self.c.delete(f"{PAPER}/orders/{order['id']}")
        if response.status_code not in (204, 404, 422):
            raise BrokerError("Could not cancel restricted entry")

    def refresh(self, leg: Leg) -> None:
        o = self._get(f"{PAPER}/orders:by_client_order_id", client_order_id=leg.client_order_id)
        leg.order_id = o.get("id")
        st = o.get("status", "")
        got = float(o.get("filled_qty") or 0.0)
        if (got > 0 or st == "filled") and o.get("filled_avg_price") is not None:
            leg.filled_price, leg.filled_at = float(o["filled_avg_price"]), o.get("filled_at") or leg.filled_at
        if st == "filled":
            leg.status, leg.filled_qty = "filled", got or float(leg.qty)
        elif st in (*UNFILLED, "done_for_day"):  # final; whatever filled before it ended stays in the account
            leg.status, leg.filled_qty = ("expired" if st == "done_for_day" else st), got
        else:  # still working (new, accepted, partially_filled, ...)
            leg.status, leg.filled_qty = "submitted", got
        if self.risk_policy is not None:
            self._audit("fill_snapshot", client_order_id=leg.client_order_id,
                at=datetime.now(UTC).isoformat(), status=leg.status, filled_qty=leg.filled_qty,
                filled_price=leg.filled_price, filled_at=leg.filled_at)

    def cancel_all(self) -> None:
        r = self.c.delete(f"{PAPER}/orders")
        if r.status_code not in (200, 207):
            raise BrokerError(f"cancel all: HTTP {r.status_code}")
