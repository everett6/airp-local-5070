"""Rehearsals of the account risk authority against a fake broker (review #24): the final BROKER state is asserted,
not exit codes."""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from app.portfolio import account_book as ab
from app.portfolio.broker import Leg
from app.sandbox import minute_ensemble as me

SESSION = date(2026, 10, 7)


class Resp:
    def __init__(self, code: int, body: Any = None):
        self.status_code, self._b = code, body

    def json(self) -> Any:
        return self._b


class FakeBroker:
    """Positions, orders by client id; scenario knobs: lose_reply, partial, reject, stuck."""

    def __init__(self, positions: dict[str, float] | None = None, equity: float = 100_000.0):
        self.pos = dict(positions or {})
        self.orders: dict[str, dict[str, Any]] = {}
        self.equity = equity
        self.risk_policy = ab.SANDBOX_RISK
        self.lose_reply: set[str] = set()
        self.partial: set[str] = set()
        self.reject: set[str] = set()
        self.stuck: set[str] = set()
        self.sent: list[str] = []
        self.c = self

    # httpx-like client
    def get(self, url: str, params: dict[str, Any] | None = None) -> Resp:
        cid = (params or {}).get("client_order_id")
        return Resp(200, self.orders[cid]) if cid in self.orders else Resp(404)

    def delete(self, url: str) -> Resp:
        oid = url.rsplit("/", 1)[-1]
        for o in self.orders.values():
            if o["id"] == oid and o["status"] in ("new", "partially_filled"):
                o["status"] = "canceled"
        return Resp(204)

    def account(self) -> dict[str, Any]:
        return {"status": "ACTIVE", "equity": str(self.equity), "last_equity": str(self.equity),
                "buying_power": str(4 * self.equity), "non_marginable_buying_power": str(self.equity),
                "daytrading_buying_power": str(4 * self.equity)}

    def _get(self, url: str, **params: Any) -> Any:
        if url.endswith("/positions"):
            return [{"symbol": s, "qty": str(abs(q)), "side": "long" if q > 0 else "short", "market_value": str(q * 100),
                     "current_price": "100"} for s, q in self.pos.items() if q]
        if url.endswith("/orders"):
            return [o for o in self.orders.values() if o["status"] in ("new", "partially_filled")]
        if "quotes/latest" in url:
            now = datetime.now(UTC).isoformat()
            return {"quotes": {s: {"bp": 99.99, "ap": 100.01, "t": now} for s in params["symbols"].split(",")}}
        raise AssertionError(url)

    def _audit(self, kind: str, **fields: Any) -> None:
        pass

    def _submit(self, leg: Leg) -> None:
        sgn = 1 if leg.side == "buy" else -1
        st, fill = "filled", leg.qty
        if leg.symbol in self.reject:
            leg.status, leg.note = "rejected", "HTTP 403"
            return
        if leg.symbol in self.partial:
            st, fill = "partially_filled", leg.qty // 2
        if leg.symbol in self.stuck:
            st, fill = "new", 0
        self.orders[leg.client_order_id] = {"id": f"o{len(self.orders)}", "symbol": leg.symbol, "side": leg.side,
                                            "qty": str(leg.qty), "filled_qty": str(fill), "status": st,
                                            "filled_avg_price": "100", "client_order_id": leg.client_order_id}
        self.pos[leg.symbol] = self.pos.get(leg.symbol, 0.0) + sgn * fill
        self.sent.append(leg.client_order_id)
        if leg.symbol in self.lose_reply:
            raise TimeoutError("reply lost")
        leg.status, leg.order_id = "submitted", self.orders[leg.client_order_id]["id"]

    def refresh(self, leg: Leg) -> None:
        o = self.orders[leg.client_order_id]
        leg.order_id, leg.filled_qty = o["id"], float(o["filled_qty"])
        leg.status = {"filled": "filled", "canceled": "canceled", "rejected": "rejected"}.get(o["status"], "submitted")

    def cancel_leg(self, leg: Leg) -> None:
        self.delete(f"/orders/{self.orders[leg.client_order_id]['id']}")


