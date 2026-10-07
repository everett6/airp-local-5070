"""The autopilot account's algorithmic day-trading engine: the E1 minute-bar ensemble (no AI), event-driven.

    python scripts/algo_engine.py            # runs until results/forward/algo/STOP exists or SIGTERM

E1 failed its registered test (results/e1_ensemble.json), so by the rule fixed before the run the engine trades in
SHADOW: signals and simulated fills are logged, no orders. Live orders only when results/forward/algo/LIVE exists
(the user's call). full_auto.py starts and stops this process with the autopilot.

Speed: bars arrive by websocket (Alpaca IEX stream, pushed at each minute's close); features for all 14 ETFs are
built from yesterday's and today's bars only (session stats precomputed at the open); orders go out concurrently
over a kept-alive HTTPS pool, so no connection or TLS setup sits between a signal and its orders.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import httpx
import numpy as np
import pandas as pd
import websockets

from app.data_ingestion.bars import key
from app.portfolio.broker import DATA, PAPER
from app.sandbox import minute_ensemble as me

NY = ZoneInfo("America/New_York")
ALGO = BACKEND / "results" / "forward" / "algo"
KEYS = ("AIRP_AUTO_ALPACA_KEY_ID", "AIRP_AUTO_ALPACA_SECRET_KEY")  # the autopilot's own paper account
STREAM = "wss://stream.data.alpaca.markets/v2/iex"
SYMBOLS = (*me.UNIVERSE, "QQQ")  # QQQ: SPY's leader, a signal only (it belongs to the N1 hedge basket)
CONFIG = BACKEND / "config" / "autopilot_day.json"
DEFAULTS = {"start": "09:35", "end": "15:55", "gross": 3.0,  # algo book, fraction of equity, intraday only
            "account_gross_cap": 3.9,   # whole account, under Alpaca's 4x day-trading buying power
            "daily_stop": 0.05,         # account down this much on the day: algo book flat until tomorrow
            "breaker": 0.04,            # R2: down this much: nothing new until tomorrow
            "stale_skip_s": 180,        # R3: no decision on a bar older than this
            "stale_flat_s": 600}        # R3: no bars this long inside the window: flat
FLAT_AT = 385                # bar of the day (15:55): flat at the latest
WAIT_S = 1.5                 # after the leader's bar, wait this long at most for the other bars of the minute


def now_utc() -> datetime:
    return datetime.now(UTC)


def config() -> dict[str, Any]:
    """The user's window and the risk numbers; read at every decision, so a change in the app applies at once."""
    try:
        got = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        got = {}
    return {**DEFAULTS, **{k: v for k, v in got.items() if k in DEFAULTS}}


def minute(hm: str) -> int:
    """'09:35' -> bar index of that minute (0 = 09:30)."""
    return int(hm[:2]) * 60 + int(hm[3:5]) - 570


def in_window(m: int, cfg: dict[str, Any]) -> bool:
    """A decision on bar m happens at minute m + 1; it opens risk only inside [start, end)."""
    return minute(cfg["start"]) <= m + 1 < minute(cfg["end"])


def no_new_risk(new: dict[str, float], cur: dict[str, float]) -> dict[str, float]:
    """R2: keep or shrink what is held, open nothing, flip nothing."""
    out = {}
    for s, w in cur.items():
        n = new.get(s, 0.0)
        if n * w > 0:
            out[s] = n if abs(n) < abs(w) else w
    return out


def regular(df: pd.DataFrame) -> pd.DataFrame:
    t = df["ts"].dt.hour * 60 + df["ts"].dt.minute
    return df[(t >= 570) & (t < 960)]


def to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows).rename(columns={"t": "ts", "o": "open", "h": "high", "l": "low", "c": "close",
                                            "v": "volume"})
    if df.empty:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(NY)
    return regular(df[["ts", "open", "high", "low", "close", "volume"]])


