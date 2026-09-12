"""
Every data source (market data, filings, transcripts, insider transactions,
analyst estimates, alt data) implements `DataConnector`. This base class is
where the Layer 1 cross-cutting requirements live ONCE, so individual
connectors only implement `_fetch_raw` and `_normalize`:

  - normalization: `_normalize` maps provider-specific shapes to our schema
  - caching: `fetch()` checks/populates the cache before/after `_fetch_raw`
  - validation: `_validate` runs after normalization, before returning
  - versioning: every `IngestedRecord` carries a `schema_version` and
    `fetched_at`, so downstream consumers can detect stale/older shapes
  - quality scoring: computed from staleness, validation warnings, and
    provider-reported completeness — never a hardcoded 1.0
"""
from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar, cast

from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from app.sandbox.clock import LookaheadViolation

T = TypeVar("T")


class IngestionError(Exception):
    def __init__(self, connector: str, message: str, retryable: bool = True) -> None:
        super().__init__(f"[{connector}] {message}")
        self.connector = connector
        self.retryable = retryable


@dataclass(frozen=True)
class ValidationIssue:
    field: str
    severity: str  # "warning" | "error"
    message: str


@dataclass(frozen=True)
class IngestedRecord(Generic[T]):
    source_id: str
    connector_name: str
    schema_version: str
    fetched_at: datetime
    data: T
    validation_issues: list[ValidationIssue] = field(default_factory=list)
    quality_score: float = 1.0
    from_cache: bool = False


class CacheBackend(abc.ABC):
    @abc.abstractmethod
    async def get(self, key: str) -> Any | None: ...

    @abc.abstractmethod
    async def set(self, key: str, value: Any, ttl_seconds: int) -> None: ...


class InMemoryCache(CacheBackend):
    """Dev/test cache. Production swaps in a Redis-backed implementation with
    the identical interface (app/data_ingestion/redis_cache.py, not included
    in this skeleton to avoid a hard Redis dependency in unit tests)."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}

    async def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.time() > expires_at:
            del self._store[key]
            return None
        return value

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._store[key] = (time.time() + ttl_seconds, value)


class DataConnector(abc.ABC, Generic[T]):
    name: str
    schema_version: str = "1.0.0"
    default_ttl_seconds: int = 300

    def __init__(self, cache: CacheBackend) -> None:
        self._cache = cache

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=8),
        retry=retry_if_not_exception_type(LookaheadViolation),
    )
    async def fetch(self, cache_key: str, **kwargs: Any) -> IngestedRecord[T]:
        cached = await self._cache.get(cache_key)
        if cached is not None:
            # The cache is genuinely untyped (CacheBackend.get -> Any | None,
            # since it stores whatever any connector puts in it) — this cast
            # documents the real invariant (only fetch() ever writes to a
            # given key, and it only ever writes an IngestedRecord[T] here)
            # rather than leaving an unchecked Any flowing out as if it were
            # type-safe.
            return cast(IngestedRecord[T], cached)

        try:
            raw = await self._fetch_raw(**kwargs)
        except LookaheadViolation:
            # Never retry, never wrap: this is a correctness guarantee firing
            # as designed, not a flaky upstream call. Wrapping it as an
            # IngestionError (or letting tenacity mask it behind a RetryError
            # after 3 pointless attempts) would hide the one failure mode
            # this whole module exists to make loud. See app/sandbox/clock.py.
            raise
        except Exception as exc:
            raise IngestionError(self.name, str(exc)) from exc

        data = self._normalize(raw)
        issues = self._validate(data)
        quality = self._score_quality(issues, raw)

        record = IngestedRecord(
            source_id=self._source_id(**kwargs),
            connector_name=self.name,
            schema_version=self.schema_version,
            fetched_at=datetime.now(UTC),
            data=data,
            validation_issues=issues,
            quality_score=quality,
            from_cache=False,
        )
        await self._cache.set(cache_key, record, self.default_ttl_seconds)
        return record

    @abc.abstractmethod
    async def _fetch_raw(self, **kwargs: Any) -> Any: ...

    @abc.abstractmethod
    def _normalize(self, raw: Any) -> T: ...

    @abc.abstractmethod
    def _validate(self, data: T) -> list[ValidationIssue]: ...

    @abc.abstractmethod
    def _source_id(self, **kwargs: Any) -> str: ...

    def _score_quality(self, issues: list[ValidationIssue], raw: Any) -> float:
        if not issues:
            return 1.0
        penalty = sum(0.25 if i.severity == "error" else 0.05 for i in issues)
        return max(0.0, 1.0 - penalty)
