"""Phase F: log-prob scoring, Newton stacker, anomaly and Kronos baselines, LAP probe, Deflated Sharpe, GPU lock."""
import asyncio
import hashlib
import importlib.util
import json
import math
import shutil
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import numpy as np
import pytest
from test_walkforward_fund_rl import env  # noqa: F401  (fixture)

from app.sandbox import agent_worker as aw
from app.sandbox import anomalies as anom
from app.sandbox import ohlcv
from app.sandbox import provenance as prov
from app.sandbox import walkforward as wf
from app.sandbox.gpu_lock import gpu_job
from app.sandbox.jail import AgentJail
from app.sandbox.pit_data import PriceTable
from app.sandbox.scoring import deflated_sharpe, probabilistic_sharpe, weekly_long_short

BACKEND = Path(__file__).resolve().parents[1]


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, BACKEND / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- Newton stacker

def test_newton_reaches_the_same_optimum_as_long_gradient_descent():
    rng = np.random.default_rng(1)
    rows = rng.normal(size=(400, 4)).tolist()
    ys = [int(rng.random() < 1 / (1 + math.exp(-(0.8 * r[0] - 0.5 * r[2] + 0.2)))) for r in rows]
    w_newton = aw.fit_logistic_newton(rows, ys)
    w_gd = aw.fit_logistic(rows, ys, iters=5000)
    assert np.allclose(w_newton, w_gd, atol=2e-3)
    # the gradient of the penalized objective is ~0 at the Newton solution
    x = np.hstack([np.ones((400, 1)), np.array(rows)])
    p = 1 / (1 + np.exp(-(x @ np.array(w_newton))))
    g = x.T @ (p - np.array(ys)) / 400
    g[1:] += 0.05 * np.array(w_newton[1:])
    assert np.abs(g).max() < 1e-8


def test_newton_handles_separable_and_constant_columns():
    rows = [[float(i), 1.0] for i in range(-50, 50)]
    ys = [int(i >= 0) for i in range(-50, 50)]
    w = aw.fit_logistic_newton(rows, ys)
    assert all(math.isfinite(v) for v in w) and aw.predict_logistic(w, [10.0, 1.0]) > 0.9


def test_guarded_stacker_uses_the_requested_solver(monkeypatch):
    used = []
    monkeypatch.setattr(aw, "fit_logistic_newton", lambda r, y: used.append("newton") or [0.0] * 7)
    monkeypatch.setattr(aw, "fit_logistic", lambda r, y: used.append("gd") or [0.0] * 7)
    f = aw.features([100 + k for k in range(120)], [100.0] * 120)
    resolved = [{"p_llm": 0.6, "features": f, "up": k % 2 == 0} for k in range(250)]
    aw.guarded_stacker(resolved, solver="newton")
    aw.guarded_stacker(resolved)
    assert used[0] == "newton" and used[-1] == "gd"


# ---------------------------------------------------------------- log-prob scoring

def test_updown_prompt_asks_for_one_word_and_keeps_the_task():
    s = aw.system_prompt_updown("abs", 20)
    assert s.endswith("Answer with exactly one word: UP or DOWN.") and "20 trading days" in s and "JSON" not in s


def test_run_predict_logprob_mode_marks_requests(monkeypatch):
    sent = []
    monkeypatch.setattr(aw, "_send", sent.append)
    monkeypatch.setattr(aw, "_recv", lambda: {"llm_responses": {"a0": '{"p_up": 0.73}'}})
    item = {"id": "a0", "asset": [100 + k for k in range(120)], "market": [100.0 + 0.1 * k for k in range(120)]}
    out = aw.run_predict({"items": [item], "score": "logprob"})
    req = sent[0]["llm_requests"][0]
    assert req["mode"] == "updown" and "one word" in req["system"]
    assert out["predictions"][0]["p_llm"] == 0.73


