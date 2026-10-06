import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from llm_fields import verify

from app.sandbox.judge_lookup import double_check, extended_card, lookups, readable, revise_prompt


class FakeGateway:
    def __init__(self, results):
        self.results, self.calls = results, []

    async def execute(self, reqs):
        self.calls += reqs
        return {r["id"]: self.results.get(r["tool"], {"ok": False, "error": "unknown tool"}) for r in reqs}


def replies(*texts):
    it = iter(texts)

    async def llm(system, user):
        return next(it)
    return llm


NEWS = {"ok": True, "result": json.dumps({"items": [{"title": "Acme raises full-year guidance after record quarter",
                                                     "url": "https://x.test/a", "published": "2026-10-01"}]})}


def test_lookups_append_only_successful_source_text():
    gw = FakeGateway({"stock_news": NEWS})
    llm = replies(json.dumps({"thought": "need news", "actions": [{"tool": "stock_news", "args": {"ticker": "ACME"}},
                                                                  {"tool": "nope", "args": {}}]}),
                  json.dumps({"thought": "enough", "done": True}), json.dumps({"thought": "still enough", "done": True}))
    out = asyncio.run(lookups(llm, gw, "Company: ACME", []))
    assert [s["round"] for s in out["steps"]] == [1, 2, 3] and out["steps"][2]["done"]
    assert "Acme raises full-year guidance after record quarter" in out["evidence"]
    assert "unknown tool" not in out["evidence"] and "https://x.test/a" not in out["evidence"]
    assert out["steps"][0]["calls"][1]["ok"] is False


def test_a_quote_from_a_lookup_passes_the_unchanged_check_and_an_invented_one_does_not():
    gw = FakeGateway({"stock_news": NEWS})
    llm = replies(json.dumps({"thought": "t", "actions": [{"tool": "stock_news", "args": {}}]}), "{\"done\": true}",
                  "{\"done\": true}")
    card = extended_card("Company: ACME", asyncio.run(lookups(llm, gw, "Company: ACME", []))["evidence"])
    fields = {"day": (("1", "2", "3", "4", "5"), "3")}
    real = {"day": {"label": "4", "quote": "Acme raises full-year guidance after record quarter"}}
    made_up = {"day": {"label": "5", "quote": "Acme will double its sales next year"}}
    assert verify(real, card, fields)["day"] == "4"
    assert verify(made_up, card, fields)["day"] == "3"


def test_round_limit_unparsed_reply_and_no_evidence():
    gw = FakeGateway({"stock_news": NEWS})
    act = json.dumps({"thought": "more", "actions": [{"tool": "stock_news", "args": {}}]})
    out = asyncio.run(lookups(replies(act, act, act, act), gw, "c", [], rounds=3))
    assert len(gw.calls) == 3 and len(out["steps"]) == 3
    out = asyncio.run(lookups(replies(*["not json"] * 8), gw, "c", []))
    assert len(out["steps"]) == 4 and out["steps"][0]["error"] == "unparsed reply" and out["evidence"] == ""
    fixed = asyncio.run(lookups(replies("not json", act, *["{\"done\": true}"] * 4), gw, "c", [], rounds=2))
    assert fixed["steps"][0].get("calls") and fixed["evidence"]  # the retry was read
    assert extended_card("c", "") == "c"


def test_readable_flattens_json_and_survives_truncation():
    assert readable(json.dumps({"ticker": "A", "closes": [1, 2, 3], "note": "He said \"up\""})) == \
        'ticker: A\ncloses: 1, 2, 3\nnote: He said "up"'
    assert readable('{"title": "Cut \\"short"…(truncated)') == '{"title": "Cut "short"…(truncated)'


def test_an_early_done_is_pushed_to_keep_looking_and_the_push_names_the_bear_case():
    gw = FakeGateway({"stock_news": NEWS})
    seen = []
    it = iter([json.dumps({"thought": "enough", "done": True}),
               json.dumps({"thought": "bear case", "actions": [{"tool": "stock_news", "args": {}}]}),
               json.dumps({"thought": "done now", "done": True}), json.dumps({"thought": "really", "done": True})])

    async def llm(system, user):
        seen.append(user)
        return next(it)
    out = asyncio.run(lookups(llm, gw, "c", [], rounds=4))
    assert len(out["steps"]) == 4 and "AGAINST" in seen[1] and out["evidence"]


def test_the_double_check_sees_the_draft_and_the_revision_prompt_carries_it():
    gw = FakeGateway({"stock_news": NEWS})
    seen = []
    it = iter([json.dumps({"thought": "doubt the guidance", "doubts": ["guidance"],
                           "actions": [{"tool": "stock_news", "args": {}}]}), json.dumps({"done": True})])

    async def llm(system, user):
        seen.append((system, user))
        return next(it)
    out = asyncio.run(double_check(llm, gw, "Company: ACME", '{"day": {"label": "5"}}', []))
    assert "double-check" in seen[0][0] and 'label": "5"' in seen[0][1] and "guidance" in out["evidence"]
    assert "draft judgment was" in revise_prompt("BASE", "DRAFT") and "DRAFT" in revise_prompt("BASE", "DRAFT")
