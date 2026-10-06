import asyncio
import json

from app.sandbox.page_readers import block, card, chunks, merge, read_pages

AS_OF = "2026-10-04T22:00:00+00:00"


def page(url, text, published="2026-10-02T12:00:00+00:00"):
    return {"url": url, "title": "t", "published": published, "text": text}


def test_chunks_cover_the_text_with_small_overlaps():
    text = " ".join(f"Sentence number {i} is here." for i in range(800))
    parts = chunks(text, size=2000, overlap=100)
    assert len(parts) > 5 and all(len(p) <= 2000 for p in parts)
    assert parts[0].startswith("Sentence number 0") and parts[-1].endswith("Sentence number 799 is here.")
    assert all(p.rstrip().endswith(".") for p in parts[:-1])  # cut at sentence ends
    assert chunks("short") == ["short"] and chunks("") == []


def test_every_page_is_read_and_numbers_are_checked_against_that_page_only():
    a = page("https://a.test/x", "Acme revenue rose 31% to $2.4 billion in the quarter. " * 3)
    b = page("https://b.test/y", "Acme signed a supply deal with Initech worth $500 million. " * 3)
    calls = []

    async def llm(system, user):
        calls.append(user)
        if "a.test" in user:
            facts = [{"text": "Acme revenue rose 31% to $2.4 billion.", "source": "S1", "date": "2026-10-02"},
                     {"text": "Acme sold $500 million of chips.", "source": "S1", "date": "2026-10-02"}]  # wrong page
        else:
            facts = [{"text": "Acme signed a $500 million supply deal with Initech.", "source": "S1", "date": "2026-10-01"}]
        return json.dumps({"facts": facts, "risks": ["Customer concentration"], "catalysts": []})

    out = asyncio.run(read_pages(llm, {"ticker": "ACME", "as_of": AS_OF, "horizon_days": 21}, [a, b]))
    assert len(calls) == 2 and [r["kept"] for r in out["reads"]] == [1, 1]
    assert out["reads"][0]["dropped"]["number_not_in_evidence"] == 1
    merged = merge(out["briefs"], AS_OF)
    texts = [f["text"] for f in merged["facts"]]
    assert texts == ["Acme revenue rose 31% to $2.4 billion.", "Acme signed a $500 million supply deal with Initech."]
    assert merged["risks"] == ["Customer concentration"]
    c = card("ACME", merged)
    assert c.startswith("Company: ACME") and "[https://a.test/x]" in c and "Jan risks" in c


def test_merge_drops_duplicates_and_facts_dated_after_the_decision():
    briefs = [{"facts": [{"text": "Acme beat estimates.", "source": "u1", "date": "2026-10-01"},
                         {"text": "Acme will report on Oct 30.", "source": "u1", "date": "2026-10-30"}]},
              {"facts": [{"text": "ACME beat estimates!", "source": "u2", "date": "2026-10-01"},
                         {"text": "Acme cut prices.", "source": "u2", "date": "2026-09-20"}]}]
    merged = merge(briefs, AS_OF, max_facts=5)
    assert [f["text"] for f in merged["facts"]] == ["Acme beat estimates.", "Acme cut prices."]


def test_a_failed_reader_does_not_lose_the_other_pages_and_chunks_are_capped():
    pages = [page(f"https://p{i}.test/", "Acme grew 5%. " * 1000) for i in range(3)]

    async def llm(system, user):
        if "p1.test" in user:
            raise TimeoutError
        return json.dumps({"facts": [{"text": "Acme grew 5%.", "source": "S1", "date": ""}]})

    out = asyncio.run(read_pages(llm, {"ticker": "ACME", "as_of": AS_OF}, pages, max_chunks=4))
    assert len(out["reads"]) == 4
    assert [r["url"] for r in out["reads"][:3]] == [p["url"] for p in pages]  # first chunk of each page first
    assert sum("error" in r for r in out["reads"]) == 1 and len(out["briefs"]) == 3


def test_block_matches_the_verifier_format():
    b = block(page("https://a.test/x", "text 1"), "text 1")
    assert b.startswith("[page excerpt]\n  - fetch_page(")
