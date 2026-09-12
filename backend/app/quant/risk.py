"""
Risk and performance metrics operating on return series / P&L distributions.
No LLM math; everything here takes numpy arrays or plain floats and returns
plain floats/dataclasses.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _to_array(returns: list[float]) -> np.ndarray:
    arr = np.asarray(returns, dtype=float)
    if arr.size == 0:
        raise ValueError("returns cannot be empty")
    return arr


def historical_var(returns: list[float], confidence: float = 0.95) -> float:
    """Historical (non-parametric) Value at Risk, as a positive loss fraction.
    Preferred default over parametric VaR because it makes no normality
    assumption — equity/options return distributions are fat-tailed."""
    arr = _to_array(returns)
    var_percentile = 100 * (1 - confidence)
    return float(-np.percentile(arr, var_percentile))


def conditional_var(returns: list[float], confidence: float = 0.95) -> float:
    """Expected shortfall: mean loss *beyond* the VaR threshold. Strictly more
    informative than VaR alone since VaR says nothing about tail severity."""
    arr = _to_array(returns)
    var_percentile = 100 * (1 - confidence)
    threshold = np.percentile(arr, var_percentile)
    tail = arr[arr <= threshold]
    if tail.size == 0:
        return float(-threshold)
    return float(-tail.mean())


def parametric_var(returns: list[float], confidence: float = 0.95) -> float:
    """Variance-covariance VaR assuming normal returns. Kept alongside historical
    VaR (never as the sole measure) so a report can flag when the two diverge
    sharply — that divergence itself is a risk signal (fat tails / skew)."""
    from scipy.stats import norm

    arr = _to_array(returns)
    mu, sigma = arr.mean(), arr.std(ddof=1)
    z = norm.ppf(1 - confidence)
    return float(-(mu + z * sigma))


def sharpe_ratio(returns: list[float], risk_free_rate_per_period: float = 0.0,
                  periods_per_year: int = 252) -> float:
    arr = _to_array(returns)
    excess = arr - risk_free_rate_per_period
    std = excess.std(ddof=1)
    # Guard against floating-point noise, not just exact zero: a "constant"
    # return series can std() to ~1e-18 rather than 0.0, which would otherwise
    # blow up into a meaningless, enormous Sharpe ratio.
    if std < 1e-12:
        return 0.0
    return float(excess.mean() / std * np.sqrt(periods_per_year))


def sortino_ratio(returns: list[float], risk_free_rate_per_period: float = 0.0,
                   periods_per_year: int = 252) -> float:
    arr = _to_array(returns)
    excess = arr - risk_free_rate_per_period
    downside = excess[excess < 0]
    downside_dev = np.sqrt(np.mean(downside ** 2)) if downside.size else 0.0
    if downside_dev == 0:
        return 0.0
    return float(excess.mean() / downside_dev * np.sqrt(periods_per_year))


@dataclass(frozen=True)
class DrawdownResult:
    max_drawdown: float           # positive fraction, e.g. 0.23 == -23%
    max_drawdown_start_idx: int
    max_drawdown_trough_idx: int
    current_drawdown: float
    drawdown_series: list[float]


def drawdown_analysis(cumulative_equity_curve: list[float]) -> DrawdownResult:
    arr = _to_array(cumulative_equity_curve)
    running_max = np.maximum.accumulate(arr)
    dd = (arr - running_max) / running_max
    trough_idx = int(np.argmin(dd))
    # start = last index at/before trough where equity equaled the running max
    start_idx = int(np.argmax(arr[: trough_idx + 1] == running_max[trough_idx]))
    return DrawdownResult(
        max_drawdown=float(-dd.min()),
        max_drawdown_start_idx=start_idx,
        max_drawdown_trough_idx=trough_idx,
        current_drawdown=float(-dd[-1]),
        drawdown_series=dd.tolist(),
    )


def kelly_fraction(win_probability: float, win_loss_ratio: float, kelly_multiplier: float = 0.5) -> float:
    """Kelly criterion for position sizing, with a fractional-Kelly multiplier
    (default half-Kelly) since full Kelly is provably too aggressive under
    real-world parameter uncertainty. Clamped to [0, 1]."""
    if not (0.0 < win_probability < 1.0):
        raise ValueError("win_probability must be in (0, 1)")
    if win_loss_ratio <= 0:
        raise ValueError("win_loss_ratio must be positive")
    edge = win_probability - (1 - win_probability) / win_loss_ratio
    raw = edge  # standard form: f* = p - q/b
    sized = max(0.0, min(1.0, raw * kelly_multiplier))
    return sized


def expected_value(outcomes: list[tuple[float, float]]) -> float:
    """outcomes: list of (probability, payoff). Probabilities validated to sum to ~1."""
    total_p = sum(p for p, _ in outcomes)
    if not (0.98 <= total_p <= 1.02):
        raise ValueError(f"probabilities must sum to ~1.0, got {total_p}")
    return sum(p * payoff for p, payoff in outcomes)


def correlation_matrix(returns_by_asset: dict[str, list[float]]) -> tuple[list[str], list[list[float]]]:
    names = list(returns_by_asset.keys())
    lengths = {len(v) for v in returns_by_asset.values()}
    if len(lengths) != 1:
        raise ValueError("all return series must have equal length")
    matrix = np.corrcoef(np.array([returns_by_asset[n] for n in names]))
    return names, matrix.tolist()


@dataclass(frozen=True)
class FactorExposure:
    factor_name: str
    beta: float
    t_stat: float
    r_squared: float


def factor_regression(asset_returns: list[float], factor_returns: dict[str, list[float]]) -> list[FactorExposure]:
    """Multi-factor OLS regression (e.g. Fama-French style) of asset returns
    on named factor return series. Uses ordinary least squares — no LLM,
    no black box."""
    y = _to_array(asset_returns)
    factor_names = list(factor_returns.keys())
    x = np.column_stack([_to_array(factor_returns[f]) for f in factor_names])
    x_design = np.column_stack([np.ones(len(y)), x])

    coeffs, _residuals, _rank, _sv = np.linalg.lstsq(x_design, y, rcond=None)
    y_hat = x_design @ coeffs
    resid = y - y_hat
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    n, k = x_design.shape
    dof = max(n - k, 1)
    sigma2 = ss_res / dof
    xtx_inv = np.linalg.pinv(x_design.T @ x_design)
    se = np.sqrt(np.diag(sigma2 * xtx_inv))

    exposures = []
    for i, name in enumerate(factor_names, start=1):  # skip intercept at index 0
        beta = float(coeffs[i])
        t_stat = float(beta / se[i]) if se[i] > 0 else 0.0
        exposures.append(FactorExposure(factor_name=name, beta=beta, t_stat=t_stat, r_squared=r_squared))
    return exposures


def stress_test(
    base_value: float,
    factor_exposures: dict[str, float],
    scenario_factor_shocks: dict[str, float],
) -> float:
    """Simple linear stress test: base_value * (1 + sum(beta_i * shock_i)).
    Intended for quick scenario deltas (e.g. -20% market, +150bps rates),
    not a substitute for full repricing where nonlinear payoffs (options)
    are involved — those must use Black-Scholes/binomial repricing instead."""
    shock_effect = sum(
        factor_exposures.get(factor, 0.0) * shock
        for factor, shock in scenario_factor_shocks.items()
    )
    return base_value * (1 + shock_effect)
