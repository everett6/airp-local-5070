from app.sandbox import research_memory as rm
from app.sandbox import research_quality as rq

PR = ("Acme Corp today announced record third quarter revenue of 120 million dollars, up 40 percent from a year "
      "ago, and raised its full year guidance to 480 million dollars on strong demand for its data center products.")


def test_syndicated_press_release_counts_once():
    pages = [{"publisher": "a.com", "text": PR}, {"publisher": "b.com", "text": "Reprinted: " + PR + " More."},
             {"publisher": "c.com", "text": "Analysts at a bank said shares of the chip maker looked expensive after "
                                            "a long rally and cut their rating to neutral this morning."},
             {"publisher": "www.sec.gov", "text": "Form 8-K exhibit unrelated wording entirely about bylaws."}]
    assert min(map(sorted, rq.syndication_groups(pages))) == [0, 1]
    assert rq.independent_sources(pages) == 2  # a.com/b.com once, c.com; SEC alone does not count


def test_fact_tags_and_timestamp_audit():
    facts = [{"text": "Revenue rose 40%.", "date": "2026-10-05"}, {"text": "Old deal signed.", "date": "2026-07-01"},
             {"text": "Guidance raised.", "date": ""}]
    known = {rm._key("Guidance raised."): "2026-10-01"}
    tags = [f["tag"] for f in rq.tag_facts(facts, known, "2026-10-07T01:00:00+00:00", rm._key)]
    assert tags == ["NEW", "OLD", "KNOWN since 2026-10-01"]
    rec = {"as_of": "2026-10-07T01:00:00+00:00", "decided_at": "2026-10-07T01:10:00+00:00",
           "article_retrieval": {"pages": [{"url": "u", "published": "2026-10-07T02:00:00+00:00",
                                            "retrieved_at": "2026-10-07T01:01:00+00:00"}]},
           "wide_brief": {"facts": [{"text": "x", "date": "2026-10-08"}]}}
    out = rq.audit(rec)
    assert len(out) == 2 and "published after" in out[0] and "after the research time" in out[1]
    assert rq.audit({"as_of": "2026-10-07"}) == []
