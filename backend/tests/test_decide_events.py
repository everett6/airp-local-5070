"""The live judge step (scripts/decide_events.py) end to end with a stub model: what it reads, what it skips and what
it appends. No GPU, no network."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import decide_events as D

from app.forward.ledger import jsonl_records


class StubLLM:
    calls: ClassVar[list[str]] = []

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __call__(self, system: str, user: str, mode: str | None = None) -> str:
        StubLLM.calls.append(user)
        return json.dumps({"logodds": 1.5 if "AAA" in user else -0.5, "mass": 0.9, "censored": False})

    async def unload(self) -> None:
        pass


def _inputs(tmp_path: Path) -> argparse.Namespace:
    days = pd.bdate_range("2026-08-03", "2026-09-30")
    rows = [{"Date": d.date().isoformat(), "Ticker": t, "Open": 100.0 + i, "Close": 100.5 + i}
            for i, d in enumerate(days) for t in ("SPY", "XLI", "AAA", "BBB", "CCC")]
    pd.DataFrame(rows).to_parquet(tmp_path / "prices.parquet")
    ev = pd.DataFrame([{"cik": n, "ticker": t, "sector": "Industrials", "accession": f"a{n}",
                        "accepted_utc": "2026-10-01T11:00:00", "filed": "2026-10-01"}
                       for n, t in ((1, "AAA"), (2, "BBB"), (3, "CCC"))])
    ev.to_csv(tmp_path / "events.csv", index=False)
    pd.DataFrame({"accession": ["a1", "a2", "a3"], "fact_sheet": ["sheet AAA", "sheet BBB", "sheet CCC"]}
                 ).to_csv(tmp_path / "features.csv", index=False)
    (tmp_path / "results" / "events").mkdir(parents=True)
    return argparse.Namespace(model="m", base_url="http://x", events=str(tmp_path / "events.csv"),
                              extract=str(tmp_path / "extract.jsonl"), prices=str(tmp_path / "prices.parquet"),
                              date_from="2026-09-30", date_to="2099-12-31", features=str(tmp_path / "features.csv"),
                              tag="t", horizon=5, limit=0, live=True, parallel=3, explain=0)


def test_judge_scores_new_releases_once_and_appends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    args = _inputs(tmp_path)
    monkeypatch.setattr(D, "BACKEND", tmp_path)
    monkeypatch.setattr(D, "OllamaLLM", StubLLM)
    StubLLM.calls = []
    (tmp_path / "extract.jsonl").write_text("".join(json.dumps({"accession": a, "ticker": t}) + "\n"
                                                    for a, t in (("a1", "AAA"), ("a2", "BBB"), ("a3", "CCC"))))
    asyncio.run(D.run(args))
    out = tmp_path / "results" / "events" / "decide_m_t_h5.jsonl"
    recs = jsonl_records(out)
    assert [(r["accession"], r["logodds"], r["buy"], r["prompt_user"]) for r in recs] == [
        ("a1", 1.5, True, "sheet AAA"), ("a2", -0.5, False, "sheet BBB"), ("a3", -0.5, False, "sheet CCC")]
    asyncio.run(D.run(args))  # a second run decides nothing again
    assert len(StubLLM.calls) == 3 and len(jsonl_records(out)) == 3


def test_judge_survives_lines_cut_off_by_a_power_loss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Before: a cut-off line in the reader's or the judge's file made every later live run fail at json.loads, so
    every later decision was missed until someone repaired the file by hand."""
    args = _inputs(tmp_path)
    monkeypatch.setattr(D, "BACKEND", tmp_path)
    monkeypatch.setattr(D, "OllamaLLM", StubLLM)
    StubLLM.calls = []
    (tmp_path / "extract.jsonl").write_text(json.dumps({"accession": "a1", "ticker": "AAA"}) + "\n"
                                            + json.dumps({"accession": "a2", "ticker": "BBB"}) + "\n"
                                            + '{"accession": "a3", "tic')  # the reader was cut off on a3
    out = tmp_path / "results" / "events" / "decide_m_t_h5.jsonl"
    out.write_text(json.dumps({"accession": "a1", "logodds": 1.5}) + "\n" + '{"accession": "a2", "logo')
    asyncio.run(D.run(args))
    recs = jsonl_records(out)
    assert [r["accession"] for r in recs] == ["a1", "a2"]  # a2 decided again on its own line; a3 has no fact sheet
    assert recs[1]["logodds"] == -0.5 and StubLLM.calls == ["sheet BBB"]
    with pytest.raises(FileNotFoundError):  # a missing reader file is still an error, not an empty run
        asyncio.run(D.run(argparse.Namespace(**{**vars(args), "extract": str(tmp_path / "missing.jsonl")})))


def test_reader_resumes_after_a_cut_off_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reader step (scripts/extract_events.py) with a stub model: a release whose line was cut off is read again
    and written on a line of its own; whole lines are not read twice."""
    import gzip

    import extract_events as X

    class Reader(StubLLM):
        async def __call__(self, system: str, user: str, mode: str | None = None) -> str:
            StubLLM.calls.append(user)
            return json.dumps({"guidance": "raised", "tone": "positive"})

    args = _inputs(tmp_path)
    monkeypatch.setattr(X, "BACKEND", tmp_path)
    monkeypatch.setattr(X, "TEXT", tmp_path)
    monkeypatch.setattr(X, "OllamaLLM", Reader)
    StubLLM.calls = []
    for n in (1, 2, 3):
        (tmp_path / f"a{n}.txt.gz").write_bytes(gzip.compress(f"release text {n}".encode()))
    out = tmp_path / "extract.jsonl"
    out.write_text(json.dumps({"accession": "a1", "ticker": "AAA"}) + "\n" + '{"accession": "a2", "tick')
    xargs = argparse.Namespace(events=args.events, date_from="2026-09-30", date_to="2099-12-31", model="qwen3:8b",
                               base_url="http://x", parallel=4, max_chars=8000, compact=True, limit=0, out=str(out))
    asyncio.run(X.extract(xargs))
    recs = jsonl_records(out)
    assert [r["accession"] for r in recs] == ["a1", "a2", "a3"]
    assert sorted(StubLLM.calls) == ["release text 2", "release text 3"]
    assert recs[1]["guidance"] == "unverified"  # the code check: a guidance label without its quote is not kept
