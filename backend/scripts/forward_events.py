"""Forward test of the 1-week Bonsai book on new S&P 500 earnings releases (docs/PLAN_60_V2.md, Stage 3). Run BY HAND,
ideally every weekday evening; nothing runs on its own (no service, no timer). Paper money only; shadow book only.

    python scripts/forward_events.py              # discover new releases, decide them, log, score what matured
    python scripts/forward_events.py --no-gpu     # Bonsai-lite on CPU (the PC-off / GPU-busy fallback)
    python scripts/forward_events.py --status     # verify the ledger and print the scoreboard

Each run:
  1. discovery: every S&P 500 member's 8-K with Item 2.02 accepted since the last run (SEC submissions API, free);
  2. fact sheet: press release -> number reader (code-checked quotes) -> build_features.py, exactly as in the backtests;
  3. decision: Bonsai-27B's 1-week log-odds (decide_events.py --horizon 5). Without the GPU, Bonsai-lite (a ridge on
     every past Bonsai 1-week decision, scripts/bonsai_lite.py) scores the release instead, flagged source=lite and
     scored as its own book (pre-registered fallback rule, PLAN_60_V2 "Mon 28 item 2");
  4. ledger: each decision is appended to a hash-chained ledger (app/forward/ledger.py). It counts only if it was
     written before its entry open (09:30 ET on the first weekday after the SEC acceptance; a holiday makes this
     stricter, never looser). A late or impossible decision is logged as `missed` and is never backfilled;
  5. outcomes: once 5 trading days have passed, the release's 5-day return vs its sector ETF is appended.

--as-of replays the runner at an earlier time for a dry run (--dir keeps that ledger apart); it never touches the
real ledger. Lookahead guard: discovery only keeps filings accepted before the run's time, and outcomes only use
bars dated before the run's date.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from event_eval import SECTOR_ETF

from app.forward.ledger import Ledger
from app.forward.schedule import NY, OPEN
from app.sandbox.events import Prices, entry_index, fwd_excess
from app.tools.gateway import _read_env_file

PY = str(BACKEND / ".venv" / "bin" / "python")
H = 5


def entry_deadline(accepted_utc: str) -> datetime:
    """09:30 ET of the first weekday whose open is after the acceptance."""
    acc = datetime.fromisoformat(accepted_utc).replace(tzinfo=UTC).astimezone(NY)
    d = acc.date()
    if acc.weekday() >= 5 or acc.time() >= OPEN:
        d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return datetime.combine(d, OPEN, NY)


def members(year: int) -> pd.DataFrame:
    m = pd.read_csv(BACKEND / "data" / "events" / "members_2024_2026.csv")
    y = min(year, int(m["year"].max()))
    return m[(m["year"] == y) & (m["index"] == "sp500") & m["cik"].notna()]


async def discover(since: date, now: datetime) -> pd.DataFrame:
    from build_events import Sec, company_events, ex99_url
    ua = _read_env_file(BACKEND / ".env").get("SEC_USER_AGENT", "")
    if not ua:
        raise SystemExit("set SEC_USER_AGENT in backend/.env")
    sec = Sec(ua)
    mem = members(now.year)
    info = {int(r.cik): r for r in mem.itertuples()}
    try:
        found: list[dict[str, Any]] = []
        for i in range(0, len(mem), 50):
            batch = await asyncio.gather(*(company_events(sec, int(c), since.isoformat(), now.date().isoformat())
                                           for c in mem["cik"].iloc[i:i + 50]))
            found += [e for b in batch for e in b]
        found = [e for e in found if datetime.fromisoformat(e["accepted_utc"]).replace(tzinfo=UTC) <= now]
        urls = await asyncio.gather(*(ex99_url(sec, e) for e in found))
    finally:
        await sec.client.aclose()
    rows = [{"cik": e["cik"], "ticker": info[e["cik"]].ticker, "index": "sp500", "sector": info[e["cik"]].sector,
             "accession": e["accession"], "accepted_utc": e["accepted_utc"], "filed": e["filed"],
             "items": e["items"], "ex99_url": u} for e, u in zip(found, urls, strict=True)]
    return pd.DataFrame(rows, columns=["cik", "ticker", "index", "sector", "accession", "accepted_utc", "filed",
                                       "items", "ex99_url"])


def gpu_free() -> bool:
    try:
        out = subprocess.run(["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"], capture_output=True,
                             text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0 and not out.stdout.strip()


class Ollama:
    """A private Ollama server for one step (started with nohup-style setsid, stopped after; never a service)."""

    def __init__(self, port: int, models: str, parallel: int, log: Path) -> None:
        env = os.environ | {"OLLAMA_MODELS": models, "OLLAMA_NOPRUNE": "1", "OLLAMA_HOST": f"127.0.0.1:{port}",
                            "OLLAMA_NUM_PARALLEL": str(parallel), "OLLAMA_MAX_LOADED_MODELS": "1",
                            "OLLAMA_FLASH_ATTENTION": "1"}
        self.proc = subprocess.Popen(["ollama", "serve"], env=env, stdout=log.open("a"), stderr=subprocess.STDOUT,
                                     start_new_session=True)
        for _ in range(60):
            if subprocess.run(["curl", "-s", "-m", "2", f"127.0.0.1:{port}/api/tags"], capture_output=True, check=False).returncode == 0:
                return
            time.sleep(1)
        self.stop()
        raise RuntimeError(f"ollama on :{port} did not start")

    def stop(self) -> None:
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            self.proc.wait(timeout=60)


def run(cmd: list[str]) -> bool:
    print("  $", " ".join(cmd[1:]), flush=True)
    return subprocess.run(cmd, cwd=BACKEND, check=False).returncode == 0


def fact_sheets(d: Path, ev_csv: Path, since: date, use_gpu: bool, tag: str) -> Path:
    ex = d / "extract.jsonl"
    run([PY, "scripts/extract_events.py", "fetch", "--events", str(ev_csv), "--from", since.isoformat()])
    if use_gpu:
        srv = Ollama(11437, "/usr/share/ollama/.ollama/models", 4, d / "ollama.log")
        try:
            run([PY, "scripts/extract_events.py", "extract", "--events", str(ev_csv), "--from", since.isoformat(),
                 "--model", "qwen3:8b", "--base-url", "http://127.0.0.1:11437", "--parallel", "4", "--out",
                 str(ex.relative_to(BACKEND))])
        finally:
            srv.stop()
    else:  # no reader: the fact sheet keeps only the SEC-filed and price parts (lite reads those)
        done = {json.loads(x)["accession"] for x in ex.read_text().splitlines()} if ex.exists() else set()
        with ex.open("a") as f:
            for r in pd.read_csv(ev_csv).itertuples():
                if r.accession not in done:
                    f.write(json.dumps({"accession": r.accession, "ticker": r.ticker, "cik": int(r.cik),
                                        "accepted_utc": r.accepted_utc, "model": "none"}) + "\n")
    run([PY, "scripts/build_features.py", "--events", str(ev_csv), "--extract", str(ex), "--name", tag])
    return BACKEND / "results" / "events" / f"features_{tag}.csv"


def bonsai(ev_csv: Path, ex: Path, feats: Path, tag: str, since: date, d: Path) -> dict[str, float]:
    srv = Ollama(11435, str(Path.home() / ".ollama" / "models"), 3, d / "ollama.log")
    try:
        run([PY, "scripts/decide_events.py", "--events", str(ev_csv), "--extract", str(ex), "--features", str(feats),
             "--tag", tag, "--horizon", str(H), "--explain", "0", "--from", since.isoformat(), "--to", "2099-12-31"])
    finally:
        srv.stop()
    p = BACKEND / "results" / "events" / f"decide_bonsai-27b_latest_{tag}_h{H}.jsonl"
    rows = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
    return {r["accession"]: float(r["logodds"]) for r in rows if not r.get("censored")}


def pre_entry(ev: pd.DataFrame, p: Prices) -> pd.DataFrame:
    """The price features event_eval.build computes, from closes before the acceptance only: at decision time the
    entry day's bar does not exist yet, so build() would mark every new release unscorable."""
    rows = []
    for r in ev.itertuples():
        t, etf = str(r.ticker).replace(".", "-"), SECTOR_ETF.get(str(r.sector))
        if etf is None or t not in p.close.columns or etf not in p.close.columns:
            continue
        acc = datetime.fromisoformat(str(r.accepted_utc)).replace(tzinfo=UTC).astimezone(NY)
        # bars strictly before the entry day: a pre-open release enters that day, a later one the next trading day
        cutoff = pd.Timestamp(acc.date()) - pd.Timedelta(days=1 if acc.time() < OPEN else 0)
        c, e = p.close[t].loc[:cutoff], p.close[etf].loc[:cutoff]
        i = len(c)
        row = {"accession": r.accession, "sector": r.sector, "momentum": np.nan}
        if i >= 253 and pd.notna(c.iloc[i - 253]) and pd.notna(c.iloc[i - 22]):
            row["momentum"] = float((c.iloc[i - 22] / c.iloc[i - 253] - 1) - (e.iloc[i - 22] / e.iloc[i - 253] - 1))
        rows.append(row)
    return pd.DataFrame(rows, columns=["accession", "sector", "momentum"])


