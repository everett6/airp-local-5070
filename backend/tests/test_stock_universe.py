"""Synthetic restrictions, replay and cancellation; no broker or market connections."""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ai_picks

from app.portfolio import sleeve
from app.portfolio.broker import PAPER, Alpaca, Leg
from app.portfolio.stock_universe import activate, allowed


def test_policy_is_bounded_hashed_and_normalizes_share_classes(tmp_path):
    path = tmp_path / 'policy.json'
    queue = {'profile': 'tech100', 'source_sha256': 'source', 'companies': [
        {'ticker': 'BRK.B', 'sector': 'Information Technology'}]}
    activate(queue, path)
    assert allowed(path) == {'BRK-B'}
    data = json.loads(path.read_text())
    data['symbols'] = ['OUTSIDE']
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='hash'):
        allowed(path)
    assert allowed(tmp_path / 'missing') is None


def test_outside_candidates_and_pending_entries_blocked_before_simulated_fill():
    now = datetime(2026, 10, 5, 12, tzinfo=UTC)
    prices = pd.DataFrame({'AAA': [100., 100.], 'XLK': [50., 50.]}, index=pd.bdate_range('2026-10-01', periods=2))
    decision = {'type': 'decision', 'ticker': 'AAA', 'sector': 'Information Technology', 'source': 'bonsai',
                'logodds': 4, 'accession': '1', 'entry_deadline': '2026-10-05T09:30:00-04:00'}
    st = sleeve.new_state()
    sleeve.step(st, [decision], prices, prices, {'Information Technology': 'XLK'}, now, allowed_stocks={'BBB'})
    assert st['pairs'][0]['status'] == 'skipped' and '100-stock' in st['pairs'][0]['note']
    st2 = sleeve.new_state()
    sleeve.step(st2, [decision], prices, prices, {'Information Technology': 'XLK'}, now)
    later = pd.DataFrame({'AAA': [100.] * 3, 'XLK': [50.] * 3}, index=pd.bdate_range('2026-10-01', periods=3))
    sleeve.step(st2, [], later, later, {'Information Technology': 'XLK'}, now, allowed_stocks={'BBB'})
    assert st2['pairs'][0]['status'] == 'skipped' and st2['cash'] == sleeve.CAPITAL


def test_cancel_only_the_owned_order_uses_paper_endpoint():
    calls = []
    def handle(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(204) if request.method == 'DELETE' else httpx.Response(200, json={'id': 'owned-order'})
    client = Alpaca('fake', 'fake', transport=httpx.MockTransport(handle))
    try:
        client.cancel_leg(Leg('AAA', 'AAA', 'buy', 1, 'day', 'owned-client-id', 100))
    finally:
        client.c.close()
    assert calls == [('GET', '/v2/orders:by_client_order_id'), ('DELETE', '/v2/orders/owned-order')]
    assert PAPER.startswith('https://paper-api.')


def test_restricted_mirror_never_submits_an_entry_or_its_hedge():
    class Broker:
        def submit(self, *_):
            raise AssertionError('restricted entry submitted')
    st = sleeve.new_state()
    st['pairs'] = [{'ticker': 'AAA', 'etf': 'XLK', 'accession': '1', 'qty': 1, 'etf_qty': 2,
                    'status': 'planned', 'entry_deadline': '2026-10-05T09:30:00-04:00'}]
    ai_picks.mirror(st, Broker(), pd.bdate_range('2026-10-01', periods=2),
                    datetime(2026, 10, 5, 12, tzinfo=UTC), True, 'ACTIVE', allowed_stocks={'BBB'})
    assert st['pairs'][0]['legs'] == []


def test_existing_outside_position_exits_remain_available():
    st = sleeve.new_state()
    st['pairs'] = [{'ticker': 'AAA', 'etf': 'XLK', 'accession': '1', 'qty': 1, 'etf_qty': 2,
        'status': 'skipped', 'legs': [{'client_order_id': 'owned-in-s', 'asset': 'AAA', 'symbol': 'AAA',
        'side': 'buy', 'qty': 1, 'tif': 'day', 'ref_price': 100, 'status': 'filled', 'filled_qty': 1,
        'filled_price': 100, 'filled_at': '2026-10-01T14:00:00Z'}]}]
    class Broker:
        def __init__(self):
            self.sent = []
        def submit(self, leg):
            self.sent.append((leg.asset, leg.side))
            leg.status = 'submitted'
    broker = Broker()
    ai_picks.mirror(st, broker, pd.bdate_range('2026-10-01', periods=2),
                    datetime(2026, 10, 5, 12, tzinfo=UTC), False, 'ACTIVE', {'AAA': 100}, {'BBB'})
    assert broker.sent == [('AAA', 'sell')]
