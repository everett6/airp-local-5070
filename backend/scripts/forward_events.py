"""Forward test of the 1-week Bonsai book on new S&P 500 earnings releases (docs/PLAN_60_V2.md, Stage 3). Run BY HAND,
ideally every weekday evening; nothing runs on its own (no service, no timer). Paper money only; shadow book only.

    python scripts/forward_events.py              # discover new releases, decide them, log, score what matured
    python scripts/forward_events.py --no-gpu     # Bonsai-lite on CPU (the PC-off / GPU-busy fallback)
    python scripts/forward_events.py --status     # verify the ledger and print the scoreboard
    python scripts/forward_events.py --index sp400,sp600 --dir results/forward/events_breadth   # the breadth book

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

When to run: twice each weekday. About 08:45 ET catches the pre-market releases (most are filed 06:30-08:45 ET and
enter at that morning's open), and any time in the evening catches the after-close ones (they enter the next morning).
An evening-only schedule misses every pre-market release (the 8-25 Sep 2026 dry run missed 6 of 13 that way).

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
from collections.abc import Collection
from contextlib import ExitStack
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from event_eval import SECTOR_ETF

from app.data_ingestion.tickers import trading_symbol
from app.forward.ledger import Ledger, jsonl_records, open_append
from app.forward.schedule import NY, OPEN
from app.sandbox.events import Prices, entry_index, fwd_excess
from app.sandbox.gpu_lock import gpu_priority
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


def members(year: int, indexes: tuple[str, ...] = ("sp500",)) -> pd.DataFrame:
    m = pd.read_csv(BACKEND / "data" / "events" / "members_2024_2026.csv")
    y = min(year, int(m["year"].max()))
    return m[(m["year"] == y) & m["index"].isin(indexes) & m["cik"].notna()].drop_duplicates("cik")


async def discover(since: date, now: datetime, indexes: tuple[str, ...] = ("sp500",)) -> pd.DataFrame:
    from build_events import Sec, company_events, ex99_url
    ua = _read_env_file(BACKEND / ".env").get("SEC_USER_AGENT", "")
    if not ua:
        raise SystemExit("set SEC_USER_AGENT in backend/.env")
    sec = Sec(ua)
    mem = members(now.year, indexes)
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
    rows = [{"cik": e["cik"], "ticker": info[e["cik"]].ticker, "index": info[e["cik"]].index,
             "sector": info[e["cik"]].sector,
             "accession": e["accession"], "accepted_utc": e["accepted_utc"], "filed": e["filed"],
             "items": e["items"], "ex99_url": u} for e, u in zip(found, urls, strict=True)]
    return pd.DataFrame(rows, columns=["cik", "ticker", "index", "sector", "accession", "accepted_utc", "filed",
                                       "items", "ex99_url"])


HEAVY = ("python", "ollama", "vllm")  # compute jobs; desktop apps (file manager, browser) hold a few MiB and don't count
HEAVY_MIB = 1024


def gpu_busy(listing: str) -> bool:
    """From `nvidia-smi --query-compute-apps=process_name,used_memory --format=csv,noheader,nounits`: busy if any
    compute job (python, ollama, vllm) or any process with 1 GiB or more is on the GPU."""
    for line in listing.strip().splitlines():
        name, _, mem = line.rpartition(",")
        try:
            mib = float(mem)
        except ValueError:
            mib = HEAVY_MIB
        if any(h in name.lower() for h in HEAVY) or mib >= HEAVY_MIB:
            return True
    return False


def gpu_free() -> bool:
    try:
        out = subprocess.run(["nvidia-smi", "--query-compute-apps=process_name,used_memory",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0 and not gpu_busy(out.stdout)


def wait_gpu_free(timeout_s: float, poll_s: float = 15.0) -> bool:
    """gpu_free(), waiting up to `timeout_s` for research jobs to yield (they unload within one request)."""
    end = time.monotonic() + timeout_s
    while not gpu_free():
        if time.monotonic() >= end:
            return False
        time.sleep(poll_s)
    return True


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


def run(cmd: list[str]) -> None:
    """Stop the event run if a child fails, before recording a missed decision or a clean heartbeat."""
    print("  $", " ".join(cmd[1:]), flush=True)
    subprocess.run(cmd, cwd=BACKEND, check=True)


def fact_sheets(d: Path, ev_csv: Path, since: date, use_gpu: bool, tag: str, prices: Path) -> Path:
    ex = d / "extract.jsonl"
    run([PY, "scripts/extract_events.py", "fetch", "--events", str(ev_csv), "--from", since.isoformat()])
    if use_gpu:
        srv = Ollama(11437, "/usr/share/ollama/.ollama/models", 4, d / "ollama.log")
        try:
            run([PY, "scripts/extract_events.py", "extract", "--events", str(ev_csv), "--from", since.isoformat(),
                 "--to", "2099-12-31", "--model", "qwen3:8b", "--base-url", "http://127.0.0.1:11437", "--parallel", "4", "--out",
                 str(ex.relative_to(BACKEND))])
        finally:
            srv.stop()
    else:  # no reader: the fact sheet keeps only the SEC-filed and price parts (lite reads those)
        done = {x["accession"] for x in jsonl_records(ex)}
        with open_append(ex) as f:
            for r in pd.read_csv(ev_csv).itertuples():
                if r.accession not in done:
                    f.write(json.dumps({"accession": r.accession, "ticker": r.ticker, "cik": int(r.cik),
                                        "accepted_utc": r.accepted_utc, "model": "none"}) + "\n")
    run([PY, "scripts/build_features.py", "--events", str(ev_csv), "--extract", str(ex), "--name", tag,
         "--prices", str(prices), "--live"])
    return BACKEND / "results" / "events" / f"features_{tag}.csv"


def bonsai(ev_csv: Path, ex: Path, feats: Path, tag: str, since: date, d: Path, prices: Path) -> dict[str, float]:
    srv = Ollama(11435, str(Path.home() / ".ollama" / "models"), 3, d / "ollama.log")
    try:
        run([PY, "scripts/decide_events.py", "--events", str(ev_csv), "--extract", str(ex), "--features", str(feats),
             "--tag", tag, "--horizon", str(H), "--explain", "0", "--from", since.isoformat(), "--to", "2099-12-31",
             "--prices", str(prices), "--live"])
    finally:
        srv.stop()
    p = BACKEND / "results" / "events" / f"decide_bonsai-27b_latest_{tag}_h{H}.jsonl"
    return {r["accession"]: float(r["logodds"]) for r in jsonl_records(p) if not r.get("censored")}


def pre_entry(ev: pd.DataFrame, p: Prices) -> pd.DataFrame:
    """The price features event_eval.build computes, from closes before the acceptance only: at decision time the
    entry day's bar does not exist yet, so build() would mark every new release unscorable."""
    rows = []
    for r in ev.itertuples():
        t, etf = trading_symbol(r.ticker), SECTOR_ETF.get(str(r.sector))
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


