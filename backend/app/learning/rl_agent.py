"""
Deep reinforcement-learning layer on top of the jailed LLM agents (numpy only, deterministic).

Setting: every week, for every stock, the agent sees a state s (price features, point-in-time
fundamentals, and every LLM arm's probability) and picks a position a in {short, flat, long}.
Its reward once the week resolves is

    R(s, a) = a * z - cost * |a| - risk * a^2 * z^2,      z = forward return / training-return std

This is a one-step contextual bandit with FULL information: when the outcome resolves, the reward
of every action is known, not only the one taken. So the policy gradient is computed exactly over
all actions (expected policy gradient) with no sampling noise.

Network: shared MLP trunk (tanh) with three heads
  actor     softmax over {short, flat, long}, trained to maximize E_pi[R] + entropy bonus
  critic    V(s) ~ E_pi[R], the value of the current policy (reported; a baseline for diagnostics)
  forecast  P(up), trained on the log score (a proper scoring rule, so it stays calibrated)

`ContinualTrainer` retrains at every walk-forward cutoff on resolved outcomes only (the caller
guarantees and the walk-forward re-checks it), warm-starting from the previous cutoff, with
weight decay and time-ordered early stopping. A holdout guard adopts the new forecast head
only if it beats the base rate, and the new trader only if it beats staying flat, on the
most recent held-out weeks. Otherwise it falls back.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

Arr = NDArray[np.float64]
POSITIONS = np.array([-1.0, 0.0, 1.0])


def _softmax(x: Arr) -> Arr:
    e = np.exp(x - x.max(axis=1, keepdims=True))
    out: Arr = e / e.sum(axis=1, keepdims=True)
    return out


def _sigmoid(x: Arr) -> Arr:
    out: Arr = 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))
    return out


@dataclass
class RLConfig:
    hidden: tuple[int, int] = (32, 32)
    lr: float = 3e-3
    weight_decay: float = 1e-3
    entropy: float = 0.02
    cost_bps: float = 5.0        # per unit of position per week (conservative: full turnover each week)
    risk: float = 0.05
    actor_weight: float = 1.0
    critic_weight: float = 0.5
    max_epochs: int = 300
    patience: int = 25
    holdout_frac: float = 0.25
    min_records: int = 400
    min_cutoffs: int = 4
    seed: int = 0


class PolicyNet:
    def __init__(self, n_in: int, cfg: RLConfig) -> None:
        rng = np.random.default_rng(cfg.seed)
        h1, h2 = cfg.hidden

        def init(fan_in: int, fan_out: int) -> Arr:
            w: Arr = rng.normal(0.0, np.sqrt(1.0 / fan_in), size=(fan_in, fan_out))
            return w

        self.p: dict[str, Arr] = {
            "W1": init(n_in, h1), "b1": np.zeros(h1), "W2": init(h1, h2), "b2": np.zeros(h2),
            "Wa": init(h2, 3) * 0.1, "ba": np.array([0.0, 1.0, 0.0]),  # starts leaning flat
            "Wv": init(h2, 1) * 0.1, "bv": np.zeros(1),
            "Wf": init(h2, 1) * 0.1, "bf": np.zeros(1),
        }

    def forward(self, x: Arr) -> dict[str, Arr]:
        p = self.p
        h1 = np.tanh(x @ p["W1"] + p["b1"])
        h2 = np.tanh(h1 @ p["W2"] + p["b2"])
        return {"x": x, "h1": h1, "h2": h2, "pi": _softmax(h2 @ p["Wa"] + p["ba"]),
                "v": (h2 @ p["Wv"] + p["bv"])[:, 0], "p_up": _sigmoid((h2 @ p["Wf"] + p["bf"])[:, 0])}


def rewards(z: Arr, cfg: RLConfig, ret_std: float) -> Arr:
    """Reward of each action (columns short, flat, long) for standardized returns z."""
    cost = cfg.cost_bps / 1e4 / max(ret_std, 1e-6)
    a = POSITIONS[None, :]
    out: Arr = a * z[:, None] - cost * np.abs(a) - cfg.risk * a * a * (z[:, None] ** 2)
    return out


def loss_and_grads(net: PolicyNet, x: Arr, z: Arr, y: Arr, cfg: RLConfig, ret_std: float,
                   want_grads: bool = True) -> tuple[dict[str, float], dict[str, Arr]]:
    """Total loss = -actor_weight*(E_pi[R] + entropy*H) + log_loss + critic_weight*(V - E_pi[R])^2 + decay."""
    f = net.forward(x)
    n = len(x)
    R = rewards(z, cfg, ret_std)
    pi, v, p_up = f["pi"], f["v"], f["p_up"]
    exp_r = (pi * R).sum(axis=1)
    logpi = np.log(pi + 1e-12)
    ent = -(pi * logpi).sum(axis=1)
    eps = 1e-7
    logloss = -(y * np.log(p_up + eps) + (1 - y) * np.log(1 - p_up + eps))
    critic = (v - exp_r) ** 2
    decay = sum(float((net.p[k] ** 2).sum()) for k in ("W1", "W2", "Wa", "Wv", "Wf"))
    total = (-cfg.actor_weight * (exp_r + cfg.entropy * ent).mean() + logloss.mean()
             + cfg.critic_weight * critic.mean() + cfg.weight_decay * decay)
    stats = {"loss": float(total), "exp_reward": float(exp_r.mean()), "log_loss": float(logloss.mean()),
             "entropy": float(ent.mean()), "critic_mse": float(critic.mean())}
    if not want_grads:
        return stats, {}
    p = net.p
    # d(-actor*(E[R] + ent*H))/d logits: for softmax, dE/dlogit_k = pi_k (R_k - E[R]); dH/dlogit_k = -pi_k (log pi_k + H)
    d_obj = pi * (R - exp_r[:, None]) + cfg.entropy * (-pi * (logpi + ent[:, None]))
    # the critic target E_pi[R] depends on pi too; its gradient flows back into the actor logits
    d_crit_target = -2 * cfg.critic_weight * (v - exp_r)[:, None] * pi * (R - exp_r[:, None])
    g_logits = (-cfg.actor_weight * d_obj + d_crit_target) / n
    g_v = (2 * cfg.critic_weight * (v - exp_r) / n)[:, None]
    g_f = ((p_up - y) / n)[:, None]
    h2 = f["h2"]
    grads = {"Wa": h2.T @ g_logits, "ba": g_logits.sum(axis=0), "Wv": h2.T @ g_v, "bv": g_v.sum(axis=0),
             "Wf": h2.T @ g_f, "bf": g_f.sum(axis=0)}
    g_h2 = g_logits @ p["Wa"].T + g_v @ p["Wv"].T + g_f @ p["Wf"].T
    g_z2 = g_h2 * (1 - h2 ** 2)
    grads["W2"], grads["b2"] = f["h1"].T @ g_z2, g_z2.sum(axis=0)
    g_z1 = (g_z2 @ p["W2"].T) * (1 - f["h1"] ** 2)
    grads["W1"], grads["b1"] = x.T @ g_z1, g_z1.sum(axis=0)
    for k in ("W1", "W2", "Wa", "Wv", "Wf"):
        grads[k] = grads[k] + 2 * cfg.weight_decay * p[k]
    return stats, grads


class Adam:
    def __init__(self, params: dict[str, Arr], lr: float) -> None:
        self.lr, self.t = lr, 0
        self.m = {k: np.zeros_like(v) for k, v in params.items()}
        self.s = {k: np.zeros_like(v) for k, v in params.items()}

    def step(self, params: dict[str, Arr], grads: dict[str, Arr]) -> None:
        self.t += 1
        for k, g in grads.items():
            self.m[k] = 0.9 * self.m[k] + 0.1 * g
            self.s[k] = 0.999 * self.s[k] + 0.001 * g * g
            mh = self.m[k] / (1 - 0.9 ** self.t)
            sh = self.s[k] / (1 - 0.999 ** self.t)
            params[k] -= self.lr * mh / (np.sqrt(sh) + 1e-8)


@dataclass
class TrainResult:
    status: str                      # not_enough_data | adopted | rejected_forecast | rejected_trader | rejected
    n_train: int = 0
    n_holdout: int = 0
    epochs: int = 0
    holdout: dict[str, float] = field(default_factory=dict)
    forecast_adopted: bool = False
    trader_adopted: bool = False
    base_rate: float = 0.5

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "n_train": self.n_train, "n_holdout": self.n_holdout, "epochs": self.epochs,
                "holdout": {k: round(v, 5) for k, v in self.holdout.items()},
                "forecast_adopted": self.forecast_adopted, "trader_adopted": self.trader_adopted,
                "base_rate": round(self.base_rate, 4)}


class ContinualTrainer:
    """Walk-forward trainer: call `fit(records)` at each cutoff with RESOLVED records only, then `act(x)`."""

    def __init__(self, n_in: int, cfg: RLConfig | None = None) -> None:
        self.cfg = cfg or RLConfig()
        self.n_in = n_in
        self.net = PolicyNet(n_in, self.cfg)
        self.mu = np.zeros(n_in)
        self.sd = np.ones(n_in)
        self.ret_std = 0.03
        self.base_rate = 0.5
        self.forecast_ok = False
        self.trader_ok = False
        self.history: list[dict[str, Any]] = []

    def _norm(self, x: Arr) -> Arr:
        out: Arr = np.clip((x - self.mu) / self.sd, -5, 5)
        return out

    def _train(self, net: PolicyNet, x: Arr, z: Arr, y: Arr, epochs: int, x_val: Arr | None = None,
               z_val: Arr | None = None, y_val: Arr | None = None) -> tuple[int, dict[str, float]]:
        opt = Adam(net.p, self.cfg.lr)
        best: tuple[float, int, dict[str, Arr], dict[str, float]] = (np.inf, 0, copy.deepcopy(net.p), {})
        stale = 0
        for ep in range(1, epochs + 1):
            _, grads = loss_and_grads(net, x, z, y, self.cfg, self.ret_std)
            opt.step(net.p, grads)
            if x_val is None:
                continue
            assert z_val is not None and y_val is not None
            val, _ = loss_and_grads(net, x_val, z_val, y_val, self.cfg, self.ret_std, want_grads=False)
            if val["loss"] < best[0] - 1e-6:
                best, stale = (val["loss"], ep, copy.deepcopy(net.p), val), 0
            else:
                stale += 1
                if stale >= self.cfg.patience:
                    break
        if x_val is not None:
            net.p = best[2]
            return best[1], best[3]
        return epochs, {}

    def fit(self, records: list[dict[str, Any]]) -> TrainResult:
        """records: {"cutoff", "x": list[float], "ret": float, "up": bool}, all already resolved."""
        cfg = self.cfg
        cutoffs = sorted({r["cutoff"] for r in records})
        if len(records) < cfg.min_records or len(cutoffs) < cfg.min_cutoffs:
            res = TrainResult("not_enough_data", n_train=len(records))
            self.history.append(res.as_dict())
            return res
        split = cutoffs[int(len(cutoffs) * (1 - cfg.holdout_frac))]
        train = [r for r in records if r["cutoff"] < split]
        hold = [r for r in records if r["cutoff"] >= split]
        x_all = np.array([r["x"] for r in records], dtype=np.float64)
        tr_x = np.array([r["x"] for r in train], dtype=np.float64)
        # normalization and reward scale come from the training split only
        self.mu, self.sd = tr_x.mean(axis=0), tr_x.std(axis=0) + 1e-6
        self.ret_std = float(np.std([r["ret"] for r in train]) or 0.03)

        def arrays(rs: list[dict[str, Any]]) -> tuple[Arr, Arr, Arr]:
            x = self._norm(np.array([r["x"] for r in rs], dtype=np.float64))
            return x, np.array([r["ret"] for r in rs]) / self.ret_std, np.array([float(r["up"]) for r in rs])

        (xt, zt, yt), (xh, zh, yh) = arrays(train), arrays(hold)
        candidate = copy.deepcopy(self.net)  # warm start: continual learning from the previous cutoff
        epochs, val = self._train(candidate, xt, zt, yt, cfg.max_epochs, xh, zh, yh)
        f = candidate.forward(xh)
        base = float(yt.mean())
        eps = 1e-7
        base_ll = float(-(yh * np.log(base + eps) + (1 - yh) * np.log(1 - base + eps)).mean())
        model_ll = float(-(yh * np.log(f["p_up"] + eps) + (1 - yh) * np.log(1 - f["p_up"] + eps)).mean())
        R = rewards(zh, cfg, self.ret_std)
        pos = f["pi"] @ POSITIONS
        trader_reward = float((f["pi"] * R).sum(axis=1).mean())
        forecast_ok = model_ll < base_ll
        trader_ok = trader_reward > 0.0  # staying flat earns exactly 0
        # final model: continue from the same warm start on ALL resolved data for the selected epoch count
        final = copy.deepcopy(self.net)
        self.mu, self.sd = x_all.mean(axis=0), x_all.std(axis=0) + 1e-6
        self.ret_std = float(np.std([r["ret"] for r in records]) or 0.03)
        xa, za, ya = arrays(records)
        self._train(final, xa, za, ya, max(1, epochs))
        self.net = final
        self.base_rate = float(ya.mean())
        self.forecast_ok, self.trader_ok = forecast_ok, trader_ok
        status = "adopted" if forecast_ok and trader_ok else (
            "rejected_forecast" if trader_ok else "rejected_trader" if forecast_ok else "rejected")
        res = TrainResult(status, len(train), len(hold), epochs,
                          {"model_log_loss": model_ll, "base_log_loss": base_ll, "trader_reward": trader_reward,
                           "mean_abs_position": float(np.abs(pos).mean()), **{f"val_{k}": v for k, v in val.items()}},
                          forecast_ok, trader_ok, self.base_rate)
        self.history.append(res.as_dict())
        return res

    def act(self, x: Arr) -> tuple[Arr, Arr]:
        """(P(up), position in [-1, 1]) for each row, with guard fallbacks (base rate, flat)."""
        n = len(x)
        if not self.history or self.history[-1]["status"] == "not_enough_data":
            return np.full(n, 0.5), np.zeros(n)
        f = self.net.forward(self._norm(np.asarray(x, dtype=np.float64)))
        p_up = np.clip(f["p_up"], 0.01, 0.99) if self.forecast_ok else np.full(n, self.base_rate)
        pos = f["pi"] @ POSITIONS if self.trader_ok else np.zeros(n)
        return p_up, pos


def score_trader(rows: list[dict[str, Any]], cost_bps: float = 5.0, n_boot: int = 2000,
                 seed: int = 0, horizon: int = 5, step: int = 5) -> dict[str, float]:
    """Per-cutoff long/short P&L of positions after costs (|position| x cost each cutoff), Sharpe with a bootstrap CI
    over cutoffs. Each period's return covers `horizon` trading days, so the Sharpe is annualized with
    periods_per_year(horizon) (52 for the published 5-day runs); when horizon > step the outcome windows overlap and
    the CI uses a moving-block bootstrap. Defaults reproduce every published number."""
    from app.sandbox.scoring import boot_indices, overlap_block, periods_per_year
    by_cut: dict[Any, list[tuple[float, float]]] = {}
    for r in rows:
        by_cut.setdefault(r["cutoff"], []).append((r["position"], r["ret"]))
    weekly = np.array([np.mean([p * ret - cost_bps / 1e4 * abs(p) for p, ret in v]) for _, v in sorted(by_cut.items(),
                                                                                                        key=lambda kv: kv[0])])
    if len(weekly) < 2:
        return {"weeks": float(len(weekly))}
    gross = np.array([np.mean([p * ret for p, ret in v]) for _, v in sorted(by_cut.items(), key=lambda kv: kv[0])])
    ann = np.sqrt(periods_per_year(horizon))
    sharpe = float(weekly.mean() / (weekly.std(ddof=1) + 1e-12) * ann)
    rng = np.random.default_rng(seed)
    idx = boot_indices(rng, len(weekly), n_boot, overlap_block(horizon, step))
    boot = weekly[idx]
    boot_sharpe = boot.mean(axis=1) / (boot.std(axis=1, ddof=1) + 1e-12) * ann
    lo, hi = np.percentile(boot_sharpe, [2.5, 97.5])
    return {"weeks": float(len(weekly)), "mean_weekly_pct_after_costs": round(float(weekly.mean()) * 100, 4),
            "mean_weekly_pct_gross": round(float(gross.mean()) * 100, 4),
            "sharpe_after_costs": round(sharpe, 3), "sharpe_ci_lo": round(float(lo), 3), "sharpe_ci_hi": round(float(hi), 3),
            "mean_abs_position": round(float(np.mean([abs(r["position"]) for r in rows])), 4)}