def test_word_probabilities_sum_variants_and_prefixes():
    lp = [{"token": "UP", "logprob": math.log(0.6),
           "top_logprobs": [{"token": "UP", "logprob": math.log(0.6)}, {"token": " Down", "logprob": math.log(0.2)},
                            {"token": "_DOWN", "logprob": math.log(0.1)}, {"token": "UN", "logprob": math.log(0.05)},
                            {"token": "U", "logprob": math.log(0.04)}, {"token": "**", "logprob": math.log(0.01)}]}]
    probs = wf.word_probabilities(lp, ("UP", "DOWN", "UNKNOWN"))
    assert probs["UP"] == pytest.approx(0.6) and probs["DOWN"] == pytest.approx(0.3)
    assert probs["UNKNOWN"] == pytest.approx(0.05)  # "UN" is a unique prefix; "U" is ambiguous and ignored
    p, mass = wf.updown_probability(lp)
    assert p == pytest.approx(0.6 / 0.9) and mass == pytest.approx(0.9)
    assert wf.updown_probability([]) == (0.5, 0.0)
    assert wf.updown_probability([{"token": "**", "logprob": 0.0, "top_logprobs": []}]) == (0.5, 0.0)


def _mock_llm(tmp_path, monkeypatch, handler):
    monkeypatch.setattr(wf, "RESULTS", tmp_path)
    llm = wf.OllamaLLM("m", base_url="http://ollama.test:1")
    llm._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return llm


def test_ollama_updown_mode_requests_logprobs_and_caches_separately(tmp_path, monkeypatch):
    bodies = []

    def handler(request):
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "m", "size": 10, "size_vram": 10}]})
        body = json.loads(request.content)
        bodies.append(body)
        if body.get("logprobs"):
            return httpx.Response(200, json={"message": {"content": "UP"}, "logprobs": [
                {"token": "UP", "logprob": math.log(0.7), "top_logprobs": [
                    {"token": "UP", "logprob": math.log(0.7)}, {"token": "DOWN", "logprob": math.log(0.3)}]}]})
        return httpx.Response(200, json={"message": {"content": '{"p_up": 0.52}'}})

    llm = _mock_llm(tmp_path, monkeypatch, handler)
    verbal = asyncio.run(llm("s", "u"))
    lp = json.loads(asyncio.run(llm("s", "u", mode="updown")))
    assert verbal == '{"p_up": 0.52}' and lp["p_up"] == pytest.approx(0.7) and len(llm._cache) == 2
    assert "format" in bodies[0] and "format" not in bodies[1]
    assert bodies[1]["top_logprobs"] == 20 and bodies[1]["options"]["num_predict"] == 1
    assert llm.base_url == "http://ollama.test:1"
    with pytest.raises(ValueError, match="unknown LLM mode"):
        asyncio.run(llm("s", "other", mode="nope"))


def test_ollama_url_from_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(wf, "RESULTS", tmp_path)
    monkeypatch.setenv("AIRP_OLLAMA_URL", "http://127.0.0.1:11435/")
    assert wf.OllamaLLM("m").base_url == "http://127.0.0.1:11435"


async def test_jail_relays_mode_and_rejects_unknown_modes(tmp_path):
    seen = []

    async def llm(system, user, mode=None):
        seen.append(mode)
        return "ok"

    worker = tmp_path / "w.py"
    worker.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    msg = json.loads(line)\n"
        "    if 'task' in msg:\n"
        "        print(json.dumps({'llm_requests': [{'id': 'a', 'system': 's', 'user': 'u'},\n"
        "                          {'id': 'b', 'system': 's', 'user': 'u', 'mode': msg['mode']}]}), flush=True)\n"
        "    else:\n"
        "        print(json.dumps({'result': msg['llm_responses']}), flush=True)\n")
    jailed = shutil.which("bwrap") is not None
    async with AgentJail(llm, allow_unjailed=not jailed, worker=worker) as jail:
        assert await jail.call({"task": "x", "mode": "updown"}) == {"a": "ok", "b": "ok"}
    assert seen == [None, "updown"]
    from app.sandbox.jail import JailError
    async with AgentJail(llm, allow_unjailed=not jailed, worker=worker) as jail:
        with pytest.raises(JailError, match="malformed"):
            await jail.call({"task": "x", "mode": "run_shell"})


# ---------------------------------------------------------------- anomalies

def _table(n=400, seed=0, tickers=("A", "B", "C", "SPY")):
    rng = np.random.default_rng(seed)
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(n * 2) if (date(2024, 1, 1) + timedelta(days=i)).weekday() < 5][:n]
    closes = {t: list(100 * np.exp(np.cumsum(rng.normal(0.0003, 0.02, n)))) for t in tickers}
    return PriceTable(days, closes)


