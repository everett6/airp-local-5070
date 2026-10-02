"""The bounded learning loop (app/signals/registry.py has the rules; docs/DEV_PLAN_AUTONOMOUS.md Phase B).

    python scripts/learn_loop.py collect   # after each event run: keep the live releases' fields (CPU, seconds)
    python scripts/learn_loop.py monthly   # once a month: propose 5 -> train test (2024) -> holdout (2025-26) -> shadow
    python scripts/learn_loop.py review    # weekly: live check of shadow / promoted signals -> promote / retire
    python scripts/learn_loop.py status

Proposals come from Bonsai-27B (GPU, when it is free; it yields to the forward runner) or, when the GPU is busy or
Bonsai's reply is unusable, from a seeded draw of untried recipes ("grid"). Either way code checks every proposal
and the tests decide. Lines starting "LEARN ALERT:" become phone alerts through autorun.py.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import random
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd

from app.forward.ledger import jsonl_records, open_append
from app.signals import registry as R

EV = BACKEND / "results" / "events"
FEATS = ["accession", "eps_q", "eps_prior", "rev_q", "rev_prior", "guidance", "tone"]
PORT = 11439


def derive(df: pd.DataFrame) -> pd.DataFrame:
    from bonsai_lite import change
    df = df.copy()
    df["eps_growth"] = change(df["eps_q"], df["eps_prior"], 2.0)
    df["rev_growth"] = change(df["rev_q"], df["rev_prior"], 1.0)
    df["guidance"] = df["guidance"].fillna("none").astype(str)
    df["tone"] = df["tone"].fillna("neutral").astype(str)
    return df


def history(tag: str) -> pd.DataFrame:
    """2024 ("2024") or 2025-26 ("2025-26"): scorable releases with the menu's fields and the 5-day outcome."""
    from bonsai_lite import SAMPLES
    from event_eval import build
    from secchk_eval import load

    from app.sandbox.events import Prices
    dec, events, feats = SAMPLES[tag]
    p = Prices.from_long(pd.read_parquet(BACKEND / "data/events/ohlcv_2023-01-01_2026-09-25.parquet"))
    ev = pd.read_csv(BACKEND / events)
    df = build(ev, p)
    df = df[df["scorable"]].merge(pd.read_csv(EV / feats)[FEATS], on="accession").merge(load(dec, 5), on="accession")
    return derive(df.merge(ev[["accession", "accepted_utc"]].drop_duplicates("accession"), on="accession"))


def reference() -> pd.DataFrame:
    """Every history release (2024, then 2025-26) with each menu field's point-in-time percentile: what a later
    release is ranked against. `sample` says which test a row belongs to."""
    pool = pd.concat([history("2024").assign(sample="2024"), history("2025-26").assign(sample="2025-26")],
                     ignore_index=True)
    return R.add_pit(pool)


# ---------- collect (after every event run) ----------

