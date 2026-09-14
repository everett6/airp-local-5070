"""Deep RL agent: exact gradients, learns a planted signal, stays at the base rate on noise, trader after costs."""
from datetime import date, timedelta

import numpy as np
import pytest

from app.learning.rl_agent import (
    ContinualTrainer,
    PolicyNet,
    RLConfig,
    loss_and_grads,
    rewards,
    score_trader,
)


def test_gradients_match_finite_differences():
    cfg = RLConfig(hidden=(6, 5), weight_decay=1e-3, entropy=0.05)
    rng = np.random.default_rng(1)
    x, z = rng.normal(size=(30, 4)), rng.normal(size=30)
    y = (rng.random(30) < 0.5).astype(float)
    net = PolicyNet(4, cfg)
    for k in net.p:
        net.p[k] = net.p[k] + rng.normal(scale=0.3, size=net.p[k].shape)
    _, g = loss_and_grads(net, x, z, y, cfg, 1.0)
    for k, v in net.p.items():
        idx = tuple(int(rng.integers(0, s)) for s in v.shape)
        old, h = v[idx], 1e-6
        v[idx] = old + h
        lp = loss_and_grads(net, x, z, y, cfg, 1.0, want_grads=False)[0]["loss"]
        v[idx] = old - h
        lm = loss_and_grads(net, x, z, y, cfg, 1.0, want_grads=False)[0]["loss"]
        v[idx] = old
        assert (lp - lm) / (2 * h) == pytest.approx(g[k][idx], rel=1e-4, abs=1e-8), k


def test_rewards_flat_is_zero_and_costs_apply():
    cfg = RLConfig(cost_bps=10, risk=0.0)
    r = rewards(np.array([1.0, -1.0]), cfg, ret_std=0.01)
    assert r[:, 1].tolist() == [0.0, 0.0]
    assert r[0, 2] == pytest.approx(1.0 - 0.1) and r[0, 0] == pytest.approx(-1.0 - 0.1)


def _records(n_weeks, per_week, signal, seed):
    rng = np.random.default_rng(seed)
    recs = []
    start = date(2025, 6, 2)
    for w in range(n_weeks):
        c = start + timedelta(days=7 * w)
        for _ in range(per_week):
            x = rng.normal(size=5)
            ret = 0.02 * (signal * np.tanh(x[0]) + rng.normal())
            recs.append({"cutoff": c, "x": x.tolist(), "ret": float(ret), "up": ret > 0})
    return recs


def test_learns_a_planted_signal_and_trades_it_after_costs():
    recs = _records(12, 100, signal=1.2, seed=0)
    agent = ContinualTrainer(5, RLConfig(max_epochs=300))
    res = agent.fit(recs)
    assert res.forecast_adopted and res.trader_adopted, res.as_dict()
    test = _records(4, 200, signal=1.2, seed=9)
    p, pos = agent.act(np.array([r["x"] for r in test]))
    up = np.array([r["up"] for r in test])
    assert ((p >= 0.5) == up).mean() > 0.6
    rows = [{"cutoff": r["cutoff"], "position": float(q), "ret": r["ret"]} for r, q in zip(test, pos, strict=True)]
    assert score_trader(rows)["mean_weekly_pct_after_costs"] > 0


def test_pure_noise_falls_back_to_base_rate_and_flat():
    recs = _records(12, 100, signal=0.0, seed=3)
    agent = ContinualTrainer(5, RLConfig(max_epochs=300))
    res = agent.fit(recs)
    p, pos = agent.act(np.array([r["x"] for r in _records(1, 50, 0.0, seed=4)]))
    if not res.forecast_adopted:
        assert np.allclose(p, res.base_rate)
    else:  # if noise happened to pass the guard, the forecasts must still hug the base rate
        assert np.abs(p - res.base_rate).max() < 0.1
    if not res.trader_adopted:
        assert np.allclose(pos, 0.0)
    assert np.abs(p - 0.5).max() < 0.15


def test_not_enough_data_means_no_opinion():
    agent = ContinualTrainer(5)
    res = agent.fit(_records(2, 50, 1.0, seed=1))
    assert res.status == "not_enough_data"
    p, pos = agent.act(np.zeros((3, 5)))
    assert p.tolist() == [0.5] * 3 and pos.tolist() == [0.0] * 3


def test_training_is_deterministic():
    recs = _records(8, 60, signal=0.8, seed=5)
    outs = []
    for _ in range(2):
        a = ContinualTrainer(5, RLConfig(max_epochs=60))
        a.fit(recs)
        outs.append(a.act(np.array([r["x"] for r in recs[:10]])))
    assert np.array_equal(outs[0][0], outs[1][0]) and np.array_equal(outs[0][1], outs[1][1])
