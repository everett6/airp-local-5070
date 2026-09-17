"""Evaluate a finished run against the success criteria pre-registered in docs/EXECUTION_PLAN.md.

    python scripts/evaluate_criteria.py v5_fund_top100 [v6_fund_rank20d ...]

Phase C (primary arm llm_fund; other learning arms reported for context, with a multiple-comparisons caveat):
  1. rank IC week-clustered 95% CI above 0 after warm-up
  2. beats the surprise baseline (sue_rule) on the same weeks: paired rank-IC gap CI above 0
  3. leak probe identification under 20%
Phase R:
  rl_forecast beats always_up on Brier (paired, week-clustered CI below 0)
  rl_trader after-cost Sharpe CI above 0
Phase F (runs with score_logprob, anomalies and kronos, e.g. v7; pre-registered in docs/EXECUTION_PLAN.md):
  F1  llm_fund_lp rank IC week-clustered 95% CI above 0 after warm-up
  F2  llm_fund_lp beats anomaly_rank, kronos AND sue_rule on paired rank-IC gap (each CI above 0)
  F3  no leak: company identification < 20% AND no lookahead-propensity signature (scripts/lap_test.py, d CI not > 0)
  R'  rl_forecast beats always_up on Brier AND the trader's Deflated Sharpe > 0.95, where the number of trials is
      every arm of every published walk-forward run (counted from results/walkforward_*.json)
Every run also reports PSR/DSR for its long/short arms (reporting only).
Writes results/criteria_<tag>.json and prints a markdown table.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, cast

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import numpy as np

from app.dashboard import data as D
from app.sandbox.scoring import (
    deflated_sharpe,
    paired_ic_gap,
    probabilistic_sharpe,
    weekly_long_short,
)

PRIMARY = "llm_fund"
CONTEXT_ARMS = ["llm_plain", "llm_selfimprove", "feat_fund_logit", "rl_forecast", "sue_rule"]
PRIMARY_F = "llm_fund_lp"
CONTEXT_ARMS_F = ["llm_lp", "llm_fund", "anomaly_rank", "anomaly_logit", "kronos"]
BASELINES_F = ["anomaly_rank", "kronos", "sue_rule"]


def published_trials(results: Path) -> int:
    """Number of arms across every published walk-forward run: the multiple-testing count for Deflated Sharpe."""
    n = 0
    for f in results.glob("walkforward_*.json"):
        if f.name.endswith("_predictions.json"):
            continue
        try:
            n += len(json.loads(f.read_text()).get("scores_full", {}))
        except (ValueError, OSError):
            continue
    return max(n, 1)


def evaluate(tag: str, results: Path = BACKEND / "results") -> dict[str, Any]:
    report = D.load_report(tag, results)
    preds = D.warm_filter(D.load_predictions(tag, results), report, True)
    cs = report.get("cross_sectional_after_warmup", {})
    rows: dict[str, list[dict[str, Any]]] = {
        str(a): cast(list[dict[str, Any]], g.to_dict("records")) for a, g in preds.groupby("arm")}
    leak_path = results / f"leak_probe_{report['model'].replace(':', '_')}.json"
    leak = json.loads(leak_path.read_text()) if leak_path.exists() else {}
    leak_rate = (leak.get("rates") or {}).get("prices+digest")
    horizon = int(report["horizon_days"])
    block = D.bootstrap_block(report)  # >1 when outcome windows overlap (e.g. v6: 20-day returns every 5 days)

    def arm_eval(arm: str) -> dict[str, Any]:
        ic = cs.get(arm, {})
        gap = (paired_ic_gap(rows.get(arm, []), rows.get("sue_rule", []), block=block)
               if arm != "sue_rule" else None)
        brier = D.brier_gap_ci(preds, arm, block=block) if arm != "always_up" else None
        return {"rank_ic": ic.get("rank_ic_mean"), "rank_ic_ci": [ic.get("rank_ic_lo"), ic.get("rank_ic_hi")],
                "q_spread_pct": ic.get("q_spread_pct"), "ic_gap_vs_sue_rule": gap,
                "brier_gap_vs_always_up": brier,
                "c1_rank_ic_ci_above_0": bool(ic.get("rank_ic_lo") is not None and ic["rank_ic_lo"] > 0),
                "c2_beats_sue_rule": bool(gap and gap["lo"] > 0),
                "beats_always_up_brier": bool(brier and brier["hi"] < 0)}

    phase_f = bool(report.get("config", {}).get("score_logprob") and report["config"].get("anomalies")
                   and report["config"].get("kronos"))
    wanted = [PRIMARY, *CONTEXT_ARMS] + ([PRIMARY_F, *CONTEXT_ARMS_F] if phase_f else [])
    arms = {a: arm_eval(a) for a in dict.fromkeys(wanted) if a in rows}
    primary = arms.get(PRIMARY, {})
    c3 = leak_rate is not None and leak_rate < 0.20
    out: dict[str, Any] = {
        "tag": tag, "window": report["window"], "horizon_days": report["horizon_days"],
        "n_cutoffs": report["n_cutoffs"], "warmup_from": report["warmup_from"], "bootstrap_block": block, "arms": arms,
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
    # multiple-testing-aware Sharpe for every long/short arm (reporting only, except R' below)
    n_trials = published_trials(results)
    ls = {a: weekly_long_short(r) for a, r in rows.items() if a != "always_up"}
    sr_trials = [float(np.mean(v) / np.std(v, ddof=1)) for v in ls.values() if len(v) > 2 and np.std(v, ddof=1) > 0]
    out["sharpe_deflation"] = {"n_trials": n_trials, "arms": {
        a: {"psr": probabilistic_sharpe(v, block=block), **deflated_sharpe(v, n_trials, sr_trials, block, horizon)}
        for a, v in ls.items()}}
    trader_rows = [{"cutoff": r["cutoff"], "ret": r["ret"], "position": r["position"]}
                   for r in rows.get("rl_forecast", []) if r.get("position") is not None]
    trader_weekly = trader_weekly_returns(trader_rows)
    if trader_weekly:
        out["sharpe_deflation"]["rl_trader"] = {"psr": probabilistic_sharpe(trader_weekly, block=block),
                                                **deflated_sharpe(trader_weekly, n_trials, sr_trials, block, horizon)}
    if phase_f:
        pa = arms.get(PRIMARY_F, {})
        lap_path = results / f"lap_test_{tag}_{PRIMARY_F}.json"
        lap = json.loads(lap_path.read_text()) if lap_path.exists() else None
        gaps = {b: (paired_ic_gap(rows.get(PRIMARY_F, []), rows.get(b, []), block=block) if b in rows else None)
                for b in BASELINES_F}
        f3 = bool(c3 and lap is not None and not lap["leak_signature"])
        dsr = (out["sharpe_deflation"].get("rl_trader") or {}).get("dsr")
        out["phase_f"] = {
            "f1": pa.get("c1_rank_ic_ci_above_0", False),
            "f2": all(g is not None and g["lo"] > 0 for g in gaps.values()), "f2_gaps": gaps,
            "f3": f3, "lap_test": lap,
            "lp_vs_verbal_ic_gap": paired_ic_gap(rows.get(PRIMARY_F, []), rows.get("llm_fund", []), block=block),
        }
        out["phase_f"]["passed"] = out["phase_f"]["f1"] and out["phase_f"]["f2"] and out["phase_f"]["f3"]
        out["phase_r_v7"] = {"rl_forecast_beats_always_up": arms.get("rl_forecast", {}).get("beats_always_up_brier", False),
                             "trader_dsr": dsr, "trader_dsr_above_0_95": bool(dsr is not None and dsr > 0.95)}
        out["phase_r_v7"]["passed"] = (out["phase_r_v7"]["rl_forecast_beats_always_up"]
                                       and out["phase_r_v7"]["trader_dsr_above_0_95"])
    (results / f"criteria_{tag}.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    return out


def trader_weekly_returns(rows: list[dict[str, Any]], cost_bps: float = 5.0) -> list[float]:
    """Same weekly after-cost P&L as app.learning.rl_agent.score_trader, as a series."""
    by: dict[Any, list[float]] = {}
    for r in rows:
        by.setdefault(r["cutoff"], []).append(r["position"] * r["ret"] - cost_bps / 1e4 * abs(r["position"]))
    return [float(np.mean(v)) for _, v in sorted(by.items(), key=lambda kv: kv[0])]


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
    if "phase_f" in ev:
        pf, rv = ev["phase_f"], ev["phase_r_v7"]
        def gap_text(x: dict[str, float] | None) -> str:
            return "—" if x is None else f"{x['gap']:+.4f} [{x['lo']:+.4f}, {x['hi']:+.4f}]"
        g = " ".join(f"{b}: {gap_text(x)}" for b, x in pf["f2_gaps"].items())
        lap = pf["lap_test"] or {}
        lines.append(f"Phase F (primary {PRIMARY_F}): F1 rank IC CI > 0: **{pf['f1']}**; F2 beats baselines ({g}): "
                     f"**{pf['f2']}**; F3 no leak (identification < 20% and LAP interaction d CI "
                     f"{lap.get('d_ci')} not above 0): **{pf['f3']}** → **{'PASSED' if pf['passed'] else 'NOT PASSED'}**")
        lines.append(f"Phase R (v7): rl_forecast beats always-up on Brier: **{rv['rl_forecast_beats_always_up']}**; "
                     f"trader Deflated Sharpe {rv['trader_dsr']} > 0.95: **{rv['trader_dsr_above_0_95']}** → "
                     f"**{'PASSED' if rv['passed'] else 'NOT PASSED'}**")
    sd = ev.get("sharpe_deflation")
    if sd:
        best = max(((a, v) for a, v in sd["arms"].items() if v.get("dsr") is not None), key=lambda kv: kv[1]["dsr"],
                   default=None)
        if best:
            lines.append(f"Deflated Sharpe ({sd['n_trials']} trials across all published runs): best long/short arm "
                         f"`{best[0]}` DSR {best[1]['dsr']:.3f} (PSR {best[1]['psr']:.3f}); luck benchmark "
                         f"{best[1]['sr0_annual']} annualized Sharpe")
    return "\n".join(lines)


if __name__ == "__main__":
    print("\n\n".join(markdown(evaluate(t)) for t in sys.argv[1:]))
