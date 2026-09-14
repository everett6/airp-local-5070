"""Re-run published walk-forward experiments and check they reproduce exactly.

    python scripts/reproduce.py                      # every configs/*.toml with a published report
    python scripts/reproduce.py configs/v2.toml      # just one
    python scripts/reproduce.py --fetch              # download prices first if missing

Steps:
  1. verify data/prices.csv against configs/data.lock.json
  2. re-run each frozen config under a temporary tag (the committed LLM response
     cache means no GPU is needed when prompts are unchanged)
  3. compare every score and every individual prediction with the published files
  4. delete the temporary files

Exit code: 0 all identical, 1 some difference, 2 data missing, 3 data checksum mismatch.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.sandbox import provenance as prov
from app.sandbox import walkforward as wf

LOCK = BACKEND / "configs" / "data.lock.json"


def check_data(fetch: bool, allow_drift: bool) -> int:
    lock = json.loads(LOCK.read_text())
    if not wf.DATA.exists():
        if not fetch:
            print(f"data: MISSING {wf.DATA}. Run: {lock['fetch']}  (or pass --fetch)")
            return 2
        start, end = lock["fetch"].split("--start ")[1].split()[0], lock["fetch"].split("--end ")[1].split()[0]
        subprocess.run([sys.executable, str(BACKEND / "scripts" / "fetch_prices.py"),
                        "--start", start, "--end", end], check=True)
    digest = prov.sha256_file(wf.DATA)
    if digest == lock["sha256"]:
        print(f"data: exact match ({lock['trading_days']} trading days, {lock['first']}..{lock['last']})")
        return 0
    print(f"data: CHECKSUM MISMATCH (have {str(digest)[:16]}, lock {lock['sha256'][:16]}).\n  {lock['note']}")
    if allow_drift:
        print("  continuing because --allow-data-drift was given; expect small differences and new LLM calls")
        return 0
    return 3


def _pred_index(path: Path) -> dict[tuple[str, str, str], float]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        r = json.loads(line)
        out[(r["arm"], r["cutoff"], r["ticker"])] = r["p"]
    return out


def compare(ref: dict[str, Any], new: dict[str, Any], ref_preds: Path, new_preds: Path) -> list[str]:
    diffs = []
    for section in ("scores_full", "scores_after_warmup"):
        for arm, s in ref[section].items():
            t = new[section].get(arm)
            if t is None:
                diffs.append(f"{section}.{arm}: missing")
                continue
            for k, v in s.items():
                if t.get(k) != v:
                    diffs.append(f"{section}.{arm}.{k}: {v} -> {t.get(k)}")
    a, b = _pred_index(ref_preds), _pred_index(new_preds)
    shared = a.keys() & b.keys()
    changed = [k for k in shared if a[k] != b[k]]
    if changed:
        diffs.append(f"predictions: {len(changed)} of {len(shared)} probabilities differ, e.g. {changed[0]}")
    missing = a.keys() - b.keys()
    if missing:
        diffs.append(f"predictions: {len(missing)} published predictions not reproduced")
    return diffs


def reproduce(config: Path) -> bool:
    cfg = prov.load_config_file(config)
    tag = cfg["tag"]
    ref_path = wf.RESULTS / f"walkforward_{tag}.json"
    if not ref_path.exists():
        print(f"{tag}: no published report, skipped")
        return True
    ref = json.loads(ref_path.read_text())
    tmp_tag = f"repro_{tag}"
    new_path = wf.RESULTS / f"walkforward_{tmp_tag}.json"
    new_preds = wf.RESULTS / f"walkforward_{tmp_tag}_predictions.jsonl"
    if new_path.exists() or new_preds.exists():
        print(f"{tag}: ERROR {new_path.name} already exists; it would be deleted, so refusing (rename it first)")
        return False
    try:
        new = asyncio.run(wf.run(wf.parse_args(["--config", str(config), "--tag", tmp_tag, "--force"])))
        diffs = compare(ref, new, wf.RESULTS / f"walkforward_{tag}_predictions.jsonl", new_preds)
    except (Exception, SystemExit) as e:  # noqa: BLE001 - any failure (incl. jail probe exit) is reported per config
        print(f"{tag}: ERROR re-running: {type(e).__name__}: {e}")
        return False
    finally:
        new_path.unlink(missing_ok=True)
        new_preds.unlink(missing_ok=True)
    if ref.get("config_hash") and ref["config_hash"] != new["config_hash"]:
        diffs.insert(0, f"config: {config.name} (hash {new['config_hash']}) no longer matches the published "
                        f"run's config (hash {ref['config_hash']})")
    ref_data = ((ref.get("provenance") or {}).get("data") or {}).get("sha256")
    if ref_data and ref_data != new["provenance"]["data"]["sha256"]:
        diffs.insert(0, "data: the price file differs from the one the published run used")
    old_digest = (ref.get("provenance") or {}).get("model_digest")
    if old_digest and old_digest != new["provenance"]["model_digest"]:
        print(f"{tag}: note: Ollama model digest changed since publication")
    status = "IDENTICAL" if not diffs else f"DIFFERENT ({len(diffs)} differences)"
    print(f"{tag}: {status} — {new['llm_calls']} new LLM calls, {new['cache_hits']} cached")
    for d in diffs[:10]:
        print(f"    {d}")
    return not diffs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("configs", nargs="*", type=Path)
    ap.add_argument("--fetch", action="store_true", help="download prices if data/prices.csv is missing")
    ap.add_argument("--allow-data-drift", action="store_true", help="continue if the data checksum differs")
    args = ap.parse_args()
    code = check_data(args.fetch, args.allow_data_drift)
    if code:
        return code
    configs = args.configs or sorted((BACKEND / "configs").glob("*.toml"))
    results = [reproduce(c) for c in configs]
    ok = all(results)
    print("ALL IDENTICAL" if ok else "SOME RUNS DIFFER")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
