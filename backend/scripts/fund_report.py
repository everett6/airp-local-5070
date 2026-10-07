"""O1 reports for the app (docs/PLAN_60_V2.md, "O1"): descriptive only; nothing here changes trading.

    python scripts/fund_report.py            # JSON for the app's Fund control page

- calls:      every decided research call scored on the defined target (#26): the stock's return from the entry
              session's open to the exit session's close minus beta x QQQ over the same dates, for horizons whose
              exit session has closed; calibration by label (#34), abstention (#35), attribution by horizon, side,
              label, support, regime and memory (#39), and the AI book against its shadow controls (#38)
- funnel:     companies -> researched -> decided -> eligible -> risk-approved -> submitted -> filled -> exited ->
              evaluated, with the reasons things drop out (#44)
- risk:       the account's actual exposure, pending exposure, ownership, headroom, breakers and states (#45)
- orders:     intents and broker orders by state (#46); execution quality: slippage against the decision price (#47)
- evidence:   every strategy's evidence: backtest or forward, sample, interval, result; audit exceptions (#48)
- incidents:  open first (#49)
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np

from app.data_ingestion.bars import key
from app.portfolio import account_book as ab
from app.portfolio import auto_trader as at
from app.portfolio import fund_stability as fs
from app.portfolio.broker import DATA, PAPER, Alpaca, BrokerError

NY = ZoneInfo("America/New_York")
FWD = BACKEND / "results" / "forward"
CACHE = FWD / "report_bars.json"
LABEL = {1: "strong bear", 2: "bear", 4: "bull", 5: "strong bull"}


def load(p: Path, default: Any) -> Any:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def calls() -> list[dict[str, Any]]:
    """Every deep-research record (latest per ticker and decision time)."""
    out = {}
    for f in (FWD / "deep_research").glob("*/*.json"):
        if f.name == "summary.json":
            continue
        r = load(f, {})
        if isinstance(r, dict) and r.get("ticker"):
            out[(r["ticker"], r.get("decided_at") or r.get("as_of"))] = r
    return list(out.values())


def entry_session(decided_at: str) -> date:
    t = datetime.fromisoformat(decided_at).astimezone(NY)
    d = t.date() if (t.hour, t.minute) < (9, 30) else t.date() + timedelta(days=1)
    return at.add_sessions(d, 0)


def bars(a: Alpaca | None, symbols: list[str], start: date) -> dict[str, dict[str, tuple[float, float]]]:
    """Daily (open, close) per symbol per date from Alpaca's free bars, cached once a New York day."""
    today = datetime.now(NY).date().isoformat()
    cache = load(CACHE, {})
    if cache.get("day") == today and set(symbols) <= set(cache.get("bars", {})):
        return {s: {d: tuple(v) for d, v in x.items()} for s, x in cache["bars"].items()}
    out: dict[str, dict[str, tuple[float, float]]] = {}
    if a is None:
        return out
    begin = (start - timedelta(days=200)).isoformat()
    for i in range(0, len(symbols), 50):
        params: dict[str, Any] = {"symbols": ",".join(symbols[i:i + 50]), "timeframe": "1Day", "start": begin,
                                  "feed": "iex", "adjustment": "split", "limit": 10000}
        while True:
            d = a._get(f"{DATA}/v2/stocks/bars", **params)
            for s, rows in (d.get("bars") or {}).items():
                out.setdefault(s, {}).update({r["t"][:10]: (float(r["o"]), float(r["c"])) for r in rows})
            if not d.get("next_page_token"):
                break
            params["page_token"] = d["next_page_token"]
    CACHE.write_text(json.dumps({"day": today, "bars": {s: {d: list(v) for d, v in x.items()} for s, x in out.items()}}))
    return out


def score(sym: str, side: int, entry: date, exit_: date, px: dict[str, dict[str, tuple[float, float]]]) -> float | None:
    """#26: side x [(close at exit / open at entry - 1) - beta x the same for QQQ]; None until the exit closed."""
    s, q = px.get(sym.replace("-", "."), {}), px.get("QQQ", {})
    e, x = entry.isoformat(), exit_.isoformat()
    if e not in s or x not in s or e not in q or x not in q:
        return None
    hist = sorted(d for d in s if d < e)[-121:]
    beta = fs.beta([s[d][1] for d in hist], [q[d][1] for d in hist if d in q]) if len(hist) > 60 else 1.0
    return side * ((s[x][1] / s[e][0] - 1) - beta * (q[x][1] / q[e][0] - 1))


def summary(r: list[float]) -> dict[str, Any]:
    if not r:
        return {"n": 0, "mean": None, "hit": None, "ci": None}
    a = np.array(r)
    rng = np.random.default_rng(7)
    boots = [float(np.mean(a[rng.integers(0, len(a), len(a))])) for _ in range(500)] if len(a) > 4 else []
    return {"n": len(a), "mean": float(a.mean()), "hit": float((a > 0).mean()),
            "ci": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))] if boots else None}


