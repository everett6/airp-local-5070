"""The consensus shadow: a master algorithm over the research agents (docs/PLAN_60_V2.md "Consensus shadow", spec
fixed 2026-10-01 before any live outcome existed). Pure functions, no I/O.

Each agent votes +1, 0 or -1 on a live release. `consensus_eq` is the plain mean. `consensus_rw` weights each agent
by its own record on earlier releases whose outcome was already known: p = (hits + 10) / (calls + 20), weight =
0.1 + max(0, ln(p / (1 - p))). With no record every weight is 0.1 and the two scores are equal.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

AGENTS = ("judge", "net_read", "ai_read", "bb_read", "guidance")
READ = {"bullish": 1, "neutral": 0, "bearish": -1}
GUIDE = {"raised": 1, "initiated": 1, "lowered": -1, "withdrawn": -1}
PRIOR_HITS, PRIOR_CALLS, FLOOR = 10, 20, 0.1


def sign(x: float) -> int:
    return (x > 0) - (x < 0)


def votes(logodds: float | None, guidance: str | None, reads: Mapping[str, str | None]) -> dict[str, int]:
    """One release's votes. `reads` holds the on-time labels by agent name; a missing one is 0."""
    out = {"judge": 0 if logodds is None or math.isnan(logodds) else sign(logodds),
           "guidance": GUIDE.get(str(guidance), 0)}
    for a in ("net_read", "ai_read", "bb_read"):
        out[a] = READ.get(str(reads.get(a)), 0)
    return {a: out[a] for a in AGENTS}


def records(past: Iterable[tuple[Mapping[str, int], float]]) -> dict[str, tuple[int, int]]:
    """(hits, calls) per agent over (votes, 5-day result) pairs; zero votes and zero results are not calls."""
    rec = dict.fromkeys(AGENTS, (0, 0))
    for v, fwd in past:
        s = sign(fwd)
        if s == 0:
            continue
        for a in AGENTS:
            if v.get(a, 0) != 0:
                h, n = rec[a]
                rec[a] = (h + (v[a] == s), n + 1)
    return rec


def weight(hits: int, calls: int) -> float:
    p = (hits + PRIOR_HITS) / (calls + PRIOR_CALLS)
    return FLOOR + max(0.0, math.log(p / (1 - p)))


def combine(v: Mapping[str, int], rec: Mapping[str, tuple[int, int]]) -> dict[str, Any]:
    """Both scores for one release, given every agent's record at that time."""
    w = {a: weight(*rec.get(a, (0, 0))) for a in AGENTS}
    eq = sum(v[a] for a in AGENTS) / len(AGENTS)
    rw = sum(w[a] * v[a] for a in AGENTS) / sum(w.values())
    return {"eq": round(eq, 6), "rw": round(rw, 6), "weights": {a: round(w[a], 6) for a in AGENTS}}
