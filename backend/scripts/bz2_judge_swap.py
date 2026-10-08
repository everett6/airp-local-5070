"""BZ2: Ternary Bonsai 2 27B against the deployed Bonsai as Night's judge (docs/PLAN_60_V2.md, BZ2, fixed 2026-10-07).

    run    freeze the 7 Oct cards, then one call per card per arm, in chunks of 10 that give way to Night's research
    score  once >= 90% of short horizons have matured: the Q rule, then register() the verdict

Both arms get the same single call (forced_call.PROMPT + the stored judge_card, no repair, lookups or double check).
A run that stops is continued by running it again: cards already answered are skipped, nothing is asked twice.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import signal
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx
from forward_events import Ollama, gpu_free
from jan_forward import MODELS
from llm_fields import ask, parse

from app.forward.ledger import write_atomic
from app.portfolio import auto_trader as at
from app.sandbox.dsr import register
from app.sandbox.forced_call import PROMPT
from app.sandbox.forced_call import check as forced_check
from app.sandbox.gpu_lock import priority_wanted
from app.sandbox.llamacpp_client import _die_with_parent, vram_mib
from app.sandbox.walkforward import OllamaLLM

BACKEND = Path(__file__).resolve().parents[1]
RESEARCH = BACKEND / "results" / "forward" / "deep_research"
OUT = BACKEND / "results" / "bz2"
LOCK = BACKEND / "results" / "forward" / "autorun.lock"
DAY = "2026-10-07"
HORIZONS = ("day", "medium", "long", "short")  # Night's deep-research horizons, in its order
CHUNK = 10
YIELD_S = 90  # pause after each chunk: Night checks the lock every loop (~13 s)
NUM_CTX, NUM_PREDICT = 16384, 1600
A_MODEL = "bonsai-27b:latest"
B_GGUF = Path.home() / "models" / "Ternary-Bonsai-2-27B" / "Ternary-Bonsai-2-27B-PQ2_0.gguf"
B_SERVER = Path.home() / "prism-llama.cpp" / "build" / "bin" / "llama-server"
A_PORT, B_PORT = 11448, 11449
SMOKE = ("You are a careful assistant. Reply with one JSON object only.",
         'Return {"capital": <the capital of France>, "sum": <17 + 25>} as JSON.')


def freeze(root: Path = RESEARCH, day: str = DAY) -> list[dict[str, Any]]:
    """The latest decided row per ticker decided on `day` (UTC), with its stored judge_card."""
    latest: dict[str, dict[str, Any]] = {}
    for f in root.glob("*/*.json"):
        if f.name == "summary.json":
            continue
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or d.get("status") != "decided" or not d.get("judge_card"):
            continue
        if not str(d.get("decided_at", "")).startswith(day):
            continue
        t = d["ticker"]
        if t not in latest or d["decided_at"] > latest[t]["decided_at"]:
            latest[t] = d
    return [{"ticker": t, "decided_at": latest[t]["decided_at"], "card": latest[t]["judge_card"]} for t in sorted(latest)]


def card_digest(cards: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps([(c["ticker"], c["decided_at"]) for c in cards]).encode()).hexdigest()


def judged(raw: dict[str, Any] | None, card: str) -> tuple[dict[str, str], bool]:
    """Labels per horizon and V's test: the reply parsed and every horizon got a side."""
    verdict = forced_check(raw, card, HORIZONS)
    labels = {h: str(v["label"]) for h, v in verdict.items()}
    return labels, raw is not None and all(x != "no_call" for x in labels.values())


def side(label: str) -> int:
    return 1 if label in ("4", "5") else -1 if label in ("1", "2") else 0


# ---------------------------------------------------------------- arm B: PrismML llama-server

