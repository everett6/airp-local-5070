"""Small numpy logistic regression with standardization and L2, for walk-forward baselines."""
from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

Arr = NDArray[np.float64]


class Logistic:
    def __init__(self, l2: float = 1.0, iters: int = 400, lr: float = 0.5) -> None:
        self.l2, self.iters, self.lr = l2, iters, lr
        self.w: Arr | None = None
        self.mu: Arr | None = None
        self.sd: Arr | None = None

    def fit(self, x: Arr, y: Arr) -> Logistic:
        self.mu, self.sd = x.mean(axis=0), x.std(axis=0) + 1e-6
        xs = np.clip((x - self.mu) / self.sd, -5, 5)
        xb = np.hstack([xs, np.ones((len(xs), 1))])
        w = np.zeros(xb.shape[1])
        n = len(xb)
        for _ in range(self.iters):
            p = 1 / (1 + np.exp(-np.clip(xb @ w, -30, 30)))
            grad = xb.T @ (p - y) / n
            grad[:-1] += self.l2 * w[:-1] / n  # no penalty on the intercept
            w -= self.lr * grad
        self.w = w
        return self

    def predict(self, x: Arr) -> Arr:
        if self.w is None or self.mu is None or self.sd is None:
            return np.full(len(x), 0.5)
        xs = np.clip((x - self.mu) / self.sd, -5, 5)
        out: Arr = 1 / (1 + np.exp(-np.clip(np.hstack([xs, np.ones((len(xs), 1))]) @ self.w, -30, 30)))
        return np.clip(out, 0.01, 0.99)


def fit_logistic_newton_np(rows: list[list[float]], ys: list[int], l2: float = 0.05, iters: int = 25,
                           tol: float = 1e-9) -> list[float]:
    """numpy twin of `app.sandbox.agent_worker.fit_logistic_newton` (same objective: mean log loss +
    l2/2 * |weights|^2 with the intercept unpenalized; same [intercept, *weights] output) for the orchestrator,
    where numpy is available. The jailed agent keeps the stdlib version."""
    x = np.hstack([np.ones((len(rows), 1)), np.asarray(rows, dtype=float)])
    y = np.asarray(ys, dtype=float)
    n, k = x.shape
    pen = np.full(k, l2)
    pen[0] = 0.0
    w = np.zeros(k)

    def objective(v: Arr) -> float:
        z = x @ v
        return float(np.mean(np.logaddexp(0.0, z) - y * z) + 0.5 * np.sum(pen * v * v))

    f_w = objective(w)
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(x @ w, -30, 30)))
        g = x.T @ (p - y) / n + pen * w
        h = (x * (p * (1 - p))[:, None]).T @ x / n + np.diag(pen)
        h[0, 0] += 1e-9
        step = np.linalg.solve(h, g)
        t = 1.0
        while True:  # full Newton step unless it would raise the objective (same rule as the stdlib version)
            cand = w - t * step
            f_cand = objective(cand)
            if f_cand <= f_w + 1e-12 or t < 1e-8:
                break
            t /= 2
        w, f_w = cand, f_cand
        if np.abs(t * step).max() < tol:
            break
    return [float(v) for v in w]
