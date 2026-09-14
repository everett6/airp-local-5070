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
