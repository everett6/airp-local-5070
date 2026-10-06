"""Research all 150 technology companies with the deep pipeline, in batches. Research only: no orders.

    python scripts/research_all.py            # loops until every company has research under 20 hours old
    python scripts/research_all.py --forever  # then keep it fresh: redo research older than 20 hours (for D13)
    python scripts/research_all.py --status   # the progress file the app's Research log page reads

Each batch is one run of live_research_test.py --deep (wide reading, Bonsai lookups, bull-or-bear calls), which
takes the scheduler lock itself and refuses to start within 30 minutes of a scheduled job: this loop then waits.
A company that fails twice in 24 hours is skipped. STOP file in results/forward/research_all stops after the batch.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from full_auto import decisions, research_due, research_list

from app.forward.ledger import write_atomic

DIR = BACKEND / "results" / "forward" / "research_all"
BATCH = 4


def new_pipeline(latest: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Only research made with the current pipeline (bull-or-bear calls with their support) counts as done."""
    return {t: r for t, r in latest.items() if r.get("support")}


def save(p: dict[str, Any]) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    write_atomic(DIR / "progress.json", json.dumps(p, indent=1) + "\n")


def main() -> None:
    if "--status" in sys.argv:
        print((DIR / "progress.json").read_text() if (DIR / "progress.json").exists() else "{}")
        return
    companies = research_list()
    forever = "--forever" in sys.argv
    names = {c["ticker"] for c in companies}
    prog: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(), "total": len(companies), "batches": [],
                            "attempts": {}, "state": "running"}
    while not (DIR / "STOP").exists():
        now = datetime.now(UTC)
        latest = new_pipeline(decisions(names))
        due = research_due(companies, latest, prog["attempts"], now)
        per = [b["seconds"] / max(1, len(b["tickers"])) for b in prog["batches"] if b["code"] == 0]
        per_company = median(per) if per else 150.0
        prog.update(done=len(companies) - len(due), due=len(due), per_company_s=round(per_company),
                    eta_s=round(per_company * len(due)), updated_at=now.isoformat())
        if not due:
            if not forever:
                prog.update(state="finished", current=[], message="Every company has fresh research.")
                save(prog)
                return
            # keep going: research older than 20 hours is redone, so every evening refreshes the next day's calls
            prog.update(state="waiting", current=[], message="All research is fresh; checking again in 30 minutes.")
            save(prog)
            for _ in range(180):
                if (DIR / "STOP").exists():
                    break
                time.sleep(10)
            continue
        batch = due[:BATCH]
        prog.update(current=[c["ticker"] for c in batch], message="Researching " + ", ".join(c["ticker"] for c in batch))
        save(prog)
        cmd = [sys.executable, "-u", "scripts/live_research_test.py", "--deep", "--company-names-json",
               json.dumps({c["ticker"]: c["name"] for c in batch}), "--tickers", ",".join(c["ticker"] for c in batch)]
        t = time.monotonic()
        with (DIR / "output.log").open("ab") as log:
            rc = subprocess.run(cmd, cwd=BACKEND, stdout=log, stderr=log, check=False).returncode
        seconds = time.monotonic() - t
        if rc and seconds < 120:  # refused (scheduler room, lock or GPU): wait, nothing was attempted
            prog.update(current=[], message="Waiting: a scheduled job has priority or the GPU is busy.")
            save(prog)
            time.sleep(300)
            continue
        fresh = new_pipeline(decisions(names))
        for c in batch:
            r = fresh.get(c["ticker"])
            if not r or datetime.fromisoformat(r["decided_at"]) < now:
                prog["attempts"].setdefault(c["ticker"], []).append(now.isoformat())
        prog["batches"].append({"tickers": [c["ticker"] for c in batch], "code": rc, "seconds": round(seconds),
                                "finished_at": datetime.now(UTC).isoformat()})
        save(prog)
    prog.update(state="stopped", current=[], message="Stopped on request.")
    save(prog)


if __name__ == "__main__":
    main()
