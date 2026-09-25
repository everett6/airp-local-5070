"""Plan v2 Stage A: does year-over-year EPS change predict returns vs the sector? SEC XBRL numbers, 2010-2026, no LLM.

    python scripts/xbrl_eps_eval.py

Events: every quarter an S&P 500/400/600 member (as of Jan 1 of the filing year) first reported in a 10-Q or 10-K
filed within 100 days of the quarter's end. Entry: the open of the trading day after the filing date (XBRL gives a date
only, so the next open is the first surely-tradable one). Outcome: 20- and 60-day open-to-open return minus the
sector SPDR ETF's (SPY where the ETF did not exist yet: XLRE before 2015-10, XLC before 2018-06).

Signal fixed before the run (docs/PLAN_V2.md): eps_change = (EPS - year-earlier EPS) / |year-earlier EPS|, capped at
+-2 (the same definition that scored IC +0.15 / +0.20 on the LLM-read sample). PASS: full-sample 20-day monthly rank IC
> 0.03 with its 95% interval above zero, and positive in at least 2 of the 3 sub-periods 2010-15, 2016-20, 2021-26.
Also reported: revenue growth, momentum (control), per index, and two checks on 2024+ releases:
  release-day timing: the same signal traded at the 8-K earnings release (accepted time) instead of the 10-Q;
  reader grade: the LLM reader's EPS pair vs the XBRL numbers for the same quarter.
Writes results/xbrl/eval.json and eval.txt.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import numpy as np
import pandas as pd

from app.sandbox.events import Prices, entry_index, fwd_excess, monthly_ic, quintile_spread

SECTOR_ETF = {"Information Technology": "XLK", "Financials": "XLF", "Health Care": "XLV",
              "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", "Energy": "XLE", "Industrials": "XLI",
              "Materials": "XLB", "Utilities": "XLU", "Real Estate": "XLRE", "Communication Services": "XLC"}
PERIODS = {"2010-15": ("2010-01-01", "2015-12-31"), "2016-20": ("2016-01-01", "2020-12-31"),
           "2021-26": ("2021-01-01", "2026-12-31")}


def eps_change(q: float, prior: float) -> float | None:
    if pd.isna(q) or pd.isna(prior) or prior == 0:
        return None
    return float(np.clip((q - prior) / abs(prior), -2, 2))


def excess(p: Prices, t: str, etf: str, i: int, h: int) -> float | None:
    v = fwd_excess(p, t, etf, i, h)
    return v if v is not None else fwd_excess(p, t, "SPY", i, h)


def momentum(p: Prices, t: str, etf: str, i: int) -> float | None:
    if i < 253:
        return None
    c = p.close[t]
    e = p.close[etf] if pd.notna(p.close[etf].iloc[i - 253]) else p.close["SPY"]
    a, b, x, y = c.iloc[i - 253], c.iloc[i - 22], e.iloc[i - 253], e.iloc[i - 22]
    return None if any(pd.isna(v) for v in (a, b, x, y)) else float((b / a - 1) - (y / x - 1))


def build(xb: pd.DataFrame, members: pd.DataFrame, p: Prices) -> pd.DataFrame:
    days = pd.DatetimeIndex(p.open.index)
    mem = {(int(r.cik), int(r.year)): (str(r.ticker).replace(".", "-"), r.index, r.sector)
           for r in members.drop_duplicates(["cik", "year"]).itertuples()}
    rows = []
    for r in xb.itertuples():
        m = mem.get((int(r.cik), int(r.filed[:4])))
        if m is None or m[0] not in p.open.columns:
            continue
        t, idx, sector = m
        etf = SECTOR_ETF.get(str(sector), "SPY")
        i = entry_index(days, datetime.fromisoformat(r.filed).replace(hour=23, tzinfo=UTC))
        if i is None or pd.isna(p.open[t].iloc[i]):
            continue
        rows.append({"cik": int(r.cik), "ticker": t, "index": idx, "sector": sector, "end": r.end, "filed": r.filed,
                     "entry": days[i].date().isoformat(), "month": days[i].strftime("%Y-%m"),
                     "eps_change": eps_change(r.eps, r.eps_prior),
                     "rev_growth": (r.rev / r.rev_prior - 1) if pd.notna(r.rev) and pd.notna(r.rev_prior)
                     and r.rev_prior > 0 else None,
                     "momentum": momentum(p, t, etf, i),
                     "fwd20": excess(p, t, etf, i, 20), "fwd60": excess(p, t, etf, i, 60)})
    return pd.DataFrame(rows)


def score(df: pd.DataFrame, signals: tuple[str, ...]) -> dict[str, Any]:
    out = {}
    for s in signals:
        for h in ("fwd20", "fwd60"):
            d = df.dropna(subset=[s, h])
            out[f"{s}:{h}"] = {**monthly_ic(d, s, h), **{f"q_{k}": v for k, v in quintile_spread(d, s, h).items()}}
    return out


def release_timing(df: pd.DataFrame, xb: pd.DataFrame, events: pd.DataFrame, p: Prices) -> pd.DataFrame:
    """2024+: the same quarter's signal, entered at the 8-K earnings release instead of the 10-Q/10-K."""
    days = pd.DatetimeIndex(p.open.index)
    ev = events.assign(acc=pd.to_datetime(events["accepted_utc"], utc=True)).sort_values("acc")
    by_cik = {int(c): g for c, g in ev.groupby("cik")}
    rows = []
    for r in df[df["filed"] >= "2024-01-01"].itertuples():
        g = by_cik.get(r.cik)
        if g is None:
            continue
        end = pd.Timestamp(r.end, tz=UTC)
        filed = pd.Timestamp(r.filed, tz=UTC) + timedelta(days=1)
        hit = g[(g["acc"] > end) & (g["acc"] < filed)]
        if hit.empty:
            continue
        e = hit.iloc[-1]
        i = entry_index(days, e["acc"].to_pydatetime())
        etf = SECTOR_ETF.get(str(r.sector), "SPY")
        if i is None:
            continue
        rows.append({"accession": e["accession"], "cik": r.cik, "end": r.end, "month": days[i].strftime("%Y-%m"),
                     "eps_change": r.eps_change, "fwd20_release": excess(p, r.ticker, etf, i, 20),
                     "fwd60_release": excess(p, r.ticker, etf, i, 60), "fwd20_filing": r.fwd20,
                     "fwd60_filing": r.fwd60, "days_release_to_filing": (pd.Timestamp(r.entry, tz=UTC) - e["acc"]).days})
    return pd.DataFrame(rows)


