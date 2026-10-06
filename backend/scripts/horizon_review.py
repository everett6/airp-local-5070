"""Refresh HS1 forward price-proxy outcomes. No model calls, strategy fitting or orders."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.forward.ledger import Ledger, write_atomic
from app.forward.step_result import emit
from app.portfolio.broker import DATA, PAPER, Alpaca, BrokerError
from app.portfolio.horizon_shadow import evaluate

NY = ZoneInfo("America/New_York")


def session_time(day: str, value: str) -> str:
    stamp = datetime.fromisoformat(value if "T" in value else f"{day}T{value}")
    return (stamp.replace(tzinfo=NY) if stamp.tzinfo is None else stamp).isoformat()


def refresh(client: Alpaca, cohorts: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    if not cohorts:
        return {"status": "no_prospective_cohorts", "vintages": [], "at": now.isoformat()}
    start = min(datetime.fromisoformat(c["horizon_plan"]["decided_at"]).astimezone(NY).date() for c in cohorts).isoformat()
    end = now.astimezone(NY).date().isoformat()
    names = sorted({a for c in cohorts for arm in c["horizon_plan"]["arms"].values() for weights in arm.values() for a in weights})
    if not names or any(not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", a) for a in names):
        raise ValueError("Invalid or empty HS1 symbol list")
    calendar = client._get(f"{PAPER}/calendar", start=start, end=end)
    sessions = [{"date": s["date"], "open_at": session_time(s["date"], s["open"]),
                 "close_at": session_time(s["date"], s["close"])} for s in calendar]
    bars: dict[str, dict[str, dict[str, float]]] = {}
    params: dict[str, Any] = {"symbols": ",".join(names), "timeframe": "1Day", "start": start,
                              "end": now.isoformat(), "feed": "iex", "adjustment": "split", "limit": 10000}
    for _ in range(20):
        data = client._get(f"{DATA}/v2/stocks/bars", **params)
        for asset, values in data.get("bars", {}).items():
            for b in values:
                day = datetime.fromisoformat(b["t"]).astimezone(NY).date().isoformat()
                bars.setdefault(asset, {})[day] = {"open": float(b["o"]), "close": float(b["c"])}
        token = data.get("next_page_token")
        if not token:
            break
        params["page_token"] = token
    else:
        raise ValueError("Incomplete bar pagination; no outcomes saved")
    vintages = [{"id": Path(c["directory"]).name, "plan": c["horizon_plan"],
                 "outcomes": evaluate(c["horizon_plan"], sessions, bars, now)} for c in cohorts]
    return {"status": "ok", "at": now.isoformat(), "vintages": vintages,
            "market_inputs": {"sessions": sessions, "bars": bars, "feed": "iex", "adjustment": "split"},
            "note": "Separate locked virtual vintages. Price proxies and assumed costs; no broker fills, pooled leverage or automatic strategy promotion."}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    out = BACKEND / "results/forward/horizon_shadow"
    if args.status:
        print((out / "summary.json").read_text() if (out / "summary.json").exists() else '{"status":"not_run","vintages":[]}')
        return
    client = None
    try:
        cohorts = [json.loads(p.read_text()) for p in sorted((BACKEND / "results/forward/live_research").glob("*/summary.json"))]
        cohorts = [c for c in cohorts if c.get("horizon_plan", {}).get("protocol") == "HS1"]
        client = Alpaca.from_env()
        if client is None:
            raise BrokerError("Paper account not configured")
        result = refresh(client, cohorts, datetime.now(UTC))
        out.mkdir(parents=True, exist_ok=True)
        Ledger(out / "ledger.jsonl").append("observation", **result)
        write_atomic(out / "summary.json", json.dumps(result, allow_nan=False))
        print(json.dumps({k: v for k, v in result.items() if k != "market_inputs"}))
        emit("ok", vintages=len(cohorts), orders_submitted=0)
    except (ValueError, KeyError, TypeError, OSError, BrokerError, httpx.HTTPError) as exc:
        emit("failed", reason=type(exc).__name__, orders_submitted=0)
        raise SystemExit(1) from None
    finally:
        if client is not None:
            client.c.close()


if __name__ == "__main__":
    main()
