"""Trading-health numbers for the weekly review (report only; nothing here trades or changes a book).

    python scripts/trading_health.py      # print both summaries as JSON

- stage4(): the number the leverage ladder is gated on (docs/PLAN_60_V2.md "Stage 4"): the live book's forward
  Sharpe (excess over T-bills, weekly allocator equity, annualized with sqrt 52) and its lower 80% bound
  (block bootstrap of weekly excess returns, 4-week blocks, 5,000 draws, seed 0). Stage 4 only applies from month 6,
  so before that the row is shown as information, never acted on.
- execution(): how real paper orders compare with the simulator: broker legs by status, fill gaps, and the AI-picks
  pair audit. This is the evidence for execution quality before any talk of real money.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
FWD = BACKEND / "results" / "forward"
STAGE4 = ((1.25, "42% vol (~60%/yr), needs 12+ months ≥ 1.25"), (0.9, "35% vol"), (0.6, "25–30% vol"),
          (-np.inf, "20% vol (1.0×)"))


def stage4_row(lower: float) -> str:
    return next(label for bound, label in STAGE4 if lower >= bound)


def stage4(equity: pd.Series, rf_annual: pd.Series, start: date | None = None) -> dict[str, Any]:
    """equity: the book's equity by data date (one point per allocator run). rf_annual: T-bill rate (decimal)."""
    eq = equity.sort_index().astype(float)
    eq.index = pd.DatetimeIndex(pd.to_datetime(eq.index))
    out: dict[str, Any] = {"points": len(eq)}
    if len(eq) < 2:
        return out | {"note": "fewer than 2 allocator runs"}
    ret = eq.pct_change().dropna()
    days = pd.Series(eq.index, index=eq.index).diff().dt.days.dropna()
    rf = rf_annual.sort_index()
    rf.index = pd.DatetimeIndex(pd.to_datetime(rf.index))
    rf_per = rf.reindex(ret.index, method="ffill").fillna(0.0) * days / 365
    x = (ret - rf_per).to_numpy(float)
    first = start or eq.index[0].date()
    months = (eq.index[-1].year - first.year) * 12 + eq.index[-1].month - first.month
    dd = float((1 - eq / eq.cummax()).max())
    out |= {"weeks": len(x), "months": months, "return_pct": round(100 * (eq.iloc[-1] / eq.iloc[0] - 1), 2),
            "max_drawdown_pct": round(100 * dd, 2)}
    if len(x) >= 2 and x.std(ddof=1) > 0:
        out["sharpe"] = round(float(x.mean() / x.std(ddof=1) * np.sqrt(52)), 2)
        out["vol_pct"] = round(float(ret.std(ddof=1) * np.sqrt(52) * 100), 1)
    if len(x) >= 8:
        rng = np.random.default_rng(0)
        block = 4
        nb = -(-len(x) // block)
        starts = rng.integers(0, max(1, len(x) - block + 1), (5000, nb))
        idx = (starts[:, :, None] + np.arange(block)).reshape(5000, -1)[:, : len(x)]
        s = x[idx]
        sd = s.std(1, ddof=1)
        sh = np.where(sd > 0, s.mean(1) / np.where(sd > 0, sd, 1) * np.sqrt(52), 0.0)
        lower = float(np.percentile(sh, 20))
        out["lower80"] = round(lower, 2)
        out["stage4_row"] = stage4_row(lower) + ("" if months >= 6 else " (information only before month 6)")
    else:
        out["note"] = "the 80% bound needs 8+ weekly points"
    return out


def execution(orders: dict[str, Any], ai_book: dict[str, Any], gap_alert: float = 0.005) -> dict[str, Any]:
    """Broker legs (core book and AI picks) by status, with the fill gap against the simulator where known."""
    legs = [(k, leg) for k, d in orders.items() for leg in d.get("legs", [])]
    legs += [(p.get("ticker", "?"), leg) for p in ai_book.get("pairs", []) for leg in p.get("legs", [])]
    status: dict[str, int] = {}
    gaps = []
    for _, leg in legs:
        status[leg.get("status", "?")] = status.get(leg.get("status", "?"), 0) + 1
        if leg.get("gap") is not None:
            gaps.append(float(leg["gap"]))
    flags = [f for p in ai_book.get("pairs", []) for f in p.get("audit_flags", [])]
    g = np.abs(np.array(gaps)) if gaps else np.array([])
    return {"legs": len(legs), "by_status": status, "filled_with_gap": len(gaps),
            "mean_abs_gap_bp": round(float(g.mean()) * 1e4, 1) if len(g) else None,
            "worst_gap_bp": round(float(g.max()) * 1e4, 1) if len(g) else None,
            "gaps_over_alert": int((g > gap_alert).sum()) if len(g) else 0,
            "problem_legs": status.get("rejected", 0) + status.get("canceled", 0) + status.get("expired", 0),
            "ai_pairs": len(ai_book.get("pairs", [])), "ai_audit_flags": len(flags),
            "ai_audit_status": ai_book.get("broker_audit_status")}


def load() -> tuple[dict[str, Any], dict[str, Any]]:
    sys.path.insert(0, str(BACKEND / "scripts"))
    from vol_target_b0 import tbill
    runs = [json.loads(x) for x in (FWD / "allocator" / "ledger.jsonl").read_text().splitlines()] \
        if (FWD / "allocator" / "ledger.jsonl").exists() else []
    base = "master+brakes" if any("master+brakes" in r["books"] for r in runs) else "master"
    eq = pd.Series({r["data_through"]: r["books"][base]["equity"] for r in runs if base in r["books"]})
    s4 = stage4(eq, tbill()) | {"book": base}
    orders = json.loads((FWD / "broker" / "orders.json").read_text()) if (FWD / "broker" / "orders.json").exists() else {}
    ai = json.loads((FWD / "ai_picks" / "book.json").read_text()) if (FWD / "ai_picks" / "book.json").exists() else {}
    return s4, execution(orders, ai)


if __name__ == "__main__":
    s4, ex = load()
    print(json.dumps({"stage4": s4, "execution": ex}, indent=1, default=str))
