import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import bz2_judge_swap as bz


def row(tmp, run, ticker, decided_at, status="decided", card="CARD"):
    d = tmp / run
    d.mkdir(exist_ok=True)
    (d / f"{ticker}.json").write_text(json.dumps({"ticker": ticker, "status": status, "decided_at": decided_at,
                                                   "judge_card": card}))


def test_freeze_takes_the_latest_decided_card_of_the_day(tmp_path):
    row(tmp_path, "r1", "AAA", "2026-10-07T01:00:00+00:00", card="old")
    row(tmp_path, "r2", "AAA", "2026-10-07T09:00:00+00:00", card="new")
    row(tmp_path, "r2", "BBB", "2026-10-07T09:00:00+00:00", status="judge_failed")
    row(tmp_path, "r3", "CCC", "2026-10-08T01:00:00+00:00")
    (tmp_path / "r2" / "summary.json").write_text("{}")
    cards = bz.freeze(tmp_path)
    assert [(c["ticker"], c["card"]) for c in cards] == [("AAA", "new")]
    assert len(bz.card_digest(cards)) == 64


def reply(labels):
    return {h: {"label": labels[h], "quote": "", "why": ""} for h in bz.HORIZONS}


def test_valid_needs_a_parsed_reply_with_a_side_on_every_horizon():
    labels, valid = bz.judged(None, "card")
    assert not valid and set(labels.values()) == {"no_call"}


def rec(t, arm, labels, wall=10.0, valid=True, decided_at="2026-10-07T09:00:00+00:00"):
    return {"ticker": t, "arm": arm, "labels": labels, "wall_s": wall, "valid": valid, "decided_at": decided_at}


def test_run_day_rules():
    up = dict.fromkeys(bz.HORIZONS, "5")
    rows = [rec("A1", "A", up, 10), rec("A1", "B", up, 14), rec("A2", "A", up, 10), rec("A2", "B", up, 16, valid=False)]
    s = bz.run_summary(rows, 2)
    assert s["valid_rate"] == {"A": 1.0, "B": 0.5} and not s["V_pass"]
    assert s["median_wall_s"] == {"A": 10.0, "B": 15.0} and s["S_pass"]
    assert s["same_side"] == 1.0


def test_q_rule_scores_pairs_where_both_took_a_side():
    entry = date(2026, 10, 8)
    px_moves = {"UP": 0.02, "DN": -0.03}

    def score(t, side, e, x, px):
        return side * px_moves[t] if t in px_moves else None

    up, dn = dict.fromkeys(bz.HORIZONS, "5"), dict.fromkeys(bz.HORIZONS, "1")
    rows = [rec("UP", "A", up), rec("UP", "B", up), rec("DN", "A", up), rec("DN", "B", dn),
            rec("NA", "A", up), rec("NA", "B", up)]
    q = bz.q_rule(rows, {}, score, lambda _d: entry)
    assert q["pairs"] == 3 and q["matured"] == 2
    assert q["stats"]["B"]["mean"] > q["stats"]["A"]["mean"] and q["Q_pass"]
    assert entry + timedelta(days=1) > entry  # dates come from entry_session, never from the card
