"""Local release candidates and verified recovery copies. Never installs a strategy, trades, or copies secrets."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import sys
import tarfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.forward.ledger import Ledger, write_atomic

SKIP = {"__pycache__", "node_modules", "dist", ".venv", ".git"}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def files(root: Path, kind: str) -> list[Path]:
    folders = ["backend/results/forward"] if kind == "backup" else [
        "backend/app", "backend/scripts", "backend/tests", "backend/config", "backend/benchmarks",
        "desktop/src", "desktop/public", "desktop/electron", "desktop/tests", "deploy", ".github", "docs"]
    paths = [p for folder in folders for p in (root / folder).rglob("*") if p.is_file() and not p.is_symlink()
             and not (set(p.relative_to(root).parts) & SKIP) and not p.name.startswith(".env")
             and not p.name.endswith((".lock", ".log", ".tmp")) and p.name != "running.json"]
    if kind != "backup":
        paths += [root / p for p in ("backend/pyproject.toml", "backend/requirements.lock",
                                    "desktop/package.json", "desktop/package-lock.json") if (root / p).is_file()]
    return sorted(set(paths))


def fingerprint(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): sha(p) for p in files(root, "release")}


def bundle(root: Path, kind: str, check_evidence: dict[str, Any] | None = None,
           evidence_files: list[Path] | None = None) -> dict[str, Any]:
    selected = files(root, kind)
    selected += evidence_files or []
    if not selected:
        raise ValueError("Nothing to preserve")
    ident = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    out = root / "backend" / "results" / "ops" / f"{kind}s" / ident
    out.mkdir(parents=True)
    manifest = {str(p.relative_to(root)): {"sha256": sha(p), "bytes": p.stat().st_size} for p in selected}
    archive = out / "snapshot.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path in selected:
            tar.add(path, arcname=str(path.relative_to(root)), recursive=False)
    # Catch files changing while copied; callers also hold the shared autorun lock.
    if any(sha(root / name) != info["sha256"] for name, info in manifest.items()):
        raise ValueError("Source changed during snapshot; do not use this candidate")
    meta: dict[str, Any] = {"id": ident, "kind": kind, "created_at": datetime.now(UTC).isoformat(), "files": manifest,
            "archive_sha256": sha(archive), "python": sys.version.split()[0], "status": "candidate"}
    if kind == "release":
        meta["checks"] = check_evidence
        meta["installed_packages"] = sorted(
            [{"name": d.metadata["Name"], "version": d.version} for d in importlib.metadata.distributions()],
            key=lambda d: str(d["name"]))
    write_atomic(out / "manifest.json", json.dumps(meta, indent=1))
    Ledger(root / "backend" / "results" / "ops" / "index.jsonl").append("snapshot", id=ident,
        kind=kind, archive_sha256=meta["archive_sha256"], manifest_sha256=sha(out / "manifest.json"))
    return meta


def verify_restore(root: Path, kind: str = "backup") -> dict[str, Any]:
    base = root / "backend" / "results" / "ops"
    snapshots = sorted((base / f"{kind}s").glob("*/manifest.json"))
    if not snapshots:
        raise ValueError("No snapshot to rehearse")
    source = snapshots[-1].parent
    meta = json.loads((source / "manifest.json").read_text())
    recorded = [r for r in Ledger(base / "index.jsonl").verify() if r.get("id") == meta["id"]]
    if not recorded or recorded[-1]["manifest_sha256"] != sha(source / "manifest.json"):
        raise ValueError("Snapshot manifest does not match its recorded digest")
    if sha(source / "snapshot.tar.gz") != meta["archive_sha256"]:
        raise ValueError("Snapshot archive digest differs")
    dest = base / "restores" / (meta["id"] + "-" + uuid.uuid4().hex[:8])
    dest.mkdir(parents=True)
    with tarfile.open(source / "snapshot.tar.gz", "r:gz") as tar:
        members = tar.getmembers()
        if {m.name for m in members} != set(meta["files"]) or len(members) != len(meta["files"]):
            raise ValueError("Unexpected snapshot members")
        for member in members:
            target = (dest / member.name).resolve()
            if (not member.isfile() or not target.is_relative_to(dest.resolve())
                    or any(p.startswith(".env") for p in Path(member.name).parts)):
                raise ValueError("Unsafe archive member")
            if member.size != meta["files"][member.name]["bytes"]:
                raise ValueError("Snapshot member size differs")
        tar.extractall(dest, members=members, filter="data")
    for name, info in meta["files"].items():
        if sha(dest / name) != info["sha256"]:
            raise ValueError("Restored file differs")
    result = {"id": meta["id"], "kind": kind, "status": "verified_scratch_restore",
              "files": len(meta["files"]), "at": datetime.now(UTC).isoformat(), "path": str(dest)}
    write_atomic(base / "restore_review.json", json.dumps(result, indent=1))
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=("start_checks", "release", "backup", "restore", "rollback_probe"))
    args = ap.parse_args()
    import fcntl
    lock_path = ROOT / "backend" / "results" / "forward" / "autorun.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Desktop workers pass the already-held lock to children; direct CLI calls acquire it themselves.
    inherited = os.environ.get("AIRP_AUTORUN_LOCK_FD")
    with (os.fdopen(os.dup(int(inherited)), "a") if inherited else lock_path.open("a")) as lock:
        if os.fstat(lock.fileno()).st_ino != lock_path.stat().st_ino:
            raise ValueError("Invalid inherited scheduler lock")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        checks = ROOT / "backend" / "results" / "ops" / "check_start.json"
        checks.parent.mkdir(parents=True, exist_ok=True)
        if args.action == "start_checks":
            write_atomic(checks, json.dumps({"files": fingerprint(ROOT), "job": os.environ.get("AIRP_DESKTOP_JOB")}))
            result: dict[str, Any] = {"status": "checks_started"}
        elif args.action == "release":
            started = json.loads(checks.read_text()) if checks.exists() else {}
            if started.get("files") != fingerprint(ROOT):
                raise ValueError("Source changed during checks; repeat the checks before packaging")
            job = os.environ.get("AIRP_DESKTOP_JOB")
            if not job or started.get("job") != job:
                raise ValueError("Release checks must run together in the app worker")
            job_path = Path(job).resolve()
            if job_path.parent != ROOT / "backend" / "results" / "desktop_runs":
                raise ValueError("Invalid release check job")
            history = json.loads((job_path / "status.json").read_text())
            passed = {s["label"] for s in history.get("steps", []) if s.get("state") == "succeeded"}
            if history.get("action") != "release_check" or not {
                    "Verify backend", "Desktop tests", "Check Python safety modules", "Check safety types"} <= passed:
                raise ValueError("Backend and desktop checks must pass for this candidate")
            evidence = {"job_id": history["id"], "steps": [s for s in history["steps"] if s["state"] == "succeeded"],
                        "log_sha256": sha(job_path / "output.log"), "source_bound": True}
            result = bundle(ROOT, "release", evidence, [job_path / "output.log", job_path / "steps.jsonl"])
            result["checked_job"] = str(job_path)
            result["qualification"] = "Local checks passed; independent review and activation remain separate"
        elif args.action in ("restore", "rollback_probe"):
            result = verify_restore(ROOT, "backup" if args.action == "restore" else "release")
        else:
            result = bundle(ROOT, "backup")
        print(json.dumps(result))


if __name__ == "__main__":
    main()
