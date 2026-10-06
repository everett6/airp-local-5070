"""Recovery rehearsals for the AI-picks broker mirror (docs/PLAN_60_V2.md, "Outside review, second part").

Each test runs scripts/ai_picks.run through a pair's life against a stand-in for the Alpaca paper API (the real
`Alpaca` client talks to it over a mock transport, so submit / refresh / duplicate handling are the shipped code),
breaks something on the way, and then checks the three things recovery has to leave behind:
  - no order was sent twice;
  - the account holds nothing the book cannot explain, and nothing at all once the pair is done;
  - the simulator's record of the pair (the scored one) is what a clean run gives.
"""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ai_picks

from app.forward.ledger import Ledger
from app.portfolio.broker import PAPER, Alpaca

DAYS = pd.bdate_range("2026-10-01", periods=14)  # Thu 1 Oct ...; entry open Mon 5 Oct (index 2), exit Mon 12 (index 7)
STOCK = [100.0] * 7 + [110.0] * 7
ETF = [50.0] * 7 + [51.0] * 7
ACC = "0000000001-26-000001"
IN_S, IN_E = f"airp-pk-{ACC.replace('-', '')}-in-s", f"airp-pk-{ACC.replace('-', '')}-in-e"
OUT_S, OUT_E = IN_S.replace("-in-", "-out-"), IN_E.replace("-in-", "-out-")


def at(day: int, hour: int, minute: int = 45) -> datetime:
    """A run time on DAYS[day], in UTC: 12:45 is the 08:45 New York run, 22:30 the evening one."""
    d = DAYS[day]
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=UTC)


class Exchange:
    """The paper account: orders by client id, fills at the open, positions. `plan` says what happens to an order
    when the market opens: "fill" (default), ("part", qty), "reject", "expire"."""

    def __init__(self) -> None:
        self.orders: dict[str, dict] = {}
        self.posts: list[str] = []          # every accepted POST, by client id
        self.refused: list[str] = []        # POSTs refused as duplicates
        self.plan: dict[str, object] = {}
        self.pos: dict[str, float] = {}
        self.fail_next_post = ""            # "before": the request never arrives; "after": it arrives, the reply is lost

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if req.method == "POST" and path == "/v2/orders":
            body = json.loads(req.content)
            mode, self.fail_next_post = self.fail_next_post, ""
            if mode == "before":
                raise httpx.ConnectError("connection reset", request=req)
            cid = body["client_order_id"]
            if cid in self.orders:
                self.refused.append(cid)
                return httpx.Response(422, json={"message": "client_order_id must be unique"})
            self.orders[cid] = {"id": f"o{len(self.orders)}", "client_order_id": cid, "symbol": body["symbol"],
                                "side": body["side"], "qty": body["qty"], "status": "accepted", "filled_qty": "0",
                                "filled_avg_price": None, "filled_at": None}
            self.posts.append(cid)
            if mode == "after":
                raise httpx.ReadTimeout("timed out", request=req)
            return httpx.Response(200, json=self.orders[cid])
        if req.method == "GET" and path == "/v2/orders:by_client_order_id":
            cid = req.url.params["client_order_id"]
            return httpx.Response(200, json=self.orders[cid]) if cid in self.orders else httpx.Response(404, text="none")
        return httpx.Response(404, text=path)

    def open(self, prices: dict[str, float]) -> None:
        """The market opens: every working order meets its fate."""
        for cid, o in self.orders.items():
            if o["status"] != "accepted":
                continue
            what = self.plan.get(cid, "fill")
            qty = float(o["qty"])
            got = qty if what == "fill" else float(what[1]) if isinstance(what, tuple) else 0.0
            o["status"] = {"fill": "filled", "reject": "rejected", "expire": "expired"}.get(str(what), "canceled")
            if got:
                o.update(filled_qty=str(got), filled_avg_price=str(prices[o["symbol"]]), filled_at="t")
                self.pos[o["symbol"]] = self.pos.get(o["symbol"], 0.0) + (got if o["side"] == "buy" else -got)

    def holdings(self) -> dict[str, float]:
        return {a: q for a, q in self.pos.items() if abs(q) > 1e-9}


