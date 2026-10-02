"""Signal registry and the rules of the bounded learning loop (docs/DEV_PLAN_AUTONOMOUS.md Phase B, "the only
self-improving part"). Rules fixed 2026-09-27, before any candidate was proposed.

A signal is a small, checkable recipe over a FIXED menu of point-in-time fields that exist both in the 2024-26
history and in the live forward runner:

    score = sum over at most 3 terms of sign x (percentile of the field among the releases accepted BEFORE this one)
            [optionally only for releases whose sector is in a list; others get no score]

Until 1 Oct 2026 the percentile was taken within the entry month, so a release filed later in the month moved the
scores of the ones before it (found by the outside review; no signal had been proposed yet). Now a score uses only
what existed when its release was accepted, and for live releases the per-field percentiles are stored when the
release is collected (docs/PLAN_60_V2.md, "Outside review, second part", rules 2 and 3).

Categorical fields enter as indicators ("guidance=raised"). Horizon: 5 trading days vs the sector ETF (the only
outcome the live ledger records). Nothing is fitted, so nothing can be tuned to the test data.

Life of a signal:
  proposed → train test (2024 only) → holdout test (2025-26, once; test k needs p < 0.05 / (k (k + 1)), which sums
             to under 0.05 over any number of tests)
           → shadow (scored on live forward releases, no money) → promoted (a capped paper sleeve) → retired
  Every proposal is written to results/trials_registry.jsonl, so the Deflated Sharpe bar rises with each try.
The loop may add or remove signals only. It can't change the book, the mandate, the kill switch, the limits below
or its own budget: these constants are the budget.
"""
from __future__ import annotations

import bisect
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
HOLDOUT_MIN_IC = 0.02        # holdout: mean IC at least this, one-sided p below holdout_alpha(k) for test number k,
#                              and the blend (logodds rank + signal rank) beats logodds alone, paired 95% CI above 0
HOLDOUT_ALPHA = 0.05         # the lifetime error budget of all holdout tests together
SHADOW_MIN_DAYS = 90         # shadow: at least 3 months and 100 scored live releases before promotion
SHADOW_MAX_DAYS = 180        # not promoted by 6 months: retired
LOOK_DAYS = (90, 120, 150, 180)  # promotion is checked at these four points of the shadow period, not every week
PROMOTE_ALPHA = 0.20         # the error budget of one signal's four looks together (look j: PROMOTE_ALPHA / (j (j + 1)))
MIN_PRIOR = 100              # a release with fewer earlier releases than this has no percentile, so no score
PIT = "pit:"                 # column prefix of a field's stored point-in-time percentile
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


def holdout_alpha(k: int) -> float:
    """The p-value holdout test number k (1, 2, ...) must beat. The sum over all k is HOLDOUT_ALPHA (the old
    0.05 / k summed to 0.155 over twelve tests and has no finite total)."""
    return HOLDOUT_ALPHA / (k * (k + 1))


def look_alpha(j: int) -> float:
    """The one-sided level of a signal's promotion look number j: 0.10, 0.033, 0.017, 0.01."""
    return PROMOTE_ALPHA / (j * (j + 1))


def menu_fields() -> list[str]:
    return [*NUMERIC, *(f"{k}={v}" for k, vs in CATEGORICAL.items() for v in vs)]


def order_key(df: pd.DataFrame) -> pd.Series:
    """What "before" means: the SEC acceptance time, or (frames without it) the entry day."""
    return (df["accepted_utc"] if "accepted_utc" in df.columns else df["entry"]).astype(str)


def field_values(df: pd.DataFrame, f: str) -> pd.Series:
    if "=" in f:
        k, v = f.split("=", 1)
        return (df[k].astype(str) == v).astype(float)
    return df[f].astype(float)


