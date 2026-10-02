"""The bounded learning loop: proposals are checked by code, tests decide, the registry refuses repeats, and live
evidence promotes or retires (app/signals/registry.py, scripts/learn_loop.py). Synthetic data only."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.signals import registry as R

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import learn_loop as L


def frame(year: int, months: int, strength: float, n: int = 60, seed: int = 0) -> pd.DataFrame:
    """Releases where fwd5 follows momentum with the given strength; logodds is pure noise."""
    rng = np.random.default_rng(seed)
    rows = []
    for m in range(months):
        mom = rng.normal(size=n)
        rows.append(pd.DataFrame({
            "month": f"{year + m // 12}-{m % 12 + 1:02d}", "entry": f"{year + m // 12}-{m % 12 + 1:02d}-15",
            "momentum": mom, "eps_growth": rng.normal(size=n), "rev_growth": rng.normal(size=n),
            "logodds": rng.normal(size=n), "guidance": rng.choice(["raised", "none"], n), "tone": "positive",
            "sector": "Energy", "fwd5": strength * mom + rng.normal(size=n)}))
    return pd.concat(rows, ignore_index=True)


def test_validate_refuses_what_is_not_on_the_menu() -> None:
    ok = R.validate({"name": "Mom Up!", "terms": [{"field": "momentum", "sign": 1}, {"field": "guidance=raised",
                                                                                     "sign": -1}]})
    assert ok.name == "mom_up" and ok.key() == "+momentum,-guidance=raised"
    for bad in ({"terms": []}, {"terms": [{"field": "price_target", "sign": 1}]},
                {"terms": [{"field": "momentum", "sign": 2}]}, {"terms": [{"field": "guidance=great", "sign": 1}]},
                {"terms": [{"field": "momentum", "sign": 1}] * 2},
                {"terms": [{"field": f, "sign": 1} for f in ("momentum", "logodds", "eps_growth", "rev_growth")]},
                {"terms": [{"field": "momentum", "sign": 1}], "sectors": ["Crypto"]}):
        with pytest.raises(R.SpecError):
            R.validate(bad)


def test_tests_pass_a_real_edge_and_fail_noise() -> None:
    edge, noise = R.validate({"terms": [{"field": "momentum", "sign": 1}]}), \
        R.validate({"terms": [{"field": "eps_growth", "sign": 1}]})
    tr, ho = frame(2024, 12, 0.3), frame(2025, 14, 0.3, seed=1)
    assert R.train_test(tr, edge)["pass"] and not R.train_test(tr, noise)["pass"]
    assert R.holdout_test(ho, edge, 1)["pass"]
    assert not R.holdout_test(frame(2025, 14, 0.0, seed=2), edge, 1)["pass"]
    assert R.holdout_test(ho, edge, 1)["p_needed"] == 0.025
    assert R.holdout_test(ho, edge, 50)["p_needed"] == round(0.05 / (50 * 51), 6)  # the bar rises with every test


def test_registry_refuses_repeats_and_grid_skips_them(tmp_path: Path) -> None:
    reg = R.Registry(tmp_path / "r.json")
    reg.add(R.validate({"name": "a", "terms": [{"field": "momentum", "sign": 1}]}))
    with pytest.raises(R.SpecError):
        reg.add(R.validate({"name": "b", "terms": [{"field": "momentum", "sign": 1}]}))
    reg.save()
    reg2 = R.Registry(tmp_path / "r.json")
    assert "+momentum" not in {g.key() for g in L.grid(reg2, 10_000, 1)}
    assert [g.key() for g in L.grid(reg2, 5, 7)] == [g.key() for g in L.grid(reg2, 5, 7)]  # seeded


def test_monthly_runs_once_and_sends_only_passing_signals_to_shadow(tmp_path: Path, monkeypatch) -> None:
    trials: list[dict] = []
    monkeypatch.setattr("app.sandbox.dsr.register", trials.append)
    picks = [R.validate({"name": "edge", "terms": [{"field": "momentum", "sign": 1}]}),
             R.validate({"name": "noise", "terms": [{"field": "eps_growth", "sign": 1}]})]
    monkeypatch.setattr(L, "propose", lambda reg, n, seed, gpu: picks)
    s = L.monthly(tmp_path, False, date(2026, 11, 7), frame(2024, 12, 0.3), frame(2025, 14, 0.3, seed=1))
    assert s["signals"]["edge"]["status"] == "shadow" and s["signals"]["noise"]["status"] == "rejected_train"
    assert len(trials) == 3  # two train tests + one holdout, all registered
    assert L.monthly(tmp_path, False, date(2026, 11, 14)) == {}  # once per month
    assert R.Registry(tmp_path / "registry.json").holdout_tests == 1


HISTORY = frame(2025, 6, 0.3, seed=9)  # what live releases are ranked against (in production: 2024-26, ~3,200 rows)


def _reg(tmp: Path, name: str, field: str, status: str, since: str) -> None:
    reg = R.Registry(tmp / "registry.json")
    s = reg.add(R.validate({"name": name, "terms": [{"field": field, "sign": 1}]}))
    s.status = status
    s.note(since, status)
    reg.save()


def test_review_promotes_only_after_3_months_of_live_edge(tmp_path: Path) -> None:
    _reg(tmp_path, "edge", "momentum", "shadow", "2026-11-07")
    live = frame(2026, 16, 0.5, n=40, seed=3)
    live = live[live["month"] >= "2026-11"]
    assert L.review(tmp_path, tmp_path, date(2027, 1, 9), live, HISTORY)["signals"]["edge"]["status"] == "shadow"
    L.review(tmp_path, tmp_path, date(2027, 2, 13), live, HISTORY)
    assert R.Registry(tmp_path / "registry.json").signals["edge"].status == "promoted"
    rep = json.loads((tmp_path / "review.json").read_text())
    assert rep["sleeve"]["weight"] == R.SLEEVE_PER_SIGNAL


def test_review_retires_a_promoted_signal_that_turns_negative(tmp_path: Path) -> None:
    _reg(tmp_path, "faded", "momentum", "promoted", "2026-11-07")
    live = frame(2026, 16, -0.5, n=40, seed=4)
    L.review(tmp_path, tmp_path, date(2027, 3, 6), live[live["month"] >= "2026-11"], HISTORY)
    assert R.Registry(tmp_path / "registry.json").signals["faded"].status == "retired"


def test_review_retires_a_shadow_signal_after_6_months(tmp_path: Path) -> None:
    _reg(tmp_path, "slow", "momentum", "shadow", "2026-05-02")
    live = frame(2026, 16, -0.5, n=40, seed=5)
    L.review(tmp_path, tmp_path, date(2026, 11, 7), live[live["month"] >= "2026-05"], HISTORY)
    assert R.Registry(tmp_path / "registry.json").signals["slow"].status == "retired"


def test_without_the_gpu_proposals_come_from_the_grid(tmp_path: Path) -> None:
    props = L.propose(R.Registry(tmp_path / "r.json"), 5, 202611, use_gpu=False)
    assert len(props) == 5 and {p.proposer for p in props} == {"grid"}
    for p in props:
        R.validate({"terms": p.terms})  # every grid recipe is on the menu


def test_collect_keeps_new_releases_after_a_row_cut_off_by_a_power_loss(tmp_path, monkeypatch):
    """live_features.csv is the only copy of the live releases' fields (the feature file is rewritten every run).
    A cut-off row used to make every later collect fail at reading it."""
    days = pd.bdate_range(end="2026-09-30", periods=300)
    px = pd.DataFrame([{"Date": d.date().isoformat(), "Ticker": t, "Open": 100.0 + i, "Close": 100.0 + i}
                       for i, d in enumerate(days) for t in ("SPY", "XLI", "AAA", "BBB")])
    ev_dir, out = tmp_path / "events", tmp_path / "signals"
    ev_dir.mkdir()
    px.to_parquet(ev_dir / "prices.parquet")
    monkeypatch.setattr(L, "EV", tmp_path)

    def release(acc, t):
        pd.DataFrame([{"accession": acc, "ticker": t, "sector": "Industrials",
                       "accepted_utc": "2026-10-01T20:10:00"}]).to_csv(ev_dir / "events.csv", index=False)
        pd.DataFrame([{"accession": acc, "eps_q": 1.0, "eps_prior": 0.9, "rev_q": 10.0, "rev_prior": 9.0,
                       "guidance": "raised", "tone": "positive"}]).to_csv(tmp_path / "features_forward.csv", index=False)
    ref = frame(2024, 3, 0.0).assign(accepted_utc=lambda d: d["entry"] + "T21:00:00")
    release("a1", "AAA")
    assert L.collect(ev_dir, out, ref=ref) == 1
    store = out / "live_features.csv"
    with store.open("a") as f:
        f.write("a-torn,Industr")  # power loss in the middle of a row
    release("a2", "BBB")
    assert L.collect(ev_dir, out, ref=ref) == 1
    got = pd.read_csv(store, on_bad_lines="skip")
    assert list(got["accession"]) == ["a1", "a-torn", "a2"] or list(got["accession"]) == ["a1", "a2"]
    assert not np.isnan(got[got["accession"] == "a2"]["eps_q"].iloc[0])
    assert L.collect(ev_dir, out, ref=ref) == 0  # nothing is collected twice
    pit = pd.read_csv(out / "live_pit.csv")
    assert list(pit["accession"]) == ["a1", "a2"]  # each release's percentiles are stored once, when it is collected
    assert pit["pit:eps_growth"].between(0, 1).all() and pit["pit:guidance=raised"].between(0, 1).all()


def test_a_later_release_cannot_change_an_earlier_score() -> None:
    """Outside review, 1 Oct 2026: ranks were taken within the whole entry month, so releases filed later in the
    month re-ordered the earlier ones (A above B, then B above A). A percentile now uses earlier releases only."""
    sig = R.validate({"terms": [{"field": "momentum", "sign": 1}, {"field": "eps_growth", "sign": 1}]})
    past = frame(2026, 3, 0.0, n=60).assign(accepted_utc=lambda d: d["entry"] + "T12:00:00")
    two = pd.DataFrame({"month": "2026-04", "entry": "2026-04-01", "accepted_utc": ["2026-04-01T12:00:00",
                                                                                   "2026-04-02T12:00:00"],
                        "momentum": [2.0, -1.0], "eps_growth": [-1.0, 2.5], "rev_growth": 0.0, "logodds": 0.0,
                        "guidance": "none", "tone": "positive", "sector": "Energy", "fwd5": 0.0})

    def later(mom: list[float], eps: list[float]) -> pd.DataFrame:
        return two.iloc[[0] * len(mom)].assign(accepted_utc=[f"2026-04-{d:02d}T12:00:00" for d in range(3, 3 + len(mom))],
                                               momentum=mom, eps_growth=eps)
    alone = R.score(pd.concat([past, two], ignore_index=True), sig).iloc[-2:].tolist()
    for extra in (later([1.2, 1.4, 1.6, 1.8], [6, 7, 8, 9.0]), later([2.5, 2.6, 2.7, 2.8], [2, 3, 4, 4.5])):
        got = R.score(pd.concat([past, two, extra], ignore_index=True), sig).iloc[len(past):len(past) + 2].tolist()
        assert got == alone
    assert not any(np.isnan(alone))
    # the first 100 releases have nothing to be ranked against yet, so they carry no score
    assert R.score(past, sig).iloc[:100].isna().all() and R.score(past, sig).iloc[120:].notna().all()
    # ties count half, a missing value sits in the middle, rows with the same time do not see each other
    pct = R.pit_pct(pd.Series([1.0, 2.0, 2.0, 3.0, 2.0, np.nan, 0.5, 9.0]), pd.Series(list("abcdeffg")), min_prior=0)
    assert pct.tolist()[1:] == [1.0, 0.75, 1.0, 0.5, 0.5, 0.0, 1.0]


def test_the_holdout_budget_is_finite_and_the_old_one_was_not() -> None:
    assert sum(R.holdout_alpha(k) for k in range(1, 10_000)) < R.HOLDOUT_ALPHA
    assert sum(0.05 / k for k in range(1, 13)) > 0.15  # what "0.05 / tests ever run" added up to after a year
    assert [round(R.look_alpha(j), 4) for j in (1, 2, 3, 4)] == [0.1, 0.0333, 0.0167, 0.01]
    assert sum(R.look_alpha(j) for j in (1, 2, 3, 4)) < R.PROMOTE_ALPHA


def test_promotion_is_looked_at_four_times_not_every_week(tmp_path: Path) -> None:
    _reg(tmp_path, "flat", "momentum", "shadow", "2026-11-07")
    live = frame(2026, 20, 0.0, n=40, seed=6)  # no edge: it should never be promoted, and never looked at weekly
    live = live[live["month"] >= "2026-08"]

    def looks() -> list[dict]:
        return [h for h in R.Registry(tmp_path / "registry.json").signals["flat"].history if h["event"] == "look"]
    for day in (date(2027, 1, 9), date(2027, 1, 30)):               # before day 90: no look
        L.review(tmp_path, tmp_path, day, live, HISTORY)
    assert looks() == []
    for day in (date(2027, 2, 6), date(2027, 2, 13), date(2027, 2, 20), date(2027, 2, 27)):  # days 91 to 112
        L.review(tmp_path, tmp_path, day, live, HISTORY)
    assert [(h["n"], h["days"], h["level"]) for h in looks()] == [(1, [90], 0.1)]  # one look, then quiet weeks
    L.review(tmp_path, tmp_path, date(2027, 3, 13), live, HISTORY)          # day 126: the second look, a stricter level
    assert [(h["n"], h["days"], h["level"]) for h in looks()][1:] == [(2, [120], 0.0333)]
    L.review(tmp_path, tmp_path, date(2027, 5, 8), live, HISTORY)           # day 182: the looks for 150 and 180 fall together
    assert [(h["n"], h["days"]) for h in looks()][2:] == [(3, [150, 180])]
    assert R.Registry(tmp_path / "registry.json").signals["flat"].status == "retired" and len(looks()) <= 4
