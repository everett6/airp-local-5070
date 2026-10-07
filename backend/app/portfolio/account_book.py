"""The autopilot account's single risk authority (docs/PLAN_60_V2.md, "O1").

Both autopilots (Night: scripts/full_auto.py, Day: scripts/algo_engine.py) send orders only through `send_batch`,
which, under one file lock for the whole account:
- takes one broker snapshot (account, positions, open orders, quotes) and runs the account risk check, which counts
  open orders at their worst-case fill, for every order in turn (orders already approved count against later ones);
- enforces symbol ownership (Day: the 14 ETFs; Night: everything else; the watchdog may only reduce);
- gives each order a stable client order ID from strategy, session, decision, symbol and intent, looks that ID up
  before sending (a lost reply is recovered, never re-sent) and writes the intent to disk before contacting Alpaca.
`wait_final` follows orders to a final state (partial fills, rejections, expiry, cancels); `flatten` closes owned
positions and verifies they are gone; `reconcile` compares the intent log with the broker. Incidents and operating
states are logged for the app. Hard ceilings (review #40) cannot be loosened by any config file.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.portfolio.account_risk import RiskPolicy, evaluate
from app.portfolio.broker import DATA, PAPER, Alpaca, BrokerError, Leg

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND / "results" / "forward" / "account"
DAY_SYMBOLS = frozenset(("SPY", "IWM", "DIA", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLC",
                         "XLRE", "SMH"))  # = app.sandbox.minute_ensemble.UNIVERSE (checked by a test)
PREFIX = {"night": "nt", "day": "dy", "watchdog": "wd"}
OPEN_STATES = ("intended", "submitted")
# Review #40: limits no config may loosen, and no automatic process may change.
CEILINGS = {"max_gross": 3.9, "daily_loss": 0.15, "max_asset": 0.25, "max_drawdown": 0.35, "day_gross": 3.0}
SANDBOX_RISK = RiskPolicy(max_gross=3.9, max_asset=0.25, max_crypto=0.01, daily_loss=0.15, max_spread_bp=150.0,
                          max_reference_gap=0.10, max_quote_age_s=180.0)
STATES = {  # review #16: operating states and what each allows
    "waiting": "market closed or outside the window: nothing is sent",
    "ready": "checks passed; trading starts at the next decision",
    "trading": "entries and exits allowed",
    "reduce_only": "only orders that shrink positions",
    "reconciling": "nothing is sent until local records and the broker agree",
    "halted": "nothing is sent; needs the user or a resolved incident",
    "recovering": "waiting for fresh data, a clean reconciliation and valid limits before trading again",
}


def owner(symbol: str) -> str:
    return "day" if symbol in DAY_SYMBOLS else "night"


def ceiling_violations(policy: RiskPolicy, **limits: float) -> list[str]:
    """Limits looser than the hard ceilings (empty: fine)."""
    out = [f"{k} {getattr(policy, k)} > ceiling {CEILINGS[k]}" for k in ("max_gross", "daily_loss", "max_asset")
           if getattr(policy, k) > CEILINGS[k]]
    out += [f"{k} {v} > ceiling {CEILINGS[k]}" for k, v in limits.items() if k in CEILINGS and v > CEILINGS[k]]
    return out


def client_id(strategy: str, session: date, decision: str, symbol: str, side: str, to_qty: float) -> str:
    """Same strategy, session, decision, symbol and intent -> same ID, so a retry finds the first attempt."""
    raw = f"{PREFIX[strategy]}-{session:%y%m%d}-{decision}-{symbol}-{side[0]}{round(to_qty)}"
    raw = re.sub(r"[^0-9A-Za-z.-]", "", raw)
    if len(raw) <= 48:
        return raw
    return f"{PREFIX[strategy]}-{session:%y%m%d}-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def _append(path: Path, rec: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())  # review #4: on disk before the broker is contacted


def _read(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # a torn last line after a crash
    return out


class Book:
    """Intents, incidents and state transitions under one directory (tests pass a temporary one)."""

    def __init__(self, root: Path = ROOT):
        self.root = root

    # -- intents (review #3, #4, #6) --
    def intent(self, leg: Leg, strategy: str, state: str, **extra: Any) -> None:
        _append(self.root / "intents.jsonl", {"at": datetime.now(UTC).isoformat(), "id": leg.client_order_id,
                                              "strategy": strategy, "symbol": leg.symbol, "side": leg.side,
                                              "qty": leg.qty, "limit": leg.limit_price, "state": state,
                                              "filled_qty": leg.filled_qty, "order_id": leg.order_id,
                                              "note": leg.note[:200], **extra})

    def intents(self) -> dict[str, dict[str, Any]]:
        last: dict[str, dict[str, Any]] = {}
        for r in _read(self.root / "intents.jsonl"):
            last[r["id"]] = r
        return last

    def open_intents(self, strategy: str) -> list[dict[str, Any]]:
        return [r for r in self.intents().values() if r["strategy"] == strategy and r["state"] in OPEN_STATES]

    # -- incidents (review #49) --
    def incident(self, kind: str, detail: str, exposed: dict[str, float] | None = None, fallback: str = "",
                 resume: str = "") -> str:
        iid = f"{kind}-{int(time.time())}"
        _append(self.root / "incidents.jsonl", {"at": datetime.now(UTC).isoformat(), "id": iid, "kind": kind,
                                                "detail": detail[:500], "exposed": exposed or {}, "fallback": fallback,
                                                "resume_when": resume, "state": "open"})
        return iid

    def resolve(self, kind: str, how: str) -> None:
        for r in self.incidents():
            if r["kind"] == kind and r["state"] == "open":
                _append(self.root / "incidents.jsonl", {**r, "at": datetime.now(UTC).isoformat(), "state": "resolved",
                                                        "resolution": how})

    def incidents(self) -> list[dict[str, Any]]:
        last: dict[str, dict[str, Any]] = {}
        for r in _read(self.root / "incidents.jsonl"):
            last[r["id"]] = r
        return list(last.values())

    def open_incidents(self, kind: str | None = None) -> list[dict[str, Any]]:
        return [r for r in self.incidents() if r["state"] == "open" and (kind is None or r["kind"] == kind)]

    # -- operating states (review #16) --
    def state(self, strategy: str, new: str, reason: str) -> None:
        assert new in STATES, new
        path = self.root / f"state_{strategy}.json"
        try:
            cur = json.loads(path.read_text())
        except (OSError, ValueError):
            cur = {}
        if cur.get("state") == new and cur.get("reason") == reason:
            return
        rec = {"at": datetime.now(UTC).isoformat(), "strategy": strategy, "state": new, "reason": reason,
               "allows": STATES[new], "from": cur.get("state")}
        tmp = path.with_suffix(".tmp")
        self.root.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(rec))
        os.replace(tmp, path)
        _append(self.root / "states.jsonl", rec)

    def current(self, strategy: str) -> dict[str, Any]:
        try:
            return json.loads((self.root / f"state_{strategy}.json").read_text())
        except (OSError, ValueError):
            return {}


@contextmanager
def account_lock(root: Path = ROOT) -> Iterator[None]:
    """Review #1/#22: one writer at a time for the whole account, across processes."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / "risk.lock").open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def signed_positions(positions: list[dict[str, Any]]) -> dict[str, float]:
    return {p["symbol"]: abs(float(p["qty"])) * (-1 if p.get("side") == "short" else 1) for p in positions}


