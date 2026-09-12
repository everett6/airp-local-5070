"""
The sandbox clock is what makes backtesting *honest*. Without it, nothing
stops a "historical" research run from calling a mock connector that
secretly bases its output on the real wall-clock date, or a real connector
from returning a filing published after the date you're pretending to stand
at — either way, you'd get a backtest that looks great and means nothing
(classic lookahead bias).

The mechanism:
  - `SandboxClock.activate(as_of)` sets a `contextvars.ContextVar` for the
    duration of one pipeline run. Every `DataConnector.fetch()` call reads
    the active clock (if any) and calls `enforce_point_in_time()` on the
    timestamp of whatever it's about to return.
  - If a connector tries to hand back something timestamped after `as_of`,
    that's a `LookaheadViolation` — a hard error, not a warning, because a
    backtest that silently drops or clips a lookahead violation is exactly
    the failure mode this exists to prevent from going unnoticed.
  - Outside a sandbox run (normal "live" research), no clock is active and
    `enforce_point_in_time` is a no-op — this has zero effect on the regular
    research pipeline.
  - `contextvars` (not a global variable) so this is safe under concurrent
    runs (asyncio tasks each see their own active clock, matching how
    `MessageBus` already scopes state per `run_id`).
"""
from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime


class LookaheadViolation(Exception):
    """Raised when a connector or agent tries to use information that would
    not have been available as of the sandbox's `as_of` timestamp. This is a
    correctness bug in a connector/mock, not a normal runtime condition —
    it should never be caught-and-ignored anywhere in the pipeline."""

    def __init__(self, as_of: datetime, attempted_timestamp: datetime, source: str) -> None:
        self.as_of = as_of
        self.attempted_timestamp = attempted_timestamp
        self.source = source
        super().__init__(
            f"Lookahead violation in {source}: attempted to use data timestamped "
            f"{attempted_timestamp.isoformat()}, which is after the sandbox cutoff "
            f"of {as_of.isoformat()}. A backtest run must never see this."
        )


@dataclass(frozen=True)
class SandboxClock:
    as_of: datetime
    run_id: str

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            object.__setattr__(self, "as_of", self.as_of.replace(tzinfo=UTC))


_active_clock: contextvars.ContextVar[SandboxClock | None] = contextvars.ContextVar(
    "airp_sandbox_clock", default=None
)


def get_active_clock() -> SandboxClock | None:
    return _active_clock.get()


@contextmanager
def sandbox_scope(as_of: datetime, run_id: str) -> Iterator[SandboxClock]:
    """Activate a sandbox clock for the duration of a `with` block (typically
    wrapping one full pipeline run). Nested/concurrent scopes are independent
    thanks to contextvars — this is safe to use from multiple asyncio tasks
    backtesting different dates simultaneously."""
    clock = SandboxClock(as_of=as_of, run_id=run_id)
    token = _active_clock.set(clock)
    try:
        yield clock
    finally:
        _active_clock.reset(token)


def enforce_point_in_time(candidate_timestamp: datetime, source: str) -> None:
    """Call this with the timestamp of any data a connector is about to
    return. No-op if no sandbox is active (normal live research). Raises
    `LookaheadViolation` if a sandbox is active and the timestamp is after
    its `as_of` cutoff."""
    clock = get_active_clock()
    if clock is None:
        return
    ts = candidate_timestamp if candidate_timestamp.tzinfo else candidate_timestamp.replace(tzinfo=UTC)
    if ts > clock.as_of:
        raise LookaheadViolation(as_of=clock.as_of, attempted_timestamp=ts, source=source)


def current_effective_time() -> datetime:
    """What connectors/agents should treat as 'now' — the sandbox cutoff if
    one is active, otherwise real wall-clock time. Mock connectors MUST call
    this instead of `datetime.now()` when generating synthetic 'as of today'
    data, or a backtest at any past date would silently generate data as if
    it were today — defeating the entire point of the sandbox."""
    clock = get_active_clock()
    return clock.as_of if clock is not None else datetime.now(UTC)
