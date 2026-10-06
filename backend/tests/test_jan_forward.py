"""Jan paper research gates and provenance with fake models, tools and GPU servers."""
from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import extract_events
import forward_events as FE
import jan_forward as J

from app.sandbox.agent_worker import brief_request, finish_brief


def brief(evidence: str, facts: list[dict[str, str]]) -> dict[str, Any]:
    _, _, tags = brief_request({"ticker": "AAA", "as_of": "2026-10-01", "horizon_days": 5}, evidence)
    return {"as_of": "2026-10-01T20:10:00+00:00", "brief": finish_brief(json.dumps({"facts": facts}), tags, evidence)}


def test_only_source_checked_numbers_and_nonfuture_facts_enter_sheet() -> None:
    evidence = "source https://www.sec.gov/a.htm revenue 100"
    facts = [{"text": "revenue 100", "source": "S1", "date": "2026-10-01"},
             {"text": "revenue 900", "source": "S1"},
             {"text": "unknown source", "source": "https://unknown.example/a"},
             {"text": "future fact", "source": "S1", "date": "2026-10-02"}]
    lines = J.checked_lines(brief(evidence, facts))
    assert len(lines) == 2 and "revenue 100" in lines[1] and "https://www.sec.gov/a.htm" in lines[1]


@pytest.mark.parametrize("raw", ["not JSON", '{"facts": [{"text": "cut off", "source": "S1"}'])
def test_unparsed_or_truncated_research_cannot_trade(raw: str) -> None:
    assert J.checked_lines({"as_of": "2026-10-01", "brief": finish_brief(raw, {}, "")}) == []


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"stopped": False, "unloaded": False, "closed": False, "researched": []}
    jan = tmp_path / "jan"
    jan.mkdir()
    ft = tmp_path / "features.csv"
    rows = [{"accession": a, "ticker": a.upper(), "accepted_utc": "2026-10-01T20:10:00", "cik": i,
             "ex99_url": "https://www.sec.gov/a.htm", "fact_sheet": "base sheet", "guidance": "none"}
            for i, a in enumerate(["a", "b"])]
    pd.DataFrame(rows).to_csv(ft, index=False)
    events = tmp_path / "events.csv"
    pd.DataFrame(rows).to_csv(events, index=False)
    (jan / "extract.jsonl").write_text("".join(json.dumps({"accession": a, "model": J.JAN, "parsed": True}) + "\n"
                                                for a in ["a", "b"]))
    monkeypatch.setattr(extract_events, "TEXT", tmp_path)
    for a in ["a", "b"]:
        extract_events.text_path(a).write_bytes(b"test source")

    class LLM:
        def __init__(self, *_a: Any, **kwargs: Any) -> None:
            assert kwargs["require_gpu"] and not kwargs["cache"]
        async def unload(self) -> None:
            state["unloaded"] = True

    class Gateway:
        sec_user_agent = "test"
        fetcher = type("Fetcher", (), {"user_agent": "test"})()
        @classmethod
        def from_env(cls, *_a: Any, **_k: Any) -> Gateway:
            return cls()
        def specs_native(self) -> list[Any]:
            return []
        async def aclose(self) -> None:
            pass

    class Fetcher:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass
        async def aclose(self) -> None:
            state["closed"] = True

    class Server:
        def __init__(self, *_a: Any) -> None:
            pass
        def stop(self) -> None:
            state["stopped"] = True

    async def research(r: Any, *_a: Any) -> dict[str, Any]:
        state["researched"].append(r.accession)
        if r.accession == "b":
            raise TimeoutError
        return {**brief("https://www.sec.gov/a.htm revenue 100",
                        [{"text": "revenue 100", "source": "S1"}]), "evidence": "saved evidence"}

    monkeypatch.setattr(J, "OllamaLLM", LLM)
    monkeypatch.setattr(J, "ToolGateway", Gateway)
    monkeypatch.setattr(J, "SafeFetcher", Fetcher)
    monkeypatch.setattr(FE, "Ollama", Server)
    monkeypatch.setattr(J, "gpu_job", lambda *_a: contextlib.nullcontext())
    monkeypatch.setattr(J, "research_one", research)
    state.update(dir=tmp_path, features=ft, events=events)
    return state


def test_success_is_saved_individually_and_failure_is_excluded(setup: dict[str, Any]) -> None:
    clock = lambda: datetime(2026, 10, 1, 22, 30, tzinfo=UTC)
    path, skip, provenance = asyncio.run(J.gather(setup["features"], setup["events"], None,
                                                 setup["dir"], clock, "model-digest"))
    assert list(pd.read_csv(path)["accession"]) == ["a"]
    assert skip == {"b": "Jan research failed: TimeoutError"}
    body = (setup["dir"] / provenance["a"]["research_path"]).read_text()
    assert J.sha(body) == provenance["a"]["research_sha256"]
    rec = json.loads(body)
    assert rec["model"] == J.JAN and rec["digest"] == "model-digest"
    assert rec["source_sha256"] == J.sha(b"test source") and rec["base_fact_sheet"] == "base sheet"
    assert all(setup[k] for k in ["unloaded", "stopped", "closed"])


def test_failed_extraction_never_becomes_a_researched_decision(setup: dict[str, Any]) -> None:
    (setup["dir"] / "jan" / "extract.jsonl").write_text("")
    _, skip, provenance = asyncio.run(J.gather(setup["features"], setup["events"], None, setup["dir"],
                                             lambda: datetime(2026, 10, 1, 22, 30, tzinfo=UTC), "digest"))
    assert not provenance and not setup["researched"]
    assert set(skip) == {"a", "b"}


def test_deadline_reserves_time_for_bonsai(setup: dict[str, Any]) -> None:
    _, skip, provenance = asyncio.run(J.gather(setup["features"], setup["events"], None, setup["dir"],
                                             lambda: datetime(2026, 10, 2, 13, 28, tzinfo=UTC), "digest"))
    assert not provenance and not setup["researched"]
    assert all("insufficient time" in x for x in skip.values())


def test_missing_models_fail_before_server_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import evidence_bundle
    monkeypatch.setattr(evidence_bundle, "manifest_digest", lambda *_a: None)
    monkeypatch.setattr(FE, "Ollama", lambda *_a: pytest.fail("server started without installed models"))
    with pytest.raises(RuntimeError, match="already be installed"):
        J.prepare(tmp_path, tmp_path / "events.csv", datetime.now(UTC).date(), "tag", tmp_path / "prices", None,
                  lambda: datetime.now(UTC))


def test_research_requests_use_five_day_horizon_and_save_exact_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    import research_events
    calls = []

    class LLM:
        num_ctx, num_predict = 8192, 1200
        async def __call__(self, system: str, user: str, **kwargs: Any) -> str:
            calls.append((system, user))
            return json.dumps({"facts": [{"text": "revenue 100", "source": "S1"}]})

    async def research(r: Any, router: Any, *_a: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["horizon_days"] == 5 and not kwargs["brief"]
        await router("exact research system", "exact research user")
        return {"as_of": "2026-10-01T20:10:00+00:00", "evidence": "https://www.sec.gov/a.htm revenue 100"}

    monkeypatch.setattr(research_events, "research", research)
    rec = asyncio.run(J.research_one(SimpleNamespace(ticker="AAA"), LLM(), None, "test", None, []))
    assert len(rec["llm_calls"]) == 2
    assert rec["llm_calls"][0]["system"] == "exact research system"
    assert "Horizon: 5 trading days" in rec["llm_calls"][1]["user"]
    assert J.checked_lines(rec) and len(calls) == 2
