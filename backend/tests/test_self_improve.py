import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import numpy as np
import pandas as pd
import self_improve as F

from app.forward.ledger import Ledger


def test_clean_drops_names_tickers_and_long_lessons():
    names = {"NVDA", "abbott"}
    got = F.clean(["Weigh guidance changes above the headline beat.", "NVDA shows chips lead.",
                   "Abbott results are special.", "word " * 31, "", 7], names)
    assert got == ["Weigh guidance changes above the headline beat.", "7"]
    assert F.clean("not a list", names) == [] and len(F.clean(["ok rule"] * 9, names)) == 5


def _setup(tmp_path: Path, n: int = 60, v1_tracks: bool = True, v0_tracks: bool = False) -> Path:
    """n releases, 8 a week from 5 Oct 2026; v1 (and optionally v0) call the outcome's sign, the other is unrelated."""
    d = tmp_path / "events"
    d.mkdir()
    led = Ledger(d / "ledger.jsonl")
    v0, v1 = [], []
    for i in range(n):
        acc = f"a{i}"
        entry = (pd.Timestamp("2026-10-05") + pd.Timedelta(days=7 * (i // 8) + i % 5)).date().isoformat()
        fwd = 0.01 * (i % 10 - 4.5)
        led.append("outcome", accession=acc, entry=entry, fwd5=fwd)
        led.append("decision", accession=acc, ticker="T", entry_deadline="2027-06-01T09:30:00-04:00")
        base = {"accession": acc, "ticker": "T", "entry_deadline": "2027-06-01T09:30:00-04:00",
                "written_at": f"2026-10-02T12:{i // 60:02d}:{i % 60:02d}+00:00",
                "bull": [{"point": "p", "quote": "q", "verified": True}], "bear": [], "reason": "r"}
        track = "bullish" if fwd > 0 else "bearish"
        other = "bullish" if i % 3 == 0 else "bearish"
        v0.append(base | {"bb_read": track if v0_tracks else other})
        v1.append(base | {"bb_read": (track if v1_tracks else {"bullish": "bearish", "bearish": "bullish"}[track])})
    (d / "bull_bear.jsonl").write_text("".join(json.dumps(x) + "\n" for x in v0))
    (d / "bb_v1.jsonl").write_text("".join(json.dumps(x) + "\n" for x in v1))
    return d


LATER = datetime(2027, 3, 1, 21, 30, tzinfo=UTC)


def _vs(champ: int = 0) -> list[dict]:
    return [{"v": 0, "status": "champion" if champ == 0 else "retired", "lessons": [], "created_at": None},
            {"v": 1, "status": "challenger" if champ == 0 else "champion", "lessons": ["x"],
             "created_at": "2026-10-01T00:00:00+00:00"}]


def test_summary_is_contrastive_and_split_holds_back_the_newest(tmp_path):
    d = _setup(tmp_path)
    m = F.matured(d, 0)
    assert len(m) == 60
    s = F.summary(m)
    assert "Worst misses:" in s and "Best hits:" in s and "You said" in s
    train, held = F.split(m)
    assert len(train) == 40 and len(held) == 20 and train["written_at"].max() < held["written_at"].min()


def test_e_process_keeps_its_error_bound_under_the_null():
    rng = np.random.default_rng(1)
    hits = sum(F.e_process(list(rng.uniform(-1, 1, 40))) >= 20 for _ in range(2000))
    assert hits / 2000 <= 0.05
    assert F.e_process([0.5] * 30) >= 20 and F.e_process([]) == 1.0


def test_challenger_that_tracks_outcomes_is_promoted(tmp_path):
    d = _setup(tmp_path, 120)
    vs = _vs()
    assert set(F.active_lenses(d)) == set()  # nothing saved yet: only v0 by default
    F.save(d, vs)
    assert set(F.active_lenses(d)) == {"bb_v1"}
    msg = F.judge(d, vs, LATER)
    assert msg and "promoted" in msg and F.champion(vs)["v"] == 1 and F.challenger(vs) is None
    assert vs[1]["last_judged"]["e_better"] >= 20


def test_worse_challenger_is_retired_early(tmp_path):
    d = _setup(tmp_path, 120, v1_tracks=False, v0_tracks=True)
    vs = _vs()
    msg = F.judge(d, vs, LATER)
    assert msg and "reliably worse" in msg and F.challenger(vs) is None and F.champion(vs)["v"] == 0


def test_too_early_to_judge(tmp_path):
    d = _setup(tmp_path, 120)
    vs = _vs()
    assert F.judge(d, vs, datetime(2026, 10, 20, tzinfo=UTC)) is None and F.challenger(vs)["v"] == 1


def test_rollback_to_v0_when_the_champion_is_worse(tmp_path):
    d = _setup(tmp_path, 120, v1_tracks=False, v0_tracks=True)
    vs = _vs(champ=1)
    msg = F.judge(d, vs, LATER)
    assert msg and "rolled back" in msg and F.champion(vs)["v"] == 0


def test_merge_lessons_dedupes_and_caps():
    old = [f"rule {i}" for i in range(6)]
    got = F.merge_lessons(old, ["Rule 0!", "new a", "new b", "new c"])
    assert len(got) == F.KEEP_LESSONS and got[-1] == "new c" and "Rule 0!" in got and "rule 0" not in got


def test_choose_needs_to_beat_the_champion():
    cands = [{"lessons": ["a"], "held_ic": 0.10}, {"lessons": ["b"], "held_ic": 0.30}, {"lessons": [], "held_ic": 0.9}]
    assert F.choose(cands, 0.05)["lessons"] == ["b"] and F.choose(cands, 0.35) is None


def test_candidates_replay_the_held_back_releases(tmp_path, monkeypatch):
    import asyncio

    import net_read_shadow
    d = _setup(tmp_path, 60)
    m = F.matured(d, 0)
    train, held = F.split(m)
    fwd = dict(zip(m["accession"], m["fwd5"], strict=True))
    replies = iter(['{"lessons": ["Weigh guidance above the headline."]}', '{"lessons": []}',
                    '{"lessons": ["Discount one-off gains."]}'])

    async def ask(llm, system, user):
        assert "Best hits:" in user and not any(a in user for a in held["accession"] if len(a) > 2 and a + "." in user)
        return next(replies), False

    async def label(llm, r, lens="net_read", spec=None):
        good = "Weigh guidance" in spec[0]  # the first candidate's prompt calls the outcome right
        right = "bullish" if fwd[r["accession"]] > 0 else "bearish"
        return {"accession": r["accession"], "bb_read": right if good else "neutral"}
    monkeypatch.setattr(F, "ask", ask)
    monkeypatch.setattr(net_read_shadow, "label", label)
    _, cands, champ_ic = asyncio.run(F.candidates(None, d, {"lessons": []}, train, held))
    assert [c["held_ic"] is not None for c in cands] == [True, False, True]
    best = F.choose(cands, champ_ic)
    assert best is not None and best["lessons"] == ["Weigh guidance above the headline."] and best["held_ic"] > 0.8


def test_reflect_waits_for_enough_matured_labels(tmp_path):
    d = _setup(tmp_path, 20)
    vs = F.versions(d)
    now = datetime(2026, 11, 2, 21, 30, tzinfo=UTC)  # first weekday of November, after 16:00 ET
    assert F.reflect_due(vs, now)
    assert F.reflect(d, vs, now, use_gpu=True) is None and vs[-1]["status"] == "skipped"
    assert not F.reflect_due(vs, now)  # one try a month
