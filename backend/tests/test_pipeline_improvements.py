"""Synthetic prospective inputs, source failures, accounting and shadow outcomes."""
from __future__ import annotations

import asyncio
import copy
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ai_contribution as C
import live_research_test as L

from app.portfolio import sleeve
from app.portfolio.horizon_shadow import evaluate
from app.portfolio.measurement import execution
from app.portfolio.sleeve_replay import inputs, replay, state


def test_exact_journal_preserves_missing_prices_and_blocks_changed_state():
    days = pd.to_datetime(["2026-10-01", "2026-10-02"])
    closes = pd.DataFrame({"AAA": [100., 110.], "XLE": [50., 50.]}, index=days)
    opens = closes.copy()
    opens.loc[days[1], "AAA"] = float("nan")
    decision = {"type": "decision", "ticker": "AAA", "accession": "a", "sector": "Energy", "source": "bonsai",
                "on_time": True, "logodds": 5., "entry_deadline": "2026-10-02T13:30:00+00:00"}
    book = sleeve.new_state()
    rows = []
    for n, now in [(1, datetime(2026, 10, 1, 22, tzinfo=UTC)), (2, datetime(2026, 10, 3, 22, tzinfo=UTC))]:
        before = state(book)
        sleeve.step(book, [decision], opens.iloc[:n], closes.iloc[:n], {"Energy": "XLE"}, now)
        rows.append(inputs(before, book, [decision], opens.iloc[:n], closes.iloc[:n], now, "ACTIVE"))
    assert replay(rows, {"Energy": "XLE"}) == state(book)
    assert book["pairs"][0]["status"] == "skipped"
    report = C.prospective(rows, {"Energy": "XLE"}, book, shuffles=0)
    assert report["matches_live_state"] and report["ai_minus_every_release"] == 0
    tampered = copy.deepcopy(rows)
    tampered[1]["opens"]["data"][1][0] = 100
    with pytest.raises(ValueError, match="output does not reproduce"):
        replay(tampered, {"Energy": "XLE"})
    book["equity"] += 1
    with pytest.raises(ValueError, match="current simulator"):
        C.prospective(rows, {"Energy": "XLE"}, book)


def test_unmeasured_legacy_fills_are_not_a_zero_cost_result():
    fills = [{"client_order_id": "old", "filled_qty": 4, "filled_price": 100}]
    report = execution([], {"fees": 0, "borrow": 0, "financing": 0}, fills)
    assert report["cost_status"] == "incomplete"
    assert report["unmeasured_fill_snapshots"] == 1
    assert report["unmeasured_fills"][0]["reason"] == "missing arrival quote"
    assert report["weighted_slippage_bp"] is None


def sessions(n=66):
    days = pd.bdate_range("2026-10-01", periods=n)
    return [{"date": d.date().isoformat(), "open_at": f"{d.date()}T13:30:00+00:00",
             "close_at": f"{d.date()}T20:00:00+00:00"} for d in days]


def plan():
    return {"protocol": "HS1", "decided_at": "2026-10-01T13:30:00+00:00",
            "arms": {"ai": {"day": {"AAA": .04}, "medium": {"AAA": .03}, "long": {"AAA": .03}},
                     "no_ai": {"day": {"AAA": .02}, "medium": {"AAA": .03}, "long": {"AAA": .03}}}}


def test_horizons_enter_strictly_after_decision_and_charge_each_side_once():
    calendar = sessions()
    bars = {"AAA": {s["date"]: {"open": 100., "close": 110.} for s in calendar}}
    bars["AAA"][calendar[22]["date"]]["open"] = 120
    report = evaluate(plan(), calendar, bars, datetime(2027, 3, 1, tzinfo=UTC))
    day = report["horizons"]["day"]
    assert day["entry_at"] == calendar[1]["open_at"]  # equal timestamp cannot fill
    assert day["arms"]["ai"]["net_return"] == pytest.approx(.04 * (1.1 * .999 - 1.001))
    assert day["incremental_net_return"] == pytest.approx(.02 * (1.1 * .999 - 1.001))
    assert day["arms"]["ai"]["turnover"] == pytest.approx(.04 * 2.1)
    assert report["horizons"]["medium"]["exit_at"] == calendar[22]["open_at"]
    assert report["horizons"]["long"]["exit_at"] == calendar[64]["open_at"]
    assert report["winner"] is None and report["orders_submitted"] == 0


def test_horizon_missing_prices_waits_and_half_days_are_excluded():
    calendar = sessions()
    now = datetime(2027, 3, 1, tzinfo=UTC)
    assert evaluate(plan(), calendar, {}, now)["horizons"]["day"]["status"] == "missing_prices"
    assert evaluate(plan(), calendar[:1], {}, now)["horizons"]["day"]["status"] == "pending"
    early = datetime.fromisoformat(calendar[1]["open_at"]) + timedelta(hours=3.5)
    calendar[1]["close_at"] = early.isoformat()
    assert evaluate(plan(), calendar, {}, now)["horizons"]["day"]["status"] == "excluded_half_day"
    bad = plan()
    bad["arms"]["ai"]["long"]["AAA"] = .04
    with pytest.raises(ValueError, match="Company cap"):
        evaluate(bad, calendar, {}, now)


