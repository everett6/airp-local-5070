"""
The forecasting AGENT. Runs inside the jail (`app/sandbox/jail.py`): no
network, no view of the repo's data directory, stdlib only. It can see
exactly what the orchestrator writes to its stdin — anonymized, past-only
observations plus point-in-time memory — and nothing else.

It cannot call the LLM directly (no network). Instead it writes an
`llm_requests` line to stdout; the orchestrator forwards the prompts to
Ollama and writes the replies back. The orchestrator only relays text; it
never adds data.

Protocol (one JSON object per line):
  in : {"task": "predict", "arm": ..., "items": [...], "memory": {...}}
  out: {"llm_requests": [{"id": ..., "system": ..., "user": ...}, ...]}
  in : {"llm_responses": {"<id>": "<text>", ...}}
  out: {"result": {...}}

Self-improvement ("memory" arm, after FinMem arXiv:2311.13743 and Reflexion):
  - `lessons`: short rules the model wrote itself by reflecting on its
    RESOLVED past mistakes (the orchestrator only includes lessons written
    at a cutoff <= the current one).
  - `stacker`: a logistic regression fitted here on resolved records only,
    combining the LLM's probability with simple features, so the system
    learns how far to trust the LLM rather than trusting it blindly.
"""
from __future__ import annotations

import json
import math
import sys
from typing import Any

SYSTEM = (
    "You are a quantitative analyst. You receive an anonymized daily price series "
    "(rebased to 100, oldest first, last value is today) for one asset and for the "
    "overall market. You do not know which asset or which calendar dates these are; "
    "do not guess. Estimate the probability that the asset's price is HIGHER in 5 "
    "trading days than today. Markets are noisy: well-calibrated probabilities are "
    "usually between 0.40 and 0.60. Reply with JSON only: "
    '{"p_up": <number 0-1>, "reason": "<max 20 words>"}'
)

SYSTEM_EXCESS = SYSTEM.replace(
    "the probability that the asset's price is HIGHER in 5 trading days than today",
    "the probability that the asset OUTPERFORMS the market (higher 5-trading-day return "
    "than the market series) over the next 5 trading days",
).replace('"p_up"', '"p_up"')

REFLECT_SYSTEM = (
    "You review a forecaster's resolved 5-day direction calls on anonymized assets. "
    "Write at most 5 short, general, testable lessons (each under 25 words) that would "
    "have improved calibration or accuracy. No asset names or dates. Reply JSON only: "
    '{"lessons": ["...", "..."]}'
)