def reader_grade(rel: pd.DataFrame, xb: pd.DataFrame, extract: Path) -> dict[str, Any]:
    if not extract.exists() or rel.empty:
        return {}
    ex = {json.loads(x)["accession"]: json.loads(x) for x in extract.read_text().splitlines()}
    xq = xb.set_index(["cik", "end"])
    n = both = q_ok = p_ok = 0
    for r in rel.itertuples():
        e = ex.get(r.accession)
        if e is None:
            continue
        n += 1
        d = e.get("eps") or {}
        if "q" in d and "prior" in d:
            both += 1
            x = xq.loc[(r.cik, r.end)]
            x = x.iloc[0] if isinstance(x, pd.DataFrame) else x
            q_ok += abs(d["q"] - x["eps"]) <= 0.011 + 0.01 * abs(x["eps"])
            p_ok += pd.notna(x["eps_prior"]) and abs(d["prior"] - x["eps_prior"]) <= 0.011 + 0.01 * abs(x["eps_prior"])
    return {"releases_read": n, "reader_eps_pairs": both,
            "reader_q_matches_xbrl_pct": round(100 * q_ok / both, 1) if both else None,
            "reader_prior_matches_xbrl_pct": round(100 * p_ok / both, 1) if both else None,
            "note": "GAAP diluted EPS; a mismatch can also be the reader picking adjusted EPS or XBRL restating"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xbrl", default="data/xbrl/eps_quarterly.csv")
    ap.add_argument("--members", default="data/xbrl/members_2010_2026.csv")
    ap.add_argument("--prices", default="data/events/ohlcv_2009-01-01_2026-09-25.parquet")
    ap.add_argument("--events", default="data/events/events_2024-01-01_2026-09-24.csv")
    ap.add_argument("--extract", default="results/events/extract_qwen3_8b.jsonl")
    args = ap.parse_args()
    xb = pd.read_csv(BACKEND / args.xbrl)
    lag = (pd.to_datetime(xb["filed"]) - pd.to_datetime(xb["end"])).dt.days
    xb = xb[(lag >= 0) & (lag <= 100) & (xb["filed"] >= "2010-01-01")].sort_values("filed")
    xb = xb.drop_duplicates(["cik", "end"])  # a quarter counts once: its first filing
    members = pd.read_csv(BACKEND / args.members)
    p = Prices.from_long(pd.read_parquet(BACKEND / args.prices))
    df = build(xb, members, p)
    sig = ("eps_change", "rev_growth", "momentum")
    res: dict[str, Any] = {"events": len(df), "with_eps_change": int(df["eps_change"].notna().sum()),
                           "window": [df["entry"].min(), df["entry"].max()], "all": score(df, sig)}
    for name, (a, b) in PERIODS.items():
        res[name] = score(df[(df["entry"] >= a) & (df["entry"] <= b)], ("eps_change",))
    for idx in ("sp500", "sp400", "sp600"):
        res[idx] = score(df[df["index"] == idx], ("eps_change",))
    full = res["all"]["eps_change:fwd20"]
    subs = [res[k]["eps_change:fwd20"]["mean_ic"] or 0 for k in PERIODS]
    res["pass"] = {"full_ic": full["mean_ic"], "full_ci_lo": full["ci_lo"], "sub_period_ics": subs,
                   "passed": bool(full["mean_ic"] and full["mean_ic"] > 0.03 and full["ci_lo"] > 0
                                  and sum(s > 0 for s in subs) >= 2)}
    rel = release_timing(df, xb, pd.read_csv(BACKEND / args.events), p)
    if not rel.empty:
        res["release_vs_filing_2024"] = {
            "events": len(rel), "median_days_release_to_filing_entry": float(rel["days_release_to_filing"].median()),
            **{f"{h}_{w}": monthly_ic(rel.dropna(subset=["eps_change", f"{h}_{w}"]), "eps_change", f"{h}_{w}")
               for h in ("fwd20", "fwd60") for w in ("release", "filing")}}
    res["reader_vs_xbrl"] = reader_grade(rel, xb, BACKEND / args.extract)
    out = BACKEND / "results" / "xbrl"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "events_scored.csv.gz", index=False)
    (out / "eval.json").write_text(json.dumps(res, indent=1, default=str) + "\n")
    head = (f"{res['events']} events ({res['with_eps_change']} with EPS change), entries {res['window'][0]} to "
            f"{res['window'][1]}")
    cols = f"{'sample':10s} {'signal:horizon':22s} {'months':>6s} {'rank IC [95% CI]':>28s} {'top-bottom 5th %':>18s}"
    lines = [head, cols]
    for part in ("all", *PERIODS, "sp500", "sp400", "sp600"):
        for k, v in res[part].items():
            if v["mean_ic"] is not None:
                lines.append(f"{part:10s} {k:22s} {v['months']:>6d} {v['mean_ic']:+.4f} [{v['ci_lo']:+.4f}, "
                             f"{v['ci_hi']:+.4f}] {v['q_spread_pct']:+8.3f}")
    lines.append(f"PASS rule: {json.dumps(res['pass'])}")
    if "release_vs_filing_2024" in res:
        lines.append(f"release vs filing timing (2024+): {json.dumps(res['release_vs_filing_2024'])}")
    lines.append(f"reader vs XBRL: {json.dumps(res['reader_vs_xbrl'])}")
    (out / "eval.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