def evaluate_calls(recs: list[dict[str, Any]], px: dict[str, Any]) -> dict[str, Any]:
    rows, abstain = [], []
    for r in recs:
        if r.get("status") != "decided" or not r.get("decided_at"):
            continue
        e = entry_session(r["decided_at"])
        support = r.get("call_support") or {}
        for h, lab in (r.get("ratings") or {}).items():
            x = at.add_sessions(e, at.HOLD.get(h, 0))
            if lab == 3 or (support.get(h) or {}).get("label") == "no_call":
                m = score(r["ticker"], 1, e, x, px)
                if m is not None:
                    abstain.append(abs(m))
                continue
            side = 1 if lab > 3 else -1
            v = score(r["ticker"], side, e, x, px)
            if v is not None:
                rows.append({"ticker": r["ticker"], "horizon": h, "label": LABEL.get(lab, lab), "side": side,
                             "support": (support.get(h) or {}).get("support", "?"), "memory": bool(r.get("memory")),
                             "ret": v})
    by = lambda k: {str(g): summary([x["ret"] for x in rows if x[k] == g])
                    for g in sorted({x[k] for x in rows}, key=str)}
    return {"all": summary([x["ret"] for x in rows]), "calibration_by_label": by("label"),
            "by_horizon": by("horizon"), "by_side": by("side"), "by_support": by("support"), "by_memory": by("memory"),
            "abstention": {"n": len(abstain), "mean_abs_move_missed": float(np.mean(abstain)) if abstain else None,
                           "note": "size of the hedged move on calls the judge left without a side"},
            "target": "side x [(close at exit / open at entry - 1) - beta x QQQ's], entry = first session after the "
                      "research, exit = entry + the horizon's sessions"}


def controls(state: dict[str, Any], px: dict[str, Any]) -> dict[str, Any]:
    """#38: the AI lots against their seeded random-side and momentum-side shadows, same dates and sizes."""
    groups: dict[str, list[float]] = defaultdict(list)
    for lot in state.get("lots", []):
        if lot.get("horizon") == "day" or not lot.get("session"):
            continue
        v = score(lot["symbol"], lot["side"], date.fromisoformat(lot["session"]),
                  date.fromisoformat(lot["exit_session"]), px)
        if v is not None:
            groups["ai"].append(v)
    for c in state.get("controls", []):
        v = score(c["symbol"], c["side"], date.fromisoformat(c["session"]), date.fromisoformat(c["exit_session"]), px)
        if v is not None:
            groups[c["kind"]].append(v)
    return {k: summary(v) for k, v in groups.items()} | {"note": "shadow controls send no orders"}


def funnel(recs: list[dict[str, Any]], state: dict[str, Any], intents: dict[str, dict[str, Any]],
           universe: int) -> dict[str, Any]:
    researched = [r for r in recs if r.get("stage") == "complete" or r.get("status") in ("researched", "decided", "judge_failed")]
    decided = [r for r in recs if r.get("status") == "decided"]
    lots = state.get("lots", [])
    night = [x for x in intents.values() if x.get("strategy") == "night"]
    stages = {"companies": universe, "researched": len({r["ticker"] for r in researched}),
              "decided": len({r["ticker"] for r in decided}), "eligible_calls": len(lots),
              "risk_approved": sum(1 for x in night if x["state"] != "rejected"),
              "submitted": sum(1 for x in night if x["state"] not in ("rejected", "intended", "never_sent")),
              "filled": sum(1 for x in night if (x.get("filled_qty") or 0) > 0),
              "exited": sum(1 for x in lots if x["state"] != "open"),
              "evaluated": sum(1 for x in lots if x.get("hedged_return") is not None)}
    drops = {"research failed": Counter(str(r.get("error_reason") or r.get("error") or "?")[:80]
                                        for r in recs if r.get("status") == "research_failed").most_common(6),
             "no trade": Counter(why.split(":")[-1].strip()[:80] for v in state.get("why_no_trade", {}).values()
                                 for why in v["reasons"]).most_common(8),
             "rejected": Counter(str(x.get("note", ""))[:80] for x in night if x["state"] == "rejected").most_common(6)}
    return {"stages": stages, "drops": drops}


def orders_view(intents: dict[str, dict[str, Any]]) -> dict[str, Any]:
    recent = sorted(intents.values(), key=lambda r: r["at"])[-60:][::-1]
    return {"by_state": dict(Counter(r["state"] for r in intents.values())), "recent": recent}


def execution(a: Alpaca | None) -> dict[str, Any]:
    """#47: fills against the decision reference price in the risk audit (slippage), per strategy."""
    out: dict[str, Any] = {"slippage_bp": {}, "fills": 0}
    if a is None:
        return out
    try:
        acts = a._get(f"{PAPER}/account/activities", activity_types="FILL", direction="desc", page_size=100)
    except BrokerError:
        return out
    ref: dict[str, float] = {}
    for d in (FWD / "full_auto" / "execution", FWD / "algo" / "execution"):
        for line in (d / "ledger.jsonl").read_text().splitlines() if (d / "ledger.jsonl").exists() else []:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") == "risk" and r.get("ref_price"):
                ref[r.get("client_order_id", "")] = float(r["ref_price"])
    orders = {}
    try:
        for o in a._get(f"{PAPER}/orders", status="all", limit=500):
            orders[o["id"]] = o.get("client_order_id", "")
    except BrokerError:
        pass
    slip: dict[str, list[float]] = defaultdict(list)
    for f in acts:
        cid = orders.get(f.get("order_id", ""), "")
        r = ref.get(cid)
        if r:
            sgn = 1 if f.get("side") == "buy" else -1
            slip[cid[:2]].append(sgn * (float(f["price"]) / r - 1) * 1e4)
    out["fills"] = len(acts)
    out["slippage_bp"] = {k: {"n": len(v), "mean": float(np.mean(v))} for k, v in slip.items()}
    return out


