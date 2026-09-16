import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from app.sandbox import provenance as prov
from app.sandbox.walkforward import parse_args

CONFIGS = Path(__file__).resolve().parents[1] / "configs"


def test_config_hash_ignores_non_result_fields_and_key_order():
    a = {"model": "m", "start": "2025-06-02", "end": "2026-01-01", "horizon": 5, "step": 5,
         "warmup": 12, "reflect_every": 4, "target": "abs"}
    b = dict(reversed(list(a.items()))) | {"tag": "other", "concurrency": 8, "description": "x"}
    assert prov.config_hash(a) == prov.config_hash(b)
    assert prov.config_hash(a) != prov.config_hash(a | {"step": 10})


def test_config_file_rejects_unknown_keys(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('model = "m"\nlearning_rate = 3\n')
    with pytest.raises(prov.ProvenanceError, match="learning_rate"):
        prov.load_config_file(f)


def test_parse_args_uses_config_and_explicit_flags_override(tmp_path):
    args = parse_args(["--config", str(CONFIGS / "v4_14b.toml")])
    assert (args.model, args.step, args.warmup, args.reflect_every, args.tag) == ("qwen3:14b", 10, 6, 2, "v4_14b")
    args = parse_args(["--config", str(CONFIGS / "v4_14b.toml"), "--step", "5", "--tag", "x"])
    assert (args.model, args.step, args.tag) == ("qwen3:14b", 5, "x")


@pytest.mark.parametrize("name", ["v2", "v3_excess", "v4_14b"])
def test_frozen_configs_match_the_published_runs(name):
    """The committed config must reproduce the parameters recorded in the published report."""
    cfg = prov.load_config_file(CONFIGS / f"{name}.toml")
    report = json.loads((CONFIGS.parent / "results" / f"walkforward_{name}.json").read_text())
    assert cfg["tag"] == report["tag"] and cfg["model"] == report["model"]
    assert [cfg["start"], cfg["end"]] == report["window"]
    assert (cfg["horizon"], cfg["step"], cfg["target"]) == (report["horizon_days"], report["step_days"],
                                                           report.get("target", "abs"))
    if "config_hash" in report:
        assert prov.config_hash(cfg) == report["config_hash"]


def test_check_overwrite(tmp_path):
    path = tmp_path / "walkforward_t.json"
    prov.check_overwrite(path, "aaaa")  # nothing there yet
    path.write_text(json.dumps({"config_hash": "aaaa"}))
    prov.check_overwrite(path, "aaaa")  # same experiment: fine
    with pytest.raises(prov.ProvenanceError, match="new tag"):
        prov.check_overwrite(path, "bbbb")
    prov.check_overwrite(path, "bbbb", force=True)
    # same parameters but different price data is a different experiment too
    path.write_text(json.dumps({"config_hash": "aaaa", "provenance": {"data": {"sha256": "d1"}}}))
    prov.check_overwrite(path, "aaaa", data_sha256="d1")
    with pytest.raises(prov.ProvenanceError, match="different price data"):
        prov.check_overwrite(path, "aaaa", data_sha256="d2")
    path.write_text(json.dumps({"tag": "legacy"}))
    with pytest.raises(prov.ProvenanceError, match="no config hash"):
        prov.check_overwrite(path, "aaaa")


def test_git_state_detects_dirty_source(tmp_path):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    src = tmp_path / "backend" / "app" / "m.py"
    src.parent.mkdir(parents=True)
    src.write_text("x = 1\n")
    (tmp_path / "notes.txt").write_text("a")
    git("add", "-A")
    git("commit", "-qm", "c")
    s = prov.git_state(tmp_path)
    assert len(s["commit"]) == 40 and s["dirty"] is False
    (tmp_path / "notes.txt").write_text("b")  # not a result-affecting path
    assert prov.git_state(tmp_path)["dirty"] is False
    src.write_text("x = 2\n")
    s = prov.git_state(tmp_path)
    assert s["dirty"] is True and s["dirty_files"] == ["backend/app/m.py"]
    git("checkout", "--", "backend/app/m.py")
    (src.parent / "new_module.py").write_text("y = 1\n")  # uncommitted new source file also affects results
    s = prov.git_state(tmp_path)
    assert s["dirty"] is True and s["dirty_files"] == ["backend/app/new_module.py"]


def test_git_state_outside_a_repo(tmp_path):
    assert prov.git_state(tmp_path / "nope") == {"commit": None, "dirty": None}


def test_sha256_file(tmp_path):
    f = tmp_path / "d.csv"
    f.write_text("a,b\n")
    assert prov.sha256_file(f) == hashlib.sha256(b"a,b\n").hexdigest()
    assert prov.sha256_file(tmp_path / "missing") is None


def test_optional_data_field_changes_hash_only_when_set():
    base = {"model": "m", "start": "a", "end": "b", "horizon": 5, "step": 5, "warmup": 12, "reflect_every": 4,
            "target": "abs"}
    assert prov.config_hash(base) == prov.config_hash(base | {"data": None})
    assert prov.config_hash(base) != prov.config_hash(base | {"data": "data/prices_pit_2025-06-02.csv"})


def test_data_path_must_stay_under_backend_data():
    from app.sandbox.walkforward import DATA, resolve_data_path

    assert resolve_data_path(None) == DATA
    assert resolve_data_path("data/prices_pit_2025-06-02.csv").name == "prices_pit_2025-06-02.csv"
    for bad in ["../README.md", "/etc/passwd", "app/sandbox/jail.py"]:
        with pytest.raises(SystemExit):
            resolve_data_path(bad)


def test_reproduce_skips_forward_test_configs():
    import importlib.util

    spec = importlib.util.spec_from_file_location("reproduce", CONFIGS.parent / "scripts" / "reproduce.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.is_backtest_config(CONFIGS / "v2.toml")
    assert not mod.is_backtest_config(CONFIGS / "forward_v1.toml")
    assert mod.reproduce(CONFIGS / "forward_v1.toml") is True  # skipped, not crashed


def test_criteria_evaluator_handles_runs_without_fundamentals_or_rl():
    """v2 predates the fundamentals and RL arms: the evaluator must report, not crash."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("evaluate_criteria",
                                                  CONFIGS.parent / "scripts" / "evaluate_criteria.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ev = mod.evaluate("v2")
    assert "llm_fund" not in ev["arms"] and "phase_r" not in ev
    assert ev["phase_c"]["passed"] is False and ev["arms"]["llm_plain"]["brier_gap_vs_always_up"]["gap"] is not None
    assert "NOT PASSED" in mod.markdown(ev)
