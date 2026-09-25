"""As-of web tools: nothing dated after the decision time ever reaches the model. All offline (mocked web)."""
import json
from datetime import UTC, datetime

import httpx
import pytest

from app.tools import asof, netguard
from app.tools import gateway as G
from app.tools.netguard import SafeFetcher

AS_OF = datetime(2015, 3, 10, 20, 0, tzinfo=UTC)
HOSTS = ("en.wikipedia.org", "web.archive.org", "www.sec.gov", "data.sec.gov")


async def resolver(host):
    if host not in HOSTS:
        raise OSError("no such host")
    return ["93.184.216.40"]


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setitem(netguard.HOST_MIN_INTERVAL, "web.archive.org", 0.0)


def gw(handler, tmp_path=None, **kw):
    f = SafeFetcher("t", resolver=resolver, transport=httpx.MockTransport(handler))
    return G.ToolGateway(mode="as_of", fetcher=f, as_of=AS_OF, sec_user_agent="A b@c.d",
                         tool_cache=tmp_path, **kw)


async def call(g, tool, **args):
    out = (await g.execute([{"id": 1, "tool": tool, "args": args}]))["1"]
    return (json.loads(out["result"]) if out["ok"] else out["error"]), out["ok"]


def test_as_of_mode_offers_only_dated_tools():
    g = gw(lambda r: httpx.Response(404))
    names = {s["name"] for s in g.specs_for_prompt()}
    assert {"wiki_as_of", "sec_filings_as_of", "news_as_of", "archived_page", "python"} <= names
    assert not names & {"stock_news", "news_search", "fetch_page", "price_history", "web_search", "sec_filings"}
    assert "price_history_as_of" not in names  # no local price file configured
    with pytest.raises(ValueError, match="timezone"):
        G.ToolGateway(mode="as_of", fetcher=g.fetcher, as_of=datetime(2015, 3, 10))  # noqa: DTZ001 - the naive time under test
    live = {s["name"] for s in G.ToolGateway(mode="live", fetcher=g.fetcher).specs_for_prompt()}
    assert "wiki_as_of" not in live


async def test_open_web_tools_are_refused_in_as_of_mode():
    g = gw(lambda r: httpx.Response(200, text="future news"))
    err, ok = await call(g, "news_search", query="AAPL")
    assert not ok and "as_of mode" in err


def test_sec_times_are_shifted_so_they_are_never_early():
    assert asof.sec_accepted_utc("2015-03-10T16:05:00.000Z") == datetime(2015, 3, 10, 21, 5, tzinfo=UTC)
    assert asof.sec_accepted_utc("20150310160500") == datetime(2015, 3, 10, 21, 5, tzinfo=UTC)


def wiki_handler(rev_ts):
    def h(req):
        q = dict(req.url.params)
        if q.get("action") == "query" and q.get("prop") == "revisions":
            assert q["rvstart"] == "20150310200000" and q["rvdir"] == "older"
            return httpx.Response(200, json={"query": {"pages": {"1": {"title": q["titles"], "revisions": [
                {"revid": 42, "timestamp": rev_ts}]}}}})
        if q.get("action") == "parse":
            assert q["oldid"] == "42"
            return httpx.Response(200, json={"parse": {"text": {"*": "<p>Apple makes phones.</p>"}}})
        return httpx.Response(404)
    return h


async def test_wiki_as_of_reads_the_revision_before_the_decision_time():
    res, ok = await call(gw(wiki_handler("2015-03-09T11:00:00Z")), "wiki_as_of", title="Apple Inc.")
    assert ok and res["revision_time"] == "2015-03-09T11:00:00Z" and "phones" in res["text"]
    assert res["url"].endswith("oldid=42")


async def test_wiki_revision_after_the_decision_time_is_refused():
    err, ok = await call(gw(wiki_handler("2015-03-11T00:00:00Z")), "wiki_as_of", title="Apple Inc.")
    assert not ok and "newer than the decision time" in err


async def test_wiki_search_drops_articles_created_later():
    def h(req):
        q = dict(req.url.params)
        if q.get("list") == "search":
            return httpx.Response(200, json={"query": {"search": [{"title": "Apple Inc."},
                                                                  {"title": "Apple Vision Pro"}]}})
        pages = {"1": {"title": q["titles"], **({"revisions": [{"revid": 1, "timestamp": "2014-01-01T00:00:00Z"}]}
                                                if q["titles"] == "Apple Inc." else {"missing": ""})}}
        return httpx.Response(200, json={"query": {"pages": pages}})
    res, ok = await call(gw(h), "wiki_search", query="apple")
    assert ok and res["titles"] == ["Apple Inc."]


def sec_handler(header_ts="20150302160500"):
    def h(req):
        u = str(req.url)
        if u.endswith("company_tickers.json"):
            return httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL"}})
        if "submissions/CIK" in u:
            return httpx.Response(200, json={"filings": {"files": [], "recent": {
                "form": ["8-K", "8-K", "10-Q"], "accessionNumber": ["0001-15-000009", "0001-15-000005", "0001-15-1"],
                "acceptanceDateTime": ["2015-03-10T17:30:00.000Z", "2015-03-02T16:05:00.000Z",
                                       "2015-01-28T16:30:00.000Z"],
                "filingDate": ["2015-03-10", "2015-03-02", "2015-01-28"], "items": ["2.02", "8.01", ""],
                "primaryDocument": ["a.htm", "b.htm", "q.htm"]}}})
        if u.endswith("-index-headers.html"):
            return httpx.Response(200, text=f"<ACCEPTANCE-DATETIME>{header_ts}\n")
        if u.endswith("index.json"):
            return httpx.Response(200, json={"directory": {"item": [{"name": "b.htm"}, {"name": "ex99.htm"},
                                                                     {"name": "x.jpg"}]}})
        if u.endswith(".htm"):
            return httpx.Response(200, text="<p>Revenue rose 30%.</p>", headers={"content-type": "text/html"})
        return httpx.Response(404)
    return h


