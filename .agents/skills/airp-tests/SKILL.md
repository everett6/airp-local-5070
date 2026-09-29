---
name: airp-tests
description: Write or fix pytest tests in the airp-local-5070 repo. Use when a task adds or changes code under backend/ and needs tests, or when a test fails.
---

# Tests in airp

- Run from `backend/`: `.venv/bin/python -m pytest -q tests/<file>` (system python lacks pyarrow). Full suite ~25 s.
- **Synthetic data only.** Never load real market data, results files or the network in a test; never assert on a
  real backtest number.
- Reuse the helpers already there instead of new fixtures:
  - `tests/test_intraday.py::day(date, path, ...)` builds 1-minute bars; `tests/test_forward_allocator.py::frames()`
    builds SPY/BTC/ETH opens and closes.
  - Ledgers: `app.forward.ledger.Ledger(tmp_path / "ledger.jsonl").append(type, **fields)`.
  - Scripts are imported by putting `backend/scripts` on `sys.path` (see `tests/test_self_improve.py`).
- Patch network/GPU with `monkeypatch.setattr` on the module attribute the code actually calls
  (e.g. `monkeypatch.setattr(autorun.subprocess, "run", fake)`), never real services.
- Test the rule, not the implementation: exact expected returns with hand-computed numbers (`abs(x - y) < 1e-12`),
  edge cases (half days, missing bars, first period without history), and costs charged exactly once.
- Statistical code: add a calibration test (e.g. under pure noise the error rate stays within its bound), as in
  `test_e_process_keeps_its_error_bound_under_the_null`.
- Then lint: `.venv/bin/python -m ruff check <files>`; new code must pass `mypy` strict for the files you changed.
