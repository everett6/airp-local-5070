"""Daily phone digest: one short push after the evening event run, so the user can see the day without Claude.

    python scripts/digest.py            # print today's digest (sends nothing)
    python scripts/digest.py --send     # print and push it via ntfy (the approved alert channel)

It reads only the forward files. Paper money only; nothing here trades.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
FWD = BACKEND / "results" / "forward"


def _jsonl(p: Path) -> list[dict[str, Any]]:
    out = []
    if p.exists():
        for line in p.read_text(errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def build(day: date, fwd: Path = FWD, research: list[dict[str, Any]] | None = None) -> str:
    """The digest text for `day` (UTC dates of the records)."""
    d = day.isoformat()
    beats = [h for h in _jsonl(fwd / "heartbeat.jsonl") if h.get("start", "").startswith(d)]
    runs = [h for h in beats if h.get("job") in ("events", "allocator")]
    # a run whose event runner finished but a later step alerted (rc 1, scoreboard present) is not a failed run
    done = [h for h in runs if h.get("rc") == 0 or (h.get("job") == "events" and h.get("missed_total") is not None)]
    bad = [h for h in runs if h not in done]
    noisy = [h for h in done if h.get("rc") != 0]
    led = [r for r in _jsonl(fwd / "events" / "ledger.jsonl") if str(r.get("as_of", "")).startswith(d)]
    dec = [r for r in led if r.get("type") == "decision"]
    missed = [r for r in led if r.get("type") == "missed"]
    alerts = [a for a in _jsonl(fwd / "alerts.jsonl") if a.get("at", "").startswith(d) and a.get("job") != "ntfy"]
    lines = [f"airp {d} ({(fwd / 'AUTORUN_MODE').read_text().strip() if (fwd / 'AUTORUN_MODE').exists() else 'dry'})"]
    lines.append(f"runs: {len(runs) - len(bad) - len(noisy)}/{len(runs)} clean"
                 + (f", {len(noisy)} finished with an alert" if noisy else "")
                 + (f", FAILED: {', '.join(h['job'] for h in bad)}" if bad else ""))
    top = sorted(dec, key=lambda r: -float(r.get("logodds") or 0))[:3]
    tops = ", ".join(f"{r.get('ticker')} {float(r.get('logodds') or 0):+.1f}" for r in top)
    lines.append(f"decisions: {len(dec)} on time, {len(missed)} missed" + (f"; top {tops}" if top else ""))
    book = fwd / "ai_picks" / "book.json"
    if book.exists():
        b = json.loads(book.read_text())
        st: dict[str, int] = {}
        for p in b.get("pairs", []):
            st[p.get("status", "?")] = st.get(p.get("status", "?"), 0) + 1
        lines.append(f"AI picks: equity {b.get('equity', 0):,.0f}; pairs {st or 'none'}")
    lines.append(f"alerts: {len(alerts)}" + (f" (latest: {alerts[-1]['msg'][:80]})" if alerts else ""))
    if research:
        parts = [f"{j['name']} {j['progress'][0]}/{j['progress'][1]}" + (" running" if j["running"] else "")
                 for j in research if j["kind"] != "gate" and not j["done"]]
        gates = [j["name"] for j in research if j["kind"] == "gate" and not j["done"]
                 and all(any(k["name"] == r and k["done"] for k in research) for r in j["requires"])]
        lines.append("research: " + ("; ".join(parts) or "idle") + (f"; WAITING: {', '.join(gates)}" if gates else ""))
    return "\n".join(lines)


def send(text: str) -> bool:
    import urllib.request
    sys.path.insert(0, str(BACKEND / "scripts"))
    from autorun import append, ntfy_topic
    topic = ntfy_topic()
    ok = False
    if topic:
        try:
            req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=text.encode()[:3000], method="POST",
                                         headers={"Title": "Paper book: daily digest", "Priority": "low"})
            urllib.request.urlopen(req, timeout=15).read()
            ok = True
        except OSError:
            ok = False
    append("digests.jsonl", {"at": datetime.now(UTC).isoformat(timespec="seconds"), "sent": ok, "text": text})
    return ok


def main() -> None:
    sys.path.insert(0, str(BACKEND / "scripts"))
    from research_queue import status
    text = build(datetime.now(UTC).date(), research=status())
    print(text)
    if "--send" in sys.argv:
        print("sent" if send(text) else "not sent (no topic or network)")


if __name__ == "__main__":
    main()
