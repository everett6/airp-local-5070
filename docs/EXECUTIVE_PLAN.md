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

## Status at 04:00 on 2026-09-27

- **Steps 1–5: done.**
  - Research v3 failed its rule in every book, so step 5's portfolio comparison did not apply.
  - Leak audit: 0 leaks. The 9 flags were all date labels.
  - Results: WEEK_PLAN.md ("Research v3") and PLAN_60_V2.md.
- **Step 6:** docs updated through the night and pushed after each result.
- **Step 7 (shutdown)** comes after the breadth test.
  - That test is the last GPU job, run on the user's 10-hour autonomous window.
  - It is expected to finish around 08:30.
- **Failure log: built.**
  - `scripts/failure_review.py` writes `results/failures.jsonl` and appends to `results/failures_summary.jsonl`, with a test.
  - **First review, research v3 (1,180 releases):**
    - 1,781 brief facts were dropped because a number was not in the cited source: 1.5 per brief, 26% of all facts.
    - 775 news lookups timed out, and 604 had no archived page.
    - Analyst-target pages were missing or timed out 261 times.
    - 18 calls used an unknown tool name; 5 replies could not be parsed.
  - **Candidate fixes, each to be tested on the 24-release benchmark (`scripts/spec_bench.sh`) before adoption:**
    - Skip `news_as_of` once the archive has timed out twice in a run.
    - Show the brief writer each number next to its source tag, so it cites the right one.

## Final status at 07:55 on 2026-09-27

- The breadth test (the last GPU job) failed both rules.
  - Bonsai's IC on S&P 400/600 releases is −0.002 on 6,597 releases.
  - The combined book stays as frozen.
- The spike check failed its rule and is kept as information only.
- The daily trend check failed its rule and is a lead.
- The crypto trend rule is a plateau (Sharpe 0.85–1.07 across 30 parameter pairs).
- Final checks:
  - 404 tests pass;
  - ruff and strict mypy are clean;
  - CI is green again.
- Then: push, stop every model server, check that nothing is left running, and shut down.
