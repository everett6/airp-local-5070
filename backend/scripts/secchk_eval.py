"""Does the SEC revenue cross-check change Bonsai's fact-sheet books? Old vs cleaned sheets, same releases.

    python scripts/secchk_eval.py

Per book and year: monthly rank IC of BUY log-odds vs the return over the sector ETF on the book's horizon, for the
old decisions (results/events/decide_bonsai-27b_latest_factsheet*) and the cleaned ones (*_secchk), on the releases
both have; plus how many sheets changed and how often the BUY/PASS call flipped. The cleaned sheets are adopted
either way (they remove wrong numbers); this only reports the effect.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd
from event_eval import build

from app.sandbox.events import Prices, monthly_ic

EV = BACKEND / "results" / "events"
YEARS = {"2025-26": ("factsheet", "data/events/events_sp500_2025.csv", "sp500_2025"),
         "2024": ("factsheet2024", "data/events/events_sp500_2024.csv", "sp500_2024")}


def load(tag: str, h: int) -> pd.DataFrame:
    p = EV / f"decide_bonsai-27b_latest_{tag}{'' if h == 20 else f'_h{h}'}.jsonl"
    rows = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
    return pd.DataFrame([{"accession": r["accession"], "logodds": r["logodds"], "buy": r["buy"]} for r in rows
                         if not r.get("censored")], columns=["accession", "logodds", "buy"])


def main() -> None:
    p = Prices.from_long(pd.read_parquet(BACKEND / "data/events/ohlcv_2023-01-01_2026-09-25.parquet"))
    res = {}
    for year, (tag, events, name) in YEARS.items():
        a = pd.read_csv(EV / f"features_{name}.csv")
        b = pd.read_csv(EV / f"features_{name}_secchk.csv")
        changed = set(a.loc[a["fact_sheet"] != b.set_index("accession").loc[a["accession"], "fact_sheet"].values,
                            "accession"])
        ev = pd.read_csv(BACKEND / events)
        df = build(ev, p)
        for h, book in {5: "quick money", 20: "mid term", 120: "long term"}.items():
            old, new = load(tag, h), load(f"{tag}_secchk", h)
            x = df.merge(old, on="accession").merge(new, on="accession", suffixes=("_old", "_new"))
            x = x.dropna(subset=[f"fwd{h}"])
            if len(x) < 50:
                continue
            k = f"{year} {book} ({h}d)"
            ch = x[x["accession"].isin(changed)]
            res[k] = {"n": len(x), "changed": len(ch), "flipped": int((ch["buy_old"] != ch["buy_new"]).sum()),
                      "old": monthly_ic(x, "logodds_old", f"fwd{h}", min_n=10),
                      "new": monthly_ic(x, "logodds_new", f"fwd{h}", min_n=10)}
            o, n = res[k]["old"], res[k]["new"]
            print(f"{k:26s} n={len(x):5d} changed={len(ch):4d} flipped={res[k]['flipped']:3d}  IC old "
                  f"{o['mean_ic']:+.3f} [{o['ci_lo']:+.3f}, {o['ci_hi']:+.3f}]  new {n['mean_ic']:+.3f} "
                  f"[{n['ci_lo']:+.3f}, {n['ci_hi']:+.3f}]")
    (EV / "secchk_eval.json").write_text(json.dumps(res, indent=1) + "\n")


if __name__ == "__main__":
    main()
