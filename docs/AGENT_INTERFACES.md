# Agent Interfaces

This is the checklist every specialist agent must satisfy, and the reference
for how prompts, tools, and schemas are organized per agent. `agents/fundamental_analyst.py`
is the canonical example — read it alongside this doc.

## The contract (`agents/base.py::SpecialistAgent`)

Every agent:

1. Declares `role: AgentRole` (one value from `core/message_protocol.AgentRole`).
2. Receives its tool allow-list (`dict[str, ToolFn]`) at construction — it
   cannot call anything not in that dict (`SpecialistAgent.call_tool` raises
   `PermissionError` otherwise).
3. Implements `analyze(ctx, request) -> (output, ClaimBundle)` where `request`
   and `output` are both Pydantic models. `output` is registered via
   `@register_payload` so it can travel over the message bus.
4. Returns a `ClaimBundle` alongside its output. **Every number in `output`
   must have a matching `Claim` with `is_numeric=True` and a `numeric_source`
   naming the exact function that computed it** (e.g.
   `"quant.dcf.discounted_cash_flow"`). This is checked by
   `Claim._numeric_requires_source` at construction time — you cannot
   construct an invalid claim, not just "shouldn't".
5. Does not read another agent's private reasoning. It may `recall()` its own
   memory namespace, and it will *receive* other agents' published findings
   as input (e.g. thesis agents receive `specialist_findings` in their
   request), but there is no API to reach into another agent's internal state.

## Two kinds of agents, two different verification postures

| Agent type | Examples | What the ClaimBundle looks like |
|---|---|---|
| **Extraction/computation agents** | Fundamental Analyst, Financial Statement Analyst, Technical Analyst, Options Strategist, Quant Researcher | Every claim is numeric, every `numeric_source` points into `quant/*` or a data connector. Fully re-verifiable by `EvidenceVerifier` recomputation. |
| **Synthesis agents** | Bull/Bear Thesis, CIO, Portfolio Manager | Claims are non-numeric prose syntheses (`is_numeric=False`). These are *not* independently re-verifiable the way a DCF number is — instead, the debate engine's Cross-Examination stage checks their *internal consistency* against upstream numeric claims (see `debate/orchestrator.py::_cross_examine`). A synthesis agent's price target must cite an existing upstream `claim_id`, never introduce a new number. |

New agents should be obviously one or the other. An agent that mixes both
(computes new numbers *and* freely synthesizes prose about them) is a sign
the role should be split — see the "Bounded blast radius" rationale in
`docs/ARCHITECTURE.md` §2.

## Adding a new specialist agent — checklist

1. Add the role to `AgentRole` in `core/message_protocol.py`.
2. Define the request/output Pydantic models in the agent's own file. Decorate
   the output with `@register_payload`.
3. Write the agent class, subclassing `SpecialistAgent[RequestType, OutputType]`.
4. Enumerate its tools explicitly at construction — resist the urge to hand it
   a broad "do everything" tool dict. If it needs a new tool, add that tool
   function and give it *only* to the agents that need it.
5. Write the prompt as a versioned artifact — see "Prompt versioning" below.
6. Add a unit test that constructs the agent with fake/stub tools (see
   `tests/test_debate_orchestrator.py`'s `DummySpecialist` pattern) and
   asserts the returned `ClaimBundle` has a `numeric_source` for every numeric
   claim.
7. Register the agent in the orchestrator wiring (`debate/orchestrator.py`'s
   `_agents` dict, constructed at the API layer — see `docs/API_SPEC.md`).
8. If the agent produces facts that belong in the knowledge graph (company
   relationships, not just numbers), have it emit `GraphRelationship` objects
   using only triples already in `knowledge_graph/schema.VALID_TRIPLES` — add
   a new triple there first if needed, as its own reviewed change.

## Prompt versioning

Each agent's prompt is a plain-text/markdown file colocated with the agent
(e.g. `agents/prompts/fundamental_analyst_v0.1.0.md`, not included in this
skeleton — wire the path in the agent's `__init__`). The agent's
`prompt_version` class attribute must match the filename's version suffix.
When you change a prompt in a way that could change output distribution
(not a typo fix), bump `prompt_version` — this is what lets
`memory/retrieval.py::build_calibration_report` segment accuracy stats by
prompt version, so a regression from a prompt change is visible rather than
blended into the running average.

## Full agent roster and current implementation status

| Role | File | Status |
|---|---|---|
| Fundamental Analyst | `agents/fundamental_analyst.py` | Implemented (reference example) |
| Bull Thesis / Bear Thesis | `agents/thesis_agents.py` | Implemented |
| Chief Investment Officer | — | Not yet implemented — synthesizes Consensus stage output into a final recommendation; planned as a synthesis agent per the table above |
| Portfolio Manager | — | Stubbed in `debate/orchestrator.py` wiring; needs `agents/portfolio_manager.py` |
| Financial Statement Analyst | — | Not yet implemented — will share most of `fundamental_analyst.py`'s pattern with a deeper focus on statement quality (Piotroski/Altman via `quant/ratios.py`) |
| Accounting Expert | — | Not yet implemented |
| Macro Economist | — | Not yet implemented — consumes `data_ingestion` economic-release connectors (not yet written) |
| Technical Analyst | — | Not yet implemented — needs a technicals tool module (`quant/technicals.py`, not yet written: moving averages, RSI, MACD as pure functions, same pattern as `quant/ratios.py`) |
| Options Strategist / Volatility Analyst | — | Not yet implemented — tools already exist (`quant/options.py`), agent wiring does not |
| News Analyst / Sentiment Analyst | — | Not yet implemented — needs `data_ingestion` news connector (not yet written) |
| Industry Expert / Supply Chain / Competition Analyst | — | Not yet implemented — primarily knowledge-graph consumers (`COMPETES_WITH`, `SUPPLIES` traversals) |
| Valuation Expert | — | Not yet implemented — thin wrapper combining `quant/dcf.py` comparable valuation with the Fundamental Analyst's output |
| Quant Researcher / Factor Model Researcher | — | Not yet implemented — wraps `quant/risk.py::factor_regression` |
| Risk Manager | — | Not yet implemented — wraps `quant/risk.py` VaR/CVaR/drawdown + `quant/ratios.py::altman_z_score` |
| Compliance Agent | — | Not yet implemented — rule-based (not LLM) checks against disclosure/insider-trading-window constraints; deliberately NOT an LLM agent for the same reason cross-examination is rule-based first |

This table is the actual, current, honest state — cross-reference it against
`docs/ROADMAP.md`'s milestones before assuming a role is live.
