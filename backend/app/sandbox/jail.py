"""
Process-level jail for the forecasting agent.

`clock.sandbox_scope` protects code that goes through connectors; it cannot
stop code that simply opens the CSV, imports the orchestrator's objects, or
calls an API. So the agent runs as a separate process under bubblewrap:

  --unshare-all       own network namespace (no interfaces: no internet, not
                      even localhost Ollama), own PID/IPC/UTS namespaces
  --clearenv          no inherited environment variables (tokens, paths)
  read-only binds     only the OS runtime, the Python install, and the single
                      file `agent_worker.py` — NOT the repo, NOT `data/`,
                      NOT $HOME
  --tmpfs /tmp        scratch space that vanishes with the process

Everything the agent knows arrives on stdin from the orchestrator, which
only ever sends `PointInTimeView`-derived, anonymized data. `probe()` lets
every simulation run verify these properties live instead of assuming them.

If bubblewrap is unavailable the jail refuses to start unless
`allow_unjailed=True` (tests only) — a silent downgrade would make every
accuracy number after it untrustworthy.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Self

WORKER_PATH = Path(__file__).with_name("agent_worker.py")


def _python_root() -> Path:
    # e.g. /home/u/miniconda3/bin/python3.14 -> /home/u/miniconda3
    return Path(sys.base_prefix).resolve()


def build_command(allow_unjailed: bool = False) -> list[str]:
    py = str(Path(sys.base_prefix) / "bin" / f"python{sys.version_info.major}.{sys.version_info.minor}")
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        if not allow_unjailed:
            raise RuntimeError("bubblewrap (bwrap) not found; refusing to run the agent unjailed")
        return [sys.executable, "-I", "-S", str(WORKER_PATH)]
    root = str(_python_root())
    cmd = [
        bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", root, root,
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--ro-bind", str(WORKER_PATH), "/agent/agent_worker.py",
        "--chdir", "/agent",
    ]
    for lib in ("/lib", "/lib64", "/bin"):
        if Path(lib).is_symlink():
            cmd += ["--symlink", str(Path(lib).readlink()), lib]
        elif Path(lib).exists():
            cmd += ["--ro-bind", lib, lib]
    return [*cmd, py, "-I", "-S", "/agent/agent_worker.py"]


LLMFn = Any  # async (system: str, user: str) -> str


class AgentJail:
    def __init__(self, llm: LLMFn, allow_unjailed: bool = False) -> None:
        self._llm = llm
        self._cmd = build_command(allow_unjailed)
        self.jailed = "bwrap" in Path(self._cmd[0]).name
        self._proc: asyncio.subprocess.Process | None = None
        self.llm_calls = 0

    async def __aenter__(self) -> Self:
        self._proc = await asyncio.create_subprocess_exec(
            *self._cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._proc and self._proc.returncode is None:
            assert self._proc.stdin is not None
            self._proc.stdin.close()
            try:
                await asyncio.wait_for(self._proc.wait(), 5)
            except TimeoutError:
                self._proc.kill()

    async def _write(self, obj: dict[str, Any]) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write((json.dumps(obj) + "\n").encode())
        await self._proc.stdin.drain()

    async def _read(self) -> dict[str, Any]:
        assert self._proc and self._proc.stdout and self._proc.stderr
        line = await self._proc.stdout.readline()
        if not line:
            err = (await self._proc.stderr.read()).decode()[-2000:]
            raise RuntimeError(f"agent process exited: {err}")
        out: dict[str, Any] = json.loads(line)
        return out

    async def call(self, task: dict[str, Any]) -> dict[str, Any]:
        """Send a task; service any LLM requests the agent makes; return its result."""
        await self._write(task)
        while True:
            msg = await self._read()
            if "llm_requests" in msg:
                reqs = msg["llm_requests"]
                texts = await asyncio.gather(*(self._llm(r["system"], r["user"]) for r in reqs))
                self.llm_calls += len(reqs)
                await self._write({"llm_responses": {r["id"]: t for r, t in zip(reqs, texts, strict=True)}})
            elif "result" in msg:
                result: dict[str, Any] = msg["result"]
                return result
            else:
                raise RuntimeError(f"agent error: {msg}")

    async def probe(self, forbidden_paths: list[str]) -> dict[str, Any]:
        r = await self.call({"task": "probe", "paths": forbidden_paths})
        r["jailed"] = self.jailed
        r["passed"] = self.jailed and not r["readable_forbidden_paths"] and not r["network_reachable"]
        return r
