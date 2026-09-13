# Cross-Platform Notes (Windows / Linux / macOS)

This repo is meant to run the same way on all three. This page documents
the specific choices made to keep that true, and the couple of things you
need to know on Windows in particular.

## Setup

Run the setup script for your OS — both do the same thing (create a Python
venv, install backend deps into it, `npm install` the frontend, copy
`.env.example` to `.env` if missing) and neither starts any servers or
touches Ollama:

```bash
# macOS / Linux
./scripts/setup.sh

# Windows (PowerShell)
.\scripts\setup.ps1
```

If `setup.ps1` refuses to run at all, PowerShell's execution policy is
blocking unsigned scripts by default. Run once, as your normal user (not an
elevated/Administrator prompt):

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

This only relaxes the policy for scripts you run yourself under your own
account — it doesn't change anything system-wide or for other users.

## Why the backend has no compiled/platform-specific dependencies

`backend/pyproject.toml`'s default install is deliberately minimal:
FastAPI, Pydantic, NumPy/SciPy, httpx, tenacity, Jinja2 — all of which ship
prebuilt wheels for Windows/Linux/macOS on every actively-supported Python
version. Notably absent from the default install: `asyncpg`, `psycopg`, and
the `neo4j` driver, all of which have historically been a common source of
"failed to build wheel" errors on Windows (they either need a C toolchain
present or a wheel that matches your exact Python build). `neo4j` is
available as an optional extra (`pip install -e ".[graph]"`) if you actually
want the knowledge-graph feature; the DB-backed persistence path was removed
entirely in favor of stdlib `sqlite3` (see `docs/ARCHITECTURE.md`'s note on
`app/store/sqlite_store.py`), which needs nothing beyond Python itself on
any OS.

## Path handling

All file paths in config (`Settings.sqlite_path`, etc.) are plain strings
passed to `pathlib.Path`, which normalizes separators correctly on Windows
even when you write them with forward slashes (`./airp_local.db` works
identically on all three OSes) — you never need to write a Windows-style
backslash path in config or `.env`.

## Line endings

`.gitignore` and source files use LF line endings. If you're on Windows and
your git client is configured to check out CRLF (`core.autocrlf=true`,
common on Windows-default Git installs), that's fine — Python, Node, and
this repo's tooling all handle either line ending correctly on read. The one
place it could theoretically matter is `scripts/setup.sh`: if you ever run
it under Windows via WSL or Git Bash (rather than using `setup.ps1`, which
is what you should use on native Windows) and your checkout converted it to
CRLF, bash will fail on the shebang line. If that happens:

```bash
sed -i 's/\r$//' scripts/setup.sh
```

## Networking across your two GPU machines (Windows/Linux/Mac in any combination)

Nothing about `docs/LOCAL_SETUP.md`'s Ollama LAN setup cares what OS either
machine runs — `OLLAMA_HOST=0.0.0.0` and the firewall rule are the only
requirements, and both have OS-specific instructions there. A Windows
5070 box talking to a Linux-hosted backend, or any other combination, works
identically because the only thing crossing the network is plain HTTP.

## Docker

`docker-compose.yml` and both Dockerfiles are unaffected by host OS — Docker
Desktop (Windows/Mac) or the Docker Engine (Linux) both run the same Linux
containers underneath. The one Docker-specific note: on Windows/Mac, Docker
Desktop's default resource limits (CPU/memory) are sometimes lower than
native — if a backtest suite feels slow only inside Docker but not when run
natively, check Docker Desktop's resource settings before assuming it's a
code problem.

## What we deliberately did not build

A single cross-platform "start everything" script (e.g. a `justfile` or a
Node-based process manager to launch both backend and frontend together).
Two separate, boring `uvicorn ...` / `npm run dev` commands in two terminals
is less magic, easier to debug when one side fails to start, and doesn't
require installing yet another tool — a reasonable tradeoff for a
development setup, even though it means one extra terminal window.