def pit_pct(values: pd.Series, order: pd.Series, min_prior: int = MIN_PRIOR, missing: float | None = 0.5) -> pd.Series:
    """Each row's percentile among the rows ordered strictly before it: the share below it, ties counting half.
    Rows with the same order key do not see each other. A missing value gets `missing`; a row with fewer than
    `min_prior` earlier rows gets nothing. Nothing after a row can change its number."""
    v = pd.to_numeric(values, errors="coerce").to_numpy(float)
    keys = order.astype(str).to_numpy()
    idx = np.argsort(keys, kind="stable")
    out = np.full(len(v), np.nan)
    seen: list[float] = []
    i = 0
    while i < len(idx):
        j = i
        while j < len(idx) and keys[idx[j]] == keys[idx[i]]:
            j += 1
        if i >= min_prior:  # i rows come strictly before this group
            for g in idx[i:j]:
                x = v[g]
                if np.isnan(x):
                    out[g] = np.nan if missing is None else missing
                elif seen:
                    lo, hi = bisect.bisect_left(seen, x), bisect.bisect_right(seen, x)
                    out[g] = (lo + 0.5 * (hi - lo)) / len(seen)
                else:
                    out[g] = np.nan if missing is None else missing
        for g in idx[i:j]:
            if not np.isnan(v[g]):
                bisect.insort(seen, float(v[g]))
        i = j
    return pd.Series(out, index=values.index)


def add_pit(df: pd.DataFrame, min_prior: int = MIN_PRIOR) -> pd.DataFrame:
    """The frame with every menu field's point-in-time percentile (columns "pit:<field>"), computed among the frame's
    own earlier rows. A value already stored for a row (a live release, kept when it was collected) is left as it is."""
    d = df.copy()
    order = order_key(d)
    for f in menu_fields():
        if f.split("=")[0] not in d.columns:
            continue
        pct = pit_pct(field_values(d, f), order, min_prior)
        d[PIT + f] = d[PIT + f].combine_first(pct) if PIT + f in d.columns else pct
    return d


def _ready(df: pd.DataFrame) -> pd.DataFrame:
    need = [PIT + f for f in menu_fields() if f.split("=")[0] in df.columns]
    return df if all(c in df.columns and df[c].notna().any() for c in need) and len(need) else add_pit(df)


def score(df: pd.DataFrame, sig: Signal) -> pd.Series:
    """The signal's value for each row: NaN outside its sectors and for rows too early to have percentiles. Missing
    numeric values rank in the middle. Built from the stored percentiles only."""
    d = _ready(df)
    total = pd.Series(0.0, index=d.index)
    for t in sig.terms:
        total += t["sign"] * d[PIT + t["field"]]
    if sig.sectors:
        total = total.where(d["sector"].isin(sig.sectors))
    return total


def with_signal(df: pd.DataFrame, sig: Signal, col: str = "_s") -> pd.DataFrame:
    """`df` (a pool: history first, then live releases) with the signal's score in `col` and, in `col`_blend, the
    score's own point-in-time percentile added to the judge's: the blend the tests and the sleeve use."""
    d = _ready(df)
    d = d.assign(**{col: score(d, sig)})
    d[col + "_blend"] = d[PIT + "logodds"] + pit_pct(d[col], order_key(d), missing=None)
    return d


def monthly_ics(df: pd.DataFrame, col: str, outcome: str = "fwd5", min_n: int = 20) -> np.ndarray:
    d = df.dropna(subset=[col, outcome])
    return np.array([g[col].rank().corr(g[outcome].rank()) for _, g in d.groupby("month")
                     if len(g) >= min_n and g[col].nunique() > 1])


def _boot(x: np.ndarray, lo_q: float, n_boot: int = 5000) -> float:
    if len(x) < 2:
        return float("nan")
    b = x[np.random.default_rng(0).integers(0, len(x), (n_boot, len(x)))].mean(1)
    return float(np.percentile(b, lo_q))


def _t_lower(x: np.ndarray, level: float) -> float:
    """The one-sided lower confidence bound of the mean at `level`, from Student's t over the months. A bootstrap
    of three or four months cannot give a bound this strict: when every month is positive its lowest resample is
    still positive, so it passes at any level (a no-edge signal passed a 10% look 7 times in 40 that way)."""
    if len(x) < 3 or float(np.std(x, ddof=1)) == 0.0:
        return float("nan")
    from scipy.stats import t
    return float(np.mean(x) - t.ppf(1 - level, len(x) - 1) * np.std(x, ddof=1) / math.sqrt(len(x)))


def _norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2))


