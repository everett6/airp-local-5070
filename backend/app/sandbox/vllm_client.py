"""vLLM (OpenAI-compatible server) client with the same call shape as walkforward.OllamaLLM, for the research agent.

vLLM batches concurrent requests continuously and reuses cached prompt prefixes (the research history is re-sent every
round), which is where its speed over Ollama comes from. Start the server with scripts/vllm_serve.sh. Only what the
research runner needs: native tool-call rounds (Jan-v1) and plain JSON replies; the log-probability modes stay on
Ollama.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

from app.sandbox.walkforward import _append_line, _as_actions, _is_agent_reply

RESULTS = Path(__file__).resolve().parents[2] / "results"


class VLLMChat:
    def __init__(self, model: str, base_url: str = "http://127.0.0.1:8000", concurrency: int = 8,
                 num_ctx: int = 6144, num_predict: int = 1200, cache: bool = True, tag: str = "") -> None:
        # num_ctx is the budget the agent sizes its research history for: the server allows 8,192 tokens, but in
        # native tool mode the chat template adds the tool schemas (~2,000 tokens) on top of the prompt
        self.model, self.base_url = model, base_url.rstrip("/")
        self.num_ctx, self.num_predict = num_ctx, num_predict
        self.native_tools: list[dict[str, Any]] | None = None
        self.calls = self.cache_hits = 0
        self._sem = asyncio.Semaphore(concurrency)
        self._client = httpx.AsyncClient(timeout=600)
        self.use_cache = cache
        self._cache_path = RESULTS / f"llm_cache_vllm_{model.split('/')[-1]}{tag}.jsonl"
        self._cache: dict[str, str] = {}
        if cache and self._cache_path.exists():
            for line in self._cache_path.read_text().splitlines():
                d = json.loads(line)
                self._cache[d["k"]] = d["v"]

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(3):
            try:
                r = await self._client.post(f"{self.base_url}/v1/chat/completions", json=body)
                if r.status_code == 400 and attempt == 0:
                    # prompt longer than the server's context: keep the newest research (the end of the user turn)
                    user = body["messages"][-1]["content"]
                    body = {**body, "messages": [*body["messages"][:-1], {"role": "user", "content": (
                        "(earlier research cut to fit the context)\n" + user[-int(len(user) * 0.5):])}]}
                    continue
                if r.status_code == 400:
                    return {"content": ""}  # still too long: this round counts as a failed reply, the run goes on
                r.raise_for_status()
                msg: dict[str, Any] = r.json()["choices"][0]["message"]
                return msg
            except (httpx.HTTPError, KeyError, IndexError):
                if attempt == 2:
                    raise
                await asyncio.sleep(2)
        raise RuntimeError("unreachable")

    def _body(self, system: str, user: str, thinking: bool, max_tokens: int, temperature: float) -> dict[str, Any]:
        return {"model": self.model, "temperature": temperature, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "chat_template_kwargs": {"enable_thinking": thinking}}

    async def __call__(self, system: str, user: str, mode: str | None = None) -> str:
        if mode is not None:
            raise ValueError("log-probability modes run on Ollama, not vLLM")
        native = (self.native_tools is not None and '"actions"' in system
                  and "You have used all tool rounds" not in user)
        key = hashlib.sha256(f"{self.model}\0{system}\0{user}\0native={native}\0v2".encode()).hexdigest()
        if self.use_cache and key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        async with self._sem:
            text = ""
            if native:
                note = ("\n\nCall the tools directly with your tool-call format (several at once is fine). When you "
                        "have enough evidence, reply with the final JSON object only.")
                for temp in (0.0, 0.7):
                    body = self._body(system + note, user, True, 1024, temp) | {"tools": self.native_tools,
                                                                                 "tool_choice": "auto"}
                    m = await self._post(body)
                    if m.get("tool_calls"):
                        text = json.dumps({"thought": (m.get("content") or "").strip()[:300], "actions": [
                            {"tool": c["function"]["name"], "args": _args(c["function"].get("arguments"))}
                            for c in m["tool_calls"]]})
                        break
                    if _is_agent_reply(t := _as_actions(m.get("content") or "")):
                        text = t  # prose without a call falls through to a retry, then to plain JSON
                        break
            if not text:
                body = self._body(system, user, False, self.num_predict, 0.0) | {
                    "response_format": {"type": "json_object"}}
                text = _as_actions((await self._post(body)).get("content") or "")
        self.calls += 1
        if self.use_cache and text.strip():
            self._cache[key] = text
            _append_line(self._cache_path, json.dumps({"k": key, "v": text}))
        return text

    async def unload(self) -> None:
        await self._client.aclose()


def _args(a: Any) -> dict[str, Any]:
    if isinstance(a, dict):
        return a
    try:
        v = json.loads(a or "{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}
