"""Paper broker mirror (app/portfolio/broker.py, scripts/broker_sync.py) against a fake Alpaca: no network."""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.portfolio import broker as B

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import broker_sync

DEC = "2026-10-05T22:01:00+00:00"  # Monday 18:01 ET


def test_plan_sizes_sells_first_and_reduce_only() -> None:
    px = {"SPY": 600.0, "BTC-USD": 100_000.0, "ETH-USD": 4_000.0}
    legs = B.plan(DEC, {"SPY": 0.78, "BTC-USD": 0.12, "ETH-USD": 0.08}, 100_000.0, {"ETH-USD": 5.0}, px)
    by = {x.asset: x for x in legs}
    assert by["SPY"].qty == 130 and by["SPY"].tif == "day" and by["SPY"].side == "buy"
    assert by["BTC-USD"].qty == pytest.approx(0.12) and by["BTC-USD"].symbol == "BTC/USD"
    assert by["ETH-USD"].side == "sell" and by["ETH-USD"].qty == pytest.approx(3.0)
    assert legs[0].side == "sell"
    assert len({x.client_order_id for x in legs}) == 3
    assert [x.asset for x in B.plan(DEC, {"SPY": 0.78}, 100_000.0, {"ETH-USD": 5.0}, px, reduce_only=True)] == ["ETH-USD"]
    assert B.plan(DEC, {"SPY": 0.78}, 100_000.0, {"SPY": 130.0}, px) == []  # already there


def test_opg_window() -> None:
    assert not B.opg_open(datetime(2026, 10, 5, 22, 1, tzinfo=UTC))   # 18:01 ET
    assert B.opg_open(datetime(2026, 10, 5, 23, 30, tzinfo=UTC))      # 19:30 ET
    assert B.opg_open(datetime(2026, 10, 6, 12, 45, tzinfo=UTC))      # 08:45 ET
    assert not B.opg_open(datetime(2026, 10, 6, 13, 29, tzinfo=UTC))  # 09:29 ET


def test_only_paper_endpoint() -> None:
    with pytest.raises(B.BrokerError):
        B.Alpaca("k", "s", base="https://api.alpaca.markets/v2")


def test_reconcile_alerts_once() -> None:
    leg = B.Leg("SPY", "SPY", "buy", 1, "opg", "id", 600.0, status="filled", filled_price=606.0)
    assert B.reconcile(leg, {"SPY": 600.0}) and leg.gap == pytest.approx(0.01)
    assert B.reconcile(leg, {"SPY": 600.0}) == []
    ok = B.Leg("SPY", "SPY", "buy", 1, "opg", "id2", 600.0, status="filled", filled_price=600.5)
    assert B.reconcile(ok, {"SPY": 600.0}) == []


class FakeAlpaca:
    def __init__(self) -> None:
        self.orders: dict[str, dict[str, Any]] = {}
        self.posts = 0
        self.cancels = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        u = req.url
        if u.path == "/v2/account":
            return httpx.Response(200, json={"status": "ACTIVE", "equity": "100000"})
        if u.path == "/v2/positions":
            return httpx.Response(200, json=[])
        if u.path == "/v2/stocks/trades/latest":
            return httpx.Response(200, json={"trades": {"SPY": {"p": 600.0}}})
        if u.path == "/v1beta3/crypto/us/latest/trades":
            return httpx.Response(200, json={"trades": {"BTC/USD": {"p": 100_000.0}, "ETH/USD": {"p": 4000.0}}})
        if u.path == "/v2/orders" and req.method == "POST":
            body = json.loads(req.content)
            if body["client_order_id"] in self.orders:
                return httpx.Response(422, text="client_order_id must be unique")
            self.posts += 1
            self.orders[body["client_order_id"]] = {"id": f"o{self.posts}", "status": "accepted", **body}
            return httpx.Response(200, json=self.orders[body["client_order_id"]])
        if u.path == "/v2/orders" and req.method == "DELETE":
            self.cancels += 1
            return httpx.Response(207, json=[])
        if u.path == "/v2/orders:by_client_order_id":
            return httpx.Response(200, json=self.orders[u.params["client_order_id"]])
        return httpx.Response(404)


def setup(tmp: Path, fake: FakeAlpaca) -> tuple[B.Alpaca, Path]:
    alloc = tmp / "alloc"
    alloc.mkdir()
    (alloc / "state.json").write_text(json.dumps({"master": {"pending": {"SPY": 0.78, "BTC-USD": 0.2},
                                                             "decided_at": DEC}}))
    return B.Alpaca("k", "s", transport=httpx.MockTransport(fake)), alloc


