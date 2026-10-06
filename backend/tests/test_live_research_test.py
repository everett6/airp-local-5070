"""Prospective experiment controls; all web, models and GPU servers are simulated."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Self

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import desktop_run as D
import live_research_test as L

from app.forward.ledger import Ledger


def test_capital_cannot_exceed_sleeve_or_company_limits() -> None:
    rows = [{"ticker": str(i), "status": "decided", "ratings": dict.fromkeys(L.CAPS, 5)} for i in range(10)]
    portfolio = L.targets(rows)
    assert portfolio["cash"] >= 0.1
    for h, weights in portfolio["weights"].items():
        assert sum(weights.values()) <= L.CAPS[h] + 1e-12
    for r in rows:
        assert sum(w.get(r["ticker"], 0) for w in portfolio["weights"].values()) <= 0.1 + 1e-12
    assert portfolio["returns"] is None and portfolio["incremental_net_return"] is None


def test_failures_and_passes_do_not_receive_money_or_disappear_from_control() -> None:
    rows = [{"ticker": "AAA", "status": "research_failed"},
            {"ticker": "BBB", "status": "decided", "ratings": dict.fromkeys(L.CAPS, 3)}]
    p = L.targets(rows)
    assert p["cash"] == 1 and not any(p["weights"].values())
    assert set(p["no_ai_weights"]) == {"AAA", "BBB"}
    with pytest.raises(ValueError, match="duplicate"):
        L.targets(rows + rows)


def test_failed_attempt_latency_is_not_hidden() -> None:
    rows = [{"status": "decided", "research_s": 10, "total_latency_s": 20},
            {"status": "research_failed", "research_s": 180, "total_latency_s": 200}]
    s = L.timing_stats(rows)
    assert s["all_attempts"]["research_s"] == {"n": 2, "median": 95, "p95": 180, "max": 180}
    assert s["decided"]["research_s"]["median"] == 10
    assert L.timing_stats([]) == {"all_attempts": {}, "decided": {}}


@pytest.fixture
def models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"servers": [], "unloaded": [], "gateway_closed": 0, "fail": "MSFT"}

    class Fetcher:
        async def aclose(self) -> None:
            state["gateway_closed"] += 1

    def gateway(*_a: Any, **kw: Any) -> Any:
        assert kw["tool_cache"] is None
        return L.FreeLiveGateway(mode="live", fetcher=Fetcher(), sec_user_agent="test")

    class Model:
        num_ctx, num_predict = 8192, 1200
        def __init__(self, model: str, **kw: Any) -> None:
            assert kw["require_gpu"] and not kw["cache"]
            self.model = model
        async def unload(self) -> None:
            state["unloaded"].append(self.model)
        async def __call__(self, system: str, user: str, **kw: Any) -> str:
            if self.model == L.JAN:
                return json.dumps({"facts": [{"text": "The company reported revenue 100", "source": "S1"}]})
            return json.dumps({h: {"label": "5", "quote": "The company reported revenue 100"} for h in L.CAPS})

    class Server:
        def __init__(self, port: int, *_a: Any) -> None:
            assert not state["servers"]
            state["servers"].append(port)
        def stop(self) -> None:
            state["servers"].pop()

    class Jail:
        def __init__(self, llm: Any, tools: Any, limits: Any) -> None:
            self.tools = tools
        async def __aenter__(self) -> Self:
            return self
        async def __aexit__(self, *_a: object) -> None:
            pass
        async def call(self, request: dict[str, Any]) -> dict[str, str]:
            assert self.tools.mode == "live" and self.tools.tool_cache is None
            assert "web_search" not in {s.name for s in self.tools.available()}
            if request["subject"]["ticker"] == state["fail"]:
                raise TimeoutError
            return {"evidence": "https://www.sec.gov/a.htm The company reported revenue 100"}

    async def articles(*_a, **_kw):
        return {"pages": [], "domains": [], "attempts": [], "version": "test"}
    monkeypatch.setattr(L, "collect", articles)
    monkeypatch.setattr(L.FreeLiveGateway, "from_env", gateway)
    monkeypatch.setattr(L, "OllamaLLM", Model)
    monkeypatch.setattr(L, "Ollama", Server)
    monkeypatch.setattr(L, "AgentJail", Jail)
    monkeypatch.setattr(L, "manifest_digest", lambda model, _dir: "digest-" + model)
    state["dir"] = tmp_path
    return state


def test_full_cohort_uses_fresh_gateway_saves_failures_and_stops_jan_before_bonsai(models: dict[str, Any]) -> None:
    summary = asyncio.run(L.run_cohort(("AAPL", "MSFT"), models["dir"]))
    assert [r["status"] for r in summary["companies"]] == ["decided", "research_failed"]
    assert summary["orders_submitted_by_experiment"] == 0 and summary["portfolio"]["mode"] == "proposal_only"
    assert not models["servers"] and models["unloaded"] == [L.JAN, "bonsai-27b:latest"]
    assert models["gateway_closed"] == 2
    record = json.loads((models["dir"] / "AAPL.json").read_text())
    assert record["gateway_mode"] == "live" and not record["cache"]
    assert record["evidence_sha256"] and record["decided_at"] and record["llm_calls"]
    assert record["ratings"] == dict.fromkeys(L.CAPS, 5)
    assert [r["type"] for r in Ledger(models["dir"] / "ledger.jsonl").verify()] == [
        "research_attempt", "research_attempt", "decision_attempt", "summary"]


def test_no_successful_research_means_no_bonsai_server(models: dict[str, Any]) -> None:
    summary = asyncio.run(L.run_cohort(("MSFT",), models["dir"]))
    assert summary["portfolio"]["cash"] == 1
    assert models["unloaded"] == [L.JAN]
    assert "bonsai_server_start_s" not in summary["loads"]


def test_combined_paper_action_preserves_existing_order_path_and_halt(tmp_path: Path) -> None:
    cmds = D.commands("paper_research_test", "node")
    assert cmds[0][1][-1] == "scripts/live_research_test.py"
    assert cmds[1:] == D.commands("paper_ai", "node")
    assert len(D.commands("live_research_test", "node")) == 1
    (tmp_path / "AUTORUN_MODE").write_text("live")
    (tmp_path / "HALT").write_text("{}")
    from datetime import UTC, datetime
    assert "halted" in str(D.preflight("paper_research_test", tmp_path, datetime(2026, 10, 3, 15, tzinfo=UTC)))


def test_budget_cohort_persists_both_arms_on_shared_research(models: dict[str, Any]) -> None:
    summary = asyncio.run(L.run_cohort(("AAPL", "MSFT"), models["dir"], budget_experiment=True))
    assert summary["protocol"] == "BE1" and summary["orders_submitted_by_experiment"] == 0
    assert summary["comparison"]["winner"] is None
    for arm in ("neutral", "budget_aware"):
        assert summary["comparison"]["arms"][arm]["attempts"] == 2
        assert summary["comparison"]["arms"][arm]["failed"] == 1
    recs = Ledger(models["dir"] / "ledger.jsonl").verify()
    arms = [r for r in recs if r["type"] == "budget_arm"]
    assert len(arms) == 2 and arms[0]["card_sha256"] == arms[1]["card_sha256"]
    assert models["gateway_closed"] == 2  # one shared research phase, not a re-fetch for each arm


def test_deep_attempt_requires_original_articles_and_never_creates_hs1(models, tmp_path, monkeypatch):
    monkeypatch.setattr(L, 'DEEP', True)
    monkeypatch.setattr(L, 'COMPANY_NAME', 'Alpha Company')
    out = tmp_path / 'deep'
    out.mkdir()
    summary = asyncio.run(L.run_cohort(('AAPL',), out))
    assert summary['protocol'] == 'DEEP1'
    assert 'horizon_plan' not in summary
    assert summary['companies'][0]['status'] == 'research_failed'
    assert 'article coverage' in summary['companies'][0]['error_reason']
    row = json.loads((out / 'AAPL.json').read_text())
    assert row['research_budget_s'] == 600 and row['evidence_sha256']


def test_two_company_deep_batch_loads_each_model_once_and_checks_four_horizons(models, tmp_path, monkeypatch):
    monkeypatch.setattr(L, 'DEEP', True)
    models['fail'] = 'NONE'
    async def articles(*_a, **_kw):
        return {"pages": [{"publisher": h, "url": f"https://{h}/story", "text": "The company reported revenue 100"}
                          for h in ("publisher-a.example", "publisher-b.example")],
                "domains": ["publisher-a.example", "publisher-b.example"]}
    monkeypatch.setattr(L, 'collect', articles)
    original_gateway = L.FreeLiveGateway.from_env
    original_model = L.OllamaLLM
    def gateway(*a, **kw):
        gw = original_gateway(*a, **kw)
        gw.log.extend([{'tool': 'fetch_page', 'ok': True, 'args': {'url': 'https://publisher-a.example/story'}},
                       {'tool': 'fetch_page', 'ok': True, 'args': {'url': 'https://publisher-b.example/story'}}])
        return gw
    class Model(original_model):
        async def __call__(self, system, user, **kw):
            if self.model == L.JAN:
                return await super().__call__(system, user, **kw)
            return json.dumps({h: {'label': '4', 'quote': 'The company reported revenue 100'} for h in (*L.CAPS, 'short')})
    monkeypatch.setattr(L.FreeLiveGateway, 'from_env', gateway)
    monkeypatch.setattr(L, 'OllamaLLM', Model)
    out = tmp_path / 'batch'
    out.mkdir()
    summary = asyncio.run(L.run_cohort(('AAPL', 'MSFT'), out))
    assert all(c['status'] == 'decided' for c in summary['companies'])
    assert all(set(c['ratings']) == {'day', 'short', 'medium', 'long'} for c in summary['companies'])
    assert models['unloaded'] == [L.JAN, 'bonsai-27b:latest']
    assert set(summary['loads']) == {'jan_server_start_s', 'bonsai_server_start_s'}
