"""
Long-term memory: past theses, reports, recommendations, mistakes, market
reactions, earnings outcomes, prediction accuracy. Retrieval-first design —
agents query a small top-k relevant set instead of the system growing prompt
context over time, per Layer 3's explicit requirement.

`MemoryStore` is a Protocol; `PostgresMemoryStore` (postgres_store.py) is the
production implementation using pgvector for semantic retrieval plus
structured columns for exact filtering (ticker, outcome, date range).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol


class MemoryKind(str, Enum):
    THESIS = "thesis"
    REPORT = "report"
    RECOMMENDATION = "recommendation"
    MISTAKE = "mistake"
    MARKET_REACTION = "market_reaction"
    EARNINGS_OUTCOME = "earnings_outcome"
    PREDICTION_ACCURACY = "prediction_accuracy"


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    kind: MemoryKind
    namespace: str            # typically an AgentRole value, or "system"
    ticker: str | None
    timestamp: datetime
    confidence: float
    source: str               # run_id or external source that generated this memory
    citations: list[str]      # evidence_ids
    content: dict             # structured payload (schema depends on `kind`)
    outcome: dict | None   # filled in later once the real-world result is known
    retrieval_tags: list[str]
    embedding: list[float] | None = None


@dataclass(frozen=True)
class MemoryQueryResult:
    record: MemoryRecord
    relevance_score: float


class MemoryStore(Protocol):
    async def write(self, record: MemoryRecord) -> None: ...

    async def query(
        self, namespace: str, query: str, tags: list[str], top_k: int = 5
    ) -> list[MemoryQueryResult]: ...

    async def record_outcome(self, memory_id: str, outcome: dict) -> None:
        """Called once ground truth is known (e.g. earnings actually reported,
        price moved). This is what powers 'continuously learns from previous
        trades' — accuracy stats are computed FROM this data by
        `retrieval.py::prediction_accuracy_report`, not asserted by an LLM."""
        ...
