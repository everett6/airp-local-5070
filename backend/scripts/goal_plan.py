"""Break a dollar goal by a date into what each strategy track must earn, with the odds on today's evidence.

    python scripts/goal_plan.py --start 10000 --target 25000 --by 2029-12-31
    python scripts/goal_plan.py            # the saved goal (results/planner/goal.json)

Needs results/planner/track_returns.parquet (scripts/track_returns.py). Writes results/planner/plan.json. Paper
money; the planner only reports: it never changes the book's weights (app/portfolio/planner.py).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import pandas as pd

from app.portfolio.planner import Goal, plan

DIR = BACKEND / "results" / "planner"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=float)
    ap.add_argument("--target", type=float)
    ap.add_argument("--by", help="YYYY-MM-DD")
    a = ap.parse_args()
    saved = json.loads((DIR / "goal.json").read_text()) if (DIR / "goal.json").exists() else {}
    g = {"start": a.start or saved.get("start"), "target": a.target or saved.get("target"), "by": a.by or saved.get("by")}
    if None in g.values():
        raise SystemExit("give --start, --target and --by once (they are then saved)")
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / "goal.json").write_text(json.dumps(g) + "\n")
    goal = Goal(float(str(g["start"])), float(str(g["target"])), date.fromisoformat(str(g["by"])),
                datetime.now(UTC).date())
    res = plan(goal, pd.read_parquet(DIR / "track_returns.parquet"))
    (DIR / "plan.json").write_text(json.dumps(res, indent=1) + "\n")
    q, n, c = res["goal"], res["with_current_evidence"], res["core_only"]
    print(f"Goal: ${q['start']:,.0f} -> ${q['target']:,.0f} by {q['by']} ({q['years']} years): needs "
          f"{100 * q['required_cagr']:.1f}% a year")
    print(f"Today's evidence-based mix {res['weights']}: odds {100 * n['p_goal']:.0f}%, median ${n['median_end']:,.0f} "
          f"({100 * n['median_cagr']:.1f}%/yr), bad case (5%) ${n['p5_end']:,.0f}, median max drawdown "
          f"{100 * n['median_max_dd']:.0f}%")
    print(f"Core only: odds {100 * c['p_goal']:.0f}%, median ${c['median_end']:,.0f}")
    print(f"Gap: {100 * res['gap_cagr']:+.1f} points a year")
    print("Stock picking would need, per year, at this share of the book: "
          + ", ".join(f"{k}: {100 * v:.0f}%" for k, v in res["picking_needs_cagr"].items()))
    for t in res["tracks"]:
        h = "" if t["history_cagr"] is None else f", history {100 * t['history_cagr']:+.1f}%/yr"
        print(f"  {t['label']}: {t['status']}, weight {100 * t['weight']:.0f}%{h}. Next: {t['unlock']}")
    for f in res["flags"]:
        print("  !", f)


if __name__ == "__main__":
    main()
