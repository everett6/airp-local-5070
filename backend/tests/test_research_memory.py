from datetime import UTC, datetime

from app.sandbox import research_memory as rm

NOW = datetime(2026, 10, 6, tzinfo=UTC)


def row(facts, urls, status="decided", ratings=None):
    return {"ticker": "ionq", "status": status, "decided_at": NOW.isoformat(), "ratings": ratings or {"long": 4},
            "primary": "long", "bear_case": "dilution", "wide_brief": {"facts": facts},
            "article_retrieval": {"pages": [{"url": u} for u in urls]}}


def test_remember_merges_dedupes_and_expires(tmp_path):
    rm.remember(row([{"text": "IonQ won a $50m contract.", "source": "S1", "date": "2026-10-01"},
                     {"text": "Old news.", "source": "S2", "date": "2026-01-01"}], ["https://a/1"]), tmp_path, NOW)
    mem = rm.remember(row([{"text": "IONQ won a $50M contract", "source": "S9", "date": "2026-10-01"},
                           {"text": "Revenue guidance raised.", "source": "S3", "date": "2026-10-05"}],
                          ["https://a/1", "https://b/2"]), tmp_path, NOW)
    assert [f["text"] for f in mem["facts"]] == ["Revenue guidance raised.", "IonQ won a $50m contract."]
    assert rm.seen_urls("IONQ", tmp_path) == {"https://a/1", "https://b/2"}
    assert len(mem["calls"]) == 2
    block = rm.past_block("IONQ", tmp_path)
    assert "past research" in block.lower() and "long bull" in block and "dilution" not in block
    assert len(rm.prior_brief("IONQ", tmp_path, NOW)["facts"]) == 2


def test_failed_research_keeps_facts_but_adds_no_call_and_area_note(tmp_path):
    rm.remember(row([{"text": "Rigetti shipped a 100-qubit system.", "source": "S1", "date": "2026-10-02"}], [],
                    status="research_failed"), tmp_path, NOW)
    assert rm.load("ionq", tmp_path)["calls"] == []
    note = rm.area_note("RGTI", ["IONQ", "RGTI"], tmp_path)
    assert "IONQ" in note and "100-qubit" in note
    assert rm.area_note("IONQ", ["IONQ"], tmp_path) == "" and rm.past_block("XYZ", tmp_path) == ""
