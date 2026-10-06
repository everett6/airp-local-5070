"""Learning dashboard and bounded reviews: synthetic records only, no trials, GPU or broker calls."""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.forward.ledger import Ledger, LedgerError
from app.signals import registry as R

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import improvement_report as I

NOW = datetime(2026, 10, 2, 15, tzinfo=UTC)


def test_empty_engines_show_baseline_and_fixed_gates_without_writing(tmp_path):
    events, signals = tmp_path / "events", tmp_path / "signals"
    result = I.report(events, signals, NOW)
    assert result["scope"] == "shadow" and not result["live_strategy_changed"]
    assert result["prompt"]["champion"]["v"] == 0
    assert result["prompt"]["remaining"] == I.self_improve.MIN_MATURED
    assert result["recipes"]["signals"] == []
    assert result["recipes"]["gates"]["min_events"] == R.LIVE_MIN_EVENTS
    assert result["errors"] == []
    assert list(tmp_path.iterdir()) == []


def test_registry_stages_and_failed_evidence_remain_visible(tmp_path):
    signals = tmp_path / "signals"
    reg = R.Registry(signals / "registry.json")
    sig = R.validate({"name": "weak", "terms": [{"field": "momentum", "sign": 1}]})
    sig.status = "rejected_holdout"
    sig.note("2026-10-01", "holdout", passed=False, mean_ic=-0.04)
    reg.add(sig)
    reg.save()
    result = I.report(tmp_path / "events", signals, NOW)
    assert result["recipes"]["counts"]["rejected_holdout"] == 1
    assert result["recipes"]["signals"][0]["history"][0]["passed"] is False
    assert result["recipes"]["signals"][0]["status"] == "rejected_holdout"


def test_paired_forward_evidence_and_lessons_are_reported(tmp_path):
    from test_self_improve import LATER, _setup, _vs
    events = _setup(tmp_path, n=60)
    I.self_improve.save(events, _vs())
    result = I.report(events, tmp_path / "signals", LATER)
    prompt = result["prompt"]
    assert prompt["matured"] == 60 and prompt["remaining"] == 0
    assert prompt["challenger"]["lessons"] == ["x"]
    assert prompt["comparison"]["paired"] == 60
    assert prompt["comparison"]["e_better"] > 1


def test_corrupt_versions_are_an_error_and_are_not_repaired_silently(tmp_path):
    events = tmp_path / "events"
    events.mkdir()
    p = events / "bb_versions.jsonl"
    p.write_text("broken")
    result = I.report(events, tmp_path / "signals", NOW)
    assert not result["prompt"]["available"] and result["errors"]
    assert p.read_text() == "broken"


def test_broken_ledger_prevents_review_before_any_mutation(tmp_path, monkeypatch):
    events = tmp_path / "events"
    ledger = Ledger(events / "ledger.jsonl")
    ledger.append("run", as_of=NOW.isoformat())
    p = events / "ledger.jsonl"
    p.write_text(p.read_text().replace('"run"', '"oops"'))
    monkeypatch.setattr(I.self_improve, "save", lambda *a: pytest.fail("wrote versions"))
    monkeypatch.setattr(I.learn_loop, "collect", lambda *a: pytest.fail("collected features"))
    with pytest.raises(LedgerError):
        I.review_existing(events, tmp_path / "signals", NOW)
    assert I.report(events, tmp_path / "signals", NOW)["errors"]


def test_review_collects_and_judges_existing_shadows_never_generates_new_trials(tmp_path, monkeypatch):
    events = tmp_path / "events"
    Ledger(events / "ledger.jsonl").append("run", as_of=NOW.isoformat())
    calls = []
    monkeypatch.setattr(I.self_improve, "judge", lambda *a: calls.append("judge"))
    monkeypatch.setattr(I.learn_loop, "collect", lambda *a: calls.append("collect"))
    monkeypatch.setattr(I.learn_loop, "review", lambda *a, **k: calls.append("review"))
    monkeypatch.setattr(I.self_improve, "reflect", lambda *a: pytest.fail("reflection launched"))
    monkeypatch.setattr(I.learn_loop, "monthly", lambda *a: pytest.fail("trial launched"))
    monkeypatch.setattr("app.sandbox.dsr.register", lambda *a: pytest.fail("registered a trial"))
    result = I.review_existing(events, tmp_path / "signals", NOW)
    assert calls == ["judge", "collect", "review"]
    assert not result["live_strategy_changed"]
    assert json.loads((events / "bb_versions.jsonl").read_text())["v"] == 0


def test_automatic_review_records_no_change_without_claiming_live_promotion(tmp_path, monkeypatch):
    events, signals = tmp_path / 'events', tmp_path / 'signals'
    Ledger(events / 'ledger.jsonl').append('run', as_of=NOW.isoformat())
    monkeypatch.setattr(I.self_improve, 'judge', lambda *a: None)
    monkeypatch.setattr(I.learn_loop, 'collect', lambda *a: None)
    monkeypatch.setattr(I.learn_loop, 'review', lambda *a, **k: None)
    result = I.review_existing(events, signals, NOW)
    audit = Ledger(signals / 'automation.jsonl').verify()
    assert audit[-1]['status'] == 'reviewed'
    assert not audit[-1]['live_strategy_changed'] and not audit[-1]['prompt_changed']
    assert audit[-1]['signal_transitions'] == []
    assert len(result['automatic_reviews']) == 1
