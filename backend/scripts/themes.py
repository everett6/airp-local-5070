"""Theme track, forward-only (docs/PLAN_60_V2.md "Theme track", fixed 2026-09-28): investment directions by horizon,
with a market-risk (AI-bubble) gauge. A shadow: no money.

    python scripts/themes.py                  # make this month's cohort if due, score matured ones
    python scripts/themes.py --status
    python scripts/themes.py --cards          # print today's cards (no GPU, nothing written)

Once a month (the long-term picks' schedule: first weekday at or after 16:00 ET, catch-up within 7 days), code builds
one card per theme (app/portfolio/themes.py) plus the risk register (hyperscaler capex from SEC filings, market
concentration, semiconductor trend, high-yield spread and VIX from FRED). Bonsai rates the register (bubble_risk) and
each theme (theme_outlook 1-5), arguing bull and bear first. Picks: up to 2 medium themes (held 6 months) and 2 long
ones (held 12 months) rated 4+; AI-linked themes are left out when bubble_risk is high. A code-only momentum
baseline is recorded beside them. Problems print "LEARN ALERT: ..." and never fail the events job.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from llm_fields import FIELDS_RISK, FIELDS_TH, PROMPT_RISK, PROMPT_TH, ask, parse, theses, verify
from longterm_picks import due

from app.forward.ledger import Ledger
from app.forward.schedule import NY
from app.portfolio import themes as T
from app.sandbox.gpu_lock import gpu_priority

PORT = 11441


def prices(now: datetime) -> tuple[pd.DataFrame, pd.DataFrame]:
    import yfinance as yf  # type: ignore[import-untyped]
    df = yf.download(sorted(T.tickers()), start=(now - timedelta(days=900)).date().isoformat(),
                     end=now.astimezone(NY).date().isoformat(), auto_adjust=True, progress=False, threads=True)
    return df["Open"], df["Close"]


def fred(series: str) -> pd.Series:
    import httpx
    r = httpx.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    return pd.to_numeric(df.iloc[:, 1], errors="coerce").set_axis(pd.to_datetime(df.iloc[:, 0])).dropna()


def capex() -> tuple[float | None, float | None, float | None]:
    """Hyperscalers' summed capex (last 4 quarters and the 4 before them a year earlier) and operating cash flow."""
    import httpx

    from app.data_ingestion.edgar import sec_user_agent
    now = ago = ocf = 0.0
    with httpx.Client(headers={"User-Agent": sec_user_agent()}, timeout=30) as c:
        for cik in T.HYPERSCALER_CIKS.values():
            g = c.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json").json()["facts"]["us-gaap"]
            qs = [T.quarters(g[t]["units"]["USD"]) for t in T.CAPEX_TAGS if t in g]
            q = max(qs, key=lambda s: s.index.max() if len(s) else pd.Timestamp(0))
            o = T.quarters(g["NetCashProvidedByUsedInOperatingActivities"]["units"]["USD"])
            a, b, f = T.ttm(q), T.ttm(q, 4), T.ttm(o)
            if a is None or b is None or f is None:
                return None, None, None
            now, ago, ocf = now + a, ago + b, ocf + f
    return now, ago, ocf


def build(now: datetime, closes: pd.DataFrame) -> tuple[str, list[dict[str, Any]]]:
    """The risk register's text and one card per theme, from data dated before today."""
    px = closes[closes.index < pd.Timestamp(now.astimezone(NY).date())]
    spy = px["SPY"]
    st = {th.key: T.stats(T.proxy_series(px, th), spy) for th in T.THEMES}
    rsp = T.stats(px["RSP"], spy)
    hy, vix = fred("BAMLH0A0HYM2"), fred("VIXCLS")
    hy = hy[hy.index < pd.Timestamp(now.astimezone(NY).date())]
    hy3 = float(hy.iloc[-1] - hy[hy.index <= hy.index[-1] - pd.Timedelta(days=91)].iloc[-1]) if len(hy) > 70 else None
    cx, ca, cf = capex()
    lines = T.risk_lines(cx, ca, cf, None if rsp["r12"] is None else -rsp["r12"], st["semis"],
                         float(hy.iloc[-1]) if len(hy) else None, hy3, float(vix.iloc[-1]) if len(vix) else None)
    cards = [{"key": th.key, "label": th.label, "horizon": th.horizon, "ai_linked": th.ai_linked,
              "mom": st[th.key]["mom"], "stats": st[th.key], "card": T.card(th, st[th.key], lines)}
             for th in T.THEMES]
    return "=== Market risk register ===\n" + "\n".join(lines), cards


