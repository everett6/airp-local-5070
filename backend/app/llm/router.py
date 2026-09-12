"""
Maps each agent role to a named LLM endpoint (a physical machine + model),
so "which GPU handles which agent" is one small config block instead of
scattered across agent code.

Default routing philosophy for a 2-GPU setup (12GB-class 5070 + 3060):
  - **reasoning** endpoint (the 5070 — more VRAM/bandwidth, run the bigger
    quantized model here): Bull/Bear thesis, CIO synthesis, Portfolio
    Manager narrative — anything that has to weigh conflicting evidence and
    produce a judgment call.
  - **extraction** endpoint (the 3060 — smaller/faster model): summarizing
    a single filing section, writing a one-paragraph narrative from
    already-computed numbers (Fundamental/Macro/Risk analysts) — structured,
    lower-ambiguity tasks where a 7-8B class model does fine and speed
    matters more than depth.

This split is a default, not a hard rule — override `AGENT_ROUTING` per
deployment (e.g. if your 3060 is 8GB and struggles, route more roles to the
5070 and accept the queueing; see docs/LOCAL_SETUP.md).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.message_protocol import AgentRole
from app.llm.client import LLMClient, MockLLMClient, OpenAICompatibleClient


@dataclass(frozen=True)
class EndpointConfig:
    name: str
    base_url: str
    model: str


DEFAULT_ENDPOINT_KIND = "extraction"  # roles not explicitly routed fall back here

# Which endpoint (by name) each role uses. Anything not listed uses
# DEFAULT_ENDPOINT_KIND. Edit this to rebalance load across your two boxes.
AGENT_ROUTING: dict[AgentRole, str] = {
    AgentRole.BULL_THESIS: "reasoning",
    AgentRole.BEAR_THESIS: "reasoning",
    AgentRole.CIO: "reasoning",
    AgentRole.PORTFOLIO_MANAGER: "reasoning",
    AgentRole.RISK_MANAGER: "reasoning",
    AgentRole.FUNDAMENTAL_ANALYST: "extraction",
    AgentRole.FINANCIAL_STATEMENT_ANALYST: "extraction",
    AgentRole.MACRO_ECONOMIST: "extraction",
    AgentRole.TECHNICAL_ANALYST: "extraction",
    AgentRole.NEWS_ANALYST: "extraction",
    AgentRole.SENTIMENT_ANALYST: "extraction",
}


class LLMRouter:
    """Owns one `LLMClient` per named endpoint and hands the right one to
    whoever asks by `AgentRole`. Endpoints whose `base_url` is empty/unset
    fall back to `MockLLMClient` automatically — a missing GPU host degrades
    that role's output quality, it doesn't crash the pipeline."""

    def __init__(self, endpoints: dict[str, EndpointConfig]) -> None:
        self._clients: dict[str, LLMClient] = {}
        for name, cfg in endpoints.items():
            if not cfg.base_url:
                self._clients[name] = MockLLMClient(endpoint_name=name)
            else:
                self._clients[name] = OpenAICompatibleClient(
                    endpoint_name=name, base_url=cfg.base_url, model=cfg.model,
                )

    def for_role(self, role: AgentRole) -> LLMClient:
        endpoint_name = AGENT_ROUTING.get(role, DEFAULT_ENDPOINT_KIND)
        client = self._clients.get(endpoint_name)
        if client is None:
            raise KeyError(
                f"role {role.value} routes to endpoint {endpoint_name!r} which was "
                f"never configured — check core.config.Settings LLM endpoint list"
            )
        return client

    def for_endpoint(self, name: str) -> LLMClient:
        return self._clients[name]
