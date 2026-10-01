"""The label engine: Ollama's bundled llama-server with parallel slots (PLAN_60_V2, B4b "Engine note").
No GPU and no server: the HTTP side is a mock transport and the process is a stand-in."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import llm_fields

from app.sandbox import llamacpp_client as lc
from app.sandbox.walkforward import GPUFallbackError, OllamaLLM


class FakeProc:
    pid = 4242
    returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    def wait(self) -> int | None:
        return self.returncode


def make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: Any = None, up_before: bool = False,
         **kw: Any) -> tuple[lc.LlamaServerLLM, dict[str, Any]]:
    monkeypatch.setattr(lc, "RESULTS", tmp_path)
    seen: dict[str, Any] = {"bodies": [], "up": up_before, "procs": [], "live": 0, "peak": 0}

    async def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/health":
            return httpx.Response(200 if seen["up"] else 503, json={})
        seen["bodies"].append(json.loads(req.content))
        seen["live"] += 1
        seen["peak"] = max(seen["peak"], seen["live"])
        await asyncio.sleep(0.01)
        seen["live"] -= 1
        if reply is not None:
            return reply
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"a": 1}'}}],
                                         "usage": {"prompt_tokens": 100, "completion_tokens": 5}})

    llm = lc.LlamaServerLLM("bonsai-27b:latest", require_gpu=False, log=tmp_path / "srv.log",
                            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), **kw)

    def spawn() -> FakeProc:
        p = FakeProc()
        seen["procs"].append(p)
        seen["up"] = True
        return p
    monkeypatch.setattr(llm, "_spawn", spawn)
    return llm, seen


def test_blob_path_reads_ollamas_manifest(tmp_path: Path) -> None:
    m = tmp_path / "manifests" / "registry.ollama.ai" / "library" / "bonsai-27b"
    m.mkdir(parents=True)
    (m / "latest").write_text(json.dumps({"layers": [
        {"mediaType": "application/vnd.ollama.image.template", "digest": "sha256:aa"},
        {"mediaType": lc.MODEL_LAYER, "digest": "sha256:bb"}]}))
    assert lc.blob_path("bonsai-27b:latest", tmp_path) == tmp_path / "blobs" / "sha256-bb"
    assert lc.blob_path("bonsai-27b", tmp_path) == tmp_path / "blobs" / "sha256-bb"


def test_server_cmd_gives_every_slot_the_full_context() -> None:
    cmd = lc.server_cmd(Path("/m.gguf"), 11439, 3, 8192)
    assert cmd[cmd.index("-np") + 1] == "3" and cmd[cmd.index("-c") + 1] == "24576"
    assert "--jinja" in cmd and cmd[cmd.index("--host") + 1] == "127.0.0.1"


def test_three_at_once_one_server_and_a_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm, seen = make(tmp_path, monkeypatch)

    async def go() -> list[str]:
        out = await asyncio.gather(*(llm("sys", f"release {i}") for i in range(9)))
        again = await llm("sys", "release 0")
        await llm.unload()
        return [*out, again]
    out = asyncio.run(go())
    assert out == ['{"a": 1}'] * 10
    assert len(seen["procs"]) == 1 and llm.starts == 1 and seen["peak"] == 3
    assert len(seen["bodies"]) == 9 and llm.calls == 9 and llm.cache_hits == 1
    b = seen["bodies"][0]
    assert b["temperature"] == 0 and b["max_tokens"] == 700 and b["response_format"] == {"type": "json_object"}
    assert b["chat_template_kwargs"] == {"enable_thinking": False}
    assert [m["role"] for m in b["messages"]] == ["system", "user"]
    assert seen["procs"][0].returncode == -15  # unload stopped the server
    cache = tmp_path / "llm_cache_llamacpp_bonsai-27b_latest_np3.jsonl"
    assert len(cache.read_text().splitlines()) == 9  # its own file, never Ollama's


def test_replies_survive_a_restart_through_the_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm, _ = make(tmp_path, monkeypatch)
    asyncio.run(llm("s", "u"))
    llm2, seen2 = make(tmp_path, monkeypatch)
    assert asyncio.run(llm2("s", "u")) == '{"a": 1}'
    assert seen2["bodies"] == [] and seen2["procs"] == []  # no server started for a cached reply


def test_context_overflow_is_not_retried_and_counts_as_unparsed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    err = httpx.Response(400, json={"error": {"code": 400, "type": "exceed_context_size_error", "message":
                                              "request (9001 tokens) exceeds the available context size (8192 tokens)"}})
    llm, seen = make(tmp_path, monkeypatch, reply=err)
    assert asyncio.run(llm_fields.ask(llm, "s", "u")) == ("", True)
    assert len(seen["bodies"]) == 1


def test_a_stopped_server_is_started_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm, seen = make(tmp_path, monkeypatch, cache=False)

    async def go() -> None:
        await llm("s", "a")
        await llm.unload()
        seen["up"] = False
        await llm("s", "b")
    asyncio.run(go())
    assert len(seen["procs"]) == 2 and seen["procs"][1].returncode is None


def test_refuses_a_server_it_did_not_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm, seen = make(tmp_path, monkeypatch, up_before=True)
    with pytest.raises(RuntimeError, match="unknown server"):
        asyncio.run(llm("s", "u"))
    assert seen["procs"] == []


def test_logprob_modes_stay_on_ollama(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm, _ = make(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        asyncio.run(llm("s", "u", mode="updown"))
    assert not hasattr(llm, "next_token_probs")  # llm_fields.rating_score then keeps the plain rating


def test_refuses_a_server_that_is_not_on_the_gpu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm, seen = make(tmp_path, monkeypatch)
    llm.require_gpu = True
    blob = tmp_path / "blob"
    blob.write_bytes(b"x" * 2**21)
    monkeypatch.setattr(lc, "blob_path", lambda model: blob)
    monkeypatch.setattr(lc, "vram_mib", lambda pid: 0)
    with pytest.raises(GPUFallbackError):
        asyncio.run(llm("s", "u"))
    assert seen["procs"][0].returncode == -15 and seen["bodies"] == []
    monkeypatch.setattr(lc, "vram_mib", lambda pid: 5000)
    seen["up"] = False
    assert asyncio.run(llm("s", "u")) == '{"a": 1}'


def test_label_engine_switch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(lc, "RESULTS", tmp_path)
    monkeypatch.delenv("AIRP_LABEL_ENGINE", raising=False)
    llm = llm_fields.bonsai(700)
    assert isinstance(llm, lc.LlamaServerLLM) and llm.slots == 3 and llm.num_ctx == 8192 and llm.num_predict == 700
    assert llm_fields.llm_client_j().num_predict == 1000
    monkeypatch.setenv("AIRP_LABEL_ENGINE", "ollama")
    old = llm_fields.bonsai(700)
    assert isinstance(old, OllamaLLM) and old.base_url.endswith(":11435")
    monkeypatch.setenv("AIRP_LABEL_ENGINE", "qwen")
    with pytest.raises(SystemExit):
        llm_fields.bonsai(700)