class PrismServer:
    def __init__(self, log: Path, binary: Path = B_SERVER, gguf: Path = B_GGUF, port: int = B_PORT) -> None:
        cmd = [str(binary), "--model", str(gguf), "--port", str(port), "--host", "127.0.0.1", "--no-webui",
               "-c", str(NUM_CTX), "-np", "1", "-ngl", "99", "-fa", "on", "-b", "1024", "-ub", "1024", "--jinja"]
        self.url = f"http://127.0.0.1:{port}"
        self.proc = subprocess.Popen(cmd, stdout=log.open("ab"), stderr=subprocess.STDOUT,
                                     preexec_fn=_die_with_parent)  # noqa: PLW1509
        t0 = time.monotonic()
        while True:
            try:
                if httpx.get(f"{self.url}/health", timeout=3).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if self.proc.poll() is not None or time.monotonic() - t0 > 300:
                self.stop()
                raise RuntimeError(f"PrismML llama-server did not come up; see {log}")
            time.sleep(1)
        got = vram_mib(self.proc.pid)
        if got is None or got < 0.9 * gguf.stat().st_size / 2**20:
            self.stop()
            raise RuntimeError(f"Bonsai 2 holds {got} MiB of GPU memory: not fully on the GPU")

    async def __call__(self, system: str, user: str, client: httpx.AsyncClient) -> dict[str, Any]:
        body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": 0, "max_tokens": NUM_PREDICT, "response_format": {"type": "json_object"},
                "reasoning_effort": "none", "stream": False}
        r = await client.post(f"{self.url}/v1/chat/completions", json=body, timeout=600)
        if r.status_code == 400 and "context" in r.text:
            return {"reply": "", "overflow": True}
        r.raise_for_status()
        d = r.json()
        t = d.get("timings") or {}
        return {"reply": d["choices"][0]["message"].get("content") or "", "overflow": False,
                "gen_tokens": (d.get("usage") or {}).get("completion_tokens"),
                "prompt_tokens": (d.get("usage") or {}).get("prompt_tokens"),
                "decode_tok_s": t.get("predicted_per_second"), "prompt_tok_s": t.get("prompt_per_second")}

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


async def smoke(out: Path) -> dict[str, Any]:
    """The registered smoke test: a fixed non-card prompt, to see that B's runtime writes sense."""
    srv = PrismServer(out / "bonsai2_server.log")
    try:
        async with httpx.AsyncClient() as client:
            res = await srv(*SMOKE, client)
    finally:
        srv.stop()
    raw = parse(res["reply"])
    ok = isinstance(raw, dict) and "paris" in str(raw.get("capital", "")).lower() and str(raw.get("sum")) == "42"
    return {**res, "ok": ok, "at": datetime.now(UTC).isoformat()}


# ---------------------------------------------------------------- the run