def share_orders(weights: dict[str, float], held: dict[str, float], equity: float,
                 prices: dict[str, float]) -> list[dict[str, Any]]:
    """Market orders taking the algo's held shares to `weights` (fraction of equity); flips close first."""
    out = []
    for s in sorted(set(weights) | set(held)):
        px = prices.get(s)
        if not px:
            continue
        cur = held.get(s, 0.0)
        tgt = float(int(weights.get(s, 0.0) * equity / px))
        if cur * tgt < 0:
            tgt = 0.0
        if tgt != cur:
            out.append({"symbol": s, "side": "buy" if tgt > cur else "sell", "qty": abs(tgt - cur), "to": tgt})
    return sorted(out, key=lambda o: abs(o["to"]) > abs(held.get(o["symbol"], 0.0)))  # reductions first


def shadow_step(book: dict[str, Any], weights: dict[str, float], prices: dict[str, float]) -> float:
    """Simulated P&L since the last decision (weights x price change, minus the per-side cost on weight changes);
    updates book in place and returns the step's return."""
    r = sum(w * (prices[s] / book["px"][s] - 1) for s, w in book["w"].items() if prices.get(s) and book["px"].get(s))
    r -= sum(abs(weights.get(s, 0.0) - book["w"].get(s, 0.0)) * me.cost(s) for s in set(weights) | set(book["w"]))
    book["equity"] *= 1 + r
    book["w"], book["px"] = dict(weights), {s: prices[s] for s in weights if prices.get(s)}
    return r


