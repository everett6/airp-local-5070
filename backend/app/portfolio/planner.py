"""Goal planner: break a dollar goal by a date into what each strategy track has to earn, and the odds on the evidence.

The user's rule (28 Sep 2026): show the gap; a track's money grows only after it passes its test, always within the
35% max drawdown. So the planner never raises a weight to chase the goal. It reports:
  - the yearly return the goal needs;
  - the odds of reaching it with today's evidence-based weights (block bootstrap of each track's real daily returns);
  - what the stock-picking tracks would have to earn, at the most weight the evidence ladder could ever give them,
    for the goal to be a coin flip;
  - each track's status and what unlocks its next step.
Evidence ladder (fixed 2026-09-28, before any track moved up): not built / failed / shadow (first month, machinery check) 0% -> untested, paper only 10% ->
passed its pre-registered test AND 3 months of forward paper results 25% -> 12 months forward, still passing 40%.
All picking tracks together at most 60%; the core book always keeps at least 40%.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

LADDER = {"not_built": 0.0, "failed": 0.0, "shadow": 0.0, "untested": 0.10, "passed": 0.25, "proven": 0.40}
PICKING_CAP = 0.60
MAX_DD = 0.35
# FINRA retired the pattern-day-trader rule on 4 Jun 2026 (Rule 4210 amendments; Alpaca adopted them that day): no
# $25k minimum or trade count; intraday margin is checked in real time, and a margin account needs $2,000.
MARGIN_MIN = 2_000.0


@dataclass
class Track:
    name: str
    label: str
    status: str            # not_built | untested | passed | proven | core
    unlock: str            # what moves it to the next rung
    note: str = ""


TRACKS = [
    Track("core", "Core: SPY + crypto trend, with brakes", "core", "always on; takes whatever the picks don't",
          "backtest 2018-26: 17.7%/yr, max drawdown 27%"),
    Track("event", "Short-term event picks (5-day, Jan + Bonsai)", "untested",
          "a research arm passes its test, then 3 months of forward paper results",
          "B2 and B3 failed (research adds ~0 over the release); sleeve replay 2024-26: -2.2%/yr"),
    Track("long_term", "Long-term stock picks (3 months, Bonsai)", "shadow",
          "first cohort 1 Oct 2026; one clean month -> 10% paper; judged after 12 cohorts",
          "forward-only: Bonsai knows 2024-26, so no backtest"),
    Track("themes", "Themes by horizon (6-12 months: hyperscalers, biotech, quantum...) + AI-bubble gauge", "shadow",
          "first cohort 1 Oct 2026; one clean month -> 10% paper; judged after 12 six-month cohorts",
          "forward-only; judged against SPY and a code-only momentum pick of the same themes"),
    Track("day_trading", "Day trading (intraday, SPY/QQQ)", "failed",
          "a new rule, pre-registered and tested after its publication date",
          "5 published rules failed after publication: D1 -1.00, D2 -0.24, D3 +0.20, D4 -1.21, D5 -1.86 (Sharpe)"),
    Track("private", "Private companies", "not_built",
          "later stage: needs accredited-investor access and a data source; not available yet"),
]


@dataclass
class Goal:
    start: float
    target: float
    by: date
    today: date

    @property
    def years(self) -> float:
        return max(1 / 252, (self.by - self.today).days / 365.25)

    @property
    def required_cagr(self) -> float:
        return float((self.target / self.start) ** (1 / self.years) - 1)


def weights(tracks: list[Track]) -> dict[str, float]:
    w = {t.name: LADDER.get(t.status, 0.0) for t in tracks if t.status != "core"}
    total = sum(w.values())
    if total > PICKING_CAP:
        w = {k: v * PICKING_CAP / total for k, v in w.items()}
    w["core"] = 1 - sum(w.values())
    return w


def simulate(returns: pd.DataFrame, w: dict[str, float], days: int, n: int = 4000, block: int = 21,
             seed: int = 0) -> np.ndarray:
    """Paths of daily book returns: each track resampled in 21-day blocks from its own history (tracks with a
    weight but no history earn 0). Rebalanced daily to the weights. Shape (n, days)."""
    rng = np.random.default_rng(seed)
    out = np.zeros((n, days))
    nb = -(-days // block)
    for name, wt in w.items():
        if wt == 0 or name not in returns or returns[name].notna().sum() < 2 * block:
            continue
        x = returns[name].dropna().to_numpy()
        starts = rng.integers(0, len(x) - block, (n, nb))
        idx = (starts[:, :, None] + np.arange(block)).reshape(n, -1)[:, :days]
        out += wt * x[idx]
    return out


def outcome(goal: Goal, paths: np.ndarray) -> dict[str, Any]:
    eq = goal.start * np.cumprod(1 + paths, axis=1)
    end = eq[:, -1]
    peak = np.maximum.accumulate(np.concatenate([np.full((len(eq), 1), goal.start), eq], axis=1), axis=1)[:, 1:]
    mdd = (1 - eq / peak).max(axis=1)
    return {"p_goal": round(float((end >= goal.target).mean()), 3),
            "median_end": round(float(np.median(end)), 2), "p5_end": round(float(np.percentile(end, 5)), 2),
            "p95_end": round(float(np.percentile(end, 95)), 2),
            "median_cagr": round(float((np.median(end) / goal.start) ** (1 / goal.years) - 1), 4),
            "median_max_dd": round(float(np.median(mdd)), 3),
            "p_dd_over_limit": round(float((mdd > MAX_DD).mean()), 3)}


def plan(goal: Goal, returns: pd.DataFrame, tracks: list[Track] | None = None) -> dict[str, Any]:
    tracks = tracks or TRACKS
    days = max(1, round(goal.years * 252))
    w = weights(tracks)
    now = outcome(goal, simulate(returns, w, days))
    core_only = outcome(goal, simulate(returns, {"core": 1.0}, days))
    core_cagr = core_only["median_cagr"]
    need = {}
    for share in (LADDER["passed"], LADDER["proven"], PICKING_CAP):
        need[f"{int(share * 100)}%"] = round((goal.required_cagr - (1 - share) * core_cagr) / share, 4)
    flags = []
    if goal.start < MARGIN_MIN:
        flags.append(f"Day trading needs a margin account, which needs at least ${MARGIN_MIN:,.0f} (FINRA's intraday "
                     "margin rule, which replaced the pattern-day-trader rule on 4 June 2026).")
    if goal.required_cagr > 1.0:
        flags.append("The goal needs more than doubling every year: no track here has evidence anywhere near that.")
    if now["p_dd_over_limit"] > 0.10:
        flags.append(f"{100 * now['p_dd_over_limit']:.0f}% of paths break the {100 * MAX_DD:.0f}% max drawdown.")
    return {"goal": {"start": goal.start, "target": goal.target, "by": goal.by.isoformat(),
                     "years": round(goal.years, 2), "required_cagr": round(goal.required_cagr, 4)},
            "weights": {k: round(v, 3) for k, v in w.items()},
            "with_current_evidence": now, "core_only": core_only,
            "gap_cagr": round(goal.required_cagr - now["median_cagr"], 4),
            "picking_needs_cagr": need,
            "tracks": [asdict(t) | {"weight": round(w.get(t.name, w["core"] if t.status == "core" else 0.0), 3),
                                    "history_cagr": _cagr(returns, t.name)} for t in tracks],
            "flags": flags}


def _cagr(returns: pd.DataFrame, name: str) -> float | None:
    if name not in returns or returns[name].notna().sum() < 60:
        return None
    x = returns[name].dropna().to_numpy(float)
    return round(float(np.prod(1 + x) ** (252 / len(x)) - 1), 4)
