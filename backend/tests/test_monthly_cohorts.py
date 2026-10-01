"""The monthly cohort runs end to end with a stub model (scripts/themes.py and scripts/longterm_picks.py `run`): what
is written, what is picked, and that a second run in the same month does nothing. No GPU, no network."""
from __future__ import annotations

import contextlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import forward_events
import longterm_picks as LT
import themes as TH

from app.forward.ledger import Ledger
from app.portfolio import themes as T
from app.sandbox import walkforward

NOW = datetime(2026, 10, 1, 22, 30, tzinfo=UTC)  # Thu 1 Oct, 18:30 New York: the first weekday, after 16:00


class Server:
    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    def stop(self) -> None:
        pass


def _line(text: str, start: str) -> str:
    return next(x for x in text.splitlines() if x.startswith(start))


class StubLLM:
    """Rates from the card it is given; every quote is a line copied from that card, as the prompts ask."""
    best: tuple[str, ...] = ()

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __call__(self, system: str, user: str, mode: str | None = None) -> str:
        if system.startswith("You are a risk officer"):
            q = _line(user, "Hyperscaler capex as a share")
            return json.dumps({"bull": [], "bear": [{"point": "spending outruns cash flow", "quote": q}],
                               "reason": "capex is most of the cash flow", "bubble_risk": {"label": "high", "quote": q}})
        field = "theme_outlook" if user.startswith("Theme:") else "outlook_6m"
        first = user.splitlines()[0]
        label = "5" if any(b in first for b in self.best) else "3"
        return json.dumps({"bull": [{"point": "demand", "quote": first}], "bear": [], "reason": "sample",
                           field: {"label": label, "quote": first}})

    async def next_token_probs(self, system: str, user: str, prefix: str, words: tuple[str, ...]) -> dict[str, float]:
        if any(b in user.splitlines()[0] for b in self.best):
            return {"1": 0.0, "2": 0.0, "3": 0.0, "4": 0.2, "5": 0.8}
        return {"1": 0.0, "2": 0.0, "3": 0.9, "4": 0.1, "5": 0.0}

    async def unload(self) -> None:
        pass


def _prices(tickers: set[str], end: str = "2026-09-30", n: int = 600) -> tuple[pd.DataFrame, pd.DataFrame]:
    days = pd.bdate_range(end=end, periods=n)
    rng = np.random.default_rng(0)
    px = pd.DataFrame({t: 100 * np.cumprod(1 + rng.normal(0.0003, 0.01, n)) for t in sorted(tickers)}, index=days)
    return px * 0.999, px


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(forward_events, "Ollama", Server)
    monkeypatch.setattr(forward_events, "wait_gpu_free", lambda *_a, **_k: True)
    monkeypatch.setattr(walkforward, "OllamaLLM", StubLLM)
    for mod in (TH, LT):
        monkeypatch.setattr(mod, "gpu_priority", lambda _name: contextlib.nullcontext())


