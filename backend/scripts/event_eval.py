"""Score earnings-event signals: does a signal rank the next 20/60 trading days' returns vs the sector?

    python scripts/event_eval.py                         # code-only baselines
    python scripts/event_eval.py --extract results/events/extract_qwen3_8b.jsonl --decide results/events/decide_<m>.jsonl

Signals (all known at the entry time; see app/sandbox/events.py for the timing rules):
  ear            announcement-day return vs sector (earnings-announcement drift); trades one day later
  rev_growth     reported revenue / year-earlier revenue - 1, from the reader's checked numbers
  eps_change     (EPS - year-earlier EPS) / |year-earlier EPS|, capped at +-2; checked numbers
  guidance       +1 raised or initiated, 0 maintained or none, -1 lowered or withdrawn
  momentum       12-1 month return vs sector before the entry (a control: is anything new here?)
  decision       the decision model's log-odds (scripts/decide_events.py), when given
Windows: all events, and the clean window (entries from 2025-01-01, after qwen3's training data).
Writes results/events/eval_<tag>.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import numpy as np
import pandas as pd

from app.sandbox.events import (
    Prices,
    entry_index,
    fwd_excess,
    monthly_ic,
    plausible,
    quintile_spread,
    reaction,
)

SECTOR_ETF = {"Information Technology": "XLK", "Financials": "XLF", "Health Care": "XLV",
              "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", "Energy": "XLE", "Industrials": "XLI",
              "Materials": "XLB", "Utilities": "XLU", "Real Estate": "XLRE", "Communication Services": "XLC"}
GUIDE = {"raised": 1, "initiated": 1, "maintained": 0, "none": 0, "lowered": -1, "withdrawn": -1}


def build(events: pd.DataFrame, p: Prices) -> pd.DataFrame:
    days = pd.DatetimeIndex(p.open.index)
    rows = []
    for r in events.itertuples():
        t = str(r.ticker).replace(".", "-")
        etf = SECTOR_ETF.get(str(r.sector))
        acc = datetime.fromisoformat(str(r.accepted_utc))
        i = entry_index(days, acc)
        if etf is None or i is None or t not in p.open.columns:
            rows.append({"accession": r.accession, "ticker": t, "scorable": False})
            continue
        row = {"accession": r.accession, "ticker": t, "sector": r.sector, "index": r.index, "scorable": True,
               "entry": days[i].date().isoformat(), "month": days[i].strftime("%Y-%m"),
               "fwd5": fwd_excess(p, t, etf, i, 5), "fwd20": fwd_excess(p, t, etf, i, 20),
               "fwd60": fwd_excess(p, t, etf, i, 60), "fwd120": fwd_excess(p, t, etf, i, 120),
               "ear": reaction(p, t, etf, i),
               "fwd20_ear": fwd_excess(p, t, etf, i + 1, 20), "fwd60_ear": fwd_excess(p, t, etf, i + 1, 60)}
        c, e = p.close[t], p.close[etf]
        if i >= 253 and pd.notna(c.iloc[i - 253]) and pd.notna(c.iloc[i - 22]):
            row["momentum"] = float((c.iloc[i - 22] / c.iloc[i - 253] - 1) - (e.iloc[i - 22] / e.iloc[i - 253] - 1))
        rows.append(row)
    return pd.DataFrame(rows)


def add_extract(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    ex = pd.DataFrame([json.loads(x) for x in path.read_text().splitlines()])
    for key in ("revenue", "eps"):  # extracts made before the plausibility rule get it here
        ex[key] = ex[key].map(lambda d, k=key: plausible(k, d if isinstance(d, dict) else {})[0])

    def growth(d: object) -> float | None:
        if isinstance(d, dict) and d.get("prior") not in (None, 0) and d.get("q") is not None:
            return float(d["q"]) / float(d["prior"]) - 1
        return None

    def change(d: object) -> float | None:
        if isinstance(d, dict) and d.get("prior") not in (None, 0) and d.get("q") is not None:
            return float(np.clip((float(d["q"]) - float(d["prior"])) / abs(float(d["prior"])), -2, 2))
        return None
    ex["rev_growth"] = ex["revenue"].map(growth)
    ex["eps_change"] = ex["eps"].map(change)
    ex["guidance_score"] = ex["guidance"].map(lambda g: GUIDE.get(g))
    ex["reader_ok"] = ex["rev_growth"].notna() | ex["eps_change"].notna()
    return df.merge(ex[["accession", "rev_growth", "eps_change", "guidance_score", "tone", "reader_ok"]],
                    on="accession", how="left")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default="data/events/events_2024-01-01_2026-09-24.csv")
    ap.add_argument("--prices", default="data/events/ohlcv_2023-01-01_2026-09-25.parquet")
    ap.add_argument("--extract", default=None)
    ap.add_argument("--decide", default=None, help="jsonl with accession, logodds (and buy) per event")
    ap.add_argument("--clean-from", default="2025-01-01")
    ap.add_argument("--tag", default="baseline")
    args = ap.parse_args()
    events = pd.read_csv(BACKEND / args.events)
    p = Prices.from_long(pd.read_parquet(BACKEND / args.prices))
    df = build(events, p)
    if args.extract:
        df = add_extract(df, BACKEND / args.extract)
    if args.decide:
        dec = pd.DataFrame([json.loads(x) for x in (BACKEND / args.decide).read_text().splitlines()])
        df = df.merge(dec[["accession", "logodds"]].rename(columns={"logodds": "decision"}), on="accession",
                      how="left")
    signals = [s for s in ("ear", "momentum", "rev_growth", "eps_change", "guidance_score", "decision")
               if s in df.columns]
    ok = df[df["scorable"]]
    out = {"events": len(df), "scorable": int(df["scorable"].sum()),
           "unscorable_share_pct": round(100 * (1 - df["scorable"].mean()), 1), "windows": {}}
    for wname, w in (("all", ok), ("clean", ok[ok["entry"] >= args.clean_from])):
        res = {}
        for s in signals:
            for h in ("fwd20", "fwd60"):
                target = f"{h}_ear" if s == "ear" else h
                res[f"{s}:{h}"] = {"ic": monthly_ic(w, s, target), "top_minus_bottom_quintile": quintile_spread(
                    w, s, target)}
        out["windows"][wname] = {"events": len(w), "results": res}
    (BACKEND / "results" / "events").mkdir(parents=True, exist_ok=True)
    (BACKEND / "results" / "events" / f"eval_{args.tag}.json").write_text(json.dumps(out, indent=1) + "\n")
    print(f"{out['scorable']} of {out['events']} events scorable")
    for wname, wres in out["windows"].items():
        print(f"\n[{wname}] {wres['events']} events")
        print(f"{'signal:horizon':26s} {'months':>6s} {'rank IC [95% CI]':>28s} {'top-bottom 5th % [95% CI]':>30s}")
        for k, v in wres["results"].items():
            ic, sp = v["ic"], v["top_minus_bottom_quintile"]
            if ic["mean_ic"] is None:
                continue
            print(f"{k:26s} {ic['months']:>6d} {ic['mean_ic']:>+10.4f} [{ic['ci_lo']:+.4f}, {ic['ci_hi']:+.4f}]"
                  f" {sp['spread_pct']:>+10.3f} [{sp['ci_lo']:+.3f}, {sp['ci_hi']:+.3f}]")


if __name__ == "__main__":
    main()
