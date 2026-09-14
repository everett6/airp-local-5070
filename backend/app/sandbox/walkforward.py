"""
Walk-forward simulation: how accurate is the local LLM agent, honestly?

For each weekly cutoff date c in the test window, and each ticker:
  1. the orchestrator takes `PriceTable.view(c)` (rows <= c only) and sends
     an anonymized series to the jailed agent,
  2. the agent returns P(price higher in `horizon` trading days),
  3. later — only once the simulated calendar has passed the resolution
     date — the outcome is revealed to the agent's memory.

Leakage controls (see docs/WALKFORWARD_5070.md for the research behind them):
  - data:     PointInTimeView + process jail (no files, no network)
  - memory:   a resolved record enters memory at cutoff c only if its
              resolution date <= c, checked by `enforce_point_in_time` inside
              `sandbox_scope(c)` — a violation aborts the run
  - model:    tickers and dates anonymized; the test window starts after the
              model's training cutoff, which `--probe-memorization` measures
              empirically instead of trusting a model card
  - every run: live jail probe, recorded in the results file

Arms, all scored on the identical (cutoff, ticker) grid:
  llm_plain         LLM only, no memory
  llm_selfimprove   LLM + own track record + self-written lessons + stacker
  feat_logit        the same walk-forward logistic stacker WITHOUT the LLM
                    (tells us whether the LLM adds anything at all)
  always_up, momentum_20d, reversal_5d   classic baselines
  base_rate         point-in-time up-rate of resolved outcomes (climatology)
  selector          per cutoff, the arm with the best rolling resolved Brier

Usage:
  python -m app.sandbox.walkforward --start 2025-06-01 --tag v1
  python -m app.sandbox.walkforward --probe-memorization
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from app.sandbox import agent_worker as aw
from app.sandbox import provenance as prov
from app.sandbox.clock import enforce_point_in_time, sandbox_scope
from app.sandbox.jail import AgentJail
from app.sandbox.pit_data import PriceTable

BACKEND = Path(__file__).resolve().parents[2]
DATA = BACKEND / "data" / "prices.csv"
RESULTS = BACKEND / "results"
MARKET = "SPY"
LOOKBACK = 120


class OllamaLLM:
    """Native /api/chat so we can disable qwen3's thinking tokens (5-10x
    faster, and thinking did not change 5-day direction calls in pilots).
    Responses are cached on disk by prompt hash, so re-running a suite after
    changing only the scoring/stacker costs no GPU time."""

    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", concurrency: int = 4,
                 num_ctx: int = 4096, cache: bool = True, num_predict: int = 300) -> None:
        self.model = model
        self.num_predict = num_predict
        self.num_ctx = num_ctx
        self.use_cache = cache
        self.base_url = base_url
        self._sem = asyncio.Semaphore(concurrency)
        self._client = httpx.AsyncClient(timeout=300)
        RESULTS.mkdir(exist_ok=True)
        self._cache_path = RESULTS / f"llm_cache_{model.replace(':', '_').replace('/', '_')}.jsonl"
        self._cache: dict[str, str] = {}
        if self._cache_path.exists():
            for line in self._cache_path.open():
                rec = json.loads(line)
                self._cache[rec["k"]] = rec["v"]
        self.calls = 0
        self.cache_hits = 0

    async def __call__(self, system: str, user: str) -> str:
        ctx = "" if self.num_ctx == 4096 else f"\0ctx={self.num_ctx}"  # keeps existing cache keys valid
        key = hashlib.sha256(f"{self.model}\0{system}\0{user}{ctx}".encode()).hexdigest()
        if self.use_cache and key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        body = {
            "model": self.model, "stream": False, "think": False, "format": "json",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": 0, "num_ctx": self.num_ctx, "num_predict": self.num_predict},
        }
        async with self._sem:
            for attempt in range(3):
                try:
                    r = await self._client.post(f"{self.base_url}/api/chat", json=body)
                    r.raise_for_status()
                    text: str = r.json()["message"]["content"]
                    break
                except (httpx.HTTPError, KeyError):
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2)
        self.calls += 1
        if self.use_cache:
            self._cache[key] = text
            with self._cache_path.open("a") as f:
                f.write(json.dumps({"k": key, "v": text}) + "\n")
        return text


@dataclass
class ArmState:
    name: str
    preds: list[dict[str, Any]] = field(default_factory=list)  # all predictions (resolved or not)
    lessons: list[str] = field(default_factory=list)
    lesson_log: list[dict[str, Any]] = field(default_factory=list)


def _dt(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def resolved_as_of(arm: ArmState, cutoff: date) -> list[dict[str, Any]]:
    """Point-in-time memory: only predictions whose outcome date <= cutoff.
    Must be called inside sandbox_scope(cutoff); every record is re-checked
    against the active clock so a bug here aborts instead of leaking."""
    out = []
    for p in arm.preds:
        if p["resolve_date"] <= cutoff:
            enforce_point_in_time(_dt(p["resolve_date"]), source=f"memory:{arm.name}")
            out.append(p)
    return out


def track_record(recs: list[dict[str, Any]], key: str = "p_llm") -> dict[str, float]:
    if not recs:
        return {"n": 0}
    n = len(recs)
    return {
        "n": n,
        "hit_rate": sum((r[key] >= 0.5) == r["up"] for r in recs) / n,
        "brier": sum((r[key] - r["up"]) ** 2 for r in recs) / n,
        "base_rate": sum(r["up"] for r in recs) / n,
        "avg_p": sum(r[key] for r in recs) / n,
    }


def score(preds: list[dict[str, Any]], key: str) -> dict[str, Any]:
    n = len(preds)
    hits = sum((p[key] >= 0.5) == p["up"] for p in preds)
    acc = hits / n
    brier = sum((p[key] - p["up"]) ** 2 for p in preds) / n
    ll = -sum(math.log(p[key] if p["up"] else 1 - p[key]) for p in preds if 0 < p[key] < 1) / n
    # long/short each name by the sign of its call, equal weight per cutoff
    by_cut: dict[date, list[float]] = {}
    for p in preds:
        if p[key] != 0.5:
            by_cut.setdefault(p["cutoff"], []).append(math.copysign(1, p[key] - 0.5) * p["ret"])
    rets = [sum(v) / len(v) for v in by_cut.values()]
    mean = sum(rets) / len(rets) if rets else 0.0
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) if len(rets) > 1 else 0.0
    z = (hits - n / 2) / math.sqrt(n / 4)
    return {
        "n": n, "accuracy": round(acc, 4), "pct_up_calls": round(sum(p[key] >= 0.5 for p in preds) / n, 3),
        "brier": round(brier, 4), "log_loss": round(ll, 4),
        "z_vs_coinflip": round(z, 2),
        "ls_mean_weekly_ret_pct": round(mean * 100, 3),
        "ls_sharpe_ann": round(mean / sd * math.sqrt(52), 2) if sd else 0.0,
    }


def point_in_time_meta_arms(
    arms: dict[str, list[dict[str, Any]]], cutoffs: list[date], window: int = 400,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Two arms built only from outcomes already resolved at each cutoff:
      base_rate: p = fraction of resolved moves that were up (climatology)
      selector:  at each cutoff, use whichever candidate arm has the lowest
                 Brier over its last `window` resolved predictions — the
                 system-level self-improvement: it can learn that the LLM
                 has no edge and fall back to something that does.
    Every record used is re-checked against the sandbox clock."""
    ref = arms["llm_plain"]
    base_rate: list[dict[str, Any]] = []
    selector: list[dict[str, Any]] = []
    choices: list[dict[str, Any]] = []
    for c in cutoffs:
        with sandbox_scope(as_of=_dt(c), run_id=f"meta-{c}"):
            resolved_ref = [p for p in ref if p["resolve_date"] <= c]
            for p in resolved_ref:
                enforce_point_in_time(_dt(p["resolve_date"]), source="meta:base_rate")
            rate = (sum(p["up"] for p in resolved_ref) / len(resolved_ref)) if resolved_ref else 0.5
            rate = min(max(rate, 0.05), 0.95)
            briers = {}
            for name, ps in arms.items():
                hist = [p for p in ps if p["resolve_date"] <= c][-window:]
                if len(hist) >= 100:
                    briers[name] = sum((p["p"] - p["up"]) ** 2 for p in hist) / len(hist)
            chosen = min(briers, key=lambda k: briers[k]) if briers else "base_rate"
        choices.append({"cutoff": c.isoformat(), "chosen": chosen,
                        "rolling_brier": {k: round(v, 4) for k, v in briers.items()}})
        idx = {name: {p["ticker"]: p for p in ps if p["cutoff"] == c} for name, ps in arms.items()}
        for p in ref:
            if p["cutoff"] != c:
                continue
            base_rate.append({**p, "p": rate})
            src_p = rate if chosen == "base_rate" else idx[chosen][p["ticker"]]["p"]
            selector.append({**p, "p": src_p, "chosen": chosen})
    return base_rate, selector, choices


