"""Arm F, a self-improving Bonsai (docs/PLAN_60_V2.md "Arm F", fixed 2026-09-28): champion/challenger on the live
bull/bear lens. No money.

    python scripts/self_improve.py --dir results/forward/events          # reflect if due, then judge
    python scripts/self_improve.py --dir results/forward/events --status

Versions live in <dir>/bb_versions.jsonl (v0 = arm E's frozen PROMPT_BB, labelled into bull_bear.jsonl; version n
labels into bb_v<n>.jsonl, by scripts/net_read_shadow.py through active_lenses()). Monthly, Bonsai reads a code-built
summary of the champion's matured calls and writes at most 5 general lessons; the challenger = the champion's prompt
plus those lessons. The paired monthly IC decides promotion. Problems print "LEARN ALERT: ..." and never fail the job.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import numpy as np
import pandas as pd
from llm_fields import FIELDS_BB, PROMPT_BB, ask, parse

from app.forward.ledger import Ledger
from app.forward.schedule import NY

PORT = 11441
MIN_MATURED, SHOW, WORST, MAX_LESSONS, MAX_WORDS = 40, 80, 15, 5, 30
PROMOTE = {"min_paired": 100, "min_months": 2, "retire_at": 200}
SCORE = {"bullish": 1, "neutral": 0, "bearish": -1}
PROMPT_REFLECT = """You make bull/bear calls on company earnings releases: bullish if the release makes the next week
better for the stock than a typical release, bearish if worse. Below is a summary of your past calls whose results
are now known (the stock's 5-day return against its sector), then your worst misses with the bull and bear points
you wrote at the time.
Write at most 5 lessons that would make your future calls more accurate. Each lesson: one general rule, at most 30
words, about how to weigh evidence in a release. Never name a company, ticker, date or sector. If there is no clear
lesson, write fewer.
Reply with ONLY one JSON object on one line: {"lessons": ["...", "..."]}"""


def versions(d: Path) -> list[dict[str, Any]]:
    f = d / "bb_versions.jsonl"
    vs = [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []
    return vs or [{"v": 0, "status": "champion", "lessons": [], "created_at": None, "parent": None}]


def save(d: Path, vs: list[dict[str, Any]]) -> None:
    (d / "bb_versions.jsonl").write_text("".join(json.dumps(v) + "\n" for v in vs))


def fname(v: int) -> str:
    return "bull_bear.jsonl" if v == 0 else f"bb_v{v}.jsonl"


def prompt(lessons: list[str]) -> str:
    if not lessons:
        return PROMPT_BB
    return PROMPT_BB + "\nLessons from your past calls:\n" + "\n".join(f"- {x}" for x in lessons)


def champion(vs: list[dict[str, Any]]) -> dict[str, Any]:
    return next(v for v in reversed(vs) if v["status"] == "champion")


def challenger(vs: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((v for v in vs if v["status"] == "challenger"), None)


def active_lenses(d: Path) -> dict[str, tuple[str, dict[str, tuple[tuple[str, ...], str]], str, str]]:
    """Extra lenses for net_read_shadow: a champion above v0 (v0 is arm E itself) and the challenger, if any."""
    vs = versions(d)
    out = {}
    for v in (champion(vs), challenger(vs)):
        if v is not None and v["v"] > 0:
            out[f"bb_v{v['v']}"] = (prompt(v["lessons"]), FIELDS_BB, "bb_read", fname(v["v"]))
    return out


def matured(d: Path, v: int) -> pd.DataFrame:
    """Version v's on-time labels with a known 5-day outcome: accession, month, net, fwd5, created (written_at),
    and the stored bull/bear points and reason."""
    p, led = d / fname(v), d / "ledger.jsonl"
    cols = ["accession", "month", "net", "fwd5", "written_at", "bull", "bear", "reason", "label"]
    if not p.exists() or not led.exists():
        return pd.DataFrame(columns=cols)
    labs = [json.loads(x) for x in p.read_text().splitlines()]
    labs = [x for x in labs if datetime.fromisoformat(x["written_at"]) < datetime.fromisoformat(x["entry_deadline"])]
    out = {r["accession"]: r for r in Ledger(led).records() if r.get("type") == "outcome" and r.get("fwd5") is not None}
    rows = [{"accession": x["accession"], "month": out[x["accession"]]["entry"][:7], "net": SCORE[x["bb_read"]],
             "fwd5": out[x["accession"]]["fwd5"], "written_at": x["written_at"], "bull": x.get("bull", []),
             "bear": x.get("bear", []), "reason": x.get("reason", ""), "label": x["bb_read"]}
            for x in labs if x["accession"] in out]
    return pd.DataFrame(rows, columns=cols)


def summary(m: pd.DataFrame) -> str:
    """The code-built reflection input: per-label counts and average outcomes, then the worst misses."""
    m = m.sort_values("written_at").tail(SHOW)
    lines = [f"Your last {len(m)} calls with known results:"]
    for lab in ("bullish", "neutral", "bearish"):
        g = m[m["label"] == lab]
        avg = "n/a" if g.empty else f"{g['fwd5'].mean():+.2%}"
        lines.append(f"- {lab}: {len(g)} calls, average 5-day return vs sector {avg}")
    miss = pd.concat([m[m["label"] == "bullish"].nsmallest(WORST, "fwd5"),
                      m[m["label"] == "bearish"].nlargest(WORST, "fwd5")])
    miss = miss.reindex(miss["fwd5"].abs().sort_values(ascending=False).index).head(WORST)
    lines.append("\nWorst misses:")
    for r in miss.to_dict("records"):
        pts = " | ".join(f"{side}: {p['point']}" for side in ("bull", "bear") for p in r[side])
        lines.append(f"- You said {r['label']}; result {r['fwd5']:+.2%}. Your points: {pts}. Your reason: {r['reason']}")
    return "\n".join(lines)


def clean(lessons: object, names: set[str]) -> list[str]:
    """At most 5 lessons of at most 30 words; any lesson with a universe ticker (an upper-case word) or a company
    name (from `names`, lower-case) is dropped."""
    out = []
    for x in lessons if isinstance(lessons, list) else []:
        t = str(x).strip()
        words = re.findall(r"[A-Za-z][A-Za-z.&-]*", t)
        if not t or len(t.split()) > MAX_WORDS:
            continue
        if any(w in names for w in words if w.isupper() and len(w) >= 2) or any(w.lower() in names for w in words
                                                                                  if w[0].isupper() and len(w) >= 5):
            continue
        out.append(t)
    return out[:MAX_LESSONS]


def blocked_names(d: Path) -> set[str]:
    """Tickers (upper case) and the first word of company names (lower case) from the event history."""
    ev = pd.read_csv(BACKEND / "data" / "events" / "events_2024-01-01_2026-09-24.csv")
    names = {str(t) for t in ev["ticker"]}
    f = BACKEND / "data" / "events" / "sec_company_names.json"
    if f.exists():
        names |= {str(n).split()[0].lower().strip(".,") for n in json.loads(f.read_text()) if str(n).split()}
    return names


def paired(d: Path, a: int, b: int, since: str) -> dict[str, Any]:
    """Paired monthly IC of version b minus version a on releases both labelled after `since`."""
    ma, mb = matured(d, a), matured(d, b)
    ma, mb = ma[ma["written_at"] >= since], mb[mb["written_at"] >= since]
    j = ma.merge(mb[["accession", "net"]], on="accession", suffixes=("_a", "_b"))
    diffs = []
    for _, g in j.groupby("month"):
        if len(g) < 10:
            continue
        ia = g["net_a"].rank().corr(g["fwd5"].rank()) if g["net_a"].nunique() > 1 else 0.0
        ib = g["net_b"].rank().corr(g["fwd5"].rank()) if g["net_b"].nunique() > 1 else 0.0
        diffs.append(float(ib) - float(ia))
    x = np.array(diffs)
    lo80 = None
    if len(x) > 1:
        bs = x[np.random.default_rng(0).integers(0, len(x), (5000, len(x)))].mean(1)
        lo80 = float(np.percentile(bs, 20))
    return {"paired": len(j), "months": len(x), "mean_diff": float(x.mean()) if len(x) else None, "lo80": lo80}


def judge(d: Path, vs: list[dict[str, Any]]) -> str | None:
    ch = challenger(vs)
    if ch is None:
        return None
    champ = champion(vs)
    st = paired(d, champ["v"], ch["v"], ch["created_at"])
    ch["last_judged"] = st
    if st["paired"] >= PROMOTE["min_paired"] and st["months"] >= PROMOTE["min_months"] \
            and st["mean_diff"] is not None and st["mean_diff"] > 0 and (st["lo80"] or -1) > 0:
        champ["status"], ch["status"] = "retired", "champion"
        return f"v{ch['v']} promoted over v{champ['v']}: {json.dumps(st)}"
    if st["paired"] >= PROMOTE["retire_at"]:
        ch["status"] = "retired"
        return f"v{ch['v']} retired (no gain over v{champ['v']}): {json.dumps(st)}"
    return None


def reflect_due(vs: list[dict[str, Any]], now: datetime) -> bool:
    """Monthly (the long-term schedule's day), and only when no challenger is running."""
    from longterm_picks import due
    made = [{"type": "cohort", "month": v["created_at"][:7]} for v in vs if v.get("created_at")]
    tried = [{"type": "cohort", "month": v["month"]} for v in vs if v.get("month")]
    return challenger(vs) is None and due(made + tried, now)


def reflect(d: Path, vs: list[dict[str, Any]], now: datetime, use_gpu: bool) -> str | None:
    champ = champion(vs)
    m = matured(d, champ["v"])
    month = now.astimezone(NY).strftime("%Y-%m")
    if len(m) < MIN_MATURED:
        vs.append({"v": None, "status": "skipped", "month": month, "why": f"{len(m)} matured labels (< {MIN_MATURED})"})
        return None
    if not use_gpu:
        return "LEARN ALERT: self-improve: a reflection is due but the GPU is off; next run"
    from forward_events import Ollama, wait_gpu_free

    from app.sandbox.gpu_lock import gpu_priority
    from app.sandbox.walkforward import OllamaLLM
    text = summary(m)
    with gpu_priority("self_improve"):
        if not wait_gpu_free(900):
            return "LEARN ALERT: self-improve: the GPU stayed busy; reflection next run"
        srv = Ollama(PORT, str(Path.home() / ".ollama" / "models"), 1, d / "ollama_self_improve.log")
        try:
            llm = OllamaLLM("bonsai-27b:latest", base_url=f"http://127.0.0.1:{PORT}", concurrency=1, num_ctx=8192,
                            num_predict=600, cache=False, require_gpu=True)

            async def go() -> str:
                try:
                    return (await ask(llm, PROMPT_REFLECT, text))[0]
                finally:
                    await llm.unload()
            reply = asyncio.run(go())
        finally:
            srv.stop()
    lessons = clean((parse(reply) or {}).get("lessons"), blocked_names(d))
    if not lessons:
        vs.append({"v": None, "status": "skipped", "month": month, "why": "no usable lessons"})
        return None
    n = max(v["v"] for v in vs if v["v"] is not None) + 1
    new = (champ["lessons"] + lessons)[-10:]
    vs.append({"v": n, "status": "challenger", "parent": champ["v"], "lessons": new, "new_lessons": lessons,
               "created_at": now.isoformat(timespec="seconds"), "month": month, "summary": text})
    from app.sandbox.dsr import register
    register({"trial": f"bb_selfimprove_v{n}", "date": now.date().isoformat(), "kind": "selfimprove",
              "result": "running"})
    return f"self-improve: challenger v{n} from v{champ['v']} with {len(lessons)} new lessons"


def run(d: Path, now: datetime, use_gpu: bool) -> None:
    if not (d / "ledger.jsonl").exists():
        print("self-improve: no event ledger yet")
        return
    vs = versions(d)
    msg = judge(d, vs)
    if msg:
        print("self-improve:", msg)
    if reflect_due(vs, now):
        msg = reflect(d, vs, now, use_gpu)
        if msg:
            print(msg)
    save(d, vs)


def status(d: Path) -> dict[str, Any]:
    vs = versions(d)
    ch = challenger(vs)
    return {"champion": champion(vs)["v"], "challenger": None if ch is None else ch["v"],
            "versions": sum(v["v"] is not None for v in vs), "last_judged": None if ch is None else ch.get("last_judged")}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="results/forward/events")
    ap.add_argument("--no-gpu", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    d = BACKEND / a.dir
    try:
        if not a.status:
            run(d, datetime.now(UTC), not a.no_gpu)
        print("self-improve (shadow):", json.dumps(status(d)))
    except Exception as e:  # noqa: BLE001 - a shadow: never fail the events job
        print(f"LEARN ALERT: self-improve failed: {type(e).__name__}: {e}"[:300])


if __name__ == "__main__":
    main()