def book(tmp_path: Any) -> ab.Book:
    return ab.Book(tmp_path)


def test_day_symbols_match_the_engine_universe() -> None:
    assert ab.DAY_SYMBOLS == frozenset(me.UNIVERSE)


def test_lost_reply_is_recovered_by_id_and_never_sent_twice(tmp_path: Any) -> None:
    b, br = book(tmp_path), FakeBroker()
    br.lose_reply.add("XLF")
    order = [{"symbol": "XLF", "side": "buy", "qty": 10, "to": 10}]
    first = ab.send_batch(br, b, "day", SESSION, "m29", order, {"XLF": 100})
    assert first[0].status == "submitted" and "reply lost" in first[0].note
    assert b.intents()[first[0].client_order_id]["ambiguous"]
    br.lose_reply.clear()
    again = ab.send_batch(br, b, "day", SESSION, "m29", order, {"XLF": 100})  # the same intent retried
    assert len(br.sent) == 1 and br.pos["XLF"] == 10 and again[0].status == "filled"
    rec = ab.reconcile(br, b, "day")
    assert rec["ok"] and br.pos["XLF"] == 10


def test_crash_after_intent_before_send_is_never_sent(tmp_path: Any) -> None:
    b, br = book(tmp_path), FakeBroker()
    leg = Leg("XLE", "XLE", "buy", 5, "day", ab.client_id("day", SESSION, "m34", "XLE", "buy", 5), 100)
    b.intent(leg, "day", "intended")  # the process died here
    rec = ab.reconcile(br, b, "day")
    assert rec["ok"] and b.intents()[leg.client_order_id]["state"] == "never_sent" and not br.sent


def test_ownership_and_watchdog_reduce_only(tmp_path: Any) -> None:
    b, br = book(tmp_path), FakeBroker({"AAPL": 10, "SPY": 20})
    legs = ab.send_batch(br, b, "day", SESSION, "m29", [{"symbol": "AAPL", "side": "buy", "qty": 1, "to": 11}], {})
    assert legs[0].status == "rejected" and "OWNERSHIP" in legs[0].note and br.pos["AAPL"] == 10
    legs = ab.send_batch(br, b, "watchdog", SESSION, "w", [{"symbol": "SPY", "side": "buy", "qty": 5, "to": 25}], {})
    assert legs[0].status == "rejected"  # the watchdog may only reduce
    legs = ab.send_batch(br, b, "watchdog", SESSION, "w", [{"symbol": "SPY", "side": "sell", "qty": 20, "to": 0}], {})
    assert legs[0].status == "submitted" and br.pos["SPY"] == 0


def test_partial_fill_and_reject_are_followed_to_a_final_state(tmp_path: Any) -> None:
    b, br = book(tmp_path), FakeBroker()
    br.partial.add("XLV")
    br.reject.add("XLU")
    legs = ab.send_batch(br, b, "day", SESSION, "m39", [{"symbol": "XLV", "side": "buy", "qty": 10, "to": 10},
                                                         {"symbol": "XLU", "side": "buy", "qty": 10, "to": 10}], {})
    ab.wait_final(br, b, "day", legs, timeout_s=0.05, poll_s=0.01, sleep=lambda s: None)
    v, u = legs
    assert v.status == "canceled" and v.filled_qty == 5 and br.pos["XLV"] == 5  # rest canceled after the timeout
    assert u.status == "rejected" and br.pos.get("XLU", 0) == 0
    assert b.intents()[v.client_order_id]["state"] == "canceled"


def test_account_cap_counts_open_orders_of_both_strategies(tmp_path: Any) -> None:
    b, br = book(tmp_path), FakeBroker()
    br.stuck.update({"AAPL", "SPY"})
    ab.send_batch(br, b, "night", SESSION, "p1", [{"symbol": "AAPL", "side": "buy", "qty": 2000, "to": 2000}], {"AAPL": 100})
    legs = ab.send_batch(br, b, "day", SESSION, "m29", [{"symbol": "SPY", "side": "buy", "qty": 2000, "to": 2000}],
                         {"SPY": 100})
    assert legs[0].status == "rejected" and "ACCOUNT RISK" in legs[0].note  # 2.0x pending + 2.0x > 3.9x
    assert br.pos.get("SPY", 0) == 0


