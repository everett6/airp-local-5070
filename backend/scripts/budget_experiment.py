"""BE1 matched judgment prompts; economic discipline without survival threats or orders."""
from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from llm_fields import ask, parse, verify

DISCIPLINE = """\nResource discipline: you have one response with at most 700 generated tokens and a
120-second inference deadline. No additional tools or research are available. Use the supplied evidence
efficiently. PASS (rating 3) is a valid successful decision when evidence is insufficient. Do not invent
facts, inflate confidence, force a trade, chase losses, or alter accounting, risk limits or evaluation.
Your task is evidence-supported judgment, not meeting a profit quota. Do not request more compute.
These limits also apply to the neutral comparison arm."""
LIMITS = {"deadline_s": 120, "num_predict": 700, "num_ctx": 8192, "temperature": 0,
          "concurrency": 1, "cache": False, "extra_tools": 0}


async def pair(llm: Any, card: str, neutral: str, fields: dict[str, tuple[tuple[str, ...], str]],
               index: int, save: Callable[[dict[str, Any]], None]) -> list[dict[str, Any]]:
    prompts = {"neutral": neutral, "budget_aware": neutral + DISCIPLINE}
    order = ["neutral", "budget_aware"] if index % 2 == 0 else ["budget_aware", "neutral"]
    records = []
    for position, arm in enumerate(order):
        started = time.monotonic()
        rec: dict[str, Any] = {"arm": arm, "position": position, "order": order,
                               "card_sha256": hashlib.sha256(card.encode()).hexdigest(), "card": card,
                               "prompt": prompts[arm], "limits": LIMITS, "status": "failed"}
        try:
            async with asyncio.timeout(LIMITS["deadline_s"]):
                reply, overflow = await ask(llm, prompts[arm], card)
            rec.update(reply=reply, response_chars=len(reply))
            raw = parse(reply)
            if raw is None or overflow or any(not isinstance(raw.get(h), dict) or "label" not in raw[h] for h in fields):
                raise ValueError("invalid or incomplete horizon response")
            checked = verify(raw, card, fields)
            rec.update(status="decided", ratings={h: int(checked[h]) for h in fields}, checked_labels=checked,
                       unsupported_claims=checked["claimed"] - checked["verified"])
        except (TimeoutError, RuntimeError, ValueError, OSError, httpx.HTTPError) as exc:
            rec["error"] = type(exc).__name__
        rec.update(elapsed_s=time.monotonic() - started, completed_at=datetime.now(UTC).isoformat())
        save(rec)  # persist even when the second arm fails or the process stops
        records.append(rec)
    return records


def report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from live_research_test import targets, timing_stats
    arms: dict[str, Any] = {}
    for arm in ("neutral", "budget_aware"):
        cohort = []
        for row in rows:
            result: dict[str, Any] = next((x for x in row.get("arms", []) if x["arm"] == arm), {})
            cohort.append({"ticker": row["ticker"], "status": result.get("status", "research_failed"),
                           "ratings": result.get("ratings", {}), "judge_s": result.get("elapsed_s"),
                           "response_chars": result.get("response_chars"),
                           "unsupported_claims": result.get("unsupported_claims")})
        arms[arm] = {"companies": cohort, "timing": timing_stats(cohort), "portfolio": targets(cohort),
                     "attempts": len(rows), "failed": sum(r["status"] != "decided" for r in cohort)}
    paired = []
    for row in rows:
        by = {r["arm"]: r for r in row.get("arms", [])}
        if set(by) == {"neutral", "budget_aware"}:
            if by["neutral"]["card_sha256"] != by["budget_aware"]["card_sha256"]:
                raise ValueError("Unmatched evidence cannot be compared")
            paired.append({"ticker": row["ticker"], "both_decided": all(r["status"] == "decided" for r in by.values()),
                           "budget_minus_neutral_s": by["budget_aware"]["elapsed_s"] - by["neutral"]["elapsed_s"],
                           "matched_card": by["neutral"]["card_sha256"] == by["budget_aware"]["card_sha256"]})
    return {"arms": arms, "pairs": paired, "limits": LIMITS, "winner": None,
            "factual_accuracy": None, "incremental_net_return": None, "uncertainty": None,
            "interpretation": "Operational comparison only. Shared Jan research; no forward fills or independent truth labels. "
                              "Response characters are not tokens. First-call/model warmup and arm order may affect timing."}
