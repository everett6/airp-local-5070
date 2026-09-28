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
    assert R.holdout_test(ho, edge, 50)["p_needed"] == 0.001  # the bar rises with every holdout test


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
    assert L.review(tmp_path, tmp_path, date(2027, 1, 9), live)["signals"]["edge"]["status"] == "shadow"
    L.review(tmp_path, tmp_path, date(2027, 2, 13), live)
    assert R.Registry(tmp_path / "registry.json").signals["edge"].status == "promoted"
    rep = json.loads((tmp_path / "review.json").read_text())
    assert rep["sleeve"]["weight"] == R.SLEEVE_PER_SIGNAL


def test_review_retires_a_promoted_signal_that_turns_negative(tmp_path: Path) -> None:
    _reg(tmp_path, "faded", "momentum", "promoted", "2026-11-07")
    live = frame(2026, 16, -0.5, n=40, seed=4)
    L.review(tmp_path, tmp_path, date(2027, 3, 6), live[live["month"] >= "2026-11"])
    assert R.Registry(tmp_path / "registry.json").signals["faded"].status == "retired"


def test_review_retires_a_shadow_signal_after_6_months(tmp_path: Path) -> None:
    _reg(tmp_path, "slow", "momentum", "shadow", "2026-05-02")
    live = frame(2026, 16, -0.5, n=40, seed=5)
    L.review(tmp_path, tmp_path, date(2026, 11, 7), live[live["month"] >= "2026-05"])
    assert R.Registry(tmp_path / "registry.json").signals["slow"].status == "retired"


def test_without_the_gpu_proposals_come_from_the_grid(tmp_path: Path) -> None:
    props = L.propose(R.Registry(tmp_path / "r.json"), 5, 202611, use_gpu=False)
    assert len(props) == 5 and {p.proposer for p in props} == {"grid"}
    for p in props:
        R.validate({"terms": p.terms})  # every grid recipe is on the menu
