"""
Portfolio construction primitives: mean-variance optimization, risk-budget
based position sizing, and concentration checks. Deliberately conservative
implementations (no leverage, long-only default) since this system proposes
allocations for human review, not autonomous execution.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class OptimizationResult:
    weights: dict[str, float]
    expected_return: float
    expected_volatility: float
    sharpe: float


def min_variance_weights(
    tickers: list[str],
    covariance_matrix: list[list[float]],
    long_only: bool = True,
) -> dict[str, float]:
    """Closed-form global minimum-variance portfolio: w = Sigma^-1 * 1 / (1' Sigma^-1 1).
    If long_only and the closed form produces negative weights, falls back to
    a projected-gradient style clip-and-renormalize (documented approximation,
    not a full QP solver — good enough for a research proposal, and flagged
    as approximate in the returned metadata via the caller)."""
    sigma = np.array(covariance_matrix)
    n = len(tickers)
    ones = np.ones(n)
    sigma_inv = np.linalg.pinv(sigma)
    raw = sigma_inv @ ones
    raw = raw / (ones @ sigma_inv @ ones)

    if long_only and np.any(raw < 0):
        raw = np.clip(raw, 0, None)
        raw = raw / raw.sum()

    return dict(zip(tickers, raw.tolist()))


def mean_variance_optimize(
    tickers: list[str],
    expected_returns: list[float],
    covariance_matrix: list[list[float]],
    risk_aversion: float = 3.0,
    long_only: bool = True,
) -> OptimizationResult:
    """Markowitz mean-variance with a risk-aversion parameter (utility =
    w'mu - (risk_aversion/2) w'Sigma w). Unconstrained closed form is
    w = (1/risk_aversion) * Sigma^-1 * mu; long-only enforced by clip +
    renormalize, same approximation caveat as `min_variance_weights`."""
    mu = np.array(expected_returns)
    sigma = np.array(covariance_matrix)
    sigma_inv = np.linalg.pinv(sigma)

    raw = (1.0 / risk_aversion) * (sigma_inv @ mu)
    if raw.sum() <= 0:
        raw = np.ones(len(tickers))
    if long_only:
        raw = np.clip(raw, 0, None)
    if raw.sum() == 0:
        raw = np.ones(len(tickers))
    weights = raw / raw.sum()

    port_return = float(weights @ mu)
    port_vol = float(np.sqrt(weights @ sigma @ weights))
    sharpe = port_return / port_vol if port_vol > 0 else 0.0

    return OptimizationResult(
        weights=dict(zip(tickers, weights.tolist())),
        expected_return=port_return,
        expected_volatility=port_vol,
        sharpe=sharpe,
    )


@dataclass(frozen=True)
class ConcentrationCheck:
    ticker: str
    proposed_weight: float
    sector: str
    sector_weight_after: float
    breaches_position_limit: bool
    breaches_sector_limit: bool


def check_concentration_limits(
    proposed_weight: float,
    ticker: str,
    sector: str,
    current_sector_weights: dict[str, float],
    max_single_position: float = 0.08,
    max_sector_weight: float = 0.30,
) -> ConcentrationCheck:
    sector_weight_after = current_sector_weights.get(sector, 0.0) + proposed_weight
    return ConcentrationCheck(
        ticker=ticker,
        proposed_weight=proposed_weight,
        sector=sector,
        sector_weight_after=sector_weight_after,
        breaches_position_limit=proposed_weight > max_single_position,
        breaches_sector_limit=sector_weight_after > max_sector_weight,
    )


def risk_budget_position_size(
    portfolio_risk_budget: float,
    position_volatility: float,
    position_liquidity_score: float,
    max_position_weight: float = 0.08,
) -> float:
    """Sizes a position so its standalone volatility contribution roughly
    consumes a fraction of the portfolio's total risk budget, then scales
    down by a liquidity penalty (0..1, 1 = fully liquid) so illiquid names
    can't be sized as if they were mega-caps."""
    if position_volatility <= 0:
        raise ValueError("position_volatility must be positive")
    raw_weight = portfolio_risk_budget / position_volatility
    liquidity_adjusted = raw_weight * max(0.0, min(1.0, position_liquidity_score))
    return min(liquidity_adjusted, max_position_weight)
