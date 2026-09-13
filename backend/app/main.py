from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.routes import agents, health, reports, research, sandbox
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="AIRP Local — AI Investment Research Platform (local-first edition)",
    version="0.1.0",
    description="Runs against your own local LLM endpoints (e.g. Ollama on your own "
                 "GPUs). Produces research and trade proposals only; never executes "
                 "trades. Includes a point-in-time sandbox for honest backtesting.",
)

# Compresses responses over 500 bytes (e.g. backtest-suite results, which can
# be a few dozen KB across a year of monthly runs) — meaningfully faster over
# a real network (including your LAN if the frontend calls a backend running
# on a different machine), effectively free for the tiny single-result
# responses that are already under the threshold.
app.add_middleware(GZipMiddleware, minimum_size=500)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(research.router, prefix="/api/research", tags=["research"])
app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
app.include_router(sandbox.router, prefix="/api/sandbox", tags=["sandbox"])
