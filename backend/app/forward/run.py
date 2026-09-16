"""
Pre-registered forward test: weekly decisions logged before their outcomes exist.

    python -m app.forward.run              # do whatever is due now (idempotent; safe to run hourly)
    python -m app.forward.run --dry-run    # show what is due, change nothing
    python -m app.forward.run --status     # verify the ledger and print the scoreboard

Each completed trading week (cutoff = the week's last close) gets, before the
next market open:
  live_plain  the frozen backtest agent (anonymized prices only, same prompt as llm_plain)
  live_web    the web-informed research agent (app/live/research.py)
for every stock in the frozen universe. A week that can't be decided before the
deadline is logged as `missed` and never backfilled. Once the horizon has
passed, `outcome` records are appended. Everything goes into a hash-chained
ledger (results/forward/<tag>.jsonl).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import fcntl
import hashlib
import json
import tomllib
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.forward.ledger import Ledger
from app.forward.schedule import NY, deadline, resolve_day, weekly_cutoffs
from app.sandbox import provenance as prov
from app.sandbox.jail import AgentJail
from app.sandbox.pit_data import PriceTable
from app.sandbox.walkforward import BACKEND, OllamaLLM
from app.tools.gateway import DEFAULT_UA
from app.tools.netguard import FetchError, SafeFetcher

MARKET = "SPY"
Bars = dict[str, tuple[list[date], list[float]]]
BarsFn = Callable[[list[str]], Awaitable[Bars]]
ResearchFn = Callable[..., Awaitable[list[dict[str, Any]]]]


def load_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        cfg = tomllib.load(f)
    cfg["config_hash"] = hashlib.sha256(json.dumps({k: v for k, v in cfg.items() if k != "description"},
                                                   sort_keys=True).encode()).hexdigest()[:16]
    return cfg


def load_universe(cfg: dict[str, Any]) -> list[str]:
    with (BACKEND / cfg["universe"]).open() as f:
        return [row["ticker"] for row in csv.DictReader(f)]


async def yahoo_bars(tickers: list[str], attempts: int = 4, backoff_s: float = 2.0) -> Bars:
    fetcher = SafeFetcher(DEFAULT_UA)
    sem = asyncio.Semaphore(4)

    async def get(url: str) -> str:
        # transient DNS/HTTP failures must not cost a forward-test week; the last error is raised
        for i in range(attempts):
            try:
                async with sem:
                    r = await fetcher.fetch(url, use_cache=False)
                if r.status != 200:
                    raise FetchError(f"HTTP {r.status} for {url}")
                return r.text
            except FetchError:
                if i == attempts - 1:
                    raise
                await asyncio.sleep(backoff_s * 2 ** i)
        raise AssertionError("unreachable")

    try:
        async def one(t: str) -> tuple[str, tuple[list[date], list[float]]]:
            text = await get(f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?range=1y&interval=1d")
            res = json.loads(text)["chart"]["result"][0]
            adj = res["indicators"]["adjclose"][0]["adjclose"]
            pairs = [(datetime.fromtimestamp(ts, UTC).astimezone(NY).date(), c)
                     for ts, c in zip(res["timestamp"], adj, strict=False) if c is not None]
            return t, ([d for d, _ in pairs], [float(c) for _, c in pairs])
        return dict(await asyncio.gather(*(one(t) for t in tickers)))
    finally:
        await fetcher.aclose()


def table_from_bars(bars: Bars, tickers: list[str]) -> PriceTable:
    common = set(bars[MARKET][0])
    for t in tickers:
        common &= set(bars[t][0])
    dates = sorted(common)
    closes = {t: [dict(zip(*bars[t], strict=True))[d] for d in dates] for t in [*tickers, MARKET]}
    return PriceTable(dates, closes)


MIN_HISTORY = 61  # the price-only agent's longest feature is a 60-day return


async def decide_plain(table: PriceTable, cutoff: date, tickers: list[str], llm: Any, lookback: int,
                       allow_unjailed: bool = False) -> dict[str, float]:
    view = table.view(cutoff)
    if len(view.dates) < min(lookback, MIN_HISTORY):
        raise RuntimeError(f"only {len(view.dates)} days of shared price history before {cutoff}; "
                           f"need {min(lookback, MIN_HISTORY)}")
    items = [{"id": f"a{k}", **view.anonymize(t, MARKET, lookback)} for k, t in enumerate(tickers)]
    async with AgentJail(llm, allow_unjailed=allow_unjailed) as jail:
        res = await jail.call({"task": "predict", "items": items, "target": "abs"})
    by_id = {p["id"]: p["p_final"] for p in res["predictions"]}
    return {t: by_id[f"a{k}"] for k, t in enumerate(tickers)}


async def run_once(cfg: dict[str, Any], ledger: Ledger, *, now: datetime | None = None, dry_run: bool = False,
                   bars_fn: BarsFn = yahoo_bars, llm: Any = None, research_fn: ResearchFn | None = None,
                   allow_unjailed: bool = False, log: Callable[[str], None] = print) -> list[dict[str, Any]]:
    real_clock = now is None
    now = now or datetime.now(UTC)
    recs = ledger.verify()
    tickers = load_universe(cfg)
    bars = await bars_fn([*tickers, MARKET])
    start = date.fromisoformat(cfg["first_cutoff"])
    cutoffs = [c for c in weekly_cutoffs(bars[MARKET][0], now) if c >= start]
    handled = {r["cutoff"] for r in recs if r["type"] in ("decision", "missed")}
    written: list[dict[str, Any]] = []

    for c in cutoffs:
        if c.isoformat() in handled:
            continue
        dl = deadline(c)
        if c != cutoffs[-1] or now >= dl:
            log(f"{c}: MISSED (deadline {dl.isoformat()} passed without a decision)")
            if not dry_run:
                written.append(ledger.append("missed", cutoff=c.isoformat(), deadline=dl.isoformat(),
                                             config_hash=cfg["config_hash"]))
            continue
        log(f"{c}: decision due before {dl.isoformat()} for {len(tickers)} stocks")
        if dry_run:
            continue
        table = table_from_bars(bars, tickers)
        if table.dates[table.index_on_or_before(c)] != c:
            raise RuntimeError(f"price data does not contain the cutoff close {c}")
        arms: dict[str, dict[str, float]] = {}
        failed: dict[str, list[str]] = {}
        web_paths: dict[str, str] = {}
        model = cfg["model"]
        if "live_plain" in cfg["arms"]:
            arms["live_plain"] = await decide_plain(table, c, tickers,
                                                    llm or OllamaLLM(model, concurrency=4, cache=False, require_gpu=False),
                                                    cfg.get("lookback", 120), allow_unjailed)
            log(f"  live_plain done ({len(arms['live_plain'])} stocks)")
        if "live_web" in cfg["arms"]:
            if research_fn is None:
                from app.live.research import research_tickers
                research_fn = research_tickers
            web = await research_fn(tickers, model=model, horizon=cfg["horizon"], max_rounds=cfg["web_rounds"],
                                    concurrency=cfg.get("web_concurrency", 3))
            arms["live_web"] = {r["ticker"]: r["p_up"] for r in web if r.get("p_up") is not None}
            failed["live_web"] = sorted(set(tickers) - set(arms["live_web"]))
            web_paths = {r["ticker"]: r.get("saved_to", "") for r in web}
            log(f"  live_web done ({len(arms['live_web'])} stocks, {len(failed['live_web'])} failed)")
        decided_at = datetime.now(UTC) if real_clock else now  # the real time after all decisions finished
        cutoff_close = {t: table.closes[t][table.index_on_or_before(c)] for t in tickers}
        rec = ledger.append(
            "decision", cutoff=c.isoformat(), deadline=dl.isoformat(),
            decided_at=decided_at.isoformat(timespec="seconds"), on_time=decided_at < dl, horizon=cfg["horizon"],
            model=model, config_hash=cfg["config_hash"], arms=arms, cutoff_close=cutoff_close,
            web_records=web_paths, failed=failed, git=prov.git_state(BACKEND.parent),
            model_digest=prov.ollama_digest(model))
        written.append(rec)
        log(f"  logged decision seq {rec['seq']} ({'on time' if rec['on_time'] else 'LATE: excluded'})")

    recs = ledger.records()
    resolved = {r["cutoff"] for r in recs if r["type"] == "outcome"}
    for r in [r for r in recs if r["type"] == "decision" and r["cutoff"] not in resolved]:
        c = date.fromisoformat(r["cutoff"])
        rd = resolve_day(c, bars[MARKET][0], r["horizon"], now)
        if rd is None:
            continue
        rets = {}
        for t in r["cutoff_close"]:
            series = dict(zip(*bars[t], strict=True))
            if c in series and rd in series:
                rets[t] = series[rd] / series[c] - 1
        log(f"{c}: outcome available ({rd})")
        if not dry_run:
            written.append(ledger.append("outcome", cutoff=r["cutoff"], resolve_date=rd.isoformat(),
                                         returns=rets, up={t: v > 0 for t, v in rets.items()}))
    return written


def scoreboard(ledger: Ledger) -> dict[str, Any]:
    recs = ledger.verify()
    outcomes = {r["cutoff"]: r for r in recs if r["type"] == "outcome"}
    decisions = [r for r in recs if r["type"] == "decision"]
    rows: dict[str, list[tuple[float, bool]]] = {}
    weeks = 0
    for d in decisions:
        o = outcomes.get(d["cutoff"])
        if not d["on_time"] or o is None:
            continue
        weeks += 1
        for arm, ps in {**d["arms"], "always_up": dict.fromkeys(o["up"], 0.51)}.items():
            rows.setdefault(arm, []).extend((ps[t], o["up"][t]) for t in ps if t in o["up"])
    arms = {arm: {"n": len(v), "accuracy": round(sum((p >= 0.5) == u for p, u in v) / len(v), 4),
                  "brier": round(sum((p - u) ** 2 for p, u in v) / len(v), 4)} for arm, v in rows.items() if v}
    return {"records": len(recs), "decisions": len(decisions),
            "missed": sum(r["type"] == "missed" for r in recs),
            "late": sum(1 for d in decisions if not d["on_time"]),
            "weeks_scored": weeks, "arms": arms,
            "note": "too early to judge (under 8 scored weeks)" if weeks < 8 else ""}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=BACKEND / "configs" / "forward_v1.toml")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    cfg = load_config(args.config)
    ledger = Ledger(BACKEND / "results" / "forward" / f"{cfg['tag']}.jsonl")
    if args.status:
        print(json.dumps(scoreboard(ledger), indent=1))
        return
    lock_path = ledger.path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another forward-test run is in progress; exiting")
            return
        written = asyncio.run(run_once(cfg, ledger, dry_run=args.dry_run))
    print(f"{len(written)} record(s) written" + (" (dry run)" if args.dry_run else ""))
    print(json.dumps(scoreboard(ledger), indent=1))


if __name__ == "__main__":
    main()
