"""Runs one snippet of agent-written Python inside the jail (stdlib only, no network, no files).

stdin:  {"code": "..."}      stdout: {"stdout": "...", "error": "..." | null}
"""
import contextlib
import io
import json
import sys
import traceback

MAX_OUT = 8000


def main() -> None:
    req = json.loads(sys.stdin.read())
    buf = io.StringIO()
    error = None
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(req["code"], "<agent>", "exec"), {"__name__": "__agent__"})  # noqa: S102 - jailed
    except BaseException:  # noqa: BLE001 - report everything back to the agent
        error = traceback.format_exc(limit=3)[-2000:]
    sys.stdout.write(json.dumps({"stdout": buf.getvalue()[:MAX_OUT], "error": error}))


if __name__ == "__main__":
    main()
