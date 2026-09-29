import pandas as pd

from app.sandbox.carry import funding_carry


def _hours(start: str, days: int, rate: float, asset: str = "BTC") -> pd.DataFrame:
    ts = pd.date_range(start, periods=24 * days, freq="h", tz="UTC")
    return pd.DataFrame({"ts": ts, "asset": asset, "rate": rate})


def _both(btc: pd.DataFrame) -> pd.DataFrame:
    eth = btc.assign(asset="ETH", rate=0.0)  # ETH never positive: never held
    return pd.concat([btc, eth], ignore_index=True)


def test_held_from_the_second_week_and_charged_once():
    f = _both(_hours("2024-01-01", 21, 1e-5))  # Mon 1 Jan 2024, three weeks of positive funding
    r = funding_carry(f, cost=0.0015, margin=0.25)
    wk1, wk2 = r.loc["2024-01-01":"2024-01-07"], r.loc["2024-01-08":"2024-01-14"]
    assert not wk1["held_btc"].any() and wk2["held_btc"].all()
    day = 24 * 1e-5 / 1.25
    assert abs(wk2["btc"].iloc[0] - (day - 0.0015)) < 1e-12 and abs(wk2["btc"].iloc[1] - day) < 1e-12
    assert abs(r.loc["2024-01-16", "ret"] - 0.5 * day) < 1e-12  # half the sleeve per asset
    assert (r["btc"] < -0.001).sum() == 1  # entry cost only once while it stays held


def test_negative_funding_stays_flat_and_exit_is_charged():
    pos = _hours("2024-01-01", 14, 1e-5)
    neg = _hours("2024-01-15", 14, -1e-5)
    r = funding_carry(_both(pd.concat([pos, neg], ignore_index=True)), cost=0.001, margin=0.0)
    assert r.loc["2024-01-15", "held_btc"]  # last week was positive
    assert not r.loc["2024-01-22":"2024-01-28", "held_btc"].any()
    assert abs(r.loc["2024-01-22", "btc"] + 0.001) < 1e-12  # exit cost, no funding
    assert (r.loc["2024-01-23":"2024-01-28", "btc"] == 0).all()


def test_needs_120_hours_of_history():
    f = _both(_hours("2024-01-03", 12, 1e-5))  # Wed start: only 120 h before Mon 8 Jan
    r = funding_carry(f, cost=0.0, margin=0.0)
    assert r.loc["2024-01-08", "held_btc"]
    g = _both(_hours("2024-01-04", 11, 1e-5))  # 96 h: not enough
    assert not funding_carry(g, cost=0.0, margin=0.0).loc["2024-01-08", "held_btc"]