BAR_COLS = ["Open", "High", "Low", "Close", "Volume"]


def daily_bars(tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    """Each ticker's daily bars from Yahoo: one request for all of them, then one by one for any that request left
    out (the only path until 1 Oct 2026). Both return the same numbers (compared on the live tickers that day); the
    list grows with every release, and hundreds of single requests twice a day invite Yahoo's rate limit."""
    import yfinance as yf
    out: dict[str, pd.DataFrame] = {}
    if len(tickers) > 1:
        try:
            both = yf.download(tickers, start=start, end=end, auto_adjust=True, progress=False, group_by="ticker",
                               threads=True)
        except Exception as e:  # noqa: BLE001 - whatever the batch does, the one-by-one path below still runs
            print(f"prices: the batched request failed ({type(e).__name__}); fetching one by one", flush=True)
            both = pd.DataFrame()
        have = set(both.columns.get_level_values(0)) if isinstance(both.columns, pd.MultiIndex) else set()
        for t in tickers:
            if t in have and set(BAR_COLS) <= set(both[t].columns):
                df = both[t][BAR_COLS].dropna(how="all").rename_axis(columns=None)
                if not df.empty:
                    if df["Volume"].notna().all():  # whole numbers, as a single download returns them
                        df = df.astype({"Volume": "int64"})
                    out[t] = df
    for t in tickers:
        if t not in out:
            df = yf.download(t, start=start, end=end, auto_adjust=True, progress=False, multi_level_index=False)
            if not df.empty:
                out[t] = df[BAR_COLS]
    return out


def prices_for(tickers: set[str], start: date, end: date, save: Path) -> Prices:
    """Daily bars dated before `end` (Yahoo, free), also saved in the long format the pipeline scripts read."""
    bars = daily_bars(sorted(tickers), start.isoformat(), end.isoformat())
    frames = [bars[t].assign(Ticker=t) for t in sorted(tickers) if t in bars]
    long = pd.concat(frames).rename_axis("Date").reset_index()
    long["Date"] = pd.to_datetime(long["Date"]).dt.date.astype(str)
    tmp = save.with_name(save.name + ".tmp")  # whole file in one step: the sleeve reads it right after this run
    long.to_parquet(tmp)
    tmp.replace(save)
    return Prices.from_long(long)


NO_RELEASE = "no decision (no press release, fact sheet or model output)"


def undecidable(new: pd.DataFrame, p: Prices, now: datetime, fetched: bool = False) -> dict[str, str]:
    """Releases no decision can be made for, with the `missed` reason (docs/PLAN_60_V2.md: "late or impossible
    decisions are logged as missed"): the filing has no press release (about 1 in 70 has none), or the stock itself
    has no recent price. These belong to the release, not to the run: one of them must not stop the others from
    being decided. `fetched`: the download step has run, so a release without its text on disk has none."""
    from extract_events import text_path
    out: dict[str, str] = {}
    for r in new.itertuples():
        acc, t = str(r.accession), trading_symbol(r.ticker)
        url = getattr(r, "ex99_url", "x")
        if pd.isna(url) or not str(url).strip() or (fetched and not text_path(acc).exists()):
            out[acc] = NO_RELEASE
        elif t not in p.close or p.close[t].dropna().empty:
            out[acc] = f"no decision (no price for {t})"
        elif (now.date() - pd.Timestamp(p.close[t].dropna().index[-1]).date()).days > 5:
            out[acc] = f"no decision (stale price for {t})"
    return out


def validate_event_inputs(new: pd.DataFrame, p: Prices, now: datetime,
                          feats: Path | None = None, logodds: dict[str, float] | None = None,
                          skip: Collection[str] = ()) -> None:
    """Fail a forward run on missing or stale inputs before recording decisions. Releases in `skip` (see
    `undecidable`) are left out of the per-release checks."""
    if p.close.empty or "SPY" not in p.close or p.close["SPY"].dropna().empty:
        raise ValueError("event data check: no SPY closing prices")
    last = pd.Timestamp(p.close["SPY"].dropna().index[-1]).date()
    if last > now.date() or (now.date() - last).days > 5:
        raise ValueError(f"event data check: SPY close stale or future-dated ({last})")
    new = new[~new["accession"].isin(set(skip))]
    if new.empty:
        return
    missing: list[str] = []
    for r in new.itertuples():
        acc = datetime.fromisoformat(str(r.accepted_utc)).replace(tzinfo=UTC)
        if acc > now:
            missing.append(f"{r.accession}: acceptance after run time")
        for asset in (trading_symbol(r.ticker), SECTOR_ETF.get(str(r.sector))):
            if asset is None or asset not in p.close or p.close[asset].dropna().empty:
                missing.append(f"{r.accession}: no close for {asset or 'sector ETF'}")
            elif (now.date() - pd.Timestamp(p.close[asset].dropna().index[-1]).date()).days > 5:
                missing.append(f"{r.accession}: stale close for {asset}")
    if feats is not None:
        try:
            f = pd.read_csv(feats)
        except pd.errors.EmptyDataError as e:
            raise ValueError("event data check: fact sheet file empty") from e
        if not {"accession", "fact_sheet"} <= set(f.columns):
            missing.append("fact sheet columns absent")
        else:
            cards = f.drop_duplicates("accession").set_index("accession")["fact_sheet"]
            missing += [f"{a}: fact sheet absent" for a in new["accession"]
                        if a not in cards.index or pd.isna(cards[a]) or not str(cards[a]).strip()]
    if logodds is not None:
        missing += [f"{a}: model score absent" for a in new["accession"]
                    if a not in logodds or not np.isfinite(logodds[a])]
    if missing:
        raise ValueError("event data check: " + "; ".join(missing[:8]))


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
    ap.add_argument("--start", default="2026-09-30", help="first filing date the forward test covers")
    ap.add_argument("--no-gpu", action="store_true")
    ap.add_argument("--index", default="sp500", help="comma list: sp500 (the shadow book), sp400,sp600 (breadth)")
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

    new = asyncio.run(discover(since, now, tuple(args.index.split(","))))
    new = new[~new["accession"].isin(seen)]
    ev_csv = d / "events.csv"
    allev = pd.concat([pd.read_csv(ev_csv), new]) if ev_csv.exists() else new
    tmp = ev_csv.with_name(ev_csv.name + ".tmp")  # in one step: a cut-off file would silently lose past releases
    allev.drop_duplicates("accession").to_csv(tmp, index=False)
    tmp.replace(ev_csv)
    print(f"{len(new)} new releases", flush=True)

    prio = ExitStack()  # forward decisions can't be made later: research jobs yield the GPU (app/sandbox/gpu_lock.py)
    if len(new) and not args.no_gpu:
        prio.enter_context(gpu_priority("forward_events"))
    use_gpu = not args.no_gpu and wait_gpu_free(900 if len(new) else 0)
    logodds: dict[str, float] = {}
    guidance: dict[str, str] = {}
    source = "bonsai" if use_gpu else "lite"
    tickers = {trading_symbol(t) for t in allev["ticker"]} | set(SECTOR_ETF.values()) | {"SPY"}
    px_file = d / "prices.parquet"
    p = prices_for(tickers, now.date() - timedelta(days=420), now.date(), px_file)
    skip = undecidable(new, p, now)
    validate_event_inputs(new, p, now, skip=skip)
    if len(skip) < len(new):
        new_csv = d / "events_new.csv"
        new.to_csv(new_csv, index=False)
        feats = fact_sheets(d, new_csv, since, use_gpu, tag, px_file)
        skip = undecidable(new, p, now, fetched=True)
    if len(skip) < len(new):
        validate_event_inputs(new, p, now, feats=feats, skip=skip)
        f = pd.read_csv(feats).drop_duplicates("accession")
        guidance = dict(zip(f["accession"], f["guidance"].fillna("none"), strict=True))
        if use_gpu:
            logodds = bonsai(new_csv, d / "extract.jsonl", feats, tag, since, d, px_file)
        else:
            logodds = lite(feats, new_csv, p)
        validate_event_inputs(new, p, now, logodds=logodds, skip=skip)
    prio.close()
    for r in new.itertuples():
        dl = entry_deadline(str(r.accepted_utc))
        base = {"accession": r.accession, "ticker": r.ticker, "sector": r.sector, "accepted_utc": r.accepted_utc,
                "entry_deadline": dl.isoformat(), "as_of": now.isoformat(timespec="seconds"),
                "guidance": guidance.get(r.accession, "none")}
        if r.accession in skip or r.accession not in logodds:
            why = skip.get(str(r.accession), NO_RELEASE)
            if now < dl:  # its open is still ahead: the next run tries again (a download or price may have failed)
                print(f"not decidable yet, tried again next run: {r.ticker} {r.accession}: {why}", flush=True)
                continue
            ledger.append("missed", **base, reason=why)
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
        t, etf = trading_symbol(r["ticker"]), SECTOR_ETF.get(str(r["sector"]))
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