def lite(feats: Path, ev_csv: Path, p: Prices) -> dict[str, float]:
    """Bonsai-lite: ridge on every past Bonsai 1-week decision (S&P 500, 2024 and 2025-26), applied to new releases."""
    from bonsai_lite import SAMPLES, design, ridge
    from event_eval import build
    from secchk_eval import load
    cols = ["accession", "eps_q", "eps_prior", "rev_q", "rev_prior", "guidance", "tone"]
    hist = Prices.from_long(pd.read_parquet(BACKEND / "data/events/ohlcv_2023-01-01_2026-09-25.parquet"))
    frames = []
    for tg, events, fs in SAMPLES.values():
        df = build(pd.read_csv(BACKEND / events), hist)
        f = pd.read_csv(BACKEND / "results" / "events" / fs)[cols]
        frames.append(df[df["scorable"]].merge(f, on="accession").merge(load(tg, H), on="accession"))
    train = pd.concat(frames, ignore_index=True)
    cats = {c: sorted(train[c].dropna().astype(str).unique()) for c in ("guidance", "tone", "sector")}
    model = ridge(design(train, cats), train["logodds"].to_numpy(float))
    new = pre_entry(pd.read_csv(ev_csv), p).merge(pd.read_csv(feats)[cols], on="accession")
    for c in ("guidance", "tone"):
        new[c] = new[c].fillna("none" if c == "guidance" else "neutral")
    if new.empty:
        return {}
    return dict(zip(new["accession"], model(design(new, cats)), strict=True))


