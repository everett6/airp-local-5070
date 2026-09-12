import pytest

from app.quant.dcf import (
    ComparableMultiple,
    DCFInputs,
    capm_cost_of_equity,
    comparable_valuation,
    dcf_sensitivity_grid,
    discounted_cash_flow,
    weighted_average_cost_of_capital,
)


def make_inputs(**overrides):
    defaults = {
        "base_free_cash_flow": 100.0,
        "projection_years": 5,
        "growth_rates": [0.10, 0.08, 0.06, 0.05, 0.04],
        "terminal_growth_rate": 0.025,
        "discount_rate": 0.09,
        "net_debt": 200.0,
        "shares_outstanding": 50.0,
    }
    defaults.update(overrides)
    return DCFInputs(**defaults)


def test_dcf_basic_sanity():
    result = discounted_cash_flow(make_inputs())
    assert result.enterprise_value > sum(result.discounted_fcfs)
    assert result.equity_value == pytest.approx(result.enterprise_value - 200.0)
    assert result.fair_value_per_share == pytest.approx(result.equity_value / 50.0)


def test_dcf_requires_discount_rate_above_terminal_growth():
    with pytest.raises(ValueError):
        make_inputs(discount_rate=0.02, terminal_growth_rate=0.03)


def test_dcf_growth_rates_length_validated():
    with pytest.raises(ValueError):
        DCFInputs(
            base_free_cash_flow=100, projection_years=5, growth_rates=[0.1, 0.1],
            terminal_growth_rate=0.02, discount_rate=0.09, net_debt=0, shares_outstanding=10,
        )


def test_higher_discount_rate_lowers_fair_value():
    low_dr = discounted_cash_flow(make_inputs(discount_rate=0.08)).fair_value_per_share
    high_dr = discounted_cash_flow(make_inputs(discount_rate=0.12)).fair_value_per_share
    assert high_dr < low_dr


def test_capm_and_wacc():
    coe = capm_cost_of_equity(risk_free_rate=0.04, beta=1.2, equity_risk_premium=0.05)
    assert coe == pytest.approx(0.10)
    wacc = weighted_average_cost_of_capital(
        cost_of_equity=coe, cost_of_debt_pretax=0.05, tax_rate=0.21,
        market_value_equity=800, market_value_debt=200,
    )
    assert 0.0 < wacc < coe  # blending in cheaper after-tax debt should lower WACC below pure CoE


def test_comparable_valuation_median_and_mean():
    peers = [
        ComparableMultiple("A", "EV/EBITDA", 10.0),
        ComparableMultiple("B", "EV/EBITDA", 12.0),
        ComparableMultiple("C", "EV/EBITDA", 8.0),
        ComparableMultiple("D", "EV/EBITDA", 30.0),  # outlier
    ]
    result = comparable_valuation(target_metric_value=100.0, peer_multiples=peers)
    assert result["median_multiple"] == pytest.approx(11.0)
    assert result["implied_value_median"] == pytest.approx(1100.0)


def test_sensitivity_grid_shape_and_monotonicity():
    grid = dcf_sensitivity_grid(
        make_inputs(),
        discount_rate_range=(0.07, 0.11, 3),
        terminal_growth_range=(0.01, 0.03, 3),
    )
    assert len(grid) == 3 and all(len(row) == 3 for row in grid)
    # Lower discount rate (row 0) should give higher fair value than higher discount rate (row 2)
    # for the same terminal growth column.
    assert grid[0][1] > grid[2][1]
