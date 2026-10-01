"""Weekly review of the forward test (docs/PLAN_60_V2.md, Stage 3). Run by hand; reads the ledgers, changes nothing in
them, and writes results/forward/review_<date>.md.

    python scripts/weekly_review.py

Sections:
  books     each allocator book's equity, return and drawdown since the start, and its brake state
  shadows   the braked master book at 1.0x / 1.5x / 2.0x: levered from its own run-to-run returns, the borrowed part
            paying rf + 1.5% a year (FRED DTB3). Paper only: nothing is traded at these sizes.
  events    the 1-week Bonsai book's scoreboard (forward_events.py): decisions on time, missed, outcomes, IC per
            source (Bonsai and the lite fallback are never mixed)
  failures  weekdays with no allocator or event run, and every missed decision with its reason
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import pandas as pd

from app.forward.ledger import Ledger
from app.portfolio.forward import AGGRESSIVE, brake_multiplier

FWD = BACKEND / "results" / "forward"
SPREAD = 0.015


def weekdays(a: date, b: date) -> list[date]:
    return [a + timedelta(days=i) for i in range((b - a).days + 1) if (a + timedelta(days=i)).weekday() < 5]


def shadows(eq: pd.Series, rf: pd.Series) -> dict[float, float]:
    out = {}
    for lev in (1.0, 1.5, 2.0):
        v = 1.0
        for (d0, e0), (d1, e1) in zip(eq.items(), list(eq.items())[1:], strict=False):
            days = (d1 - d0).days
            r = float(rf.asof(pd.Timestamp(d0))) if len(rf) else 0.0
            v *= 1 + lev * (e1 / e0 - 1) - (lev - 1) * (r + SPREAD) * days / 365
        out[lev] = v
    return out


def goal_lines() -> list[str]:
    """Progress against the user's saved goal (results/planner/goal.json), on today's evidence (app/portfolio/planner.py)."""
    import json

    from app.portfolio.planner import Goal, plan
    d = BACKEND / "results" / "planner"
    if not (d / "goal.json").exists() or not (d / "track_returns.parquet").exists():
        return []
    g = json.loads((d / "goal.json").read_text())
    res = plan(Goal(float(g["start"]), float(g["target"]), date.fromisoformat(g["by"]),
                    datetime.now(UTC).date()),
               pd.read_parquet(d / "track_returns.parquet"))
    q, n = res["goal"], res["with_current_evidence"]
    return ["", "## Goal", "",
            f"- ${q['start']:,.0f} -> ${q['target']:,.0f} by {q['by']}: needs {q['required_cagr']:.1%} a year",
            (f"- Odds on today's evidence: {n['p_goal']:.0%} (median ${n['median_end']:,.0f}; gap "
             f"{res['gap_cagr']:+.1%} a year)"),
            *[f"- {t['label']}: {t['status'].replace('_', ' ')}, {t['weight']:.0%}" for t in res["tracks"]]]


def shadow_lines(fwd: Path = FWD) -> list[str]:
    """The no-money AI tests: the three live lenses (C2, D, E), the AI-picks sleeve, long-term picks and themes.
    A section that cannot be read says so instead of stopping the review."""
    out = ["", "## AI tests running on live data", ""]
    try:
        import net_read_shadow as N
        for lens in N.LENSES:
            st = N.status(fwd / "events", lens)
            ic = "—" if st["mean_ic"] is None else f"{st['mean_ic']:+.3f}"
            out.append(f"- {lens}: {st['events']} scored releases, {st['months']} months, IC {ic} "
                       f"(80% bound {st['ic_lo80']}); judged at 150 releases and 3 months")
    except Exception as e:  # noqa: BLE001
        out.append(f"- live lenses: could not read ({type(e).__name__}: {e})")
    try:
        import consensus_shadow as CS
        st = CS.status(fwd / "events", fwd / "consensus")
        parts = []
        for k in ("eq", "rw"):
            c = st[f"consensus_{k}"]
            parts.append(f"{k} IC " + ("—" if c["mean_ic"] is None else f"{c['mean_ic']:+.3f}")
                         + f" (80% bound {c['ic_lo80']})")
        out.append(f"- consensus of the agents (equal and record-weighted): {st['scored']} scored releases, "
                   + ", ".join(parts) + "; judged at 150 releases and 3 months")
    except Exception as e:  # noqa: BLE001
        out.append(f"- consensus: could not read ({type(e).__name__}: {e})")
    try:
        from app.portfolio import sleeve as S
        for name in ("ai_picks", "ai_picks_autodry"):
            f = fwd / name / "book.json"
            if f.exists():
                sm = S.summary(json.loads(f.read_text()))
                out.append(f"- AI-picks sleeve ({name}): equity ${sm['equity']:,.0f}, return {sm['return']:+.2%}, "
                           f"drawdown {sm['drawdown']:.1%}, {sm['closed']} pairs closed")
                break
    except Exception as e:  # noqa: BLE001
        out.append(f"- AI-picks sleeve: could not read ({type(e).__name__}: {e})")
    try:
        import guidance_shadow as G
        f = fwd / "guidance_shadow" / "ledger.jsonl"
        recs = Ledger(f).verify() if f.exists() else []
        out.append(f"- guidance shadow: {json.dumps(G.status(recs))}")
    except Exception as e:  # noqa: BLE001
        out.append(f"- guidance shadow: could not read ({type(e).__name__}: {e})")
    for name, mod in (("longterm", "longterm_picks"), ("themes", "themes")):
        try:
            m = __import__(mod)
            f = fwd / name / "ledger.jsonl"
            recs = Ledger(f).verify() if f.exists() else []
            out.append(f"- {name}: {json.dumps(m.status(recs))}")
        except Exception as e:  # noqa: BLE001
            out.append(f"- {name}: could not read ({type(e).__name__}: {e})")
    return out


def main() -> None:
    lines = [f"# Forward test review, {datetime.now(UTC).date().isoformat()}", ""]
    alloc = FWD / "allocator" / "ledger.jsonl"
    runs = [json.loads(x) for x in alloc.read_text().splitlines()] if alloc.exists() else []
    lines += ["## Books", ""]
    run_days: set[date] = set()
    if runs:
        run_days = {date.fromisoformat(r["run_at_utc"][:10]) for r in runs}
        lines += ["| Book | Start | Now | Return | Max drawdown | Brake |", "|---|---|---|---|---|---|"]
        names = sorted({k for r in runs for k in r["books"]})
        for n in names:
            pts = [(date.fromisoformat(r["data_through"]), r["books"][n]["equity"]) for r in runs if n in r["books"]]
            eq = pd.Series({d: e for d, e in pts})
            dd = float((1 - eq / eq.cummax()).max())
            lines.append(f"| {n} | {eq.iloc[0]:,.0f} | {eq.iloc[-1]:,.0f} | {eq.iloc[-1] / eq.iloc[0] - 1:+.2%} | "
                         f"{dd:.1%} | {brake_multiplier(eq.iloc[-1], eq.max()):.2f} |")
        base = "master+brakes" if any("master+brakes" in r["books"] for r in runs) else "master"
        agg = [r["books"][AGGRESSIVE] for r in runs if AGGRESSIVE in r["books"]]
        if agg:  # the user's 2.5x paper book (PLAN_60_V2 "Aggressive book, 2.5x"): reported, never evidence
            a = agg[-1]
            lines += ["", f"**{AGGRESSIVE}** (your decision of 30 Sep; not a tested strategy): " + (
                "wiped out: the loan grew larger than the holdings." if a.get("wiped_out") else
                f"its last run failed ({a['error']}); the other books were not affected." if "error" in a else
                f"holding {a.get('gross', 0):.2f}× its equity, borrowed {a.get('borrowed', 0):,.0f}, interest paid so "
                f"far {a.get('interest_paid', 0):,.0f}. Compare its return with 2.5× the {base} return above.")]
        eq = pd.Series({date.fromisoformat(r["data_through"]): r["books"][base]["equity"] for r in runs
                        if base in r["books"]})
        rf_f = BACKEND / "data" / "fred_dtb3.csv"
        rf = pd.Series(dtype=float)
        if rf_f.exists():
            df = pd.read_csv(rf_f)
            rf = pd.Series(pd.to_numeric(df.iloc[:, 1], errors="coerce").to_numpy() / 100,
                           index=pd.to_datetime(df.iloc[:, 0])).ffill().dropna()
        lines += ["", f"## Shadow books ({base}, levered on paper; borrowing at rf + 1.5%)", ""]
        lines += [f"- {lev:.1f}×: {v - 1:+.2%}" for lev, v in shadows(eq, rf).items()]
    else:
        lines.append("No allocator runs yet.")

    lines += ["", "## Core leads, forward check (shadows, no money; verdict from 2027-09-30)", ""]
    try:
        from core_leads import forward
        f = forward()
        if "note" in f:
            lines.append(f"- {f['days']} forward days: {f['note']}")
        for k in ("vol_target", "risk_parity"):
            if k in f:
                v = f[k]
                lines.append(f"- {k}: Sharpe vs B0 {v['sharpe_diff']:+.2f} (90% CI {v['ci90']}), vol-matched CAGR "
                             f"{v['cagr_vol_matched_pct']}% vs {v['b0_cagr_pct']}% over {f['days']} days"
                             + (" (verdict due)" if f["verdict_due"] else " (interim, not judged)"))
    except Exception as e:  # noqa: BLE001 - a report line must never fail the review
        lines.append(f"- not available: {type(e).__name__}: {e}"[:200])

    lines += ["", "## Trading health (report only)", ""]
    try:
        from trading_health import load
        s4, ex = load()
        if "sharpe" in s4:
            lines.append(f"- Leverage gate ({s4['book']}): forward Sharpe {s4['sharpe']}, lower 80% bound "
                         f"{s4.get('lower80', 'n/a')}, {s4['weeks']} weeks / {s4['months']} months, vol "
                         f"{s4.get('vol_pct', 'n/a')}%, max drawdown {s4['max_drawdown_pct']}%; Stage 4 row: "
                         f"{s4.get('stage4_row', s4.get('note', 'n/a'))}")
        else:
            lines.append(f"- Leverage gate ({s4['book']}): {s4.get('note', 'no data')}")
        lines.append(f"- Paper execution: {ex['legs']} broker legs {ex['by_status']}; fill gap vs simulator "
                     f"mean {ex['mean_abs_gap_bp'] if ex['mean_abs_gap_bp'] is not None else 'n/a'} bp, worst "
                     f"{ex['worst_gap_bp'] if ex['worst_gap_bp'] is not None else 'n/a'} bp, "
                     f"{ex['gaps_over_alert']} over 0.5%; {ex['problem_legs']} rejected/canceled/expired; "
                     f"AI pairs {ex['ai_pairs']} (audit flags {ex['ai_audit_flags']})")
    except Exception as e:  # noqa: BLE001 - a report line must never fail the review
        lines.append(f"- not available: {type(e).__name__}: {e}"[:200])

    lines += ["", "## T1 / O1 forward shadows (failed backtests, tracked on new days only; no money; verdict 2028-09-30)", ""]
    try:
        from calendar_shadows import forward
        f = forward()
        for k in ("T1", "O1"):
            v = f[k]
            lines.append(f"- {k}: {v['days']} days ({v['active_days']} active), net excess "
                         f"{v['cum_net_excess_pct'] if v['cum_net_excess_pct'] is not None else 'n/a'}%, "
                         f"Sharpe {v['sharpe'] if v['sharpe'] is not None else 'n/a'}, "
                         f"95% CI {v['ci95'] or 'n/a'}" + (" (verdict due)" if f["verdict_due"] else ""))
    except Exception as e:  # noqa: BLE001 - a report line must never fail the review
        lines.append(f"- not available: {type(e).__name__}: {e}"[:200])

    lines += ["", "## 1-week Bonsai book (shadow, 0 weight)", ""]
    led = Ledger(FWD / "events" / "ledger.jsonl")
    recs = led.verify()
    if recs:
        from forward_events import score
        s = score(recs)
        lines += [f"- {k}: {v}" for k, v in s.items()]
        run_days |= {date.fromisoformat(r["as_of"][:10]) for r in recs if r["type"] == "run"}
    else:
        lines.append("No event runs yet.")

    lines += ["", "## Failures", ""]
    if run_days:
        gap = [d.isoformat() for d in weekdays(min(run_days), datetime.now(UTC).date()) if d not in run_days]
        lines.append(f"- Weekdays with no run: {len(gap)}" + (f" ({', '.join(gap[-10:])})" if gap else ""))
    missed = [r for r in recs if r["type"] == "missed"]
    lines.append(f"- Missed decisions: {len(missed)}")
    reasons = pd.Series([r["reason"] for r in missed]).value_counts() if missed else pd.Series(dtype=int)
    lines += [f"  - {n} × {why}" for why, n in reasons.items()]
    lines += shadow_lines()
    lines += goal_lines()
    out = FWD / f"review_{datetime.now(UTC).date().isoformat()}.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwritten to {out.relative_to(BACKEND)}")


if __name__ == "__main__":
    main()
