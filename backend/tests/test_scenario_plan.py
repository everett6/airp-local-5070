"""Hand-calculated virtual budget cases; no market data or measured profitability assertions."""
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from trading_plan import report

from app.portfolio.scenario_plan import HORIZONS, plan


def test_allocations_and_costed_flat_case_are_bounded():
    rows = [{'ticker': f'T{i}', 'status': 'decided', 'ratings': dict.fromkeys(HORIZONS, 5)} for i in range(10)]
    p = plan(rows)
    assert sum(p['allocation'].values()) == pytest.approx(.9)
    assert p['cash'] == pytest.approx(.1)
    for row in rows:
        assert sum(w.get(row['ticker'], 0) for w in p['weights'].values()) <= .1 + 1e-12
    flat = next(c for c in p['cases'] if c['name'] == 'flat')
    assert flat['net_return'] == pytest.approx(-.9 * .004)
    assert flat['pnl'] == pytest.approx(-36.)
    assert p['probabilities'] is None and p['estimated_gain'] is None


def test_failed_missing_and_pass_ratings_hold_cash():
    p = plan([{'ticker': 'FAIL', 'status': 'research_failed', 'ratings': dict.fromkeys(HORIZONS, 5)},
              {'ticker': 'PASS', 'status': 'decided', 'ratings': {'day': 3}}])
    assert p['cash'] == 1 and all(c['pnl'] == 0 for c in p['cases'])
    with pytest.raises(ValueError):
        plan([], capital=float('nan'))


def test_planner_filters_universe_future_and_stale_evidence(tmp_path):
    now = datetime(2026, 10, 4, 12, tzinfo=UTC)
    directory = tmp_path / 'paper_autopilot'
    directory.mkdir()
    (directory / 'queue_tech100.json').write_text(json.dumps({'companies': [{'ticker': 'AAA', 'state': 'pending'}]}))
    for name, when, ticker in [('fresh', now - timedelta(hours=1), 'AAA'), ('stale', now - timedelta(hours=49), 'AAA'),
                               ('future', now + timedelta(hours=1), 'AAA'), ('outside', now, 'BBB')]:
        out = tmp_path / 'deep_research' / name
        out.mkdir(parents=True)
        (out / 'summary.json').write_text(json.dumps({'created_at': when.isoformat(), 'companies': [
            {'ticker': ticker, 'status': 'decided', 'ratings': {'short': 5}}]}))
    d = report(tmp_path, now)
    assert len(d['evidence']) == 1 and d['evidence'][0]['ticker'] == 'AAA'
    assert len(d['plans']) == 3 and d['returns_measured'] is False
