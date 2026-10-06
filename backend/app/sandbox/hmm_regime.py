"""Two-state Gaussian hidden Markov model for the H1 leverage guardrail (docs/PLAN_60_V2.md, "Regime guardrail H1").

Plain Python floats on purpose: with two states the per-step loops are faster than small numpy arrays.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

TWO_PI = 2 * math.pi


@dataclass(frozen=True)
class HMM:
    pi: tuple[float, float]
    a: tuple[tuple[float, float], tuple[float, float]]
    mu: tuple[float, float]
    var: tuple[float, float]
    loglik: float = float("nan")
    iterations: int = 0

    @property
    def panic(self) -> int:
        """The state with the larger variance."""
        return 0 if self.var[0] > self.var[1] else 1


def _pdf(x: float, mu: float, var: float) -> float:
    return math.exp(-(x - mu) ** 2 / (2 * var)) / math.sqrt(TWO_PI * var) + 1e-300


def start(x: np.ndarray) -> HMM:
    """The spec's fixed start: means 0, variances 0.5x and 2x the sample variance, diagonal 0.98, start 0.5/0.5."""
    v = float(np.var(x))
    return HMM((0.5, 0.5), ((0.98, 0.02), (0.02, 0.98)), (0.0, 0.0), (0.5 * v, 2 * v))


def forward(x: np.ndarray, m: HMM) -> tuple[np.ndarray, np.ndarray, float]:
    """Scaled forward pass: filtered probabilities P(state | x_1..x_t), the scale factors and the log-likelihood."""
    n = len(x)
    alpha = np.empty((n, 2))
    scale = np.empty(n)
    (a00, a01), (a10, a11) = m.a
    p0, p1 = m.pi
    f0 = f1 = 0.0
    ll = 0.0
    for t in range(n):
        xt = float(x[t])
        if t:
            p0, p1 = f0 * a00 + f1 * a10, f0 * a01 + f1 * a11
        e0, e1 = p0 * _pdf(xt, m.mu[0], m.var[0]), p1 * _pdf(xt, m.mu[1], m.var[1])
        c = e0 + e1
        f0, f1 = e0 / c, e1 / c
        alpha[t, 0], alpha[t, 1], scale[t] = f0, f1, c
        ll += math.log(c)
    return alpha, scale, ll


def fit(x: np.ndarray, iterations: int = 200, tol: float = 1e-6) -> HMM:
    """Baum-Welch from the fixed start; stops when the log-likelihood gains less than `tol`."""
    x = np.asarray(x, dtype=float)
    m, prev, n = start(x), -math.inf, len(x)
    floor = 1e-4 * float(np.var(x))
    for it in range(1, iterations + 1):
        alpha, scale, ll = forward(x, m)
        (a00, a01), (a10, a11) = m.a
        beta = np.empty((n, 2))
        b0 = b1 = 1.0
        beta[-1] = 1.0
        x00 = x01 = x10 = x11 = 0.0
        for t in range(n - 1, 0, -1):
            xt = float(x[t])
            e0, e1 = _pdf(xt, m.mu[0], m.var[0]) * b0, _pdf(xt, m.mu[1], m.var[1]) * b1
            f0, f1, c = alpha[t - 1, 0], alpha[t - 1, 1], scale[t]
            x00 += f0 * a00 * e0 / c
            x01 += f0 * a01 * e1 / c
            x10 += f1 * a10 * e0 / c
            x11 += f1 * a11 * e1 / c
            b0, b1 = (a00 * e0 + a01 * e1) / c, (a10 * e0 + a11 * e1) / c
            beta[t - 1, 0], beta[t - 1, 1] = b0, b1
        g = alpha * beta
        g /= g.sum(axis=1, keepdims=True)
        w = g.sum(axis=0)
        mu = (g * x[:, None]).sum(axis=0) / w
        var = np.maximum((g * (x[:, None] - mu) ** 2).sum(axis=0) / w, floor)
        r0, r1 = x00 + x01, x10 + x11
        m = HMM((float(g[0, 0]), float(g[0, 1])), ((x00 / r0, x01 / r0), (x10 / r1, x11 / r1)),
                (float(mu[0]), float(mu[1])), (float(var[0]), float(var[1])), ll, it)
        if ll - prev < tol:
            break
        prev = ll
    return m


def panic_probability(x: np.ndarray, m: HMM) -> np.ndarray:
    """P(panic state | returns up to each day), the filter run over all of x."""
    return forward(np.asarray(x, dtype=float), m)[0][:, m.panic]
