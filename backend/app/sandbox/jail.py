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

Resource limits (`JailLimits`, applied with `prlimit` in both modes) stop a
misbehaving agent from taking the orchestrator down with it: address space,
CPU seconds, open files, file size; a response timeout between messages
(LLM time is not counted); a maximum message size; and a cap on how many LLM
calls one message may request. Any violation kills the agent and raises
`JailError`, which aborts the run before a results file is written.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import sys
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Self

WORKER_PATH = Path(__file__).with_name("agent_worker.py")


def _python_root() -> Path:
    # e.g. /home/u/miniconda3/bin/python3.14 -> /home/u/miniconda3
    return Path(sys.base_prefix).resolve()


class JailError(RuntimeError):
    """The agent broke a jail limit or protocol rule; it has been killed."""


@dataclass(frozen=True)
class JailLimits:
    memory_mb: int = 2048          # address space
    cpu_seconds: int = 4 * 3600    # total CPU time of the agent process (backstop for runaway loops)
    open_files: int = 64
    file_size_mb: int = 1          # largest file it may write (only /tmp is writable anyway)
    response_timeout_s: float = 300.0  # max time for the agent to answer, excluding LLM calls
    max_message_bytes: int = 16 * 1024 * 1024
    max_llm_requests_per_message: int = 256
    max_prompt_chars: int = 100_000
    max_tool_calls_per_message: int = 8

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _prlimit_prefix(limits: JailLimits) -> list[str]:
    prlimit = shutil.which("prlimit")
    if prlimit is None:
        raise RuntimeError("prlimit (util-linux) not found; refusing to run the agent without resource limits")
    mb = 1024 * 1024
    return [prlimit, f"--as={limits.memory_mb * mb}", f"--cpu={limits.cpu_seconds}",
            f"--nofile={limits.open_files}", f"--fsize={limits.file_size_mb * mb}", "--"]


def build_command(allow_unjailed: bool = False, worker: Path = WORKER_PATH,
                  limits: JailLimits | None = None) -> list[str]:
    py = str(Path(sys.base_prefix) / "bin" / f"python{sys.version_info.major}.{sys.version_info.minor}")
    prefix = _prlimit_prefix(limits) if limits else []
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        if not allow_unjailed:
            raise RuntimeError("bubblewrap (bwrap) not found; refusing to run the agent unjailed")
        return [*prefix, sys.executable, "-I", "-S", str(worker)]
    root = str(_python_root())
    cmd = [
        bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", root, root,
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--ro-bind", str(worker), "/agent/agent_worker.py",
        "--chdir", "/agent",
    ]
    for lib in ("/lib", "/lib64", "/bin"):
        if Path(lib).is_symlink():
            cmd += ["--symlink", str(Path(lib).readlink()), lib]
        elif Path(lib).exists():
            cmd += ["--ro-bind", lib, lib]
    return [*prefix, *cmd, py, "-I", "-S", "/agent/agent_worker.py"]


LLMFn = Any  # async (system: str, user: str, mode: str = ...) -> str
# request modes the agent may ask for; None = ordinary text completion. "updown" = one-word UP/DOWN answer scored
# from token log-probabilities (the LLM function must accept mode=... to be used with it)
LLM_MODES = (None, "updown")
ToolExecutor = Any  # object with async execute(list[dict]) -> dict[str, dict]; see app/tools/gateway.py


