"""Stage 2: a decision model (e.g. Bonsai 27B) reads each research brief and decides BUY or PASS.

    # stage 1 (research agent, qwen3:8b + date-limited web tools) writes source-checked briefs:
    python scripts/llm_web_backtest.py --brief --rounds 3 --out analyst
    # stage 2 (decision model, no tools, no web):
    python scripts/decide.py --model bonsai-27b:latest --base-url http://127.0.0.1:11435
    python scripts/llm_web_report.py --run decisions_bonsai-27b_latest

Following airp's rule "LLMs reason and write, code calculates":
  - the brief the model reads holds only facts that passed code checks (cited URL was actually fetched, every
    number appears in the fetched text; see agent_worker.verify_brief);
  - every price figure it reads is computed here from the point-in-time price file, never by a model;
  - the model writes the bull case, the bear case and BUY/PASS; its probability of beating the average S&P 500
    stock is read from token log-probabilities (unrounded log-odds), not from a number it writes;
  - the portfolio rule is code: buy only BUY stocks, the 10 with the highest log-odds; PASS on everything = cash.
Each decision is saved to results/decisions_<model>/<date>/<ticker>.json with the exact prompt it saw.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import pandas as pd

from app.sandbox.gpu_lock import gpu_job
from app.sandbox.longrun import Panel
from app.sandbox.walkforward import OllamaLLM

RESULTS = BACKEND / "results"

DECIDE_SYSTEM = """You are the portfolio manager. Decide whether to BUY {ticker} now ({as_of} UTC) and hold it for
{horizon} trading days, or PASS. You are choosing among about 20 candidate stocks each month; buy only those you
expect to beat the average S&P 500 stock over that period.

You get a research brief (facts checked against their sources) and price figures computed from market data.
Use only this information. Weigh the strongest case for and against.

