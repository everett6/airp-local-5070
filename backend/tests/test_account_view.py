"""The account-wide view (scripts/account_view.py): two books, one account. Made-up broker answers, no network."""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import account_view as V

from app.portfolio.broker import PAPER, Alpaca

NOW = datetime(2026, 10, 6, 22, 30, tzinfo=UTC)
ACCT = {"equity": "100000", "cash": "20000", "buying_power": "150000", "regt_buying_power": "150000",
        "maintenance_margin": "30000"}


def pos(sym: str, qty: float, px: float) -> dict:
    return {"symbol": sym, "qty": str(qty), "market_value": str(qty * px)}


def leg(asset: str, side: str, qty: float, status: str = "filled", got: float | None = None, cid: str = "") -> dict:
    return {"asset": asset, "side": side, "qty": qty, "status": status, "filled_qty": got,
            "client_order_id": cid or f"airp-x-{asset}"}


ORDERS = {"2026-10-05T22:00:00+00:00": {"book": "aggressive", "targets": {"SPY": 2.0, "BTC-USD": 0.5},
                                        "broker_scale": 0.65,
                                        "legs": [leg("SPY", "buy", 170), leg("BTC-USD", "buy", 0.3, got=0.299)]}}
PICKS = {"pairs": [
    {"ticker": "MU", "etf": "XLK", "qty": 1, "etf_qty": 5, "status": "open", "accession": "1",
     "legs": [leg("MU", "buy", 1, cid="airp-pk-1-in-s"), leg("XLK", "sell", 5, cid="airp-pk-1-in-e")]},
    {"ticker": "ACN", "etf": "XLK", "qty": 10, "etf_qty": 9, "status": "open", "accession": "2",
     "legs": [leg("ACN", "buy", 10, "canceled", got=4.0, cid="airp-pk-2-in-s"),
              leg("XLK", "sell", 9, cid="airp-pk-2-in-e")]},
    {"ticker": "JBL", "etf": "XLK", "qty": 6, "etf_qty": 9, "status": "open", "accession": "3",
     "legs": [leg("JBL", "buy", 6, "expired", cid="airp-pk-3-in-s"),
              leg("XLK", "sell", 9, "expired", cid="airp-pk-3-in-e")]}]}
POSITIONS = [pos("SPY", 170, 600), pos("BTCUSD", 0.299, 100_000), pos("MU", 1, 1000), pos("ACN", 4, 220),
             pos("XLK", -14, 200)]


def test_exposure_and_both_books_add_up_to_the_account() -> None:
    v, alerts = V.build(ACCT, POSITIONS, [], ORDERS, PICKS, NOW)
    assert alerts == [] and all(r["unexplained_qty"] == 0 for r in v["positions"])
    assert v["long"] == 170 * 600 + 29_900 + 1000 + 880 and v["short"] == 2800
    assert v["gross"] == v["long"] + v["short"] and v["net"] == v["long"] - v["short"]
    assert v["leverage"] == round(v["gross"] / 100_000, 3) and v["buying_power"] == 150_000
    s = v["sleeve"]
    assert (s["long"], s["short"], s["open_pairs"]) == (1880.0, 2800.0, 3) and s["gross_share"] == 0.0468
    assert v["shared_hedges"] == {"XLK": {"pairs": ["ACN", "MU"], "qty": -14.0}}  # one short behind two pairs
    assert v["mirror"]["intended_weights"] == {"SPY": 1.17, "BTC-USD": 0.2925}
    assert v["mirror"]["broker_weights"]["SPY"] == 1.02  # what the broker holds against what the mirror wanted
    by = {r["asset"]: r for r in v["positions"]}
    assert (by["XLK"]["sleeve_qty"], by["XLK"]["mirror_qty"], by["ACN"]["sleeve_qty"]) == (-14.0, 0.0, 4.0)


def test_a_position_no_book_explains_is_an_alert_and_a_working_order_is_not() -> None:
    extra = [*POSITIONS[:-1], pos("XLK", -23, 200), pos("TSLA", 3, 400)]     # JBL's hedge filled after all; a stray
    _, alerts = V.build(ACCT, extra, [], ORDERS, PICKS, NOW)
    assert len(alerts) == 2 and any("XLK broker holds -23" in a and "difference -9" in a for a in alerts)
    assert any("TSLA broker holds 3" in a for a in alerts)
    _, alerts = V.build(ACCT, extra, [{"symbol": "TSLA"}, {"symbol": "XLK"}], ORDERS, PICKS, NOW)
    assert alerts == []                                                       # orders still working: wait
    gone = [p for p in POSITIONS if p["symbol"] != "MU"]                      # the book says held, the broker not
    _, alerts = V.build(ACCT, gone, [], ORDERS, PICKS, NOW)
    assert len(alerts) == 1 and "MU broker holds 0, the books' orders add up to 1" in alerts[0]
    dust = [*POSITIONS[:1], pos("BTCUSD", 0.29895, 100_000), *POSITIONS[2:]]  # 5 dollars of rounding: no alert
    assert V.build(ACCT, dust, [], ORDERS, PICKS, NOW)[1] == []
    _, alerts = V.build(ACCT | {"buying_power": "-5"}, POSITIONS, [], ORDERS, PICKS, NOW)
    assert any("buying power is negative" in a for a in alerts)
    big = {"pairs": [{**PICKS["pairs"][0], "qty": 30, "legs": [leg("MU", "buy", 30, cid="airp-pk-1-in-s")]}]}
    _, alerts = V.build(ACCT, [pos("MU", 30, 1000)], [], {}, big, NOW)
    assert any("AI sleeve's gross is 30.0%" in a for a in alerts)


def test_run_only_reads_the_account_and_writes_its_view(tmp_path: Path) -> None:
    seen: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        body = {"/v2/account": ACCT, "/v2/positions": POSITIONS, "/v2/orders": []}[req.url.path]
        return httpx.Response(200, json=body)
    (tmp_path / "broker").mkdir()
    (tmp_path / "picks").mkdir()
    (tmp_path / "broker" / "orders.json").write_text(json.dumps(ORDERS))
    (tmp_path / "picks" / "book.json").write_text(json.dumps(PICKS))
    client = Alpaca("k", "s", PAPER, transport=httpx.MockTransport(handler))
    assert V.run(client, tmp_path / "account", NOW, tmp_path / "broker", tmp_path / "picks") == []
    assert {m for m, _ in seen} == {"GET"} and len(seen) == 3  # nothing is sent, changed or cancelled
    v = json.loads((tmp_path / "account" / "view.json").read_text())
    assert v["equity"] == 100_000 and len(v["positions"]) == 5
    line = json.loads((tmp_path / "account" / "history.jsonl").read_text())
    assert line["leverage"] == v["leverage"] and line["unexplained"] == 0
    assert V.read_json(tmp_path / "nothing.json") == {}
