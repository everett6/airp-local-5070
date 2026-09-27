"""Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014, JPM 40(5)) and the project's trials registry.

The DSR is the probability that a strategy's true Sharpe is above the best Sharpe expected from N trials of pure
noise, allowing for skewness, kurtosis and sample length. Every sleeve/algorithm test appends one row to
results/trials_registry.jsonl so N counts everything ever tried, not just what was reported.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import kurtosis, norm, skew

EULER = 0.5772156649015329
REGISTRY = Path(__file__).resolve().parents[2] / "results" / "trials_registry.jsonl"


def expected_max_sharpe(n_trials: int, var_sharpe: float) -> float:
    """Expected maximum of n_trials Sharpe estimates drawn around a true Sharpe of zero (per-period units)."""
    if n_trials < 2 or var_sharpe <= 0:
        return 0.0
    return math.sqrt(var_sharpe) * ((1 - EULER) * norm.ppf(1 - 1 / n_trials)
                                    + EULER * norm.ppf(1 - 1 / (n_trials * math.e)))


def deflated_sharpe(returns: np.ndarray, n_trials: int, var_sharpe: float) -> float:
    """DSR for per-period returns; var_sharpe is the variance of the trials' per-period Sharpe ratios."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    t = len(r)
    sr = r.mean() / r.std(ddof=1)
    sr0 = expected_max_sharpe(n_trials, var_sharpe)
    g3, g4 = skew(r), kurtosis(r, fisher=False)
    denom = math.sqrt(max(1e-12, 1 - g3 * sr + (g4 - 1) / 4 * sr * sr))
    return float(norm.cdf((sr - sr0) * math.sqrt(t - 1) / denom))


def register(entry: dict[str, Any], path: Path = REGISTRY) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def load_registry(path: Path = REGISTRY) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
