"""Arms B/C: an input longer than the context window is recorded as unparsed instead of stopping the run."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import llm_fields


def _err(status: int, text: str) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://x/api/chat")
    return httpx.HTTPStatusError("e", request=req, response=httpx.Response(status, text=text, request=req))


def test_overflow_is_unparsed_other_errors_raise() -> None:
    async def over(s: str, u: str) -> str:
        raise _err(400, '{"error":"request (8436 tokens) exceeds the available context size (8192 tokens)"}')

    async def down(s: str, u: str) -> str:
        raise _err(500, "boom")

    async def ok(s: str, u: str) -> str:
        return "{}"
    assert asyncio.run(llm_fields.ask(over, "s", "u")) == ("", True)
    assert asyncio.run(llm_fields.ask(ok, "s", "u")) == ("{}", False)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(llm_fields.ask(down, "s", "u"))
    rec = llm_fields.verify(llm_fields.parse(""), "text", llm_fields.FIELDS_R)
    assert rec["parsed"] is False and rec["claimed"] == 0
