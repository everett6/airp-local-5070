"""Account tab reads only paper details and excludes unapproved response fields."""
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from alpaca_account import snapshot

from app.portfolio.broker import PAPER, Alpaca


def test_snapshot_is_read_only_masked_and_keeps_blocked_account_visible():
    calls = []

    def handle(request):
        calls.append((request.method, str(request.url)))
        return httpx.Response(200, json={"account_number": "123456789", "status": "ACTIVE",
                                       "equity": "98765.43", "trading_blocked": True,
                                       "secret": "never-display", "id": "private-id"})

    client = Alpaca("fake-key", "fake-secret", transport=httpx.MockTransport(handle))
    try:
        result = snapshot(client)
    finally:
        client.c.close()
    assert calls == [("GET", f"{PAPER}/account")]
    assert result["account_number"] == "••••6789"
    assert result["account"]["trading_blocked"] is True
    assert result["account"]["equity"] == "98765.43"
    assert "never-display" not in json.dumps(result)
    assert "private-id" not in json.dumps(result)
    assert "cash" not in result["account"]


def test_full_snapshot_reads_positions_orders_and_history_without_writes_or_secrets():
    calls = []
    def handle(request):
        calls.append(request.method)
        name = request.url.path.rsplit('/', 1)[-1]
        data = {'positions': [{'symbol': 'AAA', 'qty': '2', 'secret': 'omit'}],
                'orders': [{'symbol': 'AAA', 'status': 'filled', 'secret': 'omit'}],
                'history': {'timestamp': [1, 2], 'equity': [100, 101], 'secret': 'omit'},
                'account': {'status': 'ACTIVE', 'equity': '101'}}[name]
        return httpx.Response(200, json=data)
    client = Alpaca('fake', 'fake', transport=httpx.MockTransport(handle))
    try:
        result = snapshot(client, full=True)
    finally:
        client.c.close()
    assert calls == ['GET'] * 4
    assert result['positions'][0]['symbol'] == 'AAA'
    assert result['history']['equity'] == [100, 101]
    assert 'omit' not in json.dumps(result)
