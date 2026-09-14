"""Forward test: ledger integrity, schedule rules, and the weekly cycle (offline)."""
import json
import shutil
from datetime import date, datetime, timedelta

import pytest

from app.forward import run as F
from app.forward.ledger import Ledger, LedgerError
from app.forward.schedule import NY, deadline, resolve_day, weekly_cutoffs

HAS_BWRAP = shutil.which("bwrap") is not None


def ny(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=NY)


def trading_days(start, end, holidays=()):
    d, out = start, []
    while d <= end:
        if d.weekday() < 5 and d not in holidays:
            out.append(d)
        d += timedelta(days=1)
    return out


# ---------------- ledger ----------------

def test_ledger_chain_detects_edits_deletions_and_reordering(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    for i in range(3):
        led.append("decision", cutoff=f"2026-09-{11 + 7 * i}", arms={"x": {"A": 0.5}})
    assert [r["seq"] for r in led.verify()] == [0, 1, 2]
    lines = led.path.read_text().splitlines()
    tampered = json.loads(lines[1])
    tampered["arms"]["x"]["A"] = 0.9  # change a past prediction
    for bad in ([lines[0], json.dumps(tampered), lines[2]], [lines[0], lines[2]], [lines[1], lines[0], lines[2]]):
        led.path.write_text("\n".join(bad) + "\n")
        with pytest.raises(LedgerError):
            led.verify()
        with pytest.raises(LedgerError):
            led.append("outcome")  # refuses to extend a broken chain


# ---------------- schedule ----------------

def test_deadline_is_next_weekday_open():
    assert deadline(date(2026, 9, 11)) == ny(2026, 9, 14, 9, 30)  # Friday -> Monday
    assert deadline(date(2026, 11, 25)) == ny(2026, 11, 26, 9, 30)  # conservative on Thanksgiving


def test_weekly_cutoffs_handle_holidays_and_unsettled_days():
    days = trading_days(date(2026, 8, 31), date(2026, 9, 18), holidays={date(2026, 9, 7)})
    # Friday 9/11 16:00 ET: not settled yet, so the week of 9/8 isn't complete
    assert weekly_cutoffs(days, ny(2026, 9, 11, 16, 0)) == [date(2026, 9, 4)]
    assert weekly_cutoffs(days, ny(2026, 9, 11, 16, 20))[-1] == date(2026, 9, 11)
    # a week whose last trading day is Thursday (Good Friday style)
    short = trading_days(date(2026, 3, 30), date(2026, 4, 2))
    assert weekly_cutoffs(short, ny(2026, 4, 3, 17)) == [date(2026, 4, 2)]
    assert weekly_cutoffs(short, ny(2026, 4, 2, 17)) == []  # Friday could still trade


def test_resolve_day():
    days = trading_days(date(2026, 9, 8), date(2026, 9, 25))
    assert resolve_day(date(2026, 9, 11), days, 5, ny(2026, 9, 18, 16, 20)) == date(2026, 9, 18)
    assert resolve_day(date(2026, 9, 11), days, 5, ny(2026, 9, 18, 15)) is None


# ---------------- weekly cycle ----------------

TICKERS = ["AAA", "BBB"]


def fake_bars_fn(last_day):
    async def fn(tickers):
        days = [d for d in trading_days(date(2026, 1, 2), last_day)]
        out = {}
        for k, t in enumerate(tickers):
            out[t] = (days, [100 + k + 0.1 * i for i in range(len(days))])  # every stock rises daily
        return out
    return fn


class FakeLLM:
    calls = 0

    async def __call__(self, system, user):
        self.calls += 1
        return '{"p_up": 0.6}'


async def fake_research(tickers, **kw):
    return [{"ticker": t, "p_up": 0.4, "saved_to": f"results/live/x/{t}.json"} for t in tickers]


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    uni = tmp_path / "universe.csv"
    uni.write_text("rank,ticker\n1,AAA\n2,BBB\n")
    monkeypatch.setattr(F, "BACKEND", tmp_path)
    return {"tag": "t", "universe": "universe.csv", "model": "m", "horizon": 5, "arms": ["live_plain", "live_web"],
            "web_rounds": 1, "lookback": 60, "first_cutoff": "2026-09-11", "config_hash": "h"}


async def test_weekly_cycle_decide_idempotent_resolve_score(cfg, tmp_path):
    led = Ledger(tmp_path / "forward.jsonl")
    kw = {"llm": FakeLLM(), "research_fn": fake_research, "allow_unjailed": not HAS_BWRAP, "log": lambda s: None}
    now = ny(2026, 9, 13, 20)  # Sunday evening after week 1
    w = await F.run_once(cfg, led, now=now, bars_fn=fake_bars_fn(date(2026, 9, 11)), **kw)
    assert [r["type"] for r in w] == ["decision"]
    d = w[0]
    assert d["cutoff"] == "2026-09-11" and d["on_time"] and d["arms"]["live_plain"] == {"AAA": 0.6, "BBB": 0.6}
    assert d["arms"]["live_web"] == {"AAA": 0.4, "BBB": 0.4}
    # running again before the outcome is known changes nothing
    assert await F.run_once(cfg, led, now=now, bars_fn=fake_bars_fn(date(2026, 9, 11)), **kw) == []
    # a week later: week 2 was never decided before its deadline -> missed; week 1 resolves
    later = ny(2026, 9, 21, 12)  # Monday noon, after week 2's deadline
    w = await F.run_once(cfg, led, now=later, bars_fn=fake_bars_fn(date(2026, 9, 18)), **kw)
    assert [(r["type"], r["cutoff"]) for r in w] == [("missed", "2026-09-18"), ("outcome", "2026-09-11")]
    assert w[1]["resolve_date"] == "2026-09-18" and all(w[1]["up"].values())
    board = F.scoreboard(led)
    assert board["weeks_scored"] == 1 and board["missed"] == 1
    assert board["arms"]["live_plain"]["accuracy"] == 1.0 and board["arms"]["live_web"]["accuracy"] == 0.0
    assert board["arms"]["always_up"]["accuracy"] == 1.0 and "too early" in board["note"]
    led.verify()


async def test_decision_after_deadline_is_logged_as_missed_not_made(cfg, tmp_path):
    led = Ledger(tmp_path / "forward.jsonl")
    llm = FakeLLM()
    w = await F.run_once(cfg, led, now=ny(2026, 9, 14, 9, 31), bars_fn=fake_bars_fn(date(2026, 9, 11)), llm=llm,
                         research_fn=fake_research, allow_unjailed=not HAS_BWRAP, log=lambda s: None)
    assert [r["type"] for r in w] == ["missed"] and llm.calls == 0


async def test_dry_run_writes_nothing(cfg, tmp_path):
    led = Ledger(tmp_path / "forward.jsonl")
    w = await F.run_once(cfg, led, now=ny(2026, 9, 13, 20), bars_fn=fake_bars_fn(date(2026, 9, 11)),
                         dry_run=True, log=lambda s: None)
    assert w == [] and not led.path.exists()


async def test_yahoo_bars_retries_transient_failures(monkeypatch):
    from app.tools import netguard

    calls = {"n": 0}
    body = json.dumps({"chart": {"result": [{"timestamp": [1757620800], "indicators": {"adjclose": [{"adjclose": [10.0]}]}}]}})

    class R:
        status = 200
        text = body

    async def flaky_fetch(self, url, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise netguard.FetchError("cannot resolve host: ")
        return R()

    monkeypatch.setattr(netguard.SafeFetcher, "fetch", flaky_fetch)
    bars = await F.yahoo_bars(["SPY"], backoff_s=0.01)
    assert bars["SPY"][1] == [10.0] and calls["n"] == 3
    calls["n"] = -100  # always failing now
    monkeypatch.setattr(netguard.SafeFetcher, "fetch", lambda self, url, **kw: (_ for _ in ()).throw(netguard.FetchError("down")))
    with pytest.raises(netguard.FetchError):
        await F.yahoo_bars(["SPY"], attempts=2, backoff_s=0.01)


async def test_failed_web_research_is_recorded_not_scored_as_half(cfg, tmp_path):
    async def partly_failing(tickers, **kw):
        return [{"ticker": "AAA", "p_up": 0.7}, {"ticker": "BBB", "p_up": None, "error": "JailError: boom"}]

    led = Ledger(tmp_path / "forward.jsonl")
    w = await F.run_once(cfg, led, now=ny(2026, 9, 13, 20), bars_fn=fake_bars_fn(date(2026, 9, 11)), llm=FakeLLM(),
                         research_fn=partly_failing, allow_unjailed=not HAS_BWRAP, log=lambda s: None)
    d = w[0]
    assert d["arms"]["live_web"] == {"AAA": 0.7} and d["failed"] == {"live_web": ["BBB"]}
