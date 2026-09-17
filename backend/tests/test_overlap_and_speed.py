"""Overlap-aware statistics (horizon > step), Newton safeguards, GPU-lock path, and optimizations that must not
change any published number."""
import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from app.learning.linear import fit_logistic_newton_np
from app.learning.rl_agent import score_trader
from app.sandbox import agent_worker as aw
from app.sandbox import gpu_lock
from app.sandbox import walkforward as wf
from app.sandbox.clock import enforce_point_in_time, sandbox_scope
from app.sandbox.scoring import (
    _boot_ci,
    boot_indices,
    cross_sectional,
    overlap_block,
    paired_ic_gap,
    periods_per_year,
    probabilistic_sharpe,
)

BACKEND = Path(__file__).resolve().parents[1]


def test_block_and_annualization_helpers():
    assert overlap_block(5, 5) == 1 and overlap_block(20, 5) == 4 and overlap_block(21, 5) == 5
    assert overlap_block(1, 5) == 1 and overlap_block(10, 0) == 10
    assert periods_per_year(5) == 52 and periods_per_year(20) == 13


def test_block_one_is_exactly_the_published_bootstrap_draw():
    a, b = np.random.default_rng(0), np.random.default_rng(0)
    assert np.array_equal(boot_indices(a, 37, 50, block=1), b.integers(0, 37, size=(50, 37)))


def test_block_draws_are_contiguous_circular_runs():
    idx = boot_indices(np.random.default_rng(1), 10, 200, block=4)
    assert idx.shape == (200, 10) and idx.min() >= 0 and idx.max() < 10
    for row in idx:
        for k in range(0, 8, 4):
            assert all((row[k + j] - row[k]) % 10 == j for j in range(4))


def test_block_bootstrap_widens_the_ci_for_autocorrelated_series():
    rng = np.random.default_rng(3)
    e = rng.normal(size=400)
    x = np.convolve(e, np.ones(4), mode="valid") / 4  # overlapping 4-period sums, like 20-day returns every 5 days
    lo1, hi1 = _boot_ci(x, block=1)
    lo4, hi4 = _boot_ci(x, block=4)
    assert (hi4 - lo4) > 1.3 * (hi1 - lo1)


def _weekly_preds(n_weeks=60, names=20, seed=0):
    rng = np.random.default_rng(seed)
    out = []
    for w in range(n_weeks):
        c = date(2025, 1, 6) + timedelta(weeks=w)
        for i in range(names):
            r = float(rng.normal())
            out.append({"cutoff": c, "ticker": f"T{i}", "p": 0.5 + 0.1 * r + float(rng.normal()), "ret": r})
    return out


def test_scoring_defaults_unchanged_and_block_is_threaded_through():
    preds = _weekly_preds()
    assert cross_sectional(preds) == cross_sectional(preds, block=1)
    assert cross_sectional(preds, block=4)["rank_ic_mean"] == cross_sectional(preds)["rank_ic_mean"]
    other = _weekly_preds(seed=9)
    assert paired_ic_gap(preds, other) == paired_ic_gap(preds, other, block=1)
    assert paired_ic_gap(preds, other, block=4)["gap"] == paired_ic_gap(preds, other)["gap"]


def test_trader_sharpe_is_annualized_per_horizon():
    rng = np.random.default_rng(2)
    rows = [{"cutoff": w, "position": 1.0, "ret": float(rng.normal(0.002, 0.02))} for w in range(80)]
    five = score_trader(rows)
    assert five == score_trader(rows, horizon=5, step=5)  # published numbers unchanged
    twenty = score_trader(rows, horizon=20, step=5)
    assert twenty["sharpe_after_costs"] == pytest.approx(five["sharpe_after_costs"] / 2, abs=2e-3)
    assert twenty["mean_weekly_pct_after_costs"] == five["mean_weekly_pct_after_costs"]


def test_score_long_short_sharpe_uses_the_horizon():
    rng = np.random.default_rng(4)
    preds = [{"cutoff": date(2025, 1, 1) + timedelta(weeks=w), "p": 0.6, "up": True, "ret": float(rng.normal(0.01, 0.02))}
             for w in range(30)]
    assert wf.score(preds, "p")["ls_sharpe_ann"] == wf.score(preds, "p", 5)["ls_sharpe_ann"]
    assert wf.score(preds, "p", 20)["ls_sharpe_ann"] == pytest.approx(wf.score(preds, "p")["ls_sharpe_ann"] / 2, abs=0.01)


def test_psr_uses_effective_sample_length():
    r = np.random.default_rng(5).normal(0.004, 0.02, 64)
    assert probabilistic_sharpe(r, block=4) < probabilistic_sharpe(r)
    assert probabilistic_sharpe(r) == probabilistic_sharpe(r, block=1)


def _penalized_objective(w, rows, ys, l2=0.05):
    x = np.hstack([np.ones((len(rows), 1)), np.array(rows)])
    z = x @ np.array(w)
    return float(np.mean(np.logaddexp(0, z) - np.array(ys) * z) + l2 / 2 * np.sum(np.array(w[1:]) ** 2))


