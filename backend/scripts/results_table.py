"""Render walk-forward result JSONs as markdown tables (after-warm-up scores).

    python scripts/results_table.py results/walkforward_v1.json results/walkforward_v3_excess.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ORDER = ["always_up", "base_rate", "momentum_20d", "reversal_5d", "feat_logit",
         "llm_plain", "llm_selfimprove", "selector"]


def render(path: Path) -> str:
    r = json.loads(path.read_text())
    s = r["scores_after_warmup"]
    n = next(iter(s.values()))["n"]
    se = 50 / n ** 0.5
    head = (
        f"### `{r['tag']}` — {r['model']}, target={r.get('target', 'abs')}, "
        f"{r['window'][0]}..{r['window'][1]}, {r['n_cutoffs']} cutoffs x {len(r['tickers'])} tickers\n\n"
        f"Scored after warm-up (from {r['warmup_from']}), n={n}; 1 standard error of accuracy "
        f"is about ±{se:.1f} pts. Jail probe passed: {r['jail_probe_start']['passed']} (start), "
        f"{r['jail_probe_end']['passed']} (end). LLM calls: {r['llm_calls']} new + {r['cache_hits']} cached.\n\n"
        "| arm | accuracy | % 'up' calls | Brier (lower=better) | long/short weekly ret | L/S Sharpe |\n"
        "|---|---|---|---|---|---|\n"
    )
    best = min(v["brier"] for v in s.values())
    rows = []
    for k in ORDER:
        if k not in s:
            continue
        v = s[k]
        b = f"**{v['brier']:.4f}**" if v["brier"] == best else f"{v['brier']:.4f}"
        rows.append(f"| {k} | {v['accuracy'] * 100:.1f}% | {v['pct_up_calls'] * 100:.0f}% | {b} | "
                    f"{v['ls_mean_weekly_ret_pct']:+.2f}% | {v['ls_sharpe_ann']:.2f} |")
    extra = ""
    if r.get("stacker_log"):
        extra += f"\nStacker decisions: {dict(Counter(x.get('stacker') for x in r['stacker_log']))}. "
    if r.get("selector_log"):
        extra += f"Selector choices: {dict(Counter(x['chosen'] for x in r['selector_log']))}.\n"
    return head + "\n".join(rows) + "\n" + extra


if __name__ == "__main__":
    print("\n\n".join(render(Path(p)) for p in sys.argv[1:]))