class Rig:
    def __init__(self, tmp: Path) -> None:
        self.events, self.out, self.halt = tmp / "events", tmp / "ai_picks", tmp / "HALT"
        self.x = Exchange()
        self.client = Alpaca("k", "s", PAPER, transport=httpx.MockTransport(self.x.handler), risk_policy=None)
        self.stock, self.etf = list(STOCK), list(ETF)
        Ledger(self.events / "ledger.jsonl").append(
            "decision", accession=ACC, ticker="AAA", sector="Industrials", logodds=3.5, source="bonsai",
            entry_deadline="2026-10-05T09:30:00-04:00", on_time=True)

    def bars(self, n: int) -> None:
        df = pd.DataFrame({"AAA": self.stock, "XLI": self.etf, "SPY": [600.0] * len(DAYS)}, index=DAYS).iloc[:n]
        long = df.rename_axis("Date").reset_index().melt("Date", var_name="Ticker", value_name="Open").dropna()
        long["Close"] = long["Open"]
        long["Date"] = long["Date"].dt.date.astype(str)
        long.to_parquet(self.events / "prices.parquet")

    def run(self, bars: int, now: datetime) -> list[str]:
        self.bars(bars)
        return ai_picks.run(self.events, self.out, now, False, self.client, self.halt)

    def pair(self) -> dict:
        return json.loads((self.out / "book.json").read_text())["pairs"][0]

    def market_open(self, day: int) -> None:
        self.x.open({"AAA": self.stock[day], "XLI": self.etf[day]})

    def life(self, skip: tuple[str, ...] = ()) -> list[str]:
        """The scheduled runs of one pair's life, two a day; `skip` leaves named runs out (a PC that was off)."""
        alerts: list[str] = []
        steps = [("entry-am", 2, 2, 12), ("entry-pm", 3, 2, 22), ("mid-am", 5, 4, 12), ("exit-am", 7, 7, 12),
                 ("exit-pm", 8, 7, 22), ("next-am", 8, 8, 12), ("next-pm", 9, 8, 22), ("later-am", 9, 9, 12),
                 ("later-pm", 10, 9, 22)]
        for name, bars, day, hour in steps:
            if hour == 22:
                self.market_open(day)  # the day's open happened between the morning and the evening run
            if name not in skip:
                alerts += self.run(bars, at(day, hour, 45 if hour == 12 else 30))
        return alerts


def check_clean(r: Rig, closed: bool = True) -> dict:
    """What every recovery must leave behind."""
    p = r.pair()
    assert len(r.x.posts) == len(set(r.x.posts)), "an order was sent twice"
    assert r.x.holdings() == {}, f"positions left at the broker: {r.x.holdings()}"
    assert p["broker_audit"]["held"] == {}
    if closed:  # the scored record is the simulator's, and it is the clean run's
        assert p["status"] == "closed" and p["pnl"] == CLEAN_PNL and p["exit_day"] == "2026-10-12"
    return p


CLEAN_PNL = round(20 * (110.0 * 0.998 - 100.0 * 1.002) - 40 * (51.0 * 1.002 - 50.0 * 0.998), 2)