def test_newton_safeguard_on_nearly_separable_data():
    rng = np.random.default_rng(6)
    rows = [[float(v), float(rng.normal())] for v in rng.normal(scale=20, size=200)]
    ys = [int(r[0] > 0) for r in rows]
    for l2 in (0.05, 1e-4):
        w_std = aw.fit_logistic_newton(rows, ys, l2=l2)
        w_np = fit_logistic_newton_np(rows, ys, l2=l2)
        assert all(math.isfinite(v) for v in w_std + w_np)
        for w in (w_std, w_np):  # the safeguard never accepts a step that raises the objective
            assert _penalized_objective(w, rows, ys, l2) <= _penalized_objective([0.0] * 3, rows, ys, l2)
    # with the default penalty the problem is well conditioned: both reach the optimum (gradient ~ 0) and agree
    w_std, w_np = aw.fit_logistic_newton(rows, ys), fit_logistic_newton_np(rows, ys)
    assert np.allclose(w_std, w_np, atol=1e-6)
    x = np.hstack([np.ones((200, 1)), np.array(rows)])
    p = 1 / (1 + np.exp(-(x @ np.array(w_np))))
    g = x.T @ (p - np.array(ys)) / 200
    g[1:] += 0.05 * np.array(w_np[1:])
    assert np.abs(g).max() < 1e-6


def test_gpu_lock_path_ignores_xdg_runtime_dir(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/nonexistent/one")
    a = gpu_lock.lock_path()
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    assert gpu_lock.lock_path() == a and str(a).endswith(f"airp-gpu-{__import__('os').getuid()}.lock")


def _load_lap_test():
    spec = importlib.util.spec_from_file_location("lap_test", BACKEND / "scripts" / "lap_test.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_vectorized_lap_test_matches_least_squares_and_handles_tiny_input():
    mod = _load_lap_test()
    rng = np.random.default_rng(8)
    rows = [{"cutoff": f"w{w}", "p": float(rng.random()), "up": bool(rng.random() < 0.5), "lap": float(rng.random()),
             "p_up_recall": 0.5} for w in range(12) for _ in range(30)]
    out = mod.interaction_test(rows, n_boot=200)
    s = np.array([r["p"] - 0.5 for r in rows])
    lap = np.array([r["lap"] for r in rows])
    x = np.column_stack([np.ones(len(rows)), s, lap, s * lap])
    ref = np.linalg.lstsq(x, np.array([float(r["up"]) for r in rows]), rcond=None)[0]
    assert np.allclose([out["coef"][k] for k in ("a", "b_signal", "c_lap", "d_signal_x_lap")], ref, atol=1e-9)
    assert out["d_ci"][0] < out["d_ci"][1] and out["weeks"] == 12 and out["n"] == 360
    tiny = mod.interaction_test(rows[:30])
    assert tiny["leak_signature"] is False and tiny["coef"] is None


def _meta_arms_original(arms, cutoffs, window=400):
    """The implementation before indexing (kept here as the reference)."""
    ref = arms["llm_plain"]
    base_rate, selector, choices = [], [], []
    for c in cutoffs:
        with sandbox_scope(as_of=wf._dt(c), run_id=f"meta-{c}"):
            resolved_ref = [p for p in ref if p["resolve_date"] <= c]
            for p in resolved_ref:
                enforce_point_in_time(wf._dt(p["resolve_date"]), source="meta:base_rate")
            rate = (sum(p["up"] for p in resolved_ref) / len(resolved_ref)) if resolved_ref else 0.5
            rate = min(max(rate, 0.05), 0.95)
            briers = {}
            for name, ps in arms.items():
                hist = [p for p in ps if p["resolve_date"] <= c][-window:]
                if len(hist) >= 100:
                    briers[name] = sum((p["p"] - p["up"]) ** 2 for p in hist) / len(hist)
            chosen = min(briers, key=lambda k: briers[k]) if briers else "base_rate"
        choices.append({"cutoff": c.isoformat(), "chosen": chosen,
                        "rolling_brier": {k: round(v, 4) for k, v in briers.items()}})
        idx = {name: {p["ticker"]: p for p in ps if p["cutoff"] == c} for name, ps in arms.items()}
        for p in ref:
            if p["cutoff"] != c:
                continue
            base_rate.append({**p, "p": rate})
            src_p = rate if chosen == "base_rate" else idx[chosen][p["ticker"]]["p"]
            selector.append({**p, "p": src_p, "chosen": chosen})
    return base_rate, selector, choices


@pytest.mark.parametrize("shuffle", [False, True])
def test_indexed_meta_arms_are_identical_to_the_original(shuffle):
    rng = np.random.default_rng(10)
    cutoffs = [date(2025, 1, 6) + timedelta(weeks=w) for w in range(40)]
    arms = {}
    for name, skill in (("llm_plain", 0.0), ("feat", 0.3), ("good", 0.8)):
        rows = []
        for c in cutoffs:
            for i in range(15):
                up = bool(rng.random() < 0.52)
                p = float(np.clip(0.5 + skill * (0.2 if up else -0.2) + rng.normal(0, 0.1), 0.01, 0.99))
                rows.append({"cutoff": c, "ticker": f"T{i}", "resolve_date": c + timedelta(days=7), "up": up, "p": p})
        if shuffle and name != "llm_plain":
            rng.shuffle(rows)  # out-of-order resolve dates take the original scan
        arms[name] = rows
    assert wf.point_in_time_meta_arms(arms, cutoffs, window=120) == _meta_arms_original(arms, cutoffs, window=120)