async def run(args: argparse.Namespace) -> dict[str, Any]:
    table = PriceTable.from_csv(DATA)
    tickers = [t for t in table.tickers if t != MARKET]
    llm = OllamaLLM(args.model, concurrency=args.concurrency)

    i0 = table.index_on_or_before(date.fromisoformat(args.start))
    last = len(table.dates) - 1 - args.horizon
    if args.end:
        last = min(last, table.index_on_or_before(date.fromisoformat(args.end)))
    cut_idx = list(range(i0, last + 1, args.step))
    cutoffs = [table.dates[i] for i in cut_idx]
    if not cutoffs:
        raise SystemExit("no cutoffs in the requested window")
    # the resolved window is what gets hashed, so '--end' omitted vs pinned to the same date match
    config = {"model": args.model, "start": cutoffs[0].isoformat(), "end": cutoffs[-1].isoformat(),
              "horizon": args.horizon, "step": args.step, "warmup": args.warmup,
              "reflect_every": args.reflect_every, "target": args.target}
    cfg_hash = prov.config_hash(config)
    provenance = prov.collect(BACKEND.parent, DATA, args.model)
    prov.check_overwrite(RESULTS / f"walkforward_{args.tag}.json", cfg_hash, force=getattr(args, "force", False),
                         data_sha256=provenance["data"]["sha256"])

    plain, selfimp, featlr = ArmState("llm_plain"), ArmState("llm_selfimprove"), ArmState("feat_logit")
    stacker_log: list[dict[str, Any]] = []
    t_start = time.time()
    async with AgentJail(llm) as jail_plain, AgentJail(llm) as jail_self:
        probe = await jail_self.probe([str(DATA), str(Path.home() / ".bashrc"), str(BACKEND / "app")])
        if not probe["passed"]:
            raise SystemExit(f"jail probe FAILED, refusing to run: {probe}")

        for ci, cutoff in enumerate(cutoffs):
            with sandbox_scope(as_of=_dt(cutoff), run_id=f"wf-{args.tag}-{cutoff}"):
                view = table.view(cutoff)  # rows <= cutoff, copies
                items: list[dict[str, Any]] = []
                for t in tickers:
                    anon = view.anonymize(t, MARKET, LOOKBACK)
                    items.append({"id": t, **anon})
                # the ids are tickers only on the orchestrator side of the pipe:
                id_map = {f"a{k}": it for k, it in enumerate(items)}
                sent = [{"id": k, "asset": it["asset"], "market": it["market"]} for k, it in id_map.items()]

                mem_recs = resolved_as_of(selfimp, cutoff)
                if args.reflect_every and ci % args.reflect_every == 0 and len(mem_recs) >= 40:
                    refl = await jail_self.call({
                        "task": "reflect",
                        "records": [{"p_llm": r["p_llm"], "features": r["features"], "up": r["up"]}
                                    for r in mem_recs[-40:]], "previous_lessons": selfimp.lessons,
                    })
                    selfimp.lessons = refl["lessons"]
                    selfimp.lesson_log.append({"cutoff": cutoff.isoformat(), "lessons": selfimp.lessons})
                memory = {
                    "track_record": track_record(mem_recs), "lessons": selfimp.lessons,
                    "resolved": [{"p_llm": r["p_llm"], "features": r["features"], "up": r["up"]} for r in mem_recs],
                }
                res_plain, res_self = await asyncio.gather(
                    jail_plain.call({"task": "predict", "items": sent, "target": args.target}),
                    jail_self.call({"task": "predict", "items": sent, "memory": memory,
                                    "target": args.target}),
                )
                # feature-only stacker, same point-in-time rule, no LLM
                feat_recs = resolved_as_of(featlr, cutoff)
                w = None
                if len(feat_recs) >= 200:
                    w = aw.fit_logistic([aw._row(0.5, r["features"]) for r in feat_recs],
                                        [int(r["up"]) for r in feat_recs])

            # ---- outside the sandbox: attach outcomes (used only once resolved) ----
            p_by_id = {p["id"]: p for p in res_plain["predictions"]}
            for sp in res_self["predictions"]:
                ticker: str = id_map[sp["id"]]["id"]
                resolve_date, ret = table.outcome(ticker, cutoff, args.horizon)
                if args.target == "excess":
                    ret -= table.outcome(MARKET, cutoff, args.horizon)[1]
                pp = p_by_id[sp["id"]]
                f = sp["features"]
                base = {"cutoff": cutoff, "ticker": ticker, "resolve_date": resolve_date, "ret": ret,
                        "up": ret > 0, "features": f}
                plain.preds.append({**base, "p_llm": pp["p_llm"], "p": pp["p_llm"]})
                selfimp.preds.append({**base, "p_llm": sp["p_llm"], "p": sp["p_final"],
                                      "stacker": res_self["stacker_active"]})
                featlr.preds.append({**base, "p_llm": 0.5,
                                     "p": aw.predict_logistic(w, aw._row(0.5, f)) if w else 0.5})
            stacker_log.append({"cutoff": cutoff.isoformat(), **res_self.get("stacker_info", {})})
            if ci % 5 == 0 or ci == len(cutoffs) - 1:
                tr = track_record(selfimp.preds, "p")
                print(f"[{ci + 1}/{len(cutoffs)}] {cutoff} llm_calls={llm.calls} cache={llm.cache_hits} "
                      f"self_hit={tr.get('hit_rate', 0):.3f} elapsed={time.time() - t_start:.0f}s", flush=True)

        probe_end = await jail_plain.probe([str(DATA)])

    base_rows = selfimp.preds
    arms: dict[str, list[dict[str, Any]]] = {
        "llm_plain": plain.preds,
        "llm_selfimprove": selfimp.preds,
        "feat_logit": featlr.preds,
        "always_up": [{**p, "p": 0.51} for p in base_rows],
        "momentum_20d": [{**p, "p": 0.51 if p["features"]["ret_20d"] > 0 else 0.49} for p in base_rows],
        "reversal_5d": [{**p, "p": 0.49 if p["features"]["ret_5d"] > 0 else 0.51} for p in base_rows],
    }
    arms["base_rate"], arms["selector"], selector_log = point_in_time_meta_arms(
        {k: v for k, v in arms.items() if k != "always_up"}, cutoffs,
    )
    # Score only on cutoffs where every learning arm had a chance to learn
    # (after warm-up) as well as on the full window, so warm-up can't flatter or hide anything.
    warm = cutoffs[min(len(cutoffs) - 1, args.warmup)]
    report: dict[str, Any] = {
        "tag": args.tag, "config": config, "config_hash": cfg_hash, "provenance": provenance,
        "jail_limits": jail_plain.limits.as_dict(),
        "model": args.model, "target": args.target, "horizon_days": args.horizon, "step_days": args.step,
        "window": [cutoffs[0].isoformat(), cutoffs[-1].isoformat()], "n_cutoffs": len(cutoffs),
        "tickers": tickers, "jail_probe_start": probe, "jail_probe_end": probe_end,
        "llm_calls": llm.calls, "cache_hits": llm.cache_hits, "runtime_s": round(time.time() - t_start),
        "scores_full": {k: score(v, "p") for k, v in arms.items()},
        "scores_after_warmup": {k: score([p for p in v if p["cutoff"] >= warm], "p") for k, v in arms.items()},
        "warmup_from": warm.isoformat(),
        "lessons": selfimp.lesson_log,
        "stacker_log": stacker_log,
        "selector_log": selector_log,
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"walkforward_{args.tag}.json").write_text(json.dumps(report, indent=2, default=str))
    rows = [{k: (v.isoformat() if isinstance(v, date) else v) for k, v in p.items() if k != "features"}
            for arm, ps in arms.items() for p in ({**q, "arm": arm} for q in ps)]
    (RESULTS / f"walkforward_{args.tag}_predictions.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    return report


async def probe_memorization(model: str) -> dict[str, Any]:
    """Lopez-Lira et al. (2025) / Gao et al. (2025, arXiv:2512.23847): ask for
    exact historical closes. Low error = memorized = inside training data =
    unusable for a clean backtest. Error jumping up marks the real cutoff."""
    table = PriceTable.from_csv(DATA)
    llm = OllamaLLM(model)
    sys_p = 'Answer from memory. Reply JSON only: {"price": <number>}'
    months = sorted({(d.year, d.month) for d in table.dates})
    out: dict[str, list[float]] = {}
    tasks = []
    for (y, m) in months:
        d = date(y, m, 15)
        i = table.index_on_or_before(d)
        for t in ("AAPL", "MSFT", "NVDA", "KO", "JPM"):
            q = f"What was the closing price of {t} stock on {table.dates[i].isoformat()} (split-adjusted)?"
            tasks.append((f"{y}-{m:02d}", table.closes[t][i], llm(sys_p, q)))
    answers = await asyncio.gather(*(t[2] for t in tasks))
    for (label, truth, _), ans in zip(tasks, answers, strict=True):
        try:
            p = float(json.loads(ans[ans.index("{"): ans.rindex("}") + 1])["price"])
            err = abs(p / truth - 1)
        except (ValueError, KeyError, TypeError, ZeroDivisionError):
            err = 1.0
        out.setdefault(label, []).append(err)
    med = {k: round(sorted(v)[len(v) // 2] * 100, 1) for k, v in out.items()}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"memorization_probe_{model.replace(':', '_')}.json").write_text(json.dumps(med, indent=2))
    return med


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=None,
                    help="TOML file of run parameters (see backend/configs); explicit flags override it")
    ap.add_argument("--force", action="store_true", help="overwrite a finished run made with a different config")
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--start", default="2025-06-02")
    ap.add_argument("--end", default=None)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=12, help="cutoffs before 'after warm-up' scoring")
    ap.add_argument("--reflect-every", type=int, default=4)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--tag", default="run")
    ap.add_argument("--target", choices=["abs", "excess"], default="abs",
                    help="abs: will the price rise? excess: will it beat the market (SPY)?")
    ap.add_argument("--probe-memorization", action="store_true")
    return ap


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = build_parser()
    pre, _ = ap.parse_known_args(argv)
    if pre.config is not None:
        cfg = prov.load_config_file(pre.config)
        ap.set_defaults(**{k: v for k, v in cfg.items() if k != "description"})
    return ap.parse_args(argv)


def main() -> None:
    args = parse_args()
    if args.probe_memorization:
        print(json.dumps(asyncio.run(probe_memorization(args.model)), indent=1))
        return
    rep = asyncio.run(run(args))
    print(json.dumps({k: rep[k] for k in ("window", "n_cutoffs", "llm_calls", "runtime_s")}))
    for section in ("scores_full", "scores_after_warmup"):
        print(section)
        for arm, s in rep[section].items():
            print(f"  {arm:16s} {s}")


if __name__ == "__main__":
    main()
