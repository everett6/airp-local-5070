"""One evidence file per live decision (docs/PLAN_60_V2.md, "Outside review, second part", new records).

    python scripts/evidence_bundle.py                 # write the bundle of every decision that has none yet
    python scripts/evidence_bundle.py --check         # re-hash every bundle and its sources; say what no longer matches
    python scripts/evidence_bundle.py --show <accession>

A bundle holds what a reviewer needs to rebuild one decision without guessing which files were current: the press
release's address, hash and download time; the reader's record of it; the exact fact sheet the judge read; both
models' weights (Ollama digests) and prompts (hashes) and the settings they ran with; the code version; the entry
session the decision was for; and the ledger line it became. Bundles are written once, by a step that runs right
after the event runner, and never rewritten; each one's hash is appended to `evidence/index.jsonl`, which is pushed
with the run. The runner's ledger record does not depend on any of this: a failure here changes no decision.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd

from app.forward.ledger import Ledger, jsonl_records, open_append
from app.forward.schedule import NY
from app.portfolio import sleeve

H = 5
# what the runner passes to each model (forward_events.fact_sheets / .bonsai and the scripts they start);
# tests/test_evidence_bundle.py fails if the scripts stop saying the same
READER: dict[str, Any] = {"model": "qwen3:8b", "num_ctx": 8192, "num_predict": 450, "temperature": 0, "parallel": 4,
          "max_chars": 8000, "compact": True, "models_dir": "/usr/share/ollama/.ollama/models"}
JUDGE: dict[str, Any] = {"model": "bonsai-27b:latest", "num_ctx": 4096, "num_predict": 1, "temperature": 0, "parallel": 3,
         "mode": "buypass_lo", "top_logprobs": 20, "horizon_days": H, "models_dir": "~/.ollama/models"}


def sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def manifest_digest(model: str, models_dir: str) -> str | None:
    """Ollama's digest of a model, read from its manifest on disk (the digest is that file's SHA-256, the number
    `ollama list` shows): no server needed."""
    name, _, tag = model.partition(":")
    root = Path(models_dir).expanduser() / "manifests"
    rel = Path(name) if name.count("/") >= 2 else Path("registry.ollama.ai") / "library" / name
    f = root / rel / (tag or "latest")
    try:
        return sha(f.read_bytes())
    except OSError:
        return None


def code_version() -> dict[str, Any]:
    def git(*a: str) -> str:
        try:
            return subprocess.run(["git", "-C", str(BACKEND.parent), *a], capture_output=True, text=True, timeout=20,
                                  check=False).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""
    return {"commit": git("rev-parse", "HEAD") or None,
            "uncommitted_code": bool(git("status", "--porcelain", "--", "backend/app", "backend/scripts")),
            "runner_sha256": sha((BACKEND / "scripts" / "forward_events.py").read_bytes())}


def prompts() -> dict[str, str]:
    from decide_events import DECIDE_SYSTEM
    from extract_events import COMPACT, READER_SYSTEM
    return {"judge": DECIDE_SYSTEM.replace("next 20 trading days", f"next {H} trading days"),
            "reader": READER_SYSTEM + COMPACT}


def build(dec: dict[str, Any], ev: dict[str, Any], extract: dict[str, Any] | None, judged: dict[str, Any] | None,
          text_file: Path, versions: dict[str, Any], backfilled: bool,
          figures: dict[str, Any] | None = None) -> dict[str, Any]:
    """The bundle for one ledger decision. Pure: everything it needs is handed in."""
    pr = versions["prompts"]
    deadline = datetime.fromisoformat(dec["entry_deadline"])
    b: dict[str, Any] = {
        "accession": dec["accession"], "ticker": dec["ticker"], "sector": dec.get("sector"),
        "accepted_utc": dec["accepted_utc"],
        "entry": {"deadline": dec["entry_deadline"],
                  "session": dec.get("entry_session") or deadline.astimezone(NY).date().isoformat(),
                  # decisions since the evening of 1 Oct 2026 store their session (PLAN_60_V2 "One entry-time rule")
                  "rule": ("accepted before 13:00 UTC on a trading session: that session's open, else the next "
                           "session's (exchange holiday calendar); exit 5 trading days later"
                           if dec.get("entry_session") else
                           "the open of the first weekday after the SEC acceptance; exit 5 trading days later")},
        "ledger": {k: dec.get(k) for k in ("seq", "hash", "prev", "written_at", "as_of", "decided_at", "source",
                                           "logodds", "on_time", "guidance", "entry_session", "sheet_version",
                                           "pipeline", "judge_tag", "research_sha256")},
        "source": {"ex99_url": ev.get("ex99_url"), "items": ev.get("items"), "filed": ev.get("filed")},
        "code": versions["code"],
        "strategy": {"score": sleeve.SCORE_NAME, "threshold": sleeve.THRESHOLD, "hold_days": sleeve.HOLD,
                     "slots": sleeve.SLOTS, "cost_per_leg": sleeve.COST, "sleeve_share": sleeve.SHARE},
        "backfilled": backfilled,
    }
    if figures:  # fact sheet v2: each figure's period, units, accounting basis, source and SEC reconciliation
        b["figures"] = figures
    if text_file.exists():
        raw = text_file.read_bytes()
        try:
            text = gzip.decompress(raw)
        except OSError:
            text = b""
        b["source"].update(file=text_file.name, file_sha256=sha(raw), text_sha256=sha(text), text_chars=len(text),
                           retrieved_at=datetime.fromtimestamp(text_file.stat().st_mtime, UTC)
                           .isoformat(timespec="seconds"))
    else:
        b["source"]["file"] = None
    if extract is not None:
        b["reader"] = {**{k: v for k, v in versions.get("reader_settings", READER).items() if k != "models_dir"},
                       "model_recorded": extract.get("model"), "digest": versions["reader_digest"],
                       "prompt_sha256": sha(pr["reader"]), "record": extract,
                       "record_sha256": sha(json.dumps(extract, sort_keys=True))}
    if dec.get("source") == "bonsai":
        b["judge"] = {**{k: v for k, v in JUDGE.items() if k != "models_dir"}, "digest": versions["judge_digest"],
                      "prompt_sha256": sha(pr["judge"]), "prompt_version": sha(pr["judge"])[:12]}
        if judged is not None:
            sheet = judged.get("prompt_user") or ""
            b["judge"].update(logodds=judged.get("logodds"), mass=judged.get("mass"), censored=judged.get("censored"))
            b["fact_sheet"] = {"text": sheet, "sha256": sha(sheet), "version": int(dec.get("sheet_version") or 1)}
            b["ledger"]["matches_judge"] = (judged.get("logodds") is not None and dec.get("logodds") is not None
                                            and round(float(judged["logodds"]), 4) == float(dec["logodds"]))
    else:  # Bonsai-lite: a ridge on past decisions, no model call
        b["judge"] = {"model": "bonsai-lite (ridge, scripts/bonsai_lite.py)", "digest": None}
    return b


def write_new(events_dir: Path, tag: str = "forward", now: datetime | None = None) -> list[str]:
    """Write a bundle for each ledger decision that has none. Returns the accessions written."""
    from extract_events import text_path
    out = events_dir / "evidence"
    if not (events_dir / "ledger.jsonl").exists():
        return []
    decs = [r for r in Ledger(events_dir / "ledger.jsonl").records() if r.get("type") == "decision"]
    todo = [r for r in decs if not (out / f"{r['accession']}.json").exists()]
    if not todo:
        return []
    now = now or datetime.now(UTC)
    ev = (pd.read_csv(events_dir / "events.csv").drop_duplicates("accession").set_index("accession")
          .to_dict("index") if (events_dir / "events.csv").exists() else {})
    ex = {x["accession"]: x for x in jsonl_records(events_dir / "extract.jsonl")}
    judged = {x["accession"]: x for x in
              jsonl_records(BACKEND / "results" / "events" / f"decide_bonsai-27b_latest_{tag}_h{H}.jsonl")}
    feats = BACKEND / "results" / "events" / f"features_{tag}.csv"  # this run's fact-sheet table (v2: with figures)
    figs: dict[str, Any] = {}
    if feats.exists():
        ft = pd.read_csv(feats)
        if "figures" in ft.columns:
            figs = {a: json.loads(x) for a, x in zip(ft["accession"], ft["figures"], strict=True) if isinstance(x, str)}
    versions = {"prompts": prompts(), "code": code_version(),
                "reader_digest": manifest_digest(READER["model"], READER["models_dir"]),
                "judge_digest": manifest_digest(JUDGE["model"], JUDGE["models_dir"])}
    out.mkdir(parents=True, exist_ok=True)
    done = []
    for r in todo:
        acc = r["accession"]
        # a decision bundled in the run that made it: within two hours of its ledger line. Older: backfilled, and
        # the code version and model digests are today's, not necessarily that day's
        age = (now - datetime.fromisoformat(r["written_at"])).total_seconds() if r.get("written_at") else 1e9
        reader_record, judge_record, current_versions = ex.get(acc), judged.get(acc), versions
        research_record = None
        figures = figs.get(acc)
        if r.get("pipeline") == "jan_bonsai_v1":
            from jan_forward import JAN, MODELS
            reader_path = (events_dir / str(r.get("reader_record_path", "jan/extract.jsonl"))).resolve()
            research_path = (events_dir / str(r.get("research_path", ""))).resolve()
            if not reader_path.is_relative_to(events_dir.resolve()) or not research_path.is_relative_to(events_dir.resolve()):
                raise ValueError("Jan evidence path escapes the events directory")
            research_body = research_path.read_text()
            if sha(research_body) != r.get("research_sha256"):
                raise ValueError("Jan research changed since the decision")
            research_record = json.loads(research_body)
            source_hash = research_record.get("source_sha256")
            if source_hash and (not text_path(acc).exists() or sha(text_path(acc).read_bytes()) != source_hash):
                raise ValueError("Jan source changed since the research")
            reader_record = next((x for x in jsonl_records(reader_path) if x["accession"] == acc), None)
            judge_tag = str(r.get("judge_tag", ""))
            if not all(c.isalnum() or c in "_-" for c in judge_tag) or not judge_tag:
                raise ValueError("invalid Jan judge tag")
            jp = BACKEND / "results" / "events" / f"decide_bonsai-27b_latest_{judge_tag}_h{H}.jsonl"
            judge_record = next((x for x in jsonl_records(jp) if x["accession"] == acc), None)
            jan_features = BACKEND / "results" / "events" / f"features_{judge_tag}.csv"
            if jan_features.exists():
                jan_ft = pd.read_csv(jan_features)
                matching = jan_ft[jan_ft["accession"].astype(str) == str(acc)]
                if not matching.empty and "figures" in matching and isinstance(matching.iloc[0]["figures"], str):
                    figures = json.loads(matching.iloc[0]["figures"])
            current_versions = {**versions, "reader_digest": manifest_digest(JAN, MODELS),
                                "reader_settings": {**READER, "model": JAN, "parallel": 1, "models_dir": MODELS}}
            if reader_record is None or judge_record is None:
                raise ValueError("Jan decision is missing its reader or judge evidence")
        b = build(r, {str(k): (None if pd.isna(v) else v) for k, v in ev.get(acc, {}).items()}, reader_record,
                  judge_record, text_path(acc), current_versions, backfilled=age > 7200, figures=figures)
        if research_record is not None:
            b["research"] = {"record": research_record, "sha256": r["research_sha256"],
                             "path": r["research_path"], "model": research_record["model"],
                             "digest": research_record["digest"]}
        b["bundle_written_at"] = now.isoformat(timespec="seconds")
        body = json.dumps(b, indent=1, sort_keys=True) + "\n"
        tmp = out / f"{acc}.json.tmp"
        tmp.write_text(body)
        tmp.replace(out / f"{acc}.json")
        with open_append(out / "index.jsonl") as f:
            f.write(json.dumps({"accession": acc, "sha256": sha(body), "at": b["bundle_written_at"]}) + "\n")
        done.append(acc)
    return done


def check(events_dir: Path) -> list[str]:
    """Problems found re-hashing the bundles: a bundle changed since it was indexed, a press release that is no
    longer the file the decision read, a ledger line that no longer matches."""
    from extract_events import text_path
    out = events_dir / "evidence"
    problems = []
    index = {x["accession"]: x["sha256"] for x in jsonl_records(out / "index.jsonl")}
    ledger = {r["accession"]: r for r in Ledger(events_dir / "ledger.jsonl").records() if r.get("type") == "decision"}
    for acc in sorted(ledger):
        f = out / f"{acc}.json"
        if not f.exists():
            problems.append(f"{acc}: no bundle")
            continue
        body = f.read_text()
        if index.get(acc) != sha(body):
            problems.append(f"{acc}: the bundle is not the one that was indexed")
        b = json.loads(body)
        if b["ledger"].get("hash") != ledger[acc].get("hash"):
            problems.append(f"{acc}: the ledger line differs from the bundle's")
        want = b["source"].get("file_sha256")
        if want and (not text_path(acc).exists() or sha(text_path(acc).read_bytes()) != want):
            problems.append(f"{acc}: the press release on disk is not the one the decision read")
        research = b.get("research")
        if research:
            research_path = (events_dir / research["path"]).resolve()
            if (not research_path.is_relative_to(events_dir.resolve()) or not research_path.is_file()
                    or sha(research_path.read_bytes()) != research["sha256"]):
                problems.append(f"{acc}: Jan research differs from the decision evidence")
        fs = b.get("fact_sheet")
        if fs and sha(fs["text"]) != fs["sha256"]:
            problems.append(f"{acc}: the fact sheet does not match its hash")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="results/forward/events")
    ap.add_argument("--tag", default="forward")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--show", default="")
    a = ap.parse_args()
    d = BACKEND / a.dir
    if a.show:
        print((d / "evidence" / f"{a.show}.json").read_text())
        return
    if a.check:
        problems = check(d)
        for x in problems:
            print("LEARN ALERT: evidence:", x)
        print(f"evidence: {len(jsonl_records(d / 'evidence' / 'index.jsonl'))} bundles, {len(problems)} problem(s)")
        return
    done = write_new(d, a.tag)
    print(f"evidence: {len(done)} new bundle(s)" + (f" ({', '.join(done[:6])}{' ...' if len(done) > 6 else ''})"
                                                   if done else ""))


if __name__ == "__main__":
    main()
