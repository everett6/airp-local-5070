"""Arm F, a self-improving Bonsai (docs/PLAN_60_V2.md "Arm F", fixed 2026-09-28): champion/challenger on the live
bull/bear lens. No money.

    python scripts/self_improve.py --dir results/forward/events          # reflect if due, then judge
    python scripts/self_improve.py --dir results/forward/events --status

Versions live in <dir>/bb_versions.jsonl (v0 = arm E's frozen PROMPT_BB, labelled into bull_bear.jsonl; version n
labels into bb_v<n>.jsonl, by scripts/net_read_shadow.py through active_lenses()). Monthly, Bonsai reads a code-built
summary of the champion's matured calls and writes at most 5 general lessons; the challenger = the champion's prompt
plus those lessons. Problems print "LEARN ALERT: ..." and never fail the job.

v2 (docs/PLAN_60_V2.md "Arm F v2"): the reflection sees the older 2/3 of the matured calls (contrastive: worst
misses and best hits); 3 framings give 3 lesson sets; each re-labels the newer 1/3 (held back) and the best one
becomes the challenger only if it beats the champion there. Promotion, early retirement and rollback use an
anytime-valid betting e-process on weekly paired scores (W >= 20: a 5% error bound at every look).
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
MIN_MATURED, SHOW, WORST, MAX_LESSONS, MAX_WORDS = 60, 80, 10, 5, 30
HOLDOUT, KEEP_LESSONS, CLIP, SCALE = 1 / 3, 8, 0.05, 0.2
PROMOTE = {"min_paired": 60, "min_months": 2, "retire_weeks": 26, "e_bound": 20.0}
SCORE = {"bullish": 1, "neutral": 0, "bearish": -1}
PROMPT_REFLECT = """You make bull/bear calls on company earnings releases: bullish if the release makes the next week
better for the stock than a typical release, bearish if worse. Below is a summary of your past calls whose results
are now known (the stock's 5-day return against its sector), then your worst misses with the bull and bear points
you wrote at the time.
Write at most 5 lessons that would make your future calls more accurate. Each lesson: one general rule, at most 30
words, about how to weigh evidence in a release. Never name a company, ticker, date or sector. If there is no clear
lesson, write fewer.
Reply with ONLY one JSON object on one line: {"lessons": ["...", "..."]}"""
FRAMINGS = ("Focus on the errors behind your worst misses and how to avoid them.",
            "Focus on what separated your best hits from your worst misses.",
            "Focus on when you were over-confident: which kinds of evidence you trusted too much.")


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
    cols = ["accession", "month", "entry", "net", "fwd5", "written_at", "bull", "bear", "reason", "label"]
    if not p.exists() or not led.exists():
        return pd.DataFrame(columns=cols)
    labs = [json.loads(x) for x in p.read_text().splitlines()]
    labs = [x for x in labs if datetime.fromisoformat(x["written_at"]) < datetime.fromisoformat(x["entry_deadline"])]
    out = {r["accession"]: r for r in Ledger(led).records() if r.get("type") == "outcome" and r.get("fwd5") is not None}
    rows = [{"accession": x["accession"], "month": out[x["accession"]]["entry"][:7],
             "entry": out[x["accession"]]["entry"][:10], "net": SCORE[x["bb_read"]],
             "fwd5": out[x["accession"]]["fwd5"], "written_at": x["written_at"], "bull": x.get("bull", []),
             "bear": x.get("bear", []), "reason": x.get("reason", ""), "label": x["bb_read"]}
            for x in labs if x["accession"] in out]
    return pd.DataFrame(rows, columns=cols)


def summary(m: pd.DataFrame) -> str:
    """The code-built reflection input: per-label counts and average outcomes, then the worst misses and the best
    hits (contrastive, so lessons do not only over-correct)."""
    m = m.sort_values("written_at").tail(SHOW)
    lines = [f"Your last {len(m)} calls with known results:"]
    for lab in ("bullish", "neutral", "bearish"):
        g = m[m["label"] == lab]
        avg = "n/a" if g.empty else f"{g['fwd5'].mean():+.2%}"
        lines.append(f"- {lab}: {len(g)} calls, average 5-day return vs sector {avg}")
    edge = m["fwd5"] * m["net"]  # > 0: the call was right
    called = m[m["net"] != 0]
    for title, rows in (("Worst misses", called.loc[edge[called.index].nsmallest(WORST).index]),
                        ("Best hits", called.loc[edge[called.index].nlargest(WORST).index])):
        lines.append(f"\n{title}:")
        for r in rows.to_dict("records"):
            pts = " | ".join(f"{side}: {p['point']}" for side in ("bull", "bear") for p in r[side])
            lines.append(f"- You said {r['label']}; result {r['fwd5']:+.2%}. Your points: {pts}. "
                         f"Your reason: {r['reason']}")
    return "\n".join(lines)


def split(m: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Time-ordered: the older 2/3 are shown to Bonsai, the newer 1/3 held back for choosing a candidate."""
    m = m.sort_values("written_at")
    k = len(m) - round(len(m) * HOLDOUT)
    return m.iloc[:k], m.iloc[k:]


def rank_ic(net: pd.Series, fwd: pd.Series) -> float:
    return float(net.rank().corr(fwd.rank())) if net.nunique() > 1 and fwd.nunique() > 1 else 0.0


def merge_lessons(old: list[str], new: list[str]) -> list[str]:
    """Old then new, de-duplicated ignoring case and punctuation, the newest KEEP_LESSONS kept."""
    seen: dict[str, str] = {}
    for x in old + new:
        seen.pop(re.sub(r"[^a-z0-9 ]", "", x.lower()).strip(), None)
        seen[re.sub(r"[^a-z0-9 ]", "", x.lower()).strip()] = x
    return list(seen.values())[-KEEP_LESSONS:]


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


def weekly_scores(d: Path, a: int, b: int, since: str, now: datetime) -> tuple[list[float], dict[str, Any]]:
    """Paired weekly scores of version b over version a on releases both labelled after `since`: per release
    x = (net_b - net_a) * clip(fwd5 / 5%, -1, 1) / 2; per entry week y = clip(mean x / 0.2, -1, 1), oldest first;
    only weeks that ended 8+ days before `now` (all their outcomes known)."""
    ma, mb = matured(d, a), matured(d, b)
    ma, mb = ma[ma["written_at"] >= since], mb[mb["written_at"] >= since]
    j = ma.merge(mb[["accession", "net"]], on="accession", suffixes=("_a", "_b"))
    if j.empty:
        return [], {"paired": 0, "months": 0}
    j["x"] = (j["net_b"] - j["net_a"]) * (j["fwd5"] / CLIP).clip(-1, 1) / 2
    wk = pd.to_datetime(j["entry"]).dt.to_period("W-SUN")
    cutoff = pd.Timestamp(now.astimezone(NY).date()) - pd.Timedelta(days=8)
    j = j[wk.dt.end_time.dt.normalize() <= cutoff]
    wk = wk[j.index]
    xs = [float(np.clip(v / SCALE, -1, 1)) for v in j.groupby(wk)["x"].mean().sort_index()]
    return xs, {"paired": len(j), "months": int(j["month"].nunique())}


def e_process(xs: list[float], cap: float = 0.5) -> float:
    """Max wealth of a betting e-process against H0: E[x | past] <= 0, x in [-1, 1] (aGRAPA bets from past weeks
    only). By Ville's inequality P(max >= 1/alpha) <= alpha under H0, at any number of looks."""
    w, best = 1.0, 1.0
    for t, x in enumerate(xs):
        past = np.array(xs[:t])
        lam = 0.0
        if len(past) >= 2:
            mu, var = float(past.mean()), float(past.var())
            lam = float(np.clip(mu / (var + mu * mu), 0.0, cap)) if var + mu * mu > 0 else 0.0
        w *= 1 + lam * x
        best = max(best, w)
    return best


def judge(d: Path, vs: list[dict[str, Any]], now: datetime | None = None) -> str | None:
    now = now or datetime.now(UTC)
    msgs = []
    champ = champion(vs)
    if champ["v"] > 0:  # rollback guard: is v0 (arm E, always labelled) reliably better than the champion?
        xs, n = weekly_scores(d, champ["v"], 0, champ.get("promoted_at") or champ["created_at"], now)
        e_back = e_process(xs)
        champ["rollback_e"] = round(e_back, 3)
        if e_back >= PROMOTE["e_bound"]:
            champ["status"] = "retired"
            v0 = next(v for v in vs if v["v"] == 0)
            v0["status"], v0["promoted_at"] = "champion", now.isoformat(timespec="seconds")
            msgs.append(f"v{champ['v']} rolled back to v0 (v0 reliably better, e={e_back:.1f}, {json.dumps(n)})")
            champ = v0
    ch = challenger(vs)
    if ch is not None:
        xs, n = weekly_scores(d, champ["v"], ch["v"], ch["created_at"], now)
        e_up, e_down = e_process(xs), e_process([-x for x in xs])
        st = n | {"weeks": len(xs), "e_better": round(e_up, 3), "e_worse": round(e_down, 3)}
        ch["last_judged"] = st
        if e_up >= PROMOTE["e_bound"] and n["paired"] >= PROMOTE["min_paired"] and n["months"] >= PROMOTE["min_months"]:
            champ["status"], ch["status"] = "retired", "champion"
            ch["promoted_at"] = now.isoformat(timespec="seconds")
            msgs.append(f"v{ch['v']} promoted over v{champ['v']}: {json.dumps(st)}")
        elif e_down >= PROMOTE["e_bound"] or len(xs) >= PROMOTE["retire_weeks"]:
            ch["status"] = "retired"
            why = "reliably worse" if e_down >= PROMOTE["e_bound"] else "no gain"
            msgs.append(f"v{ch['v']} retired ({why} vs v{champ['v']}): {json.dumps(st)}")
    return "; ".join(msgs) or None


def reflect_due(vs: list[dict[str, Any]], now: datetime) -> bool:
    """Monthly (the long-term schedule's day), and only when no challenger is running."""
    from longterm_picks import due
    made = [{"type": "cohort", "month": v["created_at"][:7]} for v in vs if v.get("created_at")]
    tried = [{"type": "cohort", "month": v["month"]} for v in vs if v.get("month")]
    return challenger(vs) is None and due(made + tried, now)


async def candidates(llm: Any, d: Path, champ: dict[str, Any], train: pd.DataFrame, held: pd.DataFrame
                     ) -> tuple[str, list[dict[str, Any]], float]:
    """The reflection summary, 3 candidate lesson sets (one per framing) with their held-back rank IC, and the
    champion's held-back IC (from its own live labels)."""
    from net_read_shadow import label
    text = summary(train)
    names = blocked_names(d)
    ledger = {r["accession"]: r for r in Ledger(d / "ledger.jsonl").records() if r.get("type") == "decision"}
    todo = [ledger[a] for a in held["accession"] if a in ledger]
    out = []
    for framing in FRAMINGS:
        reply = (await ask(llm, PROMPT_REFLECT + "\n" + framing, text))[0]
        lessons = clean((parse(reply) or {}).get("lessons"), names)
        cand: dict[str, Any] = {"framing": framing, "lessons": lessons, "held_ic": None}
        if lessons and todo:
            spec = (prompt(merge_lessons(champ["lessons"], lessons)), FIELDS_BB, "bb_read", "")
            labs = await asyncio.gather(*(label(llm, r, spec=spec) for r in todo))
            got = pd.Series({x["accession"]: SCORE[x["bb_read"]] for x in labs})
            h = held.set_index("accession")
            cand["held_ic"] = round(rank_ic(got.reindex(h.index).fillna(0), h["fwd5"]), 4)
        out.append(cand)
    return text, out, round(rank_ic(held["net"], held["fwd5"]), 4)


def choose(cands: list[dict[str, Any]], champ_ic: float) -> dict[str, Any] | None:
    """The candidate with the best held-back IC, if it beats the champion's on the same releases."""
    ok = [c for c in cands if c["lessons"] and c["held_ic"] is not None and c["held_ic"] > champ_ic]
    return max(ok, key=lambda c: c["held_ic"]) if ok else None


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
    train, held = split(m)
    with gpu_priority("self_improve"):
        if not wait_gpu_free(900):
            return "LEARN ALERT: self-improve: the GPU stayed busy; reflection next run"
        srv = Ollama(PORT, str(Path.home() / ".ollama" / "models"), 3, d / "ollama_self_improve.log")
        try:
            llm = OllamaLLM("bonsai-27b:latest", base_url=f"http://127.0.0.1:{PORT}", concurrency=3, num_ctx=8192,
                            num_predict=1000, cache=False, require_gpu=True)

            async def go() -> tuple[str, list[dict[str, Any]], float]:
                try:
                    return await candidates(llm, d, champ, train, held)
                finally:
                    await llm.unload()
            text, cands, champ_ic = asyncio.run(go())
        finally:
            srv.stop()
    best = choose(cands, champ_ic)
    audit = {"month": month, "summary": text, "candidates": cands, "champion_held_ic": champ_ic,
             "shown": len(train), "held_back": len(held)}
    if best is None:
        vs.append({"v": None, "status": "skipped", "why": "no candidate beat the champion on held-back calls"} | audit)
        return f"self-improve: no challenger this month (champion held-back IC {champ_ic})"
    n = max(v["v"] for v in vs if v["v"] is not None) + 1
    vs.append({"v": n, "status": "challenger", "parent": champ["v"],
               "lessons": merge_lessons(champ["lessons"], best["lessons"]), "new_lessons": best["lessons"],
               "lesson_source": {x: f"v{n} {month}" for x in best["lessons"]}, "framing": best["framing"],
               "created_at": now.isoformat(timespec="seconds")} | audit)
    from app.sandbox.dsr import register
    register({"trial": f"bb_selfimprove_v{n}", "date": now.date().isoformat(), "kind": "selfimprove",
              "result": "running"})
    return (f"self-improve: challenger v{n} from v{champ['v']} (held-back IC {best['held_ic']} vs champion "
            f"{champ_ic}), {len(best['lessons'])} new lessons")


def run(d: Path, now: datetime, use_gpu: bool) -> None:
    if not (d / "ledger.jsonl").exists():
        print("self-improve: no event ledger yet")
        return
    vs = versions(d)
    msg = judge(d, vs, now)
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
