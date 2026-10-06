"""Account controls at the actual submit boundary against a synthetic exchange."""
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.forward.ledger import Ledger
from app.portfolio.account_risk import RiskPolicy, evaluate
from app.portfolio.broker import Alpaca, Leg

NOW = datetime(2026, 10, 2, 15, tzinfo=UTC)


def account(**changes):
    return {"status": "ACTIVE", "equity": "10000", "last_equity": "10000", "buying_power": "20000",
            "non_marginable_buying_power": "10000", **changes}


def check(qty=10, side="buy", positions=None, orders=None, acct=None, quote=None, symbol="SPY"):
    return evaluate(RiskPolicy(), acct or account(), positions or [], orders or [],
                    {symbol: quote or {"bp": 99.9, "ap": 100.1, "t": NOW.isoformat()}}, symbol,
                    side, qty, 100, NOW, symbol == "BTC/USD", NOW - timedelta(seconds=180))


def test_combined_pending_orders_cannot_exceed_account_limits():
    orders = [{"symbol": "SPY", "qty": "170", "filled_qty": "0", "side": "buy"}]
    v = check(qty=40, orders=orders)
    assert not v["allowed"] and v["gross"] == pytest.approx(2.1)
    assert "account gross" in " ".join(v["reasons"])


def test_opposing_pending_orders_are_not_netted_and_partial_fills_are_not_counted_twice():
    orders = [{"symbol": "SPY", "qty": "200", "filled_qty": "100", "side": "buy"},
              {"symbol": "SPY", "qty": "100", "filled_qty": "0", "side": "sell"}]
    positions = [{"symbol": "SPY", "qty": "100", "market_value": "10000"}]
    v = check(qty=1, orders=orders, positions=positions)
    assert v["gross"] == pytest.approx(2.01) and not v["allowed"]


def test_reduce_overlimit_and_daily_loss_without_creating_a_new_short():
    positions = [{"symbol": "SPY", "qty": "300", "market_value": "30000"}]
    acct = account(last_equity="12000", buying_power="0")
    assert check(qty=100, side="sell", positions=positions, acct=acct)["allowed"]
    assert not check(qty=350, side="sell", positions=positions, acct=acct)["allowed"]
    assert not check(qty=1, positions=positions, acct=acct)["allowed"]


def test_stale_crossed_wide_quotes_and_buying_power_fail_closed():
    assert not check(quote={"bp": 99.9, "ap": 100.1, "t": (NOW - timedelta(minutes=4)).isoformat()})["allowed"]
    assert not check(quote={"bp": 90, "ap": 110, "t": NOW.isoformat()})["allowed"]
    assert not check(acct=account(buying_power="1"))["allowed"]
    with pytest.raises(ValueError):
        check(quote={"bp": 101, "ap": 100, "t": NOW.isoformat()})
    with pytest.raises(ValueError):
        check(qty=float("nan"))


def test_crypto_symbol_forms_and_nonmarginable_cash_limit():
    positions = [{"symbol": "BTCUSD", "qty": "44", "market_value": "4400"}]
    assert not check(qty=2, positions=positions, symbol="BTC/USD")["allowed"]
    assert not check(qty=10, symbol="BTC/USD", acct=account(non_marginable_buying_power="10"))["allowed"]


def exchange(tmp_path, *, missing_quotes=False, existing=False):
    posts = []
    stamp = datetime.now(UTC).isoformat()

    def handler(req):
        p = req.url.path
        if p == "/v2/orders:by_client_order_id":
            return httpx.Response(200, json={"id": "old", "status": "filled", "filled_qty": "10",
                 "filled_avg_price": "100", "filled_at": stamp}) if existing else httpx.Response(404)
        if p == "/v2/account":
            return httpx.Response(200, json=account())
        if p == "/v2/positions":
            return httpx.Response(200, json=[])
        if p == "/v2/orders" and req.method == "GET":
            return httpx.Response(200, json=[])
        if p == "/v2/stocks/quotes/latest":
            return httpx.Response(200, json={"quotes": {} if missing_quotes else {"SPY": {"bp": 99.9, "ap": 100.1, "t": stamp}}})
        if p == "/v2/clock":
            return httpx.Response(200, json={"is_open": True})
        if p == "/v2/orders" and req.method == "POST":
            posts.append(json.loads(req.content))
            return httpx.Response(200, json={"id": "new"})
        return httpx.Response(404)
    return Alpaca("synthetic", "synthetic", transport=httpx.MockTransport(handler), audit_path=tmp_path), posts


def test_real_submission_requires_recorded_gate_pass_and_captures_latency(tmp_path):
    client, posts = exchange(tmp_path)
    leg = Leg("SPY", "SPY", "buy", 10, "day", "unique", 100)
    client.submit(leg)
    assert len(posts) == 1 and leg.status == "submitted"
    rows = Ledger(tmp_path / "ledger.jsonl").verify()
    assert rows[0]["type"] == "risk" and rows[0]["allowed"]
    assert rows[1]["type"] == "submission" and rows[1]["latency_ms"] >= 0


def test_data_outage_sends_no_order_and_keeps_a_rejection_record(tmp_path):
    client, posts = exchange(tmp_path, missing_quotes=True)
    leg = Leg("SPY", "SPY", "buy", 10, "day", "unique", 100)
    client.submit(leg)
    assert not posts and leg.status == "rejected" and "ACCOUNT RISK" in leg.note
    assert not Ledger(tmp_path / "ledger.jsonl").verify()[0]["allowed"]


def test_existing_order_is_recovered_even_when_current_quotes_are_missing(tmp_path):
    client, posts = exchange(tmp_path, missing_quotes=True, existing=True)
    leg = Leg("SPY", "SPY", "buy", 10, "day", "same-id", 100)
    client.submit(leg)
    assert not posts and leg.status == "filled" and leg.filled_qty == 10


def test_invalid_policy_rejected():
    with pytest.raises(ValueError):
        RiskPolicy(daily_loss=1)


def test_independent_risk_reads_overlap_but_all_finish_before_submission(tmp_path):
    import threading
    gate = threading.Barrier(3, timeout=2)
    client, posts = exchange(tmp_path)
    transport = client.c._transport
    def concurrent(request):
        if request.method == 'GET' and request.url.path in {'/v2/account', '/v2/positions', '/v2/orders'}:
            gate.wait()
        return transport.handle_request(request)
    original = client.c
    client.c = httpx.Client(transport=httpx.MockTransport(concurrent))
    try:
        leg = Leg('SPY', 'SPY', 'buy', 10, 'day', 'parallel-read', 100)
        client.submit(leg)
        assert len(posts) == 1 and leg.status == 'submitted'
    finally:
        client.c.close()
        original.close()
