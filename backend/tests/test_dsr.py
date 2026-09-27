import numpy as np

from app.sandbox.dsr import deflated_sharpe, expected_max_sharpe, load_registry, register


def test_expected_max_grows_with_trials():
    assert expected_max_sharpe(1, 0.01) == 0.0
    a, b = expected_max_sharpe(10, 0.01), expected_max_sharpe(1000, 0.01)
    assert 0 < a < b


def test_noise_is_not_significant_and_strong_signal_is():
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 0.01, 2500)
    strong = rng.normal(0.002, 0.01, 2500)  # daily Sharpe 0.2 ~ 3.2 annualized
    var = (0.5 / np.sqrt(252)) ** 2
    assert deflated_sharpe(noise, 20, var) < 0.5
    assert deflated_sharpe(strong, 20, var) > 0.99


def test_more_trials_deflate_more():
    r = np.random.default_rng(1).normal(0.0006, 0.01, 2500)
    var = (0.5 / np.sqrt(252)) ** 2
    assert deflated_sharpe(r, 2, var) > deflated_sharpe(r, 200, var)


def test_registry_roundtrip(tmp_path):
    p = tmp_path / "reg.jsonl"
    register({"trial": "a", "sharpe_ann": 0.4}, p)
    register({"trial": "b", "sharpe_ann": 0.1}, p)
    assert [x["trial"] for x in load_registry(p)] == ["a", "b"]