class Engine:
    def __init__(self, stop_file: Path | None = None) -> None:
        self.stop_file = stop_file
        ALGO.mkdir(parents=True, exist_ok=True)
        k, s = key(KEYS[0]), key(KEYS[1])
        if not (k and s):
            raise SystemExit("no autopilot Alpaca keys in backend/.env")
        self.auth = {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s}
        self.http = httpx.AsyncClient(headers=self.auth, timeout=10,
                                      limits=httpx.Limits(max_connections=20, max_keepalive_connections=20))
        m = json.loads((ALGO / "model.json").read_text())
        self.model, self.model_result = me.Ridge.from_json(m), m.get("result")
        self.hist: dict[str, pd.DataFrame] = {}
        self.today: dict[str, list[dict[str, Any]]] = {s: [] for s in SYMBOLS}
        self.stats: dict[str, pd.DataFrame] = {}
        self.session: date | None = None
        self.done: set[int] = set()
        self.stopped_today = False
        self.breaker = False
        self.flat_done = False
        self.last_bar_wall = time.monotonic()
        self.book: dict[str, Any] = {"equity": 1.0, "w": {}, "px": {}}
        self.status: dict[str, Any] = {"state": "starting", "model_result": self.model_result}
        self.stop = False

    # --- plumbing -----------------------------------------------------------------------------------------------
    def halted(self) -> bool:
        return self.stop or (ALGO / "STOP").exists() or bool(self.stop_file and self.stop_file.exists())

    def mode(self) -> str:
        return "live" if (ALGO / "LIVE").exists() else "shadow"

    def log(self, kind: str, **kw: Any) -> None:
        with (ALGO / "events.jsonl").open("a") as f:
            f.write(json.dumps({"at": now_utc().isoformat(), "type": kind, **kw}, default=str) + "\n")

    def report(self, **kw: Any) -> None:
        self.status.update(kw, mode=self.mode(), updated_at=now_utc().isoformat(), shadow_equity=self.book["equity"])
        tmp = ALGO / "status.json.tmp"
        tmp.write_text(json.dumps(self.status, indent=2, default=str))
        tmp.replace(ALGO / "status.json")

    async def get(self, url: str, **params: Any) -> Any:
        for attempt in range(4):
            r = await self.http.get(url, params=params)
            if r.status_code == 429:
                await asyncio.sleep(1 + attempt)
                continue
            r.raise_for_status()
            return r.json()
        r.raise_for_status()

    async def bars(self, start: datetime, end: datetime, feed: str) -> dict[str, pd.DataFrame]:
        rows: dict[str, list[dict[str, Any]]] = {s: [] for s in SYMBOLS}
        params: dict[str, Any] = {"symbols": ",".join(SYMBOLS), "timeframe": "1Min", "start": start.isoformat(),
                                  "end": end.isoformat(), "feed": feed, "adjustment": "split", "limit": 10000}
        while True:
            d = await self.get(f"{DATA}/v2/stocks/bars", **params)
            for s, b in (d.get("bars") or {}).items():
                rows[s] += b
            if not d.get("next_page_token"):
                break
            params["page_token"] = d["next_page_token"]
        return {s: to_frame(r) for s, r in rows.items()}

    # --- session ------------------------------------------------------------------------------------------------
    async def open_session(self, day: date) -> None:
        """History (SIP, sessions before today) -> session stats; today's bars so far (IEX) as a backfill."""
        t0 = time.perf_counter()
        start = datetime.combine(day - timedelta(days=45), datetime.min.time(), NY)
        hist = await self.bars(start, datetime.combine(day, datetime.min.time(), NY), "sip")
        self.stats = {s: me.day_stats(hist[s], hist[me.leader_of(s)], day) for s in me.UNIVERSE}
        self.hist = {s: h[h["ts"].dt.date == h["ts"].dt.date.max()] for s, h in hist.items()}  # yesterday only
        so_far = await self.bars(datetime.combine(day, datetime.min.time(), NY), now_utc(), "iex")
        self.today = {s: so_far[s].to_dict("records") for s in SYMBOLS}
        self.session, self.done, self.stopped_today, self.breaker, self.flat_done = day, set(), False, False, False
        self.book = {"equity": self.book["equity"], "w": {}, "px": {}}
        self.log("session", day=day, setup_s=round(time.perf_counter() - t0, 2),
                 sigma={s: float(v["sigma"].iloc[0]) for s, v in self.stats.items()})

    def frame_now(self, s: str) -> pd.DataFrame:
        today = pd.DataFrame(self.today[s]) if self.today[s] else self.hist[s].iloc[:0]
        lead = me.leader_of(s)
        lt = pd.DataFrame(self.today[lead]) if self.today[lead] else self.hist[lead].iloc[:0]
        return me.frame(pd.concat([self.hist[s], today]), pd.concat([self.hist[lead], lt]), stats=self.stats[s])

    def predict(self, m: int) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
        pred, sigma, prices = {}, {}, {}
        for s in me.UNIVERSE:
            if not self.today[s]:
                continue
            f = self.frame_now(s)
            row = f[(f["m"] == m) & (f["date"] == self.session)]
            if row.empty or not np.isfinite(row[list(me.FEATURES)].to_numpy()).all():
                continue
            pred[s] = float(self.model.predict(row[list(me.FEATURES)].to_numpy())[0])
            sigma[s] = float(row["sigma"].iloc[0])
            prices[s] = float(self.today[s][-1]["close"])
        return pred, sigma, prices

    # --- decisions ----------------------------------------------------------------------------------------------
    async def decide(self, m: int, t_bar: float, flat: bool = False) -> None:
        cfg = config()
        pred, sigma, prices = self.predict(m)
        closing = flat or m >= FLAT_AT or m + 1 >= minute(cfg["end"]) or self.stopped_today
        weights = {} if closing else me.targets(pred, sigma, cfg["gross"])
        acct = await self.get(f"{PAPER}/account")
        equity, last = float(acct["equity"]), float(acct.get("last_equity") or acct["equity"])
        if equity < last * (1 - cfg["daily_stop"]) and not self.stopped_today:
            self.stopped_today, weights = True, {}
            self.log("daily_stop", equity=equity, last_equity=last)
        if equity < last * (1 - cfg["breaker"]) and not self.breaker:
            self.breaker = True
            self.log("breaker", equity=equity, last_equity=last)
        if self.breaker:
            weights = no_new_risk(weights, self.book["w"])
        t_sig = time.perf_counter()
        r = shadow_step(self.book, weights, prices)
        sent: list[dict[str, Any]] = []
        if self.mode() == "live":
            sent = await self.trade(weights, prices, equity, cfg)
        t_done = time.perf_counter()
        lat = {"signal_ms": round((t_sig - t_bar) * 1000, 1), "to_orders_ms": round((t_done - t_bar) * 1000, 1)}
        self.log("decision", m=m, mode=self.mode(), weights=weights, step_return=r, latency=lat, orders=sent,
                 top=sorted(((round(p * sigma[s] * 1e4, 2), s) for s, p in pred.items()), reverse=True)[:3])
        self.report(state="running", last_decision=now_utc().isoformat(), bar=m, weights=weights, latency=lat,
                    decisions_today=sum(1 for x in self.done if in_window(x, cfg)),
                    expected_decisions=sum(1 for x in me.DECISION_BARS if in_window(x, cfg)),
                    window={"start": cfg["start"], "end": cfg["end"]},
                    breaker="account down 4%+ today: nothing new" if self.breaker else None,
                    message=("flat (" + ("daily stop" if self.stopped_today else "window end" if closing else "") + ")")
                    if closing else f"{len(weights)} positions; signal {lat['signal_ms']} ms after the bar")

    async def trade(self, weights: dict[str, float], prices: dict[str, float], equity: float,
                    cfg: dict[str, Any]) -> list[dict[str, Any]]:
        positions = await self.get(f"{PAPER}/positions")
        held = {p["symbol"]: abs(float(p["qty"])) * (-1 if p.get("side") == "short" else 1)
                for p in positions if p["symbol"] in me.UNIVERSE}
        other = sum(abs(float(p["market_value"])) for p in positions if p["symbol"] not in me.UNIVERSE)
        room = max(0.0, cfg["account_gross_cap"] * equity - other) / equity
        scale = min(1.0, room / max(sum(abs(w) for w in weights.values()), 1e-9))
        orders = share_orders({s: w * scale for s, w in weights.items()}, held, equity, prices)

        async def send(o: dict[str, Any]) -> dict[str, Any]:
            t = time.perf_counter()
            body = {"symbol": o["symbol"], "qty": str(int(o["qty"])), "side": o["side"], "type": "market",
                    "time_in_force": "day", "client_order_id": f"algo-{int(time.time() * 1000)}-{o['symbol']}"}
            r = await self.http.post(f"{PAPER}/orders", json=body)
            return {**o, "http": r.status_code, "ack_ms": round((time.perf_counter() - t) * 1000, 1),
                    "error": None if r.is_success else r.text[:200]}

        reduce = [o for o in orders if abs(o["to"]) < abs(held.get(o["symbol"], 0.0))]
        add = [o for o in orders if o not in reduce]
        out = list(await asyncio.gather(*map(send, reduce)))
        out += list(await asyncio.gather(*map(send, add)))
        return out

    # --- main loop ----------------------------------------------------------------------------------------------
    async def run(self) -> None:
        while not self.halted():
            try:
                clock = await self.get(f"{PAPER}/clock")
                if not clock["is_open"]:
                    self.report(state="waiting", message=f"market closed; next open {clock['next_open']}")
                    await asyncio.sleep(30)
                    continue
                await self.stream(now_utc().astimezone(NY).date())
            except Exception as e:  # noqa: BLE001 - keep the engine up; every failure is logged
                self.log("error", error=repr(e)[:300])
                self.report(state="error", last_error=repr(e)[:300], last_error_at=now_utc().isoformat())
                await asyncio.sleep(5)
        if self.mode() == "live" and self.book["w"] is not None:
            try:
                positions = await self.get(f"{PAPER}/positions")
                for p in positions:
                    if p["symbol"] in me.UNIVERSE:
                        await self.http.delete(f"{PAPER}/positions/{p['symbol']}")
                        self.log("close_on_stop", symbol=p["symbol"], qty=p["qty"])
            except (httpx.HTTPError, KeyError) as e:
                self.log("error", error=f"closing on stop: {e!r}"[:300])
        await self.http.aclose()
        self.report(state="stopped", message="stopped")

    async def stream(self, day: date) -> None:
        if self.session != day:
            await self.open_session(day)
        async with websockets.connect(STREAM, max_size=2**22, ping_interval=15) as ws:
            await ws.send(json.dumps({"action": "auth", "key": self.auth["APCA-API-KEY-ID"],
                                      "secret": self.auth["APCA-API-SECRET-KEY"]}))
            await ws.send(json.dumps({"action": "subscribe", "bars": list(SYMBOLS)}))
            self.report(state="running", message="streaming bars")
            pending: tuple[int, float] | None = None
            while not self.halted():
                timeout = max(0.05, pending[1] + WAIT_S - time.perf_counter()) if pending else 30
                try:
                    msgs = json.loads(await asyncio.wait_for(ws.recv(), timeout))
                except TimeoutError:
                    msgs = []
                for b in msgs:
                    if b.get("T") == "error":
                        raise RuntimeError(f"stream: {b}")
                    if b.get("T") != "b" or b.get("S") not in self.today:
                        continue
                    ts = pd.Timestamp(b["t"]).tz_convert(NY)
                    if ts.date() != day:
                        continue
                    self.today[b["S"]].append({"ts": ts, "open": b["o"], "high": b["h"], "low": b["l"],
                                               "close": b["c"], "volume": b["v"]})
                    m = ts.hour * 60 + ts.minute - 570
                    self.last_bar_wall = time.monotonic()
                    cfg = config()
                    stale = (now_utc() - ts.tz_convert(UTC).to_pydatetime()).total_seconds() - 60 > cfg["stale_skip_s"]
                    if stale:  # R3: a late bar is not traded on
                        self.report(stale=f"bar {ts:%H:%M} arrived late; skipped")
                        continue
                    due = m in me.DECISION_BARS and in_window(m, cfg)
                    end = m + 1 >= minute(cfg["end"]) or m >= FLAT_AT
                    if (due or (end and not self.flat_done)) and pending is None and m not in self.done:
                        pending = (m, time.perf_counter())
                if pending:
                    m, t_bar = pending
                    have = all(self.today[s] and self.today[s][-1]["ts"].hour * 60 + self.today[s][-1]["ts"].minute
                               - 570 >= m for s in me.UNIVERSE)
                    if have or time.perf_counter() - t_bar >= WAIT_S:
                        self.done.add(m)
                        pending = None
                        cfg = config()
                        if m + 1 >= minute(cfg["end"]) or m >= FLAT_AT:
                            self.flat_done = True
                        await self.decide(m, t_bar)
                cfg = config()
                quiet = time.monotonic() - self.last_bar_wall
                if quiet > cfg["stale_flat_s"] and self.book["w"]:  # R3: no data for 10 minutes: flat
                    self.log("stale_flat", quiet_s=round(quiet))
                    self.report(stale=f"no bars for {quiet / 60:.0f} min: flat")
                    await self.decide(max(self.done, default=0), time.perf_counter(), flat=True)
                if now_utc().astimezone(NY).hour >= 16:
                    return


