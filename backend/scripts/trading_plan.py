"""Read-only planning dashboard from the frozen tech queue and completed deep evidence."""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.portfolio.scenario_plan import PROFILES, plan
from app.portfolio.stock_universe import allowed

BACKEND = Path(__file__).resolve().parents[1]


def report(fwd: Path, now: datetime) -> dict[str, Any]:
    directory = fwd / 'paper_autopilot'
    queue_file = directory / 'queue_tech100.json'
    queue = json.loads(queue_file.read_text()) if queue_file.exists() else {'companies': []}
    companies = {c['ticker'] for c in queue['companies']}
    latest: dict[str, Any] = {}
    diagnostics: list[str] = []
    for source in sorted((fwd / 'deep_research').glob('*/summary.json'), reverse=True):
        try:
            data = json.loads(source.read_text())
            stamp = datetime.fromisoformat(data['created_at'])
            if stamp > now or now - stamp > timedelta(hours=48):
                continue
            for row in data['companies']:
                if row['ticker'] in companies and row['ticker'] not in latest:
                    latest[row['ticker']] = {**row, 'evidence_summary': str(source), 'as_of': data['created_at']}
        except (OSError, ValueError, KeyError, TypeError):
            diagnostics.append('Unreadable research summary: ' + source.parent.name)
    policy_error = None
    try:
        universe = allowed(directory / 'stock_policy.json')
    except (OSError, ValueError, KeyError, TypeError):
        universe = set()
        policy_error = 'Invalid stock-entry policy: new AI entries must remain blocked.'
    rows = list(latest.values())
    return {'at': now.isoformat(), 'universe': queue['companies'], 'policy_active': universe is not None,
            'allowed_symbols': sorted(universe) if universe is not None else [], 'policy_error': policy_error,
            'evidence': rows, 'freshness_hours': 48, 'diagnostics': diagnostics,
            'plans': [plan(rows, profile=p) for p in PROFILES], 'returns_measured': False}


if __name__ == '__main__':
    try:
        print(json.dumps(report(BACKEND / 'results' / 'forward', datetime.now(UTC))))
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({'error': 'Planning records unavailable or malformed; no planning data used for orders.'}))
