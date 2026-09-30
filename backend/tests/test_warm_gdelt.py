import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import warm_gdelt

from app.tools.gateway import TOOLS, ToolGateway, validate_args


def test_asof_filter_strictly_before_acceptance():
    accepted = datetime(2026, 2, 2, 21, 7, 57, tzinfo=UTC)
    articles = [
        {"seendate": "20260202T210757Z", "domain": "later.test", "title": "At acceptance"},
        {"seendate": "20260202T210657Z", "domain": "earlier.test", "title": "One minute earlier"},
    ]
    assert [h["title"] for h in warm_gdelt.asof_headlines(articles, accepted)] == ["One minute earlier"]


def test_naive_acceptance_is_interpreted_as_utc():
    assert warm_gdelt.parse_accepted("2026-02-02T21:07:57") == datetime(
        2026, 2, 2, 21, 7, 57, tzinfo=UTC
    )


def test_cache_uses_gateway_news_key(monkeypatch, tmp_path):
    accepted = datetime(2026, 2, 2, 21, 7, 57, tzinfo=UTC)
    monkeypatch.setattr(warm_gdelt, "WEBCACHE", tmp_path)
    actual = warm_gdelt.cache_path("RMBS", accepted)
    gw = ToolGateway.from_env("as_of", as_of=accepted, tool_cache=tmp_path, max_result_chars=5000)
    args = validate_args(TOOLS["news_as_of"], {"ticker": "RMBS"})
    expected = gw._cache_path(TOOLS["news_as_of"].name, args)
    assert actual == expected


def test_headlines_limited_to_15_newest_first():
    accepted = datetime(2026, 2, 2, tzinfo=UTC)
    articles = [{"seendate": (accepted - timedelta(minutes=i + 1)).strftime("%Y%m%dT%H%M%SZ"),
                 "domain": "example.test", "title": str(i)} for i in range(20)]
    headlines = warm_gdelt.asof_headlines(articles, accepted)
    assert len(headlines) == 15
    assert headlines[0]["title"] == "0"
    assert headlines[-1]["title"] == "14"


def test_resume_skips_ok_none_and_retries_error(monkeypatch, tmp_path):
    sample = tmp_path / "events.csv"
    sample.write_text("cik,accession,accepted_utc,ticker\n"
                      "1,ok,2026-01-02T03:04:05Z,AAA\n"
                      "2,none,2026-01-02T03:04:05Z,BBB\n"
                      "3,error,2026-01-02T03:04:05Z,CCC\n")
    log = tmp_path / "warm.jsonl"
    log.write_text("\n".join(json.dumps({"accession": acc, "status": status})
                          for acc, status in (("ok", "ok"), ("none", "none"), ("error", "error"))) + "\n")
    monkeypatch.setattr(warm_gdelt, "SAMPLE", sample)
    monkeypatch.setattr(warm_gdelt, "LOG", log)
    monkeypatch.setattr(warm_gdelt, "clean_name", lambda _: "Company")
    monkeypatch.setattr(warm_gdelt, "fetch_gdelt", lambda *_: [])
    monkeypatch.setattr(warm_gdelt, "cache_path", lambda *_: tmp_path / "cache.json")
    records = warm_gdelt.run()
    assert [r["accession"] for r in records] == ["error"]
    assert records[0]["status"] == "none"


def test_naive_acceptance_times_are_utc_not_local():
    """29 Sep 2026: a sandbox run read the UTC acceptance times as local (PDT) time, shifting the as-of window
    7 hours past the release. Naive CSV times must stay exactly as written, as UTC."""
    from datetime import UTC, datetime

    import warm_gdelt as w
    assert w.parse_accepted("2026-02-02T21:07:57") == datetime(2026, 2, 2, 21, 7, 57, tzinfo=UTC)
    assert w.parse_accepted("2026-02-02T21:07:57").isoformat() == "2026-02-02T21:07:57+00:00"
