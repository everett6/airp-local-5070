import pytest

from app.quant.portfolio import (
    check_concentration_limits,
    mean_variance_optimize,
    min_variance_weights,
    risk_budget_position_size,
)


def test_min_variance_weights_sum_to_one():
    tickers = ["A", "B"]
    cov = [[0.04, 0.01], [0.01, 0.09]]
    weights = min_variance_weights(tickers, cov)
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6)
    assert all(w >= 0 for w in weights.values())


def test_min_variance_favors_lower_variance_asset():
    tickers = ["LOW_VOL", "HIGH_VOL"]
    cov = [[0.01, 0.0], [0.0, 0.25]]
    weights = min_variance_weights(tickers, cov)
    assert weights["LOW_VOL"] > weights["HIGH_VOL"]


def test_mean_variance_optimize_basic():
    tickers = ["A", "B"]
    expected_returns = [0.10, 0.06]
    cov = [[0.04, 0.00], [0.00, 0.02]]
    result = mean_variance_optimize(tickers, expected_returns, cov, risk_aversion=3.0)
    assert sum(result.weights.values()) == pytest.approx(1.0, abs=1e-6)
    assert result.expected_volatility >= 0
    assert isinstance(result.sharpe, float)


def test_mean_variance_long_only_has_no_negative_weights():
    tickers = ["A", "B", "C"]
    expected_returns = [0.10, -0.05, 0.08]
    cov = [[0.04, 0.0, 0.0], [0.0, 0.09, 0.0], [0.0, 0.0, 0.03]]
    result = mean_variance_optimize(tickers, expected_returns, cov, long_only=True)
    assert all(w >= 0 for w in result.weights.values())
    assert sum(result.weights.values()) == pytest.approx(1.0, abs=1e-6)


def test_concentration_check_flags_position_limit_breach():
    check = check_concentration_limits(
        proposed_weight=0.10, ticker="ACME", sector="Tech",
        current_sector_weights={"Tech": 0.15}, max_single_position=0.08,
    )
    assert check.breaches_position_limit is True


def test_concentration_check_flags_sector_limit_breach():
    check = check_concentration_limits(
        proposed_weight=0.05, ticker="ACME", sector="Tech",
        current_sector_weights={"Tech": 0.28}, max_sector_weight=0.30,
    )
    assert check.breaches_sector_limit is True
    assert check.sector_weight_after == pytest.approx(0.33)


def test_concentration_check_passes_within_limits():
    check = check_concentration_limits(
        proposed_weight=0.03, ticker="ACME", sector="Tech",
        current_sector_weights={"Tech": 0.10},
    )
    assert not check.breaches_position_limit
    assert not check.breaches_sector_limit


def test_risk_budget_position_size_respects_cap():
    size = risk_budget_position_size(
        portfolio_risk_budget=0.02, position_volatility=0.10,
        position_liquidity_score=1.0, max_position_weight=0.08,
    )
    assert size == pytest.approx(0.08)  # would be 0.20 uncapped, clamped to 0.08


def test_risk_budget_position_size_penalizes_illiquidity():
    liquid = risk_budget_position_size(0.01, 0.20, position_liquidity_score=1.0)
    illiquid = risk_budget_position_size(0.01, 0.20, position_liquidity_score=0.3)
    assert illiquid < liquid


def test_risk_budget_position_size_rejects_nonpositive_volatility():
    with pytest.raises(ValueError):
        risk_budget_position_size(0.02, position_volatility=0.0, position_liquidity_score=1.0)
