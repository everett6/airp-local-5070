"""Full autopilot (docs/FULL_AUTOPILOT.md): one button researches 150 technology companies with Jan and Bonsai, turns
their bull and bear calls into day and swing trades, and runs them in a SEPARATE Alpaca paper account.

    python scripts/full_auto.py universe          # the 150 companies (frozen on first use)
    python scripts/full_auto.py view              # everything the app's Autopilot page shows (JSON)
    python scripts/full_auto.py reasoning TICKER  # the latest full reasoning record for one company
    (the app starts the controller through desktop_run.py: action "full_auto")

User-requested paper experiment (4 Oct 2026). Not a registered trial, no go-live gate, no register() call. Every
day-trading rule tested so far failed (docs/PLAN_60_V2.md); this book trades the AI's own calls and its results
are kept apart. The main paper account, the frozen books and the scheduled jobs are untouched:
- the account keys are AIRP_AUTO_ALPACA_KEY_ID / AIRP_AUTO_ALPACA_SECRET_KEY in backend/.env (the user creates the
  paper account and fills them in); the same account as the main mirror is refused; without keys the controller
  researches and logs what it would trade, and sends nothing;
- research runs on the GPU only under the scheduler lock with the scheduled jobs' room kept (desktop_run.next_slot);
  trading keeps going while research runs;
- 35% below the starting equity everything is closed and trading stops (KILLED file; delete it to resume).
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import httpx

from app.data_ingestion.bars import key
from app.forward.ledger import Ledger, jsonl_records, write_atomic
from app.portfolio import account_book as ab
from app.portfolio import auto_trader as at
from app.portfolio import fund_stability as fs
from app.portfolio.broker import DATA, PAPER, Alpaca, BrokerError
from app.sandbox import minute_ensemble as me
from app.sandbox import research_memory as rm

NY = ZoneInfo("America/New_York")
FWD = BACKEND / "results" / "forward"
DIR = FWD / "full_auto"
RESEARCH = FWD / "deep_research"
CONFIG = BACKEND / "config" / "auto_trader.json"
KEYS = ("AIRP_AUTO_ALPACA_KEY_ID", "AIRP_AUTO_ALPACA_SECRET_KEY")
# Large technology companies the sector list files elsewhere (internet, media, cars, payments).
TECH_EXTRA = ("GOOGL", "META", "AMZN", "NFLX", "TSLA", "UBER", "ABNB", "DASH", "EBAY", "PYPL", "EA", "TTWO", "MTCH",
              "BKNG", "EXPE", "CHTR", "TMUS")
SANDBOX_RISK = ab.SANDBOX_RISK  # one policy for the whole account (app/portfolio/account_book.py)
ROUND_TRIP = 0.001       # O1 #33: K1 Kelly on returns net of a 10 bp round trip
PARTICIPATION = 0.01     # O1 #15: an order at most 1% of the stock's average daily dollar volume
BETA_NET_CAP = 0.30      # O1 #14: beta-weighted net after the hedge
PRICED_IN_ATR = 1.5      # O1 #27: no entry after a move of this many ATRs in the call's direction
STALE_DAY_S = 180        # O1 #21: Day's heartbeat older than this inside its window: the watchdog flattens
# Autopilot Day (scripts/algo_engine.py) owns these ETFs in this account; QQQ stays with the N1 hedge.
ALGO_SYMBOLS = frozenset(me.UNIVERSE)
BREAKER = 0.04  # R2
BUDGET_NEW_S, BUDGET_MEMORY_S = 480, 300  # M1: research minutes a company, first time / with saved memory
REFRESH_H = 20.0       # research older than this is redone
BATCH = 4              # companies per research run (Jan and Bonsai load once per run)
RESEARCH_ROOM_S = 1800  # the research script's own preflight room before the next scheduled job
# One risk snapshot per pass (about 6 requests) plus one request per order; Alpaca allows 200 a minute.
ORDERS_PER_LOOP = 35
PASS_S = {"open": 10, "backlog": 15, "closed": 30}  # seconds between passes


def now_utc() -> datetime:
    return datetime.now(UTC)


def config() -> at.Config:
    return at.Config.from_dict(json.loads(CONFIG.read_text())) if CONFIG.exists() else at.Config()


def load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(data, indent=1, default=str) + "\n")


# ---------------------------------------------------------------- universe and research records

def tech_universe(members: Path, prices: Any, n: int = 150) -> list[dict[str, Any]]:
    """The n most traded technology companies of the latest cached membership year: the Information Technology
    sector plus TECH_EXTRA, ranked by median dollar volume over the last 60 sessions (liquid names day-trade best)."""
    rows = list(csv.DictReader(members.read_text().splitlines()))
    year = max(int(r["year"]) for r in rows)
    pool: dict[str, dict[str, Any]] = {}
    for r in rows:
        if int(r["year"]) == year and r["cik"] and (r["sector"] == "Information Technology" or r["ticker"] in TECH_EXTRA):
            pool.setdefault(r["ticker"], {"ticker": r["ticker"], "name": r["name"], "cik": r["cik"], "sector": r["sector"]})
    px = prices[prices["Ticker"].isin(list(pool))].copy()
    px["dv"] = px["Close"] * px["Volume"]
    last = sorted(px["Date"].unique())[-60:]
    dv = px[px["Date"].isin(last)].groupby("Ticker")["dv"].median().sort_values(ascending=False)
    return [{**pool[t], "dollar_volume": round(float(v))} for t, v in dv.head(n).items()]


def universe() -> list[dict[str, Any]]:
    path = DIR / "universe.json"
    got = load(path, None)
    if got:
        return list(got["companies"])
    import pandas as pd
    data = max((BACKEND / "data" / "events").glob("ohlcv_*.parquet"))
    companies = tech_universe(BACKEND / "data" / "events" / "members_2024_2026.csv", pd.read_parquet(data))
    save(path, {"companies": companies, "created_at": now_utc().isoformat(), "prices": data.name,
                "rule": "Information Technology + named internet/tech companies, top 150 by 60-session median dollar volume"})
    return companies


def themes() -> dict[str, dict[str, Any]]:
    """{ticker: {"themes": [...], "horizons": [...]}} from config/themes_book.json (the user's themes)."""
    out: dict[str, dict[str, Any]] = {}
    for name, t in load(BACKEND / "config" / "themes_book.json", {}).get("themes", {}).items():
        for tick in t["stocks"]:
            x = out.setdefault(tick, {"themes": [], "horizons": []})
            x["themes"].append(name)
            x["horizons"] = sorted(set(x["horizons"]) | set(t["horizons"]))
    return out


def research_list() -> list[dict[str, Any]]:
    """The 150 technology companies plus every theme stock not among them."""
    companies = universe()
    have = {c["ticker"] for c in companies}
    for t in load(BACKEND / "config" / "themes_book.json", {}).get("themes", {}).values():
        for tick, name in t["stocks"].items():
            if tick not in have:
                have.add(tick)
                companies.append({"ticker": tick, "name": name, "sector": "theme", "cik": ""})
    return companies


_SCAN: dict[str, tuple[float, dict[str, Any] | None]] = {}


def decisions(names: set[str], root: Path = RESEARCH) -> dict[str, dict[str, Any]]:
    """The latest decided research record per ticker (the per-company files of every deep-research run)."""
    best: dict[str, dict[str, Any]] = {}
    for f in root.glob("*/*.json"):
        if f.stem not in names:
            continue
        try:
            m = f.stat().st_mtime
        except OSError:
            continue
        cached = _SCAN.get(str(f))
        if cached is None or cached[0] != m:
            r = load(f, {})
            rec = None
            if r.get("status") == "decided" and r.get("decided_at") and isinstance(r.get("ratings"), dict):
                rec = {"ticker": f.stem, "decided_at": r["decided_at"], "ratings": r["ratings"], "evidence": str(f),
                       "primary": r.get("primary"),
                       "support": {h: v.get("support", "none") for h, v in (r.get("call_support") or {}).items()
                                   if isinstance(v, dict)} or None}
            _SCAN[str(f)] = cached = (m, rec)
        rec = cached[1]
        if rec and (f.stem not in best or rec["decided_at"] > best[f.stem]["decided_at"]):
            best[f.stem] = rec
    return best


def reasoning(path: Path) -> dict[str, Any]:
    """The models' reasoning for one decision: Jan's research steps (its stated thought, the tools it called, its
    estimate), the checked facts, and Bonsai's ratings with the quote and the reasons it gave per horizon."""
    r = load(path, {})
    steps = []
    for call in r.get("llm_calls", []):
        try:
            obj = json.loads(call.get("reply", ""))
        except ValueError:
            obj = None
        if not isinstance(obj, dict):
            continue
        if not {"thought", "actions", "final"} & set(obj):  # page readers' facts and the price snapshot
            continue
        step: dict[str, Any] = {"thought": str(obj.get("thought", ""))[:1200]}
        if isinstance(obj.get("actions"), list):
            step["tools"] = [f"{a.get('tool')}({json.dumps(a.get('args', {}))[:160]})" for a in obj["actions"] if isinstance(a, dict)]
        if isinstance(obj.get("final"), dict):
            step["estimate"] = {k: obj["final"].get(k) for k in ("p_up", "reason") if k in obj["final"]}
        steps.append(step)
    judge: dict[str, Any] = {}
    try:
        raw = json.loads(str(r.get("judge_reply", "")).strip().removeprefix("```json").removesuffix("```").strip())
    except ValueError:
        raw = {}
    for h, v in (raw.items() if isinstance(raw, dict) else []):
        if isinstance(v, dict):
            judge[h] = {"label": v.get("label"), "quote": v.get("quote", ""), "why": v.get("why", ""),
                        "risk": v.get("invalidation_quote", "")}
    for h, why in (r.get("reasons") or {}).items():
        judge.setdefault(h, {})["why"] = why
    return {"ticker": r.get("ticker"), "decided_at": r.get("decided_at"), "ratings": r.get("ratings"),
            "checked": r.get("checked_labels"), "jan_steps": steps,
            "facts": [f.get("text") for f in (r.get("wide_brief") or r.get("brief") or {}).get("facts", [])
                      if isinstance(f, dict)],
            "sources": r.get("article_domains", []), "bonsai": judge,
            "tools_used": [t.get("tool") for t in r.get("tool_log", []) if isinstance(t, dict)],
            "bull_case": r.get("bull_case"), "bear_case": r.get("bear_case"), "primary": r.get("primary"),
            "double_check": {k: (r.get("double_check") or {}).get(k) for k in ("draft", "changed", "s")}
            if r.get("double_check") else None,
            "research_s": r.get("research_s"), "judge_s": r.get("judge_s"), "evidence": str(path)}


def research_due(companies: list[dict[str, Any]], latest: dict[str, dict[str, Any]], attempts: dict[str, list[str]],
                 now: datetime) -> list[dict[str, Any]]:
    """Companies to research next: never decided first, then the stalest; two failures in 24 hours rest a company."""
    due = []
    for c in companies:
        recent_fails = [t for t in attempts.get(c["ticker"], []) if now - datetime.fromisoformat(t) < timedelta(hours=24)]
        if len(recent_fails) >= 2:
            continue
        rec = latest.get(c["ticker"])
        age = (now - datetime.fromisoformat(rec["decided_at"])).total_seconds() / 3600 if rec else 1e9
        if age >= REFRESH_H:
            due.append((age, c))
    return [c for _, c in sorted(due, key=lambda x: -x[0])]


def research_all_active(now: datetime, path: Path = FWD / "research_all" / "progress.json") -> bool:
    """True while scripts/research_all.py keeps the research fresh (it then owns the GPU and the research list)."""
    p = load(path, {})
    try:
        age = (now - datetime.fromisoformat(p["updated_at"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return False
    return p.get("state") in ("running", "waiting") and age < 3 * 3600


# ---------------------------------------------------------------- the separate paper account

class Sandbox:
    """The autopilot's own Alpaca paper account (separate keys, its own audit trail and risk limits)."""

    def __init__(self, client: Alpaca):
        self.a = client
        self._shortable: dict[str, bool] = {}

    @classmethod
    def from_env(cls) -> Sandbox | None:
        k, s = key(KEYS[0]), key(KEYS[1])
        if not (k and s):
            return None
        return cls(Alpaca(k, s, PAPER, risk_policy=SANDBOX_RISK, audit_path=DIR / "execution"))

    def get(self, url: str, **params: Any) -> Any:
        return self.a._get(url, **params)

    def account(self) -> dict[str, Any]:
        return self.a.account()

    def clock(self) -> dict[str, Any]:
        return dict(self.get(f"{PAPER}/clock"))

    def held(self) -> tuple[dict[str, float], dict[str, float], dict[str, float], set[str]]:
        """(positions, positions plus open orders, average entry prices, symbols with a working order), signed:
        shorts are negative."""
        pos, entry = {}, {}
        for p in self.get(f"{PAPER}/positions"):
            if p["symbol"] in ALGO_SYMBOLS:  # the algo engine's book; this controller leaves it alone
                continue
            q = abs(float(p["qty"])) * (-1 if p.get("side") == "short" else 1)
            pos[p["symbol"]] = q
            entry[p["symbol"]] = float(p.get("avg_entry_price") or 0)
        total, working = dict(pos), set()
        for o in self.get(f"{PAPER}/orders", status="open", limit=500):
            if o["symbol"] in ALGO_SYMBOLS:
                continue
            left = float(o.get("qty") or 0) - float(o.get("filled_qty") or 0)
            total[o["symbol"]] = total.get(o["symbol"], 0.0) + (left if o["side"] == "buy" else -left)
            working.add(o["symbol"])
        return pos, total, entry, working

    def prices(self, symbols: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for i in range(0, len(symbols), 100):
            chunk = [s.replace("-", ".") for s in symbols[i:i + 100]]
            t = self.get(f"{DATA}/v2/stocks/trades/latest", symbols=",".join(chunk), feed="iex")["trades"]
            out.update({s.replace(".", "-"): float(v["p"]) for s, v in t.items()})
        return out

    def closes(self, sym: str, days: int = 120) -> list[float]:
        start = (now_utc() - timedelta(days=days)).date().isoformat()
        bars = self.get(f"{DATA}/v2/stocks/bars", symbols=sym, timeframe="1Day", start=start, feed="iex", limit=1000)
        return [float(b["c"]) for b in (bars.get("bars") or {}).get(sym, [])]

    def daily_closes(self, symbols: list[str], days: int = 200) -> dict[str, list[float]]:
        """Daily IEX closes per symbol (oldest first), paginated."""
        start = (now_utc() - timedelta(days=days)).date().isoformat()
        out: dict[str, list[float]] = {}
        for i in range(0, len(symbols), 50):
            chunk = ",".join(s.replace("-", ".") for s in symbols[i:i + 50])
            token = None
            while True:
                params: dict[str, Any] = {"symbols": chunk, "timeframe": "1Day", "start": start, "feed": "iex",
                                          "limit": 10000}
                if token:
                    params["page_token"] = token
                got = self.get(f"{DATA}/v2/stocks/bars", **params)
                for s, bars in (got.get("bars") or {}).items():
                    out.setdefault(s, []).extend(float(b["c"]) for b in bars)
                token = got.get("next_page_token")
                if not token:
                    break
        return out

    def shortable(self, sym: str) -> bool:
        if sym not in self._shortable:
            try:
                a = self.get(f"{PAPER}/assets/{sym.replace('-', '.')}")
                self._shortable[sym] = bool(a.get("tradable") and a.get("shortable") and a.get("easy_to_borrow"))
            except BrokerError:
                self._shortable[sym] = False
        return self._shortable[sym]

    def fills(self, after: str | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"activity_types": "FILL", "direction": "asc", "page_size": 100}
        if after:
            params["after"] = after
        return list(self.get(f"{PAPER}/account/activities", **params))

    def history(self, period: str, timeframe: str) -> dict[str, Any]:
        h = self.get(f"{PAPER}/account/portfolio/history", period=period, timeframe=timeframe, extended_hours="false")
        return {k: h.get(k) for k in ("timestamp", "equity", "profit_loss", "base_value")}

    def close_everything(self) -> None:
        r = self.a.c.delete(f"{PAPER}/positions", params={"cancel_orders": "true"})
        if r.status_code not in (200, 207):
            raise BrokerError(f"close all: HTTP {r.status_code}")


def why_none(rec: dict[str, Any], lots: list[dict[str, Any]], ticker: str) -> str:
    """O1 #43: why a decided call made no new lot."""
    sided = {h: v for h, v in (rec.get("ratings") or {}).items() if v != 3}
    if not sided:
        return "PASS: the judge gave no side on any horizon"
    if any(x["symbol"] == ticker and x.get("decided_at") == rec.get("decided_at") for x in lots):
        return "already planned from this research (open or finished)"
    if rec.get("theme_horizons") and not set(sided) & set(rec["theme_horizons"]):
        return f"theme rule: this stock trades only on {', '.join(rec['theme_horizons'])}, which got no side"
    return "sizing rules gave 0 (weak support or against the market regime)"


def unstrong(lots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """O1 #13: undo the x2 for "strong" ratings (1 and 5), then rescale so the open book's gross is unchanged."""
    before = sum(x["weight"] for x in lots if x["state"] == "open")
    out = [{**x, "weight": x["weight"] / 2 if x.get("rating") in (1, 5) else x["weight"]} for x in lots]
    after = sum(x["weight"] for x in out if x["state"] == "open")
    k = before / after if after > 0 else 1.0
    return [{**x, "weight": x["weight"] * k} for x in out]


def beta_cap(w: dict[str, float], betas: dict[str, float], cap: float) -> dict[str, float]:
    """O1 #14: beta-weighted net after the hedge within +/-cap; if the hedge legs are capped out, the stocks scale."""
    stocks = {s: x for s, x in w.items() if s not in fs.BASKET}
    hedge = sum(x for s, x in w.items() if s in fs.BASKET)
    sb = sum(x * betas.get(s, 1.0) for s, x in stocks.items())
    net = sb + hedge
    if abs(net) <= cap or sb == 0:
        return w
    f = (math.copysign(cap, net) - hedge) / sb
    return {s: (x * f if s in stocks else x) for s, x in w.items()}


def participation(orders: list[dict[str, Any]], prices: dict[str, float], adv: dict[str, float]) -> list[dict[str, Any]]:
    """O1 #15: an opening or growing order at most PARTICIPATION of the stock's average daily dollar volume."""
    out = []
    for o in orders:
        cap_usd = PARTICIPATION * adv.get(o["symbol"], 0.0)
        px = prices.get(o["symbol"], 0.0)
        if not o["reducing"] and cap_usd > 0 and px > 0 and o["qty"] * px > cap_usd:
            o = {**o, "qty": int(cap_usd / px), "capped": "1% of daily dollar volume"}
            if o["qty"] < 1:
                continue
        out.append(o)
    return out


def controls(lot: dict[str, Any], mom: int) -> list[dict[str, Any]]:
    """O1 #38: a seeded random side and a 20-day momentum side for the same stock, size and dates (shadow only)."""
    seed = int(hashlib.sha256(f"{lot['symbol']}{lot['decided_at']}{lot['horizon']}".encode()).hexdigest()[:8], 16)
    base = {k: lot[k] for k in ("symbol", "horizon", "weight", "decided_at", "session", "exit_session")
            if k in lot}
    return [{**base, "kind": "random", "side": 1 if seed % 2 else -1, "ai_side": lot["side"]},
            {**base, "kind": "momentum", "side": mom, "ai_side": lot["side"]}]


def day_running() -> bool:
    """Autopilot Day holds results/forward/algo/engine.lock while it runs."""
    p = FWD / "algo" / "engine.lock"
    if not p.exists():
        return False
    with p.open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
        return False


def same_account(sandbox: Sandbox) -> bool:
    """True when the autopilot keys point at the main mirror's account (refused: the books must stay apart)."""
    main = Alpaca.from_env()
    if main is None:
        return False
    try:
        return bool(main._get(f"{PAPER}/account").get("account_number") == sandbox.get(f"{PAPER}/account").get("account_number"))
    finally:
        main.c.close()


# ---------------------------------------------------------------- the controller

class Controller:
    def __init__(self, job: Path):
        self.job = job
        self.cfg = config()
        self.state: dict[str, Any] = load(DIR / "state.json", {"lots": [], "seen": [], "attempts": {}})
        self.state.setdefault("lots", [])
        self.state.setdefault("seen", [])
        self.state.setdefault("attempts", {})
        self.companies = research_list()
        self.names = {c["ticker"] for c in self.companies}
        self.sandbox: Sandbox | None = None
        self.child: subprocess.Popen[bytes] | None = None
        self.child_lock: Any = None
        self.child_batch: list[str] = []
        self.child_deadline = 0.0
        self.status: dict[str, Any] = {}
        self.thoughts = Ledger(DIR / "thoughts.jsonl")
        self.events = Ledger(DIR / "events.jsonl")
        # -inf, not 0: time.monotonic() counts from boot, so 0 held every timer back for its interval after a reboot
        self.last = {"equity": float("-inf"), "fills": float("-inf"), "regime": float("-inf"), "keys": float("-inf")}
        self.mkt = "unknown"
        self.backlog = False
        self.market_open = False
        self.reconciled = False
        self.adv = {c["ticker"].replace("-", "."): float(c.get("dollar_volume") or 0) for c in self.companies}
        bad = ab.ceiling_violations(SANDBOX_RISK, max_drawdown=self.cfg.max_drawdown)
        if bad:  # O1 #40: never trade on limits looser than the hard ceilings
            raise SystemExit("risk limits above the hard ceilings: " + "; ".join(bad))

    # -- bookkeeping
    def persist(self) -> None:
        self.state["seen"] = self.state["seen"][-5000:]
        self.state["lots"] = [x for x in self.state["lots"] if x["state"] == "open"] + \
            [x for x in self.state["lots"] if x["state"] != "open"][-1000:]
        save(DIR / "state.json", self.state)

    def report(self, **changes: Any) -> None:
        self.status.update(changes, updated_at=now_utc().isoformat())
        save(DIR / "status.json", self.status)
        js = load(self.job / "status.json", {})
        js.update(message=self.status.get("message", ""), progress=self.status.get("research"))
        save(self.job / "status.json", js)

    def log(self, kind: str, **fields: Any) -> None:
        self.events.append(kind, at=now_utc().isoformat(), **fields)
        print(f"{datetime.now(NY):%H:%M:%S} {kind} {json.dumps(fields, default=str)[:300]}", flush=True)

    # -- research (a child process; trading continues while it runs)
    def research_tick(self, now: datetime) -> None:
        import desktop_run as worker
        if self.child is not None:
            if self.child.poll() is None and time.monotonic() < self.child_deadline:
                return
            if self.child.poll() is None:
                os.killpg(self.child.pid, signal.SIGTERM)
                try:
                    self.child.wait(30)
                except subprocess.TimeoutExpired:
                    os.killpg(self.child.pid, signal.SIGKILL)
                    self.child.wait()
            rc = self.child.returncode
            latest = decisions(self.names)
            for t in self.child_batch:
                rec = latest.get(t)
                if not rec or datetime.fromisoformat(rec["decided_at"]) < self.child_started:
                    self.state["attempts"].setdefault(t, []).append(now.isoformat())
            self.log("research_done", tickers=self.child_batch, code=rc)
            self.status.setdefault("research", {}).pop("current", None)
            self.child_lock.close()
            self.child, self.child_lock, self.child_batch = None, None, []
            self.persist()
            return
        room = (worker.next_slot(now) - now).total_seconds()
        if room < RESEARCH_ROOM_S + 120 or (DIR / "KILLED").exists() or research_all_active(now):
            return
        due = research_due(self.companies, decisions(self.names), self.state["attempts"], now)
        self.status["research"] = {"due": len(due), "total": len(self.companies),
                                   "memory": sum(1 for c in self.companies if (rm.ROOT / f"{c['ticker']}.json").exists())}
        if not due:
            return
        lock = (FWD / "autorun.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return
        batch = due[:BATCH if room >= 2700 + 1800 else 2]
        names = {c["ticker"]: c["name"] for c in batch}
        budgets = {t: BUDGET_MEMORY_S if rm.load(t)["facts"] else BUDGET_NEW_S for t in names}
        cmd = [sys.executable, "-u", "scripts/live_research_test.py", "--deep", "--company-names-json",
               json.dumps(names), "--tickers", ",".join(names), "--budgets-json", json.dumps(budgets),
               "--peers-json", json.dumps({t: self.peers(t) for t in names})]
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "AIRP_AUTORUN_LOCK_FD": str(lock.fileno())}
        out = (self.job / "research.log").open("ab")
        self.child = subprocess.Popen(cmd, cwd=BACKEND, env=env, stdout=out, stderr=out, start_new_session=True,
                                      pass_fds=(lock.fileno(),))
        out.close()
        self.child_lock, self.child_batch, self.child_started = lock, list(names), now
        span = min(room - 300, sum(budgets.values()) + 300 * len(batch) + 600)  # research + Bonsai + model loads
        self.child_deadline = time.monotonic() + span
        self.status["research"] = {**self.status.get("research", {}), "current": {
            "tickers": list(names), "started_at": now.isoformat(), "deadline_at": (now + timedelta(seconds=span)).isoformat(),
            "budget_min": round(sum(budgets.values()) / 60 / len(budgets), 1)}}
        self.log("research_start", tickers=list(names), due=len(due), budgets=budgets)

    def peers(self, ticker: str) -> list[str]:
        """M1 area note: the stock's theme mates, else the companies of its sector."""
        th = themes().get(ticker, {}).get("themes")
        if th:
            return sorted({t for t, v in themes().items() if set(v["themes"]) & set(th)} - {ticker})
        sector = next((c.get("sector") for c in self.companies if c["ticker"] == ticker), None)
        return [c["ticker"] for c in self.companies if c.get("sector") == sector and c["ticker"] != ticker][:60]

    def set_op(self, state: str, reason: str) -> None:
        """O1 #16: Night's named operating state, logged with its reason."""
        ab.Book().state("night", state, reason)
        self.report(op_state=state, op_reason=reason, op_allows=ab.STATES[state])

    def no_trade(self, ticker: str, rec: dict[str, Any], reasons: list[str]) -> None:
        """O1 #43: why a decided call did not become a trade (last 300 companies)."""
        why = self.state.setdefault("why_no_trade", {})
        why[ticker] = {"at": now_utc().isoformat(), "decided_at": rec.get("decided_at"), "reasons": reasons[:6]}
        if len(why) > 300:
            for k in sorted(why, key=lambda k: why[k]["at"])[:-300]:
                del why[k]

    def closes(self, ticker: str) -> list[float]:
        day = now_utc().astimezone(NY).date().isoformat()
        cache = self.__dict__.setdefault("_closes", {})
        if cache.get(ticker, (None,))[0] != day and self.sandbox is not None:
            try:
                cache[ticker] = (day, self.sandbox.daily_closes([ticker], days=45).get(ticker.replace("-", "."), []))
            except (BrokerError, httpx.HTTPError):
                cache[ticker] = (day, [])
        return cache.get(ticker, (day, []))[1]

    def priced_in(self, ticker: str, side: int, rec: dict[str, Any]) -> bool:
        """O1 #27: the move since the research (last close before it vs the latest close), in ATRs, in the call's
        direction. Unknown ATR or prices: not gated."""
        atr, c = rec.get("atr"), self.closes(ticker)
        if not atr or len(c) < 2:
            return False
        return side * (c[-1] / c[-2] - 1) > PRICED_IN_ATR * atr

    def momentum(self, ticker: str) -> int:
        c = self.closes(ticker)
        return 1 if len(c) > 21 and c[-1] >= c[-21] else -1

    def watch_day(self, session: date) -> None:
        """O1 #21: an independent watchdog for Autopilot Day. If Day holds positions inside its window but its
        heartbeat is stale (or it is not running after its window), flatten Day's symbols (reduce-only)."""
        if self.sandbox is None or not self.market_open:
            return
        st = load(FWD / "algo" / "status.json", {})
        beat = st.get("updated_at")
        age = (now_utc() - datetime.fromisoformat(beat)).total_seconds() if beat else 1e9
        running = day_running()
        cfg = load(BACKEND / "config" / "autopilot_day.json", {})
        ny = now_utc().astimezone(NY)
        mnow = ny.hour * 60 + ny.minute
        end = int(str(cfg.get("end", "15:55"))[:2]) * 60 + int(str(cfg.get("end", "15:55"))[3:5])
        if not ((running and age > STALE_DAY_S) or (not running and mnow >= end) or (not running and age > 3600)):
            return
        pos, orders = ab.owned(self.sandbox.a, "day")
        if not any(pos.values()) and not orders:
            return
        why = "Day heartbeat stale" if running else "Day not running"
        res = ab.flatten(self.sandbox.a, ab.Book(), "watchdog", session, why)
        ab.Book().incident("watchdog_day", f"{why} ({age:.0f} s) with positions {pos}", exposed=pos,
                           fallback="Night flattened Day's symbols" + ("" if res["flat"] else " (not yet verified)"),
                           resume="Day running with a fresh heartbeat")
        self.log("watchdog_flatten", why=why, heartbeat_age_s=round(age), result=res)

    def controls_tick(self, session: date) -> None:
        """O1 #50: the user's cancel / flatten controls for Night, each confirmed from the broker."""
        if self.sandbox is None:
            return
        done = self.status.get("controls", {})
        if (DIR / "CANCEL").exists():
            _, orders = ab.owned(self.sandbox.a, "night")
            for o in orders:
                self.sandbox.a.c.delete(f"{PAPER}/orders/{o['id']}")
            left = ab.owned(self.sandbox.a, "night")[1]
            done["cancel"] = {"confirmed": not left, "left": len(left), "at": now_utc().isoformat()}
            if not left:
                (DIR / "CANCEL").unlink(missing_ok=True)
        if (DIR / "FLATTEN").exists() and self.market_open:
            res = ab.flatten(self.sandbox.a, ab.Book(), "night", session, "user flatten")
            done["flatten"] = {"confirmed": res["flat"], "left": res.get("left"), "at": now_utc().isoformat()}
            if res["flat"]:
                (DIR / "FLATTEN").unlink(missing_ok=True)
                (DIR / "PAUSE").touch()
        self.report(controls=done)

    def stop_research(self) -> None:
        if self.child is not None and self.child.poll() is None:
            os.killpg(self.child.pid, signal.SIGTERM)
            try:
                self.child.wait(60)
            except subprocess.TimeoutExpired:
                os.killpg(self.child.pid, signal.SIGKILL)
        if self.child_lock is not None:
            self.child_lock.close()

    # -- reasoning log and new calls
    def absorb(self, session: date, market_open: bool, now: datetime) -> list[dict[str, Any]]:
        """Turn research decided since the last look into lots (and a reasoning record each, PASS included)."""
        fresh: list[dict[str, Any]] = []
        seen = set(self.state["seen"])
        latest = decisions(self.names)
        save(DIR / "latest.json", latest)
        halted = (FWD / "HALT").exists()  # the global kill switch: no new calls, exits continue
        feas = {r["ticker"]: r for r in load(FWD / "day_feasibility.json", {}).get("stocks", [])}
        themed = themes()
        for t, rec in sorted(latest.items()):
            f = feas.get(t, {})
            rec = {**rec, "day_feasible": bool(f.get("feasible")),
                   "atr": f["atr_bp"] / 1e4 if f.get("atr_bp") else None,
                   "theme_horizons": themed[t]["horizons"] if t in themed else None,
                   "theme": ",".join(themed[t]["themes"]) if t in themed else None}
            age_h = (now - datetime.fromisoformat(rec["decided_at"])).total_seconds() / 3600
            if age_h > self.cfg.max_age_h:
                self.no_trade(t, rec, [f"research {age_h:.0f} h old (limit {self.cfg.max_age_h:.0f} h)"])
                continue
            made = at.new_lots(rec, session, self.mkt, self.cfg, self.state["lots"])
            reasons = [] if made else [why_none(rec, self.state["lots"], t)]  # O1 #43: the concrete reason
            lots = [x for x in made if x["horizon"] != "day"]  # Night: day trading is Autopilot Day (no AI)
            reasons += [f"{x['horizon']}: day trades belong to Autopilot Day" for x in made if x["horizon"] == "day"]
            keep = []
            for x in lots:
                if halted:
                    reasons.append(f"{x['horizon']}: kill switch on")
                elif x["side"] < 0 and self.sandbox is not None and not self.sandbox.shortable(t):
                    reasons.append(f"{x['horizon']}: not shortable / hard to borrow")
                elif self.priced_in(t, x["side"], rec):
                    reasons.append(f"{x['horizon']}: already priced in (moved > {PRICED_IN_ATR} ATR since the research)")
                else:
                    keep.append(x)
            lots = keep
            if reasons:
                self.no_trade(t, rec, reasons)
            for x in lots:  # O1 #38: two shadow controls per AI lot, same size and dates; never traded
                self.state.setdefault("controls", []).extend(controls(x, self.momentum(t)))
            if rec["evidence"] not in seen:
                seen.add(rec["evidence"])
                self.state["seen"].append(rec["evidence"])
                why = reasoning(Path(rec["evidence"]))
                self.thoughts.append("decision", at=now.isoformat(), regime=self.mkt, session=session.isoformat(),
                                     trades=[{k: x[k] for k in ("horizon", "side", "weight", "exit_session")} for x in lots],
                                     **why)
            if lots:
                at.supersede(self.state["lots"], lots)
                self.state["lots"].extend(lots)
                fresh.extend(lots)
        if fresh:
            self.log("new_calls", lots=[f"{'LONG' if x['side'] > 0 else 'SHORT'} {x['symbol']} {x['horizon']} "
                                        f"{x['weight']:.1%}" for x in fresh])
        return fresh

    # -- trading
    def trade(self, session: date, market_open: bool, now: datetime) -> int:
        """One pass of exits and entries; returns how many orders were sent."""
        assert self.sandbox is not None
        acct = self.sandbox.account()
        equity = float(acct["equity"])
        self.state.setdefault("start_equity", equity)
        self.state["peak_equity"] = max(self.state.get("peak_equity", equity), equity)
        if at.drawdown_hit(equity, self.state["start_equity"], self.cfg):
            self.sandbox.close_everything()
            (DIR / "KILLED").write_text(f"{now.isoformat()} equity {equity:.2f} is {self.cfg.max_drawdown:.0%} below "
                                        f"the start {self.state['start_equity']:.2f}; everything closed\n")
            for lot in self.state["lots"]:
                if lot["state"] == "open":
                    lot.update(state="closed", closed_reason="drawdown limit")
            self.log("killed", equity=equity, start=self.state["start_equity"])
            return 0
        pos, total, entry, working = self.sandbox.held()
        nyt = now.astimezone(NY).strftime("%H:%M")
        flatten = market_open and nyt >= self.cfg.flatten_at
        self.refresh_betas(now)
        betas = self.state.get("betas", {})
        symbols = sorted({x["symbol"] for x in self.state["lots"] if x["state"] == "open"} | set(total)
                         | set(fs.BASKET))
        prices = self.sandbox.prices([s.replace(".", "-") for s in symbols]) if symbols else {}
        prices = {s.replace("-", "."): p for s, p in prices.items()} | prices
        if market_open:
            for lot in self.state["lots"]:
                s = lot["symbol"].replace("-", ".")
                if lot["state"] == "open" and lot.get("entry_price") is None and pos.get(s, 0) * lot["side"] > 0:
                    lot.update(entry_price=entry.get(s) or prices.get(s), entry_qqq=prices.get("QQQ"),
                               beta=betas.get(s, 1.0))
            for lot in self.state["lots"]:  # K1: a closed lot is priced at the first pass after it closed
                s = lot["symbol"].replace("-", ".")
                if (lot["state"] != "open" and lot.get("entry_price") and lot.get("exit_price") is None
                        and prices.get(s) and prices.get("QQQ")):
                    lot.update(exit_price=prices[s], exit_qqq=prices["QQQ"])
                    lot["hedged_return"] = fs.hedged_return(lot, ROUND_TRIP)
                    lot["hedged_return_gross"] = fs.hedged_return(lot)
            for lot in at.stop_out(self.state["lots"], {x["symbol"]: prices.get(x["symbol"].replace("-", "."), 0)
                                                        for x in self.state["lots"]}, self.cfg):
                self.log("stop", symbol=lot["symbol"], horizon=lot["horizon"], why=lot["closed_reason"])
        for lot in at.expire(self.state["lots"], session, flatten):
            self.log("exit", symbol=lot["symbol"], horizon=lot["horizon"], why=lot["closed_reason"])
        k = fs.kelly([x for x in self.state["lots"] if x["state"] != "open"], self.cfg.base)
        sized = [{**x, "weight": x["weight"] * k.get(x["horizon"], {"mult": 1.0})["mult"]} for x in self.state["lots"]]
        sized = unstrong(sized)  # O1 #13: labels are not calibrated; the book's gross is kept
        w = {s: x for s, x in at.weights(sized, self.cfg).items() if s not in ALGO_SYMBOLS}
        w = fs.theme_cap(w, {t: v["themes"] for t, v in themes().items()}, self.cfg.per_name)  # R1
        w = fs.hedge({s.replace("-", "."): x for s, x in w.items()}, betas, self.cfg.gross)
        w = beta_cap(w, betas, BETA_NET_CAP)  # O1 #14
        targets = at.target_shares(w, equity, prices)
        for s in set(w) - set(targets):  # no price: keep what is held
            targets[s] = int(total.get(s, 0))
        ords = fs.banded(at.orders(targets, total, prices, self.cfg), prices, equity)
        last = float(acct.get("last_equity") or equity)
        breaker = equity < last * (1 - BREAKER)  # R2: a 4% day sends only reducing orders until the next session
        paused = (DIR / "PAUSE").exists()  # O1 #50: the user's pause = reduce-only
        if breaker or paused or not self.reconciled:
            ords = [o for o in ords if o["reducing"]] if self.reconciled else []
        self.set_op("trading" if not (breaker or paused) and self.reconciled else
                    "reconciling" if not self.reconciled else "reduce_only",
                    "market open" if not (breaker or paused) and self.reconciled else
                    "records and broker not yet reconciled" if not self.reconciled else
                    "account down 4%+ today (R2)" if breaker else "paused by the user")
        ords = participation(ords, prices, self.adv)  # O1 #15
        self.report(breaker=f"account down {1 - equity / last:.1%} today: only reducing orders" if breaker else None)
        todo = [o for o in ords if o["symbol"] not in working  # one order per symbol at a time: a flip opens
                and (o["reducing"] or o["side"] == "buy" or self.sandbox.shortable(o["symbol"]))]  # after its close
        self.backlog = len(todo) > ORDERS_PER_LOOP
        sent = []
        batch = todo[:ORDERS_PER_LOOP]
        red, add = [o for o in batch if o["reducing"]], [o for o in batch if not o["reducing"]]
        a, book = self.sandbox.a, ab.Book()
        legs = ab.send_batch(a, book, "night", session, "s", red, prices) if red else []
        ab.wait_final(a, book, "night", legs, timeout_s=20)  # O1 #5: reductions settle before additions
        if add and not any(x.status == "submitted" for x in legs):
            legs += ab.send_batch(a, book, "night", session, "s", add, prices)
        else:
            add = []
        for o, leg in zip(red + add, legs):
            sent.append({**o, "status": leg.status, "order_id": leg.order_id, "id": leg.client_order_id,
                         "filled_qty": leg.filled_qty, "note": leg.note})
            self.state.setdefault("orders", {})[leg.order_id or leg.client_order_id] = {
                "symbol": o["symbol"], "why": self.why(o["symbol"]), "at": now.isoformat()}
            for lot in self.state["lots"]:  # O1 #23: research decision to first order
                if (lot["state"] == "open" and lot["symbol"].replace("-", ".") == o["symbol"]
                        and "first_order_at" not in lot and leg.status != "rejected"):
                    lot["first_order_at"] = now.isoformat()
                    lot["decision_to_order_s"] = round((now - datetime.fromisoformat(lot["decided_at"])).total_seconds())
        if sent:
            self.log("orders", session=session.isoformat(), sent=sent)
        gross = sum(abs(q) * prices.get(s, 0) for s, q in pos.items()) / equity if equity else 0
        self.state["kelly"] = k
        self.report(kelly=k, hedge=round(sum(w.get(e, 0.0) for e in fs.BASKET), 4),
                    net_beta_before_hedge=round(-sum(w.get(e, 0.0) for e in fs.BASKET), 4))
        self.report(equity=equity, start_equity=self.state["start_equity"], cash=float(acct.get("cash") or 0),
                    positions=len(pos), gross=round(gross, 3), buying_power=float(acct.get("buying_power") or 0))
        return len(sent)

    def refresh_betas(self, now: datetime) -> None:
        """N1: each held or planned stock's beta to QQQ, once a New York day."""
        day = now.astimezone(NY).date().isoformat()
        if self.state.get("betas_date") == day or self.sandbox is None:
            return
        names = sorted({x["symbol"].replace("-", ".") for x in self.state["lots"] if x["state"] == "open"})
        closes = self.sandbox.daily_closes(["QQQ", *names])
        q = closes.get("QQQ", [])
        self.state["betas"] = {s: round(fs.beta(closes.get(s, []), q), 3) for s in names}
        self.state["betas_date"] = day
        self.log("betas", n=len(names), mean=round(sum(self.state["betas"].values()) / max(1, len(names)), 3))

    def why(self, sym: str) -> str:
        if sym in fs.BASKET:
            return "market hedge: offsets the stock book's beta to QQQ"
        lots = [x for x in self.state["lots"] if x["symbol"].replace("-", ".") == sym]
        lots.sort(key=lambda x: x.get("decided_at", ""))
        if not lots:
            return "not in the plan: closed"
        x = lots[-1]
        side = "long" if x["side"] > 0 else "short"
        return (f"{side} {x['horizon']} call (rating {x['rating']}, {x['regime']} market)" if x["state"] == "open"
                else f"closing {side} {x['horizon']}: {x.get('closed_reason', '')}")

    def record(self, now: datetime, market_open: bool) -> None:
        """Equity points for the graph and every fill into the trade log."""
        assert self.sandbox is not None
        every = 60 if market_open else 900
        if time.monotonic() - self.last["equity"] >= every:
            a = self.sandbox.account()
            Ledger(DIR / "equity.jsonl").append("equity", at=now.isoformat(), equity=float(a["equity"]),
                                                 cash=float(a.get("cash") or 0), long=float(a.get("long_market_value") or 0),
                                                 short=float(a.get("short_market_value") or 0))
            self.last["equity"] = time.monotonic()
        if time.monotonic() - self.last["fills"] >= 60:
            after = self.state.get("fills_after")
            reasons = self.state.get("orders", {})
            for f in self.sandbox.fills(after):
                Ledger(DIR / "trades.jsonl").append("fill", id=f.get("id"), time=f.get("transaction_time"),
                    symbol=f.get("symbol"), side=f.get("side"), qty=float(f.get("qty") or 0),
                    price=float(f.get("price") or 0), order_id=f.get("order_id"),
                    why=(reasons.get(f.get("order_id") or "") or {}).get("why", ""))
                self.state["fills_after"] = f.get("transaction_time")
            self.last["fills"] = time.monotonic()

    def refresh_regime(self) -> None:
        if time.monotonic() - self.last["regime"] < 3600:
            return
        closes: list[float] = []
        # Market data only: without the autopilot keys the main paper keys read the QQQ bars (no orders).
        client = self.sandbox.a if self.sandbox is not None else Alpaca.from_env()
        if client is not None:
            try:
                closes = Sandbox(client).closes("QQQ")
            except (BrokerError, httpx.HTTPError, KeyError, ValueError):
                closes = []
            finally:
                if self.sandbox is None:
                    client.c.close()
        mkt = at.regime(closes)
        if mkt == "unknown":
            self.last["regime"] = time.monotonic() - 3600 + 60  # retry in a minute; keep the last known trend
            return
        if mkt != self.mkt:
            self.log("regime", regime=mkt, qqq=closes[-1] if closes else None)
        self.mkt = mkt
        self.last["regime"] = time.monotonic()

    def connect(self) -> None:
        if self.sandbox is not None or time.monotonic() - self.last["keys"] < 60:
            return
        self.last["keys"] = time.monotonic()
        sb = Sandbox.from_env()
        if sb is None:
            self.report(account="missing", message="Researching. Add the separate Alpaca paper keys to start trading.")
            return
        if same_account(sb):
            self.report(account="same_as_main", message="Refused: the autopilot keys are the main paper account's.")
            return
        self.sandbox = sb
        self.report(account="connected")
        self.log("connected", account="separate Alpaca paper account")

    # -- the loop
    def run(self) -> int:
        stop = False
        self.report(state="running", message="Starting", started_at=now_utc().isoformat())
        while not stop:
            stop = (self.job / "STOP").exists()
            now = now_utc()
            try:
                self.connect()
                self.refresh_regime()
                if self.sandbox is not None:
                    clock = self.sandbox.clock()
                    market_open = self.market_open = bool(clock["is_open"])
                    nxt = datetime.fromisoformat(clock["next_open"]).astimezone(NY)
                    session = now.astimezone(NY).date() if market_open else nxt.date()
                    self.record(now, market_open)
                    if not self.reconciled:  # O1 #9/#19: settle records with the broker first
                        rec = ab.reconcile(self.sandbox.a, ab.Book(), "night")
                        self.reconciled = rec["ok"]
                        self.log("reconcile", **rec)
                    self.controls_tick(session)
                    self.watch_day(session)
                    if (DIR / "KILLED").exists() or (FWD / "HALT").exists():
                        self.set_op("halted", "35% drawdown kill" if (DIR / "KILLED").exists() else "kill switch on")
                    elif not market_open:
                        self.set_op("waiting", "market closed")
                    if not (DIR / "KILLED").exists():
                        if not stop:
                            self.absorb(session, market_open, now)
                        if stop:
                            # Stopping: day trades are not left overnight; swing positions stay with their plan.
                            for lot in self.state["lots"]:
                                if lot["state"] == "open" and lot["horizon"] == "day" and market_open:
                                    lot.update(state="closed", closed_reason="autopilot stopped")
                        if market_open:  # closed: after-hours quotes are too wide for the risk check
                            for _ in range(15 if stop else 1):  # stopping: pass until the day trades are closed
                                if not self.trade(session, market_open, now_utc()):
                                    break
                                if stop:
                                    time.sleep(10)
                    phase = ("killed" if (DIR / "KILLED").exists() else "market open" if market_open
                             else "market closed: orders go at the open")
                else:
                    session = at.add_sessions(now.astimezone(NY).date() + timedelta(days=1), 0)
                    self.absorb(session, False, now)
                    phase = "research only (no autopilot account yet)"
                if not stop:
                    self.research_tick(now)
                open_lots = [x for x in self.state["lots"] if x["state"] == "open"]
                self.report(phase=phase, regime=self.mkt, open_calls=len(open_lots),
                            longs=sum(x["side"] > 0 for x in open_lots), shorts=sum(x["side"] < 0 for x in open_lots),
                            researching=self.child_batch,
                            message=f"{phase}; {self.mkt} market; {len(open_lots)} open calls"
                                    + (f"; researching {', '.join(self.child_batch)}" if self.child_batch else ""))
                self.persist()
            except (BrokerError, httpx.HTTPError, OSError, KeyError, ValueError) as exc:
                self.reconciled = False  # O1 #17/#19: broker trouble -> reconcile before sending again
                self.report(last_error=f"{type(exc).__name__}: {str(exc)[:200]}", last_error_at=now.isoformat())
                self.log("error", error=f"{type(exc).__name__}: {str(exc)[:200]}")
            if not stop:
                wait = PASS_S["backlog" if self.backlog else "open" if self.market_open else "closed"]
                for _ in range(wait):
                    if (self.job / "STOP").exists():
                        break
                    time.sleep(1)
        self.stop_research()
        self.persist()
        self.report(state="stopped", message="Autopilot stopped. Swing positions stay in the account with their plan.")
        return 0


def run(job: Path, node: str = "", backend: Path = BACKEND) -> int:
    del node, backend
    DIR.mkdir(parents=True, exist_ok=True)
    status_path = job / "status.json"
    import desktop_run as worker
    js = load(status_path, {})
    js.update(state="running", pid=os.getpid(), identity=worker.identity(os.getpid()),
              started_at=now_utc().isoformat(), steps=[])
    save(status_path, js)
    with (FWD / "paper_autopilot" / "controller.lock").open("a") as controller:
        try:
            fcntl.flock(controller, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            js.update(state="blocked", message="Another continuous worker is running: stop it first.")
            save(status_path, js)
            return 2
        rc = Controller(job).run()
    js = load(status_path, {})
    js.update(state="stopped", finished_at=now_utc().isoformat())
    save(status_path, js)
    return rc


# ---------------------------------------------------------------- the app's view

def view() -> dict[str, Any]:
    out: dict[str, Any] = {"status": load(DIR / "status.json", {}), "killed": (DIR / "KILLED").read_text()
                           if (DIR / "KILLED").exists() else None, "config": load(CONFIG, {})}
    companies = load(DIR / "universe.json", {}).get("companies", [])
    latest: dict[str, dict[str, Any]] = load(DIR / "latest.json", {})
    now = now_utc()
    out["universe"] = [{"ticker": c["ticker"], "name": c["name"],
                        "decided_at": latest.get(c["ticker"], {}).get("decided_at"),
                        "ratings": latest.get(c["ticker"], {}).get("ratings")} for c in companies]
    out["fresh"] = sum(1 for r in latest.values()
                       if (now - datetime.fromisoformat(r["decided_at"])).total_seconds() < REFRESH_H * 3600)
    state = load(DIR / "state.json", {})
    out["lots"] = [x for x in state.get("lots", []) if x["state"] == "open"]
    out["closed_lots"] = [x for x in state.get("lots", []) if x["state"] != "open"][-100:]
    out["trades"] = jsonl_records(DIR / "trades.jsonl")[-500:]
    out["equity"] = [{"t": r["at"], "v": r["equity"]} for r in jsonl_records(DIR / "equity.jsonl")][-3000:]
    out["thoughts"] = jsonl_records(DIR / "thoughts.jsonl")[-150:][::-1]
    out["events"] = jsonl_records(DIR / "events.jsonl")[-80:][::-1]
    out["kelly"] = state.get("kelly")
    out["betas"] = state.get("betas")
    sb = Sandbox.from_env()
    out["account_configured"] = sb is not None
    if sb is not None:
        try:
            a = sb.get(f"{PAPER}/account")
            out["account"] = {k: a.get(k) for k in ("equity", "last_equity", "cash", "buying_power", "long_market_value",
                                                    "short_market_value", "status", "daytrade_count")}
            out["positions"] = [{k: p.get(k) for k in ("symbol", "qty", "side", "avg_entry_price", "current_price",
                                                       "market_value", "unrealized_pl", "unrealized_plpc")}
                                for p in sb.get(f"{PAPER}/positions")]
            out["history_day"] = sb.history("1D", "5Min")
            out["history_month"] = sb.history("1M", "1D")
            main = Alpaca.from_env()
            if main is not None:
                try:
                    hm = main._get(f"{PAPER}/account/portfolio/history", period="1M", timeframe="1D",
                                   extended_hours="false")
                    out["combined"] = fs.combined(hm, out["history_month"])
                    out["main_account"] = {k: main._get(f"{PAPER}/account").get(k) for k in ("equity", "last_equity")}
                finally:
                    main.c.close()
            out["orders"] = [{k: o.get(k) for k in ("symbol", "side", "qty", "filled_qty", "filled_avg_price", "status",
                                                    "submitted_at", "filled_at", "id")}
                             for o in sb.get(f"{PAPER}/orders", status="all", limit=200, direction="desc")]
        except (BrokerError, httpx.HTTPError, KeyError, ValueError) as exc:
            out["account_error"] = f"{type(exc).__name__}"
        finally:
            sb.a.c.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("universe", "view", "reasoning"))
    ap.add_argument("ticker", nargs="?")
    a = ap.parse_args()
    if a.cmd == "universe":
        cs = universe()
        print(json.dumps({"count": len(cs), "tickers": [c["ticker"] for c in cs]}))
    elif a.cmd == "view":
        print(json.dumps(view(), default=str))
    else:
        t = (a.ticker or "").upper()
        if not t.replace(".", "").replace("-", "").isalnum():
            raise SystemExit("ticker?")
        rec = decisions({t}).get(t)
        print(json.dumps(reasoning(Path(rec["evidence"])) if rec else {"error": "no decided research yet"}))


if __name__ == "__main__":
    main()