def test_brief_repair_reuses_evidence_and_failed_attempt_keeps_sources(monkeypatch):
    class Fetcher:
        async def aclose(self):
            pass

    class Jail:
        def __init__(self, *_a, **_kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_a):
            pass
        async def call(self, _request):
            return {"evidence": "https://www.sec.gov/a.htm Company revenue 100"}

    class Model:
        num_ctx, num_predict = 8192, 1200
        def __init__(self, fail=False):
            self.calls: list[tuple[str, str]] = []
            self.fail = fail
        async def __call__(self, system, user, **_kw):
            self.calls.append((system, user))
            return "invalid" if self.fail or len(self.calls) == 1 else json.dumps({"facts": [{"text": "Company revenue 100", "source": "S1"}]})

    monkeypatch.setattr(L.FreeLiveGateway, "from_env", lambda *_a, **_kw: L.FreeLiveGateway(mode="live", fetcher=Fetcher()))
    monkeypatch.setattr(L, "AgentJail", Jail)
    model = Model()
    result = asyncio.run(L.research_company("AAA", model))
    assert result["stage"] == "complete" and len(result["brief_attempts"]) == 2
    assert model.calls[0][1] == model.calls[1][1]
    with pytest.raises(L.ResearchFailure) as failure:
        asyncio.run(L.research_company("AAA", Model(fail=True)))
    rec = failure.value.record
    assert rec["evidence_sha256"] and len(rec["llm_calls"]) == 2 and rec["stage"] == "brief"
    assert "bounded brief repair" in rec["error_reason"]


def test_paper_mirror_gives_entry_and_exit_a_completed_close_reference(tmp_path, monkeypatch):
    from test_recovery import Rig, at
    rig = Rig(tmp_path)
    references = []
    original = rig.client.submit

    def submit(leg):
        references.append((leg.side, leg.asset, leg.ref_price))
        assert leg.ref_price > 0
        return original(leg)

    monkeypatch.setattr(rig.client, "submit", submit)
    rig.run(2, at(1, 23, 30))
    rig.market_open(2)
    rig.run(7, at(6, 23, 30))
    assert {side for side, _, _ in references} == {"buy", "sell"}
    assert len(references) == 4


def test_cost_review_digest_changes_when_legacy_fill_changes(tmp_path):
    from app.forward.ledger import Ledger
    from app.portfolio.measurement import cost_scope
    Ledger(tmp_path / "results/forward/execution/ledger.jsonl").append("risk", allowed=False)
    p = tmp_path / "results/forward/ai_picks/book.json"
    p.parent.mkdir()
    p.write_text(json.dumps({"pairs": [{"legs": [{"client_order_id": "old", "status": "filled", "qty": 1, "filled_price": 100}]}]}))
    before = cost_scope(tmp_path)
    p.write_text(json.dumps({"pairs": [{"legs": [{"client_order_id": "old", "status": "filled", "qty": 2, "filled_price": 100}]}]}))
    assert cost_scope(tmp_path) != before


def test_horizon_refresh_uses_only_free_read_only_prices_and_calendar(tmp_path):
    from horizon_review import refresh

    from app.portfolio.broker import Alpaca
    calendar = sessions(2)
    requests = []

    def handle(request):
        requests.append(request)
        assert request.method == "GET"
        if request.url.path == "/v2/calendar":
            return httpx.Response(200, json=[{"date": s["date"], "open": "09:30", "close": "16:00"} for s in calendar])
        assert request.url.path == "/v2/stocks/bars"
        assert request.url.params["feed"] == "iex"
        return httpx.Response(200, json={"bars": {"AAA": [{"t": "2026-10-02T04:00:00Z", "o": 100, "c": 110}]}})

    client = Alpaca("fake", "fake", transport=httpx.MockTransport(handle))
    try:
        result = refresh(client, [{"directory": str(tmp_path / "cohort"), "horizon_plan": plan()}], datetime(2026, 10, 4, tzinfo=UTC))
    finally:
        client.c.close()
    assert len(requests) == 2 and result["vintages"][0]["outcomes"]["orders_submitted"] == 0
    assert result["vintages"][0]["outcomes"]["horizons"]["day"]["status"] == "evaluated_price_proxy"


def test_source_number_matches_do_not_become_human_approval(tmp_path):
    import gzip

    from app.forward.benchmark_review import case_material
    p = tmp_path / "data/events/text/0001.txt.gz"
    p.parent.mkdir(parents=True)
    with gzip.open(p, "wt") as f:
        f.write("Annual sales were 1,000. This is not the quarterly figure.")
    material = case_material(tmp_path, {"accession": "0001", "revenue": {"q": 1000}})
    assert material["label_excerpts"][0]["occurrences"] == 1
    assert "not the quarterly" in material["label_excerpts"][0]["snippets"][0]
    assert "approved" not in material


def test_local_gate_blocks_retry_same_ids_without_using_exit_allowance(tmp_path, monkeypatch):
    from test_recovery import Rig, at
    rig = Rig(tmp_path)
    submit = rig.client.submit

    def block(leg):
        leg.status, leg.note = "rejected", "ACCOUNT RISK: stale quote"

    monkeypatch.setattr(rig.client, "submit", block)
    for _ in range(8):
        rig.run(2, at(1, 23, 30))
    assert len(rig.pair()["legs"]) == 2 and not rig.x.posts
    entry_ids = {x["client_order_id"] for x in rig.pair()["legs"]}
    monkeypatch.setattr(rig.client, "submit", submit)
    rig.run(2, at(1, 23, 30))
    assert set(rig.x.posts) == entry_ids
    rig.market_open(2)
    monkeypatch.setattr(rig.client, "submit", block)
    for _ in range(8):
        rig.run(7, at(6, 23, 30))
    assert len(rig.pair()["legs"]) == 4
    assert not any("gave up" in flag for flag in rig.pair()["audit_flags"])
    monkeypatch.setattr(rig.client, "submit", submit)
    rig.run(7, at(6, 23, 30))
    assert len(rig.x.posts) == 4 and len(set(rig.x.posts)) == 4