def test_flatten_is_verified_or_opens_an_incident(tmp_path: Any) -> None:
    b, br = book(tmp_path), FakeBroker({"SPY": 10, "XLF": -4, "AAPL": 3})
    out = ab.flatten(br, b, "day", SESSION, "window end", verify_s=0.01, sleep=lambda s: None)
    assert out["flat"] and br.pos["SPY"] == 0 and br.pos["XLF"] == 0 and br.pos["AAPL"] == 3  # Night's stock kept
    br.pos["XLE"] = 7
    br.stuck.add("XLE")  # the close order never fills
    out = ab.flatten(br, b, "day", SESSION, "window end", verify_s=0.01, sleep=lambda s: None)
    assert not out["flat"] and b.open_incidents("flatten_day")
    br.stuck.clear()
    out = ab.flatten(br, b, "day", SESSION, "retry", verify_s=0.01, sleep=lambda s: None)
    assert out["flat"] and not b.open_incidents("flatten_day")


def test_execution_gate_and_ceilings(tmp_path: Any) -> None:
    b, br = book(tmp_path), FakeBroker()
    legs = ab.send_batch(br, b, "day", SESSION, "m44", [{"symbol": "XLB", "side": "buy", "qty": 3, "to": 3}], {},
                         limit_bp=3, quote_age_s=5, max_spread_bp=5, tif="ioc")
    assert legs[0].limit_price == pytest.approx(100.01 * 1.0003) and legs[0].tif == "ioc"
    legs = ab.send_batch(br, b, "day", SESSION, "m49", [{"symbol": "XLB", "side": "buy", "qty": 3, "to": 6}], {},
                         limit_bp=3, quote_age_s=5, max_spread_bp=1)
    assert legs[0].status == "rejected" and "spread" in legs[0].note
    loose = ab.RiskPolicy(max_gross=5.0, max_asset=0.25, daily_loss=0.15)
    assert ab.ceiling_violations(loose) and ab.ceiling_violations(ab.SANDBOX_RISK) == []
    assert ab.ceiling_violations(ab.SANDBOX_RISK, day_gross=4.0)


def test_states_and_ids(tmp_path: Any) -> None:
    b = book(tmp_path)
    b.state("day", "reconciling", "startup")
    b.state("day", "reconciling", "startup")  # no duplicate transition
    b.state("day", "trading", "reconciled")
    assert b.current("day")["state"] == "trading" and b.current("day")["from"] == "reconciling"
    assert len((tmp_path / "states.jsonl").read_text().splitlines()) == 2
    a = ab.client_id("night", SESSION, "p" * 40, "BRK.B", "sell", -12)
    assert len(a) <= 48 and a == ab.client_id("night", SESSION, "p" * 40, "BRK.B", "sell", -12)


def test_restore_from_backup_reconciles_instead_of_replaying(tmp_path: Any) -> None:
    """Review #25: records restored from a backup settle against the broker; nothing is sent again."""
    live, restored = tmp_path / "live", tmp_path / "restored"
    b, br = ab.Book(live), FakeBroker()
    br.stuck.add("XLI")
    order = [{"symbol": "XLI", "side": "buy", "qty": 7, "to": 7}]
    ab.send_batch(br, b, "day", SESSION, "m59", order, {})
    restored.mkdir()
    (restored / "intents.jsonl").write_text((live / "intents.jsonl").read_text())  # the backup copy
    r = ab.Book(restored)
    assert ab.reconcile(br, r, "day")["ok"]
    ab.send_batch(br, r, "day", SESSION, "m59", order, {})  # the same decision replayed after the restore
    assert len(br.sent) == 1 and r.open_intents("day")[0]["state"] == "submitted"