Reply with ONLY one JSON object:
{{"bull": ["<reason to buy>", ...], "bear": ["<reason not to>", ...], "decision": "BUY" or "PASS",
  "reason": "<1-3 sentences>"}}"""

RANK_SYSTEM = ("Using only the brief and price figures below, will {ticker} do better (UP) or worse (DOWN) than the "
               "average S&P 500 stock over the next {horizon} trading days? Answer with exactly one word: UP or DOWN.")


def price_block(panel: Panel, ticker: str, d: pd.Timestamp) -> str:
    """Deterministic figures from closes on or before d (no model involved)."""
    c = panel.close[ticker].loc[:d].dropna()
    m = panel.close["SPY"].loc[:d].dropna()

    def ret(s: pd.Series, n: int) -> str:
        return f"{100 * (s.iloc[-1] / s.iloc[-1 - n] - 1):+.1f}%" if len(s) > n else "n/a"
    r = c.pct_change().dropna().tail(20)
    vol = f"{100 * r.std() * math.sqrt(252):.0f}%" if len(r) >= 10 else "n/a"
    hi = f"{100 * (c.iloc[-1] / c.tail(252).max() - 1):+.1f}%"
    mom = f"{100 * (c.iloc[-22] / c.iloc[-253] - 1):+.1f}%" if len(c) > 253 else "n/a"
    return (f"Price figures (computed from daily closes up to {c.index[-1].date()}):\n"
            f"- return: 5 days {ret(c, 5)}, 20 days {ret(c, 20)}, 60 days {ret(c, 60)}; 12-1 month momentum {mom}\n"
            f"- distance from 52-week high {hi}; 20-day volatility (annualized) {vol}\n"
            f"- S&P 500 (SPY) return: 20 days {ret(m, 20)}, 60 days {ret(m, 60)}")


def brief_text(brief: dict[str, Any]) -> str:
    lines = ["Research brief:"]
    facts = brief.get("facts") or []
    lines += [f"- {f['text']} [{f.get('date') or 'undated'}; {f['source']}]" for f in facts] or ["- (no verified facts)"]
    for key, label in (("catalysts", "Possible upside drivers (analyst judgement)"),
                       ("risks", "Possible downside drivers (analyst judgement)"),
                       ("missing", "Not found")):
        if brief.get(key):
            lines.append(f"{label}: " + "; ".join(brief[key]))
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> None:
    panel = Panel.load(pd.read_parquet(BACKEND / args.ohlcv), pd.read_csv(BACKEND / args.universe))
    src = sorted((RESULTS / args.briefs).glob("*/*.json"))
    recs = [json.loads(p.read_text()) for p in src]
    recs = [r for r in recs if isinstance(r.get("brief"), dict)]
    if not recs:
        raise SystemExit(f"no briefs in results/{args.briefs}; run: python scripts/llm_web_backtest.py --brief "
                         f"--out {args.briefs}")
    slug = args.model.replace(":", "_").replace("/", "_")
    out_dir = RESULTS / f"decisions_{slug}"
    llm = OllamaLLM(args.model, base_url=args.base_url, concurrency=1, num_ctx=8192, num_predict=500,
                    cache=True, require_gpu=True)
    t0 = time.monotonic()
    todo = [r for r in recs if not (out_dir / r["as_of"][:10] / f"{r['ticker']}.json").exists()]
    print(f"{len(recs)} briefs, {len(recs) - len(todo)} already decided, {len(todo)} to go with {args.model}",
          flush=True)
    for n, r in enumerate(todo, start=1):
        d = pd.Timestamp(r["as_of"][:10])
        t = r["ticker"]
        user = f"{price_block(panel, t, d)}\n\n{brief_text(r['brief'])}"
        fmt = {"ticker": t, "as_of": r["as_of"], "horizon": r.get("horizon_days", 20)}
        raw = await llm(DECIDE_SYSTEM.format(**fmt), user)
        try:
            dec = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        except ValueError:
            dec = {}
        choice = str(dec.get("decision", "")).strip().upper()
        lo = json.loads(await llm(RANK_SYSTEM.format(**fmt), user, mode="updown_lo"))
        rec = {"ticker": t, "as_of": r["as_of"], "horizon_days": fmt["horizon"], "window": r.get("window"),
               "screen_score": r.get("screen_score"), "model": args.model,
               "decision": choice if choice in ("BUY", "PASS") else "INVALID", "buy": choice == "BUY",
               "bull": dec.get("bull", []), "bear": dec.get("bear", []), "reason": str(dec.get("reason", ""))[:1000],
               "logodds": lo["logodds"], "logodds_censored": lo["censored"], "logprob_mass": lo["mass"],
               "p_up_logprob": lo["p_up"], "brief_facts": len(r["brief"].get("facts", [])),
               "brief_dropped": r["brief"].get("dropped"), "prompt_user": user, "research_record": str(
                   (RESULTS / args.briefs / r["as_of"][:10] / f"{t}.json").relative_to(BACKEND))}
        path = out_dir / r["as_of"][:10] / f"{t}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rec, indent=1, default=str) + "\n")
        rate = (time.monotonic() - t0) / n
        print(f"[{n}/{len(todo)}] {d.date()} {t:6s} {rec['decision']:7s} logodds={lo['logodds']:+.2f} "
              f"facts={rec['brief_facts']} {rate:.1f}s/decision eta={(len(todo) - n) * rate / 60:.0f}min", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="bonsai-27b:latest")
    ap.add_argument("--base-url", default=None, help="Ollama server serving --model")
    ap.add_argument("--briefs", default="analyst", help="results sub-folder written by llm_web_backtest.py --brief")
    ap.add_argument("--universe", default="data/hist/universe_2010_2026_top100.csv")
    ap.add_argument("--ohlcv", default="data/hist/ohlcv_2010_2026_top100.parquet")
    args = ap.parse_args()
    with gpu_job(f"decide {args.model}"):
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