def test_sync_crypto_now_spy_in_window_then_reconcile(tmp_path: Path) -> None:
    fake = FakeAlpaca()
    client, alloc = setup(tmp_path, fake)
    out, halt = tmp_path / "broker", tmp_path / "HALT"
    assert broker_sync.sync(client, alloc, out, datetime(2026, 10, 5, 22, 5, tzinfo=UTC), False, halt) == []
    legs = {d["asset"]: d for d in json.loads((out / "orders.json").read_text())[DEC]["legs"]}
    assert legs["BTC-USD"]["status"] == "submitted" and legs["SPY"]["status"] == "planned" and fake.posts == 1
    broker_sync.sync(client, alloc, out, datetime(2026, 10, 6, 12, 45, tzinfo=UTC), False, halt)  # 08:45 ET
    broker_sync.sync(client, alloc, out, datetime(2026, 10, 6, 12, 50, tzinfo=UTC), False, halt)  # re-run: no resend
    assert fake.posts == 2
    for o in fake.orders.values():  # both fill; the simulator fills at the next allocator run
        o.update(status="filled", filled_avg_price="601.0" if o["symbol"] == "SPY" else "99000", filled_at="x")
    (alloc / "ledger.jsonl").write_text(json.dumps({"run_at_utc": "2026-10-12T22:00:00+00:00", "books": {"master": {
        "filled_on": "2026-10-06", "fills": [{"asset": "SPY", "qty": 130, "price": 600.0},
                                             {"asset": "BTC-USD", "qty": 0.2, "price": 100_000.0}]}}}) + "\n")
    alerts = broker_sync.sync(client, alloc, out, datetime(2026, 10, 12, 22, 5, tzinfo=UTC), False, halt)
    legs = {d["asset"]: d for d in json.loads((out / "orders.json").read_text())[DEC]["legs"]}
    assert legs["SPY"]["gap"] == pytest.approx(601 / 600 - 1, abs=1e-6) and legs["BTC-USD"]["gap"] == pytest.approx(-0.01)
    assert len(alerts) == 1 and "BTC-USD" in alerts[0]  # 1% gap alerts, 0.17% does not


def test_halted_sends_nothing_and_cancels(tmp_path: Path) -> None:
    fake = FakeAlpaca()
    client, alloc = setup(tmp_path, fake)
    halt = tmp_path / "HALT"
    halt.write_text(json.dumps({"mode": "HALTED", "reason": "test"}))
    broker_sync.sync(client, alloc, tmp_path / "b", datetime(2026, 10, 6, 12, 45, tzinfo=UTC), False, halt)
    assert fake.posts == 0 and fake.cancels == 1


def test_dry_sends_nothing(tmp_path: Path) -> None:
    fake = FakeAlpaca()
    client, alloc = setup(tmp_path, fake)
    broker_sync.sync(client, alloc, tmp_path / "b", datetime(2026, 10, 6, 12, 45, tzinfo=UTC), True, tmp_path / "H")
    assert fake.posts == 0


def test_no_keys_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(B, "key", lambda *a: None)
    assert B.Alpaca.from_env() is None


def test_stale_decision_is_not_mirrored(tmp_path: Path) -> None:
    fake = FakeAlpaca()
    client, alloc = setup(tmp_path, fake)
    out = tmp_path / "b"
    broker_sync.sync(client, alloc, out, datetime(2026, 10, 12, 12, 45, tzinfo=UTC), False, tmp_path / "H")
    o = json.loads((out / "orders.json").read_text())[DEC]
    assert fake.posts == 0 and o["legs"] == [] and "older" in o["skipped"]


def test_plan_sells_sgov_when_the_brakes_lift() -> None:
    px = {"SPY": 600.0, "BTC-USD": 100_000.0, "ETH-USD": 4_000.0, "SGOV": 100.0}
    legs = B.plan(DEC, {"SPY": 0.78, "BTC-USD": 0.20}, 100_000.0, {"SPY": 65.0, "SGOV": 400.0}, px)
    by = {x.asset: x for x in legs}
    assert by["SGOV"].side == "sell" and by["SGOV"].qty == 400 and by["SGOV"].tif == "day"
    assert legs[0].asset == "SGOV"  # sells first