def evidence() -> list[dict[str, Any]]:
    """#48: every strategy's evidence, failures included (results/trials_registry.jsonl and live records)."""
    reg = [json.loads(x) for x in (BACKEND / "results" / "trials_registry.jsonl").read_text().splitlines() if x.strip()]
    rows = [{"name": r["trial"], "kind": r.get("kind"), "evidence": "backtest", "sharpe": r.get("sharpe_ann"),
             "ic": r.get("ic"), "window": r.get("window"), "result": r.get("result"), "date": r.get("date")} for r in reg]
    scored = {}
    p = FWD / "day_calls" / "calls.jsonl"
    for line in p.read_text().splitlines() if p.exists() else []:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") == "score":
            scored[r["key"]] = r
    verdict = load(FWD / "day_calls" / "verdict.json", {})
    rows.append({"name": "D13 AI day calls (forward, after costs)", "kind": "daytrade", "evidence": "forward",
                 "n": len(scored), "mean_net_bp": round(1e4 * float(np.mean([x["net"] for x in scored.values()])), 1)
                 if scored else None, "result": ("pass" if verdict.get("pass") else "fail") if verdict
                 else f"running ({len(scored)} of 300 scored)"})
    return rows


def audit_exceptions(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"ticker": r["ticker"], "decided_at": r.get("decided_at"), "issues": r["audit_exceptions"]}
            for r in recs if r.get("audit_exceptions")][:50]


def risk(a: Alpaca | None, state: dict[str, Any]) -> dict[str, Any]:
    book = ab.Book()
    out: dict[str, Any] = {"states": {s: book.current(s) for s in ("night", "day")}, "ceilings": ab.CEILINGS,
                           "breakers": {"night": load(FWD / "full_auto" / "status.json", {}).get("breaker"),
                                        "day": load(FWD / "algo" / "status.json", {}).get("breaker")},
                           "controls": {s: [c for c in ab_controls(s) if c[1]] for s in ("night", "day")}}
    if a is None:
        return out
    try:
        acct = a.account()
        pos = a._get(f"{PAPER}/positions")
        orders = a._get(f"{PAPER}/orders", status="open", limit=500)
    except BrokerError as exc:
        out["error"] = str(exc)[:200]
        return out
    themes = defaultdict(list)
    for name, t in load(BACKEND / "config" / "themes_book.json", {}).get("themes", {}).items():
        for s in t["stocks"]:
            themes[s].append(name)
    out["exposure"] = ab.exposure(acct, pos, orders, state.get("betas", {}), dict(themes))
    return out


def ab_controls(strategy: str) -> list[tuple[str, bool]]:
    d = FWD / ("full_auto" if strategy == "night" else "algo")
    return [(c, (d / c).exists()) for c in ("PAUSE", "CANCEL", "FLATTEN")]


def main() -> None:
    k, s = key("AIRP_AUTO_ALPACA_KEY_ID"), key("AIRP_AUTO_ALPACA_SECRET_KEY")
    a = Alpaca(k, s, PAPER, risk_policy=ab.SANDBOX_RISK, audit_path=FWD / "account" / "report") if k and s else None
    recs = calls()
    state = load(FWD / "full_auto" / "state.json", {})
    syms = sorted({r["ticker"].replace("-", ".") for r in recs} | {"QQQ"}
                  | {c["symbol"].replace("-", ".") for c in state.get("controls", [])})
    first = min((entry_session(r["decided_at"]) for r in recs if r.get("decided_at")), default=datetime.now(NY).date())
    try:
        px = bars(a, syms, first)
    except BrokerError:
        px = {}
    intents = ab.Book().intents()
    import full_auto
    universe = len(full_auto.research_list())
    out = {"at": datetime.now(UTC).isoformat(), "calls": evaluate_calls(recs, px), "controls": controls(state, px),
           "funnel": funnel(recs, state, intents, universe), "orders": orders_view(intents), "execution": execution(a),
           "risk": risk(a, state), "evidence": evidence(), "audit_exceptions": audit_exceptions(recs),
           "incidents": sorted(ab.Book().incidents(), key=lambda r: (r["state"] != "open", r["at"]), reverse=False)[:50],
           "why_no_trade": {"night": state.get("why_no_trade", {}),
                            "day": load(FWD / "algo" / "status.json", {}).get("why_no_trade", {})},
           "feed_check": load(FWD / "algo" / "feed_check.json", {})}
    print(json.dumps(out, default=str))


if __name__ == "__main__":
    main()
