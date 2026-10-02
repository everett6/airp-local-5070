"""The extraction benchmark: hand-labelled figures for hard releases (docs/PLAN_60_V2.md, "Outside review, second
part", new records). A yardstick, not a trial: run it before a reader or a prompt replaces the live one.

    python scripts/extraction_benchmark.py                       # score the reader records already on disk
    python scripts/extraction_benchmark.py --extract results/events/bench_new_reader.jsonl
    # to score a new reader or prompt, first read the benchmark's releases with it (needs the GPU):
    #   python scripts/extract_events.py extract --events benchmarks/extraction/events.csv --from 2000-01-01 \
    #       --to 2099-12-31 --model <model> --out results/events/bench_<name>.jsonl

The gold file (`benchmarks/extraction/gold.json`) holds, per release: the quarter's end, revenue, GAAP diluted EPS
and adjusted EPS for the quarter and for the same quarter a year earlier, and the guidance label where the release
states one plainly; plus the *traps*, the other figures printed next to the right one. A field is in the gold only
if it was read in the release, and only labelled fields are scored.

A reader's number is not just right or wrong. Each scored field ends in one of:
  correct           the labelled figure (within 0.5%, or half a cent for EPS)
  wrong_period      the figure of another period: the quarter before, the half year, the full year, an outlook
  wrong_units       off by a factor of a thousand or a million (thousands or billions read as millions)
  wrong_basis       the same item on another accounting basis (net sales for total revenue, managed for reported,
                    adjusted for GAAP or the other way round)
  wrong_sign        a loss read as a profit
  missed            the release states it and the reader returned nothing
  invented          the release does not state it and the reader returned a number
  wrong             none of the above
and, for a field the release does not state: correct_absent when the reader says so. "A number that appears in a
quote" is not the test: the period, the units, the basis and honest absence are.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.forward.ledger import jsonl_records

GOLD = BACKEND / "benchmarks" / "extraction" / "gold.json"
FIELDS = ("revenue", "eps", "adj_eps")
DEFAULT_EXTRACTS = ("results/events/extract_qwen3_8b.jsonl", "results/forward/events/extract.jsonl")


def close(a: float, b: float, eps: bool) -> bool:
    return abs(a - b) <= (0.005 + 1e-9 if eps else 0.005 * max(abs(a), abs(b)))


def judge(got: Any, want: float | None, field: str, slot: str, case: dict[str, Any]) -> str:
    """The outcome of one number. `want` None with the field marked absent means the release does not state it."""
    g = case.get(field, {})
    eps = field != "revenue"
    has = isinstance(got, int | float) and not isinstance(got, bool)
    if want is None:
        return "invented" if has else "correct_absent"
    if not has:
        return "missed"
    x = float(got)
    if close(x, want, eps):
        return "correct"
    if want != 0 and close(-x, want, eps):
        return "wrong_sign"
    others = [float(v) for v in g.get("traps", {}).values()]
    others += [float(v) for k in ("q", "prior") if k != slot and (v := g.get(k)) is not None]  # the other column
    if any(close(x, o, eps) for o in others):
        return "wrong_period"
    basis = [float(b[slot]) for b in g.get("other_basis", {}).values() if b.get(slot) is not None]
    twin = case.get("adj_eps" if field == "eps" else "eps", {}) if eps else {}
    basis += [float(twin[k]) for k in ("q", "prior") if twin.get(k) is not None]  # GAAP for adjusted, or back
    if any(close(x, o, eps) for o in basis):
        return "wrong_basis"
    if not eps and any(close(x * f, want, False) for f in (1e3, 1e-3, 1e6, 1e-6)):
        return "wrong_units"
    return "wrong"


def score_case(case: dict[str, Any], rec: dict[str, Any] | None) -> dict[str, str]:
    """Outcome per labelled field of one release: "revenue.q", "eps.prior", "period_end", "guidance", ..."""
    out: dict[str, str] = {}
    rec = rec or {}
    if "period_end" in case:
        got = rec.get("period_end")
        out["period_end"] = "missed" if not got else "correct" if str(got)[:10] == case["period_end"] else "wrong_period"
    for f in FIELDS:
        g = case.get(f)
        if g is None:
            continue
        r = rec.get(f) or {}
        for slot in ("q", "prior"):
            if slot in g:
                out[f"{f}.{slot}"] = judge(r.get(slot), g[slot], f, slot, case)
    if "guidance" in case:
        got = rec.get("guidance")
        out["guidance"] = "missed" if not got else "correct" if got == case["guidance"] else "wrong"
    return out


def score(gold: dict[str, Any], records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    tally: Counter[str] = Counter()
    by_cat: dict[str, Counter[str]] = {}
    unread: list[str] = []
    for c in gold["cases"]:
        rec = records.get(c["accession"])
        if rec is None or rec.get("model") == "none":
            unread.append(c["ticker"])
            continue
        res = score_case(c, rec)
        rows.append({"ticker": c["ticker"], "accession": c["accession"], "model": rec.get("model"), "fields": res})
        tally.update(res.values())
        for cat in c["categories"]:
            k = by_cat.setdefault(cat, Counter())
            k.update("ok" if v in ("correct", "correct_absent") else "not_ok" for v in res.values())
    n = sum(tally.values())
    ok = tally["correct"] + tally["correct_absent"]

    def share(fields: tuple[str, ...], good: tuple[str, ...]) -> dict[str, Any]:
        vals = [v for r in rows for k, v in r["fields"].items() if k.startswith(fields)]
        return {"scored": len(vals), "right": sum(v in good for v in vals)}
    stated = [v for r in rows for v in r["fields"].values() if v not in ("correct_absent", "invented")]
    absent = [v for r in rows for v in r["fields"].values() if v in ("correct_absent", "invented")]
    return {
        "gold_version": gold["version"], "cases": len(gold["cases"]), "scored_cases": len(rows), "not_read": unread,
        "fields": n, "right": ok, "accuracy": round(ok / n, 3) if n else None, "outcomes": dict(tally.most_common()),
        "dimensions": {
            "period": {"note": "the quarter's end date, and no figure taken from another period",
                       "period_end": share(("period_end",), ("correct",)),
                       "figures_from_another_period": tally["wrong_period"]},
            "units": {"wrong_units": tally["wrong_units"]},
            "accounting_basis": {"wrong_basis": tally["wrong_basis"], "wrong_sign": tally["wrong_sign"]},
            "missing_information": {"stated_and_found": sum(v == "correct" for v in stated), "stated": len(stated),
                                    "stated_and_missed": tally["missed"],
                                    "absent_and_said_so": sum(v == "correct_absent" for v in absent),
                                    "absent": len(absent), "invented": tally["invented"]},
            "guidance": share(("guidance",), ("correct",)),
        },
        "by_category": {k: {"right": v["ok"], "scored": v["ok"] + v["not_ok"]} for k, v in sorted(by_cat.items())},
        "rows": rows,
    }


def load_records(paths: list[Path]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in paths:
        for r in jsonl_records(p):
            out[r["accession"]] = r  # a later file wins: the live reader's record over the backtest's
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extract", action="append", help="reader output (jsonl); may be given more than once")
    ap.add_argument("--out", default="", help="write the full result here (json)")
    a = ap.parse_args()
    gold = json.loads(GOLD.read_text())
    rep = score(gold, load_records([BACKEND / p for p in (a.extract or DEFAULT_EXTRACTS)]))
    if a.out:
        (BACKEND / a.out).write_text(json.dumps(rep, indent=1) + "\n")
    print(f"extraction benchmark v{rep['gold_version']}: {rep['right']} of {rep['fields']} labelled fields right "
          f"({rep['accuracy']}), {rep['scored_cases']} of {rep['cases']} releases read"
          + (f"; not read: {', '.join(rep['not_read'])}" if rep["not_read"] else ""))
    print("  outcomes:", ", ".join(f"{k} {v}" for k, v in rep["outcomes"].items()))
    d = rep["dimensions"]
    print(f"  period: quarter-end right {d['period']['period_end']['right']} of {d['period']['period_end']['scored']}; "
          f"figures from another period {d['period']['figures_from_another_period']}")
    print(f"  units: wrong {d['units']['wrong_units']};  basis: wrong {d['accounting_basis']['wrong_basis']}, "
          f"wrong sign {d['accounting_basis']['wrong_sign']}")
    m = d["missing_information"]
    print(f"  stated figures found {m['stated_and_found']} of {m['stated']} (missed {m['stated_and_missed']}); "
          f"absent figures left empty {m['absent_and_said_so']} of {m['absent']} (invented {m['invented']})")
    for r in rep["rows"]:
        bad = {k: v for k, v in r["fields"].items() if v not in ("correct", "correct_absent")}
        print(f"  {r['ticker']:5} " + ("all right" if not bad else ", ".join(f"{k}: {v}" for k, v in bad.items())))


if __name__ == "__main__":
    main()
