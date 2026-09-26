# Week plan: Fri 2026-09-25 to Fri 2026-10-02

Goal for the week: find out whether web research makes Bonsai's stock picks better in any of the three books (quick
money 5 days, mid term 20, long term 120), turn whatever works into a paper portfolio with the crypto sleeve and SPY
core, and start forward-testing it. Paper money, free data, nothing runs as a service. Every test's pass rule is
written here before its results exist.

Setup that all blocks use: Jan-v1-4B (NVFP4 on vLLM with FlashInfer, `scripts/vllm_serve.sh`) researches with the
as-of web tools after a code prefetch (prices, filings, the press release itself, Wikipedia, news); Bonsai-27B (Ollama)
writes the source-checked brief and decides BUY/PASS with a probability per book.

## Block 1 — Fri night, ~10 h, unattended (`scripts/night_run.sh`)

| Time | Step |
|---|---|
| 21:50 - 05:15 | Research the 1,180-release random 2025-26 S&P 500 sample in its fixed order (the first 400 finish first). ~28 s per release, GPU-bound (Bonsai briefs). Deadline-stopped, resumable. |
| 05:15 - ~06:15 | Bonsai decisions, 3 books x 2 arms (with research / fact sheet only) on exactly the researched releases, 3 in flight. |
| ~06:15 | `horizons_eval.py` per book; master portfolio per book and arm (2025-01 to 2026-09). |
| after | Code review for bugs, tests, docs, commit, push, shut down. |

**Pass rule (research helps a book):** on the same releases, the book's own-horizon monthly rank IC is higher with
research than without, AND the with-research IC's 95% interval is above zero. A book
that passes gets research in every later block; a book that fails uses the fact sheet only.

## Block 2 — Sat 26 to Sun 27: out-of-sample year (~15 h GPU, two nights)

- Run the same research + decisions on the 1,995 S&P 500 releases of 2024 (all of them, not a sample).
  Caveat recorded up front: 2024 is inside Bonsai's training data (a pass there is weak, a fail is informative).
- Pass rule: a book that passed in Block 1 keeps a positive own-horizon IC in 2024 (point estimate > 0).
- Also: run the reader on the other ~2,470 2025-26 releases (fast profile, ~1.3 h) so the fact-sheet arm covers
  every release, for the portfolio in Block 3.

## Block 3 — Mon 28: three-book portfolio (CPU)

- Stage B per book: walk-forward logistic of EPS change, Bonsai P(BUY) (with research where the book passed),
  momentum, guidance; refit monthly on outcomes known before the decision (`combine_scores.py`, generalised to
  `--horizon`).
- Master agent with three books sharing the stock sleeve (60% cap), sized by calibrated edge per book, plus the
  crypto sleeve and SPY core; costs 5 bps + 5 bps slippage; compare to SPY and SPY + crypto sleeve.
- Pass rule: Sharpe above SPY + crypto sleeve over 2024-03 to 2026-09 AND positive in both 2025 and 2026.

## Block 4 — Mon 28 evening: forward tests (manual)

- Allocator forward run #2 (`scripts/forward_allocator.py`): fills the 2026-09-25 orders at Monday's open.
- Build `scripts/forward_stocks.py`: once a week, by hand — new 8-K earnings releases since the last run → reader
  → Jan research → Bonsai per book → master agent → paper orders filled at the next open, ledger committed to git.
  Books that failed their pass rule stay in the ledger as "shadow" (tracked, not sized).

## Block 5 — Tue 29 to Wed 30: robustness and bugs

- Cost sensitivity (0 / 10 / 25 bps), turnover, worst month, per-sector concentration.
- Ablations: research without news, without the press release; brief by Bonsai vs Jan (Jan briefs: 0.83 verified
  facts vs Bonsai's 5.75 on the same 12 releases, 2026-09-25).
- Code review of this week's code; property tests for the timing rules (no fill before a decision, no tool result
  after the as-of time).

## Block 6 — Thu 1 to Fri 2: weekly forward run, write-up

- Forward runs for the allocator and the stock books; update `docs/EXECUTIVE_SUMMARY.md` and `docs/PLAN_V2.md`;
  push.

## Operating rules

- One GPU job at a time (GPU lock); vLLM at 40% of the card next to Bonsai with 3 slots x 8k context (9.4 of 12 GB;
  45% and 50% pushed part of Bonsai onto the CPU).
- vLLM kernel compiles are capped at 2 jobs (`MAX_JOBS`): unlimited parallel nvcc ran the 29 GB of RAM out on
  2026-09-25. A RAM watchdog stops vLLM below 3 GB available.
- Long runs hold `systemd-inhibit` against idle sleep; they never schedule themselves.

## Block 1 result (2026-09-26 06:24): research does not help any book

1,007 of the 1,180 sampled 2025-26 releases researched (998 with verified facts; the deadline stopped the rest).
Bonsai decided each book twice on the same releases (results/events/horizons_eval.txt):

| Book | With Jan research: IC [95% CI], top-bottom | Fact sheet only: IC [95% CI], top-bottom |
|---|---|---|
| Quick money (5 d), 1,004 releases | +0.087 [-0.001, +0.183], +0.98% | **+0.107 [+0.040, +0.179], +1.85%** |
| Mid term (20 d), 993 | +0.057 [-0.036, +0.159], -0.19% | **+0.091 [+0.016, +0.168], +1.86%** |
| Long term (120 d), 706 | -0.109 [-0.178, -0.039], -6.15% | -0.082 [-0.194, +0.025], -1.41% |

- Pass rule (research IC higher AND its interval above zero): **fails for all three books.** Research made every book
  a little worse; the extra web facts seem to add noise to Bonsai's call.
- The **fact-sheet-only** Bonsai signal is significant for quick money and mid term on this larger sample (1,000
  releases, 16 months) - the best evidence for Bonsai so far, but 2024 already failed for the 20-day version (+0.020),
  so it still needs its out-of-sample year.
- Long term: Bonsai's BUYs did *worse* than its PASSes over 120 days (with research significantly so, 11 months).
- Master portfolio per book (SPY core + crypto sleeve + that book's picks, 2025-01 to 2026-09, SPY 17.58% / Sharpe
  1.06): quick 16.93% (0.97) with research, 17.73% (1.01) without; mid 15.60% (0.91) / 14.82% (0.88); long 17.75%
  (1.01) both (almost no trades: few 120-day outcomes were known in time to calibrate). None beats SPY over this
  window (the sleeve's own 2025-26 share of that is not measured yet: Block 3).

**Plan changes for the rest of the week:** Block 2 drops web research and instead tests the fact-sheet Bonsai quick
and mid books on all 1,995 2024 releases (fact sheets only, ~1 h GPU instead of ~15 h); the long book is tested as a
contrarian signal (pre-registered: BUY log-odds IC < 0 in 2024). Blocks 3-6 unchanged.
