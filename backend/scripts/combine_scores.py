"""Stage B: one stock score from every earnings-release signal, learned walk-forward, then the master-agent portfolio.

    python scripts/combine_scores.py

Inputs per S&P 500 release (2024 all 1,995; 2025-26 the seeded 1,180 sample), all known before the entry open:
  eps_change   EPS vs a year earlier (reader's checked numbers, year-earlier from the SEC tool when missing), capped +-2
  has_eps      1 when eps_change exists (else eps_change = 0)
  bonsai       Bonsai-27B's BUY log-odds reading the research-table fact sheet
  momentum     12-1 month return vs sector (0 + flag when too little history)
  guidance     +1 raised/initiated, 0 maintained/none/unverified, -1 lowered/withdrawn
Label: the book's --horizon open-to-open return (5, 20 or 120 days) beat the sector ETF. Walk-forward: at each month's first decision, a logistic
regression is refit on releases whose outcome was already known (entry + horizon trading days < that day); scores
before 150 known outcomes are not made. No release is ever scored by a model that saw its outcome.
Caveat: Bonsai's 2024 answers fall inside its training data (it may remember 2024), so the 2024 part is optimistic.
Writes results/events/decide_combined.jsonl (logodds = the combined score's log-odds, for master_portfolio.py) and
results/events/combined_eval.json (monthly IC of the combined score vs each input on the same releases).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from event_eval import GUIDE, build

from app.learning.linear import fit_logistic_newton_np
from app.sandbox.events import Prices, monthly_ic, quintile_spread


def runs(h: int) -> list[tuple[str, str, str]]:
    """(events, research table, Bonsai decisions) per year for a book. The 20-day book keeps its original decision
    files; the 5- and 120-day books use the fact-sheet decisions made for them (same fact sheets, horizon in the
    prompt)."""
    if h == 20:
        dec = ("results/events/decide_bonsai-27b_latest_xbrl2024.jsonl", "results/events/decide_bonsai-27b_latest_xbrl.jsonl")
    else:
        dec = (f"results/events/decide_bonsai-27b_latest_factsheet2024_h{h}.jsonl",
               f"results/events/decide_bonsai-27b_latest_factsheet_h{h}.jsonl")
    return [("data/events/events_sp500_2024.csv", "results/events/features_sp500_2024.csv", dec[0]),
            ("data/events/events_sp500_2025.csv", "results/events/features_sp500_2025.csv", dec[1])]


HORIZON = 20
FEATURES = ["eps_change", "has_eps", "bonsai", "momentum", "has_mom", "guidance"]
MIN_ROWS = 150


def load(p: Prices) -> tuple[pd.DataFrame, pd.DataFrame]:
    frames, evs = [], []
    for ev_path, feat_path, dec_path in runs(HORIZON):
        f = pd.read_csv(BACKEND / feat_path)
        ev = pd.read_csv(BACKEND / ev_path)
        ev = ev[ev["accession"].isin(f["accession"])]
        dec = pd.DataFrame([json.loads(x) for x in (BACKEND / dec_path).read_text().splitlines()])
        d = build(ev, p).merge(f[["accession", "eps_q", "eps_prior", "guidance"]], on="accession")
        d = d.merge(dec[["accession", "logodds"]], on="accession", how="inner")
        frames.append(d)
        evs.append(ev)
    d = pd.concat(frames, ignore_index=True)
    d = d[d["scorable"] & d[f"fwd{HORIZON}"].notna()].copy()
    ec = (d["eps_q"] - d["eps_prior"]) / d["eps_prior"].abs().replace(0, np.nan)
    d["eps_change"] = ec.clip(-2, 2).fillna(0.0)
    d["has_eps"] = ec.notna().astype(float)
    d["bonsai"] = d["logodds"].astype(float)
    d["has_mom"] = d["momentum"].notna().astype(float)
    d["momentum"] = d["momentum"].fillna(0.0).clip(-1, 1)
    d["guidance"] = d["guidance"].map(lambda g: GUIDE.get(str(g), 0)).astype(float)
    d["y"] = (d[f"fwd{HORIZON}"] > 0).astype(int)
    return d.sort_values("entry").reset_index(drop=True), pd.concat(evs, ignore_index=True)


def walk_forward(d: pd.DataFrame, days: pd.DatetimeIndex) -> pd.Series:
    pos = {dd.date().isoformat(): i for i, dd in enumerate(days)}
    known = d["entry"].map(lambda e: days[min(pos[e] + HORIZON, len(days) - 1)].date().isoformat())
    score = pd.Series(np.nan, index=d.index)
    for month, idx in d.groupby(d["entry"].str[:7]).groups.items():
        first = d.loc[idx, "entry"].min()
        train = d[known < first]
        if len(train) < MIN_ROWS:
            continue
        x = train[FEATURES].to_numpy(float)
        mu, sd = x.mean(0), x.std(0)
        sd[sd == 0] = 1.0
        w = np.asarray(fit_logistic_newton_np(((x - mu) / sd).tolist(), train["y"].tolist(), l2=0.05))
        xs = (d.loc[idx, FEATURES].to_numpy(float) - mu) / sd
        score.loc[idx] = w[0] + xs @ w[1:]
        print(f"{month}: trained on {len(train)} known outcomes; weights "
              + ", ".join(f"{f}={v:+.2f}" for f, v in zip(FEATURES, w[1:], strict=True)), flush=True)
    return score


def main() -> None:
    global HORIZON
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--horizon", type=int, default=20, choices=(5, 20, 120), help="the book: outcome and holding days")
    HORIZON = ap.parse_args().horizon
    sfx = "" if HORIZON == 20 else f"_h{HORIZON}"
    p = Prices.from_long(pd.read_parquet(BACKEND / "data/events/ohlcv_2023-01-01_2026-09-25.parquet"))
    d, ev = load(p)
    d["combined"] = walk_forward(d, pd.DatetimeIndex(p.open.index))
    s = d[d["combined"].notna()]
    res = {"releases": len(d), "scored": len(s), "window": [s["entry"].min(), s["entry"].max()], "ic": {}}
    for sig in ("combined", "bonsai", "eps_change", "momentum", "guidance"):
        for h in sorted({f"fwd{HORIZON}", "fwd20", "fwd60"}):
            x = s.dropna(subset=[h])
            res["ic"][f"{sig}:{h}"] = {**monthly_ic(x, sig, h, min_n=10),
                                       **{f"q_{k}": v for k, v in quintile_spread(x, sig, h).items()}}
    out = BACKEND / "results" / "events"
    (out / f"combined_eval{sfx}.json").write_text(json.dumps(res, indent=1) + "\n")
    with (out / f"decide_combined{sfx}.jsonl").open("w") as f:
        for r in s.itertuples():
            f.write(json.dumps({"accession": r.accession, "ticker": r.ticker, "logodds": float(r.combined),
                                "p_beat": 1 / (1 + math.exp(-float(r.combined)))}) + "\n")
    ev[ev["accession"].isin(s["accession"])].to_csv(BACKEND / "data/events/events_sp500_combined.csv", index=False)
    print(f"{res['scored']} of {res['releases']} releases scored, {res['window'][0]} -> {res['window'][1]}")
    for k, v in res["ic"].items():
        if v["mean_ic"] is not None:
            print(f"  {k:22s} months={v['months']:3d} IC {v['mean_ic']:+.3f} [{v['ci_lo']:+.3f}, {v['ci_hi']:+.3f}]"
                  f"  top-bottom {v['q_spread_pct']:+.2f}%")


if __name__ == "__main__":
    main()