async def rate(llm: Any, register: str, cards: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    async def risk() -> dict[str, Any]:
        reply, _ = await ask(llm, PROMPT_RISK, register)
        raw = parse(reply)
        v = verify(raw, register, FIELDS_RISK)
        return {"bubble_risk": v["bubble_risk"], "parsed": v["parsed"],
                "reason": str((raw or {}).get("reason", ""))[:300], **theses(raw, register)}

    async def one(c: dict[str, Any]) -> dict[str, Any]:
        reply, _ = await ask(llm, PROMPT_TH, c["card"])
        raw = parse(reply)
        v = verify(raw, c["card"], FIELDS_TH)
        return {k: c[k] for k in ("key", "label", "horizon", "ai_linked", "mom", "stats")} | {
            "rating": int(v["theme_outlook"]), "parsed": v["parsed"],
            "reason": str((raw or {}).get("reason", ""))[:300], **theses(raw, c["card"])}
    r, rated = await asyncio.gather(risk(), asyncio.gather(*(one(c) for c in cards)))
    return r, list(rated)


def status(recs: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"cohorts": sum(r.get("type") == "cohort" for r in recs)}
    for h in T.HOLD:
        res = [r for r in recs if r.get("type") == "result" and r["horizon"] == h]
        x = np.array([r["excess_net"] for r in res])
        lo80 = None
        if len(x) > 1:
            b = x[np.random.default_rng(0).integers(0, len(x), (5000, len(x)))].mean(1)
            lo80 = round(float(np.percentile(b, 20)), 5)
        out[h] = {"scored": len(res), "mean_excess_net": round(float(x.mean()), 5) if len(x) else None,
                  "lo80": lo80, "baseline_mean": round(float(np.mean([r["baseline_excess_net"] for r in res])), 5)
                  if res else None, "ready_to_judge": len(res) >= 12}
    last = next((r for r in reversed(recs) if r.get("type") == "cohort"), None)
    if last:
        out["latest"] = {"month": last["month"], "bubble_risk": last["bubble_risk"], "picks": last["picks"]}
    return out


def run(out: Path, now: datetime, use_gpu: bool) -> None:
    from forward_events import Ollama, wait_gpu_free

    from app.sandbox.walkforward import OllamaLLM
    out.mkdir(parents=True, exist_ok=True)
    led = Ledger(out / "ledger.jsonl")
    recs = led.verify() if (out / "ledger.jsonl").exists() else []
    make = due(recs, now)
    if not make and not any(r.get("type") == "cohort" for r in recs):
        print("themes: no cohort due yet")
        return
    opens, closes = prices(now)
    for r in T.score(recs, opens):
        led.append("result", **r)
        print(f"themes: {r['horizon']} cohort {r['month']} closed, {r['excess_net']:+.2%} vs SPY after costs "
              f"(momentum baseline {r['baseline_excess_net']:+.2%})")
    if not make:
        return
    if not use_gpu:
        print("LEARN ALERT: themes: a cohort is due but the GPU is off; it will be made next run")
        return
    register, cards = build(now, closes)
    with gpu_priority("themes"):
        if not wait_gpu_free(900):
            print("LEARN ALERT: themes: the GPU stayed busy; the cohort will be made next run")
            return
        srv = Ollama(PORT, str(Path.home() / ".ollama" / "models"), 3, out / "ollama.log")
        try:
            llm = OllamaLLM("bonsai-27b:latest", base_url=f"http://127.0.0.1:{PORT}", concurrency=3, num_ctx=8192,
                            num_predict=1000, cache=False, require_gpu=True)

            async def go() -> tuple[dict[str, Any], list[dict[str, Any]]]:
                try:
                    return await rate(llm, register, cards)
                finally:
                    await llm.unload()
            risk, rated = asyncio.run(go())
        finally:
            srv.stop()
    month = now.astimezone(NY).strftime("%Y-%m")
    (out / f"ratings_{month}.json").write_text(json.dumps({"register": register, "risk": risk, "themes": rated},
                                                          indent=1) + "\n")
    picks = {h: T.choose(rated, h, risk["bubble_risk"]) for h in T.HOLD}
    base = {h: T.momentum_baseline(rated, h) for h in T.HOLD}
    led.append("cohort", month=month, made_on=now.astimezone(NY).date().isoformat(),
               made_at=now.isoformat(timespec="seconds"), bubble_risk=risk["bubble_risk"], picks=picks, baseline=base,
               ratings={r["key"]: r["rating"] for r in rated})
    print(f"themes: cohort {month}, AI-bubble risk {risk['bubble_risk']}; medium {picks['medium'] or 'none'}, "
          f"long {picks['long'] or 'none'} (momentum baseline {base['medium']} / {base['long']})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="results/forward/themes")
    ap.add_argument("--no-gpu", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--cards", action="store_true")
    a = ap.parse_args()
    out = BACKEND / a.dir
    if a.cards:
        register, cards = build(datetime.now(UTC), prices(datetime.now(UTC))[1])
        print(register, *(c["card"].split("\n\n")[0] for c in cards), sep="\n\n")
        return
    try:
        if not a.status:
            run(out, datetime.now(UTC), not a.no_gpu)
        recs = Ledger(out / "ledger.jsonl").verify() if (out / "ledger.jsonl").exists() else []
        print("themes (shadow):", json.dumps(status(recs)))
    except Exception as e:  # noqa: BLE001 - a shadow: never fail the events job
        print(f"LEARN ALERT: themes failed: {type(e).__name__}: {e}"[:300])


if __name__ == "__main__":
    main()
