"""Evidence-linked virtual horizon budgets and transparent case simulation, never orders."""
from __future__ import annotations

import math
from typing import Any

HORIZONS = {'day': 1, 'short': 5, 'medium': 21, 'long': 63}
PROFILES = {'balanced': {'day': .10, 'short': .20, 'medium': .30, 'long': .30},
            'defensive': {'day': .05, 'short': .10, 'medium': .15, 'long': .20},
            'long_focus': {'day': .05, 'short': .10, 'medium': .25, 'long': .50}}
# Explicit illustrative shocks over each horizon, not empirical estimates or model probabilities.
SHOCKS = {'selloff': [-.03, -.08, -.15, -.25], 'flat': [0., 0., 0., 0.],
          'upside': [.01, .03, .08, .15], 'liquidity_stress': [-.05, -.12, -.22, -.35]}


def plan(rows: list[dict[str, Any]], capital: float = 10_000., profile: str = 'balanced', cost_bps: float = 20.) -> dict[str, Any]:
    if profile not in PROFILES or not math.isfinite(capital) or capital <= 0 or not math.isfinite(cost_bps) or not 0 <= cost_bps <= 1000:
        raise ValueError('Invalid scenario inputs')
    if len({r['ticker'] for r in rows}) != len(rows):
        raise ValueError('Duplicate scenario company')
    weights: dict[str, dict[str, float]] = {}
    for horizon, cap in PROFILES[profile].items():
        scores: dict[str, float] = {}
        for row in rows:
            rating = (row.get('ratings') or {}).get(horizon, 3)
            if row.get('status') == 'decided' and type(rating) is int and 4 <= rating <= 5:
                scores[row['ticker']] = float(rating - 3)
        total = sum(scores.values())
        weights[horizon] = {t: min(.10, cap * score / total) for t, score in scores.items()}
    for row in rows:
        ticker = row['ticker']
        total = sum(w.get(ticker, 0) for w in weights.values())
        if total > .10:
            for w in weights.values():
                if ticker in w:
                    w[ticker] *= .10 / total
    allocation = {h: sum(w.values()) for h, w in weights.items()}
    cases = []
    for name, moves in SHOCKS.items():
        side_cost = cost_bps * (3 if name == 'liquidity_stress' else 1) / 10_000
        returns = {h: allocation[h] * (move - 2 * side_cost) for h, move in zip(HORIZONS, moves, strict=True)}
        total = sum(returns.values())
        cases.append({'name': name, 'moves': dict(zip(HORIZONS, moves, strict=True)), 'contribution': returns,
                      'net_return': total, 'pnl': capital * total, 'equity_after': capital * (1 + total),
                      'cost_bps_per_side': side_cost * 10_000})
    return {'profile': profile, 'capital': capital, 'weights': weights, 'allocation': allocation,
            'cash': max(0., 1 - sum(allocation.values())), 'horizon_sessions': HORIZONS,
            'cases': cases, 'company_cap': .10, 'caps': PROFILES[profile], 'mode': 'simulation_only',
            'estimated_gain': None, 'probabilities': None,
            'explanation': 'One hypothetical round trip per horizon, each at its own duration; combined contributions are not a common-period return. Cash earns zero. Long-only virtual budgets; existing hedged filing execution is separate. Shocks and costs are assumptions, not forecasts.'}
