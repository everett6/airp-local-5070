import math

import pytest

from app.quant.options import (
    BSInputs,
    OptionType,
    binomial_tree_price,
    black_scholes,
    implied_volatility,
    monte_carlo_option_price,
)


def test_black_scholes_call_matches_known_value():
    # Classic textbook example: S=100,K=100,T=1,r=5%,sigma=20% -> call ~10.45
    inp = BSInputs(spot=100, strike=100, time_to_expiry_years=1, risk_free_rate=0.05,
                    volatility=0.2, option_type=OptionType.CALL)
    greeks = black_scholes(inp)
    assert greeks.price == pytest.approx(10.4506, abs=1e-3)
    assert 0 < greeks.delta < 1


def test_put_call_parity():
    common = {"spot": 105, "strike": 100, "time_to_expiry_years": 0.5, "risk_free_rate": 0.03, "volatility": 0.25}
    call = black_scholes(BSInputs(**common, option_type=OptionType.CALL)).price
    put = black_scholes(BSInputs(**common, option_type=OptionType.PUT)).price
    s, k, r, t = common["spot"], common["strike"], common["risk_free_rate"], common["time_to_expiry_years"]
    # C - P = S - K e^{-rt}
    assert call - put == pytest.approx(s - k * math.exp(-r * t), abs=1e-6)


def test_implied_volatility_recovers_input_vol():
    inp = BSInputs(spot=50, strike=52, time_to_expiry_years=0.25, risk_free_rate=0.02,
                    volatility=0.35, option_type=OptionType.CALL)
    price = black_scholes(inp).price
    iv = implied_volatility(price, inp)
    assert iv == pytest.approx(0.35, abs=1e-3)


def test_binomial_converges_to_black_scholes_for_european():
    inp = BSInputs(spot=100, strike=100, time_to_expiry_years=1, risk_free_rate=0.05,
                    volatility=0.2, option_type=OptionType.CALL)
    bs_price = black_scholes(inp).price
    binom_price = binomial_tree_price(inp, steps=500, american=False)
    assert binom_price == pytest.approx(bs_price, abs=0.05)


def test_american_put_at_least_as_valuable_as_european():
    inp = BSInputs(spot=90, strike=100, time_to_expiry_years=1, risk_free_rate=0.05,
                    volatility=0.3, dividend_yield=0.0, option_type=OptionType.PUT)
    euro = binomial_tree_price(inp, steps=300, american=False)
    amer = binomial_tree_price(inp, steps=300, american=True)
    assert amer >= euro - 1e-6


def test_monte_carlo_is_close_to_black_scholes_and_deterministic():
    inp = BSInputs(spot=100, strike=100, time_to_expiry_years=1, risk_free_rate=0.05,
                    volatility=0.2, option_type=OptionType.CALL)
    bs_price = black_scholes(inp).price
    mc1 = monte_carlo_option_price(inp, n_paths=50_000, seed=7)
    mc2 = monte_carlo_option_price(inp, n_paths=50_000, seed=7)
    assert mc1.mean_payoff_price == mc2.mean_payoff_price  # reproducibility
    assert mc1.mean_payoff_price == pytest.approx(bs_price, abs=0.3)


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        BSInputs(spot=-1, strike=100, time_to_expiry_years=1, risk_free_rate=0.05, volatility=0.2)
