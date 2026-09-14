"""A misbehaving agent must be killed with JailError, never hang or crash the orchestrator.

Each test swaps in a small hostile worker script. They run both under bubblewrap (when
installed) and unjailed, because the limits and protocol checks apply in both modes.
"""
import shutil
import textwrap
import time

import pytest

from app.sandbox.jail import AgentJail, JailError, JailLimits

MODES = [pytest.param(False, id="unjailed")]
if shutil.which("bwrap"):
    MODES.append(pytest.param(True, id="bwrap"))


_which = shutil.which


@pytest.fixture
def jailed(request, monkeypatch):
    """True: run under bubblewrap. False: force unjailed mode even where bubblewrap exists."""
    if not request.param:
        monkeypatch.setattr("app.sandbox.jail.shutil.which", lambda n: None if n == "bwrap" else _which(n))
    return request.param


async def _echo_llm(system: str, user: str) -> str:
    return '{"p_up": 0.5}'


def _worker(tmp_path, body: str):
    path = tmp_path / "evil_worker.py"
    path.write_text("import json, sys\nline = sys.stdin.readline()\n" + textwrap.dedent(body))
    return path


def _jail(tmp_path, body, jailed, **limits):
    return AgentJail(_echo_llm, allow_unjailed=not jailed, worker=_worker(tmp_path, body),
                     limits=JailLimits(**limits))


def _ok():
    return 'print(json.dumps({"result": {"ok": True}}), flush=True)\n'


@pytest.mark.parametrize("jailed", MODES, indirect=True)
async def test_resource_limits_are_applied_inside_the_agent(tmp_path, jailed):
    body = """
    import resource
    lim = {n: resource.getrlimit(getattr(resource, n))[1] for n in ("RLIMIT_AS", "RLIMIT_NOFILE", "RLIMIT_FSIZE")}
    print(json.dumps({"result": lim}), flush=True)
    """
    async with _jail(tmp_path, body, jailed, memory_mb=512, open_files=32, file_size_mb=1) as jail:
        r = await jail.call({"task": "x"})
    assert r == {"RLIMIT_AS": 512 * 2**20, "RLIMIT_NOFILE": 32, "RLIMIT_FSIZE": 2**20}
    assert jail.jailed == jailed


@pytest.mark.parametrize("jailed", MODES, indirect=True)
async def test_memory_hog_is_stopped(tmp_path, jailed):
    body = "x = bytearray(2 * 1024**3)\n" + _ok()
    async with _jail(tmp_path, body, jailed, memory_mb=256) as jail:
        with pytest.raises(JailError, match="exited"):
            await jail.call({"task": "x"})


@pytest.mark.parametrize("jailed", MODES, indirect=True)
async def test_hung_agent_times_out_and_is_killed(tmp_path, jailed):
    body = "import time\ntime.sleep(60)\n"
    t0 = time.monotonic()
    async with _jail(tmp_path, body, jailed, response_timeout_s=1) as jail:
        with pytest.raises(JailError, match="did not respond"):
            await jail.call({"task": "x"})
        assert jail._proc is not None and jail._proc.returncode is not None  # really killed
    assert time.monotonic() - t0 < 10


@pytest.mark.parametrize("jailed", MODES, indirect=True)
async def test_oversized_message_is_rejected(tmp_path, jailed):
    body = 'sys.stdout.write("x" * (2 * 1024 * 1024) + "\\n"); sys.stdout.flush()\nimport time; time.sleep(5)\n'
    async with _jail(tmp_path, body, jailed, max_message_bytes=1024 * 1024) as jail:
        with pytest.raises(JailError, match="larger than"):
            await jail.call({"task": "x"})


@pytest.mark.parametrize("jailed", MODES, indirect=True)
async def test_llm_request_flood_is_rejected_before_calling_the_llm(tmp_path, jailed):
    calls = []

    async def llm(system: str, user: str) -> str:
        calls.append(1)
        return "{}"

    body = """
    reqs = [{"id": i, "system": "s", "user": "u"} for i in range(1000)]
    print(json.dumps({"llm_requests": reqs}), flush=True)
    import time; time.sleep(5)
    """
    jail = AgentJail(llm, allow_unjailed=not jailed, worker=_worker(tmp_path, body),
                     limits=JailLimits(max_llm_requests_per_message=100))
    async with jail:
        with pytest.raises(JailError, match="too many"):
            await jail.call({"task": "x"})
    assert calls == []


@pytest.mark.parametrize("jailed", MODES, indirect=True)
async def test_oversized_or_malformed_prompt_is_rejected(tmp_path, jailed):
    body = """
    print(json.dumps({"llm_requests": [{"id": 0, "system": "s", "user": "u" * 5000}]}), flush=True)
    import time; time.sleep(5)
    """
    async with _jail(tmp_path, body, jailed, max_prompt_chars=1000) as jail:
        with pytest.raises(JailError, match="malformed or oversized"):
            await jail.call({"task": "x"})


@pytest.mark.parametrize("jailed", MODES, indirect=True)
async def test_invalid_json_is_rejected(tmp_path, jailed):
    body = 'print("not json", flush=True)\nimport time; time.sleep(5)\n'
    async with _jail(tmp_path, body, jailed) as jail:
        with pytest.raises(JailError, match="invalid JSON"):
            await jail.call({"task": "x"})


@pytest.mark.parametrize("jailed", MODES, indirect=True)
async def test_stderr_flood_does_not_deadlock(tmp_path, jailed):
    # 8 MB on stderr would fill an undrained pipe (64 KB) and block the agent forever
    body = 'sys.stderr.write("e" * (8 * 1024 * 1024)); sys.stderr.flush()\n' + _ok()
    async with _jail(tmp_path, body, jailed, response_timeout_s=20) as jail:
        assert await jail.call({"task": "x"}) == {"ok": True}


async def test_real_worker_still_works_under_default_limits():
    async with AgentJail(_echo_llm, allow_unjailed=not shutil.which("bwrap")) as jail:
        probe = await jail.probe(["/etc/hostname"])
    assert probe["readable_forbidden_paths"] == [] or not jail.jailed
