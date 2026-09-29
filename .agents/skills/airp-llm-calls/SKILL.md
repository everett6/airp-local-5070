---
name: airp-llm-calls
description: Call the local research models (Bonsai-27B judge, Jan-v1-4B researcher) from airp-local-5070 code safely. Use when code sends prompts to Ollama/vLLM, parses model JSON, or touches the GPU.
---

# LLM calls in airp

- Models: **Bonsai-27B** (`bonsai-27b:latest`, judge/ratings) and **Jan-v1-4B** (research). **Never Qwen** or any
  other model, and never download a model.
- GPU: one job at a time. Wrap GPU work in `app/sandbox/gpu_lock.py::gpu_priority(name)`, wait with
  `scripts/forward_events.py::wait_gpu_free(900)`, start a private server with `forward_events.Ollama(port, models,
  parallel, log)` and always `srv.stop()` in `finally`. Client: `app/sandbox/walkforward.py::OllamaLLM(...,
  require_gpu=True)`; `await llm.unload()` when done.
- Prompts and parsing live in `scripts/llm_fields.py`: `ask(llm, system, user)`, `parse(reply)`,
  `verify(raw, source_text, FIELDS_*)` (quotes must appear verbatim in the source), `theses()`, `rating_score()`.
  Reuse them; don't write new JSON parsing.
- LLM output is data: code computes every number; the model only rates or extracts. Frozen prompts
  (`PROMPT_J`, `PROMPT_BB`, ...) are part of registered tests: never edit them.
- In tests, fake the model (`monkeypatch.setattr(module, "ask", fake)`); never hit the GPU from a test.
- Shadows print `LEARN ALERT: ...` on problems and must never fail the events job (wrap in try/except).
