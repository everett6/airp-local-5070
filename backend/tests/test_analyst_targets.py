"""Analyst targets and rating actions are read from archived Yahoo quote-page text by code, never estimated."""
from app.tools.asof import parse_analyst_page

PFE = ("PE Ratio (TTM) 19.63 EPS (TTM) 1.36 Earnings Date May 5, 2026 Forward Dividend & Yield 1.72 (6.44%) "
       "Ex-Dividend Date May 8, 2026 1y Target Est 29.00 Pfizer Inc. Overview RBC Capital 43/100 Latest Rating "
       "Underperform Analyst Price Targets 24.00 Low 29.00 Average 26.70 Current 36.00 High Analyst R g Action "
       "Maintains Rating Neutral Price Action Raises Price Target 26 -> 27 View More Statistics")


def test_targets_and_actions_from_a_real_capture():
    got = parse_analyst_page(PFE)
    assert got["target_avg"] == 29.0 and got["target_low"] == 24.0 and got["target_high"] == 36.0
    assert got["price_on_page"] == 26.7
    assert {"action": "raises target", "from": 26.0, "to": 27.0} in got["actions"]
    assert got["raises"] == 1 and got["lowers"] == 0


def test_a_page_without_targets_yields_nothing():
    got = parse_analyst_page("Some headline text about the company, no analyst section here.")
    assert got["target_avg"] is None and got["actions"] == []


def test_second_page_layout_without_the_word_low():
    txt = ("1y Target Est 19.63 Kenvue Inc. Analyst Price Targets 15.00 19.63 Average 14.37 Current 24.50 High Analyst "
           "Recommendations ... Lowers Price Target 26 -> 15 View More")
    got = parse_analyst_page(txt)
    assert (got["target_low"], got["target_avg"], got["price_on_page"], got["target_high"]) == (15.0, 19.63, 14.37, 24.5)
    assert got["lowers"] == 1


def test_headline_actions_count_only_this_company():
    from app.tools.asof import headline_actions
    lines = ["Jefferies and Deutsche Bank Lower Price Targets on Kenvue (KVUE)",
             "Deutsche Bank Lowers Kenvue (KVUE) PT to $18, Keeps a Hold Rating",
             "Skyworks upgraded, UnitedHealth downgraded: Wall Street's top analyst calls",
             "Kenvue upgraded to Buy at Citi"]
    got = headline_actions(lines, "KVUE", "Kenvue Inc.")
    assert got == {"headline_up": 1, "headline_down": 2, "headlines_about_company": 3}
