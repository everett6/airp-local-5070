import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import full_auto as fa


def lot(sym, rating, w, state="open"):
    return {"symbol": sym, "rating": rating, "weight": w, "state": state, "side": 1 if rating > 3 else -1,
            "horizon": "short", "decided_at": "2026-10-07T01:00:00+00:00", "session": "2026-10-07",
            "exit_session": "2026-10-14"}


def test_unstrong_halves_strong_labels_and_keeps_gross():
    lots = [lot("A", 5, 0.08), lot("B", 4, 0.04), lot("C", 1, 0.08), lot("D", 5, 0.5, state="closed")]
    out = fa.unstrong(lots)
    open_w = [x["weight"] for x in out if x["state"] == "open"]
    assert sum(open_w) == pytest.approx(0.20)  # gross of the open book unchanged
    assert open_w[0] == pytest.approx(open_w[1]) == pytest.approx(open_w[2])  # strong no longer double


def test_beta_cap_scales_stocks_when_the_hedge_is_capped():
    w = {"A": 0.6, "B": 0.6, "QQQ": -0.24, "QQQM": -0.24, "XLK": -0.24, "VGT": -0.24}
    out = fa.beta_cap(w, {"A": 1.5, "B": 1.5}, 0.30)
    net = 1.5 * (out["A"] + out["B"]) + sum(out[e] for e in ("QQQ", "QQQM", "XLK", "VGT"))
    assert net == pytest.approx(0.30) and out["QQQ"] == -0.24
    assert fa.beta_cap({"A": 0.2, "QQQ": -0.2}, {"A": 1.0}, 0.3) == {"A": 0.2, "QQQ": -0.2}


def test_participation_caps_new_risk_only():
    orders = [{"symbol": "X", "qty": 1000, "reducing": False}, {"symbol": "Y", "qty": 1000, "reducing": True},
              {"symbol": "Z", "qty": 10, "reducing": False}]
    out = fa.participation(orders, {"X": 100, "Y": 100, "Z": 100}, {"X": 1_000_000, "Y": 1_000, "Z": 50})
    assert out[0]["qty"] == 100 and out[1]["qty"] == 1000 and len(out) == 2  # Z capped below one share: dropped


def test_controls_are_seeded_and_mirror_the_lot():
    a = fa.controls(lot("A", 5, 0.05), mom=-1)
    assert [c["kind"] for c in a] == ["random", "momentum"] and a[1]["side"] == -1
    assert a == fa.controls(lot("A", 5, 0.05), mom=-1) and all(c["weight"] == 0.05 for c in a)


def test_why_none_gives_the_concrete_reason():
    rec = {"decided_at": "2026-10-07T01:00:00+00:00", "ratings": {"short": 3, "mid": 3}}
    assert fa.why_none(rec, [], "A").startswith("PASS")
    rec = {**rec, "ratings": {"short": 5, "mid": 3}}
    assert "already planned" in fa.why_none(rec, [lot("A", 5, 0.1)], "A")
    assert "theme rule" in fa.why_none({**rec, "theme_horizons": ["mid"]}, [], "A")
    assert "sizing" in fa.why_none(rec, [lot("B", 5, 0.1)], "A")


def test_gpu_problem_reports_a_broken_driver(monkeypatch):
    import subprocess
    broken = subprocess.CompletedProcess([], 18, "Failed to initialize NVML: Driver/library version mismatch\n", "")
    monkeypatch.setattr(fa.subprocess, "run", lambda *a, **k: broken)
    assert "version mismatch" in fa.gpu_problem()
    ok = subprocess.CompletedProcess([], 0, "NVIDIA GeForce RTX 5070\n", "")
    monkeypatch.setattr(fa.subprocess, "run", lambda *a, **k: ok)
    assert fa.gpu_problem() is None


def test_research_waits_on_a_broken_gpu_and_charges_no_company(monkeypatch, tmp_path):
    from datetime import timedelta

    import desktop_run
    now = fa.now_utc()
    c = object.__new__(fa.Controller)
    c.child, c.gpu_paused, c.names = None, None, {"AAA"}
    c.companies, c.state, c.status, logged = [{"ticker": "AAA"}], {"attempts": {}}, {}, []
    c.log = lambda kind, **f: logged.append(kind)
    monkeypatch.setattr(fa, "DIR", tmp_path)
    monkeypatch.setattr(desktop_run, "next_slot", lambda _now: now + timedelta(hours=5))
    monkeypatch.setattr(fa, "research_all_active", lambda _now: False)
    monkeypatch.setattr(fa, "decisions", lambda names: {})
    monkeypatch.setattr(fa, "gpu_problem", lambda: "nvidia-smi failed (rc 18)")
    started = []
    monkeypatch.setattr(fa.subprocess, "Popen", lambda *a, **k: started.append(a))
    c.research_tick(now)
    c.research_tick(now)
    assert started == [] and c.state["attempts"] == {}
    assert logged == ["research_paused"]  # once, not every loop
    assert c.status["research"]["paused"].startswith("nvidia-smi failed")