async def test_sec_filings_as_of_hides_filings_accepted_after_the_decision_time():
    res, ok = await call(gw(sec_handler()), "sec_filings_as_of", ticker="AAPL", forms=["8-K", "10-Q"])
    assert ok
    # the 8-K accepted 17:30 ET on the decision day is 22:30 UTC at the earliest: after 20:00 UTC, so hidden
    assert [f["filed"] for f in res["filings"]] == ["2015-03-02", "2015-01-28"]


async def test_read_filing_rechecks_acceptance_time_and_url():
    url = "https://www.sec.gov/Archives/edgar/data/320193/000100000150000050/ex99.htm"
    res, ok = await call(gw(sec_handler()), "read_filing", url=url)
    assert ok and "Revenue rose" in res["text"]
    err, ok = await call(gw(sec_handler(header_ts="20150310173000")), "read_filing", url=url)
    assert not ok and "after the decision time" in err
    err, ok = await call(gw(sec_handler()), "read_filing", url="https://evil.example/x.htm")
    assert not ok and "only https://www.sec.gov/Archives" in err
    docs, ok = await call(gw(sec_handler()), "filing_documents", url=url)
    assert ok and [d["name"] for d in docs["documents"]] == ["b.htm", "ex99.htm"]


def archive_handler(cdx_ts="20150301120000", redirect_to=None):
    def h(req):
        u = str(req.url)
        if "/cdx/search/cdx" in u:
            q = dict(req.url.params)
            assert "to" not in q  # the whole index is fetched once; the date filter is applied locally
            if "marketwatch" in q["url"]:
                return httpx.Response(200, text="[]")  # no copy at all: falls through to the next source
            # the index also lists copies made AFTER the decision time: they must never be chosen
            return httpx.Response(200, json=[["timestamp", "original"], ["20141201000000", "http://" + q["url"]],
                                             [cdx_ts, "http://" + q["url"]], ["20150311000000", "http://" + q["url"]],
                                             ["20160101000000", "http://" + q["url"]]])
        if "/web/" in u and "id_/" in u:
            if redirect_to and redirect_to not in u:
                return httpx.Response(302, headers={"location": f"https://web.archive.org/web/{redirect_to}id_/x"})
            return httpx.Response(200, text="<title>AAPL news</title><p>Apple Watch launch event.</p>",
                                  headers={"content-type": "text/html"})
        return httpx.Response(404)
    return h


async def test_news_as_of_uses_the_latest_capture_before_the_decision_time():
    res, ok = await call(gw(archive_handler()), "news_as_of", ticker="AAPL")
    assert ok and res["source"] == "reuters" and res["captured_utc"].startswith("2015-03-01")
    assert "Apple Watch" in res["text"]


async def test_archive_redirect_to_a_later_copy_is_refused():
    err, ok = await call(gw(archive_handler(redirect_to="20150420000000")), "archived_page",
                         url="https://www.example.com/story")
    assert not ok and "after the decision time" in err
    err, ok = await call(gw(archive_handler()), "archived_page", url="https://web.archive.org/web/2020/x")
    assert not ok and "original page URL" in err


async def test_price_history_as_of_uses_the_injected_point_in_time_lookup():
    seen = {}

    def lookup(t, as_of, n):
        seen["args"] = (t, as_of, n)
        return [(f"2015-02-{d:02d}", 100.0 + d) for d in range(2, 28)]
    res, ok = await call(gw(lambda r: httpx.Response(404), price_lookup=lookup), "price_history_as_of",
                         ticker="aapl", days=30)
    assert ok and seen["args"] == ("AAPL", AS_OF, 31) and res["last_close"] == 127.0


async def test_results_are_cached_and_replayed_exactly(tmp_path):
    calls = []

    def h(req):
        calls.append(str(req.url))
        return wiki_handler("2015-03-09T11:00:00Z")(req)
    first, ok = await call(gw(h, tmp_path), "wiki_as_of", title="Apple Inc.")
    n = len(calls)
    again, ok2 = await call(gw(lambda r: httpx.Response(500), tmp_path), "wiki_as_of", title="Apple Inc.")
    assert ok and ok2 and again == first and n == 2 and len(calls) == n
    # failures are not cached: a transient outage must not become a permanent "no data"
    _, ok = await call(gw(lambda r: httpx.Response(500), tmp_path), "wiki_as_of", title="Other")
    assert not ok and len(list(tmp_path.rglob("*.json"))) == 1


async def test_capture_index_is_fetched_once_per_page_and_reused(tmp_path):
    calls = []

    def h(req):
        if "/cdx/search/cdx" in str(req.url):
            calls.append(str(req.url))
        return archive_handler()(req)
    g = gw(h, tmp_path)
    await call(g, "news_as_of", ticker="AAPL")
    n = len(calls)
    later = G.ToolGateway(mode="as_of", fetcher=g.fetcher, as_of=datetime(2015, 3, 12, 20, tzinfo=UTC),
                          tool_cache=tmp_path)
    res, ok = await call(later, "news_as_of", ticker="AAPL")
    assert ok and res["captured_utc"].startswith("2015-03-11") and len(calls) == n  # new month, no new lookup
