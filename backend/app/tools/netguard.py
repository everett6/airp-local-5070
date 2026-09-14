"""
Guarded HTTP fetching for agent tools.

Web content and URLs chosen by an LLM are untrusted, so every request:
  - must be http(s) on a default-ish port, to a host that resolves ONLY to
    public addresses: no loopback (Ollama!), LAN, link-local, or metadata IPs;
    every redirect hop is re-checked
  - is capped in size (streamed, aborted past the limit) and time
  - is rate-limited per host (SEC asks for at most 10 requests/second)
  - is cached for a short TTL so repeated research is fast

Residual risk, documented rather than hidden: a hostile DNS server could
answer differently between our check and httpx's own lookup (DNS rebinding).
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

Resolver = Callable[[str], Awaitable[list[str]]]

# hosts with published or sensible request-rate expectations (seconds between requests)
HOST_MIN_INTERVAL = {"www.sec.gov": 0.12, "data.sec.gov": 0.12, "efts.sec.gov": 0.12}


class FetchError(RuntimeError):
    pass


async def system_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({str(i[4][0]) for i in infos})


def is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return bool(addr.is_global) and not addr.is_multicast


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    elapsed_ms: int
    from_cache: bool = False
    fetched_at: float = field(default_factory=time.time)

    @property
    def text(self) -> str:
        charset = "utf-8"
        if "charset=" in self.content_type:
            charset = self.content_type.split("charset=")[-1].split(";")[0].strip() or "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


class SafeFetcher:
    def __init__(self, user_agent: str, *, timeout_s: float = 6.0, max_bytes: int = 2_000_000,
                 max_redirects: int = 4, cache_ttl_s: float = 300.0, cache_size: int = 512,
                 per_host_concurrency: int = 4, resolver: Resolver = system_resolver,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.user_agent = user_agent
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.cache_ttl_s = cache_ttl_s
        self._cache: OrderedDict[tuple[str, str], FetchResult] = OrderedDict()
        self._cache_size = cache_size
        self._resolver = resolver
        self._host_sem: dict[str, asyncio.Semaphore] = {}
        self._host_last: dict[str, float] = {}
        self._host_lock: dict[str, asyncio.Lock] = {}
        self._per_host = per_host_concurrency
        self._client = httpx.AsyncClient(
            transport=transport, follow_redirects=False, timeout=timeout_s,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def check_url(self, url: str) -> str:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise FetchError(f"blocked scheme {parts.scheme!r}")
        host = parts.hostname
        if not host:
            raise FetchError("URL has no host")
        if parts.port not in (None, 80, 443, 8080, 8443):
            raise FetchError(f"blocked port {parts.port}")
        if parts.username or parts.password:
            raise FetchError("credentials in URL are not allowed")
        try:
            ips = [host] if _is_ip_literal(host) else await asyncio.wait_for(self._resolver(host), self.timeout_s)
        except (OSError, TimeoutError) as e:
            raise FetchError(f"cannot resolve {host}: {e}") from e
        if not ips or not all(is_public_ip(ip) for ip in ips):
            raise FetchError(f"blocked non-public address for {host}")
        return host

    async def _pace(self, host: str) -> None:
        interval = HOST_MIN_INTERVAL.get(host)
        if not interval:
            return
        lock = self._host_lock.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._host_last.get(host, 0.0) + interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._host_last[host] = time.monotonic()

    async def fetch(self, url: str, *, headers: dict[str, str] | None = None, max_bytes: int | None = None,
                    use_cache: bool = True) -> FetchResult:
        key = (url, repr(sorted((headers or {}).items())))
        hit = self._cache.get(key)
        if use_cache and hit and time.time() - hit.fetched_at < self.cache_ttl_s:
            self._cache.move_to_end(key)
            return FetchResult(hit.url, hit.final_url, hit.status, hit.content_type, hit.body, 0, True,
                               hit.fetched_at)
        limit = max_bytes or self.max_bytes
        t0 = time.monotonic()
        current = url
        for _ in range(self.max_redirects + 1):
            host = await self.check_url(current)
            sem = self._host_sem.setdefault(host, asyncio.Semaphore(self._per_host))
            async with sem:
                await self._pace(host)
                try:
                    async with self._client.stream("GET", current, headers=headers) as resp:
                        if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
                            current = urljoin(current, resp.headers["location"])
                            continue
                        declared = int(resp.headers.get("content-length") or 0)
                        if declared > limit:
                            raise FetchError(f"response too large ({declared} bytes > {limit})")
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in resp.aiter_bytes():
                            size += len(chunk)
                            if size > limit:
                                raise FetchError(f"response too large (> {limit} bytes)")
                            chunks.append(chunk)
                        result = FetchResult(url, current, resp.status_code,
                                             resp.headers.get("content-type", ""), b"".join(chunks),
                                             int((time.monotonic() - t0) * 1000))
                except httpx.TimeoutException as e:
                    raise FetchError(f"timed out after {self.timeout_s}s") from e
                except httpx.HTTPError as e:
                    raise FetchError(f"{type(e).__name__}: {e}") from e
            if result.status == 200 and use_cache:
                self._cache[key] = result
                self._cache.move_to_end(key)
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
            return result
        raise FetchError(f"too many redirects (> {self.max_redirects})")


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True
