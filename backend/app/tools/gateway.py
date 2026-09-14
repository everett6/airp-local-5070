"""
Tool gateway: the only way the jailed agent can touch the outside world.

The agent (no network, see sandbox/jail.py) sends `tool_requests`; the
orchestrator runs them here, in parallel, and returns compact results.

Modes
  live      "now" is the decision time, so the web is fair game: news, pages,
            prices, SEC filings, web search (if an API key is configured).
  backtest  the web already contains the future of any past date, so every
            web tool is refused; only offline tools (python) are available.

Every call is validated against the tool's parameter spec, time-limited, size-
limited, and recorded in `gateway.log` (and streamed to `on_event`) so a
decision can always be audited back to exactly what the agent saw.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
import urllib.robotparser
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlsplit

from app.sandbox.jail import JailLimits, build_command
from app.tools.extract import html_to_text, parse_rss
from app.tools.netguard import FetchError, SafeFetcher

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
PY_WORKER = Path(__file__).with_name("py_worker.py")
DEFAULT_UA = "airp-local-5070/0.2 (personal research; +https://github.com/everett6/airp-local-5070)"

Handler = Callable[["ToolGateway", dict[str, Any]], Awaitable[Any]]


class ToolInputError(ValueError):
    pass


@dataclass(frozen=True)
class Param:
    type: str  # "str" | "int" | "list[str]"
    description: str
    required: bool = False
    default: Any = None
    min: int | None = None
    max: int | None = None
    max_len: int = 500
    choices: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    params: dict[str, Param]
    handler: Handler
    live_only: bool = True
    timeout_s: float = 10.0

    def for_prompt(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "args": {k: f"{p.type}{'' if p.required else ' (optional)'} — {p.description}"
                         for k, p in self.params.items()}}


def validate_args(spec: ToolSpec, args: object) -> dict[str, Any]:
    if not isinstance(args, dict):
        raise ToolInputError("args must be an object")
    unknown = set(args) - set(spec.params)
    if unknown:
        raise ToolInputError(f"unknown args {sorted(unknown)}")
    out: dict[str, Any] = {}
    for name, p in spec.params.items():
        if name not in args or args[name] is None:
            if p.required:
                raise ToolInputError(f"missing required arg {name!r}")
            out[name] = p.default
            continue
        v = args[name]
        if p.type == "int":
            if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
                raise ToolInputError(f"{name} must be an integer")
            v = int(v)
            v = max(p.min, v) if p.min is not None else v
            v = min(p.max, v) if p.max is not None else v
        elif p.type == "str":
            if not isinstance(v, str) or not v.strip():
                raise ToolInputError(f"{name} must be a non-empty string")
            if len(v) > p.max_len:
                raise ToolInputError(f"{name} longer than {p.max_len} characters")
            if p.choices and v not in p.choices:
                raise ToolInputError(f"{name} must be one of {list(p.choices)}")
        elif p.type == "list[str]":
            if not isinstance(v, list) or not all(isinstance(x, str) and len(x) <= 40 for x in v) or len(v) > 10:
                raise ToolInputError(f"{name} must be a list of up to 10 short strings")
            if p.choices and not set(v) <= set(p.choices):
                raise ToolInputError(f"{name} entries must be in {list(p.choices)}")
        out[name] = v
    return out


def _ticker(v: str) -> str:
    t = v.strip().upper()
    if not TICKER_RE.match(t):
        raise ToolInputError(f"invalid ticker {v!r}")
    return t


# ---------------- tool implementations ----------------

async def _stock_news(gw: ToolGateway, a: dict[str, Any]) -> Any:
    t = _ticker(a["ticker"])
    r = await gw.fetcher.fetch(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={t}&region=US&lang=en-US")
    return {"ticker": t, "items": parse_rss(r.text, a["limit"])}


async def _news_search(gw: ToolGateway, a: dict[str, Any]) -> Any:
    q = a["query"]
    urls = [
        (f"https://news.google.com/rss/search?q={quote_plus(q + ' when:' + str(a['days']) + 'd')}"
         "&hl=en-US&gl=US&ceid=US:en"),
        f"https://www.bing.com/news/search?q={quote_plus(q)}&format=rss",
    ]
    feeds = await asyncio.gather(*(gw.fetcher.fetch(u) for u in urls), return_exceptions=True)
    items: dict[str, dict[str, Any]] = {}
    errors = []
    for f in feeds:
        if isinstance(f, BaseException):
            errors.append(str(f))
            continue
        for it in parse_rss(f.text, 30):
            key = re.sub(r"\W+", " ", it["title"].lower()).strip()[:80]
            if key and (key not in items or (it["fetchable"] and not items[key]["fetchable"])):
                items[key] = it
    ranked = sorted(items.values(), key=lambda x: x["published"] or "", reverse=True)[: a["limit"]]
    if not ranked and errors:
        raise FetchError("; ".join(errors))
    return {"query": q, "items": ranked}


async def _fetch_page(gw: ToolGateway, a: dict[str, Any]) -> Any:
    url = a["url"]
    if not await gw.robots_allowed(url):
        raise FetchError("disallowed by the site's robots.txt")
    r = await gw.fetcher.fetch(url, headers={"Accept": "text/html,text/plain;q=0.9"})
    if r.status != 200:
        raise FetchError(f"HTTP {r.status}")
    ctype = r.content_type.lower()
    if "html" in ctype:
        page = html_to_text(r.text, a["max_chars"])
    elif ctype.startswith("text/"):
        page = {"title": "", "published": "", "text": r.text[: a["max_chars"]]}
    else:
        raise FetchError(f"unsupported content type {ctype or 'unknown'}")
    return {"url": r.final_url, **page}


async def _price_history(gw: ToolGateway, a: dict[str, Any]) -> Any:
    t = _ticker(a["ticker"])
    rng = "3mo" if a["days"] <= 60 else "1y"
    r = await gw.fetcher.fetch(f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?range={rng}&interval=1d")
    try:
        res = json.loads(r.text)["chart"]["result"][0]
        pairs = [(s, c) for s, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"], strict=False)
                 if c is not None]
        stamps, closes = [s for s, _ in pairs], [c for _, c in pairs]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        raise FetchError(f"no price data for {t}") from e
    closes, stamps = closes[-a["days"]:], stamps[-a["days"]:]

    def ret(n: int) -> float | None:
        return round(closes[-1] / closes[-1 - n] - 1, 4) if len(closes) > n else None
    daily = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))][-20:]
    vol = (sum(x * x for x in daily) / len(daily)) ** 0.5 * 252 ** 0.5 if daily else None
    return {"ticker": t, "last_close": round(closes[-1], 2),
            "last_date": datetime.fromtimestamp(stamps[-1], UTC).date().isoformat(),
            "ret_1d": ret(1), "ret_5d": ret(5), "ret_20d": ret(20),
            "vol_20d_annualized": round(vol, 3) if vol else None,
            "closes": [round(c, 2) for c in closes[-30:]]}


async def _sec_filings(gw: ToolGateway, a: dict[str, Any]) -> Any:
    if not gw.sec_user_agent:
        raise FetchError("SEC access needs SEC_USER_AGENT (name and email) in backend/.env")
    t = _ticker(a["ticker"])
    hdr = {"User-Agent": gw.sec_user_agent}
    tickers = await gw.fetcher.fetch("https://www.sec.gov/files/company_tickers.json", headers=hdr,
                                     max_bytes=5_000_000)
    cik = next((int(v["cik_str"]) for v in json.loads(tickers.text).values() if v["ticker"] == t), None)
    if cik is None:
        raise FetchError(f"{t} not found in SEC company tickers")
    sub = await gw.fetcher.fetch(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=hdr,
                                 max_bytes=8_000_000)
    recent = json.loads(sub.text)["filings"]["recent"]
    forms = set(a["forms"])
    out = []
    for i, form in enumerate(recent["form"]):
        if form not in forms:
            continue
        acc = recent["accessionNumber"][i].replace("-", "")
        out.append({"form": form, "accepted": recent["acceptanceDateTime"][i], "filed": recent["filingDate"][i],
                    "items": recent.get("items", [""] * (i + 1))[i],
                    "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{recent['primaryDocument'][i]}"})
        if len(out) >= a["limit"]:
            break
    return {"ticker": t, "cik": cik, "filings": out}


async def _web_search(gw: ToolGateway, a: dict[str, Any]) -> Any:
    if not gw.brave_api_key:
        raise FetchError("web_search needs BRAVE_SEARCH_API_KEY in backend/.env")
    r = await gw.fetcher.fetch(
        f"https://api.search.brave.com/res/v1/web/search?q={quote_plus(a['query'])}&count={a['limit']}",
        headers={"X-Subscription-Token": gw.brave_api_key, "Accept": "application/json"}, use_cache=True)
    data = json.loads(r.text)
    return {"query": a["query"], "items": [
        {"title": x.get("title", "")[:300], "url": x.get("url", ""), "snippet": re.sub(r"<[^>]+>", "", x.get(
            "description", ""))[:400], "age": x.get("age")} for x in data.get("web", {}).get("results", [])]}


async def _python(gw: ToolGateway, a: dict[str, Any]) -> Any:
    limits = JailLimits(memory_mb=256, cpu_seconds=10, open_files=16, file_size_mb=1)
    cmd = build_command(allow_unjailed=gw.allow_unjailed_python, worker=PY_WORKER, limits=limits)
    proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(json.dumps({"code": a["code"]}).encode()),
                                          gw.python_timeout_s)
    except TimeoutError as e:
        proc.kill()
        await proc.wait()
        raise FetchError(f"python timed out after {gw.python_timeout_s:g}s") from e
    if not out:
        raise FetchError(f"python sandbox failed: {err.decode(errors='replace')[-300:]}")
    return json.loads(out)


TOOLS: dict[str, ToolSpec] = {s.name: s for s in [
    ToolSpec("stock_news", "Latest headlines for one stock ticker (Yahoo Finance), newest first.",
             {"ticker": Param("str", "e.g. NVDA", required=True, max_len=10),
              "limit": Param("int", "max items", default=10, min=1, max=20)}, _stock_news, timeout_s=8),
    ToolSpec("news_search", "Search recent news headlines across outlets (Google News + Bing News).",
             {"query": Param("str", "search words", required=True, max_len=200),
              "days": Param("int", "how many days back", default=7, min=1, max=30),
              "limit": Param("int", "max items", default=10, min=1, max=25)}, _news_search, timeout_s=8),
    ToolSpec("fetch_page", "Read the main text of a web page (e.g. a news article URL from a search result).",
             {"url": Param("str", "http(s) URL", required=True, max_len=2000),
              "max_chars": Param("int", "max characters of text", default=3000, min=500, max=8000)},
             _fetch_page, timeout_s=10),
    ToolSpec("price_history", "Recent daily closes and simple return/volatility stats for a ticker.",
             {"ticker": Param("str", "e.g. NVDA", required=True, max_len=10),
              "days": Param("int", "trading days", default=60, min=5, max=250)}, _price_history, timeout_s=8),
    ToolSpec("sec_filings", "Most recent SEC filings for a ticker, with acceptance timestamps and links.",
             {"ticker": Param("str", "e.g. NVDA", required=True, max_len=10),
              "forms": Param("list[str]", "form types", default=["8-K", "10-Q", "10-K"],
                             choices=("8-K", "10-Q", "10-K", "6-K", "20-F", "S-1", "4", "SC 13D", "DEF 14A")),
              "limit": Param("int", "max filings", default=8, min=1, max=20)}, _sec_filings, timeout_s=10),
    ToolSpec("web_search", "General web search (Brave Search API; only if an API key is configured).",
             {"query": Param("str", "search words", required=True, max_len=300),
              "limit": Param("int", "max results", default=8, min=1, max=20)}, _web_search, timeout_s=8),
    ToolSpec("python", "Run a short Python calculation (stdlib only, no network or files, 10 s). Print results.",
             {"code": Param("str", "Python source", required=True, max_len=4000)}, _python,
             live_only=False, timeout_s=15),
]}


@dataclass
class ToolGateway:
    mode: str  # "live" | "backtest"
    fetcher: SafeFetcher
    sec_user_agent: str = ""
    brave_api_key: str = ""
    max_calls_per_batch: int = 8
    max_result_chars: int = 6000
    allow_unjailed_python: bool = False
    python_timeout_s: float = 10.0
    on_event: Callable[[dict[str, Any]], None] | None = None
    log: list[dict[str, Any]] = field(default_factory=list)
    _robots: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in ("live", "backtest"):
            raise ValueError("mode must be 'live' or 'backtest'")

    @classmethod
    def from_env(cls, mode: str, **kw: Any) -> ToolGateway:
        env = _read_env_file(Path(__file__).resolve().parents[2] / ".env")
        return cls(mode=mode, fetcher=SafeFetcher(os.environ.get("WEB_USER_AGENT") or env.get("WEB_USER_AGENT")
                                                  or DEFAULT_UA),
                   sec_user_agent=os.environ.get("SEC_USER_AGENT") or env.get("SEC_USER_AGENT", ""),
                   brave_api_key=os.environ.get("BRAVE_SEARCH_API_KEY") or env.get("BRAVE_SEARCH_API_KEY", ""),
                   **kw)

    def available(self) -> list[ToolSpec]:
        out = [s for s in TOOLS.values() if self.mode == "live" or not s.live_only]
        return [s for s in out if (s.name != "web_search" or self.brave_api_key)
                and (s.name != "sec_filings" or self.sec_user_agent)]

    def specs_for_prompt(self) -> list[dict[str, Any]]:
        return [s.for_prompt() for s in self.available()]

    async def robots_allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        cached = self._robots.get(origin)
        if cached is None or time.time() - cached[0] > 3600:
            parser: urllib.robotparser.RobotFileParser | None = None
            try:
                r = await self.fetcher.fetch(origin + "/robots.txt", max_bytes=500_000)
                if r.status == 200:
                    parser = urllib.robotparser.RobotFileParser()
                    parser.parse(r.text.splitlines())
                elif r.status >= 500:  # RFC 9309: server errors mean "assume disallowed"
                    parser = urllib.robotparser.RobotFileParser()
                    parser.parse(["User-agent: *", "Disallow: /"])
            except FetchError:
                parser = None  # unreachable robots.txt: fetching the page itself will fail or succeed on its own
            cached = (time.time(), parser)
            self._robots[origin] = cached
        return cached[1] is None or cached[1].can_fetch(self.fetcher.user_agent, url)

    async def _one(self, req: dict[str, Any]) -> dict[str, Any]:
        name = req.get("tool")
        entry: dict[str, Any] = {"id": req.get("id"), "tool": name, "args": req.get("args"),
                                 "started": datetime.now(UTC).isoformat(timespec="milliseconds")}
        t0 = time.monotonic()
        try:
            spec = TOOLS.get(str(name))
            if spec is None or spec not in self.available():
                why = "not available in backtest mode (the web already contains the future)" \
                    if spec is not None and spec.live_only and self.mode == "backtest" else "unknown tool"
                raise ToolInputError(f"{name}: {why}")
            args = validate_args(spec, req.get("args", {}))
            entry["args"] = args
            result = await asyncio.wait_for(spec.handler(self, args), spec.timeout_s)
            text = json.dumps(result, ensure_ascii=False, default=str)
            if len(text) > self.max_result_chars:
                text = text[: self.max_result_chars] + "…(truncated)"
            out: dict[str, Any] = {"ok": True, "result": text}
        except (ToolInputError, FetchError) as e:
            out = {"ok": False, "error": str(e)[:500]}
        except TimeoutError:
            out = {"ok": False, "error": "timed out"}
        except Exception as e:  # noqa: BLE001 - web data is unpredictable; a tool bug must not crash the run
            out = {"ok": False, "error": f"{type(e).__name__}: {e}"[:500]}
        entry.update(ok=out["ok"], elapsed_ms=int((time.monotonic() - t0) * 1000),
                     chars=len(out.get("result", "")), error=out.get("error"),
                     preview=out.get("result", "")[:200])
        self.log.append(entry)
        if self.on_event:
            self.on_event(entry)
        return out

    async def execute(self, requests: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        run, extra = requests[: self.max_calls_per_batch], requests[self.max_calls_per_batch:]
        results = await asyncio.gather(*(self._one(r) for r in run))
        out = {str(r.get("id")): res for r, res in zip(run, results, strict=True)}
        for r in extra:
            out[str(r.get("id"))] = {"ok": False, "error": f"skipped: at most {self.max_calls_per_batch} calls per step"}
        return out

    async def aclose(self) -> None:
        await self.fetcher.aclose()


def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out
