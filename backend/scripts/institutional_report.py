"""Read-only engineering evidence. Missing observations never become a passing trading qualification."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from extraction_benchmark import load_records, score

from app.forward.benchmark_review import materials
from app.forward.ledger import Ledger, jsonl_records, write_atomic
from app.portfolio.measurement import cost_scope, execution, factors, recorded_fills


def read(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None


def attribution(backend: Path) -> dict[str, Any]:
    """Daily marked sleeve returns and same-date cached close returns; no data requests."""
    book = read(backend / "results/forward/ai_picks/book.json") or {}
    history = book.get("history", [])
    if len(history) < 2:
        return {"status": "insufficient_data", "observations": 0, "required": 120}
    equity = pd.Series({pd.Timestamp(h["day"]): float(h["equity"]) for h in history}).sort_index()
    panels = []
    events = backend / "results/forward/events/prices.parquet"
    if events.exists():
        data = pd.read_parquet(events)
        panels.append(data.pivot_table(index="Date", columns="Ticker", values="Close", aggfunc="last"))
    cached = backend / "data/trend/etf_closes.parquet"
    if cached.exists():
        panels.append(pd.read_parquet(cached))
    crypto = backend / "data/crypto/ohlcv_crypto.parquet"
    if crypto.exists():
        data = pd.read_parquet(crypto)
        panels.append(data.pivot_table(index="Date", columns="Ticker", values="Close", aggfunc="last"))
    allocator = backend / "results/forward/allocator/factor_closes.parquet"
    if allocator.exists():
        panels.insert(0, pd.read_parquet(allocator))
    prices = pd.DataFrame()
    for panel in panels:
        panel.index = pd.to_datetime(panel.index).tz_localize(None).normalize()
        prices = prices.combine_first(panel)
    requested = ["SPY", "BTC-USD", "ETH-USD", "QQQ", "TLT", "XLF", "XLK", "XLE", "XLV", "XLI"]
    available = [c for c in requested if c in prices.columns]
    if "SPY" not in available:
        return {"status": "missing_factor_prices", "missing": requested}
    # All returns span the same exchange sessions, including crypto's weekend movement.
    # A missing equity mark removes both affected returns rather than pairing a two-day
    # portfolio change with a one-day market change.
    calendar = prices["SPY"].dropna().sort_index().index
    panel = prices[available].reindex(calendar)
    portfolio = equity.reindex(calendar).pct_change(fill_method=None).dropna()
    market = panel.pct_change(fill_method=None)
    coverage: dict[str, dict[str, Any]] = {c: {"matched": int(market[c].reindex(portfolio.index).notna().sum()),
                    "last_close": str(prices[c].dropna().index.max().date())} for c in available if prices[c].notna().any()}
    optional_missing = [c for c in ("QQQ", "TLT") if c in available and coverage.get(c, {}).get("matched", 0) < 120]
    market = market.drop(columns=optional_missing)
    result = factors(portfolio, market)
    result.update(book="AI paper sleeve", frequency="daily", missing_factors=[c for c in requested if c not in available] + optional_missing,
                  factor_coverage=coverage,
                  source="local close caches; no forward filling", costs="book simulation costs; broker costs measured separately")
    return result


def report(backend: Path) -> dict[str, Any]:
    fwd = backend / "results/forward"
    reviewed = materials(backend)
    approved = [m["review"]["corrected_case"] for m in reviewed if m["approved"]]
    gold = read(backend / "benchmarks/extraction/gold.json")
    records = load_records([backend / "benchmarks/extraction/reader_live.jsonl"])
    benchmark: dict[str, Any] = {"cases": len(reviewed), "human_attested": len(approved), "remaining": len(reviewed) - len(approved),
                 "reader_change_gate": "ready_for_review" if len(approved) == len(reviewed) and approved else "needs_human_labels",
                 "human_score": score({**gold, "cases": approved}, records) if approved else None,
                 "provisional_score": score(gold, records), "label_origin": "Original labels remain provisional until a person reviews the source."}
    reader_scores = []
    paths = sorted((backend / "benchmarks/extraction").glob("reader*.jsonl"))
    paths += sorted((fwd / "events/jan").glob("extract_*.jsonl"))
    for path in paths:
        candidate_records = load_records([path])
        reader_scores.append({"source": str(path.relative_to(backend)),
            "provisional": score(gold, candidate_records),
            "human_verified": score({**gold, "cases": approved}, candidate_records) if approved else None})
    benchmark["readers"] = reader_scores
    audit = fwd / "execution/ledger.jsonl"
    costs = read(fwd / "execution/costs.json")
    audit_hash = hashlib.sha256(audit.read_bytes()).hexdigest() if audit.exists() else None
    scope = cost_scope(backend)
    applicable = costs and costs.get("cost_scope_sha256", costs.get("ledger_sha256")) == scope
    legacy = recorded_fills(backend)
    fills = execution(Ledger(audit).verify() if audit.exists() else [], costs["costs"] if applicable else None, legacy)
    fills.update(ledger_sha256=audit_hash, cost_scope_sha256=scope, cost_review=costs, cost_review_current=bool(applicable))
    ops = backend / "results/ops"
    index = ops / "index.jsonl"
    snapshots = Ledger(index).verify() if index.exists() else []
    steps = fwd / "step_records.jsonl"
    structured = Ledger(steps).verify() if steps.exists() else []
    reviews = ops / "reviews.jsonl"
    release_reviews = Ledger(reviews).verify() if reviews.exists() else []
    candidates = sorted((ops / "releases").glob("*/manifest.json"))
    candidate = read(candidates[-1]) if candidates else None
    return {"at": datetime.now(UTC).isoformat(), "benchmark": benchmark,
            "risk": {"policy": read(backend / "config/account_risk.json"), "enforcement": "before paper submissions across both books",
                     "source": "fresh account, positions, outstanding orders and quotes; missing snapshots block submission"},
            "execution": fills, "attribution": attribution(backend),
            "release": {"snapshots": [{k: r.get(k) for k in ("id", "kind", "archive_sha256")} for r in snapshots[-10:]],
                        "candidate": {k: candidate[k] for k in ("id", "archive_sha256", "created_at")} if candidate else None,
                        "reviews": release_reviews[-5:],
                        "independent_review": "Local attestations recorded; reviewer independence must be established separately" if release_reviews else "pending; automated checks do not constitute independent approval"},
            "recovery": {"last_restore": read(ops / "restore_review.json"),
                         "off_machine_copy": "Portable archives are local. Copy a verified archive to separate storage for PC-loss recovery."},
            "steps": {"native": sum(r["protocol"] == "native" for r in structured),
                      "legacy": sum(r["protocol"] == "legacy" for r in structured), "recent": structured[-15:]},
            "opportunity_funnel": read(fwd / "funnel.json"), "throughput": read(fwd / "throughput.json"),
            "ai_contribution": read(fwd / "ai_contribution.json"),
            "evidence_bundles": len(jsonl_records(fwd / "events/evidence/index.jsonl"))}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    result = report(BACKEND)
    if args.write:
        write_atomic(BACKEND / "results/forward/institutional.json", json.dumps(result, indent=1, allow_nan=False))
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
