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

## Block 2 result (2026-09-26): 2024, fact-sheet books only (1,983 releases)

| Book | 2025-26 (found) | 2024 | Pre-registered rule | Verdict |
|---|---|---|---|---|
| Quick money (5 d) | +0.107 [+0.040, +0.179] | +0.024 [-0.028, +0.074] | IC > 0 | passes (weakly; ~1/4 the size, not significant) |
| Mid term (20 d) | +0.091 [+0.016, +0.168] | +0.020 [-0.053, +0.079] | IC > 0 | passes (weakly; not significant) |
| Long term (120 d) | -0.082 | +0.050 [-0.022, +0.114] | IC < 0 (contrarian) | **fails**: the sign flipped |

## Block 3 result (2026-09-26): three-book portfolio fails; the quick-money book alone is the lead

Combined score per book (walk-forward logistic of EPS change, Bonsai, momentum, guidance), 2024-03 to 2026-08:
quick +0.073 [+0.035, +0.119] (2,684 releases), mid +0.033 [-0.032, +0.097], long -0.006 [-0.076, +0.062].

Master portfolio, 2024-03-01 to 2026-09-24, 10 bps per trade (`scripts/block3_run.sh`, results/master_full_*):

| Portfolio | CAGR | Sharpe | Max DD |
|---|---|---|---|
| SPY + crypto sleeve, no stocks | 20.40% | 1.18 | 21.1% |
| Three books, quarter Kelly | 20.06% | 1.17 | 20.9% |
| Three books, equal-weight top fifth | 17.10% | 1.04 | 23.9% |
| **Quick-money book alone, equal-weight top fifth** | **22.46%** | **1.29** | 21.7% |
| Same, Bonsai scores shuffled at random (50 seeds) | mean 18.85% | mean 1.12 | |
| SPY | 18.14% | 1.16 | 18.4% |

- Pre-registered rule (three books: Sharpe above SPY + crypto sleeve, positive in 2025 and 2026): **fails**.
- The quick-money book alone beat all 50 random shuffles of its own scores (p = 0.02, `scripts/shuffle_control.py`,
  results/shuffle_control_decide_combined_h5.json). Random picks lose ~1.5 points a year against no stocks; Bonsai's
  picks gain ~2.
- Caveats: this test was chosen after the three-book test failed (with three books looked at, p ~ 0.06 after that
  correction); one 2.5-year path; the 2024 part of Bonsai's scores is inside its training data. It is a lead for a
  forward test, not a result.

**Next (Block 4, Monday):** forward paper test of the quick-money book alone (weekly, by hand: new releases -> reader
-> fact sheet -> Bonsai 5-day -> combined score -> top fifth -> fills at the next open), next to the allocator.
Pre-registered gate after 12 weeks: the book's return beats the same-period shuffle median and the no-stock allocator.

## Research fix (2026-09-26): targeted analyst-expectations research, rule set before results

Block 1 research added noise: 36% of its facts came from Wikipedia, 31% repeated the fact sheet, only 3% were
estimates or guidance. What moves a stock after earnings is the surprise against expectations, so research now goes
straight for them: new tool `analyst_targets_as_of` reads the last archived Yahoo quote page in the 60 days before
the SEC acceptance time and code-parses the analyst targets (low/average/high), the target raises/cuts listed there
and the company's upgrade/downgrade headlines (no LLM; point-in-time checked like every as-of tool).
`scripts/fetch_analyst_targets.py` collects it for the 1,180-release 2025-26 sample, then 2024;
`scripts/analyst_eval.py` scores it.

Features: target upside (vs the last close before entry), dispersion, net target actions, net headlines.
**Pass rule (a feature helps a book):** own-horizon monthly rank IC in the pre-registered direction (positive;
dispersion negative) with its 95% interval above zero in 2025-26, AND a positive point estimate in 2024. A feature
that passes goes into the fact sheet, the research prefetch (replacing Wikipedia) and the combined score; one that
fails is dropped. Four features x three books = 12 tests, so a single pass at the edge of its interval is a lead only.

## Fact-sheet fix (2026-09-26): revenue cross-checked against SEC filings

The reader misread ~8% of press-release revenues (thousands or billions read as millions, a segment's figure, a sign
error). `build_features.py check_revenue` now checks each figure against the company's last SEC-filed quarterly
revenue: kept if within 0.5x-2x, rescaled if x1000 or /1000 fits, otherwise dropped with a note on the fact sheet
(true quarter-to-quarter swings beyond 2x: 2.3% of SEC filings). Impossible year-on-year revenue changes: 51 -> 7
(2025-26) and 67 -> 4 (2024); 297 fact sheets changed. Adopted regardless of the scores (it removes wrong numbers).
Bonsai re-decided on the cleaned sheets (`scripts/secchk_run.sh`, results/events/secchk_eval.txt), same releases:

