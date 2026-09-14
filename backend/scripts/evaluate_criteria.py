"""Evaluate a finished run against the success criteria pre-registered in docs/EXECUTION_PLAN.md.

    python scripts/evaluate_criteria.py v5_fund_top100 [v6_fund_rank20d ...]

Phase C (primary arm llm_fund; other learning arms reported for context, with a multiple-comparisons caveat):
  1. rank IC week-clustered 95% CI above 0 after warm-up
  2. beats the surprise baseline (sue_rule) on the same weeks: paired rank-IC gap CI above 0
  3. leak probe identification under 20%
Phase R:
  rl_forecast beats always_up on Brier (paired, week-clustered CI below 0)
  rl_trader after-cost Sharpe CI above 0
Writes results/criteria_<tag>.json and prints a markdown table.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.dashboard import data as D
from app.sandbox.scoring import paired_ic_gap

PRIMARY = "llm_fund"
CONTEXT_ARMS = ["llm_plain", "llm_selfimprove", "feat_fund_logit", "rl_forecast", "sue_rule"]


def evaluate(tag: str, results: Path = BACKEND / "results") -> dict[str, Any]:
    report = D.load_report(tag, results)
    preds = D.warm_filter(D.load_predictions(tag, results), report, True)
    cs = report.get("cross_sectional_after_warmup", {})
    rows = {a: g.to_dict("records") for a, g in preds.groupby("arm")}
    leak_path = results / f"leak_probe_{report['model'].replace(':', '_')}.json"
    leak = json.loads(leak_path.read_text()) if leak_path.exists() else {}
    leak_rate = (leak.get("rates") or {}).get("prices+digest")

    def arm_eval(arm: str) -> dict[str, Any]:
        ic = cs.get(arm, {})
        gap = paired_ic_gap(rows.get(arm, []), rows.get("sue_rule", [])) if arm != "sue_rule" else None
        brier = D.brier_gap_ci(preds, arm) if arm != "always_up" else None
        return {"rank_ic": ic.get("rank_ic_mean"), "rank_ic_ci": [ic.get("rank_ic_lo"), ic.get("rank_ic_hi")],
                "q_spread_pct": ic.get("q_spread_pct"), "ic_gap_vs_sue_rule": gap,
                "brier_gap_vs_always_up": brier,
                "c1_rank_ic_ci_above_0": bool(ic.get("rank_ic_lo") is not None and ic["rank_ic_lo"] > 0),
                "c2_beats_sue_rule": bool(gap and gap["lo"] > 0),
                "beats_always_up_brier": bool(brier and brier["hi"] < 0)}

    arms = {a: arm_eval(a) for a in [PRIMARY, *CONTEXT_ARMS] if a in rows}
    primary = arms.get(PRIMARY, {})
    c3 = leak_rate is not None and leak_rate < 0.20
    out: dict[str, Any] = {
        "tag": tag, "window": report["window"], "horizon_days": report["horizon_days"],
        "n_cutoffs": report["n_cutoffs"], "warmup_from": report["warmup_from"], "arms": arms,
        "leak_probe_rate": leak_rate,
        "phase_c": {"c1": primary.get("c1_rank_ic_ci_above_0", False), "c2": primary.get("c2_beats_sue_rule", False),
                    "c3": c3},
    }
    out["phase_c"]["passed"] = all(out["phase_c"][k] for k in ("c1", "c2", "c3"))
    trader = (report.get("rl_trader") or {}).get("after_warmup", {})
    if "rl_forecast" in arms:
        out["phase_r"] = {
            "rl_forecast_beats_always_up": arms["rl_forecast"]["beats_always_up_brier"],
            "rl_trader_sharpe_ci_above_0": bool(trader.get("sharpe_ci_lo") is not None and trader["sharpe_ci_lo"] > 0),
            "trader": trader,
            "weeks_trained": sum(1 for r in report.get("rl_log", []) if r["status"] != "not_enough_data"),
            "statuses": {s: sum(1 for r in report.get("rl_log", []) if r["status"] == s)
                         for s in {r["status"] for r in report.get("rl_log", [])}},
        }
        out["phase_r"]["passed"] = out["phase_r"]["rl_forecast_beats_always_up"] and \
            out["phase_r"]["rl_trader_sharpe_ci_above_0"]
    (results / f"criteria_{tag}.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    return out


def markdown(ev: dict[str, Any]) -> str:
    def f(x: Any, fmt: str = "{:+.4f}") -> str:
        return "—" if x is None else fmt.format(x)

    lines = [(f"#### `{ev['tag']}` — {ev['horizon_days']}-day horizon, {ev['n_cutoffs']} cutoffs, "
              f"scored from {ev['warmup_from']}"), "",
             "| arm | rank IC [95% CI] | top−bottom fifth %/period | IC gap vs sue_rule [CI] | Brier gap vs always-up [CI] |",
             "|---|---|---|---|---|"]
    for arm, a in ev["arms"].items():
        g, b = a["ic_gap_vs_sue_rule"], a["brier_gap_vs_always_up"]
        lines.append(f"| {arm}{' (primary)' if arm == PRIMARY else ''} | {f(a['rank_ic'])} "
                     f"[{f(a['rank_ic_ci'][0])}, {f(a['rank_ic_ci'][1])}] | {f(a['q_spread_pct'], '{:+.3f}')} | "
                     + (f"{g['gap']:+.4f} [{g['lo']:+.4f}, {g['hi']:+.4f}]" if g else "—") + " | "
                     + (f"{b['gap']:+.4f} [{b['lo']:+.4f}, {b['hi']:+.4f}]" if b else "—") + " |")
    pc = ev["phase_c"]
    lines += ["", (f"Phase C criteria (primary arm): rank IC CI > 0: **{pc['c1']}**; beats surprise baseline: "
                   f"**{pc['c2']}**; leak probe {ev['leak_probe_rate']} < 20%: **{pc['c3']}** → "
                   f"**{'PASSED' if pc['passed'] else 'NOT PASSED'}**")]
    if "phase_r" in ev:
        pr, t = ev["phase_r"], ev["phase_r"]["trader"]
        lines.append(f"Phase R: weeks trained {pr['weeks_trained']} {pr['statuses']}; rl_forecast beats always-up on "
                     f"Brier: **{pr['rl_forecast_beats_always_up']}**; trader after-cost Sharpe "
                     f"{t.get('sharpe_after_costs')} [CI {t.get('sharpe_ci_lo')}, {t.get('sharpe_ci_hi')}], "
                     f"mean weekly {t.get('mean_weekly_pct_after_costs')}% after costs "
                     f"({t.get('mean_weekly_pct_gross')}% gross), mean |position| {t.get('mean_abs_position')}: "
                     f"**{pr['rl_trader_sharpe_ci_above_0']}** → **{'PASSED' if pr['passed'] else 'NOT PASSED'}**")
    return "\n".join(lines)


if __name__ == "__main__":
    print("\n\n".join(markdown(evaluate(t)) for t in sys.argv[1:]))
