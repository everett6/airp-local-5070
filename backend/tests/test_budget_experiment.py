"""Matched economic-discipline prompts, simulated inference only."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import budget_experiment as B
import desktop_run as D
import live_research_test as L

CARD = "Company: AAA. The company reported growing revenue."


@pytest.mark.parametrize("index,first", [(0, "neutral"), (1, "budget_aware")])
def test_matched_card_limits_alternating_order_and_pass(index: int, first: str) -> None:
    requests = []
    saved = []

    async def model(system: str, user: str) -> str:
        requests.append((system, user))
        return json.dumps({h: {"label": "3", "quote": ""} for h in L.CAPS})

    results = asyncio.run(B.pair(model, CARD, L.PROMPT, L.FIELDS, index, saved.append))
    assert results[0]["arm"] == first and saved == results
    assert results[0]["card_sha256"] == results[1]["card_sha256"]
    assert requests[0][1] == requests[1][1] == CARD
    assert results[0]["limits"] == results[1]["limits"] == B.LIMITS
    assert all(x["ratings"] == dict.fromkeys(L.CAPS, 3) for x in results)
    assert {x["prompt"] for x in results} == {L.PROMPT, L.PROMPT + B.DISCIPLINE}


def test_failure_is_saved_and_other_arm_is_not_replaced() -> None:
    saved = []

    async def model(system: str, user: str) -> str:
        if system.endswith(B.DISCIPLINE):
            raise TimeoutError
        return json.dumps({h: {"label": "5", "quote": "invented evidence"} for h in L.CAPS})

    results = asyncio.run(B.pair(model, CARD, L.PROMPT, L.FIELDS, 0, saved.append))
    assert len(saved) == 2 and results[1]["status"] == "failed"
    assert results[1]["error"] == "TimeoutError"
    assert results[0]["unsupported_claims"] == 3 and results[0]["ratings"] == dict.fromkeys(L.CAPS, 3)
    rows = [{"ticker": "AAA", "arms": results}, {"ticker": "BBB", "status": "research_failed"}]
    report = B.report(rows)
    assert report["arms"]["neutral"]["failed"] == 1
    assert report["arms"]["budget_aware"]["failed"] == 2
    assert report["winner"] is None and report["incremental_net_return"] is None
    assert not report["pairs"][0]["both_decided"]
    results[1]["card_sha256"] = "different"
    with pytest.raises(ValueError, match="Unmatched"):
        B.report(rows)


def test_deadline_is_enforced_for_both_arms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(B, "LIMITS", {**B.LIMITS, "deadline_s": 0.01})

    async def model(system: str, user: str) -> str:
        await asyncio.Event().wait()
        return ""

    saved = []
    results = asyncio.run(B.pair(model, CARD, L.PROMPT, L.FIELDS, 0, saved.append))
    assert len(saved) == 2 and all(x["error"] == "TimeoutError" for x in results)


def test_action_is_research_only_with_explicit_mode() -> None:
    assert "budget_experiment" not in D.PAPER_ACTIONS
    cmds = D.commands("budget_experiment", "node")
    assert len(cmds) == 1 and cmds[0][1][-1] == "--budget-experiment"