def run_job(job: Path) -> int:
    """The app's Autopilot Day button (desktop_run.py autopilot_day): one engine at a time, stopped by job/STOP;
    on the way out the algo book is closed so nothing is held overnight."""
    import fcntl

    import desktop_run as worker
    status_path = job / "status.json"
    js = json.loads(status_path.read_text())
    js.update(state="running", pid=os.getpid(), identity=worker.identity(os.getpid()),
              started_at=now_utc().isoformat(), steps=[], message="Autopilot Day running")
    status_path.write_text(json.dumps(js) + "\n")
    ALGO.mkdir(parents=True, exist_ok=True)
    with (ALGO / "engine.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            js.update(state="blocked", message="Autopilot Day is already running.")
            status_path.write_text(json.dumps(js) + "\n")
            return 2
        eng = Engine(stop_file=job / "STOP")
        asyncio.run(eng.run())
    js = json.loads(status_path.read_text())
    js.update(state="stopped", message="Autopilot Day stopped; the algo book was closed",
              finished_at=now_utc().isoformat())
    status_path.write_text(json.dumps(js) + "\n")
    return 0


def main() -> None:
    eng = Engine()
    loop = asyncio.new_event_loop()

    def halt(*_: Any) -> None:
        eng.stop = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, halt)
    loop.run_until_complete(eng.run())


if __name__ == "__main__":
    main()