def test_anomaly_features_match_definitions():
    t = _table()
    view = t.view(t.dates[300])
    f = anom.features(view, "A", "SPY")
    c = view.closes["A"]
    assert f["mom_12_1"] == pytest.approx(c[-22] / c[-253] - 1)
    assert f["rev_1m"] == pytest.approx(-(c[-1] / c[-22] - 1))
    assert f["hi_52w"] == pytest.approx(c[-1] / max(c[-252:])) and f["hi_52w"] <= 1
    assert 0.05 < f["ivol_60d"] < 1.0


def test_anomaly_features_ignore_everything_after_the_cutoff():
    t = _table()
    cut = t.dates[300]
    before = {k: anom.features(t.view(cut), k, "SPY") for k in "ABC"}
    for k in t.closes:  # rewrite the future
        t.closes[k][301:] = [x * 3 for x in t.closes[k][301:]]
    assert {k: anom.features(t.view(cut), k, "SPY") for k in "ABC"} == before


def test_anomaly_features_short_history_do_not_crash():
    t = _table(n=30)
    f = anom.features(t.view(t.dates[-1]), "A", "SPY")
    assert all(math.isfinite(v) for v in f.values())


def test_anomaly_scores_signs_ties_and_probability():
    feats = {"hi": {"mom_12_1": 0.5, "rev_1m": 0.1, "hi_52w": 1.0, "ivol_60d": 0.1},
             "lo": {"mom_12_1": -0.5, "rev_1m": -0.1, "hi_52w": 0.5, "ivol_60d": 0.9},
             "mid": {"mom_12_1": 0.0, "rev_1m": 0.0, "hi_52w": 0.75, "ivol_60d": 0.5}}
    s = anom.anomaly_scores(feats, {"hi": 2.0, "lo": -1.0, "mid": None})
    assert s["hi"] == pytest.approx(1.0) and s["lo"] == pytest.approx(0.0) and s["mid"] == pytest.approx(0.5)
    tied = anom.anomaly_scores({k: dict.fromkeys(anom.ANOMALY_KEYS, 1.0) for k in "xyz"}, dict.fromkeys("xyz"))
    assert set(tied.values()) == {0.5}
    assert anom.anomaly_probability(0) == 0.45 and anom.anomaly_probability(1) == 0.55
    assert anom.anomaly_probability(7) == 0.55
    assert anom.sue_composite({"fund_ok": 0}) is None
    assert anom.sue_composite({"fund_ok": 1, "sue_1": 1, "sue_2": 1, "sue_3": 1, "sue_4": 1}) == pytest.approx(1.0)


# ---------------------------------------------------------------- Kronos inputs/outputs

def test_ohlcv_window_is_point_in_time(tmp_path):
    p = tmp_path / "o.csv"
    rows = ["Date,Ticker,Open,High,Low,Close,Volume"]
    for i in range(10):
        d = date(2025, 1, 1) + timedelta(days=i)
        rows += [f"{d},X,{i},{i},{i},{i},{i}", f"{d},Y,1,1,1,1,1"]
    p.write_text("\n".join(reversed(rows[1:])) + "\n")
    p.write_text(rows[0] + "\n" + "\n".join(rows[1:]) + "\n")
    bars = ohlcv.load_ohlcv(p)
    w = ohlcv.window(bars["X"], date(2025, 1, 5), 3)
    assert [b[0].day for b in w] == [3, 4, 5] and w[-1][4] == 4.0
    assert ohlcv.window(bars["X"], date(2024, 12, 31), 3) == []
    assert len(ohlcv.window(bars["X"], date(2030, 1, 1), 100)) == 10


def test_kronos_sample_probability():
    assert ohlcv.sample_probability([]) == 0.5 and ohlcv.sample_probability([0.1]) == 0.5
    assert ohlcv.sample_probability([0.01] * 5) == 0.75 and ohlcv.sample_probability([0.0] * 5) == 0.5
    p_up = ohlcv.sample_probability([0.02, 0.01, 0.03, -0.01])
    assert 0.5 < p_up < 0.75
    assert ohlcv.sample_probability([-x for x in [0.02, 0.01, 0.03, -0.01]]) == pytest.approx(1 - p_up)


