"""Signal registry and the rules of the bounded learning loop (docs/DEV_PLAN_AUTONOMOUS.md Phase B, "the only
self-improving part"). Rules fixed 2026-09-27, before any candidate was proposed.

A signal is a small, checkable recipe over a FIXED menu of point-in-time fields that exist both in the 2024-26
history and in the live forward runner:

    score = sum over at most 3 terms of sign x (percentile rank of the field within the entry month)
            [optionally only for releases whose sector is in a list; others get no score]

Categorical fields enter as indicators ("guidance=raised"). Horizon: 5 trading days vs the sector ETF (the only
outcome the live ledger records). Nothing is fitted, so nothing can be tuned to the test data.

Life of a signal:
  proposed → train test (2024 only) → holdout test (2025-26, once, Bonferroni over every holdout test ever run)
           → shadow (scored on live forward releases, no money) → promoted (a capped paper sleeve) → retired
  Every proposal is written to results/trials_registry.jsonl, so the Deflated Sharpe bar rises with each try.
The loop may add or remove signals only. It can't change the book, the mandate, the kill switch, the limits below
or its own budget: these constants are the budget.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

NUMERIC = {
    "eps_growth": "EPS this quarter vs the same quarter a year before (clipped, SEC-checked)",
    "rev_growth": "revenue this quarter vs the same quarter a year before (clipped, SEC-checked)",
    "momentum": "the stock's 12-month return minus the last month, vs its sector ETF, before the release",
    "logodds": "Bonsai-27B's 1-week up/down log-odds for the release (the live event score)",
}
CATEGORICAL = {
    "guidance": ("raised", "maintained", "lowered", "initiated", "withdrawn", "none", "unverified"),
    "tone": ("positive", "neutral", "negative"),
}
SECTORS = ("Communication Services", "Consumer Discretionary", "Consumer Staples", "Energy", "Financials",
           "Health Care", "Industrials", "Information Technology", "Materials", "Real Estate", "Utilities")

MAX_TERMS = 3
PROPOSALS_PER_MONTH = 5
TRAIN_MIN_IC = 0.02          # train: mean monthly IC at least this, and its 95% CI above 0
HOLDOUT_MIN_IC = 0.02        # holdout: mean IC at least this, one-sided p below 0.05 / (holdout tests ever run),
#                              and the blend (logodds rank + signal rank) beats logodds alone, paired 95% CI above 0
SHADOW_MIN_DAYS = 90         # shadow: at least 3 months and 100 scored live releases before promotion
SHADOW_MAX_DAYS = 180        # not promoted by 6 months: retired
LIVE_MIN_EVENTS = 100
SLEEVE_PER_SIGNAL = 0.10     # each promoted signal: a 10% paper sleeve; all sleeves together at most 20%
SLEEVE_CAP = 0.20
SLEEVE_COST = 0.004          # round trip, per 5-day holding
STATUSES = ("rejected_train", "rejected_holdout", "shadow", "promoted", "retired")


class SpecError(ValueError):
    pass


@dataclass
class Signal:
    name: str
    terms: list[dict[str, Any]]            # [{"field": "eps_growth" | "guidance=raised", "sign": 1 | -1}]
    sectors: list[str] | None = None
    rationale: str = ""
    proposer: str = ""
    status: str = "proposed"
    history: list[dict[str, Any]] = field(default_factory=list)

    def key(self) -> str:
        t = ",".join(sorted(f"{'+' if x['sign'] > 0 else '-'}{x['field']}" for x in self.terms))
        return t + ("|" + ",".join(sorted(self.sectors)) if self.sectors else "")

    def note(self, date: str, event: str, **info: Any) -> None:
        self.history.append({"date": date, "event": event, **info})


def validate(d: dict[str, Any]) -> Signal:
    """A proposal from the model, checked by code: unknown fields, too many terms, bad signs or sectors are refused."""
    if not isinstance(d, dict):
        raise SpecError("not an object")
    terms = d.get("terms")
    if not isinstance(terms, list) or not 1 <= len(terms) <= MAX_TERMS:
        raise SpecError(f"1 to {MAX_TERMS} terms")
    out = []
    for t in terms:
        f, s = str((t or {}).get("field", "")).strip(), (t or {}).get("sign")
        if s not in (1, -1):
            raise SpecError(f"sign must be 1 or -1 ({f})")
        if "=" in f:
            k, v = f.split("=", 1)
            if k not in CATEGORICAL or v not in CATEGORICAL[k]:
                raise SpecError(f"unknown category {f}")
        elif f not in NUMERIC:
            raise SpecError(f"unknown field {f}")
        out.append({"field": f, "sign": int(s)})
    if len({x["field"] for x in out}) < len(out):
        raise SpecError("a field appears twice")
    sectors = d.get("sectors") or None
    if sectors is not None and (not isinstance(sectors, list) or not all(x in SECTORS for x in sectors)
                                or len(sectors) >= len(SECTORS)):
        raise SpecError("sectors must be a strict subset of the GICS sector names")
    name = re.sub(r"[^a-z0-9_]", "", str(d.get("name", "")).lower().replace(" ", "_"))[:40] or "signal"
    return Signal(name=name, terms=out, sectors=sectors, rationale=str(d.get("rationale", ""))[:300])


def score(df: pd.DataFrame, sig: Signal, month: str = "month") -> pd.Series:
    """The signal's value for each row (NaN outside its sectors). Missing numeric values rank in the middle."""
    total = pd.Series(0.0, index=df.index)
    for t in sig.terms:
        f = t["field"]
        if "=" in f:
            k, v = f.split("=", 1)
            x = (df[k].astype(str) == v).astype(float)
        else:
            x = df[f].astype(float)
        r = x.groupby(df[month]).rank(pct=True).fillna(0.5)
        total += t["sign"] * r
    if sig.sectors:
        total = total.where(df["sector"].isin(sig.sectors))
    return total


