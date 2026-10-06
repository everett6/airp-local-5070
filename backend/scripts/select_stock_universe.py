"""Activate the user-requested 100-tech-stock entry restriction from cached membership only."""
from __future__ import annotations

import fcntl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paper_autopilot import load_queue

from app.portfolio.stock_universe import activate, allowed

BACKEND = Path(__file__).resolve().parents[1]


if __name__ == '__main__':
    fwd = BACKEND / 'results' / 'forward'
    directory = fwd / 'paper_autopilot'
    directory.mkdir(parents=True, exist_ok=True)
    with (fwd / 'autorun.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Another job is running; stock-policy activation deferred.') from None
        queue = load_queue(BACKEND / 'data' / 'events' / 'members_2024_2026.csv', directory / 'queue_tech100.json', 'tech100')
        activate(queue, directory / 'stock_policy.json')
        print(f"AI stock-entry restriction activated: {len(allowed(directory / 'stock_policy.json') or [])} cached technology stocks. No orders submitted.")