def blend_gain(df: pd.DataFrame, col: str, min_n: int = 20) -> np.ndarray:
    """Per month: IC of the blend (judge's percentile + signal's percentile, both point-in-time) minus IC of the
    judge's score alone. Does the signal add to the live score? `df` comes from `with_signal`."""
    d = df.dropna(subset=[col, col + "_blend", "logodds", "fwd5"])
    out = []
    for _, g in d.groupby("month"):
        if len(g) >= min_n and g[col].nunique() > 1:
            y = g["fwd5"].rank()
            out.append(g[col + "_blend"].rank().corr(y) - g["logodds"].rank().corr(y))
    return np.array(out)


def _part(d: pd.DataFrame, where: pd.Series | None) -> pd.DataFrame:
    return d if where is None else d[where.reindex(d.index, fill_value=False)]


def train_test(df: pd.DataFrame, sig: Signal, where: pd.Series | None = None) -> dict[str, Any]:
    """`where` picks the rows the test is about (2024); the rest of `df` only supplies earlier releases to rank
    against."""
    d = _part(with_signal(df, sig), where)
    ics = monthly_ics(d, "_s")
    mean = float(ics.mean()) if len(ics) else float("nan")
    lo = _boot(ics, 2.5)
    ok = len(ics) >= 6 and mean >= TRAIN_MIN_IC and lo > 0
    return {"months": len(ics), "mean_ic": round(mean, 4) if len(ics) else None,
            "ci_lo": None if math.isnan(lo) else round(lo, 4), "pass": bool(ok)}


def holdout_test(df: pd.DataFrame, sig: Signal, tests_ever: int, where: pd.Series | None = None) -> dict[str, Any]:
    """`tests_ever` counts this test too: it must beat holdout_alpha(tests_ever), one-sided."""
    d = _part(with_signal(df, sig), where)
    ics, gain = monthly_ics(d, "_s"), blend_gain(d, "_s")
    n = len(ics)
    mean = float(ics.mean()) if n else float("nan")
    sd = float(ics.std(ddof=1)) if n > 1 else float("nan")
    z = mean / (sd / math.sqrt(n)) if n > 1 and sd > 0 else float("nan")
    p = _norm_sf(z) if not math.isnan(z) else 1.0
    need, glo = holdout_alpha(max(1, tests_ever)), _boot(gain, 2.5)
    ok = n >= 6 and mean >= HOLDOUT_MIN_IC and p < need and glo > 0
    return {"months": n, "mean_ic": round(mean, 4) if n else None, "p_one_sided": round(p, 6),
            "p_needed": round(need, 6), "blend_gain": round(float(gain.mean()), 4) if len(gain) else None,
            "blend_gain_ci_lo": None if math.isnan(glo) else round(glo, 4), "pass": bool(ok)}


def live_test(df: pd.DataFrame, sig: Signal, where: pd.Series | None = None, level: float = PROMOTE_ALPHA
              ) -> dict[str, Any]:
    """Live forward releases (month groups of at least 10): the blend's gain over the judge's score, with its
    one-sided lower bound at `level` (Student's t over months; a promotion look passes if it is above 0)."""
    d = _part(with_signal(df, sig), where).dropna(subset=["_s", "fwd5"])
    gain = blend_gain(d, "_s", min_n=10)
    ics = monthly_ics(d, "_s", min_n=10)
    return {"events": len(d), "months": len(gain),
            "mean_ic": round(float(ics.mean()), 4) if len(ics) else None,
            "blend_gain": round(float(gain.mean()), 4) if len(gain) else None,
            "blend_gain_lo": None if math.isnan(lo := _t_lower(gain, level)) else round(lo, 4),
            "level": round(level, 4)}


def sleeve_week(df: pd.DataFrame, sigs: list[Signal], where: pd.Series | None = None) -> dict[str, Any]:
    """The promoted sleeve: long the live releases whose combined score (the promoted signals' percentiles added to
    the judge's) is in the top fifth of everything scored before them, each held 5 days; mean excess return per
    release after costs, and the sleeve's share of the book."""
    if not sigs:
        return {"weight": 0.0, "events": 0}
    d = _ready(df)
    c = d[PIT + "logodds"].copy()
    for s in sigs:
        c = c + pit_pct(score(d, s), order_key(d), missing=None).fillna(0.5)
    d = d.assign(_c=c)
    d["_top"] = pit_pct(d["_c"], order_key(d), missing=None) > 0.8
    d = _part(d, where).dropna(subset=["_c", "fwd5"])
    top = d[d["_top"]]
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
