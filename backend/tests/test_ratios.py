import pytest

from app.quant.ratios import (
    altman_z_score,
    current_ratio,
    debt_to_equity,
    ev_to_ebitda,
    free_cash_flow,
    free_cash_flow_yield,
    gross_margin,
    interest_coverage,
    net_margin,
    operating_margin,
    peg_ratio,
    piotroski_f_score,
    price_to_earnings,
    quick_ratio,
    return_on_equity,
    return_on_invested_capital,
)


def test_margins():
    assert gross_margin(100, 60) == pytest.approx(0.4)
    assert operating_margin(20, 100) == pytest.approx(0.2)
    assert net_margin(10, 100) == pytest.approx(0.1)


def test_margins_handle_zero_revenue():
    import math
    assert math.isnan(gross_margin(0, 0))
    assert math.isnan(operating_margin(5, 0))
    assert math.isnan(net_margin(5, 0))


def test_liquidity_ratios():
    assert current_ratio(200, 100) == pytest.approx(2.0)
    assert quick_ratio(200, 50, 100) == pytest.approx(1.5)


def test_leverage_and_coverage():
    assert debt_to_equity(50, 100) == pytest.approx(0.5)
    assert interest_coverage(40, 10) == pytest.approx(4.0)


def test_returns():
    assert return_on_equity(20, 200) == pytest.approx(0.1)
    assert return_on_invested_capital(15, 150) == pytest.approx(0.1)


def test_cash_flow_metrics():
    fcf = free_cash_flow(operating_cash_flow=120, capex=40)
    assert fcf == pytest.approx(80)
    assert free_cash_flow_yield(fcf, market_cap=1600) == pytest.approx(0.05)


def test_valuation_multiples():
    assert price_to_earnings(100, 5) == pytest.approx(20.0)
    assert ev_to_ebitda(1200, 100) == pytest.approx(12.0)
    assert peg_ratio(pe=20, eps_growth_rate_pct=10) == pytest.approx(2.0)


def test_altman_z_score_reasonable_range():
    z = altman_z_score(
        working_capital=50, total_assets=500, retained_earnings=100,
        ebit=60, market_value_equity=800, total_liabilities=300, revenue=700,
    )
    assert z > 0  # sane positive score for a solvent-looking company


def test_altman_z_score_handles_zero_denominators():
    import math
    assert math.isnan(altman_z_score(0, 0, 0, 0, 0, 100, 0))
    assert math.isnan(altman_z_score(0, 100, 0, 0, 0, 0, 0))


def test_piotroski_f_score_full_and_empty():
    all_true = {
        "positive_net_income": True, "positive_operating_cash_flow": True,
        "roa_improved": True, "cash_flow_exceeds_net_income": True,
        "leverage_decreased": True, "current_ratio_improved": True,
        "no_new_shares_issued": True, "gross_margin_improved": True,
        "asset_turnover_improved": True,
    }
    assert piotroski_f_score(all_true) == 9

    mixed = dict(all_true)
    mixed["roa_improved"] = False
    mixed["leverage_decreased"] = False
    assert piotroski_f_score(mixed) == 7


def test_piotroski_f_score_requires_all_signals():
    with pytest.raises(ValueError):
        piotroski_f_score({"positive_net_income": True})
