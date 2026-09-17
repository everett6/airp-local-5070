"""Kronos baseline forecasts for every (cutoff, ticker) of a walk-forward config.

    scripts/setup_kronos.sh                       # once
    .venv-kronos/bin/python scripts/kronos_forecasts.py configs/v7_phase_f.toml

For each cutoff, each stock's last `context_bars` daily OHLCV bars dated <= cutoff (app.sandbox.ohlcv.window)
go to Kronos-small; `samples` independent paths are drawn for `horizon` business days; P(up) comes from the
sampled returns via the pre-registered `sample_probability`. Future bars are never passed in, and the future
timestamps are generated business days, not dates read from the data. Output:
results/kronos_<tag>.jsonl plus .meta.json (parameters, commits, data sha256, file sha256).
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "third_party" / "Kronos"))

import numpy as np
import pandas as pd
import torch
from model import Kronos, KronosPredictor, KronosTokenizer

from app.sandbox.gpu_lock import gpu_job
from app.sandbox.ohlcv import KRONOS_PARAMS, cutoffs_for, load_ohlcv, sample_probability, window
from app.sandbox.pit_data import PriceTable

CHUNK = 480


def main() -> None:
    cfg = tomllib.loads((BACKEND / sys.argv[1]).read_text())
    P = KRONOS_PARAMS
    table = PriceTable.from_csv(BACKEND / cfg["data"])
    ohlcv_path = BACKEND / cfg["ohlcv"]
    bars = load_ohlcv(ohlcv_path)
    tickers = [t for t in table.tickers if t != "SPY"]
    cutoffs = cutoffs_for(cfg, table.dates)
    horizon = int(cfg["horizon"])
    commit = (BACKEND / "third_party/Kronos/.git/HEAD").read_text().strip()
    if commit != P["code_commit"]:
        raise SystemExit(f"third_party/Kronos is at {commit}, expected {P['code_commit']} (run scripts/setup_kronos.sh)")
    tok = KronosTokenizer.from_pretrained(P["tokenizer"], revision=P["tokenizer_revision"])
    model = Kronos.from_pretrained(P["model"], revision=P["model_revision"])
    pred = KronosPredictor(model, tok, device="cuda:0" if torch.cuda.is_available() else "cpu", max_context=512)
    out_path = BACKEND / "results" / f"kronos_{cfg['tag']}.jsonl"
    lines: list[str] = []
    t0 = time.time()
    for ci, c in enumerate(cutoffs):
        torch.manual_seed(P["seed"] + ci)
        np.random.seed(P["seed"] + ci)
        y_ts = pd.Series(pd.bdate_range(pd.Timestamp(c) + pd.Timedelta(days=1), periods=horizon))
        groups: dict[int, list[str]] = {}
        wins = {}
        for t in tickers:
            w = window(bars[t], c, P["context_bars"])
            if not w or w[-1][0] != c:
                raise SystemExit(f"{t}: no OHLCV bar on cutoff {c} (refetch the OHLCV file or drop the ticker)")
            wins[t] = w
            groups.setdefault(len(w), []).append(t)
        samples: dict[str, list[float]] = {t: [] for t in tickers}
        for _, ts in sorted(groups.items()):
            jobs = [t for t in ts for _ in range(P["samples"])]
            for k in range(0, len(jobs), CHUNK):
                part = jobs[k:k + CHUNK]
                dfs = [pd.DataFrame([b[1:] for b in wins[t]], columns=["open", "high", "low", "close", "volume"])
                       for t in part]
                x_ts = [pd.Series(pd.to_datetime([b[0] for b in wins[t]])) for t in part]
                res = pred.predict_batch(dfs, x_ts, [y_ts] * len(part), pred_len=horizon, T=P["temperature"],
                                         top_p=P["top_p"], sample_count=1, verbose=False)
                for t, df in zip(part, res, strict=True):
                    samples[t].append(float(df["close"].iloc[-1]) / wins[t][-1][4] - 1.0)
        for t in tickers:
            s = samples[t]
            lines.append(json.dumps({"cutoff": c.isoformat(), "ticker": t, "p": round(sample_probability(s), 6),
                                     "mean_ret": round(float(np.mean(s)), 6), "sd_ret": round(float(np.std(s, ddof=1)), 6)}))
        print(f"[{ci + 1}/{len(cutoffs)}] {c} elapsed={time.time() - t0:.0f}s", flush=True)
    out_path.write_text("\n".join(lines) + "\n")
    meta = {"tag": cfg["tag"], "params": P, "horizon": horizon, "cutoffs": [c.isoformat() for c in cutoffs],
            "ohlcv": cfg["ohlcv"], "ohlcv_sha256": hashlib.sha256(ohlcv_path.read_bytes()).hexdigest(),
            "forecasts_sha256": hashlib.sha256(out_path.read_bytes()).hexdigest(),
            "torch": torch.__version__, "device": str(pred.device), "runtime_s": round(time.time() - t0)}
    out_path.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(out_path.name, len(lines), "forecasts")


if __name__ == "__main__":
    with gpu_job(f"kronos {sys.argv[1]}"):
        main()