def collect(events_dir: Path, out: Path, tag: str = "forward", ref: pd.DataFrame | None = None) -> int:
    """Keep each new live release's fields as they were at decision time (features_forward.csv is rewritten by every
    run, so without this the history would be lost)."""
    from forward_events import pre_entry

    from app.sandbox.events import Prices
    ev_csv, px, feats = events_dir / "events.csv", events_dir / "prices.parquet", EV / f"features_{tag}.csv"
    if not (ev_csv.exists() and px.exists() and feats.exists()):
        print("learn collect: nothing to collect yet")
        return 0
    store = out / "live_features.csv"
    # a row cut off by a power loss is skipped: it must not stop every later run from keeping its releases
    have = set(pd.read_csv(store, on_bad_lines="skip")["accession"]) if store.exists() else set()
    f = pd.read_csv(feats)
    f = f[[c for c in FEATS if c in f.columns]]
    f = f[~f["accession"].isin(have)]
    ev = pd.read_csv(ev_csv)
    ev = ev[ev["accession"].isin(f["accession"])]
    if ev.empty:
        print("learn collect: 0 new releases")
        store_pit(events_dir, out, ref)
        return 0
    rows = pre_entry(ev, Prices.from_long(pd.read_parquet(px))).merge(f, on="accession")
    rows["collected_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    out.mkdir(parents=True, exist_ok=True)
    open_append(store).close()  # ends a row that a power loss cut off, so the new rows are not glued to it
    rows.to_csv(store, mode="a", header=not store.stat().st_size, index=False)
    print(f"learn collect: {len(rows)} new releases")
    store_pit(events_dir, out, ref)
    return len(rows)


PIT_COLS = [R.PIT + f for f in R.menu_fields()]


def store_pit(events_dir: Path, out: Path, ref: pd.DataFrame | None = None) -> int:
    """For each collected release that has none yet: every menu field's percentile among the releases accepted
    before it (the history and earlier live releases), appended once to live_pit.csv and never rewritten. A signal's
    live score is built from these stored numbers, so no later release can change it."""
    store, pits, ledger = out / "live_features.csv", out / "live_pit.csv", events_dir / "ledger.jsonl"
    ev_csv = events_dir / "events.csv"
    if not (store.exists() and ev_csv.exists()):
        return 0
    feats = pd.read_csv(store, on_bad_lines="skip").drop_duplicates("accession")
    old = pd.read_csv(pits, on_bad_lines="skip").drop_duplicates("accession") if pits.exists() else pd.DataFrame(
        columns=["accession", "accepted_utc", "logodds", *PIT_COLS])
    todo = feats[~feats["accession"].isin(set(old["accession"]))]
    if todo.empty:
        return 0
    from app.forward.ledger import Ledger
    recs = Ledger(ledger).records() if ledger.exists() else []
    lo = {r["accession"]: r.get("logodds") for r in recs if r.get("type") == "decision"}
    acc = pd.read_csv(ev_csv).drop_duplicates("accession").set_index("accession")["accepted_utc"]
    live_rows = feats.assign(accepted_utc=feats["accession"].map(acc), logodds=feats["accession"].map(lo))
    live_rows = derive(live_rows[live_rows["accepted_utc"].notna()])
    for c in PIT_COLS:  # a release stored earlier keeps its numbers; it still counts as "earlier" for the new ones
        live_rows[c] = live_rows["accession"].map(old.set_index("accession")[c]) if c in old.columns else float("nan")
    ref = reference() if ref is None else ref
    pool = R.add_pit(pd.concat([ref, live_rows], ignore_index=True))
    new = pool[pool["accession"].isin(set(todo["accession"]))][["accession", "accepted_utc", "logodds", *PIT_COLS]]
    new = new.assign(stored_at=datetime.now(UTC).isoformat(timespec="seconds"))
    if new.empty:
        return 0
    open_append(pits).close()
    new.to_csv(pits, mode="a", header=not pits.stat().st_size, index=False)
    print(f"learn collect: point-in-time percentiles stored for {len(new)} release(s)")
    return len(new)


def live(events_dir: Path, out: Path) -> pd.DataFrame:
    """Live releases with an on-time decision (its log-odds) and a 5-day outcome, by entry month."""
    store, ledger = out / "live_features.csv", events_dir / "ledger.jsonl"
    cols = ["accession", "sector", "momentum", "logodds", "fwd5", "entry", "month", "accepted_utc", *FEATS[1:], *PIT_COLS]
    if not (store.exists() and ledger.exists()):
        return pd.DataFrame(columns=cols)
    from app.forward.ledger import Ledger
    recs = Ledger(ledger).records()
    dec = {r["accession"]: r["logodds"] for r in recs if r.get("type") == "decision" and r.get("on_time")}
    outc = {r["accession"]: (r["fwd5"], r["entry"]) for r in recs if r.get("type") == "outcome"
            and r.get("fwd5") is not None}
    df = pd.read_csv(store, on_bad_lines="skip").drop_duplicates("accession")
    df = df[df["accession"].isin(dec) & df["accession"].isin(outc)]
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["logodds"] = df["accession"].map(dec)
    df["fwd5"] = df["accession"].map(lambda a: outc[a][0])
    df["entry"] = df["accession"].map(lambda a: outc[a][1])
    df["month"] = df["entry"].str[:7]
    pits = out / "live_pit.csv"
    if pits.exists():  # the percentiles stored when each release was collected
        df = df.merge(pd.read_csv(pits, on_bad_lines="skip").drop_duplicates("accession")
                      [["accession", "accepted_utc", *PIT_COLS]], on="accession", how="left")
    else:
        df = df.assign(accepted_utc=df["entry"], **dict.fromkeys(PIT_COLS, float("nan")))
    return derive(df)


# ---------- proposals ----------

def atoms() -> list[str]:
    return [*R.NUMERIC, *(f"{k}={v}" for k, vs in R.CATEGORICAL.items() for v in vs if v != "unverified")]


def grid(reg: R.Registry, n: int, seed: int) -> list[R.Signal]:
    """Untried one- and two-term recipes, drawn with a fixed seed (the month)."""
    cands = []
    for k in (1, 2):
        for fields in itertools.combinations(atoms(), k):
            if len({f.split("=")[0] for f in fields}) < k:
                continue  # two values of the same categorical field would be one field twice
            for signs in itertools.product((1, -1), repeat=k):
                s = R.Signal(name="grid_" + "_".join(f"{'p' if g > 0 else 'n'}{f.replace('=', '_')}"
                                                   for f, g in zip(fields, signs, strict=True)),
                             terms=[{"field": f, "sign": g} for f, g in zip(fields, signs, strict=True)],
                             proposer="grid")
                if s.key() not in reg.tried_keys():
                    cands.append(s)
    random.Random(seed).shuffle(cands)
    return cands[:n]


PROMPT = """You propose trading signals for S&P 500 earnings releases, to be tested by code. A signal is a sum of at
most 3 terms; each term is +1 or -1 times a field's percentile among all earlier releases; optionally the
signal only applies to some sectors. The outcome is the stock's return over the next 5 trading days minus its sector
ETF. Most ideas fail; propose ones with an economic reason, different from those already tried.
Numeric fields:
{numeric}
Categorical fields (use as "field=value", 1 if true else 0):
{categorical}
Sectors: {sectors}
Reply with ONLY one JSON object: {{"signals": [{{"name": "short_snake_case", "rationale": "at most 25 words",
"terms": [{{"field": "...", "sign": 1}}], "sectors": null}}, ...]}} with exactly {n} signals."""


async def ask_bonsai(tried: list[str], n: int) -> list[dict[str, Any]]:
    from llm_fields import parse

    from app.sandbox.walkforward import OllamaLLM
    system = PROMPT.format(numeric="\n".join(f"- {k}: {v}" for k, v in R.NUMERIC.items()),
                           categorical="\n".join(f"- {k}: {', '.join(v)}" for k, v in R.CATEGORICAL.items()),
                           sectors=", ".join(R.SECTORS), n=n)
    user = "Already tried (do not repeat):\n" + ("\n".join(tried[-80:]) or "(none yet)")
    llm = OllamaLLM("bonsai-27b:latest", base_url=f"http://127.0.0.1:{PORT}", concurrency=1, num_ctx=8192,
                    num_predict=1500, cache=False, require_gpu=True)
    try:
        raw = parse(await llm(system, user)) or {}
    finally:
        await llm.unload()
    sigs = raw.get("signals")
    return sigs if isinstance(sigs, list) else []


def propose(reg: R.Registry, n: int, seed: int, use_gpu: bool) -> list[R.Signal]:
    out: list[R.Signal] = []
    if use_gpu:
        from forward_events import Ollama, gpu_free
        if gpu_free():
            srv = Ollama(PORT, str(Path.home() / ".ollama" / "models"), 1, EV / "ollama_learn.log")
            try:
                tried = [f"{s.key()}  ({s.status})" for s in reg.signals.values()]
                for d in asyncio.run(ask_bonsai(tried, n)):
                    try:
                        s = R.validate(d)
                        s.proposer = "bonsai"
                        if s.key() not in reg.tried_keys() and s.key() not in {x.key() for x in out}:
                            out.append(s)
                    except R.SpecError as e:
                        print(f"  refused a proposal: {e}")
            except Exception as e:  # noqa: BLE001 - any model failure falls back to the grid
                print(f"  Bonsai proposals failed ({type(e).__name__}: {e}); using the grid")
            finally:
                srv.stop()
        else:
            print("  GPU busy: using the grid")
    return (out + [g for g in grid(reg, n, seed) if g.key() not in {x.key() for x in out}])[:n]


# ---------- monthly ----------

def monthly(out: Path, use_gpu: bool, today: date | None = None, train: pd.DataFrame | None = None,
            hold: pd.DataFrame | None = None) -> dict[str, Any]:
    from app.sandbox.dsr import register
    today = today or datetime.now(UTC).date()
    reg = R.Registry(out / "registry.json")
    month = today.strftime("%Y-%m")
    runs = out / "monthly.jsonl"
    done = jsonl_records(runs)
    if any(r.get("month") == month for r in done):
        print(f"learn monthly: already ran for {month}")
        return {}
    if train is None or hold is None:
        pool = reference()
    else:  # tests hand in their own two samples
        pool = R.add_pit(pd.concat([train.assign(sample="2024"), hold.assign(sample="2025-26")], ignore_index=True))
    is_train, is_hold = pool["sample"] == "2024", pool["sample"] == "2025-26"
    props = propose(reg, R.PROPOSALS_PER_MONTH, int(today.strftime("%Y%m")), use_gpu)
    summary: dict[str, Any] = {"month": month, "at": datetime.now(UTC).isoformat(timespec="seconds"), "signals": {}}
    for s in props:
        reg.add(s)
        s.note(today.isoformat(), "proposed", proposer=s.proposer)
        tr = R.train_test(pool, s, is_train)
        s.note(today.isoformat(), "train", **tr)
        register({"trial": f"learn_{s.name}", "date": today.isoformat(), "kind": "learn_train", "ic": tr["mean_ic"],
                  "result": "pass" if tr["pass"] else "fail"})
        res: dict[str, Any] = {"key": s.key(), "proposer": s.proposer, "train": tr}
        if not tr["pass"]:
            s.status = "rejected_train"
        else:
            reg.holdout_tests += 1
            ho = R.holdout_test(pool, s, reg.holdout_tests, is_hold)
            s.note(today.isoformat(), "holdout", **ho)
            register({"trial": f"learn_{s.name}_holdout", "date": today.isoformat(), "kind": "learn_holdout",
                      "ic": ho["mean_ic"], "result": "pass" if ho["pass"] else "fail"})
            res["holdout"] = ho
            s.status = "shadow" if ho["pass"] else "rejected_holdout"
            if ho["pass"]:
                s.note(today.isoformat(), "shadow")
                print(f"LEARN ALERT: new signal {s.name} passed its tests; tracking it live (no money) for 3+ months")
        res["status"] = s.status
        summary["signals"][s.name] = res
        print(f"  {s.name:40} {s.proposer:6} train IC {tr['mean_ic']}  -> {s.status}")
    reg.save()
    with open_append(runs) as f:
        f.write(json.dumps(summary) + "\n")
    return summary


# ---------- weekly review ----------

def review(events_dir: Path, out: Path, today: date | None = None, df: pd.DataFrame | None = None,
           ref: pd.DataFrame | None = None) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    reg = R.Registry(out / "registry.json")
    df = live(events_dir, out) if df is None else df
    rep: dict[str, Any] = {"at": today.isoformat(), "live_events": len(df), "signals": {}}
    watched = reg.with_status("promoted") + reg.with_status("shadow")
    if watched and ref is None and "sample" not in df.columns and (out / "live_pit.csv").exists():
        ref = reference()  # live scores are ranked against the history as well as earlier live releases
    pool = df.assign(_live=True) if ref is None else pd.concat([ref.assign(_live=False), df.assign(_live=True)],
                                                                ignore_index=True)

    def since(s: R.Signal, event: str) -> date:
        return date.fromisoformat(next(h["date"] for h in reversed(s.history) if h["event"] == event))

    def live_since(d0: date) -> pd.Series:
        return pool["_live"] & (pool["entry"] >= d0.isoformat())
    for s in sorted(reg.with_status("promoted"), key=lambda x: x.name):
        d0 = since(s, "promoted")
        lt = R.live_test(pool, s, live_since(d0))
        rep["signals"][s.name] = {"status": s.status, **lt}
        if lt["events"] >= R.LIVE_MIN_EVENTS and lt["blend_gain"] is not None and lt["blend_gain"] < 0:
            s.status = "retired"
            s.note(today.isoformat(), "retired", reason="live gain below 0 since promotion", **lt)
            print(f"LEARN ALERT: signal {s.name} retired (its live edge since promotion is negative)")
    slots = 2 - len(reg.with_status("promoted"))
    for s in sorted(reg.with_status("shadow"), key=lambda x: x.name):
        d0 = since(s, "shadow")
        age = (today - d0).days
        # Promotion is looked at four times in a signal's shadow period, not every week: the first review on or
        # after day 90, 120, 150 and 180 that has enough releases. Look j is held to level 0.20 / (j (j + 1)), so
        # the four together stay under 0.20 (a bound checked every week is not an 80% bound any more).
        looks = [h for h in s.history if h["event"] == "look"]
        used = {d for h in looks for d in h.get("days", [])}
        pending = [d for d in R.LOOK_DAYS if d <= age and d not in used]
        j = len(looks) + 1
        lt = R.live_test(pool, s, live_since(d0), R.look_alpha(j))
        rep["signals"][s.name] = {"status": s.status, "days": age, "looks": len(looks), **lt}
        looked = bool(pending) and lt["events"] >= R.LIVE_MIN_EVENTS
        ok = looked and lt["blend_gain_lo"] is not None and lt["blend_gain_lo"] > 0 and (lt["mean_ic"] or 0) > 0
        if looked:
            s.note(today.isoformat(), "look", n=j, days=pending, passed=bool(ok), **lt)
            rep["signals"][s.name]["looks"] = j
        if ok and slots > 0:
            s.status, slots = "promoted", slots - 1
            s.note(today.isoformat(), "promoted", **lt)
            print(f"LEARN ALERT: signal {s.name} promoted to a {int(100 * R.SLEEVE_PER_SIGNAL)}% paper sleeve "
                  f"(live gain {lt['blend_gain']}, {lt['events']} releases)")
        elif age >= R.SHADOW_MAX_DAYS:
            s.status = "retired"
            s.note(today.isoformat(), "retired", reason="not promoted within 6 months", **lt)
            print(f"LEARN ALERT: signal {s.name} retired (no live edge after 6 months)")
    prom = reg.with_status("promoted")
    rep["sleeve"] = R.sleeve_week(pool, prom, live_since(min((since(s, "promoted") for s in prom), default=today)))
    reg.save()
    (out / "review.json").write_text(json.dumps(rep, indent=1) + "\n")
    print(json.dumps({"live_events": rep["live_events"], "sleeve": rep["sleeve"],
                      "counts": {st: len(reg.with_status(st)) for st in R.STATUSES}}, indent=1))
    return rep


def status(out: Path) -> None:
    reg = R.Registry(out / "registry.json")
    print(f"holdout tests so far: {reg.holdout_tests} (next one needs p < {R.holdout_alpha(reg.holdout_tests + 1):.5f})")
    for s in sorted(reg.signals.values(), key=lambda x: (x.status, x.name)):
        print(f"  {s.status:17} {s.name:40} {s.key()}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("collect", "monthly", "review", "status"))
    ap.add_argument("--dir", default="results/forward/events", help="the event runner's folder")
    ap.add_argument("--out", default="results/forward/signals")
    ap.add_argument("--no-gpu", action="store_true")
    ap.add_argument("--tag", default="forward", help="the event runner's features tag (replayed dry runs differ)")
    a = ap.parse_args()
    out, ev = BACKEND / a.out, BACKEND / a.dir
    t0 = time.monotonic()
    try:
        if a.cmd == "collect":
            collect(ev, out, a.tag)
        elif a.cmd == "monthly":
            monthly(out, not a.no_gpu)
        elif a.cmd == "review":
            review(ev, out)
        else:
            status(out)
    except Exception as e:  # noqa: BLE001 - the loop must never fail the book's own jobs: alert instead
        print(f"LEARN ALERT: learn_loop {a.cmd} failed: {type(e).__name__}: {e}"[:300])
    print(f"({time.monotonic() - t0:.0f}s)")


if __name__ == "__main__":
    main()
