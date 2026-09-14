"""Web tools: network guard, parsers, gateway rules, sandboxed python, and the research loop. All offline."""
import asyncio
import json
import shutil
import textwrap
import time

import httpx
import pytest

from app.sandbox.jail import AgentJail, JailError
from app.tools import gateway as G
from app.tools.extract import html_to_text, parse_rss
from app.tools.netguard import FetchError, SafeFetcher, is_public_ip

HAS_BWRAP = shutil.which("bwrap") is not None

DNS = {"news.example": ["93.184.216.34"], "internal.example": ["10.0.0.5"], "mixed.example": ["93.184.216.34",
       "127.0.0.1"], "www.sec.gov": ["93.184.216.35"], "robots.example": ["93.184.216.36"]}


async def fake_resolver(host):
    if host not in DNS:
        raise OSError("no such host")
    return DNS[host]


def fetcher(handler, **kw):
    return SafeFetcher("test-agent", resolver=fake_resolver, transport=httpx.MockTransport(handler), **kw)


# ---------------- network guard ----------------

@pytest.mark.parametrize("ip,public", [
    ("8.8.8.8", True), ("93.184.216.34", True), ("127.0.0.1", False), ("10.1.2.3", False),
    ("192.168.1.1", False), ("172.16.0.1", False), ("169.254.169.254", False), ("100.64.0.1", False),
    ("0.0.0.0", False), ("::1", False), ("fc00::1", False), ("fe80::1", False), ("::ffff:127.0.0.1", False),
    ("2606:4700::1111", True), ("224.0.0.1", False)])
def test_is_public_ip(ip, public):
    assert is_public_ip(ip) is public


@pytest.mark.parametrize("url,msg", [
    ("file:///etc/passwd", "scheme"), ("ftp://news.example/x", "scheme"),
    ("http://news.example:11434/api/tags", "port"), ("http://user:pw@news.example/", "credentials"),
    ("http://internal.example/admin", "non-public"), ("http://mixed.example/", "non-public"),
    ("http://127.0.0.1/", "non-public"), ("http://[::1]/", "non-public"), ("http://nowhere.example/", "resolve")])
async def test_blocked_urls(url, msg):
    f = fetcher(lambda r: httpx.Response(200, text="should not be reached"))
    with pytest.raises(FetchError, match=msg):
        await f.fetch(url)
    await f.aclose()


async def test_redirect_to_private_address_is_blocked():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://internal.example/secret"})
    f = fetcher(handler)
    with pytest.raises(FetchError, match="non-public"):
        await f.fetch("http://news.example/story")
    await f.aclose()


async def test_redirect_loop_is_capped():
    f = fetcher(lambda r: httpx.Response(302, headers={"location": "http://news.example/again"}), max_redirects=2)
    with pytest.raises(FetchError, match="too many redirects"):
        await f.fetch("http://news.example/start")
    await f.aclose()


async def test_size_limits_streamed_and_declared():
    f = fetcher(lambda r: httpx.Response(200, content=b"x" * 3_000_000), max_bytes=1_000_000)
    with pytest.raises(FetchError, match="too large"):
        await f.fetch("http://news.example/big")
    await f.aclose()


async def test_cache_serves_repeat_requests_without_network():
    hits = []

    def handler(request):
        hits.append(request.url)
        return httpx.Response(200, text="hello", headers={"content-type": "text/plain"})
    f = fetcher(handler)
    a = await f.fetch("http://news.example/a")
    b = await f.fetch("http://news.example/a")
    assert (a.text, b.text, b.from_cache, len(hits)) == ("hello", "hello", True, 1)
    await f.aclose()


async def test_sec_requests_are_paced():
    f = fetcher(lambda r: httpx.Response(200, text="{}"))
    t0 = time.monotonic()
    await asyncio.gather(*(f.fetch(f"https://www.sec.gov/x{i}", use_cache=False) for i in range(3)))
    assert time.monotonic() - t0 >= 0.2  # >= 2 intervals of 0.12 s
    await f.aclose()


# ---------------- parsers ----------------

RSS = """<rss><channel>
<item><title>Chipmaker beats estimates</title><link>https://news.google.com/rss/articles/CBMi123</link>
 <pubDate>Fri, 12 Sep 2026 13:00:00 GMT</pubDate><source url="https://x.com">Reuters</source>
 <description>&lt;a href="x"&gt;Chipmaker beats&lt;/a&gt;</description></item>
<item><title>Bank &amp; rates</title>
 <link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;url=https%3a%2f%2fwww.example.com%2fstory&amp;c=1</link>
 <pubDate>Sat, 13 Sep 2026 09:30:00 GMT</pubDate></item>
</channel></rss>"""


