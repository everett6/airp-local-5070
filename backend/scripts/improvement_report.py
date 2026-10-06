"""Inspect the existing learning engines; --review evaluates existing shadows without new trials or trades."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import learn_loop
import self_improve

from app.forward.ledger import Ledger, LedgerError, jsonl_records
from app.signals import registry as R


def report(events: Path, signals: Path, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    errors: list[str] = []
    prompt: dict[str, Any] = {"available": False}
    recipes: dict[str, Any] = {"available": False}
    try:
        if (events / "ledger.jsonl").exists():
            Ledger(events / "ledger.jsonl").verify()
        vs = self_improve.versions(events)
        champ, challenger = self_improve.champion(vs), self_improve.challenger(vs)
        matured = len(self_improve.matured(events, champ["v"]))
        comparison = None
        if challenger is not None:
            xs, counts = self_improve.weekly_scores(events, champ["v"], challenger["v"],
                                                   challenger["created_at"], now)
            comparison = counts | {"weeks": len(xs), "e_better": self_improve.e_process(xs),
                                   "e_worse": self_improve.e_process([-x for x in xs])}
        prompt = {"available": True, "champion": champ, "challenger": challenger,
                  "matured": matured, "reflection_min": self_improve.MIN_MATURED,
                  "remaining": max(0, self_improve.MIN_MATURED - matured),
                  "comparison": comparison, "gates": self_improve.PROMOTE,
                  "history": vs[-20:], "reflection_due": self_improve.reflect_due(vs, now)}
    except (OSError, ValueError, KeyError, TypeError, StopIteration, LedgerError) as exc:
        errors.append(f"Prompt learning unavailable: {type(exc).__name__}: {str(exc)[:180]}")
    try:
        registry = R.Registry(signals / "registry.json")
        review_path = signals / "review.json"
        review = json.loads(review_path.read_text()) if review_path.exists() else None
        monthly = jsonl_records(signals / "monthly.jsonl")
        recipes = {"available": True, "counts": {s: len(registry.with_status(s)) for s in R.STATUSES},
                   "holdout_tests": registry.holdout_tests, "review": review,
                   "monthly": monthly[-12:], "signals": [
                       {"name": s.name, "status": s.status, "recipe": s.key(), "rationale": s.rationale,
                        "proposer": s.proposer, "history": s.history[-12:]} for s in registry.signals.values()],
                   "gates": {"proposals_per_month": R.PROPOSALS_PER_MONTH, "min_days": R.SHADOW_MIN_DAYS,
                             "min_events": R.LIVE_MIN_EVENTS, "retire_days": R.SHADOW_MAX_DAYS,
                             "look_days": R.LOOK_DAYS, "sleeve_cap": R.SLEEVE_CAP}}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"Signal learning unavailable: {type(exc).__name__}: {str(exc)[:180]}")
    return {"at": now.isoformat(), "scope": "shadow", "live_strategy_changed": False,
            "prompt": prompt, "recipes": recipes, "errors": errors,
            "automatic_reviews": jsonl_records(signals / "automation.jsonl")[-20:],
            "schedule": {"prompt": "After automatic earnings runs; reflection at most monthly when eligible",
                         "recipes": "Monthly proposals during Saturday review; existing shadows reviewed weekly"}}


def review_existing(events: Path, signals: Path, now: datetime | None = None) -> dict[str, Any]:
    """Only score previously created candidates. Never call reflect(), monthly(), register(), or the broker."""
    now = now or datetime.now(UTC)
    if (events / "ledger.jsonl").exists():
        Ledger(events / "ledger.jsonl").verify()
    before = report(events, signals, now)
    if before["errors"]:
        raise ValueError("; ".join(before["errors"]))
    if (events / "ledger.jsonl").exists():
        vs = self_improve.versions(events)
        message = self_improve.judge(events, vs, now)
        if message:
            print("Prompt shadow:", message, flush=True)
        self_improve.save(events, vs)
    # Capture immutable decision-time features before evaluating the existing signal shadows.
    learn_loop.collect(events, signals)
    learn_loop.review(events, signals, today=now.date())
    result = report(events, signals, now)
    old_champion = before['prompt'].get('champion', {})
    new_champion = result['prompt'].get('champion', {})
    prompt_changed = (old_champion.get('v'), old_champion.get('lessons')) != (new_champion.get('v'), new_champion.get('lessons'))
    previous = {s['name']: s['status'] for s in before['recipes'].get('signals', [])}
    transitions = [{'name': s['name'], 'before': previous.get(s['name']), 'after': s['status']}
                   for s in result['recipes'].get('signals', []) if previous.get(s['name']) != s['status']]
    Ledger(signals / 'automation.jsonl').append('learning_review', at=now.isoformat(),
        status='failed' if result['errors'] else 'reviewed', scope='shadow', live_strategy_changed=False,
        prompt_changed=prompt_changed, signal_transitions=transitions, errors=result['errors'],
        message='Existing candidates evaluated; evidence gates control qualification, retirement and rollback.')
    if result["errors"]:
        raise ValueError("; ".join(result["errors"]))
    return report(events, signals, now)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--dir", default="results/forward/events")
    ap.add_argument("--out", default="results/forward/signals")
    args = ap.parse_args()
    events, signals = BACKEND / args.dir, BACKEND / args.out
    result = review_existing(events, signals) if args.review else report(events, signals)
    print(json.dumps(result, allow_nan=False, indent=1))


if __name__ == "__main__":
    main()
