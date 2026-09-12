"""
Discounted cash flow, growth, and comparable-valuation primitives.

Everything here is a pure function: given the same inputs, always the same
output, no I/O, no randomness (except Monte Carlo sensitivity, which takes an
explicit seed). Agents call these functions as *tools*; they never compute a
valuation number themselves. Every function's fully-qualified name is what
gets recorded as `Claim.numeric_source`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DCFInputs:
    base_free_cash_flow: float
    projection_years: int
    growth_rates: list[float]           # length == projection_years, yr-over-yr FCF growth
    terminal_growth_rate: float
    discount_rate: float                # WACC, as a decimal (0.09 == 9%)
    net_debt: float
    shares_outstanding: float

    def __post_init__(self) -> None:
        if len(self.growth_rates) != self.projection_years:
            raise ValueError("growth_rates length must equal projection_years")
        if self.discount_rate <= self.terminal_growth_rate:
            raise ValueError("discount_rate must exceed terminal_growth_rate (Gordon growth)")
        if self.shares_outstanding <= 0:
            raise ValueError("shares_outstanding must be positive")


@dataclass(frozen=True)
class DCFResult:
    projected_fcfs: list[float]
    discounted_fcfs: list[float]
    terminal_value: float
    discounted_terminal_value: float
    enterprise_value: float
    equity_value: float
    fair_value_per_share: float


def discounted_cash_flow(inputs: DCFInputs) -> DCFResult:
    fcfs: list[float] = []
    fcf = inputs.base_free_cash_flow
    for g in inputs.growth_rates:
        fcf = fcf * (1.0 + g)
        fcfs.append(fcf)

    discounted = [
        cf / ((1.0 + inputs.discount_rate) ** (i + 1)) for i, cf in enumerate(fcfs)
    ]

    terminal_value = (
        fcfs[-1] * (1.0 + inputs.terminal_growth_rate)
        / (inputs.discount_rate - inputs.terminal_growth_rate)
    )
    discounted_terminal = terminal_value / ((1.0 + inputs.discount_rate) ** inputs.projection_years)

    enterprise_value = sum(discounted) + discounted_terminal
    equity_value = enterprise_value - inputs.net_debt
    fair_value_per_share = equity_value / inputs.shares_outstanding

    return DCFResult(
        projected_fcfs=fcfs,
        discounted_fcfs=discounted,
        terminal_value=terminal_value,
        discounted_terminal_value=discounted_terminal,
        enterprise_value=enterprise_value,
        equity_value=equity_value,
        fair_value_per_share=fair_value_per_share,
    )


def dcf_sensitivity_grid(
    inputs: DCFInputs,
    discount_rate_range: tuple[float, float, int],
    terminal_growth_range: tuple[float, float, int],
) -> list[list[float]]:
    """2D sensitivity grid of fair value per share over (WACC, terminal growth).
    Used to render the report's sensitivity table — never a single point estimate
    presented as certainty."""
    dr_lo, dr_hi, dr_n = discount_rate_range
    tg_lo, tg_hi, tg_n = terminal_growth_range
    dr_step = (dr_hi - dr_lo) / max(dr_n - 1, 1)
    tg_step = (tg_hi - tg_lo) / max(tg_n - 1, 1)

    grid: list[list[float]] = []
    for i in range(dr_n):
        row: list[float] = []
        dr = dr_lo + i * dr_step
        for j in range(tg_n):
            tg = tg_lo + j * tg_step
            if dr <= tg:
                row.append(float("nan"))
                continue
            local = DCFInputs(
                base_free_cash_flow=inputs.base_free_cash_flow,
                projection_years=inputs.projection_years,
                growth_rates=inputs.growth_rates,
                terminal_growth_rate=tg,
                discount_rate=dr,
                net_debt=inputs.net_debt,
                shares_outstanding=inputs.shares_outstanding,
            )
            row.append(discounted_cash_flow(local).fair_value_per_share)
        grid.append(row)
    return grid


def weighted_average_cost_of_capital(
    cost_of_equity: float,
    cost_of_debt_pretax: float,
    tax_rate: float,
    market_value_equity: float,
    market_value_debt: float,
) -> float:
    total = market_value_equity + market_value_debt
    if total <= 0:
        raise ValueError("total capital must be positive")
    e_weight = market_value_equity / total
    d_weight = market_value_debt / total
    return e_weight * cost_of_equity + d_weight * cost_of_debt_pretax * (1 - tax_rate)


def capm_cost_of_equity(risk_free_rate: float, beta: float, equity_risk_premium: float) -> float:
    return risk_free_rate + beta * equity_risk_premium


@dataclass(frozen=True)
class ComparableMultiple:
    ticker: str
    metric_name: str      # e.g. "EV/EBITDA", "P/E", "EV/Revenue"
    multiple: float


def comparable_valuation(
    target_metric_value: float,
    peer_multiples: list[ComparableMultiple],
    trim_outliers_pct: float = 0.0,
) -> dict[str, float]:
    """Applies the (optionally trimmed) mean/median peer multiple to the target's
    own metric. Returns both median- and mean-implied values so the report can
    show a range rather than a false-precision single number."""
    if not peer_multiples:
        raise ValueError("peer_multiples cannot be empty")

    values = sorted(m.multiple for m in peer_multiples)
    n = len(values)
    if trim_outliers_pct > 0:
        k = int(n * trim_outliers_pct)
        values = values[k: n - k] or values

    mean_multiple = sum(values) / len(values)
    mid = len(values) // 2
    median_multiple = (
        values[mid] if len(values) % 2 == 1 else (values[mid - 1] + values[mid]) / 2
    )

    return {
        "mean_multiple": mean_multiple,
        "median_multiple": median_multiple,
        "implied_value_mean": mean_multiple * target_metric_value,
        "implied_value_median": median_multiple * target_metric_value,
        "n_peers": float(len(peer_multiples)),
    }


def revenue_growth_model(
    base_revenue: float, growth_rates: list[float]
) -> list[float]:
    out = []
    rev = base_revenue
    for g in growth_rates:
        rev *= 1.0 + g
        out.append(rev)
    return out
