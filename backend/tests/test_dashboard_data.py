import json
import sys
import time

import pandas as pd
import pytest

from app.dashboard import data as D


def _write_run(tmp_path, tag="t1", n_cut=6, edge=0.0):
    """Tiny synthetic run: 'good' is right with probability 0.5+edge, always_up says 0.51."""
    import random

    rng = random.Random(1)
    rows = []
    cutoffs = pd.date_range("2025-06-02", periods=n_cut, freq="7D")
    for c in cutoffs:
        for i in range(20):
            up = rng.random() < 0.5
            right = rng.random() < 0.5 + edge
            p_good = 0.7 if up == right else 0.3
            base = {"cutoff": c.date().isoformat(), "ticker": f"T{i}", "resolve_date": c.date().isoformat(),
                    "ret": 0.01 if up else -0.01, "up": up}
            rows.append({**base, "p": p_good, "arm": "llm_plain"})
            rows.append({**base, "p": 0.51, "arm": "always_up"})
    (tmp_path / f"walkforward_{tag}_predictions.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    s = {"n": 120, "accuracy": 0.5, "pct_up_calls": 1.0, "brier": 0.25, "log_loss": 0.69,
         "z_vs_coinflip": 0, "ls_mean_weekly_ret_pct": 0, "ls_sharpe_ann": 0}
    probe = {"passed": True}
    report = {"tag": tag, "model": "m", "target": "abs", "horizon_days": 5, "step_days": 5,
              "window": [str(cutoffs[0].date()), str(cutoffs[-1].date())], "n_cutoffs": n_cut,
              "tickers": [f"T{i}" for i in range(20)], "jail_probe_start": probe, "jail_probe_end": probe,
              "runtime_s": 60, "scores_full": {"llm_plain": s, "always_up": {**s, "brier": 0.24}},
              "scores_after_warmup": {"llm_plain": s, "always_up": {**s, "brier": 0.24}},
              "warmup_from": str(cutoffs[2].date())}
    (tmp_path / f"walkforward_{tag}.json").write_text(json.dumps(report))
    return report


def test_list_load_and_warm_filter(tmp_path):
    report = _write_run(tmp_path)
    (tmp_path / "walkforward_broken.json").write_text("{not json")
    runs = D.list_runs(tmp_path)
    assert [r["tag"] for r in runs] == ["t1"] and runs[0]["jail_ok"]
    preds = D.load_predictions("t1", tmp_path)
    assert len(preds) == 240 and preds["up"].dtype == bool
    warm = D.warm_filter(preds, report, after_warmup=True)
    assert warm["cutoff"].min() == pd.Timestamp(report["warmup_from"]) and len(warm) == 160
    assert list(D.scores_frame(report)["arm"]) == ["always_up", "llm_plain"]  # canonical order


def test_over_time_matches_definitions(tmp_path):
    _write_run(tmp_path)
    ot = D.over_time(D.load_predictions("t1", tmp_path))
    au = ot[ot["arm"] == "always_up"]
    # always_up is long everything, so its L/S return equals the average return that week
    preds = D.load_predictions("t1", tmp_path)
    avg = preds[preds["arm"] == "always_up"].groupby("cutoff")["ret"].mean().to_numpy()
    assert au["ls_ret"].to_numpy() == pytest.approx(avg)
    assert au["cum_accuracy"].iloc[-1] == pytest.approx(preds[preds["arm"] == "always_up"]["up"].mean())


def test_calibration_bins(tmp_path):
    _write_run(tmp_path)
    cal = D.calibration(D.load_predictions("t1", tmp_path))
    good = cal[cal["arm"] == "llm_plain"]
    assert set(good["mean_p"].round(2)) == {0.3, 0.7}
    assert good["n"].sum() == 120


def test_brier_gap_ci_detects_real_edge_and_not_noise(tmp_path):
    _write_run(tmp_path, tag="edge", n_cut=30, edge=0.3)
    g = D.brier_gap_ci(D.load_predictions("edge", tmp_path), "llm_plain")
    assert g and g["hi"] < 0  # clearly better than always-up
    _write_run(tmp_path, tag="noise", n_cut=30, edge=0.0)
    g = D.brier_gap_ci(D.load_predictions("noise", tmp_path), "llm_plain")
    assert g and g["lo"] > 0  # confident 0.3/0.7 coin flips are worse than 0.51


def test_build_run_args_rejects_bad_input():
    args = D.build_run_args("qwen3:8b", "ok_tag", "excess", 10, 6, 2)
    assert args[1:3] == ["-m", "app.sandbox.walkforward"] and "--target" in args
    for bad in [{"tag": "../x"}, {"tag": "a b"}, {"target": "rm"}, {"model": "x; rm -rf /"}]:
        kw = {"model": "qwen3:8b", "tag": "t", "target": "abs", **bad}
        with pytest.raises(ValueError):
            D.build_run_args(kw["model"], kw["tag"], kw["target"])


def test_launch_and_status_roundtrip(tmp_path):
    D.launch_run([sys.executable, "-c", "print('[1/2] a', flush=True); print('[2/2] b')"], "demo", tmp_path)
    for _ in range(50):
        s = D.run_status("demo", tmp_path)
        if not s["running"]:
            break
        time.sleep(0.1)
    assert (s["done"], s["total"]) == (2, 2) and not s["failed"]
    assert D.ui_launched_tags(tmp_path) == ["demo"]


def test_provenance_rows(tmp_path):
    assert D.provenance_rows({"tag": "legacy"}) is None
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"sha256": "ab" * 32}))
    report = {"config_hash": "1234", "provenance": {
        "git": {"commit": "f" * 40, "dirty": True, "dirty_files": ["backend/app/x.py"]},
        "data": {"sha256": "ab" * 32}, "model_digest": "500a1f067a9f" + "0" * 52,
        "python": "3.12", "platform": "Linux", "gpu": None},
        "jail_limits": {"memory_mb": 2048, "cpu_seconds": 14400, "response_timeout_s": 300.0,
                        "max_llm_requests_per_message": 256}}
    rows = {r["item"]: r["value"] for r in D.provenance_rows(report, lock)}
    assert rows["Config hash"] == "1234"
    assert "uncommitted changes: backend/app/x.py" in rows["Code commit"]
    assert "matches" in rows["Price data sha256"]
    assert rows["GPU"].startswith("not detected")
    assert "2048 MB" in rows["Jail limits"]
