"""Fact sheet v2: a year-earlier figure the reader took from a release, reconciled with the SEC-filed quarters
(docs/PLAN_60_V2.md, "Fact sheet v2"). Pure functions, no I/O.

The reader quotes every number, and code checks the quote and the scale; neither knows which period a number
belongs to. Seen live on Micron (30 Sep 2026): the "year-earlier" revenue was the quarter before.
"""
from __future__ import annotations

import math
from typing import Any

NAMES = {"revenue": "revenue", "eps": "diluted EPS"}


def agrees(key: str, a: float, b: float) -> bool:
    """The reader's year-earlier figure `a` against the SEC-filed one `b`: revenue within 10%; EPS within 0.02 or
    10%, whichever is larger."""
    if key == "revenue":
        return b != 0 and abs(a / b - 1) <= 0.10
    return abs(a - b) <= max(0.02, 0.10 * abs(b))


def same_figure(key: str, a: float, b: float) -> bool:
    """`a` is the filed figure `b` itself: revenue within 0.5%, EPS within half a cent."""
    if key == "revenue":
        return b != 0 and abs(a / b - 1) <= 0.005
    return abs(a - b) <= 0.005


def show(key: str, v: float) -> str:
    return f"{v:,.0f}M" if key == "revenue" else f"{v:.2f}"


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def reconcile(key: str, pair: dict[str, Any], year_ago: dict[str, Any] | None, last: dict[str, Any] | None
              ) -> tuple[dict[str, Any], str, str | None]:
    """(the pair to show, status, the fact sheet's line or None). `pair`: the reader's {"q", "prior"}; `year_ago`
    and `last`: the SEC tool's year-earlier quarter and latest filed quarter ({"end", "eps", "rev"}).
    Status: unchecked (nothing to compare), agrees, previous_quarter (replaced), differs (stated, not replaced)."""
    col = "rev" if key == "revenue" else "eps"
    read, sec = _num(pair.get("prior")), _num((year_ago or {}).get(col))
    if "q" not in pair or read is None or sec is None:
        return pair, "unchecked", None
    if agrees(key, read, sec):
        return pair, "agrees", None
    before = _num((last or {}).get(col))
    end = (year_ago or {}).get("end")
    if before is not None and same_figure(key, read, before):
        line = (f"year-earlier {NAMES[key]} corrected: the release's comparison figure ({show(key, read)}) is the "
                f"previous quarter's (ended {(last or {}).get('end')}); the year-earlier quarter (ended {end}) "
                f"was {show(key, sec)} as filed with the SEC")
        return {**pair, "prior": sec}, "previous_quarter", line
    line = (f"year-earlier {NAMES[key]} as read ({show(key, read)}) differs from the SEC-filed figure for the quarter "
            f"ended {end} ({show(key, sec)}); the release may use another definition")
    return pair, "differs", line