| Book | 2025-26 old -> cleaned | 2024 old -> cleaned | BUY/PASS flips on changed sheets |
|---|---|---|---|
| Quick money (5 d) | +0.092 -> **+0.103 [+0.051, +0.158]** | +0.024 -> +0.027 | 104 of 290 |
| Mid term (20 d) | +0.091 -> +0.071 [+0.002, +0.142] | +0.020 -> +0.027 | 98 of 279 |
| Long term (120 d) | -0.050 -> -0.043 | +0.050 -> +0.064 [+0.000, +0.120] | 68 of 249 |

Combined score (`combine_scores.py`, now on the cleaned sheets and decisions; old outputs in
results/events/pre_secchk): quick +0.073 -> +0.082 [+0.041, +0.131], long -0.005 -> -0.000; mid unchanged (it
uses the original XBRL decisions). Quick-money book alone, top fifth, 10 bps: 22.46% / Sharpe 1.29 -> 21.42% / 1.24
(one 2.5-year path; the IC moved up while this moved down). It now beats 47 of 50 random shuffles of its own
scores (p = 0.06, was 0.02; shuffle mean 18.85%): still a lead for the forward test, weaker than before.

Not adopted: rescaling EPS stated in cents by a P/E-below-1 rule. It fixed ~7 releases a year (WEC "76" cents) but
also shrank real one-off losses (Centene's -$13.50 impairment quarter) and real high-EPS stocks, and the reader keeps
no quote to tell them apart.

## More horizons (2026-09-26): six books, rule set before results

Books by holding period (trading days): 1 week (5), 1 month (20), 3 months (63, new), 6 months (120, the old long
book), 1 year (252, new), 2 years (504, new). Bonsai decides each on the SEC-cross-checked fact sheets (prompt: "beat
their sector over the next N trading days"), 2025-26 sample and all of 2024 (`scripts/horizons6_run.sh`, scored by
`scripts/horizons6_eval.py`). Prices end 2026-09-25, so a 1-year outcome exists only for entries up to ~2025-09 and a
2-year outcome only for 2024 entries up to ~2024-09 (none in 2025-26).

**Pass rule (a new book is worth trading):** own-horizon monthly rank IC with its 95% interval above zero in 2025-26
AND a positive point estimate in 2024 (3 months, 1 year). The 2-year book has only 2024 (inside Bonsai's training
data): it can at most be a lead, and only if its 2024 interval is above zero. Caveat for 1 and 2 years: neighbouring
months' outcome windows overlap almost entirely, so the month bootstrap interval is too narrow; a pass there needs a
forward test before any money-like sizing. A walk-forward combined score and portfolio are not possible for 1 and 2
years inside this price window (too few outcomes known before the decisions).

## Research fix result (2026-09-26): analyst targets fail

834 of the 1,180 sampled 2025-26 releases had an archived Yahoo quote page in the 60 days before them
(results/events/analyst_eval_2025.txt). **All 12 tests fail the pre-registered rule** (none has its interval above
zero in the set direction). Quick money: every feature's IC is negative (-0.013 to -0.055). The only intervals that
exclude zero point the *wrong* way: at 120 days, high target dispersion did better (negated IC -0.105
[-0.223, -0.009]) and net target raises did worse (-0.090, only 4 months). Found after the fact, so leads at most.
Per the rule the features are dropped: not in the fact sheet, the combined score or the research prefetch, and the
2024 fetch is not run.

## Research v3 (2026-09-26): web research back on, faster and aimed at the surprise; rule set before results

Measured on v2's 1,007 releases (167 s each, 6 in flight, ~28 s per release overall):
- the press release itself was blocked in 693 of 1,285 reads: EDGAR's Eastern-time stamp plus the +5 h safety margin
  put it an hour "after" its own decision time in summer. Fixed: the filing being decided on is readable
  (`ToolGateway.own_filing`); every other source keeps the margin.
- news lookups: 647 of 2,030 timed out at 25 s (a third of the calls, nothing returned). Cap now 15 s.
- Jan's second round: mostly archived pages, 64 of 468 succeeded. Now 1 round after the prefetch.
- Bonsai wrote briefs one at a time; now 3 in flight (the model is 4 GB; 3 Ollama slots).
- Wikipedia (36% of v2's facts) is replaced by the company's previous earnings release (guidance it gave; 1,174 of
  1,180 releases have one). The brief now asks for guidance met/beat/missed and one-offs first, and not to repeat
  the fact sheet's numbers.

`scripts/research_v3_run.sh`: research on the 1,180-release sample, then Bonsai's books on the researched fact sheets
vs the same releases' cleaned fact sheets alone (`horizons6_eval.py --with-tag jan_research_v3`).
**Pass rule (same as Block 1, per book):** own-horizon IC with research higher than without on the same releases AND
the with-research 95% interval above zero. Books: 1 week, 1 month, 3 months, 6 months, 1 year (2 years has no
2025-26 outcomes). A book that passes gets research in the forward test; one that fails stays fact-sheet only.
