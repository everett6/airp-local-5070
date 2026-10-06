"""Continuous controller recovery, synthetic sources and fake child processes only."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import paper_autopilot as auto


def test_retrieval_repair_retries_once_and_retains_failed_evidence():
    reason = 'insufficient original article coverage: need two non-SEC domains'
    queue = {'companies': [{'ticker': 'A', 'state': 'failed', 'error_reason': reason, 'evidence_summary': 'old.json'},
                           {'ticker': 'B', 'state': 'completed'},
                           {'ticker': 'C', 'state': 'failed', 'error_reason': 'broker rejected'},
                           {'ticker': 'D', 'state': 'failed', 'error_reason': reason, 'retrieval_version': auto.VERSION}]}
    assert auto.recover_retrieval_failures(queue) == 1
    company = queue['companies'][0]
    assert company['state'] == 'pending' and company['attempt_history'][0]['evidence_summary'] == 'old.json'
    company['state'] = 'failed'
    assert auto.recover_retrieval_failures(queue) == 0
    assert queue['companies'][1]['state'] == 'completed' and queue['companies'][2]['state'] == 'failed'


def test_queue_deduplicates_companies_freezes_snapshot_and_resumes(tmp_path):
    source = tmp_path / 'members.csv'
    source.write_text('ticker,name,cik,year\nOLD,Old,9,2024\nB,Beta,2,2026\nA,Alpha,1,2026\nAA,Alpha shares,1,2026\n')
    path = tmp_path / 'queue.json'
    queue = auto.load_queue(source, path)
    assert [c['ticker'] for c in queue['companies']] == ['A', 'B']
    queue['companies'][0]['state'] = 'completed'
    auto.save(path, queue)
    source.write_text('broken')
    assert auto.load_queue(source, path)['companies'][0]['state'] == 'completed'


def test_controller_sync_failure_preserves_evidence_then_stops(tmp_path, monkeypatch):
    fwd = tmp_path / 'results' / 'forward'
    fwd.mkdir(parents=True)
    (fwd / 'AUTORUN_MODE').write_text('live')
    source = tmp_path / 'data' / 'events' / 'members_2024_2026.csv'
    source.parent.mkdir(parents=True)
    source.write_text('ticker,name,cik,year\nA,Alpha,1,2026\n')
    job = tmp_path / 'job'
    job.mkdir()
    (job / 'status.json').write_text('{"action":"paper_auto"}')
    monkeypatch.setattr(auto.worker, 'next_slot', lambda now: now.replace(year=now.year + 1))
    def fail(*args):
        (job / 'STOP').write_text('stop')
        return 1, True
    monkeypatch.setattr(auto.worker, 'execute', fail)
    monkeypatch.setattr(auto.time, 'sleep', lambda _: None)
    assert auto.run(job, 'node', tmp_path) == 0
    status = json.loads((job / 'status.json').read_text())
    assert status['state'] == 'stopped' and status['last_result'] == 'failed'
    assert len(status['steps']) == 1
    assert status['progress']['attempted'] == 0
    assert not (fwd / 'heartbeat.jsonl').exists()


def test_queue_caps_at_one_thousand_unique_companies(tmp_path):
    source = tmp_path / 'members.csv'
    source.write_text('ticker,name,cik,year\n' + ''.join(f'S{i:04d},Company {i},{i+1},2026\n' for i in range(1100)))
    assert len(auto.load_queue(source, tmp_path / 'queue.json')['companies']) == 1000


def test_scheduler_window_waits_without_holding_execution_lock(tmp_path, monkeypatch):
    import fcntl
    from datetime import timedelta
    fwd = tmp_path / 'results' / 'forward'
    fwd.mkdir(parents=True)
    (fwd / 'AUTORUN_MODE').write_text('live')
    source = tmp_path / 'data' / 'events' / 'members_2024_2026.csv'
    source.parent.mkdir(parents=True)
    source.write_text('ticker,name,cik,year\nA,Alpha,1,2026\n')
    job = tmp_path / 'job'
    job.mkdir()
    (job / 'status.json').write_text('{"action":"paper_auto"}')
    monkeypatch.setattr(auto.worker, 'next_slot', lambda now: now + timedelta(minutes=5))
    monkeypatch.setattr(auto.worker, 'execute', lambda *_: (_ for _ in ()).throw(AssertionError('started near timer')))
    def stop(_):
        with (fwd / 'autorun.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        (job / 'STOP').write_text('stop')
    monkeypatch.setattr(auto.time, 'sleep', stop)
    assert auto.run(job, 'node', tmp_path) == 0


def test_tech_profile_excludes_other_sectors_caps_and_keeps_separate_progress(tmp_path):
    source = tmp_path / 'members.csv'
    source.write_text('ticker,name,cik,year,sector\n' +
                      ''.join(f'T{i:03d},Tech {i},{i+1},2026,Information Technology\n' for i in range(110)) +
                      'BANK,Bank,900,2026,Financials\nOLD,Old tech,901,2024,Information Technology\n')
    all_path, tech_path = tmp_path / 'all.json', tmp_path / 'tech.json'
    broad = auto.load_queue(source, all_path)
    tech = auto.load_queue(source, tech_path, 'tech100')
    assert len(tech['companies']) == 100
    assert all(c['sector'] == 'Information Technology' for c in tech['companies'])
    assert not {'BANK', 'OLD'} & {c['ticker'] for c in tech['companies']}
    tech['companies'][0]['state'] = 'completed'
    auto.save(tech_path, tech)
    assert auto.load_queue(source, tech_path, 'tech100')['companies'][0]['state'] == 'completed'
    assert auto.load_queue(source, all_path) == broad


def test_batch_preserves_each_company_result_instead_of_marking_everyone_failed(tmp_path, monkeypatch):
    from datetime import UTC, datetime
    fwd = tmp_path / 'results' / 'forward'
    fwd.mkdir(parents=True)
    (fwd / 'AUTORUN_MODE').write_text('live')
    source = tmp_path / 'data' / 'events' / 'members_2024_2026.csv'
    source.parent.mkdir(parents=True)
    source.write_text('ticker,name,cik,year\nA,Alpha,1,2026\nB,Beta,2,2026\n')
    job = tmp_path / 'job'
    job.mkdir()
    (job / 'status.json').write_text('{"action":"paper_auto"}')
    monkeypatch.setattr(auto.worker, 'next_slot', lambda now: now.replace(year=now.year + 1))
    def execute(cmd, *_):
        if 'scripts/live_research_test.py' in cmd:
            assert cmd[cmd.index('--tickers') + 1] == 'A,B'
            assert '--paper-sync-checkpoints' in cmd
            out = fwd / 'deep_research' / 'batch'
            out.mkdir(parents=True)
            (out / 'summary.json').write_text(json.dumps({'watchlist': ['A', 'B'], 'created_at': datetime.now(UTC).isoformat(),
                'companies': [{'ticker': 'A', 'status': 'decided', 'research_s': 5, 'judge_s': 2},
                              {'ticker': 'B', 'status': 'research_failed', 'error_reason': 'missing sources'}]}))
            (job / 'STOP').write_text('stop')
            return 0, True
        return 0, False
    monkeypatch.setattr(auto.worker, 'execute', execute)
    monkeypatch.setattr(auto.time, 'sleep', lambda _: None)
    assert auto.run(job, 'node', tmp_path) == 0
    queue = json.loads((fwd / 'paper_autopilot' / 'queue.json').read_text())
    assert [c['state'] for c in queue['companies']] == ['completed', 'failed']
    assert queue['companies'][0]['research_s'] == 5
    assert queue['companies'][1]['error_reason'] == 'missing sources'