def test_theme_cohort_is_made_once_and_ai_themes_wait_out_a_high_bubble_reading(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stubs: None, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(TH, "prices", lambda _now: _prices(T.tickers()))
    monkeypatch.setattr(TH, "fred", lambda _s: pd.Series(np.linspace(3.0, 3.4, 300),
                                                         index=pd.bdate_range(end="2026-09-30", periods=300)))
    monkeypatch.setattr(TH, "capex", lambda: (586e9, 319e9, 706e9))
    StubLLM.best = ("Semiconductors", "Cybersecurity", "Nuclear energy")
    TH.run(tmp_path, NOW, use_gpu=True)
    recs = Ledger(tmp_path / "ledger.jsonl").verify()
    assert [r["type"] for r in recs] == ["cohort"]
    c = recs[0]
    assert (c["month"], c["made_on"], c["bubble_risk"]) == ("2026-10", "2026-10-01", "high")
    # semiconductors were rated 5 too, but AI-linked themes are left out while the bubble gauge reads high
    assert c["picks"] == {"medium": ["cyber"], "long": ["nuclear"]}
    assert c["ratings"]["semis"] == 5 and c["scores"]["cyber"] == 4.8 and len(c["ratings"]) == len(T.THEMES)
    assert all(len(v) == T.N_PICKS for v in c["baseline"].values())
    doc = json.loads((tmp_path / "ratings_2026-10.json").read_text())
    assert doc["risk"]["bubble_risk"] == "high" and doc["risk"]["bear"][0]["verified"] is True
    assert len(doc["themes"]) == len(T.THEMES) and doc["register"].startswith("=== Market risk register ===")
    assert TH.status(recs)["latest"]["picks"] == c["picks"]
    TH.run(tmp_path, NOW, use_gpu=True)  # same month: nothing new
    assert len(Ledger(tmp_path / "ledger.jsonl").verify()) == 1
    assert "LEARN ALERT" not in capsys.readouterr().out


def test_theme_cohort_waits_when_the_gpu_is_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stubs: None,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(TH, "prices", lambda _now: _prices(T.tickers()))
    TH.run(tmp_path, NOW, use_gpu=False)
    assert not (tmp_path / "ledger.jsonl").exists()
    assert "a cohort is due but the GPU is off" in capsys.readouterr().out


def test_longterm_cohort_picks_ten_distinct_names_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stubs: None,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    names = [f"T{i:02d}" for i in range(14)]
    ev = pd.DataFrame({"cik": range(14), "ticker": names, "sector": "Industrials",
                       "accession": [f"a{i}" for i in range(14)],
                       "t": pd.Timestamp("2026-09-15", tz="UTC")})
    cards = [{"ticker": t, "cik": i, "accession": f"a{i}", "r12": 0.01 * i,
              "card": f"Company: {t} (Industrials)\nrevenue rose and the outlook was raised",
              "source": f"Company: {t} (Industrials)\nrevenue rose and the outlook was raised"}
             for i, t in enumerate(names)]
    monkeypatch.setattr(LT, "releases", lambda _d: ev)
    monkeypatch.setattr(LT, "cards", lambda _ev, _now, _closes: cards)
    monkeypatch.setattr(LT, "prices", lambda tickers, _now: _prices(set(tickers) | {"SPY"}))
    StubLLM.best = tuple(f"{t} " for t in names[:3])
    out = tmp_path / "longterm"
    LT.run(tmp_path / "events", out, NOW, use_gpu=True)
    recs = Ledger(out / "ledger.jsonl").verify()
    assert [r["type"] for r in recs] == ["cohort"]
    c = recs[0]
    assert (c["month"], c["made_on"], c["candidates"]) == ("2026-10", "2026-10-01", 14)
    assert len(c["tickers"]) == len(set(c["tickers"])) == LT.N_PICKS
    assert set(c["tickers"][:3]) == set(names[:3]) and c["ratings"][:3] == [5, 5, 5] and c["scores"][0] == 4.8
    # ties among the rest (rated 3) are broken by the better 12-month return
    assert c["tickers"][3:] == names[:6:-1]
    assert c["rating_counts"] == {"3": 11, "5": 3}
    rated = [json.loads(x) for x in (out / "ratings_2026-10.jsonl").read_text().splitlines()]
    assert len(rated) == 14 and rated[0]["bull"][0]["verified"] is True
    assert LT.status(recs, NOW)["issues"] == [] and LT.status(recs, NOW)["missing_months"] == []
    LT.run(tmp_path / "events", out, NOW, use_gpu=True)  # same month: nothing new
    assert len(Ledger(out / "ledger.jsonl").verify()) == 1
    assert "LEARN ALERT" not in capsys.readouterr().out


def test_a_pick_that_stopped_trading_does_not_stop_later_cohorts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                 stubs: None, capsys: pytest.CaptureFixture[str]
                                                                 ) -> None:
    """Before: the first cohort with a bought-out or delisted pick made scoring raise at every run, before the new
    month's cohort was made, so the track stopped for good. Now that cohort is reported and left unscored, the
    others are scored and the new cohort is made."""
    names = [f"T{i:02d}" for i in range(14)]
    opens, closes = _prices({*names, "SPY"})
    gone = opens.copy()
    gone.loc[gone.index[-70]:, "T13"] = np.nan  # T13 stopped trading before the first cohort's exit day
    out = tmp_path / "longterm"
    led = Ledger(out / "ledger.jsonl")
    made = [d.date().isoformat() for d in (opens.index[-120], opens.index[-100])]
    led.append("cohort", month="2026-05", made_on=made[0], tickers=names[4:14], ratings=[4] * 10)  # holds T13
    led.append("cohort", month="2026-06", made_on=made[1], tickers=names[0:10], ratings=[4] * 10)
    ev = pd.DataFrame({"cik": range(14), "ticker": names, "sector": "Industrials",
                       "accession": [f"a{i}" for i in range(14)], "t": pd.Timestamp("2026-09-15", tz="UTC")})
    cards = [{"ticker": t, "cik": i, "accession": f"a{i}", "r12": 0.01 * i, "card": f"Company: {t} (Industrials)",
              "source": f"Company: {t} (Industrials)"} for i, t in enumerate(names)]
    monkeypatch.setattr(LT, "releases", lambda _d: ev)
    monkeypatch.setattr(LT, "cards", lambda _ev, _now, _closes: cards)
    monkeypatch.setattr(LT, "prices", lambda _t, _now: (gone, closes))
    LT.run(tmp_path / "events", out, NOW, use_gpu=True)
    recs = led.verify()
    assert [(r["type"], r["month"]) for r in recs] == [("cohort", "2026-05"), ("cohort", "2026-06"),
                                                       ("result", "2026-06"), ("cohort", "2026-10")]
    text = capsys.readouterr().out
    assert "LEARN ALERT: long-term cohort 2026-05: missing entry/exit opens for T13" in text
    with pytest.raises(ValueError, match="missing entry/exit opens for T13"):  # the check itself is unchanged
        LT.score(recs[:2], gone)
