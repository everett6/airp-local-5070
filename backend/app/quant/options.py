"""
Options pricing and Greeks. Pure, deterministic (Monte Carlo takes an explicit
seed), no LLM anywhere near a floating point number.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np
from scipy.stats import norm


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"


@dataclass(frozen=True)
class BSInputs:
    spot: float
    strike: float
    time_to_expiry_years: float
    risk_free_rate: float
    volatility: float
    dividend_yield: float = 0.0
    option_type: OptionType = OptionType.CALL

    def __post_init__(self) -> None:
        if self.spot <= 0 or self.strike <= 0:
            raise ValueError("spot and strike must be positive")
        if self.time_to_expiry_years <= 0:
            raise ValueError("time_to_expiry_years must be positive")
        if self.volatility <= 0:
            raise ValueError("volatility must be positive")


@dataclass(frozen=True)
class Greeks:
    price: float
    delta: float
    gamma: float
    vega: float      # per 1.00 (100 vol points) change in IV — divide by 100 for per-1% convention
    theta: float      # per year — divide by 365 for per-day
    rho: float


def _d1_d2(inp: BSInputs) -> tuple[float, float]:
    s, k, t, r, sigma, q = (
        inp.spot, inp.strike, inp.time_to_expiry_years, inp.risk_free_rate,
        inp.volatility, inp.dividend_yield,
    )
    d1 = (math.log(s / k) + (r - q + 0.5 * sigma ** 2) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    return d1, d2


def black_scholes(inp: BSInputs) -> Greeks:
    s, k, t, r, sigma, q = (
        inp.spot, inp.strike, inp.time_to_expiry_years, inp.risk_free_rate,
        inp.volatility, inp.dividend_yield,
    )
    d1, d2 = _d1_d2(inp)
    disc_q = math.exp(-q * t)
    disc_r = math.exp(-r * t)

    if inp.option_type == OptionType.CALL:
        price = s * disc_q * norm.cdf(d1) - k * disc_r * norm.cdf(d2)
        delta = disc_q * norm.cdf(d1)
        rho = k * t * disc_r * norm.cdf(d2) / 100.0
        theta = (
            -(s * disc_q * norm.pdf(d1) * sigma) / (2 * math.sqrt(t))
            - r * k * disc_r * norm.cdf(d2)
            + q * s * disc_q * norm.cdf(d1)
        )
    else:
        price = k * disc_r * norm.cdf(-d2) - s * disc_q * norm.cdf(-d1)
        delta = -disc_q * norm.cdf(-d1)
        rho = -k * t * disc_r * norm.cdf(-d2) / 100.0
        theta = (
            -(s * disc_q * norm.pdf(d1) * sigma) / (2 * math.sqrt(t))
            + r * k * disc_r * norm.cdf(-d2)
            - q * s * disc_q * norm.cdf(-d1)
        )

    gamma = disc_q * norm.pdf(d1) / (s * sigma * math.sqrt(t))
    vega = s * disc_q * norm.pdf(d1) * math.sqrt(t) / 100.0

    return Greeks(price=price, delta=delta, gamma=gamma, vega=vega, theta=theta, rho=rho)


def implied_volatility(
    market_price: float,
    inp: BSInputs,
    tolerance: float = 1e-6,
    max_iterations: int = 100,
) -> float:
    """Bisection search — slower than Newton-Raphson but never diverges,
    which matters more than speed for a research tool that must never crash
    silently on illiquid/weird quotes."""
    lo, hi = 1e-6, 5.0
    for _ in range(max_iterations):
        mid = (lo + hi) / 2
        trial = BSInputs(
            spot=inp.spot, strike=inp.strike, time_to_expiry_years=inp.time_to_expiry_years,
            risk_free_rate=inp.risk_free_rate, volatility=mid,
            dividend_yield=inp.dividend_yield, option_type=inp.option_type,
        )
        price = black_scholes(trial).price
        if abs(price - market_price) < tolerance:
            return mid
        if price > market_price:
            hi = mid
        else:
            lo = mid
    return mid


def binomial_tree_price(
    inp: BSInputs, steps: int = 200, american: bool = False
) -> float:
    """Cox-Ross-Rubinstein binomial tree. Needed for American-style equity
    options where early exercise can matter (dividends), unlike Black-Scholes
    which assumes European exercise."""
    s, k, t, r, sigma, q = (
        inp.spot, inp.strike, inp.time_to_expiry_years, inp.risk_free_rate,
        inp.volatility, inp.dividend_yield,
    )
    dt = t / steps
    u = math.exp(sigma * math.sqrt(dt))
    d = 1 / u
    p = (math.exp((r - q) * dt) - d) / (u - d)
    disc = math.exp(-r * dt)

    prices = np.array([s * (u ** j) * (d ** (steps - j)) for j in range(steps + 1)])
    if inp.option_type == OptionType.CALL:
        values = np.maximum(prices - k, 0.0)
    else:
        values = np.maximum(k - prices, 0.0)

    for i in range(steps - 1, -1, -1):
        values = disc * (p * values[1: i + 2] + (1 - p) * values[0: i + 1])
        if american:
            prices = np.array([s * (u ** j) * (d ** (i - j)) for j in range(i + 1)])
            intrinsic = (
                np.maximum(prices - k, 0.0)
                if inp.option_type == OptionType.CALL
                else np.maximum(k - prices, 0.0)
            )
            values = np.maximum(values, intrinsic)

    return float(values[0])


@dataclass(frozen=True)
class MonteCarloResult:
    mean_payoff_price: float
    std_error: float
    percentile_5: float
    percentile_95: float
    paths_used: int
    seed: int


def monte_carlo_option_price(
    inp: BSInputs,
    n_paths: int = 100_000,
    seed: int = 42,
    n_steps: int = 1,
) -> MonteCarloResult:
    """Geometric Brownian motion simulation. Deterministic given `seed` — a
    report generated twice from the same inputs must show the same number,
    which is why the seed is a required, logged parameter, never left implicit."""
    rng = np.random.default_rng(seed)
    s, k, t, r, sigma, q = (
        inp.spot, inp.strike, inp.time_to_expiry_years, inp.risk_free_rate,
        inp.volatility, inp.dividend_yield,
    )
    dt = t / n_steps
    drift = (r - q - 0.5 * sigma ** 2) * dt
    diffusion = sigma * math.sqrt(dt)

    log_s = np.full(n_paths, math.log(s))
    for _ in range(n_steps):
        z = rng.standard_normal(n_paths)
        log_s = log_s + drift + diffusion * z
    terminal = np.exp(log_s)

    payoff = (
        np.maximum(terminal - k, 0.0)
        if inp.option_type == OptionType.CALL
        else np.maximum(k - terminal, 0.0)
    )
    discounted_payoff = math.exp(-r * t) * payoff

    mean = float(np.mean(discounted_payoff))
    std_err = float(np.std(discounted_payoff, ddof=1) / math.sqrt(n_paths))
    p5, p95 = np.percentile(discounted_payoff, [5, 95])

    return MonteCarloResult(
        mean_payoff_price=mean,
        std_error=std_err,
        percentile_5=float(p5),
        percentile_95=float(p95),
        paths_used=n_paths,
        seed=seed,
    )
