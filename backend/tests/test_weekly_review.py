"""The Saturday review (scripts/weekly_review.py) on a synthetic forward folder: it must be written whatever the
books' entries look like."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import weekly_review as W

from app.forward.ledger import Ledger
from app.portfolio.forward import AGGRESSIVE


def test_review_is_written_when_the_aggressive_book_failed_a_week(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    """A failed week of the aggressive book is an {"error": ...} entry without numbers (app/portfolio/forward.py).
    The review's table read `equity` from every entry and would have crashed on it; a torn line is skipped too."""
    fwd = tmp_path / "results" / "forward"
    (fwd / "allocator").mkdir(parents=True)
    runs = [{"run_at_utc": "2026-10-05T22:01:00+00:00", "data_through": "2026-10-02",
             "books": {"master+brakes": {"equity": 100_000.0}, AGGRESSIVE: {"equity": 100_000.0, "gross": 2.5}}},
            {"run_at_utc": "2026-10-12T22:01:00+00:00", "data_through": "2026-10-09",
             "books": {"master+brakes": {"equity": 101_000.0}, AGGRESSIVE: {"error": "AssertionError: cash"}}}]
    (fwd / "allocator" / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in runs) + '{"run_at_utc": "20')
    Ledger(fwd / "events" / "ledger.jsonl").append("run", as_of="2026-10-12T12:45:00+00:00", new=0, source="bonsai")
    monkeypatch.setattr(W, "FWD", fwd)
    monkeypatch.setattr(W, "BACKEND", tmp_path)
    monkeypatch.setattr(W, "shadow_lines", list)
    monkeypatch.setattr(W, "goal_lines", list)
    monkeypatch.setattr(W, "symbol_lines", lambda: ["", "## Symbol check", "", "- 2 of 3 index members have prices"])
    for mod in ("core_leads", "calendar_shadows", "trading_health"):  # their own files: not this test's subject
        monkeypatch.setitem(sys.modules, mod, types.SimpleNamespace())
    W.main()
    text = next(fwd.glob("review_*.md")).read_text()
    assert "| master+brakes | 100,000 | 101,000 | +1.00% |" in text
    assert f"| {AGGRESSIVE} | 100,000 | 100,000 | +0.00% |" in text  # its last good week
    assert "its last run failed (AssertionError: cash); the other books were not affected." in text
    assert "## Symbol check" in text and "written to" in capsys.readouterr().out


def test_symbol_check_names_members_without_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    import forward_events
    import pandas as pd
    monkeypatch.setattr(forward_events, "members", lambda _y: pd.DataFrame({"ticker": ["AAA", "BRK.B", "EQR", "GONE"]}))
    monkeypatch.setattr(forward_events, "daily_bars", lambda want, _a, _b: {t: 1 for t in want if t != "GONE"})
    lines = W.symbol_lines()
    assert lines[1] == "## Symbol check" and lines[3].startswith("- 3 of 4 index members have recent prices; none for GONE")
