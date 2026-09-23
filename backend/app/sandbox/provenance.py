"""
Provenance for walk-forward runs: what exactly produced a results file.

Every report records
  config / config_hash   the parameters that affect results (not concurrency)
  provenance             git commit + dirty flag, prices.csv sha256, Ollama
                         model digest, Python/platform, GPU if detectable

and `check_overwrite` refuses to replace a finished run with one produced from
different parameters, so a tag always means one frozen experiment.
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import tomllib
import urllib.request
from pathlib import Path
from typing import Any

# Parameters that change results. Changing any of these changes the config hash.
RESULT_FIELDS = ("model", "start", "end", "horizon", "step", "warmup", "reflect_every", "target")
# Result-affecting fields added later: hashed only when present, so older hashes stay valid.
OPTIONAL_RESULT_FIELDS = ("data", "fund", "rl", "score_logprob", "solver", "anomalies", "ohlcv", "kronos", "rl_state")
# Allowed in a config file but not hashed: they don't change what is computed (the Ollama URL is recorded in the
# report's provenance instead; the LLM cache key does not depend on it).
OTHER_FIELDS = ("tag", "concurrency", "description", "ollama_url", "kronos_tag")


class ProvenanceError(RuntimeError):
    pass


def load_config_file(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        cfg = tomllib.load(f)
    unknown = set(cfg) - set(RESULT_FIELDS) - set(OPTIONAL_RESULT_FIELDS) - set(OTHER_FIELDS)
    if unknown:
        raise ProvenanceError(f"{path}: unknown config keys {sorted(unknown)}")
    return cfg


def config_hash(cfg: dict[str, Any]) -> str:
    canon = {k: cfg.get(k) for k in RESULT_FIELDS}
    canon |= {k: cfg[k] for k in OPTIONAL_RESULT_FIELDS if cfg.get(k)}
    return hashlib.sha256(json.dumps(canon, sort_keys=True, default=str).encode()).hexdigest()[:16]


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_state(repo: Path, source_paths: tuple[str, ...] = ("backend/app", "backend/scripts", "backend/configs")
              ) -> dict[str, Any]:
    """Commit of HEAD, and whether result-affecting sources differ from it (modified or new files)."""
    def git(*a: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True,
                              timeout=10, check=True).stdout
    try:
        commit = git("rev-parse", "HEAD").strip()
        dirty_files = git("status", "--porcelain", "--untracked-files=all", "--", *source_paths)
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}
    lines = [ln for ln in dirty_files.splitlines() if ln.strip()]
    return {"commit": commit, "dirty": bool(lines), "dirty_files": [ln[3:] for ln in lines][:20]}


def ollama_digest(model: str, host: str = "http://127.0.0.1:11434") -> str | None:
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as r:
            for m in json.load(r)["models"]:
                if model in (m.get("name"), m.get("model")):
                    return str(m["digest"])
    except (OSError, ValueError, KeyError):
        pass
    return None


def gpu_info() -> str | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
                              "--format=csv,noheader"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def collect(repo: Path, data_path: Path, model: str, host: str = "http://127.0.0.1:11434") -> dict[str, Any]:
    return {
        "git": git_state(repo),
        "data": {"path": data_path.name, "sha256": sha256_file(data_path)},
        "model_digest": ollama_digest(model, host),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "gpu": gpu_info(),
    }


def check_overwrite(report_path: Path, new_hash: str, force: bool = False,
                    data_sha256: str | None = None) -> None:
    """A tag is one frozen experiment: re-running it is fine only with identical parameters and data."""
    if force or not report_path.exists():
        return
    try:
        report = json.loads(report_path.read_text())
    except (OSError, json.JSONDecodeError):
        report = {}
    old = report.get("config_hash")
    old_data = ((report.get("provenance") or {}).get("data") or {}).get("sha256")
    if old is None:
        raise ProvenanceError(f"{report_path.name} has no config hash (made before provenance existed); "
                              "use a new tag, or --force to overwrite it")
    if old != new_hash:
        raise ProvenanceError(f"{report_path.name} was produced with config {old}, this run is {new_hash}; "
                              "use a new tag, or --force to overwrite it")
    if old_data and data_sha256 and old_data != data_sha256:
        raise ProvenanceError(f"{report_path.name} was produced from different price data "
                              f"({old_data[:12]} vs {data_sha256[:12]}); use a new tag, or --force to overwrite it")
