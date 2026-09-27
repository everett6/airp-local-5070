"""Random-ranking control for a book's portfolio: shuffle the scores across releases (seeded), run the same master
portfolio, and report where the real result falls among the shuffles.

    python scripts/shuffle_control.py --book results/events/decide_combined_h5.jsonl:5 --seeds 50

The real run's CAGR/Sharpe is compared with the shuffled runs; a one-sided p-value is (shuffles >= real + 1) /
(seeds + 1). Everything else (events, calibration, sizing, costs) is identical. Writes results/shuffle_control_<name>.json.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def run(book: str, tag: str, args: argparse.Namespace) -> dict:
    subprocess.run([sys.executable, str(BACKEND / "scripts" / "master_portfolio.py"), "full", "--start", args.start,
                    "--events", args.events, "--book", book, "--min-calibration", "150", "--sizing", args.sizing,
                    "--cost-bps", str(args.cost_bps), "--tag", tag], check=True, capture_output=True, cwd=BACKEND)
    out = BACKEND / "results" / f"master_full{tag}.json"
    r = json.loads(out.read_text())["results"][0]
    out.unlink()
    return {"cagr": r["cagr_pct"], "sharpe": r["sharpe"]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--book", required=True, help="decisions.jsonl:horizon")
    ap.add_argument("--seeds", type=int, default=50)
    ap.add_argument("--start", default="2024-03-01")
    ap.add_argument("--events", default="data/events/events_sp500_2024_2026.csv")
    ap.add_argument("--sizing", default="top5th")
    ap.add_argument("--cost-bps", type=float, default=10.0)
    args = ap.parse_args()
    path, h = args.book.rsplit(":", 1)
    rows = [json.loads(x) for x in (BACKEND / path).read_text().splitlines()]
    real = run(args.book, "_shuffle_real", args)
    shuffled = []
    for s in range(args.seeds):
        vals = [r["logodds"] for r in rows]
        random.Random(s).shuffle(vals)
        tmp = BACKEND / "results" / "events" / f"decide_shuffle_tmp_{s}.jsonl"
        tmp.write_text("".join(json.dumps({**r, "logodds": v}) + "\n" for r, v in zip(rows, vals, strict=True)))
        shuffled.append(run(f"{tmp.relative_to(BACKEND)}:{h}", f"_shuffle_{s}", args))
        tmp.unlink()
        print(f"seed {s}: {shuffled[-1]}", flush=True)
    res = {"book": args.book, "real": real, "shuffled": shuffled,
           "p_cagr": (sum(x["cagr"] >= real["cagr"] for x in shuffled) + 1) / (args.seeds + 1),
           "p_sharpe": (sum(x["sharpe"] >= real["sharpe"] for x in shuffled) + 1) / (args.seeds + 1),
           "shuffled_mean_cagr": round(sum(x["cagr"] for x in shuffled) / len(shuffled), 2),
           "shuffled_mean_sharpe": round(sum(x["sharpe"] for x in shuffled) / len(shuffled), 3)}
    (BACKEND / "results" / f"shuffle_control_{Path(path).stem}.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps({k: v for k, v in res.items() if k != "shuffled"}, indent=1))


if __name__ == "__main__":
    main()
