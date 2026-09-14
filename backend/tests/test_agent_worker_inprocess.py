"""The agent's tasks normally run inside the jailed subprocess (invisible to coverage). Run them in-process here
with the stdin/stdout protocol replaced, so their logic is tested directly."""
import io
import json
import sys

import pytest

from app.sandbox import agent_worker as aw


class Pipe:
    """Stands in for stdout/stdin: records what the agent sends and answers LLM/tool requests."""

    def __init__(self, llm_reply, tool_reply=None):
        self.sent, self.llm_reply, self.tool_reply = [], llm_reply, tool_reply
        self._last = None

    def send(self, obj):
        self.sent.append(obj)
        self._last = obj

    def recv(self):
        if "llm_requests" in self._last:
            return {"llm_responses": {r["id"]: self.llm_reply(r) for r in self._last["llm_requests"]}}
        return {"tool_responses": {r["id"]: self.tool_reply(r) for r in self._last["tool_requests"]}}


@pytest.fixture
def pipe(monkeypatch):
    def make(llm_reply, tool_reply=None):
        p = Pipe(llm_reply, tool_reply)
        monkeypatch.setattr(aw, "_send", p.send)
        monkeypatch.setattr(aw, "_recv", p.recv)
        return p
    return make


def _item(i, trend=0.2):
    asset = [100 + trend * k + (k % 3) for k in range(120)]
    return {"id": f"a{i}", "asset": asset, "market": [100 + 0.1 * k for k in range(120)]}


def test_run_predict_with_fundamentals_horizon_and_guarded_stacker(pipe):
    p = pipe(lambda r: '{"p_up": 0.62, "reason": "x"}')
    resolved = [{"p_llm": 0.6, "features": aw.features(_item(0)["asset"], _item(0)["market"]), "up": k % 2 == 0}
                for k in range(250)]
    out = aw.run_predict({"items": [{**_item(0), "fund_text": "Fundamentals as reported: +1.0%"}, _item(1)],
                          "memory": {"track_record": {"n": 30, "hit_rate": 0.5, "brier": 0.25, "base_rate": 0.5,
                                                      "avg_p": 0.6}, "lessons": ["stay calibrated"],
                                     "resolved": resolved},
                          "target": "abs", "horizon": 20})
    req = p.sent[0]["llm_requests"]
    assert "20 trading days" in req[0]["system"] and "Fundamentals as reported" in req[0]["user"]
    assert "Fundamentals" not in req[1]["user"] and "stay calibrated" in req[1]["user"]
    assert [x["p_llm"] for x in out["predictions"]] == [0.62, 0.62]
    assert out["stacker_info"]["stacker"] in ("rejected", "adopted")  # 250 identical-feature rows: a real decision


def test_system_prompt_is_unchanged_for_five_days():
    assert aw.system_prompt("abs", 5) is aw.SYSTEM and aw.system_prompt("excess", 5) is aw.SYSTEM_EXCESS
    assert "5 trading" not in aw.system_prompt("excess", 20)


def test_run_reflect_parses_lessons_and_keeps_old_ones_on_garbage(pipe):
    recs = [{"p_llm": 0.6, "up": True, "features": aw.features(_item(0)["asset"], _item(0)["market"])}] * 3
    pipe(lambda r: '{"lessons": ["a", "b"]}')
    assert aw.run_reflect({"records": recs, "previous_lessons": []}) == {"lessons": ["a", "b"]}
    pipe(lambda r: "not json")
    assert aw.run_reflect({"records": recs, "previous_lessons": ["keep"]}) == {"lessons": ["keep"]}


def test_run_research_uses_tools_then_answers(pipe):
    replies = iter([json.dumps({"thought": "t", "actions": [{"tool": "price_history", "args": {"ticker": "X"}}]}),
                    "garbage", json.dumps({"final": {"p_up": 0.7, "reason": "r", "sources": ["u", 3]}})])
    p = pipe(lambda r: next(replies), lambda r: {"ok": True, "result": "R" * 50})
    out = aw.run_research({"subject": {"ticker": "X", "as_of": "now"}, "tools": [{"name": "price_history"}],
                           "max_rounds": 3, "num_ctx": 8192, "num_predict": 600})
    assert out["p_up"] == 0.7 and out["answered"] and out["sources"] == ["u"] and out["parse_failures"] == 1
    assert any("tool_requests" in s for s in p.sent)


def test_run_research_forced_final_and_no_answer(pipe):
    pipe(lambda r: json.dumps({"actions": []}))
    out = aw.run_research({"subject": {"ticker": "X", "as_of": "now"}, "tools": [], "max_rounds": 1})
    assert out["p_up"] == 0.5 and not out["answered"]


def test_probe_isolation_sees_readable_files(tmp_path):
    f = tmp_path / "secret"
    f.write_text("x")
    r = aw.probe_isolation([str(f), str(tmp_path / "missing")])
    assert r["readable_forbidden_paths"] == [str(f)]  # unjailed here, so the file IS readable


def test_main_dispatches_tasks(monkeypatch, capsys):
    msgs = [{"task": "probe", "paths": []}, {"task": "nope"}]
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(json.dumps(m) + "\n" for m in msgs)))
    monkeypatch.setattr(aw, "probe_isolation", lambda paths: {"readable_forbidden_paths": [], "network_reachable": False})
    aw.main()
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert lines[0]["result"]["network_reachable"] is False and "unknown task" in lines[1]["error"]
