"""Opt-in continuous paper workflow; scheduler priority, durable queue, no new timers."""
from __future__ import annotations

import csv
import fcntl
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import desktop_run as worker

from app.tools.live_articles import VERSION


def recover_retrieval_failures(queue: dict[str, Any]) -> int:
    """One engineering retry after this retrieval repair; retain every prior failure."""
    recovered = 0
    for company in queue['companies']:
        reason = company.get('error_reason') or ''
        if company.get('state') != 'failed' or company.get('retrieval_version') == VERSION:
            continue
        if reason not in {'insufficient original article coverage: need two non-SEC domains',
                          'no usable citation/number-checked facts after bounded brief repair'}:
            continue
        company.setdefault('attempt_history', []).append({k: v for k, v in company.items()
                                                        if k not in {'attempt_history', 'name', 'cik', 'sector'}})
        company.update(state='pending', retrieval_version=VERSION, retry_reason='original-page retrieval and brief schema repair')
        recovered += 1
    return recovered


def save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data) + '\n')
    temp.replace(path)


def load_queue(source: Path, path: Path, profile: str = "all1000") -> dict[str, Any]:
    if profile not in {"all1000", "tech100"}:
        raise ValueError("Unknown research profile")
    limit = 100 if profile == "tech100" else 1000
    if path.exists():
        return dict(json.loads(path.read_text()))
    rows = list(csv.DictReader(source.read_text().splitlines()))
    year = max(int(r['year']) for r in rows)
    seen: set[str] = set()
    companies = []
    for row in sorted(rows, key=lambda r: r['ticker']):
        if int(row['year']) != year or not row['cik'] or row['cik'] in seen:
            continue
        if profile == 'tech100' and row.get('sector') != 'Information Technology':
            continue
        seen.add(row['cik'])
        companies.append({'ticker': row['ticker'], 'name': row['name'], 'cik': row['cik'], 'sector': row.get('sector'), 'state': 'pending'})
        if len(companies) == limit:
            break
    result: dict[str, Any] = {'companies': companies, 'profile': profile, 'requested_count': limit, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                              'snapshot_year': year, 'selection': 'cached membership, alphabetical, unique CIK; not a point-in-time backtest universe'}
    save(path, result)
    return result


