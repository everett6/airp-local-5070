"""Compare two research sub-agents on the same (month, ticker) tasks: does a smaller model research as well?

    python scripts/compare_research.py analyst analyst_jan

Per agent, over the tasks both finished: parsed brief, verified facts per brief (facts that survived the source check
in agent_worker.verify_brief), facts dropped by that check, tool calls and successful tool calls, JSON parse
failures, seconds per task. Rule fixed before the run (docs/PLAN_V2.md): the challenger replaces the incumbent only
with at least as many verified facts per brief AND at least 1.5x faster (or smaller on the GPU, noted by hand).
Writes results/compare_<a>_vs_<b>.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

BACKEND = Path(__file__).resolve().parents[1]


def load(run: str) -> dict[tuple[str, str], dict]:
    return {(p.parent.name, p.stem): json.loads(p.read_text()) for p in (BACKEND / "results" / run).glob("*/*.json")}


def stats(rs: list[dict]) -> dict[str, float]:
    def brief(r: dict) -> dict:
        return r.get("brief") or {}
    calls = [c for r in rs for s in r.get("steps", []) for c in s.get("calls", [])]
    return {"tasks": len(rs), "model": rs[0].get("model", "") if rs else "",
            "brief_parsed_pct": round(100 * mean(bool(brief(r).get("parsed")) for r in rs), 1),
            "verified_facts_per_brief": round(mean(len(brief(r).get("facts", [])) for r in rs), 2),
            "dropped_facts_per_brief": round(mean(len(brief(r).get("dropped", [])) for r in rs), 2),
            "tool_calls_per_task": round(len(calls) / len(rs), 2),
            "tool_ok_pct": round(100 * mean(bool(c.get("ok")) for c in calls), 1) if calls else 0.0,
            "answered_pct": round(100 * mean(bool(r.get("answered")) for r in rs), 1),
            "parse_failures_per_task": round(mean(r.get("parse_failures", 0) for r in rs), 2),
            "seconds_per_task": round(mean(float(r.get("elapsed_s") or 0) for r in rs), 1)}


def main() -> None:
    a_run, b_run = sys.argv[1], sys.argv[2]
    a, b = load(a_run), load(b_run)
    common = sorted(set(a) & set(b))
    res = {"tasks_in_common": len(common), a_run: stats([a[k] for k in common]), b_run: stats([b[k] for k in common])}
    sa, sb = res[a_run], res[b_run]
    speed = sa["seconds_per_task"] / max(sb["seconds_per_task"], 0.1)
    res["challenger_speedup"] = round(speed, 2)
    res["challenger_passes"] = bool(sb["verified_facts_per_brief"] >= sa["verified_facts_per_brief"] and speed >= 1.5)
    (BACKEND / "results" / f"compare_{a_run}_vs_{b_run}.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