def account_gate(book: Book) -> str:
    """Why no strategy may add risk now: the account kill switch, the 35% drawdown kill, or an order whose outcome
    is unknown (it may be live at the broker) until reconcile resolves it. Reductions stay allowed."""
    fwd = book.root.parent
    if (fwd / "HALT").exists() or (fwd / "full_auto" / "KILLED").exists():
        return "kill switch or 35% drawdown kill"
    amb = [r for r in book.intents().values() if r.get("ambiguous") and r["state"] in OPEN_STATES]
    return f"{len(amb)} order(s) with an unknown outcome; reconcile first" if amb else ""


def send_batch(a: Alpaca, book: Book, strategy: str, session: date, decision: str, orders: list[dict[str, Any]],
               prices: dict[str, float], limit_bp: float | None = None, quote_age_s: float | None = None,
               max_spread_bp: float | None = None, tif: str = "day") -> list[Leg]:
    """Each order: {symbol, side, qty, to}. Returns one Leg per order (status submitted, filled, rejected, ...).

    limit_bp: marketable limit at the quote plus/minus this many bp (bounded execution, review #15); then
    quote_age_s and max_spread_bp gate the order on the quote itself."""
    policy = a.risk_policy
    if policy is None:
        raise BrokerError("the risk authority needs a risk policy")
    bad = ceiling_violations(policy)
    if bad:
        raise BrokerError("risk limits above the hard ceilings: " + "; ".join(bad))
    legs: list[Leg] = []
    with account_lock(book.root):
        now = datetime.now(UTC)
        account = dict(a.account())
        positions = a._get(f"{PAPER}/positions")
        pending = list(a._get(f"{PAPER}/orders", status="open", limit=500))
        if len(pending) >= 500:
            raise BrokerError("open order snapshot may be truncated")
        names = sorted({o["symbol"] for o in orders} | {p["symbol"] for p in positions} | {o["symbol"] for o in pending})
        quotes: dict[str, Any] = {}
        for i in range(0, len(names), 100):
            quotes.update(a._get(f"{DATA}/v2/stocks/quotes/latest", symbols=",".join(names[i:i + 100]),
                                 feed="iex")["quotes"])
        held = signed_positions(positions)
        gate = account_gate(book)
        floor = now - timedelta(seconds=policy.max_quote_age_s)
        for o in orders:
            sym = o["symbol"]
            if not o.get("to", 0.0):  # a close is sized from the holding read under the lock, never a stale read
                cur = held.get(sym, 0.0) + sum((1 if p["side"] == "buy" else -1)
                                               * (float(p["qty"]) - float(p.get("filled_qty") or 0))
                                               for p in pending if p["symbol"] == sym)  # working orders count
                if not cur:
                    leg = Leg(sym, sym, o["side"], 0.0, tif, "", prices.get(sym, 0.0))
                    leg.status, leg.note = "rejected", "NOTHING TO CLOSE: already flat at the broker"
                    legs.append(leg)
                    continue
                o = {**o, "side": "sell" if cur > 0 else "buy", "qty": abs(cur), "to": 0.0}
            cid = client_id(strategy, session, decision, sym, o["side"], o.get("to", 0.0))
            leg = Leg(sym, sym, o["side"], float(o["qty"]), tif, cid, prices.get(sym, 0.0))
            reducing = abs(o.get("to", 0.0)) < abs(held.get(sym, 0.0)) and o.get("to", 0.0) * held.get(sym, 0.0) >= 0
            if owner(sym) != strategy and not (strategy == "watchdog" and reducing):
                leg.status, leg.note = "rejected", f"OWNERSHIP: {sym} belongs to {owner(sym)}"
                book.intent(leg, strategy, "rejected")
                legs.append(leg)
                continue
            got = None
            for k in range(6):  # attempt k of the same intent; a final unfilled attempt allows the next one
                leg.client_order_id = cid if k == 0 else f"{cid[:44]}-r{k}"
                got = a.c.get(f"{PAPER}/orders:by_client_order_id", params={"client_order_id": leg.client_order_id})
                prev = got.json() if got.status_code == 200 else {}
                if (got.status_code != 200 or prev.get("status") not in ("canceled", "expired", "rejected")
                        or float(prev.get("filled_qty") or 0) > 0):  # a partly filled attempt is never replayed
                    break
            assert got is not None
            cid = leg.client_order_id
            if got.status_code == 200:  # sent before (a lost reply or a retry): follow it, never send twice
                a.refresh(leg)
                book.intent(leg, strategy, leg.status, recovered=True)
                legs.append(leg)
                continue
            if got.status_code != 404:
                leg.status, leg.note = "rejected", f"order lookup HTTP {got.status_code}: not sent"
                book.intent(leg, strategy, "rejected")
                legs.append(leg)
                continue
            if gate and not reducing:
                leg.status, leg.note = "rejected", "ACCOUNT GATE: " + gate
                book.intent(leg, strategy, "rejected")
                legs.append(leg)
                continue
            q = quotes.get(sym) or {}
            if limit_bp is not None:
                bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
                qt = q.get("t")
                age = (now - datetime.fromisoformat(str(qt))).total_seconds() if qt else 1e9
                spread = (ask - bid) / ((ask + bid) / 2) * 1e4 if bid > 0 and ask > 0 else 1e9
                why = (f"quote older than {quote_age_s:.0f} s" if quote_age_s is not None and age > quote_age_s
                       else f"spread {spread:.1f} bp over {max_spread_bp:.1f}"
                       if max_spread_bp is not None and spread > max_spread_bp else "")
                if why:
                    leg.status, leg.note = "rejected", "EXECUTION GATE: " + why
                    book.intent(leg, strategy, "rejected")
                    legs.append(leg)
                    continue
                leg.limit_price = ask * (1 + limit_bp / 1e4) if o["side"] == "buy" else bid * (1 - limit_bp / 1e4)
            try:
                verdict = evaluate(policy, account, positions, pending, quotes, leg.symbol, leg.side, leg.qty,
                                   leg.ref_price or float(q.get("ap") or 0), now, False, floor)
            except (ValueError, KeyError, TypeError) as exc:
                verdict = {"allowed": False, "reasons": [f"risk snapshot unusable: {str(exc)[:120]}"]}
            a._audit("risk", client_order_id=cid, symbol=sym, side=leg.side, qty=leg.qty, ref_price=leg.ref_price,
                     at=datetime.now(UTC).isoformat(), strategy=strategy, **verdict)
            if not verdict["allowed"]:
                leg.status, leg.note = "rejected", "ACCOUNT RISK: " + "; ".join(verdict["reasons"])
                book.intent(leg, strategy, "rejected")
                legs.append(leg)
                continue
            book.intent(leg, strategy, "intended", reducing=bool(verdict.get("reducing")))
            try:
                a._submit(leg)
            except Exception as exc:  # noqa: BLE001 - an ambiguous send: the intent stays open for reconcile
                leg.status, leg.note = "submitted", f"reply lost ({type(exc).__name__}); reconcile will look it up"
                book.intent(leg, strategy, "submitted", ambiguous=True)
                pending.append({"symbol": sym, "side": leg.side, "qty": leg.qty, "filled_qty": 0})
                gate = gate or "an order in this batch has an unknown outcome"
                legs.append(leg)
                continue
            unknown = "outcome unknown" in leg.note
            book.intent(leg, strategy, leg.status, **({"ambiguous": True} if unknown else {}))
            gate = gate or ("an order in this batch has an unknown outcome" if unknown else "")
            if leg.status == "submitted":
                pending.append({"symbol": sym, "side": leg.side, "qty": leg.qty, "filled_qty": 0})
                if not verdict.get("reducing"):
                    ask = float(q.get("ap") or leg.ref_price or 0)
                    account["buying_power"] = str(float(account["buying_power"]) - leg.qty * ask)
            legs.append(leg)
    return legs


