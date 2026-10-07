"""O1 research quality (docs/PLAN_60_V2.md, "O1 addendum"): syndicated pages count once (#31), facts carry a
freshness tag for Bonsai (#32), and each research record gets a timestamp audit (#29)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

SEC = ("sec.gov",)
JACCARD = 0.5
OLD_DAYS = 30


def _shingles(text: str, n: int = 5) -> set[int]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {hash(" ".join(words[i:i + n])) for i in range(max(0, len(words) - n + 1))}


def syndication_groups(pages: list[dict[str, Any]], threshold: float = JACCARD) -> list[list[int]]:
    """Indexes of pages grouped by heavy text overlap (the same press release on several sites is one group)."""
    sh = [_shingles(str(p.get("text", ""))) for p in pages]
    parent = list(range(len(pages)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(pages)):
        for j in range(i + 1, len(pages)):
            if sh[i] and sh[j] and len(sh[i] & sh[j]) / len(sh[i] | sh[j]) >= threshold:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(pages)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def independent_sources(pages: list[dict[str, Any]]) -> int:
    """Groups that include a non-SEC page: the number of independent non-SEC sources."""
    return sum(1 for g in syndication_groups(pages)
               if any(not str(pages[i].get("publisher", "")).endswith(SEC) for i in g))


def tag_facts(facts: list[dict[str, Any]], known: dict[str, str], as_of: str,
              key: Any) -> list[dict[str, Any]]:
    """#32: NEW (not in memory before this run), KNOWN since <date>, or OLD (dated over 30 days before as_of)."""
    cutoff = (datetime.fromisoformat(as_of) - timedelta(days=OLD_DAYS)).date().isoformat()
    out = []
    for f in facts:
        d = str(f.get("date") or "")[:10]
        k = key(str(f.get("text", "")))
        tag = "OLD" if d and d < cutoff else f"KNOWN since {known[k]}" if k in known else "NEW"
        out.append({**f, "tag": tag})
    return out


def audit(record: dict[str, Any]) -> list[str]:
    """#29: timestamps must run published <= retrieved <= decided, and no fact may postdate the research time."""
    out = []
    as_of, decided = record.get("as_of"), record.get("decided_at")
    for p in (record.get("article_retrieval") or {}).get("pages", []):
        pub, got = p.get("published") or p.get("discovered_published"), p.get("retrieved_at")
        if pub and got and str(pub) > str(got):
            out.append(f"page published after it was retrieved: {p.get('url')}")
        if got and decided and str(got) > str(decided):
            out.append(f"page retrieved after the decision: {p.get('url')}")
    for f in (record.get("wide_brief") or {}).get("facts", []):
        if as_of and f.get("date") and str(f["date"])[:10] > str(as_of)[:10]:
            out.append(f"fact dated after the research time: {str(f.get('text'))[:80]}")
    return out
