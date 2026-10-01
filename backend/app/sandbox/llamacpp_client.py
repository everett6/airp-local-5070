"""Bonsai through the llama-server binary that Ollama bundles, with parallel slots (docs/PLAN_60_V2.md, B4b "Engine
note", 2026-09-30).

Ollama 0.34.0 gives this model architecture one slot whatever OLLAMA_NUM_PARALLEL says. The same engine started
directly answers 3 requests at once: 1.63x on the arm-A labels, and with one slot its replies are Ollama's replies.
Same call shape as walkforward.OllamaLLM for plain JSON replies; the log-probability modes stay on Ollama.

The client owns the server. It starts it on the first request and `unload()` stops it, so the GPU is free the moment
a label stage ends or the forward runner raises its priority flag. The server also dies with this process (a stage
stopped by `timeout` leaves nothing on the GPU). Nothing is downloaded: the model is the GGUF blob Ollama holds.
"""
from __future__ import annotations

import asyncio
import atexit
import ctypes
import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from app.sandbox.gpu_lock import priority_wanted
from app.sandbox.walkforward import GPUFallbackError, _append_line

RESULTS = Path(__file__).resolve().parents[2] / "results"
OLLAMA_LIB = Path("/usr/local/lib/ollama")
MODEL_LAYER = "application/vnd.ollama.image.model"
_LIBC = ctypes.CDLL("libc.so.6", use_errno=True)
_PR_SET_PDEATHSIG = 1


def blob_path(model: str, models_dir: Path | None = None) -> Path:
    """The GGUF file Ollama holds for `name:tag`: the model layer of its manifest."""
    root = models_dir or Path(os.environ.get("OLLAMA_MODELS") or Path.home() / ".ollama" / "models")
    name, _, tag = model.partition(":")
    manifest = json.loads((root / "manifests" / "registry.ollama.ai" / "library" / name / (tag or "latest")).read_text())
    digest = next(x["digest"] for x in manifest["layers"] if x["mediaType"] == MODEL_LAYER)
    return root / "blobs" / str(digest).replace(":", "-")


def server_cmd(gguf: Path, port: int, slots: int, num_ctx: int, batch: int = 1024,
               binary: Path = OLLAMA_LIB / "llama-server") -> list[str]:
    """The 30 Sep benchmark's flags: Ollama's own (q8_0 KV cache, flash attention, batch 1024) plus the model's chat
    template. `-c` is the total, so every slot gets `num_ctx` tokens as it does under Ollama. `batch` is how many
    tokens one GPU step takes: while one slot reads a prompt, the others write one token per step."""
    return [str(binary), "--model", str(gguf), "--port", str(port), "--host", "127.0.0.1", "--no-webui", "--offline",
            "-c", str(num_ctx * slots), "-np", str(slots), "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
            "--flash-attn", "on", "-b", str(batch), "-ub", str(batch), "--jinja"]


def server_env() -> dict[str, str]:
    return os.environ | {"LD_LIBRARY_PATH": f"{OLLAMA_LIB}:{OLLAMA_LIB / 'cuda_v13'}",
                         "GGML_BACKEND_PATH": str(OLLAMA_LIB / "cuda_v13" / "libggml-cuda.so"),
                         "CUDA_VISIBLE_DEVICES": "0"}


def _die_with_parent() -> None:
    _LIBC.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)


