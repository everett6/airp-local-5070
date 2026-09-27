# Executive plan: night of 2026-09-26 to 27

Written 22:50, before any of tonight's results exist. Paper money, free data, manual runs only (no services).
Ends with a push and a shutdown (the user asked for both).

## Where things stand

- **The lead:** Bonsai-27B's fact-sheet decision for the 1-week book. 2025-26 IC +0.103 [+0.051, +0.158] on the
  SEC-cross-checked sheets; 2024 +0.027 (positive, not significant). As a portfolio (with EPS change, momentum,
  guidance): 21.42% a year, Sharpe 1.24, vs 20.40% / 1.18 for SPY + the crypto sleeve (p = 0.06 against 50
  shuffles).
- **Failed tonight's pre-registered tests:** the analyst-target features (0 of 12); the 3-month, 1-year and 2-year
  books.
- **Running now:** web research v3 (Jan-v1-4B gathers, Bonsai writes source-checked briefs) on the 1,180-release
  2025-26 sample. Jan's phase is done (3 s per release); Bonsai's briefs finish ~01:35; then Bonsai decides the
  1-week, 1-month, 3-month, 6-month and 1-year books on the researched fact sheets, ~02:40.

## Steps

| # | When | Step | Output |
|---|---|---|---|
| 1 | now (CPU, next to the GPU run) | **Cost sensitivity of the lead:** the 1-week book alone at 0 / 10 / 25 bps per trade | results/master_full_quick_cost*.txt |
| 2 | now (CPU) | **Leak audit of the research:** no brief fact dated after its release's decision time; every cited source among the tools' own results | results/events/research_v3_audit.txt |
| 3 | now (CPU) | **Debug pass:** full test suite, lint of the week's code, review of the diffs since the start of the week | fixes + tests |
| 4 | ~02:40 | **Score research v3** with the rule written before the run (WEEK_PLAN.md, Research v3): per book, IC with research higher than without on the same releases AND its 95% interval above zero | results/events/research_v3_eval.txt |
| 5 | after 4 | **If a book passes:** its master portfolio with and without research over 2025-26 (as in Block 1). If none passes: research stays off for the forward test; recorded as such | results/master_full_research_v3_* |
| 6 | after 5 | Update WEEK_PLAN.md and EXECUTIVE_SUMMARY.md, commit, push | GitHub |
| 7 | last | Stop every model server, check nothing is left running, shut down | |

## Rules for tonight

- No test's pass rule changes after its results are in; a result that only shows up after the fact is a lead.
- Nothing is dropped unilaterally: a failed book stays in the record as failed.
- If the research run breaks, the debug pass fixes and resumes it (every step is resumable) before the shutdown;
  if it cannot finish before the morning, the plan records where it stopped and the machine still shuts down only
  after the push.

## Next plan, first item: failure log and weekly review (added 2026-09-26, 23:00)

Process failures are collected in one place and turned into tested fixes. They are judged without any stock outcome,
so learning from them cannot leak the future.

- **Log** (`results/failures.jsonl`, one row per failure: time, run, release, stage, cause, detail), filled from
  what the runs already record: tool errors and timeouts (tool_log), unparseable replies (parse_failures), brief
  facts dropped by the source check (brief.dropped: no source / unknown source / number not in the evidence),
  repeated tool calls, failed briefs or decisions, vLLM/Ollama start failures.
- **Weekly review** (`scripts/failure_review.py`): failures grouped by cause and stage, with the change since the
  previous week and the top causes. Each proposed fix is tested on the fixed 24-release benchmark
  (`scripts/spec_bench.sh`) and adopted only if it wins, with the result recorded here.
- **Not in scope:** learning from wrong picks. Stock outcomes are mostly noise; they only enter through the monthly
  walk-forward refits (calibrator, combined score) and pre-registered forward tests.
- **Later, with the user's OK for any download:** fine-tune Jan (LoRA) on its research runs whose facts passed the
  source check.
