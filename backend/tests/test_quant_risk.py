import pytest

from app.quant.risk import (
    conditional_var,
    correlation_matrix,
    drawdown_analysis,
    expected_value,
    factor_regression,
    historical_var,
    kelly_fraction,
    sharpe_ratio,
    sortino_ratio,
)


def test_historical_var_basic():
    returns = [-0.05, -0.03, -0.01, 0.0, 0.01, 0.02, 0.03, 0.04, 0.05, -0.10]
    var95 = historical_var(returns, confidence=0.90)
    assert var95 > 0  # expressed as a positive loss


def test_cvar_at_least_var():
    returns = [-0.20, -0.10, -0.05, 0.0, 0.01, 0.02, 0.03, 0.10, 0.15, 0.20]
    var = historical_var(returns, confidence=0.90)
    cvar = conditional_var(returns, confidence=0.90)
    assert cvar >= var - 1e-9


def test_sharpe_zero_when_no_variance():
    assert sharpe_ratio([0.01] * 10) == 0.0


def test_sharpe_positive_for_positive_mean_returns():
    returns = [0.01, 0.02, -0.005, 0.015, 0.01, 0.008]
    assert sharpe_ratio(returns) > 0


def test_sortino_ignores_upside_volatility():
    low_vol_upside = [0.01, 0.011, 0.009, 0.01, 0.0105]
    # Sortino penalizes only downside deviation; a series with zero downside
    # should have sortino == 0.0 per our convention (undefined downside dev).
    assert sortino_ratio(low_vol_upside) == 0.0


def test_kelly_fraction_bounds():
    f = kelly_fraction(win_probability=0.6, win_loss_ratio=1.5, kelly_multiplier=1.0)
    assert 0.0 <= f <= 1.0
    # With a losing edge, Kelly should clamp to 0, never go negative.
    f_losing = kelly_fraction(win_probability=0.3, win_loss_ratio=1.0)
    assert f_losing == 0.0


def test_kelly_invalid_inputs():
    with pytest.raises(ValueError):
        kelly_fraction(win_probability=1.5, win_loss_ratio=1.0)
    with pytest.raises(ValueError):
        kelly_fraction(win_probability=0.5, win_loss_ratio=-1.0)


def test_expected_value_requires_probabilities_sum_to_one():
    with pytest.raises(ValueError):
        expected_value([(0.5, 10), (0.6, -5)])
    ev = expected_value([(0.5, 10), (0.5, -4)])
    assert ev == pytest.approx(3.0)


def test_drawdown_analysis_identifies_trough():
    curve = [100, 110, 105, 90, 95, 120, 115]
    result = drawdown_analysis(curve)
    assert result.max_drawdown == pytest.approx((110 - 90) / 110)
    assert result.max_drawdown_trough_idx == 3


def test_correlation_matrix_diagonal_is_one():
    _names, matrix = correlation_matrix({
        "AAA": [0.01, 0.02, -0.01, 0.03, 0.0],
        "BBB": [0.02, 0.01, -0.02, 0.01, 0.01],
    })
    assert matrix[0][0] == pytest.approx(1.0)
    assert matrix[1][1] == pytest.approx(1.0)


def test_factor_regression_recovers_known_beta():
    # asset = 2 * market_factor + noise-free
    market = [0.01, -0.02, 0.03, 0.0, 0.015, -0.01]
    asset = [2 * m for m in market]
    exposures = factor_regression(asset, {"market": market})
    assert exposures[0].beta == pytest.approx(2.0, abs=1e-6)
    assert exposures[0].r_squared == pytest.approx(1.0, abs=1e-6)
