"""
The only two things allowed to call an LLM in this codebase are:
  1. `LLMClient` implementations here (the transport)
  2. `app/tools/toolkit.py` (the only place that constructs prompts and
     parses responses into the typed payloads agents expect)

No agent file imports httpx or an LLM SDK directly — this is what makes it
possible to point every "reasoning" call at your 5070 and every "extraction"
call at your 3060 (or swap either for a cloud model later) by editing one
config block, with zero changes to agent code.

Everything here is OpenAI-chat-completions-shaped because that's the lowest
common denominator: Ollama, llama.cpp's server, vLLM, LM Studio, and text-
generation-webui all speak it (Ollama also has its own /api/chat, but its
/v1/chat/completions endpoint is the safer long-term bet since it's the same
shape you'd use if you ever swap in a different local server).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

logger = logging.getLogger("airp.llm")


class LLMError(Exception):
    def __init__(self, endpoint_name: str, message: str) -> None:
        super().__init__(f"[{endpoint_name}] {message}")
        self.endpoint_name = endpoint_name


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class ChatResponse:
    text: str
    model: str
    endpoint_name: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LLMClient(Protocol):
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        json_mode: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> ChatResponse: ...

    async def chat_json(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        max_repair_attempts: int = 2,
    ) -> dict[str, Any]:
        """Convenience wrapper: request JSON mode, parse it, and if parsing
        fails, feed the parse error back to the model and ask it to fix its
        own output (bounded retries) rather than crashing the pipeline on a
        single malformed generation — small local models are more prone to
        near-miss JSON (trailing commas, unescaped quotes) than large hosted
        ones, so this repair loop matters more here than it would calling a
        frontier API."""
        ...


class OpenAICompatibleClient:
    """Works against any OpenAI-chat-completions-compatible local server:
    Ollama (`ollama serve`, exposes /v1/chat/completions since 0.1.x with
    OPENAI_COMPAT enabled by default in recent versions), llama.cpp's
    `llama-server`, vLLM, LM Studio, text-generation-webui's OpenAI extension.
    """

    def __init__(
        self,
        endpoint_name: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 120.0,
        api_key: str = "not-needed",
    ) -> None:
        self.endpoint_name = endpoint_name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._timeout = timeout_seconds
        self._api_key = api_key

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        json_mode: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> ChatResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        url = f"{self.base_url}/v1/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    url, json=body,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError as exc:
            raise LLMError(
                self.endpoint_name,
                f"could not reach {url} — is Ollama/llama-server running and "
                f"listening on this address? (OLLAMA_HOST=0.0.0.0 if calling "
                f"across your LAN from another machine)",
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise LLMError(self.endpoint_name, f"HTTP {exc.response.status_code}: {exc.response.text[:500]}") from exc

        choice = data["choices"][0]
        usage = data.get("usage", {})
        return ChatResponse(
            text=choice["message"]["content"],
            model=data.get("model", self.model),
            endpoint_name=self.endpoint_name,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

    async def chat_json(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        max_repair_attempts: int = 2,
    ) -> dict[str, Any]:
        working_messages = list(messages)
        last_error: Exception | None = None

        for attempt in range(max_repair_attempts + 1):
            response = await self.chat(
                working_messages, json_mode=True, temperature=temperature, max_tokens=max_tokens,
            )
            try:
                parsed = json.loads(response.text)
                if not isinstance(parsed, dict):
                    raise json.JSONDecodeError(
                        f"expected a JSON object, got {type(parsed).__name__}", response.text, 0
                    )
                return parsed
            except json.JSONDecodeError as exc:
                last_error = exc
                logger.warning(
                    "endpoint %s returned invalid JSON (attempt %d/%d): %s",
                    self.endpoint_name, attempt + 1, max_repair_attempts + 1, exc,
                )
                working_messages = [
                    *working_messages,
                    ChatMessage(role="assistant", content=response.text),
                    ChatMessage(
                        role="user",
                        content=(
                            f"That was not valid JSON ({exc}). Respond again with ONLY "
                            "valid JSON, no markdown fences, no commentary."
                        ),
                    ),
                ]

        raise LLMError(
            self.endpoint_name,
            f"model never produced valid JSON after {max_repair_attempts + 1} attempts: {last_error}",
        )


class MockLLMClient:
    """Zero-dependency fallback so `docker compose up` produces a working
    (if templated/non-insightful) report even before anyone has Ollama
    running anywhere. Used automatically when an endpoint's `base_url` is
    left as the default placeholder — see `core/config.py`."""

    def __init__(self, endpoint_name: str = "mock") -> None:
        self.endpoint_name = endpoint_name
        self.model = "mock-llm"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        json_mode: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> ChatResponse:
        text = "This is a placeholder response from MockLLMClient — configure a real Ollama endpoint in .env to get real analysis."
        if json_mode:
            text = "{}"
        return ChatResponse(text=text, model=self.model, endpoint_name=self.endpoint_name)

    async def chat_json(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        max_repair_attempts: int = 2,
    ) -> dict[str, Any]:
        return {}
