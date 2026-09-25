"""Compare two readers on the same earnings releases and print the better one's model name (last line).

    python scripts/bench_readers.py results/events/bench_qwen3_8b.jsonl results/events/bench_nuextract.jsonl

Per reader: share of releases with a verified revenue pair (quarter + year earlier), a verified diluted-EPS pair,
numbers rejected by the quote check, and seconds per release (from the extraction logs if given). Where both
readers verified the same field, how often they agree (within 0.5%). The winner has the most verified pairs;
ties go to the faster one. Writes results/events/bench_readers.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def load(path: str) -> dict[str, dict]:
    return {json.loads(x)["accession"]: json.loads(x) for x in (BACKEND / path).read_text().splitlines()}


def pair(r: dict, key: str) -> bool:
    return isinstance(r.get(key), dict) and {"q", "prior"} <= set(r[key])


def main() -> None:
    a_path, b_path = sys.argv[1], sys.argv[2]
    times = [float(x) for x in sys.argv[3:5]] if len(sys.argv) >= 5 else [0.0, 0.0]
    a, b = load(a_path), load(b_path)
    common = sorted(set(a) & set(b))
    out = {}
    for name, d, t in ((a_path, a, times[0]), (b_path, b, times[1])):
        rs = [d[k] for k in common]
        out[name] = {"model": rs[0]["model"] if rs else "", "releases": len(rs),
                     "revenue_pair_pct": round(100 * sum(pair(r, "revenue") for r in rs) / max(1, len(rs)), 1),
                     "eps_pair_pct": round(100 * sum(pair(r, "eps") for r in rs) / max(1, len(rs)), 1),
                     "rejected_per_release": round(sum(len(r["rejected"]) for r in rs) / max(1, len(rs)), 2),
                     "guidance_found_pct": round(100 * sum(r["guidance"] not in ("none", "unverified") for r in rs)
                                                 / max(1, len(rs)), 1),
                     "seconds_per_release": t}
    agree = total = 0
    for k in common:
        for key in ("revenue", "eps"):
            for part in ("q", "prior"):
                x, y = a[k].get(key, {}).get(part), b[k].get(key, {}).get(part)
                if x is not None and y is not None:
                    total += 1
                    agree += abs(x - y) <= 0.005 * max(abs(x), abs(y), 0.01)
    res = {"readers": out, "fields_both_verified": total,
           "agreement_pct": round(100 * agree / total, 1) if total else None}

    def score(v: dict) -> tuple[float, float]:
        return (v["revenue_pair_pct"] + v["eps_pair_pct"], -v["seconds_per_release"])
    winner = max(out.values(), key=score)
    res["winner"] = winner["model"]
    (BACKEND / "results" / "events" / "bench_readers.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))
    print(winner["model"])


if __name__ == "__main__":
    main()