def test_a_clean_life_is_the_reference(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    assert r.life() == []
    p = check_clean(r)
    assert r.x.posts == [IN_S, IN_E, OUT_S, OUT_E] and p["audit_flags"] == [] and "late_exit" not in p
    assert p["broker_audit"]["fill_slippage"] == 0.0 and p["broker_audit"]["late"] is False


@pytest.mark.parametrize("when", ["before", "after"])
def test_a_lost_connection_around_submission_never_duplicates(tmp_path: Path, when: str) -> None:
    r = Rig(tmp_path)
    r.x.fail_next_post = when  # the first entry order: the request is lost, or its reply is
    alerts = r.run(2, at(2, 12))
    assert len(alerts) == 1 and "submission uncertain" in alerts[0]
    alerts = r.run(2, at(2, 13, 0))  # the retry inside the same order window
    if when == "before":  # it never arrived: looking it up fails, and nothing is re-sent blind
        assert any("refresh uncertain" in a for a in alerts) and r.x.posts == [IN_E]
    else:
        assert alerts == [] and r.x.posts == [IN_S, IN_E]
    assert len(r.x.posts) == len(set(r.x.posts))


def test_a_crash_after_submission_before_the_book_was_saved(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.run(2, at(2, 12, 30))                       # an earlier run: the pair is planned, orders go out
    saved = (r.out / "book.json").read_text()
    book = json.loads(saved)
    book["pairs"][0]["legs"] = []                 # ...but the process died before the book was written
    (r.out / "book.json").write_text(json.dumps(book))
    assert r.run(2, at(2, 12)) == []              # the next run sends the same ids: refused, looked up, adopted
    assert r.x.posts == [IN_S, IN_E] and sorted(r.x.refused) == sorted([IN_S, IN_E])
    assert [d["status"] for d in r.pair()["legs"]] == ["submitted", "submitted"]
    # the same for the exit orders
    r = Rig(tmp_path / "b")
    r.life(skip=("exit-pm", "next-am", "next-pm", "later-am", "later-pm"))
    book = json.loads((r.out / "book.json").read_text())
    book["pairs"][0]["legs"] = [d for d in book["pairs"][0]["legs"] if "-in-" in d["client_order_id"]]
    (r.out / "book.json").write_text(json.dumps(book))
    assert r.run(7, at(7, 13, 0)) == []
    assert sorted(r.x.refused) == sorted([OUT_S, OUT_E])
    r.market_open(7)
    assert r.run(8, at(7, 22, 30)) == []
    check_clean(r)


def test_a_part_filled_entry_is_closed_for_what_it_filled(tmp_path: Path) -> None:
    """The review's case: 20 shares ordered, 4 filled, the rest cancelled. Before the fix the 4 shares were never
    sold and the whole hedge was bought back."""
    r = Rig(tmp_path)
    r.x.plan[IN_S] = ("part", 4)
    alerts = r.life()
    p = check_clean(r)
    assert any("canceled after filling 4 of 20" in a for a in alerts)
    assert any("in hedge part-filled: AAA 4 of 20" in a for a in alerts)
    sold = r.x.orders[OUT_S]
    assert float(sold["qty"]) == 4 and float(r.x.orders[OUT_E]["qty"]) == 40
    assert "fill_slippage" not in p["broker_audit"]  # the broker did not trade the simulator's shares
    assert p["broker_audit"]["broker_gross_pnl"] == round(4 * (110.0 - 100.0) - 40 * (51.0 - 50.0), 2)


def test_a_rejected_hedge_leg_leaves_one_leg_that_is_still_closed(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.x.plan[IN_E] = "reject"
    alerts = r.life()
    check_clean(r)
    assert any("in hedge rejected/canceled: XLI" in a for a in alerts)
    assert r.x.posts == [IN_S, IN_E, OUT_S]  # no exit for a leg that was never held


def test_an_exit_that_ends_short_is_followed_by_an_order_for_the_rest(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.x.plan[OUT_S] = ("part", 15)
    r.x.plan[OUT_E] = "expire"
    alerts = r.life()
    p = check_clean(r)
    assert r.x.posts == [IN_S, IN_E, OUT_S, OUT_E, OUT_S + "-r2", OUT_E + "-r2"]
    assert float(r.x.orders[OUT_S + "-r2"]["qty"]) == 5 and float(r.x.orders[OUT_E + "-r2"]["qty"]) == 40
    assert any("late exit" in a for a in alerts) and p["late_exit"] and p["broker_audit"]["late"] is True
    assert p["audit_flags"] == ["late exit"]  # the short exit's flags cleared once the rest was closed


def test_missed_exit_runs_do_not_strand_the_pair(tmp_path: Path) -> None:
    """The review's second case: the PC was off on the exit morning. The simulator closes the pair from the price
    file at the next run; before the fix nothing was ever sent to the broker after that."""
    r = Rig(tmp_path)
    alerts = r.life(skip=("exit-am",))
    p = check_clean(r)
    assert r.x.posts == [IN_S, IN_E, OUT_S, OUT_E]
    assert sum("late exit" in a for a in alerts) == 1 and p["broker_audit"]["late"] is True
    assert any("out hedge" in a for a in alerts)  # said once, at the run that found the pair closed but still held


def test_a_missed_entry_run_sends_nothing_and_says_so(tmp_path: Path) -> None:
    r2 = Rig(tmp_path)
    r2.run(2, datetime(2026, 10, 2, 22, 30, tzinfo=UTC))  # Fri 18:30 New York: planned, window closed, nothing sent
    assert r2.x.posts == []
    r2.market_open(2)
    alerts = r2.run(3, at(2, 22, 30))                      # the Monday morning run never happened
    assert any("orders were never sent" in a for a in alerts) and r2.x.posts == []
    rest = [r2.run(8, at(7, 12)), r2.run(9, at(8, 22, 30))]
    assert r2.x.posts == [] and r2.x.holdings() == {} and rest[1] == []
    assert r2.pair()["status"] == "closed" and r2.pair()["pnl"] == CLEAN_PNL  # the scored record is unaffected


def test_no_open_price_on_the_entry_day_closes_what_the_broker_bought(tmp_path: Path) -> None:
    """The simulator skips a pair with no open price on its entry day. The orders had already filled."""
    r = Rig(tmp_path)
    r.stock[2] = float("nan")
    r.run(2, at(2, 12))
    r.x.open({"AAA": 100.0, "XLI": 50.0})
    alerts = r.run(3, at(2, 22, 30))
    p = r.pair()
    assert p["status"] == "skipped" and any("simulator skipped the pair" in a for a in alerts)
    assert r.x.posts == [IN_S, IN_E]                 # 18:30 New York is outside the order window
    alerts = r.run(3, at(3, 12))                     # the next morning: closed at that day's open
    assert r.x.posts == [IN_S, IN_E, OUT_S, OUT_E] and any("late exit" in a for a in alerts)
    r.market_open(3)
    assert r.run(4, at(3, 22, 30)) == []
    p = check_clean(r, closed=False)
    assert p["status"] == "skipped" and "pnl" not in p and p["late_exit"]


def test_a_missing_exit_price_delays_the_score_not_the_exit(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.stock[7] = float("nan")
    r.life(skip=("next-am", "next-pm", "later-am", "later-pm"))
    p = r.pair()
    assert p["status"] == "open" and r.x.holdings() == {} and r.x.posts == [IN_S, IN_E, OUT_S, OUT_E]
    r.stock[7] = 110.0  # the price arrives
    assert r.run(9, at(8, 12)) == []
    check_clean(r)


def test_the_kill_switch_holds_the_exit_and_recovery_sends_it(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.life(skip=("exit-am", "exit-pm", "next-am", "next-pm", "later-am", "later-pm"))
    r.halt.write_text("HALTED test\n")
    assert r.run(7, at(7, 12)) == [] and r.x.posts == [IN_S, IN_E]
    r.halt.unlink()
    r.market_open(7)
    alerts = r.run(8, at(7, 22, 30)) + r.run(8, at(8, 12))  # the evening is outside the order window; next morning
    assert r.x.posts == [IN_S, IN_E, OUT_S, OUT_E] and any("late exit" in a for a in alerts)
    r.market_open(8)
    r.run(9, at(8, 22, 30))
    check_clean(r)


def test_an_exit_that_keeps_failing_stops_after_five_orders_and_says_so(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    for n in ("", "-r2", "-r3", "-r4", "-r5"):
        r.x.plan[OUT_S + n] = "expire"
    r.life()
    alerts: list[str] = []
    for d in (10, 11, 12, 13):
        alerts += r.run(d, at(d, 12))
        r.x.open({"AAA": 110.0, "XLI": 51.0})
    alerts += r.run(13, at(13, 22, 30))
    assert [c for c in r.x.posts if "-out-s" in c] == [OUT_S + n for n in ("", "-r2", "-r3", "-r4", "-r5")]
    assert any("exit gave up after 5 orders, still held: AAA" in a for a in alerts)
    assert r.x.holdings() == {"AAA": 20.0} and r.pair()["broker_audit"]["held"] == {"AAA": 20}