def run(job: Path, node: str, backend: Path = worker.BACKEND) -> int:
    status_path = job / 'status.json'
    status = json.loads(status_path.read_text())
    fwd = backend / 'results' / 'forward'
    state_dir = fwd / 'paper_autopilot'
    state_dir.mkdir(parents=True, exist_ok=True)
    status.update(state='running', pid=os.getpid(), identity=worker.identity(os.getpid()),
                  started_at=datetime.now(UTC).isoformat(), steps=[])

    def update(**changes: Any) -> None:
        status.update(changes)
        save(status_path, status)

    with (state_dir / 'controller.lock').open('a') as controller:
        try:
            fcntl.flock(controller, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            update(state='blocked', message='A continuous worker already exists.')
            return 2
        try:
            profile = 'tech100' if status.get('action') == 'paper_auto_tech100' else 'all1000'
            queue_path = state_dir / ('queue_tech100.json' if profile == 'tech100' else 'queue.json')
            queue = load_queue(backend / 'data' / 'events' / 'members_2024_2026.csv', queue_path, profile)
            recovered = recover_retrieval_failures(queue)
            if profile == "tech100":
                from app.portfolio.stock_universe import activate
                # Activate under the shared scheduler lock before any sync/submission.
                with (fwd / "autorun.lock").open("a") as policy_lock:
                    while True:
                        try:
                            fcntl.flock(policy_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            if (job / "STOP").exists():
                                update(state="stopped", message="Stopped before policy activation")
                                return 0
                            time.sleep(1)
                    activate(queue, state_dir / "stock_policy.json")
            update(research_profile=profile, recovered_retrieval_attempts=recovered)
            # An interrupted company resumes; completed/failed attempts are not silently rerun.
            for company in queue['companies']:
                if company['state'] == 'running':
                    company['state'] = 'pending'
                    company['interrupted'] = True
            save(queue_path, queue)
            due = dict.fromkeys(('paper_sync', 'paper_ai', 'improvement_review'), 0.0)
            intervals = {'paper_sync': 60, 'paper_ai': 900, 'improvement_review': 3600}
            sync_ok = False
            while not (job / 'STOP').exists():
                now = datetime.now(UTC)
                done = sum(c['state'] in ('completed', 'failed') for c in queue['companies'])
                update(progress={'attempted': done, 'total': len(queue['companies']),
                                 'failed': sum(c['state'] == 'failed' for c in queue['companies']),
                                 'previous_failed_attempts': sum(len(c.get('attempt_history', [])) for c in queue['companies'])},
                       message='Waiting for scheduler / next cycle. Sync every minute when idle; research can delay sync.')
                mode = fwd / 'AUTORUN_MODE'
                if not mode.exists() or mode.read_text().strip() != 'live':
                    update(message='Paper mode disabled; waiting without orders.')
                    time.sleep(2)
                    continue
                if (worker.next_slot(now) - now).total_seconds() < 360:
                    time.sleep(2)
                    continue
                with (fwd / 'autorun.lock').open('a') as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        time.sleep(2)
                        continue
                    action = next((a for a, deadline in due.items() if time.monotonic() >= deadline
                                   and not (a == 'paper_ai' and ((fwd / 'HALT').exists() or not sync_ok))
                                   and worker.preflight(a, fwd, now) is None), None)
                    batch: list[dict[str, Any]] = []
                    if action:
                        commands = worker.commands(action, node)
                    else:
                        candidates = [c for c in queue['companies'] if c['state'] == 'pending']
                        room = (worker.next_slot(now) - now).total_seconds()
                        batch = candidates[:2 if room >= 2700 else 1] if room >= 1800 and not (fwd / 'HALT').exists() else []
                        if not batch:
                            commands = []
                        else:
                            for company in batch:
                                company.update(state='running', started_at=now.isoformat(), retrieval_version=VERSION)
                            save(queue_path, queue)
                            names = {c['ticker']: c['name'] for c in batch}
                            commands = [('Deep research ' + ', '.join(names), [sys.executable, '-u',
                                        'scripts/live_research_test.py', '--deep', '--paper-sync-checkpoints', '--company-names-json', json.dumps(names),
                                        '--tickers', ','.join(names)])]
                    failed = 0
                    cycle_warning = False
                    attempted = False
                    started = time.monotonic()
                    for label, cmd in commands:
                        if (job / 'STOP').exists():
                            break
                        if action in worker.PAPER_ACTIONS and worker.preflight(action, fwd, datetime.now(UTC)):
                            failed = 2
                            break
                        available = (worker.next_slot(datetime.now(UTC)) - datetime.now(UTC)).total_seconds() - 300
                        if available <= 0:
                            failed = 2
                            break
                        update(message=label)
                        attempted = True
                        step_start = time.monotonic()
                        rc, warning = worker.execute(cmd, min(available, 2400 if batch else 1800), job / 'output.log', lock.fileno())
                        cycle_warning = cycle_warning or warning
                        status['steps'].append({'label': label, 'state': 'failed' if rc else 'warning' if warning else 'succeeded',
                                                'code': rc, 'elapsed_s': round(time.monotonic() - step_start, 2), 'finished_at': datetime.now(UTC).isoformat()})
                        status['steps'] = status['steps'][-40:]
                        update(last_result='failed' if rc else 'warning' if warning else 'succeeded')
                        if rc:
                            if action == 'paper_sync':
                                worker.execute([sys.executable, '-u', 'scripts/account_view.py'],
                                               min(available, 120), job / 'output.log', lock.fileno())
                            failed = rc
                            break
                    if action == 'paper_sync':
                        sync_ok = failed == 0 and not cycle_warning
                        update(sync_healthy=sync_ok)
                    if action:
                        due[action] = time.monotonic() + intervals[action]
                    if batch:
                        summaries = sorted((fwd / 'deep_research').glob('*/summary.json'))
                        summary: dict[str, Any] = {}
                        if summaries and attempted:
                            candidate = json.loads(summaries[-1].read_text())
                            if set(candidate.get('watchlist', [])) == {c['ticker'] for c in batch} and datetime.fromisoformat(candidate['created_at']) >= now:
                                summary = candidate
                        for company in batch:
                            row: dict[str, Any] = next((r for r in summary.get('companies', []) if r['ticker'] == company['ticker']), {})
                            if not attempted:
                                company['state'] = 'pending'
                            else:
                                ok = row.get('status') == 'decided'
                                company.update(state='completed' if ok else 'failed',
                                    elapsed_s=round(time.monotonic() - started, 2),
                                    research_s=row.get('research_s'), judge_s=row.get('judge_s'),
                                    error_reason=row.get('error_reason') or (None if ok else 'batch interrupted or research failed'),
                                    finished_at=datetime.now(UTC).isoformat(), code=failed)
                            if summary:
                                company['evidence_summary'] = str(summaries[-1])
                        save(queue_path, queue)
                time.sleep(2)
            update(state='stopped', message='Stopped after the current step. Start again to resume the queue.',
                   finished_at=datetime.now(UTC).isoformat())
            return 0
        except (OSError, ValueError, KeyError) as exc:
            update(state='failed', message=f'{type(exc).__name__}: {str(exc)[:200]}', finished_at=datetime.now(UTC).isoformat())
            return 1