def wait_final(a: Alpaca, book: Book, strategy: str, legs: list[Leg], timeout_s: float = 20.0,
               poll_s: float = 0.5, sleep: Callable[[float], None] = time.sleep) -> list[Leg]:
    """Review #5/#6: follow orders until each is final (filled, rejected, canceled, expired) or the timeout; every
    state change is written to the intent log. A working order past the timeout is canceled, then read once more."""
    deadline = time.monotonic() + timeout_s
    live = [x for x in legs if x.status == "submitted"]
    while live and time.monotonic() < deadline:
        sleep(poll_s)
        for leg in live:
            before = (leg.status, leg.filled_qty)
            try:
                a.refresh(leg)
            except (BrokerError, OSError) as exc:
                leg.note = f"refresh failed: {type(exc).__name__}"
                continue
            if (leg.status, leg.filled_qty) != before:
                book.intent(leg, strategy, leg.status)
        live = [x for x in live if x.status == "submitted"]
    for leg in live:  # still working: cancel, then record what filled
        try:
            a.cancel_leg(leg)
            a.refresh(leg)
        except (BrokerError, OSError, KeyError) as exc:
            leg.note = f"cancel after timeout failed: {type(exc).__name__}"
        book.intent(leg, strategy, leg.status, timed_out=True)
    return legs


