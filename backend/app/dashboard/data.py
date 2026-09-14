"""Data layer for the Streamlit dashboard (no Streamlit imports, so it is testable).

Everything here reads the files the walk-forward writes to ``backend/results``:
``walkforward_<tag>.json`` (report) and ``walkforward_<tag>_predictions.jsonl``.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[2]
RESULTS = BACKEND / "results"
DATA_CSV = BACKEND / "data" / "prices.csv"

ARM_ORDER = ["always_up", "base_rate", "momentum_20d", "reversal_5d", "feat_logit",
             "llm_plain", "llm_selfimprove", "selector"]
ARM_LABELS = {
    "always_up": "Always 'up'",
    "base_rate": "Base rate",
    "momentum_20d": "Momentum (20d)",
    "reversal_5d": "Reversal (5d)",
    "feat_logit": "Logistic (no LLM)",
    "llm_plain": "LLM",
    "llm_selfimprove": "LLM + self-improve",
    "selector": "Selector",
}
TAG_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")


def _ordered(arms: list[str]) -> list[str]:
    return [a for a in ARM_ORDER if a in arms] + sorted(a for a in arms if a not in ARM_ORDER)


# ---------- runs ----------

def list_runs(results: Path = RESULTS) -> list[dict[str, Any]]:
    runs = []
    for p in sorted(results.glob("walkforward_*.json")):
        try:
            r = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(r, dict) or not {"tag", "model", "window", "n_cutoffs", "tickers",
                                           "jail_probe_start", "jail_probe_end"} <= r.keys():
            continue  # not a finished walk-forward report
        runs.append({
            "tag": r["tag"], "model": r["model"], "target": r.get("target", "abs"),
            "window": f"{r['window'][0]} → {r['window'][1]}", "cutoffs": r["n_cutoffs"],
            "tickers": len(r["tickers"]), "runtime_min": round(r.get("runtime_s", 0) / 60, 1),
            "jail_ok": bool(r["jail_probe_start"]["passed"] and r["jail_probe_end"]["passed"]),
            "mtime": p.stat().st_mtime,
        })
    return sorted(runs, key=lambda x: x["mtime"], reverse=True)


def load_report(tag: str, results: Path = RESULTS) -> dict[str, Any]:
    return dict(json.loads((results / f"walkforward_{tag}.json").read_text()))


def load_predictions(tag: str, results: Path = RESULTS) -> pd.DataFrame:
    path = results / f"walkforward_{tag}_predictions.jsonl"
    if not path.exists():
        return pd.DataFrame(columns=["cutoff", "ticker", "resolve_date", "ret", "up", "p", "arm"])
    df = pd.read_json(path, lines=True, dtype={"ticker": str})
    for c in ("cutoff", "resolve_date"):
        df[c] = pd.to_datetime(df[c])
    df["up"] = df["up"].astype(bool)
    return df


def scores_frame(report: dict[str, Any], after_warmup: bool = True) -> pd.DataFrame:
    s = report["scores_after_warmup" if after_warmup else "scores_full"]
    rows = [{"arm": a, "label": ARM_LABELS.get(a, a), **s[a]} for a in _ordered(list(s))]
    return pd.DataFrame(rows)


def warm_filter(df: pd.DataFrame, report: dict[str, Any], after_warmup: bool) -> pd.DataFrame:
    if not after_warmup or df.empty:
        return df
    return df[df["cutoff"] >= pd.Timestamp(report["warmup_from"])]


# ---------- analysis ----------

def over_time(df: pd.DataFrame) -> pd.DataFrame:
    """Per arm and cutoff: accuracy, Brier, long/short return (as in walkforward.score),
    plus cumulative versions."""
    if df.empty:
        return pd.DataFrame()
    d = df.assign(
        hit=(df["p"] >= 0.5) == df["up"],
        brier=(df["p"] - df["up"].astype(float)) ** 2,
        ls=np.where(df["p"] != 0.5, np.sign(df["p"] - 0.5) * df["ret"], np.nan),
    )
    g = d.groupby(["arm", "cutoff"]).agg(accuracy=("hit", "mean"), brier=("brier", "mean"),
                                         ls_ret=("ls", "mean"), n=("hit", "size")).reset_index()
    g["ls_ret"] = g["ls_ret"].fillna(0.0)
    g = g.sort_values(["arm", "cutoff"])
    g["cum_accuracy"] = g.groupby("arm")["accuracy"].transform(lambda s: s.expanding().mean())
    g["cum_ls_growth"] = g.groupby("arm")["ls_ret"].transform(lambda s: (1 + s).cumprod() - 1)
    g["label"] = g["arm"].map(lambda a: ARM_LABELS.get(a, a))
    return g


def calibration(df: pd.DataFrame, bins: int = 10) -> pd.DataFrame:
    """Mean forecast vs realized up-rate per probability bin, per arm."""
    if df.empty:
        return pd.DataFrame()
    edges = [i / bins for i in range(bins + 1)]
    d = df.assign(bin=pd.cut(df["p"], bins=edges, include_lowest=True), upf=df["up"].astype(float))
    out = d.groupby(["arm", "bin"], observed=True).agg(
        mean_p=("p", "mean"), realized=("upf", "mean"), n=("p", "size")).reset_index()
    out["label"] = out["arm"].map(lambda a: ARM_LABELS.get(a, a))
    return out.drop(columns="bin")


def brier_gap_ci(df: pd.DataFrame, arm: str, vs: str = "always_up",
                 n_boot: int = 2000, seed: int = 0) -> dict[str, float] | None:
    """Paired Brier difference (arm - vs; negative = arm better) with a bootstrap CI that
    resamples whole cutoffs, because stocks in the same week are not independent."""
    a = df[df["arm"] == arm].set_index(["cutoff", "ticker"])
    b = df[df["arm"] == vs].set_index(["cutoff", "ticker"])
    j = a[["p", "up"]].join(b[["p"]], rsuffix="_vs", how="inner")
    if j.empty:
        return None
    y = j["up"].astype(float)
    j = j.assign(diff=(j["p"] - y) ** 2 - (j["p_vs"] - y) ** 2)
    per_cut = j.groupby(level="cutoff")["diff"].agg(["sum", "size"])
    sums, sizes = per_cut["sum"].to_numpy(), per_cut["size"].to_numpy()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(sums), size=(n_boot, len(sums)))
    boots = sums[idx].sum(axis=1) / sizes[idx].sum(axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"gap": float(sums.sum() / sizes.sum()), "lo": float(lo), "hi": float(hi),
            "n": int(sizes.sum()), "cutoffs": len(sums)}


def compare_runs(results: Path = RESULTS, after_warmup: bool = True) -> pd.DataFrame:
    rows = []
    for run in list_runs(results):
        s = load_report(run["tag"], results)["scores_after_warmup" if after_warmup else "scores_full"]
        best = min(s, key=lambda a: s[a]["brier"])
        row: dict[str, Any] = {"run": run["tag"], "model": run["model"], "target": run["target"],
                               "n": next(iter(s.values()))["n"], "best arm (Brier)": ARM_LABELS.get(best, best)}
        for a in ("always_up", "llm_plain", "llm_selfimprove"):
            if a in s:
                row[f"{ARM_LABELS[a]} acc"] = s[a]["accuracy"]
                row[f"{ARM_LABELS[a]} Brier"] = s[a]["brier"]
        rows.append(row)
    return pd.DataFrame(rows)


def memorization_probe(results: Path = RESULTS) -> pd.DataFrame:
    rows = []
    for p in sorted(results.glob("memorization_probe_*.json")):
        model = p.stem.removeprefix("memorization_probe_")
        for month, err in json.loads(p.read_text()).items():
            rows.append({"model": model, "month": pd.Timestamp(f"{month}-01"), "median_abs_pct_error": err})
    return pd.DataFrame(rows)


def provenance_rows(report: dict[str, Any], lock_path: Path = BACKEND / "configs" / "data.lock.json"
                    ) -> list[dict[str, str]] | None:
    """Human-readable receipts for a run, or None if it predates provenance tracking."""
    pv = report.get("provenance")
    if not pv:
        return None
    git = pv.get("git") or {}
    commit = (git.get("commit") or "unknown")[:10]
    if git.get("dirty"):
        commit += " (uncommitted changes: " + ", ".join(git.get("dirty_files", [])[:3]) + ")"
    data_sha = (pv.get("data") or {}).get("sha256")
    try:
        locked = json.loads(lock_path.read_text())["sha256"]
    except (OSError, ValueError, KeyError):
        locked = None
    data_note = "matches configs/data.lock.json" if data_sha and data_sha == locked else \
        "differs from configs/data.lock.json" if data_sha and locked else ""
    rows = [
        {"item": "Config hash", "value": str(report.get("config_hash", "—"))},
        {"item": "Code commit", "value": commit},
        {"item": "Price data sha256", "value": f"{(data_sha or 'missing')[:16]}… {data_note}".strip()},
        {"item": "Ollama model digest", "value": (pv.get("model_digest") or "unknown")[:16]},
        {"item": "Python / platform", "value": f"{pv.get('python')} · {pv.get('platform')}"},
        {"item": "GPU", "value": pv.get("gpu") or "not detected (nvidia-smi unavailable)"},
    ]
    lim = report.get("jail_limits")
    if lim:
        rows.append({"item": "Jail limits", "value": f"{lim['memory_mb']} MB memory, {lim['cpu_seconds']} s CPU, "
                     f"{lim['response_timeout_s']:.0f} s response timeout, "
                     f"{lim['max_llm_requests_per_message']} LLM calls/message"})
    return rows


# ---------- launching runs ----------

def ollama_models(host: str = "http://127.0.0.1:11434") -> list[str]:
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=2) as r:
            return sorted(m["name"] for m in json.load(r)["models"])
    except (OSError, ValueError, KeyError):
        return []


def build_run_args(model: str, tag: str, target: str = "abs", step: int = 5, warmup: int = 12,
                   reflect_every: int = 4, start: str = "2025-06-02") -> list[str]:
    if not TAG_RE.match(tag):
        raise ValueError("tag must be 1-40 letters, digits, '_' or '-'")
    if target not in ("abs", "excess"):
        raise ValueError("target must be 'abs' or 'excess'")
    if not re.match(r"^[\w.:\-/]{1,80}$", model):
        raise ValueError("invalid model name")
    return [sys.executable, "-m", "app.sandbox.walkforward", "--model", model, "--tag", tag,
            "--target", target, "--step", str(int(step)), "--warmup", str(int(warmup)),
            "--reflect-every", str(int(reflect_every)), "--start", start]


def launch_run(args: list[str], tag: str, results: Path = RESULTS) -> int:
    results.mkdir(exist_ok=True)
    with (results / f"ui_{tag}.log").open("w") as log:  # the child keeps its own copy of the fd
        proc = subprocess.Popen(args, cwd=BACKEND, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    (results / f"ui_{tag}.pid").write_text(str(proc.pid))
    return proc.pid


def run_status(tag: str, results: Path = RESULTS) -> dict[str, Any]:
    log = results / f"ui_{tag}.log"
    pid_file = results / f"ui_{tag}.pid"
    text = log.read_text(errors="replace") if log.exists() else ""
    running = False
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text())
            os.kill(pid, 0)
            # a finished child that nobody reaped is a zombie; treat as not running
            stat = Path(f"/proc/{pid}/stat")
            running = not (stat.exists() and stat.read_text().split(") ")[-1].startswith("Z"))
        except (OSError, ValueError):
            running = False
    progress = re.findall(r"\[(\d+)/(\d+)\]", text)
    done, total = (int(progress[-1][0]), int(progress[-1][1])) if progress else (0, 0)
    return {"running": running, "done": done, "total": total,
            "finished": (results / f"walkforward_{tag}.json").exists() and not running,
            "failed": "Traceback" in text, "log_tail": "\n".join(text.splitlines()[-15:])}


def ui_launched_tags(results: Path = RESULTS) -> list[str]:
    return sorted(p.stem.removeprefix("ui_") for p in results.glob("ui_*.log"))