def _send(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _recv() -> dict[str, Any]:
    line = sys.stdin.readline()
    if not line:
        raise SystemExit("orchestrator closed the pipe")
    out: dict[str, Any] = json.loads(line)
    return out


def features(asset: list[float], market: list[float]) -> dict[str, float]:
    def ret(xs: list[float], n: int) -> float:
        return xs[-1] / xs[-1 - n] - 1.0

    daily = [asset[i] / asset[i - 1] - 1.0 for i in range(1, len(asset))]
    last20 = daily[-20:]
    mean = sum(last20) / len(last20)
    vol = math.sqrt(sum((r - mean) ** 2 for r in last20) / len(last20)) * math.sqrt(252)
    gains = [max(r, 0.0) for r in daily[-14:]]
    losses = [max(-r, 0.0) for r in daily[-14:]]
    avg_loss = sum(losses) / 14
    rsi = 100.0 if avg_loss == 0 else 100 - 100 / (1 + (sum(gains) / 14) / avg_loss)
    ma50 = sum(asset[-50:]) / 50
    return {
        "ret_1d": ret(asset, 1), "ret_5d": ret(asset, 5), "ret_20d": ret(asset, 20),
        "ret_60d": ret(asset, 60), "vol_20d_ann": vol, "rsi_14": rsi,
        "dist_ma50": asset[-1] / ma50 - 1.0,
        "mkt_ret_5d": ret(market, 5), "mkt_ret_20d": ret(market, 20),
    }


def _pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def build_prompt(item: dict[str, Any], memory: dict[str, Any] | None) -> str:
    f = item["features"]
    asset = item["asset"]
    parts = [
        f"Asset last 30 closes: {[round(v, 1) for v in asset[-30:]]}",
        f"Market last 30 closes: {[round(v, 1) for v in item['market'][-30:]]}",
        (
            "Computed features: "
            f"1d {_pct(f['ret_1d'])}, 5d {_pct(f['ret_5d'])}, 20d {_pct(f['ret_20d'])}, "
            f"60d {_pct(f['ret_60d'])}, 20d annualized vol {f['vol_20d_ann'] * 100:.1f}%, "
            f"RSI14 {f['rsi_14']:.0f}, distance from 50d MA {_pct(f['dist_ma50'])}, "
            f"market 5d {_pct(f['mkt_ret_5d'])}, market 20d {_pct(f['mkt_ret_20d'])}."
        ),
    ]
    if memory:
        tr = memory.get("track_record")
        if tr and tr.get("n", 0) >= 20:
            parts.append(
                f"Your own resolved track record so far: {tr['n']} calls, hit rate "
                f"{tr['hit_rate'] * 100:.1f}%, Brier {tr['brier']:.3f}, base rate of up moves "
                f"{tr['base_rate'] * 100:.1f}%, your average p_up {tr['avg_p']:.2f}."
            )
        if memory.get("lessons"):
            parts.append("Lessons you wrote from your past mistakes:\n- " + "\n- ".join(memory["lessons"]))
    return "\n".join(parts)


def parse_p(text: str) -> float:
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        p = float(json.loads(text[start:end])["p_up"])
        if math.isfinite(p):
            return min(max(p, 0.01), 0.99)
    except (ValueError, KeyError, TypeError):
        pass
    return 0.5  # unparseable reply counts as "no information", never skipped


FEATURE_KEYS = ["llm_logit", "ret_5d", "ret_20d", "dist_ma50", "mkt_ret_5d", "rsi_c"]


def _row(p_llm: float, f: dict[str, float]) -> list[float]:
    return [
        math.log(p_llm / (1 - p_llm)), f["ret_5d"] * 10, f["ret_20d"] * 5,
        f["dist_ma50"] * 5, f["mkt_ret_5d"] * 10, (f["rsi_14"] - 50) / 25,
    ]


def fit_logistic(rows: list[list[float]], ys: list[int], l2: float = 0.05, iters: int = 300) -> list[float]:
    """Batch gradient descent with a real L2 penalty on the weights (not the
    intercept). v1 used a penalty divided by n, which was effectively zero
    and let the stacker chase noise (worse Brier than the raw LLM)."""
    k = len(rows[0]) + 1
    w = [0.0] * k
    n = len(rows)
    lr = 0.5
    for _ in range(iters):
        grad = [0.0] * k
        for x, y in zip(rows, ys, strict=True):
            z = w[0] + sum(wi * xi for wi, xi in zip(w[1:], x, strict=True))
            p = 1 / (1 + math.exp(-max(min(z, 30), -30)))
            err = p - y
            grad[0] += err
            for i, xi in enumerate(x):
                grad[i + 1] += err * xi
        w[0] -= lr * grad[0] / n
        for i in range(1, k):
            w[i] -= lr * (grad[i] / n + l2 * w[i])
    return w


def _log_loss(ps: list[float], ys: list[int]) -> float:
    return -sum(math.log(p if y else 1 - p) for p, y in zip(ps, ys, strict=True)) / len(ys)


def guarded_stacker(resolved: list[dict[str, Any]], min_n: int = 200) -> tuple[list[float] | None, dict[str, Any]]:
    """Self-improvement with a guardrail: fit on the older 70% of resolved
    records, and adopt the stacker only if it beats the raw LLM probability
    on the most recent 30% (out-of-sample, still all in the past)."""
    if len(resolved) < min_n:
        return None, {"stacker": "not_enough_data", "n": len(resolved)}
    rows = [_row(r["p_llm"], r["features"]) for r in resolved]
    ys = [int(r["up"]) for r in resolved]
    cut = int(len(rows) * 0.7)
    w_tr = fit_logistic(rows[:cut], ys[:cut])
    ll_stack = _log_loss([predict_logistic(w_tr, x) for x in rows[cut:]], ys[cut:])
    ll_llm = _log_loss([r["p_llm"] for r in resolved[cut:]], ys[cut:])
    info = {"stacker_holdout_ll": round(ll_stack, 4), "llm_holdout_ll": round(ll_llm, 4), "n": len(rows)}
    if ll_stack >= ll_llm:
        return None, {**info, "stacker": "rejected"}
    return fit_logistic(rows, ys), {**info, "stacker": "adopted"}


def predict_logistic(w: list[float], x: list[float]) -> float:
    z = w[0] + sum(wi * xi for wi, xi in zip(w[1:], x, strict=True))
    return 1 / (1 + math.exp(-max(min(z, 30), -30)))


def run_predict(msg: dict[str, Any]) -> dict[str, Any]:
    items = msg["items"]
    memory = msg.get("memory")
    for it in items:
        it["features"] = features(it["asset"], it["market"])
    system = SYSTEM_EXCESS if msg.get("target") == "excess" else SYSTEM
    _send({"llm_requests": [
        {"id": it["id"], "system": system, "user": build_prompt(it, memory)} for it in items
    ]})
    replies = _recv()["llm_responses"]

    stack_w, stack_info = guarded_stacker((memory or {}).get("resolved", [])) if memory else (None, {})

    out = []
    for it in items:
        p_llm = parse_p(replies.get(it["id"], ""))
        p_final = predict_logistic(stack_w, _row(p_llm, it["features"])) if stack_w else p_llm
        out.append({"id": it["id"], "p_llm": p_llm, "p_final": p_final, "features": it["features"]})
    return {"predictions": out, "stacker_active": stack_w is not None, "stacker_info": stack_info}


def run_reflect(msg: dict[str, Any]) -> dict[str, Any]:
    recs = msg["records"]
    lines = [
        f"p_up={r['p_llm']:.2f} actual={'UP' if r['up'] else 'DOWN'} "
        f"5d={_pct(r['features']['ret_5d'])} 20d={_pct(r['features']['ret_20d'])} "
        f"rsi={r['features']['rsi_14']:.0f} mkt5d={_pct(r['features']['mkt_ret_5d'])}"
        for r in recs
    ]
    hits = sum((r["p_llm"] >= 0.5) == bool(r["up"]) for r in recs)
    user = (
        f"{len(recs)} most recent resolved calls, {hits} correct:\n" + "\n".join(lines)
        + "\n\nPrevious lessons (revise, keep what still holds):\n- "
        + "\n- ".join(msg.get("previous_lessons") or ["(none)"])
    )
    _send({"llm_requests": [{"id": "reflect", "system": REFLECT_SYSTEM, "user": user}]})
    text = _recv()["llm_responses"].get("reflect", "")
    try:
        lessons = json.loads(text[text.index("{"): text.rindex("}") + 1])["lessons"]
        lessons = [str(x)[:200] for x in lessons][:5]
    except (ValueError, KeyError, TypeError):
        lessons = list(msg.get("previous_lessons") or [])
    return {"lessons": lessons}


def probe_isolation(paths: list[str]) -> dict[str, Any]:
    """Tries to escape: read the real price file and open a network socket.
    Both must fail inside the jail."""
    import socket

    readable = []
    for p in paths:
        try:
            with open(p, "rb") as fh:
                fh.read(1)
            readable.append(p)
        except OSError:
            pass
    net_ok = False
    for host, port in (("1.1.1.1", 443), ("127.0.0.1", 11434)):
        try:
            with socket.create_connection((host, port), timeout=2):
                net_ok = True
        except OSError:
            pass
    return {"readable_forbidden_paths": readable, "network_reachable": net_ok}


def main() -> None:
    while True:
        line = sys.stdin.readline()
        if not line:
            return
        msg = json.loads(line)
        task = msg["task"]
        if task == "predict":
            _send({"result": run_predict(msg)})
        elif task == "reflect":
            _send({"result": run_reflect(msg)})
        elif task == "probe":
            _send({"result": probe_isolation(msg["paths"])})
        else:
            _send({"error": f"unknown task {task}"})


if __name__ == "__main__":
    main()