def prices_for(tickers: set[str], start: date, end: date) -> Prices:
    import yfinance as yf
    frames = []
    for t in sorted(tickers):
        df = yf.download(t, start=start.isoformat(), end=end.isoformat(), auto_adjust=True, progress=False,
                         multi_level_index=False)
        if not df.empty:
            frames.append(df[["Open", "High", "Low", "Close", "Volume"]].assign(Ticker=t))
    long = pd.concat(frames).rename_axis("Date").reset_index()
    long["Date"] = pd.to_datetime(long["Date"]).dt.date.astype(str)
    return Prices.from_long(long)


def score(recs: list[dict[str, Any]]) -> dict[str, Any]:
    dec = {r["accession"]: r for r in recs if r["type"] == "decision" and r.get("on_time")}
    out = {r["accession"]: r["fwd5"] for r in recs if r["type"] == "outcome" and r.get("fwd5") is not None}
    res: dict[str, Any] = {"decisions": sum(r["type"] == "decision" for r in recs),
                           "on_time": len(dec), "missed": sum(r["type"] == "missed" for r in recs),
                           "outcomes": len(out)}
    for src in ("bonsai", "lite"):
        rows = [(dec[a]["logodds"], out[a]) for a in dec if a in out and dec[a]["source"] == src]
        if len(rows) >= 10:
            x = pd.DataFrame(rows, columns=["s", "y"])
            res[f"{src}_ic"] = round(float(x["s"].rank().corr(x["y"].rank())), 3)
            res[f"{src}_n"] = len(rows)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="results/forward/events")
    ap.add_argument("--as-of", default="", help="replay at this UTC time (dry runs only; needs a --dir of its own)")
    ap.add_argument("--start", default="2026-10-02", help="first filing date the forward test covers")
    ap.add_argument("--no-gpu", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    d = BACKEND / args.dir
    ledger = Ledger(d / "ledger.jsonl")
    if args.status:
        print(json.dumps(score(ledger.verify()), indent=1))
        return
    if args.as_of and args.dir == "results/forward/events":
        raise SystemExit("--as-of is for dry runs: give it its own --dir")
    now = datetime.fromisoformat(args.as_of).replace(tzinfo=UTC) if args.as_of else datetime.now(UTC)
    d.mkdir(parents=True, exist_ok=True)
    recs = ledger.verify()
    seen = {r["accession"] for r in recs if r["type"] in ("decision", "missed")}
    runs = [r for r in recs if r["type"] == "run"]
    since = max(date.fromisoformat(args.start), date.fromisoformat(runs[-1]["as_of"][:10]) - timedelta(days=3)
                if runs else date.fromisoformat(args.start))
    tag = "forward" if not args.as_of else "forward_" + d.name
    print(f"run as of {now.isoformat(timespec='minutes')}; filings since {since}", flush=True)

    new = asyncio.run(discover(since, now))
    new = new[~new["accession"].isin(seen)]
    ev_csv = d / "events.csv"
    allev = pd.concat([pd.read_csv(ev_csv), new]) if ev_csv.exists() else new
    allev.drop_duplicates("accession").to_csv(ev_csv, index=False)
    print(f"{len(new)} new releases", flush=True)

    use_gpu = not args.no_gpu and gpu_free()
    logodds: dict[str, float] = {}
    source = "bonsai" if use_gpu else "lite"
    tickers = {str(t).replace(".", "-") for t in allev["ticker"]} | set(SECTOR_ETF.values()) | {"SPY"}
    p = prices_for(tickers, now.date() - timedelta(days=420), now.date())
    if len(new):
        new_csv = d / "events_new.csv"
        new.to_csv(new_csv, index=False)
        feats = fact_sheets(d, new_csv, since, use_gpu, tag)
        if use_gpu:
            logodds = bonsai(new_csv, d / "extract.jsonl", feats, tag, since, d)
        else:
            logodds = lite(feats, new_csv, p)
    for r in new.itertuples():
        dl = entry_deadline(str(r.accepted_utc))
        base = {"accession": r.accession, "ticker": r.ticker, "sector": r.sector, "accepted_utc": r.accepted_utc,
                "entry_deadline": dl.isoformat(), "as_of": now.isoformat(timespec="seconds")}
        if r.accession not in logodds:
            ledger.append("missed", **base, reason="no decision (no press release, fact sheet or model output)")
        elif now >= dl:
            ledger.append("missed", **base, reason="decided after the entry open: never backfilled")
        else:
            ledger.append("decision", **base, source=source, logodds=round(float(logodds[r.accession]), 4),
                          on_time=True)

    # outcomes: only bars dated before the run's date
    days = pd.DatetimeIndex(p.open.index)
    days = days[days.date < now.date()]
    have = {r["accession"] for r in ledger.records() if r["type"] == "outcome"}
    for r in ledger.records():
        if r["type"] != "decision" or r["accession"] in have:
            continue
        t, etf = str(r["ticker"]).replace(".", "-"), SECTOR_ETF.get(str(r["sector"]))
        i = entry_index(days, datetime.fromisoformat(r["accepted_utc"]))
        if etf is None or i is None or i + H >= len(days) or t not in p.open.columns:
            continue
        f5 = fwd_excess(p, t, etf, i, H)
        ledger.append("outcome", accession=r["accession"], entry=days[i].date().isoformat(),
                      fwd5=None if f5 is None else round(f5, 5), as_of=now.isoformat(timespec="seconds"))
    ledger.append("run", as_of=now.isoformat(timespec="seconds"), new=len(new), source=source,
                  gpu=use_gpu)
    print(json.dumps(score(ledger.verify()), indent=1))


if __name__ == "__main__":
    main()