def monthly_ics(df: pd.DataFrame, col: str, outcome: str = "fwd5", min_n: int = 20) -> np.ndarray:
    d = df.dropna(subset=[col, outcome])
    return np.array([g[col].rank().corr(g[outcome].rank()) for _, g in d.groupby("month")
                     if len(g) >= min_n and g[col].nunique() > 1])


def _boot(x: np.ndarray, lo_q: float, n_boot: int = 5000) -> float:
    if len(x) < 2:
        return float("nan")
    b = x[np.random.default_rng(0).integers(0, len(x), (n_boot, len(x)))].mean(1)
    return float(np.percentile(b, lo_q))


def _norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2))


def blend_gain(df: pd.DataFrame, col: str, min_n: int = 20) -> np.ndarray:
    """Per month: IC of (rank logodds + rank signal) minus IC of logodds alone. Does the signal add to the live score?"""
    d = df.dropna(subset=[col, "logodds", "fwd5"])
    out = []
    for _, g in d.groupby("month"):
        if len(g) >= min_n and g[col].nunique() > 1:
            y = g["fwd5"].rank()
            blend = g["logodds"].rank(pct=True) + g[col].rank(pct=True)
            out.append(blend.rank().corr(y) - g["logodds"].rank().corr(y))
    return np.array(out)


def train_test(df: pd.DataFrame, sig: Signal) -> dict[str, Any]:
    d = df.assign(_s=score(df, sig))
    ics = monthly_ics(d, "_s")
    mean = float(ics.mean()) if len(ics) else float("nan")
    lo = _boot(ics, 2.5)
    ok = len(ics) >= 6 and mean >= TRAIN_MIN_IC and lo > 0
    return {"months": len(ics), "mean_ic": round(mean, 4) if len(ics) else None,
            "ci_lo": None if math.isnan(lo) else round(lo, 4), "pass": bool(ok)}


