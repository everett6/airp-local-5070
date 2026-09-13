# Setup for Windows (PowerShell). For macOS/Linux, use scripts/setup.sh instead.
#
# What this does, and nothing more: creates a Python venv, installs the
# backend in it, installs frontend node_modules, and copies .env.example to
# .env if you don't already have one. It does not start any servers and
# does not touch Ollama - see docs\LOCAL_SETUP.md for that.
#
# If this script won't run at all, PowerShell's execution policy is
# probably blocking it - run once as your normal user (not elevated):
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "== AIRP Local setup (Windows) =="

$PythonCmd = $null
foreach ($candidate in @("python", "py")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $PythonCmd = $candidate
        break
    }
}
if (-not $PythonCmd) {
    Write-Error "No 'python' or 'py' found on PATH. Install Python 3.11+ from python.org first."
    exit 1
}
Write-Host "Using $(& $PythonCmd --version)"

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Error "No 'node' found on PATH. Install Node.js 20+ from nodejs.org first."
    exit 1
}
Write-Host "Using $(node --version)"

Write-Host ""
Write-Host "-- Backend --"
Set-Location "$RepoRoot\backend"
if (-not (Test-Path ".venv")) {
    & $PythonCmd -m venv .venv
    Write-Host "Created .venv"
}
& ".venv\Scripts\pip.exe" install -q --upgrade pip
& ".venv\Scripts\pip.exe" install -q -e ".[dev]"
Write-Host "Backend deps installed into backend\.venv"

Write-Host ""
Write-Host "-- Frontend --"
Set-Location "$RepoRoot\frontend"
npm install
Write-Host "Frontend deps installed"

Write-Host ""
Write-Host "-- Env file --"
Set-Location $RepoRoot
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example - edit it with your Ollama endpoint IPs (see docs\LOCAL_SETUP.md)"
} else {
    Write-Host ".env already exists, leaving it alone"
}

Write-Host ""
Write-Host "== Done =="
Write-Host "Start the backend:  cd backend; .venv\Scripts\Activate.ps1; uvicorn app.main:app --reload"
Write-Host "Start the frontend: cd frontend; npm run dev"
Write-Host "Then open http://localhost:3000/sandbox"
