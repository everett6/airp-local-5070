import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import self_improve as F

from app.forward.ledger import Ledger


def test_clean_drops_names_tickers_and_long_lessons():
    names = {"NVDA", "abbott"}
    got = F.clean(["Weigh guidance changes above the headline beat.", "NVDA shows chips lead.",
                   "Abbott results are special.", "word " * 31, "", 7], names)
    assert got == ["Weigh guidance changes above the headline beat.", "7"]
    assert F.clean("not a list", names) == [] and len(F.clean(["ok rule"] * 9, names)) == 5


def _setup(tmp_path: Path, n: int = 60) -> Path:
    d = tmp_path / "events"
    d.mkdir()
    led = Ledger(d / "ledger.jsonl")
    v0, v1 = [], []
    for i in range(n):
        acc = f"a{i}"
        month = "2026-10" if i < n // 2 else "2026-11"
        led.append("outcome", accession=acc, entry=f"{month}-15", fwd5=0.001 * (i % 10 - 5))
        base = {"accession": acc, "ticker": "T", "entry_deadline": "2026-12-01T09:30:00-05:00",
                "written_at": "2026-10-02T12:00:00+00:00", "bull": [{"point": "p", "quote": "q", "verified": True}],
                "bear": [], "reason": "r"}
        v0.append(base | {"bb_read": "bullish" if i % 3 == 0 else "bearish"})  # unrelated to the outcome
        v1.append(base | {"bb_read": "bullish" if i % 10 >= 5 else "bearish"})  # tracks the outcome
    (d / "bull_bear.jsonl").write_text("".join(json.dumps(x) + "\n" for x in v0))
    (d / "bb_v1.jsonl").write_text("".join(json.dumps(x) + "\n" for x in v1))
    return d


def test_summary_and_matured(tmp_path):
    d = _setup(tmp_path)
    m = F.matured(d, 0)
    assert len(m) == 60 and set(m["month"]) == {"2026-10", "2026-11"}
    s = F.summary(m)
    assert "bullish: 20 calls" in s and "Worst misses:" in s and "You said" in s


def test_challenger_that_tracks_outcomes_is_promoted(tmp_path, monkeypatch):
    d = _setup(tmp_path, 120)
    monkeypatch.setitem(F.PROMOTE, "min_paired", 100)
    vs = [{"v": 0, "status": "champion", "lessons": []},
          {"v": 1, "status": "challenger", "lessons": ["x"], "created_at": "2026-10-01T00:00:00+00:00"}]
    assert set(F.active_lenses(d)) == set()  # nothing saved yet: only v0 by default
    F.save(d, vs)
    assert set(F.active_lenses(d)) == {"bb_v1"}
    msg = F.judge(d, vs)
    assert msg and "promoted" in msg and F.champion(vs)["v"] == 1 and F.challenger(vs) is None


def test_reflect_waits_for_enough_matured_labels(tmp_path):
    d = _setup(tmp_path, 20)
    vs = F.versions(d)
    now = datetime(2026, 11, 2, 21, 30, tzinfo=UTC)  # first weekday of November, after 16:00 ET
    assert F.reflect_due(vs, now)
    assert F.reflect(d, vs, now, use_gpu=True) is None and vs[-1]["status"] == "skipped"
    assert not F.reflect_due(vs, now)  # one try a month
