"""Turn fetched feeds and pages into compact text an LLM can use (stdlib only)."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlsplit

_SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside", "iframe", "button"}
_BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "blockquote"}


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self.published = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            a = {k: (v or "") for k, v in attrs}
            prop = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if prop in ("article:published_time", "datepublished", "pubdate", "date") and not self.published:
                self.published = a.get("content", "")
            if prop == "og:title" and not self.title:
                self.title = a.get("content", "")
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title and not self.title:
            self.title = data.strip()
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str, max_chars: int = 6000) -> dict[str, str]:
    p = _TextParser()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001, S110 - malformed markup: keep whatever was parsed
        pass
    text = "".join(p.parts)
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    # drop menu-like fragments: very short lines are mostly navigation
    kept = [ln for ln in lines if len(ln) >= 40 or (ln and ln[-1:] in ".!?:")]
    body = "\n".join(kept)
    return {"title": p.title[:300], "published": p.published[:40], "text": body[:max_chars]}


def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s)
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _unwrap_bing(link: str) -> str:
    parts = urlsplit(link)
    if parts.hostname and parts.hostname.endswith("bing.com") and "apiclick" in parts.path:
        return parse_qs(parts.query).get("url", [link])[0]
    return link


def parse_rss(xml_text: str, max_items: int = 20) -> list[dict[str, Any]]:
    try:
        if "<!ENTITY" in xml_text[:10000]:
            return []  # feeds never need entity declarations; refusing them rules out expansion attacks
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items = []
    for it in root.iter("item"):
        title = _strip_tags(it.findtext("title") or "")
        link = _unwrap_bing((it.findtext("link") or "").strip())
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        if not source:
            source = urlsplit(link).hostname or ""
        published = _parse_date(it.findtext("pubDate"))
        items.append({
            "title": title[:300], "url": link, "source": source,
            "published": published.isoformat() if published else None,
            "summary": _strip_tags(it.findtext("description") or "")[:400],
            # Google News links are redirect tokens, not the article itself
            "fetchable": not (urlsplit(link).hostname or "").endswith("news.google.com"),
        })
        if len(items) >= max_items:
            break
    return items
