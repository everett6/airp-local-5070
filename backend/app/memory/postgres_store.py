"""
Dev-friendly in-process implementation of MemoryStore, plus the retrieval
scoring function. The production implementation swaps `InMemoryStore` for a
Postgres+pgvector-backed store with the identical interface (see
db/schema.sql -> `memory_records` table with a `vector` column); nothing
above this file needs to change when that swap happens.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from app.memory.interface import MemoryQueryResult, MemoryRecord, MemoryStore


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class InMemoryStore(MemoryStore):
    """Reference implementation used in tests and local dev. Persists nothing
    across process restarts — that's the point of it being replaceable."""

    def __init__(self) -> None:
        self._by_namespace: dict[str, list[MemoryRecord]] = defaultdict(list)
        self._by_id: dict[str, MemoryRecord] = {}

    async def write(self, record: MemoryRecord) -> None:
        self._by_namespace[record.namespace].append(record)
        self._by_id[record.memory_id] = record

    async def query(
        self, namespace: str, query: str, tags: list[str], top_k: int = 5
    ) -> list[MemoryQueryResult]:
        candidates = self._by_namespace.get(namespace, [])
        scored: list[MemoryQueryResult] = []
        query_tokens = set(query.lower().split())

        for rec in candidates:
            tag_overlap = len({t.lower() for t in tags} & {t.lower() for t in rec.retrieval_tags})
            text_overlap = len(query_tokens & set(str(rec.content).lower().split()))
            score = tag_overlap * 2 + text_overlap * 0.1
            if score > 0:
                scored.append(MemoryQueryResult(record=rec, relevance_score=score))

        scored.sort(key=lambda r: r.relevance_score, reverse=True)
        return scored[:top_k]

    async def record_outcome(self, memory_id: str, outcome: dict[str, Any]) -> None:
        rec = self._by_id.get(memory_id)
        if rec is None:
            raise KeyError(f"no memory record with id {memory_id}")
        updated = MemoryRecord(**{**rec.__dict__, "outcome": outcome})
        self._by_id[memory_id] = updated
        namespace_list = self._by_namespace[rec.namespace]
        for i, r in enumerate(namespace_list):
            if r.memory_id == memory_id:
                namespace_list[i] = updated
                break
