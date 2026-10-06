from app.sandbox.forced_call import WEAK_QUOTE, check, event_quote

CARD = ("Company: ACME\n- Acme raised its full-year revenue guidance to $9.1 billion after a record quarter. [u] ()\n"
        "- Acme shares gained 21.4% over the past month. [u] ()\n"
        "- Acme closed at $54.14, moving +1.81% from the previous trading day. [u] ()\n"
        "- Acme faces an antitrust probe by the European Commission into its licensing. [u] ()")
H = ("day", "short", "medium", "long")


def test_price_moves_ranks_and_promotion_are_not_company_events():
    for q in ("Acme shares gained 21.4% over the past month.", "Acme closed at $54.14, moving +1.81%",
              "upgraded to a Zacks Rank #1 (Strong Buy)", "grown from $1,000 to $24,579 over a decade",
              "3 AI stocks to buy now", "Fortinet stock reached a record $182.09"):
        assert WEAK_QUOTE.search(q), q
    for q in ("Acme raised its full-year revenue guidance to $9.1 billion after a record quarter.",
              "Acme faces an antitrust probe by the European Commission into its licensing.",
              "Revenue grew 30% year over year to $1.2 billion."):
        assert not WEAK_QUOTE.search(q), q
    assert event_quote("acme raised its full-year revenue guidance", CARD)
    assert not event_quote("Acme doubled its profit", CARD)  # not in the card


def test_every_horizon_gets_a_side_and_strength_follows_the_evidence():
    raw = {"day": {"label": "5", "quote": "Acme shares gained 21.4% over the past month."},
           "short": {"label": "5", "quote": "Acme raised its full-year revenue guidance to $9.1 billion"},
           "medium": {"label": "1", "quote": "Acme will lose its biggest customer"},
           "long": {"label": "2", "quote": "Acme faces an antitrust probe by the European Commission"}}
    out = check(raw, CARD, H)
    assert out["day"] == {**out["day"], "label": "4", "support": "quoted", "weakened": True}
    assert out["short"]["label"] == "5" and out["short"]["support"] == "event"
    assert out["medium"]["label"] == "2" and out["medium"]["support"] == "none" and out["medium"]["weakened"]
    assert out["long"]["label"] == "2" and not out["long"]["weakened"]


def test_neutral_or_missing_answers_are_failures_never_neutral_ratings():
    out = check({"day": {"label": "3", "quote": ""}, "short": "bull"}, CARD, H)
    assert all(out[h]["label"] == "no_call" for h in H)
    assert check(None, CARD, ("day",))["day"]["label"] == "no_call"