def _write_kronos(results, tag, table_dates, cfg, tickers, ohlcv_sha, p=0.6):
    cutoffs = ohlcv.cutoffs_for(cfg, table_dates)
    path = results / f"kronos_{tag}.jsonl"
    results.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps({"cutoff": c.isoformat(), "ticker": t, "p": p + 0.01 * i})
                              for c in cutoffs for i, t in enumerate(tickers)) + "\n")
    meta = {"params": ohlcv.KRONOS_PARAMS, "horizon": cfg["horizon"], "cutoffs": [c.isoformat() for c in cutoffs],
            "ohlcv_sha256": ohlcv_sha, "forecasts_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    path.with_suffix(".meta.json").write_text(json.dumps(meta))
    return path, cutoffs


def test_kronos_forecasts_are_refused_unless_everything_matches(tmp_path):
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(60)]
    cfg = {"start": "2025-01-10", "end": "2025-02-10", "horizon": 5, "step": 5}
    path, cutoffs = _write_kronos(tmp_path, "t", dates, cfg, ["X", "Y"], "sha")
    got = ohlcv.load_forecasts(path, "sha", cutoffs, 5)
    assert got[(cutoffs[0], "Y")] == pytest.approx(0.61) and len(got) == 2 * len(cutoffs)
    with pytest.raises(SystemExit, match="OHLCV data differs"):
        ohlcv.load_forecasts(path, "other", cutoffs, 5)
    with pytest.raises(SystemExit, match="cutoffs or horizon"):
        ohlcv.load_forecasts(path, "sha", cutoffs[1:], 5)
    path.write_text(path.read_text().replace("0.61", "0.99"))
    with pytest.raises(SystemExit, match="modified"):
        ohlcv.load_forecasts(path, "sha", cutoffs, 5)
    assert ohlcv.cutoffs_for({"start": "2025-01-10", "horizon": 5, "step": 5}, dates)[-1] == dates[-6]
    with pytest.raises(ValueError):
        ohlcv.cutoffs_for({"start": "2024-01-01", "horizon": 5, "step": 5}, dates)


# ---------------------------------------------------------------- end to end (v7 options)

class LPFakeLLM:
    def __init__(self, *a, **k):
        self.calls, self.cache_hits, self.context_overflows, self.updown_no_mass = 0, 0, 0, 0
        self.base_url = "http://fake"
        self.cache_bad_lines = 0

    async def __call__(self, system, user, mode=None):
        self.calls += 1
        if mode == "updown":
            # a continuous score that depends on the input, like real log-probs
            return json.dumps({"p_up": 0.3 + (hash(user) % 1000) / 2500, "mass": 0.99})
        return json.dumps({"p_up": 0.55 if "Fundamentals" in user else 0.5, "lessons": ["be calibrated"]})


