"""GPU priority: the forward runner raises a flag; other processes' OllamaLLMs unload and wait (app/sandbox/gpu_lock.py)."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import pytest

from app.sandbox import gpu_lock
from app.sandbox import walkforward as wf


def test_flag_holder_does_not_yield_and_flag_is_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(gpu_lock.PRIORITY_ENV, raising=False)
    p = tmp_path / "prio.flag"
    assert not gpu_lock.priority_wanted(p)
    with gpu_lock.gpu_priority("forward", path=p):
        assert p.exists() and not gpu_lock.priority_wanted(p)  # the holder (and its children) never yield
        monkeypatch.delenv(gpu_lock.PRIORITY_ENV)
        assert gpu_lock.priority_wanted(p)                      # another process sees it (a live pid)
        monkeypatch.setenv(gpu_lock.PRIORITY_ENV, "1")
    assert not p.exists() and os.environ.get(gpu_lock.PRIORITY_ENV) is None


def test_dead_holder_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(gpu_lock.PRIORITY_ENV, raising=False)
    p = tmp_path / "prio.flag"
    p.write_text("999999999 forward\n")
    assert not gpu_lock.priority_wanted(p)
    p.write_text("garbage")
    assert not gpu_lock.priority_wanted(p)


def test_llm_unloads_and_waits_while_priority_is_wanted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        if req.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(200, json={"message": {"content": '{"x": 1}'}, "prompt_eval_count": 10})

    monkeypatch.setattr(wf, "RESULTS", tmp_path)
    flags = iter([True, True, True, False, False])
    monkeypatch.setattr(wf, "priority_wanted", lambda: next(flags, False))
    llm = wf.OllamaLLM("m", concurrency=2, cache=False)
    llm._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def go() -> str:
        await llm._yield_to_priority(poll_s=0.001)
        return await llm("s", "u")
    assert asyncio.run(go()) == '{"x": 1}'
    assert llm.yields == 1 and seen[0] == "/api/generate"  # unloaded before any further request
