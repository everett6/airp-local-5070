#!/usr/bin/env bash
# Setup for macOS and Linux. For Windows, use scripts/setup.ps1 instead.
#
# What this does, and nothing more: creates a Python venv, installs the
# backend in it, installs frontend node_modules, and copies .env.example to
# .env if you don't already have one. It does not start any servers and
# does not touch Ollama - see docs/LOCAL_SETUP.md for that.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "== AIRP Local setup (macOS/Linux) =="

PYTHON_BIN="python3"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  if command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  else
    echo "ERROR: no python3 or python found on PATH. Install Python 3.11+ first." >&2
    exit 1
  fi
fi
echo "Using $($PYTHON_BIN --version)"

if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: node not found on PATH. Install Node.js 20+ first." >&2
  exit 1
fi
echo "Using $(node --version)"

echo ""
echo "-- Backend --"
cd "$REPO_ROOT/backend"
if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
  echo "Created .venv"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -e ".[dev]"
deactivate
echo "Backend deps installed into backend/.venv"

echo ""
echo "-- Frontend --"
cd "$REPO_ROOT/frontend"
npm install
echo "Frontend deps installed"

echo ""
echo "-- Env file --"
cd "$REPO_ROOT"
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Created .env from .env.example - edit it with your Ollama endpoint IPs (see docs/LOCAL_SETUP.md)"
else
  echo ".env already exists, leaving it alone"
fi

echo ""
echo "== Done =="
echo "Start the backend:  cd backend && source .venv/bin/activate && uvicorn app.main:app --reload"
echo "Start the frontend: cd frontend && npm run dev"
echo "Then open http://localhost:3000/sandbox"
