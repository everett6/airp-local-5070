"""The AI's contribution, apart from execution (scripts/ai_contribution.py). Made-up releases and prices."""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ai_contribution as C

from app.portfolio import sleeve

DAYS = pd.bdate_range("2026-10-01", periods=40)
ETF = {"Energy": "XLE"}


def world(good_is_high_score: bool) -> tuple[list[dict], pd.DataFrame, pd.DataFrame]:
    """Twelve releases, one each second trading day, half of them above the threshold. The stocks of six of them
    gain 10% over the week after their entry; `good_is_high_score` says whether those are the ones the judge liked."""
    px = {"XLE": np.full(len(DAYS), 50.0), "SPY": np.full(len(DAYS), 600.0)}
    recs: list[dict] = []
    for k in range(12):
        high = k % 2 == 0
        good = high == good_is_high_score
        entry = 2 + 2 * k
        t = f"S{k:02d}"
        px[t] = np.where(np.arange(len(DAYS)) >= entry + 3, 110.0 if good else 100.0, 100.0)
        decided = DAYS[entry - 1]  # the evening before its entry open
        as_of = datetime(decided.year, decided.month, decided.day, 22, 30, tzinfo=UTC)
        dl = datetime(DAYS[entry].year, DAYS[entry].month, DAYS[entry].day, 13, 30, tzinfo=UTC)
        recs.append({"type": "decision", "accession": f"a{k}", "ticker": t, "sector": "Energy",
                     "logodds": 4.0 if high else 1.0, "source": "bonsai", "on_time": True,
                     "entry_deadline": dl.isoformat()})
        recs.append({"type": "run", "as_of": as_of.isoformat()})
    for d in DAYS[26:]:  # later runs, so every pair is closed in the replay
        recs.append({"type": "run", "as_of": datetime(d.year, d.month, d.day, 22, 30, tzinfo=UTC).isoformat()})
    df = pd.DataFrame(px, index=DAYS)
    return recs, df, df


END = datetime(2026, 11, 25, tzinfo=UTC)


def test_a_judge_with_skill_beats_the_same_sleeve_dealt_at_random() -> None:
    recs, o, c = world(good_is_high_score=True)
    rep = C.contribution(recs, o, c, ETF, END, shuffles=200)
    assert rep["on_time_bonsai_decisions"] == 12 and rep["qualifying"] == 6
    assert rep["ai"]["closed"] == 6 and rep["ai"]["mean_ret_per_pair"] > 0.08
    assert rep["ai_minus_shuffled"] > 0.03 and rep["share_of_deals_ai_beats"] > 0.95
    assert rep["shuffled"]["p05"] < rep["shuffled"]["mean_net_return"] < rep["shuffled"]["p95"]
    assert abs(rep["shuffled"]["mean_pairs"] - 6) < 0.5          # the same number of picks, chosen by chance
    assert rep["every_release"]["pairs"] > rep["ai"]["pairs"]    # more trades, more turnover, the same rules
    assert rep["every_release"]["turnover"] > rep["ai"]["turnover"] > 0
    assert 0 < rep["ai"]["mean_gross_exposure"] < 1 and rep["ai"]["costs"] > 0


def test_a_judge_without_skill_shows_as_much() -> None:
    recs, o, c = world(good_is_high_score=False)
    rep = C.contribution(recs, o, c, ETF, END, shuffles=200)
    assert rep["ai"]["net_return"] < 0 and rep["ai_minus_shuffled"] < -0.03 and rep["share_of_deals_ai_beats"] < 0.05


def test_the_replay_is_the_live_sleeve_and_nothing_else_differs() -> None:
    recs, o, c = world(True)
    live = C.replay(C.run_times(recs), o, c, ETF, None, END)
    assert C.contribution(recs, o, c, ETF, END, shuffles=0, live_book=live)["replay_matches_live_book"] is True
    live["pairs"][0]["qty"] += 1
    assert C.contribution(recs, o, c, ETF, END, shuffles=0, live_book=live)["replay_matches_live_book"] is False
    # dealing every release the AI's own score for it is the AI's book: the arms differ only in the scores
    own = {r["accession"]: r["logodds"] for r in recs if r["type"] == "decision"}
    assert C.measure(C.replay(C.run_times(recs), o, c, ETF, own, END), 30) == C.measure(live | {"pairs": C.replay(
        C.run_times(recs), o, c, ETF, None, END)["pairs"]}, 30)
    assert C.contribution([], o, c, ETF, END)["note"] == "no completed run yet"
    assert sleeve.THRESHOLD < C.TAKE_ALL