async def take_lock(poll_s: float = 30.0) -> Any:
    """Night's research lock, held for one chunk; waits while Night researches, the forward runner wants the GPU,
    or anything else is on the GPU."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    while True:
        fh = LOCK.open("a")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            await asyncio.sleep(poll_s)
            continue
        if not priority_wanted() and gpu_free():
            return fh
        fh.close()
        await asyncio.sleep(poll_s)


async def run_arm(arm: str, chunk: list[dict[str, Any]], out: Path, done: set[tuple[str, str]]) -> list[dict[str, Any]]:
    todo = [c for c in chunk if (c["ticker"], arm) not in done]
    if not todo:
        return []
    recs = []
    if arm == "A":
        srv = Ollama(A_PORT, MODELS, 1, out / "bonsai_a_server.log")
        llm = OllamaLLM(A_MODEL, base_url=f"http://127.0.0.1:{A_PORT}", concurrency=1, num_ctx=NUM_CTX,
                        num_predict=NUM_PREDICT, cache=False, require_gpu=True)
        try:
            for c in todo:
                t = time.monotonic()
                reply, overflow = await ask(llm, PROMPT, c["card"])
                recs.append({"reply": reply, "overflow": overflow, "wall_s": time.monotonic() - t, **c})
        finally:
            try:
                await llm.unload()
            finally:
                srv.stop()
    else:
        prism = PrismServer(out / "bonsai2_server.log")
        try:
            async with httpx.AsyncClient() as client:
                for c in todo:
                    t = time.monotonic()
                    res = await prism(PROMPT, c["card"], client)
                    recs.append({**res, "wall_s": time.monotonic() - t, **c})
        finally:
            prism.stop()
    rows = []
    for r in recs:
        raw = None if r["overflow"] else parse(r["reply"])
        labels, valid = judged(raw, r["card"])
        row = {k: v for k, v in r.items() if k != "card"} | {"arm": arm, "labels": labels, "valid": valid,
                                                             "at": datetime.now(UTC).isoformat()}
        with (out / "replies.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")
        rows.append(row)
    return rows


def replies(out: Path = OUT) -> list[dict[str, Any]]:
    p = out / "replies.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def run_summary(rows: list[dict[str, Any]], n_cards: int) -> dict[str, Any]:
    """V and S, decided on the run day (BZ2 verdict rules)."""
    arm = {a: [r for r in rows if r["arm"] == a] for a in ("A", "B")}
    v = {a: sum(r["valid"] for r in x) / max(1, len(x)) for a, x in arm.items()}
    s = {a: statistics.median(r["wall_s"] for r in x) if x else None for a, x in arm.items()}
    pairs = {(r["ticker"]): r for r in arm["A"]}
    agree = [r["labels"][h] == pairs[r["ticker"]]["labels"][h] for r in arm["B"] if r["ticker"] in pairs
             for h in HORIZONS if side(r["labels"][h]) and side(pairs[r["ticker"]]["labels"][h])]
    agree_side = [side(r["labels"][h]) == side(pairs[r["ticker"]]["labels"][h]) for r in arm["B"]
                  if r["ticker"] in pairs for h in HORIZONS
                  if side(r["labels"][h]) and side(pairs[r["ticker"]]["labels"][h])]
    tok = [r["decode_tok_s"] for r in arm["B"] if r.get("decode_tok_s")]
    v_pass = v["B"] >= v["A"] - 0.02
    s_pass = s["A"] is not None and s["B"] is not None and s["B"] <= 1.5 * s["A"]
    return {"cards": n_cards, "answered": {a: len(x) for a, x in arm.items()}, "valid_rate": v,
            "median_wall_s": s, "b_decode_tok_s_median": statistics.median(tok) if tok else None,
            "same_label": sum(agree) / len(agree) if agree else None,
            "same_side": sum(agree_side) / len(agree_side) if agree_side else None,
            "V_pass": v_pass, "S_pass": s_pass, "at": datetime.now(UTC).isoformat()}


async def run(out: Path = OUT) -> None:
    out.mkdir(parents=True, exist_ok=True)
    frozen = out / "cards.json"
    if frozen.exists():
        meta = json.loads(frozen.read_text())
        cards = [c for c in freeze() if (c["ticker"], c["decided_at"]) in {tuple(x) for x in meta["cards"]}]
        if card_digest(cards) != meta["sha256"]:
            raise SystemExit("the frozen card set changed on disk; refusing to continue")
    else:
        cards = freeze()
        write_atomic(frozen, json.dumps({"day": DAY, "sha256": card_digest(cards), "n": len(cards),
                                         "cards": [(c["ticker"], c["decided_at"]) for c in cards],
                                         "frozen_at": datetime.now(UTC).isoformat()}, indent=1) + "\n")
    print(f"BZ2: {len(cards)} cards, sha256 {card_digest(cards)[:12]}", flush=True)
    smoke_path = out / "smoke.json"
    if not smoke_path.exists():
        fh = await take_lock()
        try:
            res = await smoke(out)
        finally:
            fh.close()
        write_atomic(smoke_path, json.dumps(res, indent=1) + "\n")
        print(f"smoke: {'ok' if res['ok'] else 'FAILED'} {res['reply'][:120]!r}", flush=True)
    if not json.loads(smoke_path.read_text())["ok"]:
        raise SystemExit("B's smoke test failed: the runtime may be swapped once (BZ2 rules); no card was judged")
    done = {(r["ticker"], r["arm"]) for r in replies(out)}
    for i in range(0, len(cards), CHUNK):
        chunk = cards[i:i + CHUNK]
        if all((c["ticker"], a) in done for c in chunk for a in ("A", "B")):
            continue
        fh = await take_lock()
        try:
            for arm in (("A", "B") if (i // CHUNK) % 2 == 0 else ("B", "A")):
                rows = await run_arm(arm, chunk, out, done)
                done |= {(r["ticker"], arm) for r in rows}
        finally:
            fh.close()
        print(f"chunk {i // CHUNK + 1}/{-(-len(cards) // CHUNK)} done", flush=True)
        await asyncio.sleep(YIELD_S)  # Night's loop gets the lock first if it has research due
    summary = run_summary(replies(out), len(cards))
    write_atomic(out / "run_summary.json", json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary, indent=1), flush=True)
    if not (summary["V_pass"] and summary["S_pass"]):
        register({"trial": "bz2_bonsai2_judge", "date": datetime.now(UTC).date().isoformat(), "kind": "model_swap",
                  "result": "fail", "failed_on": [k for k in ("V_pass", "S_pass") if not summary[k]],
                  "valid_rate": summary["valid_rate"], "median_wall_s": summary["median_wall_s"]})
        print("BZ2 FAIL on the run-day rules: Night keeps the deployed Bonsai", flush=True)


# ---------------------------------------------------------------- the score (Q)

def q_rule(rows: list[dict[str, Any]], px: dict[str, Any], score: Any, entry_session: Any,
           horizon: str = "short") -> dict[str, Any]:
    """Signed hedged returns on calls where both arms took a side, for one horizon."""
    by = {(r["ticker"], r["arm"]): r for r in rows}
    tickers = sorted({r["ticker"] for r in rows})
    ret: dict[str, list[float]] = {"A": [], "B": []}
    due = matured = 0
    for t in tickers:
        a, b = by.get((t, "A")), by.get((t, "B"))
        if not a or not b:
            continue
        sa, sb = side(a["labels"][horizon]), side(b["labels"][horizon])
        if not sa or not sb:
            continue
        due += 1
        e = entry_session(a["decided_at"])
        x = at.add_sessions(e, at.HOLD[horizon])
        va, vb = score(t, sa, e, x, px), score(t, sb, e, x, px)
        if va is None or vb is None:
            continue
        matured += 1
        ret["A"].append(va)
        ret["B"].append(vb)
    stat = {k: {"n": len(v), "mean": statistics.mean(v) if v else None,
                "hit": sum(x > 0 for x in v) / len(v) if v else None} for k, v in ret.items()}
    q_pass = bool(ret["A"]) and stat["B"]["mean"] >= stat["A"]["mean"] and stat["B"]["hit"] >= stat["A"]["hit"] - 0.02
    return {"horizon": horizon, "pairs": due, "matured": matured, "stats": stat, "Q_pass": q_pass}


def score_main(out: Path = OUT) -> None:
    import fund_report as fr

    from app.data_ingestion.bars import key
    from app.portfolio import account_book as ab
    from app.portfolio.broker import PAPER, Alpaca
    summary = json.loads((out / "run_summary.json").read_text())
    if (out / "verdict.json").exists():
        raise SystemExit("BZ2 was already scored once")
    if not (summary["V_pass"] and summary["S_pass"]):
        raise SystemExit("BZ2 already failed on the run-day rules (registered then); nothing to score")
    rows = replies(out)
    k, s = key("AIRP_AUTO_ALPACA_KEY_ID"), key("AIRP_AUTO_ALPACA_SECRET_KEY")
    a = Alpaca(k, s, PAPER, risk_policy=ab.SANDBOX_RISK) if k and s else None
    syms = sorted({r["ticker"].replace("-", ".") for r in rows} | {"QQQ"})
    px = fr.bars(a, syms, fr.entry_session(min(r["decided_at"] for r in rows)))
    q = q_rule([{**r, "ticker": r["ticker"].replace("-", ".")} for r in rows], px, fr.score, fr.entry_session)
    if q["pairs"] == 0 or q["matured"] < 0.9 * q["pairs"]:
        print(f"not yet: {q['matured']} of {q['pairs']} short horizons matured (needs 90%)")
        return
    day = q_rule([{**r, "ticker": r["ticker"].replace("-", ".")} for r in rows], px, fr.score, fr.entry_session, "day")
    passed = summary["V_pass"] and summary["S_pass"] and q["Q_pass"]
    verdict = {"run": summary, "Q": q, "day_secondary": day, "result": "pass" if passed else "fail",
               "at": datetime.now(UTC).isoformat()}
    write_atomic(out / "verdict.json", json.dumps(verdict, indent=1) + "\n")
    register({"trial": "bz2_bonsai2_judge", "date": datetime.now(UTC).date().isoformat(), "kind": "model_swap",
              "result": verdict["result"], "short_pairs": q["matured"], "a_mean": q["stats"]["A"]["mean"],
              "b_mean": q["stats"]["B"]["mean"], "a_hit": q["stats"]["A"]["hit"], "b_hit": q["stats"]["B"]["hit"]})
    print(json.dumps(verdict, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("step", choices=("run", "score"))
    args = ap.parse_args()
    os.chdir(BACKEND)
    if args.step == "run":
        asyncio.run(run())
    else:
        score_main()


if __name__ == "__main__":
    main()
