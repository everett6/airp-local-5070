"""What the app's Research log page shows (JSON): the 150-company run's progress and, per company, both models'
reasoning and every website visited. Read-only.

    python scripts/research_view.py [--ticker NVDA]
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
FWD = BACKEND / "results" / "forward"


def load(p: Path, default: Any) -> Any:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def jan_steps(r: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for call in r.get("llm_calls", []):
        try:
            obj = json.loads(call.get("reply", ""))
        except ValueError:
            continue
        if not isinstance(obj, dict) or not {"thought", "actions", "final"} & set(obj):  # facts, price snapshot
            continue
        step: dict[str, Any] = {"thought": str(obj.get("thought", ""))[:1200]}
        if isinstance(obj.get("actions"), list):
            step["tools"] = [f"{a.get('tool')} {json.dumps(a.get('args', {}))[:200]}" for a in obj["actions"] if isinstance(a, dict)]
        if isinstance(obj.get("final"), dict):
            step["conclusion"] = str(obj["final"].get("reason", ""))[:600]
        out.append(step)
    return out


def sites(r: dict[str, Any]) -> list[dict[str, Any]]:
    """Every page either model tried to open, whether it opened, and whether its text was used."""
    used = {p["url"] for p in r.get("article_retrieval", {}).get("pages", [])} | {p["url"] for p in r.get("filings_read", [])}
    seen: dict[str, dict[str, Any]] = {}
    for a in r.get("article_retrieval", {}).get("attempts", []):
        seen[a["url"]] = {"url": a["url"], "by": "Jan", "ok": bool(a.get("ok")), "used": a["url"] in used,
                          "skipped": bool(a.get("skipped")), "note": None if a.get("usable") else a.get("error")}
    for p in r.get("filings_read", []):
        seen[p["url"]] = {"url": p["url"], "by": "Jan (SEC filing)", "ok": True, "used": True, "note": p.get("title")}
    for who, log in (("Jan", r.get("tool_log", [])), ("Bonsai", r.get("bonsai_lookups", {}).get("tool_log", [])),
                     ("Bonsai double-check", r.get("double_check", {}).get("tool_log", []))):
        for t in log:
            u = (t.get("args") or {}).get("url")
            if t.get("tool") in ("fetch_page", "read_filing") and u and u not in seen:
                seen[u] = {"url": u, "by": who, "ok": bool(t.get("ok")), "used": who != "Jan" and bool(t.get("ok")),
                           "note": t.get("error")}
    return list(seen.values())


def company(f: Path) -> dict[str, Any]:
    r = load(f, {})
    look = r.get("bonsai_lookups", {})
    calls = {h: {**v, "why": (r.get("reasons") or {}).get(h, "")} for h, v in (r.get("call_support") or {}).items()}
    searches = [f"{t.get('tool')}: {json.dumps(t.get('args', {}))[:160]}" for t in r.get("tool_log", [])
                if t.get("tool") in ("news_search", "stock_news")]
    return {"ticker": r.get("ticker"), "status": r.get("status"), "error": r.get("error_reason") or r.get("error"),
            "decided_at": r.get("decided_at"), "as_of": r.get("as_of"), "research_s": r.get("research_s"),
            "judge_s": r.get("judge_s"), "lookup_s": r.get("lookup_s"),
            "jan": {"searches": searches, "steps": jan_steps(r),
                    "readers": [{k: x.get(k) for k in ("url", "kept", "chars", "s", "error")} for x in r.get("reader_runs", [])],
                    "facts": [f"{x.get('text')} ({x.get('date') or 'undated'})" for x in (r.get("wide_brief") or r.get("brief") or {}).get("facts", [])]},
            "bonsai": {"lookups": look.get("steps", []), "double_check": r.get("double_check"),
                       "bull_case": r.get("bull_case"), "bear_case": r.get("bear_case"),
                       "primary": r.get("primary"), "calls": calls},
            "sites": sites(r)}


def main() -> None:
    want = sys.argv[sys.argv.index("--ticker") + 1].upper() if "--ticker" in sys.argv else None
    prog = load(FWD / "research_all" / "progress.json", {})
    files: dict[str, Path] = {}
    for f in sorted((FWD / "deep_research").glob("*/*.json")):  # run folders sort by time: the last one wins
        if f.name != "summary.json" and (want is None or f.stem == want):
            files[f.stem] = f
    rows = sorted((company(f) for f in files.values()), key=lambda x: x.get("decided_at") or x.get("as_of") or "",
                  reverse=True)
    if want is None:
        rows = [x for x in rows if x["bonsai"]["calls"] or x["status"] != "decided"][:60]
    newest = max((f.parent for f in files.values()), default=None, key=lambda d: d.stat().st_mtime)
    active = None
    if newest is not None and datetime.now(UTC).timestamp() - newest.stat().st_mtime < 300 and not (newest / "summary.json").exists():
        active = {"run": newest.name, "companies": [{"ticker": p.stem, "stage": load(p, {}).get("stage"),
                                                     "status": load(p, {}).get("status")} for p in sorted(newest.glob("*.json"))]}
    print(json.dumps({"progress": prog, "active": active, "companies": rows}, default=str))


if __name__ == "__main__":
    main()
