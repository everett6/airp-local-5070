"""Research table: one row per earnings release, everything known before the entry open, plus the fact sheet the
decision model reads.

    python scripts/build_features.py --events data/events/events_sp500_2025.csv \
        --extract results/events/extract_qwen3_8b.jsonl --xbrl data/xbrl/eps_quarterly.csv

Sources per release (the "sub-agents"):
  reader     the LLM reader's checked numbers from the press release (each quoted word for word): this quarter's
             revenue and diluted EPS, guidance, tone, quotes (scripts/extract_events.py)
  SEC tool   XBRL numbers filed BEFORE the release (strictly earlier filing dates): the year-earlier quarter when the
             reader could not verify it, and the last four reported quarters vs a year earlier (scripts/build_xbrl_eps.py)
  prices     returns vs the sector before the release (closes before the entry day)
The release's own quarter ends after the last filed quarter, so its year-earlier quarter is the filed one ending
~9 months before that (274 +- 20 days). Writes results/events/features_<name>.csv; decide_events.py --features reads
the fact_sheet column.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd
from decide_events import SECTOR_ETF, event_text, pct

from app.sandbox.events import Prices, entry_index


def sec_tool(hist: pd.DataFrame, released: str) -> dict[str, Any]:
    """XBRL rows for one company filed strictly before the release date."""
    h = hist[hist["filed"] < released[:10]].sort_values("end")
    if h.empty:
        return {"history": [], "prior": None}
    last_end = pd.Timestamp(h["end"].iloc[-1])
    target = last_end - pd.Timedelta(days=274)
    cand = h.assign(gap=(pd.to_datetime(h["end"]) - target).abs().dt.days)
    cand = cand[cand["gap"] <= 20].sort_values("gap")
    prior = cand.iloc[0] if len(cand) else None
    rows = []
    for r in h.tail(4).itertuples():
        rows.append({"end": r.end, "eps": r.eps, "eps_prior": r.eps_prior, "rev": r.rev, "rev_prior": r.rev_prior})
    return {"history": rows,
            "prior": None if prior is None else {"end": prior["end"], "eps": prior["eps"], "rev": prior["rev"],
                                                 "filed": prior["filed"]}}


def history_lines(hist: list[dict[str, Any]]) -> list[str]:
    out = []
    for r in hist:
        e = f"EPS {r['eps']:.2f}" + (f" vs {r['eps_prior']:.2f}" if pd.notna(r["eps_prior"]) else "")
        v = (f", revenue {r['rev']:,.0f}M" + (f" ({pct(r['rev'] / r['rev_prior'] - 1)})"
             if pd.notna(r["rev_prior"]) and r["rev_prior"] else "")) if pd.notna(r["rev"]) else ""
        out.append(f"  quarter ended {r['end']}: {e} a year earlier{v}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--events", default="data/events/events_sp500_2025.csv")
    ap.add_argument("--extract", default="results/events/extract_qwen3_8b.jsonl")
    ap.add_argument("--xbrl", default="data/xbrl/eps_quarterly.csv")
    ap.add_argument("--prices", default="data/events/ohlcv_2023-01-01_2026-09-25.parquet")
    ap.add_argument("--name", default="sp500_2025")
    args = ap.parse_args()
    ev = pd.read_csv(BACKEND / args.events)
    ex = {json.loads(x)["accession"]: json.loads(x) for x in (BACKEND / args.extract).read_text().splitlines()}
    xb = pd.read_csv(BACKEND / args.xbrl)
    by_cik = {int(c): g for c, g in xb.groupby("cik")}
    p = Prices.from_long(pd.read_parquet(BACKEND / args.prices))
    days = pd.DatetimeIndex(p.open.index)
    rows = []
    for r in ev.itertuples():
        e = ex.get(r.accession)
        etf = SECTOR_ETF.get(str(r.sector))
        t = str(r.ticker).replace(".", "-")
        i = entry_index(days, datetime.fromisoformat(str(r.accepted_utc)))
        if e is None or etf is None or i is None or t not in p.close.columns:
            continue
        tool = sec_tool(by_cik.get(int(r.cik), pd.DataFrame(columns=xb.columns)), str(r.accepted_utc))
        filled = json.loads(json.dumps(e))  # the reader's record with the SEC tool's year-earlier numbers added
        notes = []
        for key, src in (("eps", "eps"), ("revenue", "rev")):
            d = filled.get(key) or {}
            if "q" in d and "prior" not in d and tool["prior"] and pd.notna(tool["prior"][src]):
                d["prior"] = float(tool["prior"][src])
                filled[key] = d
                notes.append(f"{'diluted EPS' if key == 'eps' else 'revenue'} a year earlier from the SEC filing for "
                             f"the quarter ended {tool['prior']['end']}")
        sheet = event_text(r, filled, p, i, etf)
        extra = []
        if notes:
            extra.append("Year-earlier figures added from SEC filings: " + "; ".join(notes) + ".")
        if tool["history"]:
            extra.append("Earlier quarters as filed with the SEC (diluted EPS vs a year earlier):")
            extra += history_lines(tool["history"])
        eps, rev = filled.get("eps") or {}, filled.get("revenue") or {}
        rows.append({"accession": r.accession, "ticker": r.ticker, "cik": r.cik, "sector": r.sector,
                     "accepted_utc": r.accepted_utc, "entry": days[i].date().isoformat(),
                     "eps_q": eps.get("q"), "eps_prior": eps.get("prior"),
                     "eps_prior_source": "reader" if "prior" in (e.get("eps") or {}) else
                     ("sec" if "prior" in eps else None),
                     "rev_q": rev.get("q"), "rev_prior": rev.get("prior"),
                     "guidance": e.get("guidance"), "tone": e.get("tone"),
                     "sec_quarters": len(tool["history"]),
                     "fact_sheet": sheet + ("\n" + "\n".join(extra) if extra else "")})
    df = pd.DataFrame(rows)
    out = BACKEND / "results" / "events" / f"features_{args.name}.csv"
    df.to_csv(out, index=False)
    both = df[["eps_q", "eps_prior"]].notna().all(axis=1)
    print(f"{len(df)} releases -> {out.name}; EPS pairs: {int(both.sum())} "
          f"({int((df['eps_prior_source'] == 'sec').sum())} completed by the SEC tool); "
          f"with SEC history: {int((df['sec_quarters'] > 0).sum())}")


if __name__ == "__main__":
    main()
