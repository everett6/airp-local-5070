"""Jan-v1's tool calls arrive in several shapes; all become the agent's {"actions": [...]} or are rejected."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from app.sandbox.vllm_client import _args
from app.sandbox.walkforward import _as_actions, _is_agent_reply

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from research_events import prefetch_for


def test_calls_are_pulled_from_prose_and_from_several_objects_in_a_row():
    text = '\n\n{"name": "price_history_as_of", "args": {"ticker": "IBKR"}}\n{"name": "news_as_of", "arguments": {"ticker": "IBKR"}}'
    got = json.loads(_as_actions(text))
    assert [a["tool"] for a in got["actions"]] == ["price_history_as_of", "news_as_of"]
    assert got["actions"][1]["args"] == {"ticker": "IBKR"}
    wrapped = _as_actions('Plan: {"thought": "x", "actions": [{"tool": "wiki_search", "args": {}}]} done')
    assert json.loads(wrapped)["actions"][0]["tool"] == "wiki_search"


def test_prose_without_a_call_is_not_an_agent_reply():
    assert not _is_agent_reply(_as_actions("I'll call price_history_as_of first."))
    assert _is_agent_reply(_as_actions('{"final": {"p_up": 0.5}}'))
    assert _args('{"ticker": "X"}') == {"ticker": "X"} and _args("not json") == {} and _args(None) == {}


def test_prefetch_reads_the_release_itself_and_the_company_article():
    r = SimpleNamespace(ticker="KVUE", accession="acc-1", cik=1944048)
    calls = prefetch_for(r, {"acc-1": "https://www.sec.gov/x.htm"}, {1944048: "Kenvue"})
    tools = {c["tool"]: c["args"] for c in calls}
    assert tools["read_filing"]["url"] == "https://www.sec.gov/x.htm" and tools["wiki_as_of"]["title"] == "Kenvue"
    assert {"price_history_as_of", "sec_filings_as_of", "news_as_of"} <= set(tools)
    assert {c["tool"] for c in prefetch_for(r, {}, {})} == {"price_history_as_of", "sec_filings_as_of", "news_as_of"}
