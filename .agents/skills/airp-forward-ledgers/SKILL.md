---
name: airp-forward-ledgers
description: Work safely with the forward paper-test ledgers and books in airp-local-5070 (backend/results/forward, app/forward/ledger.py, app/portfolio/forward.py, guard.py, broker.py). Use for any change touching forward runs, the allocator, the broker mirror or the mandate.
---

# Forward ledgers and books

- `Ledger` (`app/forward/ledger.py`) is append-only with a hash chain: use `append(rtype, **fields)`, read with
  `records()`, check with `verify()`. Never rewrite, reorder or delete a line; never edit `backend/results/**` by hand.
- Real vs dry: the real books live in `results/forward/{allocator,events,...}`; dry runs use the `*_autodry` folders
  (`scripts/autorun.py::DRY`). Test changes against a copy in a temp dir (`--dir <tmp>`), never the real folders.
- Every target passes `app/portfolio/guard.py::gate(targets, MANDATE)`; the mandate (`backend/config/mandate.json`)
  is changed only by the user. Fail closed: a rejected target leaves nothing pending.
- Fills happen at the first open after the decision's UTC date (`fill_day`); equity marks at the last known close.
- The broker mirror (`app/portfolio/broker.py::plan`) must be idempotent (fixed client order ids), sell first,
  and sell any book asset dropped from the targets (`CRYPTO`, `STOCKS`).
- Paper money only: never add live endpoints, never place orders from a test, never read or print `backend/.env`.