def owned(a: Alpaca, strategy: str) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Broker-confirmed positions and open orders in the strategy's symbols (review #7)."""
    pos = {s: q for s, q in signed_positions(a._get(f"{PAPER}/positions")).items() if owner(s) == strategy}
    orders = [o for o in a._get(f"{PAPER}/orders", status="open", limit=500) if owner(o["symbol"]) == strategy]
    return pos, orders


def flatten(a: Alpaca, book: Book, strategy: str, session: date, why: str, prices: dict[str, float] | None = None,
            verify_s: float = 60.0, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Review #10/#50: cancel the strategy's open orders, close its positions, and VERIFY both are gone. An
    unverified flatten opens an incident that stays open until a later flatten verifies."""
    actor = strategy
    target = "day" if strategy == "watchdog" else strategy
    with account_lock(book.root):
        _, orders = owned(a, target)
        for o in orders:
            a.c.delete(f"{PAPER}/orders/{o['id']}")
    pos, _ = owned(a, target)
    orders_out = [{"symbol": s, "side": "sell" if q > 0 else "buy", "qty": abs(q), "to": 0.0} for s, q in pos.items() if q]
    legs = send_batch(a, book, actor, session, f"fl{datetime.now(UTC):%H%M%S%f}", orders_out, prices or {}) if orders_out else []
    wait_final(a, book, actor, legs, timeout_s=min(verify_s, 30.0), sleep=sleep)
    deadline = time.monotonic() + verify_s
    while True:
        pos, orders = owned(a, target)
        left = {s: q for s, q in pos.items() if q}
        if not left and not orders:
            book.resolve(f"flatten_{target}", f"verified flat ({why})")
            return {"flat": True, "why": why, "left": {}}
        if time.monotonic() >= deadline:
            if not book.open_incidents(f"flatten_{target}"):
                book.incident(f"flatten_{target}", f"close requested ({why}) but not verified flat", exposed=left,
                              fallback="retry every minute; no new entries", resume="positions and open orders gone")
            return {"flat": False, "why": why, "left": left, "open_orders": len(orders)}
        sleep(1.0)


def reconcile(a: Alpaca, book: Book, strategy: str) -> dict[str, Any]:
    """Review #9: settle every open intent against the broker; adopt broker orders in owned symbols that the log
    lacks. ok=False (block entries) only when the broker cannot be read or an intent cannot be explained."""
    unexplained, settled, adopted = [], 0, 0
    try:
        known = book.intents()
        for rec in book.open_intents(strategy):
            got = a.c.get(f"{PAPER}/orders:by_client_order_id", params={"client_order_id": rec["id"]})
            leg = Leg(rec["symbol"], rec["symbol"], rec["side"], float(rec["qty"]), "day", rec["id"], 0.0)
            if got.status_code == 404:
                state = "never_sent" if rec["state"] == "intended" else "unexplained"
                if state == "unexplained" and not rec.get("ambiguous"):
                    unexplained.append(rec["id"])
                leg.note = "not at the broker"
                book.intent(leg, strategy, "never_sent" if state == "never_sent" or rec.get("ambiguous") else state)
            elif got.status_code == 200:
                a.refresh(leg)
                book.intent(leg, strategy, leg.status, reconciled=True)
            else:
                return {"ok": False, "why": f"order lookup HTTP {got.status_code}", "unexplained": unexplained}
            settled += 1
        _, orders = owned(a, strategy)
        for o in orders:
            cid = o.get("client_order_id", "")
            if cid not in known:
                leg = Leg(o["symbol"], o["symbol"], o["side"], float(o.get("qty") or 0), o.get("time_in_force", "day"),
                          cid, 0.0, status="submitted", order_id=o.get("id"))
                book.intent(leg, strategy, "submitted", adopted=True)
                adopted += 1
    except (BrokerError, OSError, KeyError, ValueError) as exc:
        return {"ok": False, "why": f"broker unreadable: {type(exc).__name__}", "unexplained": unexplained}
    ok = not unexplained
    if not ok:
        book.incident(f"reconcile_{strategy}", f"{len(unexplained)} submitted order(s) not found at the broker",
                      fallback="no new entries", resume="a clean reconciliation")
    else:
        book.resolve(f"reconcile_{strategy}", "clean reconciliation")
    return {"ok": ok, "settled": settled, "adopted": adopted, "unexplained": unexplained}


def exposure(account: dict[str, Any], positions: list[dict[str, Any]], orders: list[dict[str, Any]],
             betas: dict[str, float] | None = None, themes: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Review #14/#45: the account's actual exposure for the dashboard (broker data only)."""
    eq = float(account.get("equity") or 0) or 1.0
    mv = {p["symbol"]: float(p.get("market_value") or 0) for p in positions}
    px = {p["symbol"]: float(p.get("current_price") or 0) for p in positions}
    pend = 0.0
    for o in orders:
        left = float(o.get("qty") or 0) - float(o.get("filled_qty") or 0)
        pend += left * float(o.get("limit_price") or px.get(o["symbol"]) or 0)
    by_owner: dict[str, dict[str, float]] = {}
    for s, v in mv.items():
        d = by_owner.setdefault(owner(s), {"gross": 0.0, "net": 0.0})
        d["gross"] += abs(v) / eq
        d["net"] += v / eq
    th: dict[str, dict[str, float]] = {}
    for s, v in mv.items():
        for t in (themes or {}).get(s, []):
            d = th.setdefault(t, {"gross": 0.0, "net": 0.0})
            d["gross"] += abs(v) / eq
            d["net"] += v / eq
    top = sorted(mv.items(), key=lambda x: -abs(x[1]))[:5]
    return {"equity": eq, "gross": sum(abs(v) for v in mv.values()) / eq, "net": sum(mv.values()) / eq,
            "pending_gross": pend / eq, "worst_case_gross": (sum(abs(v) for v in mv.values()) + pend) / eq,
            "beta_net": sum(v * (betas or {}).get(s, 1.0) for s, v in mv.items()) / eq,
            "by_owner": by_owner, "themes": th, "top": [{"symbol": s, "weight": v / eq} for s, v in top],
            "buying_power": float(account.get("buying_power") or 0),
            "daytrading_buying_power": float(account.get("daytrading_buying_power") or 0),
            "headroom_gross": CEILINGS["max_gross"] - (sum(abs(v) for v in mv.values()) + pend) / eq}
