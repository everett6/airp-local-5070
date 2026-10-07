import numpy as np
import pytest

from app.portfolio import fund_stability as fs


def test_beta_recovers_a_known_slope_and_defaults_to_one():
    rng = np.random.default_rng(0)
    i = rng.normal(0, 0.01, 130)
    s = 1.5 * i + rng.normal(0, 0.002, 130)
    assert fs.beta(list(100 * np.exp(np.cumsum(s))), list(100 * np.exp(np.cumsum(i)))) == pytest.approx(1.5, abs=0.1)
    assert fs.beta([1.0, 2.0], [1.0, 2.0]) == 1.0


def test_hedge_offsets_beta_and_fits_gross_and_leg_cap():
    w = fs.hedge({"A": 0.8, "B": 0.4, "C": -0.2}, {"A": 1.5, "B": 1.0}, gross=2.0)
    stocks = {k: v for k, v in w.items() if k not in fs.BASKET}
    net_beta = stocks["A"] * 1.5 + stocks["B"] * 1.0 + stocks["C"] * 1.0
    assert sum(w[e] for e in fs.BASKET) == pytest.approx(-net_beta)
    assert sum(abs(v) for v in w.values()) <= 2.0 + 1e-9
    assert all(abs(w[e]) <= fs.LEG_CAP + 1e-9 for e in fs.BASKET)
    big = fs.hedge({"A": 1.0}, {"A": 2.0}, gross=4.0)  # a 2.0 hedge would break the per-leg cap: all scaled
    assert abs(big["QQQ"]) == pytest.approx(fs.LEG_CAP) and big["A"] == pytest.approx(0.48)


def test_kelly_waits_for_100_lots_then_sizes_by_edge_and_switches_off_losers():
    lot = lambda h, r: {"horizon": h, "hedged_return": r}
    lots = [lot("short", 0.02 if i % 2 else -0.01) for i in range(120)] + [lot("day", -0.001)] * 150
    lots += [lot("medium", 0.01)] * 50
    k = fs.kelly(lots, {"day": 0.05, "short": 0.04, "medium": 0.04})
    mean, var = 0.005, float(np.var([0.02, -0.01] * 60, ddof=1))
    assert k["short"]["mult"] == pytest.approx(min(2.0, 0.5 * mean / var / 0.04))
    assert k["day"]["mult"] == 0.0 and k["medium"] == {"mult": 1.0, "n": 50, "mean": 0.01}


def test_hedged_return_and_band():
    lot = {"side": -1, "beta": 2.0, "entry_price": 100, "exit_price": 90, "entry_qqq": 400, "exit_qqq": 380}
    assert fs.hedged_return(lot) == pytest.approx(-(-0.10 - 2.0 * -0.05))  # the short only matched its beta: 0
    assert fs.hedged_return({**lot, "exit_qqq": None}) is None
    o = lambda s, f, t: {"symbol": s, "from": f, "to": t}
    keep = fs.banded([o("A", 10, 11), o("B", 0, 1), o("C", 5, 0), o("D", 100, 130), o("E", 5, -5)],
                     {"A": 250.0, "B": 1.0, "C": 1.0, "D": 250.0, "E": 1.0}, equity=100_000)
    assert [x["symbol"] for x in keep] == ["B", "C", "D", "E"]


def test_combined_fund():
    c = fs.combined({"timestamp": [1, 2, 3, 4], "equity": [100, 110, 99, 120]},
                    {"timestamp": [2, 3, 4], "equity": [50, 45, 60]})
    assert c["total"] == [160, 144, 180] and c["drawdown"] == pytest.approx(144 / 160 - 1)
    assert c["correlation"] == pytest.approx(1.0)


def test_one_snapshot_pass_counts_earlier_orders_against_later_ones(tmp_path):
    import sys
    from datetime import UTC, datetime
    sys.path.insert(0, "scripts")
    import full_auto as fa

    from app.portfolio.account_risk import RiskPolicy

    stamp = datetime.now(UTC).isoformat()

    class Fake:
        risk_policy = RiskPolicy(max_gross=2.6, max_asset=0.25, max_crypto=0.01, daily_loss=0.15, max_spread_bp=150.0,
                                 max_reference_gap=0.10, max_quote_age_s=180.0)
        audit_path = tmp_path
        calls, posted, audits = [], [], []

        def account(self):
            return {"equity": "100000", "last_equity": "100000", "status": "ACTIVE", "buying_power": "45000"}

        def _get(self, url, **params):
            self.calls.append(url)
            if url.endswith("/quotes/latest"):
                return {"quotes": {s: {"bp": 99.9, "ap": 100.1, "t": stamp} for s in params["symbols"].split(",")}}
            return []

        def _submit(self, leg):
            self.posted.append(leg.symbol)
            leg.status = "submitted"

        def _audit(self, kind, **f):
            self.audits.append((kind, f["symbol"], f.get("allowed")))

    sb = fa.Sandbox(Fake())
    orders = [{"symbol": s, "side": "buy", "qty": 200} for s in ("A", "B", "C")]  # $20k each, $45k buying power
    legs = sb.send_many(orders, {"A": 100.0, "B": 100.0, "C": 100.0})
    assert [x.status for x in legs] == ["submitted", "submitted", "rejected"]
    assert "buying power" in legs[2].note and sb.a.posted == ["A", "B"]
    assert len(sb.a.calls) == 3  # positions, open orders, quotes: once for the whole pass
    assert [a[0] for a in sb.a.audits] == ["risk"] * 3


def test_theme_cap_keeps_gross_and_caps_the_theme():
    from app.portfolio import fund_stability as fs
    w = {"IONQ": 0.1, "RGTI": 0.1, "QBTS": 0.1, "QUBT": 0.1, "AAPL": 0.05, "MSFT": -0.05, "ORCL": 0.05}
    out = fs.theme_cap(w, {s: ["quantum"] for s in ("IONQ", "RGTI", "QBTS", "QUBT")}, per_name=0.1)
    assert abs(sum(out[s] for s in ("IONQ", "RGTI", "QBTS", "QUBT")) - 0.30) < 1e-9
    assert abs(sum(map(abs, out.values())) - sum(map(abs, w.values()))) < 1e-9
    assert out["MSFT"] < -0.05 and all(abs(x) <= 0.1 + 1e-12 for x in out.values())
