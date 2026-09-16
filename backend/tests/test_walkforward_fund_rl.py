"""Offline end-to-end walk-forward with fundamentals and the RL agent (fake LLM, synthetic data)."""
import json
import shutil
import sys
from datetime import date, timedelta

import numpy as np
import pytest

from app.learning.rl_agent import RLConfig
from app.sandbox import walkforward as wf

HAS_BWRAP = shutil.which("bwrap") is not None


class FakeLLM:
    def __init__(self, *a, **k):
        self.calls, self.cache_hits, self.context_overflows = 0, 0, 0

    async def __call__(self, system, user):
        self.calls += 1
        return json.dumps({"p_up": 0.55 if "Fundamentals" in user else 0.5, "lessons": ["be calibrated"]})


@pytest.fixture
def env(tmp_path, monkeypatch):
    backend = tmp_path / "backend"
    (backend / "data" / "edgar").mkdir(parents=True)
    rng = np.random.default_rng(0)
    tickers = [f"T{i:02d}" for i in range(12)]
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(700) if (date(2024, 1, 1) + timedelta(days=i)).weekday() < 5]
    closes = {t: 100 * np.exp(np.cumsum(rng.normal(0, 0.02, len(days)))) for t in [*tickers, "SPY"]}
    lines = ["Date," + ",".join([*tickers, "SPY"])]
    lines += [f"{d}," + ",".join(f"{closes[t][i]:.4f}" for t in [*tickers, "SPY"]) for i, d in enumerate(days)]
    (backend / "data" / "prices_test.csv").write_text("\n".join(lines) + "\n")
    facts = []
    for y in range(2021, 2027):
        for q, (s, e, f) in enumerate([("01-01", "03-31", "05-02"), ("04-01", "06-30", "08-02"),
                                       ("07-01", "09-30", "11-02")]):
            facts.append([f"{y}-{s}", f"{y}-{e}", 1 + 0.1 * y % 3 + rng.normal(0, 0.1), f"{y}-{f}"])
    doc = {"companies": {t: {"cik": i, "filings": [["8-K", "2025-05-01", "2.02"], ["10-Q", "2025-05-02", ""]],
                             "eps": {"tag": "x", "facts": facts}, "revenue": {"tag": "y", "facts": facts}}
                         for i, t in enumerate(tickers)}}
    (backend / "data" / "edgar" / "fundamentals.json").write_text(json.dumps(doc))
    monkeypatch.setattr(wf, "BACKEND", backend)
    monkeypatch.setattr(wf, "DATA", backend / "data" / "prices_test.csv")
    monkeypatch.setattr(wf, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(wf, "FUND_PATH", backend / "data" / "edgar" / "fundamentals.json")
    monkeypatch.setattr(wf, "OllamaLLM", FakeLLM)
    monkeypatch.setattr(wf.prov, "collect", lambda repo, data, model: {"data": {"sha256": "x"}, "git": {}})
    monkeypatch.setattr(wf, "ContinualTrainer", lambda n: wf.__dict__["_RealTrainer"](n, RLConfig(min_records=48,
                                                                                                   min_cutoffs=4, max_epochs=30)))
    monkeypatch.setattr(wf, "_RealTrainer", __import__("app.learning.rl_agent", fromlist=["x"]).ContinualTrainer,
                        raising=False)
    if not HAS_BWRAP:
        # CI has no bubblewrap. This test is about the data pipeline; the jail has its own tests. The run
        # (correctly) refuses to start when isolation can't be verified, so the probe result is simulated here.
        orig = wf.AgentJail

        class UnjailedForTest(orig):
            def __init__(self, llm, **kw):
                super().__init__(llm, allow_unjailed=True, **kw)

            async def probe(self, forbidden_paths):
                return {"readable_forbidden_paths": [], "network_reachable": False, "jailed": False,
                        "passed": True, "simulated_for_test": True}

        monkeypatch.setattr(wf, "AgentJail", UnjailedForTest)
    return tmp_path


async def test_fund_and_rl_arms_run_end_to_end(env):
    args = wf.parse_args(["--start", "2025-06-02", "--end", "2025-09-01", "--warmup", "4", "--reflect-every", "3",
                          "--fund", "--rl", "--tag", "e2e", "--data", "data/prices_test.csv"])
    rep = await wf.run(args)
    arms = set(rep["scores_full"])
    assert {"llm_fund", "feat_fund_logit", "sue_rule", "rl_forecast", "llm_plain", "selector"} <= arms
    assert rep["config"]["fund"] and rep["config"]["rl"] and rep["jail_probe_end"]["passed"]
    assert rep["jail_probe_end"].get("simulated_for_test", False) is not HAS_BWRAP
    statuses = [r["status"] for r in rep["rl_log"]]
    assert statuses[0] == "not_enough_data" and any(s != "not_enough_data" for s in statuses)
    assert rep["rl_trader"]["full"]["weeks"] >= 2 and len(rep["rl_trader"]["state_keys"]) == len(wf.RL_STATE_KEYS)
    assert "cross_sectional_after_warmup" in rep and "rank_ic_mean" in rep["cross_sectional_after_warmup"]["llm_fund"]
    rows = [json.loads(line) for line in (env / "results" / "walkforward_e2e_predictions.jsonl").read_text().splitlines()]
    rl_rows = [r for r in rows if r["arm"] == "rl_forecast"]
    assert rl_rows and all("position" in r and "x" not in r for r in rl_rows)
    # the fund-aware LLM saw the digest; the price-only one never did
    assert {r["p"] for r in rows if r["arm"] == "llm_fund"} == {0.55}
    assert {r["p"] for r in rows if r["arm"] == "llm_plain"} == {0.5}


async def test_rl_needs_fund(env):
    args = wf.parse_args(["--start", "2025-06-02", "--end", "2025-07-01", "--rl", "--tag", "bad",
                          "--data", "data/prices_test.csv"])
    with pytest.raises(SystemExit, match="needs --fund"):
        await wf.run(args)


def test_rl_states_ranks_and_logits():
    raw = {k: {**dict.fromkeys(wf.PRICE_KEYS, 0.0), **dict.fromkeys(wf.FUND_KEYS, 0.0), "ret_5d": v, "ret_20d": -v,
               "llm_plain": 0.5, "llm_fund": 0.4 + v, "llm_self": 0.99} for k, v in [("a", 0.1), ("b", 0.2), ("c", 0.0)]}
    s = wf.rl_states(raw)
    i5 = wf.RL_STATE_KEYS.index("rank_ret_5d")
    assert (s["c"][i5], s["a"][i5], s["b"][i5]) == (0.0, 0.5, 1.0)
    assert s["a"][wf.RL_STATE_KEYS.index("llm_plain")] == pytest.approx(0.0)
    assert s["a"][wf.RL_STATE_KEYS.index("llm_self")] == pytest.approx(np.log(0.99 / 0.01))


def test_sue_rule():
    assert wf.sue_rule({"fund_ok": 0}) == 0.5
    assert wf.sue_rule({"fund_ok": 1, "sue_1": 1, "sue_2": 0, "sue_3": 0, "sue_4": -1}) == 0.51
    assert wf.sue_rule({"fund_ok": 1, "sue_1": -2, "sue_2": 1, "sue_3": 0, "sue_4": 0}) == 0.49


async def test_probe_leak_and_memorization_offline(env):
    rep = await wf.probe_leak("m", "data/prices_test.csv", n_cutoffs=2)
    assert set(rep["rates"]) == {"prices", "prices+digest"} and rep["counts"]["prices"]["n"] == 24
    assert rep["passed"] is True  # the fake model never names a company
    mem = await wf.probe_memorization("m", tickers=("T00", "T01"))
    assert mem["unanswered_rate"] and all(v == 1.0 for v in mem["unanswered_rate"].values())


def test_cli_main_routes_probes_and_runs(env, monkeypatch, capsys):
    calls = []

    async def fake_run(args):
        calls.append(args.tag)
        return {"window": ["a", "b"], "n_cutoffs": 1, "llm_calls": 0, "runtime_s": 0,
                "scores_full": {"x": {}}, "scores_after_warmup": {"x": {}}}

    monkeypatch.setattr(wf, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["walkforward", "--tag", "cli"])
    wf.main()
    assert calls == ["cli"] and "scores_after_warmup" in capsys.readouterr().out


async def test_success_criteria_script_on_a_finished_run(env):
    import importlib.util

    args = wf.parse_args(["--start", "2025-06-02", "--end", "2025-10-01", "--warmup", "4", "--fund", "--rl",
                          "--tag", "crit", "--data", "data/prices_test.csv"])
    await wf.run(args)
    spec = importlib.util.spec_from_file_location("evaluate_criteria",
                                                  wf.Path(__file__).resolve().parents[1] / "scripts" / "evaluate_criteria.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ev = mod.evaluate("crit", env / "results")
    assert set(ev["arms"]) >= {"llm_fund", "rl_forecast", "sue_rule"}
    assert ev["phase_c"]["c3"] is False  # no leak probe file for this synthetic model: not assumed to pass
    assert ev["phase_c"]["passed"] is False and "phase_r" in ev and isinstance(ev["phase_r"]["passed"], bool)
    assert "Phase C criteria" in mod.markdown(ev) and (env / "results" / "criteria_crit.json").exists()


async def test_missing_fundamentals_file_gives_a_clear_error(env, monkeypatch):
    monkeypatch.setattr(wf, "FUND_PATH", env / "backend" / "data" / "edgar" / "missing.json")
    args = wf.parse_args(["--start", "2025-06-02", "--end", "2025-07-01", "--fund", "--tag", "nofund",
                          "--data", "data/prices_test.csv"])
    with pytest.raises(SystemExit, match="app.data_ingestion.edgar"):
        await wf.run(args)