def test_parse_rss_unwraps_bing_and_flags_google_links():
    items = parse_rss(RSS)
    assert items[0]["source"] == "Reuters" and items[0]["fetchable"] is False
    assert items[0]["published"].startswith("2026-09-12T13:00")
    assert items[1] == {**items[1], "title": "Bank & rates", "url": "https://www.example.com/story",
                        "source": "www.example.com", "fetchable": True}
    assert parse_rss("<not xml") == []


def test_html_to_text_keeps_article_drops_chrome():
    html = """<html><head><title>T</title><meta property="article:published_time" content="2026-09-12T10:00Z">
    <script>var secret = 1;</script></head><body><nav>Home | Markets | About us and more links here</nav>
    <article><p>The company reported revenue growth of 20 percent, beating analyst estimates.</p>
    <p>Shares rose.</p></article><footer>Copyright notice and many footer links here too</footer></body></html>"""
    out = html_to_text(html)
    assert out["title"] == "T" and out["published"] == "2026-09-12T10:00Z"
    assert "revenue growth of 20 percent" in out["text"] and "Shares rose." in out["text"]
    assert "secret" not in out["text"] and "Home | Markets" not in out["text"] and "Copyright" not in out["text"]


# ---------------- gateway ----------------

def gw(mode="live", handler=None, **kw):
    handler = handler or (lambda r: httpx.Response(200, text="ok"))
    return G.ToolGateway(mode=mode, fetcher=fetcher(handler), allow_unjailed_python=not HAS_BWRAP, **kw)


def test_validate_args():
    spec = G.TOOLS["news_search"]
    assert G.validate_args(spec, {"query": "x", "days": 999}) == {"query": "x", "days": 30, "limit": 10}
    for bad, msg in [({"query": "x", "bogus": 1}, "unknown"), ({}, "missing"), ({"query": 5}, "string"),
                     ({"query": "x", "days": "7"}, "integer"), ({"query": "x", "days": True}, "integer"),
                     ("notadict", "object"), ({"query": "x" * 201}, "longer")]:
        with pytest.raises(G.ToolInputError, match=msg):
            G.validate_args(spec, bad)
    with pytest.raises(G.ToolInputError, match="entries"):
        G.validate_args(G.TOOLS["sec_filings"], {"ticker": "NVDA", "forms": ["rm -rf"]})


async def test_backtest_mode_refuses_web_tools():
    g = gw("backtest")
    assert [s["name"] for s in g.specs_for_prompt()] == ["python"]
    out = await g.execute([{"id": 1, "tool": "stock_news", "args": {"ticker": "NVDA"}},
                           {"id": 2, "tool": "nope", "args": {}}])
    assert "backtest mode" in out["1"]["error"] and "unknown tool" in out["2"]["error"]
    await g.aclose()


async def test_live_mode_lists_only_configured_tools():
    names = {s["name"] for s in gw("live").specs_for_prompt()}
    assert {"stock_news", "news_search", "fetch_page", "price_history", "python"} <= names
    assert "web_search" not in names and "sec_filings" not in names  # no API key / SEC contact configured
    names = {s["name"] for s in gw("live", sec_user_agent="A b@c", brave_api_key="k").specs_for_prompt()}
    assert {"web_search", "sec_filings"} <= names


async def test_tool_crash_becomes_an_error_result_and_is_logged():
    events = []

    async def boom(g, a):
        raise ZeroDivisionError("bad data")
    spec = G.ToolSpec("boom", "x", {}, boom, live_only=False)
    G.TOOLS["boom"] = spec
    try:
        g = gw(on_event=events.append)
        out = await g.execute([{"id": "a", "tool": "boom", "args": {}}])
    finally:
        del G.TOOLS["boom"]
    assert out["a"] == {"ok": False, "error": "ZeroDivisionError: bad data"}
    assert events[0]["tool"] == "boom" and events[0]["ok"] is False and g.log == events


async def test_batch_overflow_is_reported_not_run():
    g = gw(max_calls_per_batch=2)
    out = await g.execute([{"id": i, "tool": "nope", "args": {}} for i in range(4)])
    assert "skipped" in out["3"]["error"] and len(g.log) == 2


async def test_fetch_page_respects_robots_and_extracts_text():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<title>Story</title><p>An important sentence about quarterly results today.</p>")
    DNS["robots.example"] = ["93.184.216.36"]
    g = gw(handler=handler)
    out = await g.execute([{"id": "ok", "tool": "fetch_page", "args": {"url": "https://robots.example/news/1"}},
                           {"id": "no", "tool": "fetch_page", "args": {"url": "https://robots.example/private/2"}}])
    assert "quarterly results" in json.loads(out["ok"]["result"])["text"]
    assert "robots.txt" in out["no"]["error"]
    await g.aclose()


async def test_python_tool_runs_and_times_out():
    g = gw(python_timeout_s=2)
    out = await g.execute([{"id": "a", "tool": "python", "args": {"code": "print(sum(range(10)))"}},
                           {"id": "b", "tool": "python", "args": {"code": "while True: pass"}}])
    assert json.loads(out["a"]["result"])["stdout"] == "45\n"
    assert "timed out" in out["b"]["error"]


