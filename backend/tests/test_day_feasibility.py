from app.portfolio.day_feasibility import metrics, verdict


def bars(n=30, o=100.0, rng=4.0):
    return [{"o": o, "h": o + rng / 2, "l": o - rng / 2, "c": o} for _ in range(n)]


def test_liquid_tight_and_moving_passes():
    m = metrics(bars(), spread_bp=2.0, dollar_volume=500e6)
    assert m["range_bp"] == 400.0 and m["gap_bp"] == 0.0 and m["atr_bp"] == 400.0
    v = verdict(m)
    assert v["feasible"] and v["cost_bp"] == 6.0 and v["range_to_cost"] == 66.7


def test_each_failure_is_named():
    v = verdict(metrics(bars(rng=0.2), spread_bp=40.0, dollar_volume=10e6))
    assert not v["feasible"]
    assert "volume" in v["why"] and "spread" in v["why"] and "range only" in v["why"]
    assert not verdict(metrics(bars(), spread_bp=None, dollar_volume=500e6))["feasible"]  # unknown spread
    assert verdict(metrics(bars(10), 2.0, 500e6))["why"] == "not enough daily bars"
