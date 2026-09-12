"""
Financial statement ratios. Pure functions over plain floats pulled from
normalized financial statement records (see data_ingestion/normalization.py).
Naming follows standard definitions exactly so a reviewer can audit against
any textbook without guessing conventions.
"""
from __future__ import annotations


def gross_margin(revenue: float, cogs: float) -> float:
    return (revenue - cogs) / revenue if revenue else float("nan")


def operating_margin(operating_income: float, revenue: float) -> float:
    return operating_income / revenue if revenue else float("nan")


def net_margin(net_income: float, revenue: float) -> float:
    return net_income / revenue if revenue else float("nan")


def current_ratio(current_assets: float, current_liabilities: float) -> float:
    return current_assets / current_liabilities if current_liabilities else float("nan")


def quick_ratio(current_assets: float, inventory: float, current_liabilities: float) -> float:
    return (current_assets - inventory) / current_liabilities if current_liabilities else float("nan")


def debt_to_equity(total_debt: float, total_equity: float) -> float:
    return total_debt / total_equity if total_equity else float("nan")


def interest_coverage(ebit: float, interest_expense: float) -> float:
    return ebit / interest_expense if interest_expense else float("nan")


def return_on_equity(net_income: float, avg_shareholders_equity: float) -> float:
    return net_income / avg_shareholders_equity if avg_shareholders_equity else float("nan")


def return_on_invested_capital(nopat: float, invested_capital: float) -> float:
    return nopat / invested_capital if invested_capital else float("nan")


def free_cash_flow(operating_cash_flow: float, capex: float) -> float:
    return operating_cash_flow - capex


def free_cash_flow_yield(fcf: float, market_cap: float) -> float:
    return fcf / market_cap if market_cap else float("nan")


def price_to_earnings(price: float, eps: float) -> float:
    return price / eps if eps else float("nan")


def ev_to_ebitda(enterprise_value: float, ebitda: float) -> float:
    return enterprise_value / ebitda if ebitda else float("nan")


def peg_ratio(pe: float, eps_growth_rate_pct: float) -> float:
    return pe / eps_growth_rate_pct if eps_growth_rate_pct else float("nan")


def altman_z_score(
    working_capital: float,
    total_assets: float,
    retained_earnings: float,
    ebit: float,
    market_value_equity: float,
    total_liabilities: float,
    revenue: float,
) -> float:
    """Altman Z-Score (public manufacturer form) — a bankruptcy-risk screen,
    not a valuation tool. Used by the risk manager / accounting-expert agents
    as one input signal among several, never presented as a standalone verdict."""
    if total_assets == 0 or total_liabilities == 0:
        return float("nan")
    x1 = working_capital / total_assets
    x2 = retained_earnings / total_assets
    x3 = ebit / total_assets
    x4 = market_value_equity / total_liabilities
    x5 = revenue / total_assets
    return 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5


def piotroski_f_score(signals: dict[str, bool]) -> int:
    """Piotroski F-Score: caller supplies the 9 boolean signals (computed
    from two periods of financial statements upstream); this function just
    sums them so the *definition* of each signal lives in one documented
    place (the financial-statement-analyst agent's tool call), not duplicated
    across callers."""
    required = {
        "positive_net_income", "positive_operating_cash_flow", "roa_improved",
        "cash_flow_exceeds_net_income", "leverage_decreased", "current_ratio_improved",
        "no_new_shares_issued", "gross_margin_improved", "asset_turnover_improved",
    }
    missing = required - signals.keys()
    if missing:
        raise ValueError(f"missing Piotroski signals: {missing}")
    return sum(1 for k in required if signals[k])