class AgentJail:
    def __init__(self, llm: LLMFn, allow_unjailed: bool = False, *, worker: Path = WORKER_PATH,
                 limits: JailLimits | None = None, tools: ToolExecutor | None = None) -> None:
        self._llm = llm
        self._tools = tools
        self.tool_calls = 0
        self.limits = limits or JailLimits()
        bwrap = shutil.which("bwrap")
        self._cmd = build_command(allow_unjailed, worker, self.limits)
        self.jailed = bwrap is not None and bwrap in self._cmd
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_tail: deque[bytes] = deque(maxlen=64)
        self._stderr_task: asyncio.Task[None] | None = None
        self.llm_calls = 0

    async def __aenter__(self) -> Self:
        self._proc = await asyncio.create_subprocess_exec(
            *self._cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=self.limits.max_message_bytes,
        )
        # drain stderr continuously: a full pipe would otherwise block the agent forever
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        return self

    async def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        while chunk := await self._proc.stderr.read(4096):
            self._stderr_tail.append(chunk)

    def _stderr_text(self) -> str:
        return b"".join(self._stderr_tail).decode(errors="replace")[-2000:]

    async def _kill(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.kill()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._proc.wait(), 5)

    async def _fail(self, reason: str) -> JailError:
        await self._kill()
        return JailError(reason)

    async def __aexit__(self, *exc: object) -> None:
        if self._proc and self._proc.returncode is None:
            assert self._proc.stdin is not None
            self._proc.stdin.close()
            try:
                await asyncio.wait_for(self._proc.wait(), 5)
            except TimeoutError:
                await self._kill()
        if self._stderr_task:
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr_task

    async def _write(self, obj: dict[str, Any]) -> None:
        assert self._proc and self._proc.stdin
        try:
            self._proc.stdin.write((json.dumps(obj) + "\n").encode())
            # a hung agent that stops reading would otherwise block drain() forever once the pipe fills
            await asyncio.wait_for(self._proc.stdin.drain(), self.limits.response_timeout_s)
        except TimeoutError as e:
            raise await self._fail(f"agent did not accept input within {self.limits.response_timeout_s}s") from e
        except (BrokenPipeError, ConnectionResetError) as e:
            raise await self._fail(f"agent process is gone: {self._stderr_text()}") from e

    async def _read(self) -> dict[str, Any]:
        assert self._proc and self._proc.stdout
        try:
            line = await asyncio.wait_for(self._proc.stdout.readline(), self.limits.response_timeout_s)
        except TimeoutError as e:
            raise await self._fail(f"agent did not respond within {self.limits.response_timeout_s}s") from e
        except ValueError as e:  # asyncio's "chunk exceeds the limit"
            raise await self._fail(f"agent message larger than {self.limits.max_message_bytes} bytes") from e
        if not line:
            await self._kill()
            raise JailError(f"agent process exited: {self._stderr_text()}")
        try:
            out = json.loads(line)
        except json.JSONDecodeError as e:
            raise await self._fail(f"agent sent invalid JSON: {line[:200]!r}") from e
        if not isinstance(out, dict):
            raise await self._fail("agent message is not a JSON object")
        return out

    async def _validate_requests(self, reqs: object) -> list[dict[str, str]]:
        lim = self.limits
        if not isinstance(reqs, list) or len(reqs) > lim.max_llm_requests_per_message:
            raise await self._fail(f"agent requested too many LLM calls (max {lim.max_llm_requests_per_message})")
        for r in reqs:
            ok = (isinstance(r, dict) and isinstance(r.get("id"), str | int) and isinstance(r.get("system"), str)
                  and isinstance(r.get("user"), str) and r.get("mode") in LLM_MODES
                  and len(r["system"]) + len(r["user"]) <= lim.max_prompt_chars)
            if not ok:
                raise await self._fail("agent sent a malformed or oversized LLM request")
        return reqs

    async def _validate_tool_requests(self, reqs: object) -> list[dict[str, Any]]:
        if self._tools is None:
            raise await self._fail("agent requested tools, but no tools are enabled for this jail")
        lim = self.limits.max_tool_calls_per_message
        if not isinstance(reqs, list) or not reqs or len(reqs) > lim:
            raise await self._fail(f"agent sent an empty or oversized tool batch (max {lim})")
        for r in reqs:
            ok = (isinstance(r, dict) and isinstance(r.get("id"), str | int) and isinstance(r.get("tool"), str)
                  and len(r["tool"]) <= 40 and isinstance(r.get("args", {}), dict))
            if not ok:
                raise await self._fail("agent sent a malformed tool request")
        return reqs

    async def call(self, task: dict[str, Any]) -> dict[str, Any]:
        """Send a task; service any LLM requests the agent makes; return its result."""
        await self._write(task)
        while True:
            msg = await self._read()
            if "llm_requests" in msg:
                reqs = await self._validate_requests(msg["llm_requests"])
                texts = await asyncio.gather(*(self._llm(r["system"], r["user"], mode=r["mode"]) if r.get("mode")
                                               else self._llm(r["system"], r["user"]) for r in reqs))
                self.llm_calls += len(reqs)
                await self._write({"llm_responses": {r["id"]: t for r, t in zip(reqs, texts, strict=True)}})
            elif "tool_requests" in msg:
                treqs = await self._validate_tool_requests(msg["tool_requests"])
                assert self._tools is not None  # checked in _validate_tool_requests
                responses = await self._tools.execute(treqs)
                self.tool_calls += len(treqs)
                await self._write({"tool_responses": responses})
            elif "result" in msg and isinstance(msg["result"], dict):
                result: dict[str, Any] = msg["result"]
                return result
            else:
                raise await self._fail(f"agent error: {str(msg)[:500]}")

    async def probe(self, forbidden_paths: list[str]) -> dict[str, Any]:
        r = await self.call({"task": "probe", "paths": forbidden_paths})
        r["jailed"] = self.jailed
        r["passed"] = self.jailed and not r["readable_forbidden_paths"] and not r["network_reachable"]
        return r
