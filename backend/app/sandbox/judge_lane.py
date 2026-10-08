"""One Bonsai lane shared by companies judged at the same time (docs/AI2_SPEED_LESSONS.md, 7 Oct 2026).

Bonsai is served one request at a time, and about a third of a company's judge time is web lookups that leave the GPU
idle. Judging two companies at once fills those gaps. Time spent waiting for the lane is not the model's time, so
`budget()` timeouts are paused while their task waits and resume with what they had left: queueing never times out
a call that would have finished alone.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

_ACTIVE: ContextVar[tuple[asyncio.Timeout, ...]] = ContextVar("judge_budgets", default=())


@asynccontextmanager
async def budget(seconds: float) -> AsyncIterator[asyncio.Timeout]:
    """asyncio.timeout(seconds), except that time this task spends queued for a Lane does not count."""
    async with asyncio.timeout(seconds) as cm:
        token = _ACTIVE.set(_ACTIVE.get() + (cm,))
        try:
            yield cm
        finally:
            _ACTIVE.reset(token)


class Lane:
    """Wraps the judge LLM: one call at a time, with the caller's budgets paused while it waits its turn."""

    def __init__(self, llm: Any) -> None:
        self.llm = llm
        self.lock = asyncio.Lock()
        self.waited_s = 0.0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.llm, name)

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        if not self.lock.locked():
            async with self.lock:
                return await self.llm(*args, **kwargs)
        held = [(cm, cm.when()) for cm in _ACTIVE.get() if cm.when() is not None]
        for cm, _ in held:
            cm.reschedule(None)
        t0 = loop.time()
        try:
            await self.lock.acquire()
        finally:
            waited = loop.time() - t0
            self.waited_s += waited
            for cm, when in held:
                cm.reschedule(when + waited)
        try:
            return await self.llm(*args, **kwargs)
        finally:
            self.lock.release()


async def run_overlapped(rows: list[Any], work: Any, width: int) -> None:
    """await work(index, row) for every row, at most `width` at once. Every row finishes before an error is raised,
    so the caller's cleanup (stopping the server) never runs under a row still in flight."""
    sem = asyncio.Semaphore(max(1, width))

    async def one(index: int, row: Any) -> None:
        async with sem:
            await work(index, row)

    results = await asyncio.gather(*(one(i, r) for i, r in enumerate(rows)), return_exceptions=True)
    for r in results:
        if isinstance(r, BaseException):
            raise r