async def test_phase_f_arms_run_end_to_end_and_evaluate(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(wf, "OllamaLLM", LPFakeLLM)
    backend = env / "backend"
    table = PriceTable.from_csv(backend / "data" / "prices_test.csv")
    tickers = [t for t in table.tickers if t != "SPY"]
    lines = ["Date,Ticker,Open,High,Low,Close,Volume"]
    lines += [f"{d},{t},{c},{c},{c},{c},1000" for t in table.tickers for d, c in zip(table.dates, table.closes[t], strict=True)]
    (backend / "data" / "ohlcv_test.csv").write_text("\n".join(lines) + "\n")
    sha = prov.sha256_file(backend / "data" / "ohlcv_test.csv")
    cfg = {"start": "2025-06-02", "end": "2025-10-01", "horizon": 5, "step": 5}
    _write_kronos(env / "results", "f7", table.dates, cfg, tickers, sha)
    argv = ["--start", "2025-06-02", "--end", "2025-10-01", "--warmup", "4", "--reflect-every", "3", "--fund", "--rl",
            "--score-logprob", "--solver", "newton", "--anomalies", "--kronos", "--ohlcv", "data/ohlcv_test.csv",
            "--rl-state", "v7", "--tag", "f7", "--data", "data/prices_test.csv"]
    rep = await wf.run(wf.parse_args(argv))
    arms = set(rep["scores_full"])
    assert {"llm_lp", "llm_fund_lp", "anomaly_rank", "anomaly_logit", "kronos", "rl_forecast", "llm_fund"} <= arms
    for key in ("score_logprob", "anomalies", "kronos", "ohlcv", "solver", "rl_state"):
        assert rep["config"][key]
    assert rep["provenance"]["kronos"]["ohlcv_sha256"] == sha and rep["provenance"]["ollama_url"] == "http://fake"
    assert rep["rl_trader"]["state_keys"] == wf.RL_STATE_KEYS_V7 and rep["updown_no_mass"] == 0
    rows = [json.loads(x) for x in (env / "results" / "walkforward_f7_predictions.jsonl").read_text().splitlines()]
    lp = {r["p"] for r in rows if r["arm"] == "llm_fund_lp"}
    assert len(lp) > 10  # continuous, unlike the verbalized arm
    assert {r["p"] for r in rows if r["arm"] == "llm_fund"} == {0.55}
    assert all("xa" not in r for r in rows) and any(r["arm"] == "anomaly_logit" and r["p"] != 0.5 for r in rows)
    kr = [r for r in rows if r["arm"] == "kronos"]
    assert kr and {round(r["p"], 2) for r in kr} >= {0.6, 0.61}
    # a different option set is a different frozen experiment
    assert rep["config_hash"] != prov.config_hash({k: v for k, v in rep["config"].items() if k != "kronos"})

    # criteria: Phase F section present; F3 cannot pass without leak-probe and LAP-test files
    mod = _load_script("evaluate_criteria")
    ev = mod.evaluate("f7", env / "results")
    assert "phase_f" in ev and ev["phase_f"]["f3"] is False and ev["phase_f"]["passed"] is False
    assert set(ev["phase_f"]["f2_gaps"]) == {"anomaly_rank", "kronos", "sue_rule"}
    assert ev["sharpe_deflation"]["n_trials"] >= len(arms) and "rl_trader" in ev["sharpe_deflation"]
    md = mod.markdown(ev)
    assert "Phase F (primary llm_fund_lp)" in md and "Deflated Sharpe" in md


async def test_phase_f_option_guards(env):  # noqa: F811
    base = ["--start", "2025-06-02", "--end", "2025-07-01", "--tag", "g", "--data", "data/prices_test.csv"]
    with pytest.raises(SystemExit, match="needs --fund"):
        await wf.run(wf.parse_args([*base, "--anomalies"]))
    with pytest.raises(SystemExit, match="unknown solver"):
        await wf.run(wf.parse_args([*base, "--solver", "sgd"]))
    with pytest.raises(SystemExit, match="needs --ohlcv"):
        await wf.run(wf.parse_args([*base, "--kronos"]))
    with pytest.raises(SystemExit, match="kronos_forecasts.py"):
        await wf.run(wf.parse_args([*base, "--kronos", "--ohlcv", "data/prices_test.csv"]))
    with pytest.raises(SystemExit, match="rl_state"):
        await wf.run(wf.parse_args([*base, "--fund", "--rl", "--rl-state", "v7"]))


def test_new_config_fields_hash_only_when_set():
    base = {"model": "m", "start": "a", "end": "b", "horizon": 5, "step": 5, "warmup": 12, "reflect_every": 4,
            "target": "abs", "fund": True}
    h = prov.config_hash(base)
    assert prov.config_hash({**base, "score_logprob": False, "solver": None, "ollama_url": "http://x"}) == h
    assert prov.config_hash({**base, "score_logprob": True}) != h
    assert prov.config_hash({**base, "solver": "newton"}) != h


def test_v7_config_file_is_valid():
    cfg_path = BACKEND / "configs" / "v7_phase_f.toml"
    if not cfg_path.exists():
        pytest.skip("v7 config not written yet")
    cfg = prov.load_config_file(cfg_path)
    args = wf.parse_args(["--config", str(cfg_path)])
    assert args.score_logprob and args.anomalies and args.kronos and args.solver == "newton" and args.rl_state == "v7"
    assert cfg["tag"] == "v7_phase_f"


# ---------------------------------------------------------------- LAP

def test_lap_from_answer():
    a = wf.lap_from_answer(json.dumps({"up": 0.3, "down": 0.1, "unknown": 0.6}))
    assert a["lap"] == pytest.approx(0.4) and a["p_up_recall"] == pytest.approx(0.75)
    assert math.isnan(wf.lap_from_answer("garbage")["lap"])
    assert math.isnan(wf.lap_from_answer(json.dumps({"up": 0, "down": 0, "unknown": 0}))["lap"])
    assert wf.lap_from_answer(json.dumps({"up": 0, "down": 0, "unknown": 1}))["p_up_recall"] == 0.5


async def test_probe_lap_offline(env, monkeypatch):  # noqa: F811
    class LapLLM(LPFakeLLM):
        async def __call__(self, system, user, mode=None):
            assert mode == "lap" and "Did T" in user
            return json.dumps({"up": 0.2, "down": 0.1, "unknown": 0.7})

    monkeypatch.setattr(wf, "OllamaLLM", LapLLM)
    rep = await wf.probe_lap("m", "data/prices_test.csv", tag="lp", start="2025-06-02", end="2025-07-01")
    assert rep["by_month"] and all(v["mean_lap"] == pytest.approx(0.3) for v in rep["by_month"].values())
    grid = [json.loads(x) for x in (env / "results" / "lap_lp.jsonl").read_text().splitlines()]
    assert len(grid) == 12 * 5 and rep["grid_mean_lap"] == pytest.approx(0.3)


def test_lap_interaction_test_detects_a_planted_leak():
    mod = _load_script("lap_test")
    rng = np.random.default_rng(3)
    leak, clean = [], []
    for w in range(40):
        for _ in range(50):
            up = bool(rng.random() < 0.5)
            lap = float(rng.random())
            # leaky forecaster: knows the answer exactly when it remembers (high LAP)
            p_leak = (0.9 if up else 0.1) if rng.random() < lap else float(rng.random())
            leak.append({"cutoff": f"w{w:02d}", "p": p_leak, "up": up, "lap": lap, "p_up_recall": 0.5})
            clean.append({"cutoff": f"w{w:02d}", "p": float(rng.random()), "up": up, "lap": lap, "p_up_recall": 0.5})
    assert mod.interaction_test(leak, n_boot=300)["leak_signature"] is True
    assert mod.interaction_test(clean, n_boot=300)["leak_signature"] is False


# ---------------------------------------------------------------- Deflated Sharpe

def test_psr_and_dsr_behave():
    rng = np.random.default_rng(0)
    good = rng.normal(0.01, 0.02, 60)
    noise = rng.normal(0.0, 0.02, 60)
    assert probabilistic_sharpe(good) > 0.99 and probabilistic_sharpe(-good) < 0.01
    assert probabilistic_sharpe([0.1, 0.2]) is None
    one = deflated_sharpe(good, 1)
    many = deflated_sharpe(good, 50)
    assert one["sr0_per_period"] == 0.0 and one["dsr"] == pytest.approx(probabilistic_sharpe(good))
    assert many["dsr"] < one["dsr"] and many["sr0_annual"] > 0
    assert deflated_sharpe(noise, 50)["dsr"] < 0.5
    assert deflated_sharpe([0.1], 5)["dsr"] is None
    with_trials = deflated_sharpe(good, 10, trial_sharpes=[0.0, 0.5, 1.0])
    assert with_trials["sr0_per_period"] > deflated_sharpe(good, 10)["sr0_per_period"]


def test_weekly_long_short_matches_score():
    preds = [{"cutoff": c, "p": p, "ret": r} for c, p, r in
             [(1, 0.6, 0.02), (1, 0.4, 0.01), (2, 0.5, 0.5), (2, 0.7, -0.01), (3, 0.5, 0.2)]]
    assert weekly_long_short(preds) == pytest.approx([0.005, -0.01])


# ---------------------------------------------------------------- GPU lock

def test_gpu_lock_serializes_jobs(tmp_path, capsys):
    path = tmp_path / "gpu.lock"
    order = []

    def second():
        with gpu_job("second", path=path, poll_s=0.01):
            order.append("second")

    with gpu_job("first", path=path):
        assert "first" in path.read_text()
        t = threading.Thread(target=second)
        t.start()
        time.sleep(0.1)
        order.append("first-done")
    t.join(2)
    assert order == ["first-done", "second"] and "waiting for another GPU job (first" in capsys.readouterr().out
