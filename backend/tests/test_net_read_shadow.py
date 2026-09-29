import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import net_read_shadow as N

from app.forward.ledger import Ledger


def _dir(tmp_path: Path, n: int = 12) -> Path:
    d = tmp_path / "events"
    d.mkdir()
    led = Ledger(d / "ledger.jsonl")
    labels = []
    for i in range(n):
        acc = f"a{i}"
        led.append("decision", accession=acc, ticker="T", sector="Industrials",
                   entry_deadline="2026-10-06T09:30:00-04:00", source="bonsai", logodds=0.1, on_time=True)
        led.append("outcome", accession=acc, entry="2026-10-06", fwd5=0.01 * i)
        net = "bullish" if i >= 8 else "neutral" if i >= 3 else "bearish"
        at = "2026-10-06T12:00:00+00:00" if i else "2026-10-06T14:00:00+00:00"  # a0: after the 13:30 UTC open
        labels.append({"accession": acc, "ticker": "T", "entry_deadline": "2026-10-06T09:30:00-04:00",
                       "net_read": net, "written_at": at})
    (d / "net_read.jsonl").write_text("".join(json.dumps(x) + "\n" for x in labels))
    return d


def test_late_labels_are_not_scored(tmp_path):
    df = N.scored(_dir(tmp_path))
    assert len(df) == 11 and "a0" not in set(df["accession"])  # compares times, not strings with different offsets


def test_status_ic_and_readiness(tmp_path):
    s = N.status(_dir(tmp_path))
    assert s["events"] == 11 and s["months"] == 1 and s["mean_ic"] > 0.8 and not s["ready_to_judge"]
    assert s["counts"] == {"neutral": 5, "bullish": 4, "bearish": 2}


def test_todo_skips_labelled_and_non_decisions():
    recs = [{"type": "decision", "accession": "x"}, {"type": "decision", "accession": "y"},
            {"type": "missed", "accession": "z"}, {"type": "outcome", "accession": "x"}]
    assert [r["accession"] for r in N.todo(recs, {"x"})] == ["y"]


def test_label_keeps_code_last_word(monkeypatch):
    text = "Revenue rose 12%. We raise our full-year outlook on strong demand. Costs were higher."
    monkeypatch.setattr(N, "text_of", lambda acc: text)
    r = {"accession": "a", "ticker": "T", "entry_deadline": "2026-10-06T09:30:00-04:00"}

    async def fake(system, user):
        assert "No research evidence was found." in user and system == N.PROMPT_J
        return json.dumps({"reason": "good", "net_read": {"label": "bullish", "quote": "made-up sentence not in it"}})
    assert asyncio.run(N.label(fake, r))["net_read"] == "neutral"  # unverified quote -> default

    async def fake2(system, user):
        return json.dumps({"net_read": {"label": "bullish", "quote": "We raise our full-year outlook on strong demand."}})
    assert asyncio.run(N.label(fake2, r))["net_read"] == "bullish"
    monkeypatch.setattr(N, "text_of", lambda acc: None)
    assert asyncio.run(N.label(fake2, r))["net_read"] == "neutral"


def test_no_ledger_or_no_gpu_is_a_no_op(tmp_path):
    assert N.run(tmp_path / "nothing", True) == {}
    assert N.run(_dir(tmp_path), False) == {}


def test_ai_lens_uses_its_own_prompt_and_file(monkeypatch, tmp_path):
    text = "Data center revenue doubled on demand for AI accelerators from cloud customers."
    monkeypatch.setattr(N, "text_of", lambda acc: text)
    r = {"accession": "a", "ticker": "T", "entry_deadline": "2026-10-06T09:30:00-04:00"}

    async def fake(system, user):
        assert system == N.PROMPT_AI and "Aschenbrenner" in system
        return json.dumps({"reason": "x", "ai_exposure": {"label": "beneficiary", "quote": text},
                           "ai_read": {"label": "bullish", "quote": text}})
    rec = asyncio.run(N.label(fake, r, "ai_lens"))
    assert rec["ai_read"] == "bullish" and rec["fields"]["ai_exposure"] == "beneficiary"
    d = _dir(tmp_path)
    (d / "ai_lens.jsonl").write_text((d / "net_read.jsonl").read_text().replace('"net_read"', '"ai_read"'))
    assert N.status(d, "ai_lens")["events"] == 11


def test_bull_bear_keeps_points_and_marks_quotes(monkeypatch):
    text = "Revenue rose 12%. We raise our full-year outlook on strong demand. Costs were higher than planned."
    monkeypatch.setattr(N, "text_of", lambda acc: text)
    r = {"accession": "a", "ticker": "T", "entry_deadline": "2026-10-06T09:30:00-04:00"}

    async def fake(system, user):
        assert system == N.PROMPT_BB
        return json.dumps({
            "bull": [{"point": "Guidance raised", "quote": "We raise our full-year outlook on strong demand."},
                     {"point": "Invented", "quote": "Margins hit a record high this quarter."},
                     {"point": "", "quote": "x"}, {"point": "a"}, {"point": "b"}],
            "bear": [{"point": "Costs up", "quote": "Costs were higher than planned."}],
            "reason": "bull wins", "bb_read": {"label": "bullish", "quote": "We raise our full-year outlook on strong demand."}})
    out = asyncio.run(N.label(fake, r, "bull_bear"))
    assert out["bb_read"] == "bullish" and out["reason"] == "bull wins"
    assert [(p["point"], p["verified"]) for p in out["bull"]] == [("Guidance raised", True), ("Invented", False)]
    assert out["bear"][0]["verified"] and len(out["bear"]) == 1


def test_bull_bear_survives_garbage(monkeypatch):
    monkeypatch.setattr(N, "text_of", lambda acc: "Some release text here.")
    r = {"accession": "a", "ticker": "T", "entry_deadline": "2026-10-06T09:30:00-04:00"}

    async def fake(system, user):
        return '{"bull": "not a list", "bear": null}'
    out = asyncio.run(N.label(fake, r, "bull_bear"))
    assert out["bb_read"] == "neutral" and out["bull"] == [] and out["bear"] == []
