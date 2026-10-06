"""The evidence bundle of a live decision (scripts/evidence_bundle.py): written once, hashed, checkable."""
from __future__ import annotations

import gzip
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evidence_bundle as E
import extract_events

from app.forward.ledger import Ledger

SHEET = "Company: AAA (Industrials); earnings release filed 2026-10-01T20:10 UTC.\nRevenue: 10M"


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "results" / "forward" / "events"
    monkeypatch.setattr(E, "BACKEND", tmp_path)
    monkeypatch.setattr(extract_events, "TEXT", tmp_path / "text")
    (tmp_path / "text").mkdir()
    (tmp_path / "results" / "events").mkdir(parents=True)
    monkeypatch.setattr(E, "code_version", lambda: {"commit": "abc", "uncommitted_code": False, "runner_sha256": "r"})
    monkeypatch.setattr(E, "manifest_digest", lambda model, _dir: "digest-of-" + model)
    led = Ledger(d / "ledger.jsonl")
    for acc, src in (("a", "bonsai"), ("b", "lite")):
        led.append("decision", accession=acc, ticker=acc.upper() * 3, sector="Industrials",
                   accepted_utc="2026-10-01T20:10:00", entry_deadline="2026-10-02T09:30:00-04:00",
                   as_of="2026-10-01T22:30:00+00:00", guidance="raised", source=src, logodds=3.5, on_time=True)
    led.append("missed", accession="c", ticker="CCC", reason="no decision")
    pd.DataFrame([{"accession": a, "ex99_url": f"https://www.sec.gov/{a}.htm", "items": "2.02", "filed": "2026-10-01"}
                  for a in "abc"]).to_csv(d / "events.csv", index=False)
    (d / "extract.jsonl").write_text(json.dumps({"accession": "a", "model": "qwen3:8b", "eps": {"q": 1.0}}) + "\n")
    (tmp_path / "results" / "events" / "decide_bonsai-27b_latest_forward_h5.jsonl").write_text(
        json.dumps({"accession": "a", "logodds": 3.50004, "mass": 0.99, "censored": False, "prompt_user": SHEET}) + "\n")
    extract_events.text_path("a").write_bytes(gzip.compress(b"the press release"))
    return d


def test_a_bundle_holds_what_rebuilds_the_decision(live: Path) -> None:
    now = datetime.fromisoformat(Ledger(live / "ledger.jsonl").records()[0]["written_at"])
    assert E.write_new(live, now=now) == ["a", "b"]  # one per decision; a missed release has none
    b = json.loads((live / "evidence" / "a.json").read_text())
    assert b["entry"]["session"] == "2026-10-02" and b["ledger"]["logodds"] == 3.5 and b["ledger"]["matches_judge"]
    assert b["source"]["ex99_url"].endswith("/a.htm") and b["source"]["text_sha256"] == E.sha("the press release")
    assert b["source"]["retrieved_at"] and b["source"]["text_chars"] == 17
    assert b["fact_sheet"] == {"text": SHEET, "sha256": E.sha(SHEET), "version": 1}
    assert "first weekday" in b["entry"]["rule"] and "figures" not in b  # a decision from before the rule change
    assert b["judge"]["digest"] == "digest-of-bonsai-27b:latest" and b["judge"]["mode"] == "buypass_lo"
    assert len(b["judge"]["prompt_version"]) == 12 and b["judge"]["horizon_days"] == 5
    assert b["reader"]["digest"] == "digest-of-qwen3:8b" and b["reader"]["record"]["eps"] == {"q": 1.0}
    assert b["code"]["commit"] == "abc" and b["strategy"]["threshold"] == 2.873 and b["backfilled"] is False
    lite = json.loads((live / "evidence" / "b.json").read_text())
    assert "bonsai-lite" in lite["judge"]["model"] and "fact_sheet" not in lite and lite["source"]["file"] is None
    assert E.write_new(live) == []  # written once
    assert E.check(live) == []


def test_the_check_finds_what_changed_afterwards(live: Path) -> None:
    E.write_new(live)
    assert json.loads((live / "evidence" / "a.json").read_text())["backfilled"] is False
    extract_events.text_path("a").write_bytes(gzip.compress(b"a different release"))  # the source was replaced
    f = live / "evidence" / "b.json"
    f.write_text(f.read_text().replace('"logodds": 3.5', '"logodds": 9.9'))            # a bundle was edited
    problems = E.check(live)
    assert any("a: the press release on disk is not the one" in p for p in problems)
    assert any("b: the bundle is not the one that was indexed" in p for p in problems)
    (live / "evidence" / "a.json").unlink()
    assert any(p == "a: no bundle" for p in E.check(live))


def test_an_old_decision_is_marked_backfilled(live: Path) -> None:
    E.write_new(live, now=datetime(2026, 10, 9, tzinfo=UTC))
    assert json.loads((live / "evidence" / "a.json").read_text())["backfilled"] is True


