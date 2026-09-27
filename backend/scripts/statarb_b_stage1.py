"""Arm B, stage 1: drop stat-arb candidates whose company filed an 8-K in the signal window (free EDGAR pre-screen).

    python scripts/statarb_b_stage1.py

Spec (docs/PLAN_STATARB.md, "Arm B, stage 1"): a candidate formed at close t is dropped if an 8-K or 8-K/A was
accepted on any trading day t-4..t; the side's weight is re-split among the rest (no replacement). Gross edge = annualized
0-bps mean return / average gross, over 2024-06-01 .. 2026-09, for the filtered book and arm A. Continue to stage 2
only if the filtered edge is > 0 and >= 2x arm A's. 8-K dates are cached in data/statarb/8k_dates.json.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd
import statarb_sleeve as S

from app.sandbox.dsr import register

CACHE = BACKEND / "data" / "statarb" / "8k_dates.json"
START, END = "2024-06-01", "2026-09-30"


def user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        for line in (BACKEND / ".env").read_text().splitlines():
            if line.startswith("SEC_USER_AGENT="):
                ua = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not ua:
        raise SystemExit("SEC_USER_AGENT missing in backend/.env")
    return ua


def get_json(url: str, ua: str) -> dict:
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=30) as f:
                return json.loads(f.read().decode())
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def eightk_dates(tickers: list[str]) -> dict[str, list[str]]:
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    todo = [t for t in tickers if t not in cache]
    if todo:
        ua = user_agent()
        cik = {v["ticker"].upper().replace(".", "-"): int(v["cik_str"])
               for v in get_json("https://www.sec.gov/files/company_tickers.json", ua).values()}
        for t in todo:
            if t not in cik:
                cache[t] = None  # no current CIK (e.g. delisted): unknown, kept out of the filter
                continue
            sub = get_json(f"https://data.sec.gov/submissions/CIK{cik[t]:010d}.json", ua)
            blocks = [sub["filings"]["recent"]]
            oldest = min(blocks[0]["filingDate"]) if blocks[0]["filingDate"] else "9999"
            for extra in sub["filings"].get("files", []):
                if oldest <= "2024-05-01":
                    break
                b = get_json(f"https://data.sec.gov/submissions/{extra['name']}", ua)
                blocks.append(b)
                oldest = min(oldest, min(b["filingDate"]))
                time.sleep(0.15)
            days = sorted({a[:10] for b in blocks for f, a in zip(b["form"], b["acceptanceDateTime"], strict=True)
                           if f in ("8-K", "8-K/A") and a[:10] >= "2024-05-01"})
            cache[t] = days
            time.sleep(0.15)  # SEC fair-access: well under 10 requests a second
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache))
    return cache


def gross_edge(ret: pd.Series, gross: pd.Series) -> float:
    r, g = ret.loc[START:END], gross.loc[START:END]
    return float(r.mean() * 252 / g.mean())


def main() -> None:
    a_ret, _, a_gross, cands = S.build("2026-09-26", 0.0)
    c = cands[(cands["date"] >= START) & (cands["date"] <= END)]
    dates = eightk_dates(sorted(c["ticker"].unique()))
    days = a_ret.index
    pos = {d.date().isoformat(): i for i, d in enumerate(days)}
    drop, unknown = set(), 0
    for row in c.itertuples():
        filed = dates.get(row.ticker)
        if filed is None:
            unknown += 1
            continue
        i = pos.get(row.date)
        if i is None:
            continue
        window = {days[j].date().isoformat() for j in range(max(0, i - 4), i + 1)}
        if window & set(filed):
            drop.add((row.date, row.ticker))
    b_ret, _, b_gross, _ = S.build("2026-09-26", 0.0, drop=drop)
    ea, eb = gross_edge(a_ret, a_gross), gross_edge(b_ret, b_gross)
    ok = eb > 0 and eb >= 2 * ea
    out = {"window": [START, END], "candidates": len(c), "dropped_8k": len(drop),
           "dropped_pct": round(100 * len(drop) / len(c), 1), "no_cik": unknown,
           "gross_edge_A_pct": round(100 * ea, 2), "gross_edge_B1_pct": round(100 * eb, 2),
           "ratio": round(eb / ea, 2) if ea else None, "continue_to_stage2": ok,
           "B1_0bps": S.stats(b_ret.loc[START:END]), "A_0bps": S.stats(a_ret.loc[START:END])}
    (BACKEND / "results" / "statarb_b_stage1.json").write_text(json.dumps(out, indent=1))
    register({"trial": "statarb_arm_b_stage1_8k_filter", "date": time.strftime("%Y-%m-%d"), "kind": "sleeve",
              "sharpe_ann": out["B1_0bps"]["sharpe"], "window": "2024-06..2026-09 (0 bps)",
              "result": "continue" if ok else "stop"})
    print(f"{out['candidates']} candidates, {out['dropped_8k']} dropped for an 8-K ({out['dropped_pct']}%), "
          f"{unknown} without a CIK")
    print(f"gross edge per unit gross: A {out['gross_edge_A_pct']}%/yr  B1 {out['gross_edge_B1_pct']}%/yr  "
          f"ratio {out['ratio']}")
    print(f"0 bps Sharpe: A {out['A_0bps']['sharpe']}  B1 {out['B1_0bps']['sharpe']}")
    print("-> continue to stage 2" if ok else "-> B stops here (stage 1 rule)")


if __name__ == "__main__":
    main()
