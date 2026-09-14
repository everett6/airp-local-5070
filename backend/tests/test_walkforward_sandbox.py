from datetime import UTC, date, datetime, timedelta

import pytest

from app.data_ingestion.base_connector import InMemoryCache
from app.data_ingestion.market_data import MockMarketDataConnector
from app.sandbox import agent_worker as aw
from app.sandbox.clock import LookaheadViolation, sandbox_scope
from app.sandbox.jail import AgentJail, build_command
from app.sandbox.pit_data import PointInTimeView, PriceTable
from app.sandbox.walkforward import ArmState, resolved_as_of


def _table(n: int = 200) -> PriceTable:
    start = date(2025, 1, 1)
    dates = [start + timedelta(days=i) for i in range(n)]
    return PriceTable(dates, {"XYZ": [100.0 + i for i in range(n)], "SPY": [50.0 + i / 2 for i in range(n)]})


def _dt(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def test_view_contains_no_rows_after_cutoff():
    t = _table()
    cutoff = t.dates[120]
    v = t.view(cutoff)
    assert v.dates[-1] == cutoff
    assert len(v.closes["XYZ"]) == 121
    assert max(v.closes["XYZ"]) == t.closes["XYZ"][120]


def test_view_refuses_to_be_built_with_future_rows():
    t = _table()
    with pytest.raises(LookaheadViolation):
        PointInTimeView(as_of=t.dates[10], dates=tuple(t.dates[:20]), closes={})


def test_outcome_lookup_blocked_inside_sandbox_but_allowed_after():
    t = _table()
    cutoff = t.dates[100]
    with sandbox_scope(as_of=_dt(cutoff), run_id="t"), pytest.raises(LookaheadViolation):
        t.outcome("XYZ", cutoff, 5)
    resolve, ret = t.outcome("XYZ", cutoff, 5)
    assert resolve == t.dates[105] and ret > 0


def test_anonymized_series_has_no_ticker_or_dates_and_is_rebased():
    anon = _table().view(date(2025, 6, 1)).anonymize("XYZ", "SPY", 60)
    assert set(anon) == {"asset", "market"}
    assert anon["asset"][0] == 100.0 and len(anon["asset"]) == 60


def test_memory_only_includes_resolved_records():
    arm = ArmState("x")
    c = date(2025, 3, 1)
    arm.preds = [{"resolve_date": c - timedelta(days=1)}, {"resolve_date": c + timedelta(days=1)}]
    with sandbox_scope(as_of=_dt(c), run_id="t"):
        assert len(resolved_as_of(arm, c)) == 1


def test_memory_leak_bug_would_abort_not_leak():
    # Simulate a buggy caller passing a later cutoff than the active clock.
    arm = ArmState("x")
    c = date(2025, 3, 1)
    arm.preds = [{"resolve_date": c + timedelta(days=3)}]
    with sandbox_scope(as_of=_dt(c), run_id="t"), pytest.raises(LookaheadViolation):
        resolved_as_of(arm, c + timedelta(days=10))


async def test_connector_cache_cannot_bypass_sandbox():
    """Regression: a record cached outside the sandbox under the same key used
    to be served inside it without any point-in-time check."""
    cache = InMemoryCache()
    conn = MockMarketDataConnector(cache=cache)
    future = datetime(2026, 1, 1, tzinfo=UTC)
    await conn.fetch(cache_key="k", ticker="ACME", as_of=future)  # live, allowed
    with sandbox_scope(as_of=datetime(2025, 1, 1, tzinfo=UTC), run_id="t"), pytest.raises(LookaheadViolation):
        await conn.fetch(cache_key="k", ticker="ACME", as_of=future)


def test_stacker_learns_a_simple_signal():
    rows = [[1.0, 0, 0, 0, 0, 0] if i % 2 else [-1.0, 0, 0, 0, 0, 0] for i in range(200)]
    ys = [i % 2 for i in range(200)]
    w = aw.fit_logistic(rows, ys)
    assert aw.predict_logistic(w, rows[1]) > 0.7
    assert aw.predict_logistic(w, rows[0]) < 0.3


def test_parse_p_is_robust():
    assert aw.parse_p('{"p_up": 0.62, "reason": "x"}') == 0.62
    assert aw.parse_p("garbage") == 0.5
    assert aw.parse_p('{"p_up": 7}') == 0.99


needs_bwrap = pytest.mark.skipif(
    not build_command(allow_unjailed=True)[0].endswith("bwrap"), reason="bubblewrap not installed"
)


@needs_bwrap
async def test_jailed_agent_cannot_read_data_or_reach_network(tmp_path):
    secret = tmp_path / "prices.csv"
    secret.write_text("future,data\n")

    async def llm(system: str, user: str) -> str:
        return '{"p_up": 0.5}'

    async with AgentJail(llm) as jail:
        probe = await jail.probe([str(secret)])
    assert probe["passed"], probe


@needs_bwrap
async def test_jailed_agent_round_trip_prediction():
    seen: list[str] = []

    async def llm(system: str, user: str) -> str:
        seen.append(user)
        return '{"p_up": 0.7}'

    t = _table()
    anon = t.view(t.dates[150]).anonymize("XYZ", "SPY", 120)
    async with AgentJail(llm) as jail:
        r = await jail.call({"task": "predict", "items": [{"id": "a0", **anon}]})
    assert r["predictions"][0]["p_final"] == 0.7
    assert "XYZ" not in seen[0] and "2025" not in seen[0]


def test_guarded_stacker_rejects_pure_noise():
    import random

    rng = random.Random(0)
    feats = {"ret_5d": 0.0, "ret_20d": 0.0, "dist_ma50": 0.0, "mkt_ret_5d": 0.0, "rsi_14": 50.0}
    resolved = []
    for _ in range(400):
        f = {k: (rng.gauss(0, 0.05) if k != "rsi_14" else rng.uniform(20, 80)) for k in feats}
        resolved.append({"p_llm": 0.5 + rng.uniform(-0.05, 0.05), "features": f, "up": rng.random() < 0.5})
    w, info = aw.guarded_stacker(resolved)
    assert info["stacker"] == "rejected" and w is None


def test_meta_arms_only_use_resolved_outcomes():
    from app.sandbox.walkforward import point_in_time_meta_arms

    c1, c2 = date(2025, 1, 6), date(2025, 1, 13)
    # an outcome at c1 that resolves AFTER c2 must not influence c2's base rate
    preds = [{"cutoff": c1, "ticker": f"T{i}", "resolve_date": date(2025, 2, 1), "up": True, "p": 0.9}
             for i in range(150)]
    preds += [{"cutoff": c2, "ticker": f"T{i}", "resolve_date": date(2025, 2, 8), "up": False, "p": 0.9}
              for i in range(150)]
    base, _sel, log = point_in_time_meta_arms({"llm_plain": preds}, [c1, c2])
    assert all(p["p"] == 0.5 for p in base)  # nothing resolved yet at either cutoff
    assert all(entry["chosen"] == "base_rate" for entry in log)


def test_features_survive_short_histories():
    f = aw.features([100.0, 101.0, 99.0], [100.0, 100.5, 101.0])
    assert f["ret_60d"] == pytest.approx(99 / 100 - 1) and f["vol_20d_ann"] >= 0
    assert aw.features([100.0], [100.0])["ret_1d"] == 0.0
    long = [100.0 + i for i in range(120)]
    assert aw.features(long, long)["ret_60d"] == pytest.approx(long[-1] / long[-61] - 1)