def holdout_test(df: pd.DataFrame, sig: Signal, tests_ever: int) -> dict[str, Any]:
    """`tests_ever` counts this test too. Bonferroni: one-sided p < 0.05 / tests_ever."""
    d = df.assign(_s=score(df, sig))
    ics, gain = monthly_ics(d, "_s"), blend_gain(d, "_s")
    n = len(ics)
    mean = float(ics.mean()) if n else float("nan")
    sd = float(ics.std(ddof=1)) if n > 1 else float("nan")
    z = mean / (sd / math.sqrt(n)) if n > 1 and sd > 0 else float("nan")
    p = _norm_sf(z) if not math.isnan(z) else 1.0
    need, glo = 0.05 / max(1, tests_ever), _boot(gain, 2.5)
    ok = n >= 6 and mean >= HOLDOUT_MIN_IC and p < need and glo > 0
    return {"months": n, "mean_ic": round(mean, 4) if n else None, "p_one_sided": round(p, 5),
            "p_needed": round(need, 5), "blend_gain": round(float(gain.mean()), 4) if len(gain) else None,
            "blend_gain_ci_lo": None if math.isnan(glo) else round(glo, 4), "pass": bool(ok)}


def live_test(df: pd.DataFrame, sig: Signal) -> dict[str, Any]:
    """Live forward releases (month groups of at least 10): the blend's gain over logodds, 80% one-sided."""
    d = df.assign(_s=score(df, sig)).dropna(subset=["_s", "fwd5"])
    gain = blend_gain(d, "_s", min_n=10)
    ics = monthly_ics(d, "_s", min_n=10)
    return {"events": len(d), "months": len(gain),
            "mean_ic": round(float(ics.mean()), 4) if len(ics) else None,
            "blend_gain": round(float(gain.mean()), 4) if len(gain) else None,
            "blend_gain_lo80": round(_boot(gain, 20.0), 4) if len(gain) > 1 else None}


def sleeve_week(df: pd.DataFrame, sigs: list[Signal]) -> dict[str, Any]:
    """The promoted sleeve: long the top fifth of live releases by the promoted signals' combined score (blended with
    logodds), each held 5 days; mean excess return per release after costs, and the sleeve's share of the book."""
    if not sigs:
        return {"weight": 0.0, "events": 0}
    d = df.copy()
    d["_c"] = d["logodds"].groupby(d["month"]).rank(pct=True)
    for s in sigs:
        d["_c"] = d["_c"] + score(d, s).groupby(d["month"]).rank(pct=True).fillna(0.5)
    d = d.dropna(subset=["_c", "fwd5"])
    top = d[d["_c"].groupby(d["month"]).rank(pct=True) > 0.8]
    return {"weight": min(SLEEVE_CAP, SLEEVE_PER_SIGNAL * len(sigs)), "events": len(top),
            "mean_net_5d": round(float(top["fwd5"].mean() - SLEEVE_COST), 5) if len(top) else None}


class Registry:
    def __init__(self, path: Path) -> None:
        self.path = path
        raw = json.loads(path.read_text()) if path.exists() else {}
        self.signals = {n: Signal(**s) for n, s in raw.get("signals", {}).items()}
        self.holdout_tests = int(raw.get("holdout_tests", 0))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"holdout_tests": self.holdout_tests,
                                   "signals": {n: asdict(s) for n, s in self.signals.items()}}, indent=1) + "\n")
        tmp.replace(self.path)

    def tried_keys(self) -> set[str]:
        return {s.key() for s in self.signals.values()}

    def add(self, sig: Signal) -> Signal:
        """Refuses a recipe tried before (same terms and sectors, any name); renames a clashing name."""
        if sig.key() in self.tried_keys():
            raise SpecError(f"already tried: {sig.key()}")
        base, i = sig.name, 2
        while sig.name in self.signals:
            sig.name, i = f"{base}_{i}", i + 1
        self.signals[sig.name] = sig
        return sig

    def with_status(self, status: str) -> list[Signal]:
        return [s for s in self.signals.values() if s.status == status]