@pytest.mark.skipif(not HAS_BWRAP, reason="bubblewrap not installed")
async def test_python_tool_has_no_network_or_files():
    code = textwrap.dedent("""
        import socket
        try:
            socket.create_connection(("1.1.1.1", 443), timeout=2); print("NET OPEN")
        except OSError: print("no network")
        try:
            open("/etc/hostname").read(); print("FILES OPEN")
        except OSError: print("no files")
    """)
    out = await gw().execute([{"id": "a", "tool": "python", "args": {"code": code}}])
    stdout = json.loads(out["a"]["result"])["stdout"]
    assert "no network" in stdout and "no files" in stdout


# ---------------- research loop through the jail ----------------

class ScriptedLLM:
    def __init__(self, replies):
        self.replies, self.calls, self.prompts = list(replies), 0, []

    async def __call__(self, system, user):
        self.calls += 1
        self.prompts.append(user)
        return self.replies.pop(0) if self.replies else '{"final": {"p_up": 0.5}}'


class FakeTools:
    def __init__(self):
        self.seen = []

    async def execute(self, reqs):
        self.seen.extend(reqs)
        return {str(r["id"]): {"ok": True, "result": f"RESULT-FOR-{r['tool']}"} for r in reqs}


async def test_research_loop_calls_tools_then_answers():
    llm = ScriptedLLM([
        json.dumps({"thought": "look", "actions": [{"tool": "stock_news", "args": {"ticker": "NVDA"}},
                                                   {"tool": "price_history", "args": {"ticker": "NVDA"}}]}),
        json.dumps({"thought": "done", "final": {"p_up": 0.58, "reason": "news", "sources": ["https://a.b/c"]}}),
    ])
    tools = FakeTools()
    async with AgentJail(llm, tools=tools, allow_unjailed=not HAS_BWRAP) as jail:
        r = await jail.call({"task": "research", "subject": {"ticker": "NVDA", "as_of": "2026-09-13T00:00:00"},
                             "tools": [], "max_rounds": 3})
    assert (r["p_up"], r["answered"], r["rounds"], r["sources"]) == (0.58, True, 2, ["https://a.b/c"])
    assert [t["tool"] for t in tools.seen] == ["stock_news", "price_history"] and jail.tool_calls == 2
    assert "RESULT-FOR-stock_news" in llm.prompts[1]  # observations are fed back to the model


async def test_research_loop_forces_an_answer_and_survives_garbage():
    llm = ScriptedLLM(["not json"] + [json.dumps({"actions": [{"tool": "stock_news", "args": {}}]})] * 5
                      + ["still not json"])
    async with AgentJail(llm, tools=FakeTools(), allow_unjailed=not HAS_BWRAP) as jail:
        r = await jail.call({"task": "research", "subject": {"ticker": "X", "as_of": "t"}, "tools": [],
                             "max_rounds": 2})
    assert r["answered"] is False and r["p_up"] == 0.5 and llm.calls == 3
    assert "used all tool rounds" in llm.prompts[-1]


async def test_tool_requests_without_tools_enabled_kill_the_agent():
    llm = ScriptedLLM([json.dumps({"actions": [{"tool": "stock_news", "args": {}}]})])
    async with AgentJail(llm, allow_unjailed=not HAS_BWRAP) as jail:
        with pytest.raises(JailError, match="no tools are enabled"):
            await jail.call({"task": "research", "subject": {"ticker": "X", "as_of": "t"}, "tools": []})


async def test_live_research_end_to_end_offline(tmp_path, monkeypatch):
    from app.live import research as R

    llm = ScriptedLLM([json.dumps({"actions": [{"tool": "python", "args": {"code": "print(1+1)"}}]}),
                       json.dumps({"final": {"p_up": 0.61, "reason": "calc", "sources": ["python"]}})])
    events = []
    recs = await R.research_tickers(
        ["nvda"], llm=llm, save=False, on_event=events.append, allow_unjailed=not HAS_BWRAP,
        gateway_factory=lambda f, ev: G.ToolGateway(mode="live", fetcher=f, on_event=ev,
                                                   allow_unjailed_python=not HAS_BWRAP))
    rec = recs[0]
    assert rec["ticker"] == "NVDA" and rec["p_up"] == 0.61 and rec["tool_calls"] == 1
    assert rec["tool_log"][0]["tool"] == "python" and rec["tool_log"][0]["ok"]
    assert events[-1]["final"] and "provenance" in rec
    path = R.save_decision(rec, tmp_path)
    with pytest.raises(FileExistsError):  # write-once
        R.save_decision(rec, tmp_path)
    assert json.loads(path.read_text())["p_up"] == 0.61
    with pytest.raises(ValueError, match="invalid tickers"):
        await R.research_tickers(["../etc"], llm=llm, save=False)