def test_the_recorded_settings_are_the_ones_the_scripts_use() -> None:
    """The bundle states the models' settings as constants. If a script changes them, this fails until both agree."""
    root = Path(__file__).resolve().parents[1] / "scripts"
    runner, judge, reader = ((root / n).read_text() for n in ("forward_events.py", "decide_events.py",
                                                             "extract_events.py"))
    assert f'"--model", "{E.READER["model"]}"' in runner and f'"--parallel", "{E.READER["parallel"]}"' in runner
    assert E.READER["models_dir"] in runner and "Ollama(11435, str(Path.home() / \".ollama\" / \"models\"), 3" in runner
    assert f"num_ctx={E.READER['num_ctx']}, num_predict={E.READER['num_predict']}" in reader
    assert f"MAX_CHARS = {E.READER['max_chars']}" in reader
    assert f"num_ctx={E.JUDGE['num_ctx']}" in judge and f'mode="{E.JUDGE["mode"]}"' in judge
    assert f'default="{E.JUDGE["model"]}"' in judge and "--horizon\", str(H)" in runner
    wf = (root.parent / "app" / "sandbox" / "walkforward.py").read_text()
    assert 'body["options"]["num_predict"] = 1\n            body |= {"logprobs": True, "top_logprobs": 20}' in wf
    assert E.manifest_digest("no-such-model", "/nonexistent") is None


def test_a_bundle_carries_the_session_the_times_and_the_fact_sheet_version(live: Path, tmp_path: Path) -> None:
    """Decisions since the evening of 1 Oct 2026: one entry rule, on time at the write, fact sheet version 2."""
    led = Ledger(live / "ledger.jsonl")
    led.append("decision", accession="d", ticker="DDD", sector="Industrials", accepted_utc="2026-11-03T13:10:00",
               entry_deadline="2026-11-04T09:30:00-05:00", entry_session="2026-11-04", sheet_version=2,
               as_of="2026-11-03T13:45:00+00:00", decided_at="2026-11-03T13:47:10+00:00", guidance="none",
               source="bonsai", logodds=1.0, on_time=True)
    with (tmp_path / "results" / "events" / "decide_bonsai-27b_latest_forward_h5.jsonl").open("a") as f:
        f.write(json.dumps({"accession": "d", "logodds": 1.0, "mass": 0.9, "censored": False, "prompt_user": SHEET}) + "\n")
    figures = {"revenue": {"q": 54229.0, "prior": 11315.0, "prior_source": "SEC filing", "reconcile": "previous_quarter"}}
    pd.DataFrame([{"accession": "d", "figures": json.dumps(figures)}, {"accession": "x", "figures": float("nan")}]
                 ).to_csv(tmp_path / "results" / "events" / "features_forward.csv", index=False)
    assert "d" in E.write_new(live)
    b = json.loads((live / "evidence" / "d.json").read_text())
    assert b["entry"]["session"] == "2026-11-04" and "13:00 UTC" in b["entry"]["rule"]
    assert b["ledger"]["decided_at"] == "2026-11-03T13:47:10+00:00" and b["ledger"]["sheet_version"] == 2
    assert b["fact_sheet"]["version"] == 2 and b["figures"] == figures
    assert E.check(live) == []


def test_jan_decision_uses_its_actual_research_and_judge(live: Path) -> None:
    from jan_forward import JAN
    jan = live / "jan"
    jan.mkdir()
    research = {"model": JAN, "digest": "jan-at-decision", "evidence": "source text", "brief": {"facts": []}}
    body = json.dumps(research) + "\n"
    (jan / "research-j.json").write_text(body)
    (jan / "extract.jsonl").write_text(json.dumps({"accession": "j", "model": JAN, "parsed": True}) + "\n")
    (E.BACKEND / "results" / "events" / "decide_bonsai-27b_latest_forward_jan_test_h5.jsonl").write_text(
        json.dumps({"accession": "j", "logodds": 3.5, "prompt_user": "base + Jan research"}) + "\n")
    Ledger(live / "ledger.jsonl").append("decision", accession="j", ticker="JJJ", sector="Industrials",
        accepted_utc="2026-10-01T20:10:00", entry_deadline="2026-10-02T09:30:00-04:00", source="bonsai",
        logodds=3.5, on_time=True, pipeline="jan_bonsai_v1", judge_tag="forward_jan_test",
        reader_record_path="jan/extract.jsonl", research_path="jan/research-j.json", research_sha256=E.sha(body))
    E.write_new(live)
    bundle = json.loads((live / "evidence" / "j.json").read_text())
    assert bundle["reader"]["model"] == JAN and bundle["reader"]["parallel"] == 1
    assert bundle["research"]["record"] == research and bundle["research"]["digest"] == "jan-at-decision"
    assert bundle["fact_sheet"]["text"] == "base + Jan research" and bundle["ledger"]["matches_judge"]
    assert E.check(live) == []
    (jan / "research-j.json").write_text("changed")
    assert any("Jan research differs" in x for x in E.check(live))
    (live / "evidence" / "j.json").unlink()
    with pytest.raises(ValueError, match="research changed"):
        E.write_new(live)