def vram_mib(pid: int) -> int | None:
    """GPU memory the process holds, 0 if it holds none, None if nvidia-smi cannot say."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        p, _, mem = line.partition(",")
        if p.strip() == str(pid):
            return int(mem) if mem.strip().isdigit() else None
    return 0


class LlamaServerLLM:
    def __init__(self, model: str, slots: int = 3, num_ctx: int = 8192, num_predict: int = 700, cache: bool = True,
                 port: int = 11439, require_gpu: bool = True, log: Path | None = None,
                 client: httpx.AsyncClient | None = None, start_timeout: float = 180.0, batch: int = 1024) -> None:
        self.model, self.slots, self.port, self.batch = model, slots, port, batch
        self.num_ctx, self.num_predict = num_ctx, num_predict
        self.base_url = f"http://127.0.0.1:{port}"
        self.require_gpu, self.start_timeout = require_gpu, start_timeout
        self.use_cache = cache
        self.calls = self.cache_hits = self.context_overflows = self.yields = self.starts = 0
        self._log = log or RESULTS / "events" / "llama_server.log"
        self._client = client or httpx.AsyncClient(timeout=600)
        self._sem = asyncio.Semaphore(slots)
        self._start_lock = asyncio.Lock()
        self._yield_lock = asyncio.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        # its own cache: a reply made with batched slots is not mixed into Ollama's one-at-a-time replies
        self._cache_path = RESULTS / f"llm_cache_llamacpp_{model.replace(':', '_').replace('/', '_')}_np{slots}.jsonl"
        self._cache: dict[str, str] = {}
        if cache and self._cache_path.exists():
            with self._cache_path.open("rb") as fh:
                for raw in fh:
                    try:
                        rec = json.loads(raw)
                        self._cache[rec["k"]] = rec["v"]
                    except (ValueError, KeyError, TypeError):
                        pass  # a torn last line after a crash costs one re-query
        atexit.register(self._kill)

    def _spawn(self) -> subprocess.Popen[bytes]:
        self._log.parent.mkdir(parents=True, exist_ok=True)
        cmd = server_cmd(blob_path(self.model), self.port, self.slots, self.num_ctx, self.batch)
        with self._log.open("ab") as fh:
            # preexec_fn: one prctl call on a library loaded before the fork; the label scripts start no threads
            return subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, env=server_env(),
                                    preexec_fn=_die_with_parent)  # noqa: PLW1509

    async def _healthy(self) -> bool:
        try:
            return (await self._client.get(f"{self.base_url}/health", timeout=3)).status_code == 200
        except httpx.HTTPError:
            return False

    def _check_on_gpu(self) -> None:
        assert self._proc is not None
        need = blob_path(self.model).stat().st_size / 2**20
        got = vram_mib(self._proc.pid)
        if got is None or got < 0.9 * need:
            raise GPUFallbackError(f"{self.model} on llama-server holds {got} MiB of GPU memory (the model needs "
                                   f"{need:.0f}); refusing its answers. Is the GPU healthy?")

    async def _ensure_server(self) -> None:
        async with self._start_lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            if await self._healthy():
                raise RuntimeError(f"port {self.port} already answers; refusing to label through an unknown server")
            self._proc = self._spawn()
            self.starts += 1
            t0 = time.monotonic()
            while not await self._healthy():
                if self._proc.poll() is not None:
                    raise RuntimeError(f"llama-server exited (rc {self._proc.returncode}); see {self._log}")
                if time.monotonic() - t0 > self.start_timeout:
                    await self.unload()
                    raise RuntimeError(f"llama-server did not come up in {self.start_timeout:.0f}s; see {self._log}")
                await asyncio.sleep(1)
            if self.require_gpu:
                try:
                    self._check_on_gpu()
                except GPUFallbackError:
                    await self.unload()
                    raise

    async def __call__(self, system: str, user: str, mode: str | None = None) -> str:
        if mode is not None:
            raise ValueError(f"LLM mode {mode!r}: the llama-server client gives plain JSON replies only")
        key = hashlib.sha256(f"{self.model}\0{system}\0{user}\0ctx={self.num_ctx}".encode()).hexdigest()
        if self.use_cache and key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        body: dict[str, Any] = {
            "model": self.model, "stream": False, "temperature": 0, "max_tokens": self.num_predict,
            "response_format": {"type": "json_object"}, "chat_template_kwargs": {"enable_thinking": False},
            "messages": ([{"role": "system", "content": system}] if system else []) + [{"role": "user",
                                                                                    "content": user}],
        }
        await self._yield_to_priority()
        async with self._sem:
            for attempt in range(3):
                try:
                    await self._ensure_server()
                    r = await self._client.post(f"{self.base_url}/v1/chat/completions", json=body)
                    r.raise_for_status()
                    data = r.json()
                    text = data["choices"][0]["message"].get("content") or ""
                    if int((data.get("usage") or {}).get("prompt_tokens") or 0) >= self.num_ctx - self.num_predict:
                        self.context_overflows += 1
                    break
                except httpx.HTTPStatusError as e:
                    # 400 = the prompt is longer than the slot's context: the caller records it (llm_fields.ask)
                    if e.response.status_code == 400 or attempt == 2:
                        raise
                    await asyncio.sleep(2)
                except (httpx.HTTPError, KeyError, IndexError):
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2)
        self.calls += 1
        if self.use_cache and text.strip():  # an empty reply is a glitch, not an answer: don't replay it
            self._cache[key] = text
            _append_line(self._cache_path, json.dumps({"k": key, "v": text}))
        return text

    async def _yield_to_priority(self, poll_s: float = 15.0) -> None:
        """The forward runner wants the GPU (app/sandbox/gpu_lock.py): let the requests in flight finish, stop the
        server, and wait until it is done. The next request starts the server again."""
        if not priority_wanted():
            return
        async with self._yield_lock:
            if not priority_wanted():
                return
            for _ in range(self.slots):
                await self._sem.acquire()
            try:
                await self.unload()
                self.yields += 1
                print(f"[gpu-priority] {self.model}: llama-server stopped for the forward runner; waiting", flush=True)
                while priority_wanted():
                    await asyncio.sleep(poll_s)
                print(f"[gpu-priority] {self.model}: resuming", flush=True)
            finally:
                for _ in range(self.slots):
                    self._sem.release()

    async def unload(self) -> None:
        """Stop the server so the GPU is free now."""
        p, self._proc = self._proc, None
        if p is None or p.poll() is not None:
            return
        p.terminate()
        t0 = time.monotonic()
        while p.poll() is None:
            if time.monotonic() - t0 > 20:
                p.kill()
                break
            await asyncio.sleep(0.2)
        p.wait()

    def _kill(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
