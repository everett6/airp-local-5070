# Plan v2: targeting 60% a year (paper money)

Written 2026-09-27, 00:30. This replaces the phase list in PLAN_60.md, whose results so far are kept there. Paper
trading only, free data, no real money at any step.

## What tonight's tests changed

| Tested tonight | Result | Lesson |
|---|---|---|
| Trend following, momentum, FX carry | Fail | Textbook sleeves add nothing to SPY + crypto at equal risk |
| Stat-arb, arm A | Fail | Weekly full turnover costs about 20% a year at 10 bps |
| Stat-arb, arm B (8-K filter) | Fail | No stronger edge |
| Long-short 1-week Bonsai book | Fail | Some edge before costs, but the cost wall wins |
| Drawdown brakes | **Pass** | Cut the worst drop from 33.8% to 27.2% at no equal-risk cost |
| Value + quality | Running | Low turnover, so it avoids the cost wall |

**Lesson 1: the cost wall.** Anything replaced weekly pays about 4 × cost × 52 a year. That is 20% a year at 10 bps,
before any edge. **Every new signal in this plan must hold 20+ days, or trade only its most extreme scores.**

**Lesson 2: what is left.** The only book with a pre-cost *and* post-cost edge is the one already built:

- SPY core + crypto sleeve (B0), Sharpe 1.05 over 2018–26.
- Plus Bonsai's long-only 1-week satellite. The combined book's Sharpe is 1.24, but that comes from only about 2.5
  years.

## What 60% needs from that book

Risk-free rate 4%; figures from `scripts/target66.py`.

| If the combined book's true Sharpe is | Volatility needed for 60% | Leverage on a ~21% vol book | Typical worst drop (10-year median) |
|---|---|---|---|
| 1.24 (tonight's figure) | 42% | about 2× | ~50% |
| 1.05 (B0 alone) | 56% | about 2.7× | ~64% |
| 0.87 (70% of 1.24, the usual haircut) | **cannot reach 60% at any leverage** (best possible ≈ 52%) | — | — |

**So the whole plan comes down to one question:** is the combined Sharpe really about 1.25 or higher? Everything
below either raises that number with low-turnover signals, or measures it honestly before any leverage is used.

## The plan

### Stage 1 (weeks 1–2): raise the Sharpe with low-turnover signals

Every item gets its own spec before its run, and passes only under the adding rule (equal-risk CAGR up; the 90% CI of
the Sharpe difference above 0).

1. **Value + quality sleeve.** Running now. Monthly rebalance, turnover about 3× a year.
2. **The 20-day Bonsai book as a satellite.**
   - 4× less turnover than the 1-week book.
   - The 1-month IC is +0.094 in 2025-26 and +0.027 in 2024.
   - Uses the same master-portfolio code with `--horizon 20`, at 10 and 25 bps.
3. **Research v3 briefs** (tonight's run). If a book passes its rule, researched fact sheets feed item 2.
4. **Extreme-only breadth:** S&P 400/600 earnings releases, trading only |z| > 1.5 of Bonsai's score (about 13% of
   releases).
   - Uses Bonsai-lite triage to save GPU, re-tested on this universe first.
   - A GPU job of several nights.
5. **Crypto sleeve cap: needs your OK first.**
   - Crypto has the highest-return, lowest-correlation asset in the book. Raising the cap from 20% to 35% adds return
     *without borrowing*, but adds crypto's drawdowns.
   - Test: cap 35% vs 20%, 2018–26, under the adding rule. It is one trial, not a sweep.
   - **User OK'd the test (2026-09-27, 00:35).**
   - **Spec (fixed before the run; `scripts/crypto_cap_test.py`):**
     - B0 is run twice with the same code, at `crypto_cap` 0.20 and at 0.35, over 2018-01-02 → 2026-09-24.
     - **Pass (adding rule):**
       - Cap 35%, scaled to the 20%-cap book's realized volatility, has the higher CAGR.
       - The 90% block-bootstrap CI of Sharpe(35%) − Sharpe(20%) lies above 0.
     - Also reported: raw CAGR, volatility, max drawdown and worst year for both. Then the same comparison with the
       brakes on.
     - A pass changes the default cap **only after you confirm**, because the higher cap means deeper crypto drawdowns.
   - **Result (2026-09-27, 00:40): FAIL on the pre-registered rule, but a close call.** Output:
     `results/crypto_cap_test.json`.
     - Average crypto weight: 8.8% at the 20% cap, 15.5% at the 35% cap. The trend filter keeps crypto out much of
       the time.

     | Book | CAGR | Vol | Sharpe | Max DD | Worst year |
     |---|---|---|---|---|---|
     | 20% cap | 21.6% | 20.6% | 1.05 | 33.8% | −23.0% |
     | 35% cap | 26.8% | 24.4% | 1.10 | 34.4% | −26.9% |
     | 35% cap, scaled to the 20% cap's volatility | 22.7% | 20.6% | 1.10 | 29.8% | — |

     - Sharpe difference: +0.04, 90% CI −0.11 to +0.16. **The CI includes 0, so FAIL.** With the brakes on: +0.06,
       CI −0.13 to +0.22, also FAIL.
     - **Reading:**
       - The 35% cap gives about 5 points more return a year for about 4 points more volatility, at almost the same
         worst drop.
       - Per unit of risk it is at least as good as the 20% cap, but not *provably* better. So the default stays at
         20%.
     - **Lead for Stage 4:** when the plan does take more risk, raising the crypto cap is a candidate way to do it,
       instead of borrowing. It gets its own pre-registered rule then: raising volatility via the cap vs via futures
       leverage, compared at equal volatility.

### Stage 2 (weeks 2–4): build it to trade

1. **Brakes** (passed): −10% from peak cuts risk to ⅔, −20% to ½.
2. **Spike check** (analyze before responding): Jan + Bonsai explain any 4-sigma move before the book reacts. Hard
   limits never wait for the analysis.
3. **Leverage through futures, not margin.** Micro E-mini S&P (MES) for the SPY core, and CME micro bitcoin for crypto,
   both in the simulator. Financing is built into futures prices, which is cheaper than retail margin at about
   rf + 1.5%.
4. **PC-off fallback.** On days the PC is off, Bonsai-lite (CPU, milliseconds) makes the event decisions. Each such day
   is flagged, and scored separately.

### Stage 3 (months 1–6): forward paper test at 1.0×

- The combined book at about 20% volatility, with the brakes on.
- Monthly report: realized Sharpe with its CI, slippage against the backtest's fills, failure-log review.

### Stage 4 (month 6 on): leverage only as the evidence allows

| Forward Sharpe, lower 80% bound | Volatility target | Expected a year | Typical worst drop |
|---|---|---|---|
| < 0.6 | 20% (1.0×) | 12–18% | ~25% |
| 0.6–0.9 | 25–30% | 20–30% | ~35% |
| 0.9–1.2 | 35% | 35–45% | ~40% |
| **≥ 1.25 for 12+ months** | **42%** | **~60%** | **~45–50%** (brakes on) |

- Re-checked monthly. When the bound falls, leverage comes down the same month.
- A −25% drop from peak halves the target until a new high.

## Odds, stated plainly

- **60% needs** the combined book's *true* Sharpe to be about 1.25 or higher, and you to accept drops of about half
  the account.
- **The history is against it:**
  - Most backtested Sharpes shrink 50–75% in live trading.
  - Every textbook sleeve tested tonight failed.
- **The realistic central outcome** is a forward Sharpe of about 0.7–1.0, which gives **20–35% a year** at 25–35%
  volatility. That is still well above SPY's long-run 10%.
- **60% is reachable only in the top row of the Stage 4 table,** and only the forward test can tell us whether we are
  in it. The plan is built so that if we are not, it lands on the best return the evidence supports, instead of
  borrowing into a blow-up.

## Decisions needed from you

1. **Crypto cap test** (Stage 1 item 5): may the test consider a 35% crypto cap? The current rule is 20%.
2. **Drawdown you would sit through at the 60% setting:** about 45–50%. If that is too much, the plan tops out at the
   row you can live with.

## Timeline: compressed to 3 months (user request, 2026-09-27)

**What can be compressed:** building and testing. It runs in parallel, on CPU during the day and on the GPU at night.

**What cannot:** how fast forward evidence builds up. After 3 months the Sharpe's standard error is still about ±2,
so 3 months of paper results cannot prove a Sharpe of 1.25.

**How the compressed plan copes:**

1. **Levered "shadow" books from day 1.** Because it is paper money, the forward test runs the same book at 1.0×,
   1.5× and 2.0× in parallel, with no extra risk. The Sharpe does not depend on leverage, so all three measure the
   same thing, and on day 90 you see what each setting would really have done.
2. **The day-90 decision rests on three pieces of evidence together:**
   - the long backtests, several years each;
   - the pre-registered holdouts;
   - the 3-month forward test, which confirms the *implementation*: slippage and fills match the backtest, no rule
     broken, forward IC within 2 standard errors of the backtest.
3. **The day-90 leverage choice** uses the Stage 4 table, applied to the backtest Sharpe's lower bound after a 30%
   haircut, and capped at **30% volatility**. That stays capped until 6 forward months exist. The 42% (60%) row
   unlocks only after 6+ months, when the forward bound supports it.

| Week | Output |
|---|---|
| 0 (tonight) | Value + quality result; crypto 35% cap test; research v3 scored; push; shutdown (as commanded) |
| 1 | 20-day satellite test; spike check built; futures (MES, micro BTC) in the simulator; Bonsai-lite PC-off fallback |
| 1–3 (GPU nights) | Extreme-only S&P 400/600 breadth test |
| 2 | Combined book frozen from what passed; forward test starts with 1.0× / 1.5× / 2.0× shadow books, brakes on |
| 4, 8 | Monthly forward reports: Sharpe with its CI, slippage, failure log |
| 12 (day 90) | Implementation verdict + leverage choice (≤ 30% volatility), then continue |
| 26 | 60% row can unlock (6 forward months, lower bound ≥ 1.25) |

## This week, day by day (user request, 2026-09-27: "for this week every day")

**How the week runs:**

- CPU work happens in the day. GPU jobs run at night: Jan/Bonsai have the GPU to themselves, one job at a time.
- Nothing runs as a service. Each day starts when you open a session.
- Every test has its spec committed before it runs, and every day ends with a push.

**Drawdown decision.** The user answered "no" to the 45–50% drawdown question (2026-09-27). So the top 60% row of the
Stage 4 table stays **locked**. The book tops out at the highest row whose typical worst drop the user accepts; the
limit is still to be given. Until then the cap is 30% volatility, which means a typical worst drop of about 35%.

### Day-by-day schedule

**Sun 27 Sep**

- **Night (running now):** research v3 briefs, then decisions (~02:40).
- **Day:**
  - Score research v3.
  - Value + quality result.
  - Push, then shutdown (as commanded).
- **Output:** research v3 verdict; value + quality verdict.

**Mon 28**

- **Day:**
  1. 20-day Bonsai satellite test (CPU; its decisions already exist). Spec written first.
  2. Pre-register the PC-off fallback rule (Bonsai-lite on days the GPU is off).
  3. Build the spike check: list every historical trigger in 2024–26.
- **Night:** Jan + Bonsai cause briefs for the historical spike triggers.
- **Output:** 20-day verdict; trigger list; briefs.

**Tue 29**

- **Day:**
  1. Spike check score: arm A (mechanical) vs arm B (analyze first).
  2. Futures in the simulator (MES, micro BTC): margin, daily settlement, roll; with tests.
  3. Build the S&P 400/600 earnings-release set for 2025-26 (EDGAR, free).
- **Night:** fact sheets for the breadth set, plus the Bonsai-lite triage re-test.
- **Output:** spike check verdict; futures sim.

**Wed 30**

- **Day:** check triage on the breadth set (pre-registered rule).
- **Night:** Bonsai 1-week decisions on the breadth set, trading extreme scores only.
- **Output:** breadth decisions.

**Thu 1 Oct**

- **Day:**
  1. Breadth test verdict.
  2. Assemble the combined book from everything that passed, using the adding rule.
  3. Shadow books at 1.0× / 1.5× / 2.0×.
- **Night:** a spare GPU night, in case a run failed.
- **Output:** the combined book frozen, with its spec.

**Fri 2**

- **Day:**
  1. Forward-test runner: a manual script with PC-off tolerance. A missed day is marked missed, never backfilled.
  2. End-to-end dry run on the last 20 days.
  3. Failure log and weekly review script.
- **Output:** the runner is ready.

**Sat 3**

- **Day:**
  1. Weekly review (failures, what passed).
  2. Update the docs and README; push.
  3. The forward test's first orders go in at the Mon 5 Oct open.
- **Output:** the week's report.

**Honest note.** Most tests so far have failed, and some of this week's will too. The plan does not bend its rules to
make the week look good. Whatever passes by Thursday becomes the combined book. If nothing new passes, the book is the
existing SPY + crypto core + the 1-week Bonsai satellite + brakes.

## Mon 28 item 1: 20-day Bonsai satellite test (spec fixed before the run, committed 2026-09-27 00:40)

- **Book:** Bonsai-27B's 20-day decisions on S&P 500 earnings releases.
  - 2024: `decide_bonsai-27b_latest_factsheet2024_secchk.jsonl`.
  - 2025-26: `decide_bonsai-27b_latest_factsheet_secchk.jsonl`.
  - Both are merged into one book with one calibrator (min 300 known outcomes before the first pick).
  - Events file: `events_sp500_2024_2026.csv`.
- **Portfolio:** `master_portfolio.full_run`, Kelly sizing, crypto cap 20%, 2024-01-02 .. 2026-09-24.
- **B0:** the same run with no stocks (SPY core + crypto sleeve). **B1:** the same run with the satellite.
- **Pass (the sleeve adoption rule), at 10 bps:**
  - B1, scaled to B0's realized vol, has the higher CAGR, AND
  - the 90% CI of Sharpe(B1) − Sharpe(B0) is above 0 (63-day circular block bootstrap, 2000 draws).
- **25 bps:** reported, not part of the verdict.
- **Registry:** one trial is registered.

### Mon 28 item 1 result (2026-09-27 00:42): FAIL

| 2024-01-02 .. 2026-09-24 | B0 (SPY + crypto) | B1 (+ 20-day satellite) | B1 at B0's vol |
|---|---|---|---|
| 10 bps: CAGR / vol / Sharpe / max DD | 23.6% / 16.9% / 1.34 / 21.1% | 23.4% / 16.6% / 1.35 / 21.4% | 23.9% / 16.9% / 1.35 / 21.8% |
| 25 bps | 21.6% / 16.9% / 1.24 / 21.6% | 20.7% / 16.6% / 1.22 / 21.9% | 21.1% / 16.9% / 1.22 / 22.3% |

- At 10 bps the vol-matched CAGR is 0.3 points higher, but the Sharpe difference is +0.01 with a 90% CI of [−0.08, +0.13].
- The CI includes 0, so the satellite is **not added**.
- At 25 bps it costs 0.5 points.
- Stocks were held on 67% of days, across 3,175 releases.
- **Reading:** the 20-day picks are about as good as the SPY they replace. After costs they add nothing measurable.
- Stock picking stays in the forward test as a shadow book at 0 weight; it is not dropped.
- Result: `backend/results/satellite20_test.json`.

## Mon 28 item 2: PC-off fallback rule (pre-registered 2026-09-27 00:42, before any forward data)

- **When it applies:** on a forward-test day, an earnings release whose decision is not written by Bonsai-27B by the next open (PC off, GPU busy or a crash).
- **What happens:** Bonsai-lite (`scripts/bonsai_lite.py`, the ridge trained on every Bonsai decision known before that day) scores the release on CPU.
- **Flags:** the decision is flagged `source=lite` and scored as its own book, next to the Bonsai book. It is never mixed into Bonsai's IC.
- **Backfill:** never. If the PC comes back later, Bonsai does **not** re-decide a release that lite already traded. A missed day with no lite run either is marked missed.
- **Promotion rule, checked at the end of the forward test:**
  - Lite becomes the default only if its forward IC is within 0.03 of Bonsai's on the same releases, AND its 90% CI is above 0.
  - Otherwise it stays a fallback only.
- **The weight is fixed in advance:** lite picks get half of Bonsai's Kelly weight. It was the weaker arm in the back test (IC 0.086 vs 0.103).

## Optimization research (2026-09-27 00:42, user request: "do research for the optomizations")

### Portfolio optimizations

- **Volatility targeting** ([Moreira & Muir](https://www.nber.org/system/files/working_papers/w22208/w22208.pdf)):
  - Cutting exposure when recent volatility is high raised Sharpe ratios for the market and for most factors.
  - Counter-evidence: [Cederburg et al.](https://www.sciencedirect.com/science/article/abs/pii/S0304405X2030132X) tested 103 strategies and found no systematic gain once the approach is implementable.
  - For crypto, [volatility management mainly cuts crashes](https://link.springer.com/article/10.1007/s11408-025-00474-9).
  - Our book is SPY plus up to 20% crypto, whose volatility clusters, so it is worth one pre-registered test (below).
- **No-trade bands** ([NBIM](https://www.nbim.no/contentassets/8cb41f89dce345f5a6a295238f7872fb/no-trade-band-rebalancing-rules-expected-returns-and-transaction-costs.pdf); [Leland](https://arxiv.org/pdf/1203.4156)):
  - With proportional costs, it is best to trade only when a weight leaves a band, and then only to the band's edge.
  - Studies cut costs by about 50%.
  - Our costs at 10 bps are already small for B0, but they grow with futures rolls and leverage.
  - Build it into the Stage 2 executor. It is a pure cost saving, so no signal test is needed; the executor's tests check that fills stay inside the band.

### Pipeline optimizations

- **Bonsai briefs** run at 9.3 s each on Ollama with 3 parallel slots.
  - [Benchmarks](https://particula.tech/blog/ollama-vs-vllm-comparison) show vLLM up to about 16–19× Ollama's throughput at high concurrency.
  - **But our own measurement (WEEK_PLAN, 26 Sep) rules that out for Bonsai:** 3, 6 and 8 in flight gave 9.9, 9.5 and 9.6 s per brief.
    - Bonsai is a hybrid linear-attention model at 1 bit. Its batches don't speed up, and llama.cpp can't reuse a partial prompt.
    - Stock vLLM has no 1-bit Q1_0 kernels for it, so moving Bonsai to vLLM is not a real option.
  - **The one real speed-up is PrismML's llama.cpp fork**, which has Q1_0 kernels. It needs a download and your OK; not done tonight.
  - Jan is already on vLLM (FP4) and is not the bottleneck.
- **Spike cause briefs** (88 triggers): at about 12 s each they take about 18 min. No change needed.

## Optimization 1: volatility target on B0 (spec fixed before the run, committed 2026-09-27 00:42)

- **Rule:** B0's daily returns (2018-01-02 .. 2026-09-24) are scaled by exposure e_d = min(1.5, 20% / σ̂_d).
  - σ̂_d is the annualized 20-day realized vol of B0, up to the close of d−1.
  - **Band:** e only changes when the new value is more than 0.10 away from the current one.
  - **Cost:** 10 bps × |Δe|. Leverage above 1 pays financing of rf + 1.5% per year on the borrowed part.
  - rf is the 3-month T-bill, FRED DTB3.
- **Pass (the adoption rule):**
  - scaled to B0's realized vol, the managed book has the higher CAGR, AND
  - the 90% CI of the Sharpe difference is above 0 (63-day block bootstrap).
- **Also reported, not in the verdict:** the same comparison with the drawdown brakes applied on top.

### Optimization 1 result (2026-09-27 00:44): FAIL

| 2018–26 | CAGR | vol | Sharpe | max DD |
|---|---|---|---|---|
| B0 | 21.6% | 20.6% | 1.05 | 33.8% |
| vol-targeted | 22.8% | 20.3% | 1.11 | 30.7% |
| vol-targeted at B0's vol | 23.1% | 20.6% | 1.11 | 31.1% |

- The Sharpe difference is +0.06, with a 90% CI of [−0.14, +0.25]. With the brakes it is +0.04 [−0.16, +0.24].
- The direction is right: +1.5 points of CAGR and 3 points less drawdown. But the gain is inside the noise, which matches Cederburg et al.
- Exposure averaged 1.17×, and was above 1 on 71% of days.
- **Not adopted as a rule.** It goes to Stage 4 as a lead: if the forward test agrees, it combines with the leverage table.

## Tue 29 item 1: spike check score (spec fixed before any cause label exists, 2026-09-27 01:06)

- **Inputs:**
  - The 88 triggers in `results/spike_triggers.csv`, each with its 5-day forward return from the next open (`fwd5`).
  - Bonsai's code-checked labels in `results/spike_causes.csv`.
- **Arm A (mechanical):** no response. The position follows the book's normal rules.
- **Arm B (analyze first), with fixed rules per label:**
  - unexplained: halve the position at the next open, restore after 5 days.
  - earnings, company_news, sector, macro: no change. Macro defers to the volatility target, which is not adopted, so it is no change too.
- **Per-trigger difference B − A** (per unit of the position):
  - unexplained: −0.5 × fwd5 − 0.5 × 2 × 10 bps.
  - every other label: 0.
- **Pass:**
  - the mean of B − A over all triggers has a 95% CI above 0 (bootstrap clustered by trigger day, 2000 draws), AND
  - at least 20 triggers are labeled unexplained. With fewer, the verdict is "untestable", not a pass.
- **Also reported, not in the verdict:** mean fwd5 per label, and the share of labels the code overruled.

## Tue 29 item 2: futures in the simulator (done 2026-09-27 01:07)

- **Code:** `backend/app/portfolio/futures.py`, with 8 tests in `tests/test_futures.py`.
- **Pricing:** synthetic contracts priced by cost of carry from free spot data.
- **Mechanics:**
  - daily settlement to cash; collateral earns the T-bill rate;
  - margin calls cut the position to the initial margin;
  - a run ends at 0 equity;
  - quarterly (MES) and monthly (micro BTC) rolls, with costs on both legs.
- **Sanity run, 2018-01 .. 2026-09, rebalanced weekly:**

| Book | CAGR | vol | Sharpe | max DD |
|---|---|---|---|---|
| SPY price only | 14.5% | 19.0% | 0.81 | 33.7% |
| MES 1.0× | 16.0% | 19.0% | 0.88 | 33.7% |
| MES 1.5× | 21.8% | 28.6% | 0.83 | 47.2% |
| MES 2.0× | 26.9% | 38.4% | 0.81 | 58.6% |

- MES 1× matches SPY's total return (price plus about 1.3% of dividends). 35 rolls; no margin calls, even at 2×.
- **Leverage keeps the Sharpe, not the drawdown.** 2× SPY had a 59% drawdown. This is why the 60% row stays locked
  behind the drawdown limit.
- **Micro BTC 1×:** 14.7%/yr vs 22.0% for spot.
  - The gap is the assumed 5%/yr basis plus about 1%/yr of monthly roll costs.
  - **Levering the crypto sleeve through CME futures costs about 6–7 points a year.** Stage 4 must price that in.
  - The 5% basis is an assumption. Real CME basis has ranged from below 0 to above 15%/yr.

## Breadth test: S&P 400/600, 1-week book, extremes only (spec fixed before any breadth decision, committed 2026-09-27 01:12)

This replaces Tue night to Thu item 1, run in one night.

- **Releases:** all 6,943 S&P 400/600 earnings releases filed 2025-01-01 .. 2026-09-24.
  - File: `data/events/events_breadth_2025.csv`, shuffled with seed 0.
- **Fact sheets:** built exactly as for the S&P 500 set.
  - The press release is read by the same number reader (qwen3:8b). It only extracts numbers, each quoted word for word and re-checked by code. It does no research.
  - Then SEC XBRL numbers filed before the release, and `build_features.py` (name `breadth_2025`).
- **Decisions:** Bonsai-27B's 1-week (h5) decision on every release with a fact sheet, tag `breadth`.
  - **Change from the plan:** Bonsai decides every release (about 0.7 s each). No Bonsai-lite triage is used.
    Triage would save under 1 h of GPU and would add a second model to the test.
- **Rule 1 (signal):**
  - Outcome: the release's 5-day return vs its sector ETF, from the entry open.
  - Test: the monthly rank IC of Bonsai's log-odds against that outcome (at least 20 releases a month); its 95% CI must be above 0.
- **Rule 2 (extremes in the book):**
  - B1 = B0 + a 1-week satellite holding only releases whose log-odds z-score is above +1.5.
    - z is computed against the book's previous 90 days of releases, earlier ones only, with at least 100.
    - Each pick is 2.5% at the book's usual caps (`master_portfolio --sizing zext`).
  - Compared against B0 with the adding rule at 10 bps, 2025-01-02 .. 2026-09-24.
- **Pass = rule 1 AND rule 2.** 25 bps is reported, not part of the verdict.
- **Registry:** one trial.
- **A pass** makes the breadth book a candidate for the combined book. **A fail** leaves the combined book unchanged.

## Fri 2 items, done early (2026-09-27 01:20–02:00)

1. **Forward runner for the event book:** `backend/scripts/forward_events.py`. It is manual; no service or timer.
   - **Discovery:** new S&P 500 Item 2.02 8-Ks from the SEC.
   - **Fact sheet:** the same reader and code check as the backtests.
   - **Decision:** Bonsai's 1-week log-odds.
     - If the GPU is busy or `--no-gpu` is set, Bonsai-lite decides instead, flagged `source=lite` (the fallback rule).
   - **Ledger:** hash-chained.
     - A decision counts only if it is written before its entry open (09:30 ET, first weekday after the SEC acceptance).
     - Late or impossible decisions are logged as `missed`, never backfilled.
   - **Outcomes:** once 5 trading days have passed, the release's 5-day return vs its sector is appended.
2. **Allocator:** `forward_allocator.py` now also runs a **master+brakes** book (the adopted brakes, on its own equity).
3. **Weekly review:** `backend/scripts/weekly_review.py` covers:
   - the books;
   - shadow 1.0/1.5/2.0× books, levered on paper from the braked book, with borrowing at rf + 1.5%;
   - the event scoreboard;
   - failures: weekdays with no run, and missed decisions with their reasons.
4. **Dry run:** replays of 8–25 Sep 2026 with `--as-of` in a separate folder, using lite (the GPU was busy).
   - There is a deliberate gap on 14–16 Sep, to check that LEN (16 Sep) is logged as missed.
   - The first replay found a bug: the price frame needs SPY as its calendar. It is fixed.

### Forward dry runs (2026-09-27 01:50–02:50)

| Dry run | Schedule | Decided on time | Missed | Why missed |
|---|---|---|---|---|
| 1 (8–25 Sep) | evenings only, with a gap on 14–16 Sep | 4 | 8 | 6 pre-market releases; LEN (the gap, as intended); COST (bug 2) |
| 2 (21–25 Sep) | about 08:45 ET and evenings, both fixes in | 6 | 0 | — |

- **Bug 1:** the price frame needs SPY as its calendar. Fixed.
- **Bug 2, the important one:** `build_features.py` and `decide_events.py` dropped any release whose entry day was not
  yet in the price data, and in live use it never is.
  - Every live release would have been missed.
  - Fixed with `--live`: the fact sheet reads the closes before the entry, as it does in the backtests.
  - The backtests are unchanged.
- **Operational rule:**
  - Run `forward_events.py` twice each weekday: about 08:45 ET for pre-market releases, and in the evening for after-close ones.
  - Pre-market releases are about half of all releases.
- **Still manual:** refreshing the SEC XBRL history (`build_xbrl_eps.py facts`, weekly), so fact sheets see the newest filed quarters.
- Outcomes in dry run 1 were logged for the first 4 decisions: COO, ADBE, CPRT, ORCL. Lite decided them; they are not scored for anything.

### Tue 29 item 1 result: spike check (2026-09-27 03:13): FAIL. The briefs stay; no automatic response.

- **Triggers:** 88 in 2024–26; 84 have a 5-day forward return.
- **Bonsai's labels** (code-checked): 42 unexplained, 34 earnings, 4 macro, 4 company news.
  - Code overruled 7%: 4 "earnings" labels with no release near the day, and 2 "macro" labels without a valid source.
  - Spot checks read correctly: DPZ −13.6% on its earnings day, SPY −2.3% on 24 Jul 2024 (macro), Dover +5.7% on
    an acquisition.
- **Arm B − arm A:** −0.19% per trigger, 95% CI [−0.60%, +0.24%]. Halving unexplained spikes **would have cost money**.
- Mean 5-day return after a spike:
  - unexplained: +0.56%;
  - earnings: −0.43%;
  - company news: −1.19%;
  - macro: +1.32%.
  With 4 to 42 cases per label, none of these differs from 0.
- **Decision:**
  - The spike check stays as **information only**: the cause brief is written and logged, and a person reads it.
  - No rule trades on the label.
  - Hard limits (brakes, caps) are unchanged.
  - Your request to "analyze why before responding" is met by the brief. The test says the book should not respond
    automatically to an unexplained spike.
- **Files:** `results/spike_causes.csv`, `results/spike_score.json`, `results/spike_briefs/`.

## Thu 1 item 2: the combined book for the forward test (frozen 2026-09-27 03:15, before the breadth verdict)

This is built from what passed its pre-registered rule, and nothing else.

| Part | Weight | Why |
|---|---|---|
| SPY core | the rest | the base |
| BTC/ETH trend sleeve | up to 20% | B0 (the 35% cap failed; lead for Stage 4) |
| Drawdown brakes | ⅔ from −10%, ½ from −20% | passed (27 Sep) |
| S&P 400/600 1-week extremes satellite | 2.5% per pick | **only if the breadth test passes tonight**; otherwise 0 |
| S&P 500 1-week Bonsai book | 0 (shadow) | the signal is real (IC +0.10) but has not beaten costs in a book |
| Bonsai-lite fallback | 0 (shadow, own score) | the PC-off rule; scored apart |
| Spike check | information only | the cause brief is logged; no rule trades on it (the test failed) |
| Leverage | 1.0× | shadow books at 1.5× and 2.0× are paper only (`weekly_review.py`); the 30% volatility cap holds until 6 forward months |

- **Not in the book:**
  - web research (v3 failed);
  - the 20-day satellite; the vol target; value + quality;
  - long-short; stat-arb; the trend, momentum and FX sleeves.
- **Frozen:** changes before the 3-month review need a new pre-registered test. After the review, the Stage 4
  table applies.

## Optimization research 2: how sturdy is the crypto trend rule? (2026-09-27 03:16; a check, not a trial)

B0 was re-run with the trend rule's two parameters moved over a grid:
- lookback: 10, 15, 21, 30, 42 and 63 trading days;
- moving average: 50, 75, 100, 150 and 200 days.

Nothing is selected from this grid; the live rule stays at 21 × 100. Script: `scripts/crypto_robustness.py`.

- **A plateau, not a spike.**
  - All 30 cells: Sharpe 0.85–1.07, median 0.96. Every one beats SPY alone (0.81).
  - Only the shortest lookback (10 days) is clearly worse (0.85–0.88).
  - Max drawdown is 34–39% everywhere: the drawdown is SPY's (2020, 2022), not crypto's.
- **The live rule is lucky.** It sits in the 87th percentile of the grid (Sharpe 1.05, CAGR 21.6%).
  - Fair forward expectation for B0: **Sharpe about 0.96, CAGR about 19–20%**, not the 21.6% backtest.
  - With the brakes, subtract about 4 points of raw return. That leaves about 16% before any leverage.
- **For the 30–40% target:** it confirms that the gap has to be closed with risk (leverage or a higher crypto cap),
  not with a better-tuned rule. The shadow 1.5× and 2.0× books are the way to measure that on real data.

## Optimization research 3: trading costs of B0 (2026-09-27 03:40; a check, not a trial)

| Rebalance | Cost per trade | Trades 2018–26 | Costs on $100k | CAGR |
|---|---|---|---|---|
| weekly (live) | 5 bps | 639 | $5,084 | 21.59% |
| weekly (live) | 10 bps | 643 | $10,047 | 21.33% |
| monthly | 5 bps | 196 | $2,387 | 18.88% |
| monthly | 10 bps | 198 | $4,743 | 18.73% |

- Costs take only about 0.25 points a year at 10 bps. A no-trade band can save at most that, so it is a
  nice-to-have for the Stage 2 executor, not a lever.
- **Trading less often hurts a lot:** checking the trend monthly instead of weekly loses about 2.7 points a year,
  because the crypto rule reacts late.
- Weekly stays. Don't slow the book down to save costs.

## Optimization 2: daily trend check (spec fixed before the run, 2026-09-27 03:42)

- **Hypothesis:** from the cost check above, a slower check loses return. Checking the crypto trend daily instead of
  weekly may gain.
- **B1:** B0 with targets decided every trading day instead of every 5th. **B0:** the live weekly book. Both use the
  same code at **10 bps**, 2018-01-02 .. 2026-09-24.
- **Pass (the adding rule):** B1 scaled to B0's vol has the higher CAGR, AND the 90% CI of Sharpe(B1) − Sharpe(B0)
  is above 0 (63-day block bootstrap).
- **Also reported:** trades and costs.
- **Registry:** one trial.

### Optimization 2 result (2026-09-27 03:45): FAIL (lead)

| 10 bps, 2018–26 | CAGR | vol | Sharpe | max DD | trades | costs on $100k |
|---|---|---|---|---|---|---|
| weekly (live) | 21.3% | 20.6% | 1.04 | 33.8% | 643 | $10,047 |
| daily | 22.4% | 20.6% | 1.09 | 34.4% | 2,795 | $21,983 |

- The Sharpe difference is +0.04, with a 90% CI of [−0.05, +0.16]. The CI includes 0, so it is **not adopted**.
- The direction agrees with the monthly result: a faster check earns more even after twice the costs. But one more
  point a year is inside the noise of one 8.7-year path.
- It is a lead for the 3-month review, where the forward allocator's weekly runs can be compared with a daily
  shadow.

### Breadth test result (2026-09-27 07:49): FAIL on both rules

- **Rule 1:** 6,597 S&P 400/600 releases (2025-01 .. 2026-09), 21 months.
  - Bonsai's 1-week IC is **−0.002**, 95% CI [−0.044, +0.035].
  - Top-minus-bottom fifth: −0.27% [−1.46%, +0.80%].
- **Rule 2** (extremes-only satellite vs B0, 2025-01 .. 2026-09):
  - 10 bps: Sharpe 0.89 vs 0.99, a difference of −0.10 [−0.57, +0.31].
  - 25 bps: −0.24.
- **Reading:**
  - Bonsai's 1-week score has an IC of +0.10 on S&P 500 releases in the same months, and about 0 on mid- and
    small-caps.
  - The same model, prompt and fact sheets were used, with more than 5× the releases.
  - A real skill at reading earnings releases should not stop at the S&P 500's edge. Two explanations fit:
    - the model knows large companies far better (it has seen more about them);
    - the S&P 500 result is partly luck.
  - The forward test of the S&P 500 shadow book will tell these apart.
- **The combined book is unchanged** (frozen at 03:15): the breadth satellite weight is 0.
- The fact sheets were read by the same code-checked reader as the S&P 500 set, with similar coverage:
  - 6,597 of 6,943 releases got a fact sheet;
  - 4,595 have an EPS pair, 1,581 of them completed from SEC filings.
- `results/events/breadth_eval.json`, `results/events/breadth_eval.txt`.

## "LLM extracts, code scores" (spec fixed 2026-09-27 ~10:00, before any extraction exists)

**Why:** Bonsai's score is about 65% EPS growth, which code computes exactly. An LLM earns its place only by reading
what code cannot: the release's words. Here Bonsai stops deciding and only extracts labeled facts; code turns them
into a score.

**Fields**, each a label plus an exact quote (at most 30 words) from the release:

| Field | Labels |
|---|---|
| one_off | charge (impairment, restructuring, litigation, write-down) / gain / none |
| demand | strengthening / stable / weakening / not_stated (orders, backlog, bookings, pipeline, traffic) |
| margin | expanded / stable / contracted / not_stated (gross or operating margin vs a year earlier) |
| capital_return | increased (new or larger buyback or dividend) / cut (reduced or suspended) / none |
| leadership | change (CEO or CFO leaving or named) / none |
| risk_flag | yes (restatement, material weakness, going concern, delisting, investigation) / no |
| segment_weakness | yes (management names a segment or region that declined) / no |

- **Code check:** any label other than none / not_stated / no counts only if its quote is word for word in the
  release. Otherwise it becomes none / not_stated / no.

**Step 1: prompt optimization. It uses only extraction quality, never stock returns.**
- **Dev set:** 100 random 2024 S&P 500 releases (seed 1).
- **Candidates:**
  - P1: schema only;
  - P2: schema plus a definition of each label;
  - P3: P2 with the quote written before the label (evidence first);
  - P4: P3 plus two short worked examples.
- **Quality metrics per candidate:**
  - parse rate;
  - quote-verified share of the non-default labels (the hallucination proxy);
  - agreement with keyword "silver labels" from code (e.g. "repurchase" + "authoriz" → capital_return increased;
    "impairment" / "restructuring charge" → one_off charge);
  - seconds per release.
- **Score** = parse rate × verified share × silver agreement. The highest score wins; within 0.02, the faster one.
- The winning prompt is frozen and committed before step 2.

**Step 2: extract** with the frozen prompt, on:
- the 2024 S&P 500 fact-sheet sample (about 2,000 releases): training;
- the 2025-26 sample (1,180 releases): test.

**Step 3: one return test.**
- **Base model:** ridge on EPS growth + revenue growth. **Full model:** base + the 7 fields (one-hot).
- Both are trained on 2024 only and scored on 2025-26 releases.
- **Outcome:** the 5-day return vs the sector ETF.
- **Pass:**
  - the monthly rank IC of (full − base) on 2025-26 has a 95% CI above 0 (paired monthly bootstrap), AND
  - the full model's own IC CI is above 0.
- **Also reported:** the 20-day horizon, and each field's IC.
- **Registry:** one trial.

### Step 1 result: prompt optimization (2026-09-27 10:18). **P2 frozen.**

Dev set: 100 releases from 2024. Only extraction quality was measured; no returns were looked at.

| Prompt | Parse | Quotes verified | Keyword agreement | s / release | Score |
|---|---|---|---|---|---|
| P1: schema | 99% | 92.6% | 68.8% | 5.7 | 0.631 |
| **P2: + definitions** | **100%** | **96.1%** | **80.3%** | 5.8 | **0.771** |
| P3: + quote first | 98% | 93.7% | 78.0% | 6.6 | 0.716 |
| P4: + examples | 100% | 93.9% | 80.0% | 6.5 | 0.752 |

- The definitions did the work. Non-default labels per release fell from 3.5 to 2.4: fewer over-claims, and more
  of the remaining ones are right.
- Quote-first and the worked examples cost 13% more time and did not help.
- Step 2 now runs with P2 on the 2024 (train) and 2025-26 (test) samples. `results/events/llm_fields_dev.json`.

## Arm B: Jan researches → Bonsai labels → code decides (spec fixed 2026-09-27 ~10:35, before any arm-B data)

User request: "make sure u are doing the jan web research then feed into bonsai which uses algorithms to help make
judgement calls". Arm A (release only, running now) stays as specified. Arm B adds Jan's web research.

- **Jan (research):** Jan-v1-4B gathers as-of evidence per release, with the same tools and prefetch as research v3.
  The prefetch includes the company's previous release, which holds the guidance it gave. The research audit applies.
  - 2025-26: the existing v3 evidence (1,180 releases).
  - 2024: a new gather on the 2024 sample (`--run-tag _v3_2024`). Releases whose previous release predates 2024
    may lack it; that is recorded.
- **Bonsai (reading):** frozen prompt P2 plus the evidence (the release's first 6,000 characters and Jan's evidence,
  up to 6,000), and one more field that only the research can answer:
  - `vs_prior_guidance`: beat / met / missed / not_stated. This quarter's results vs the guidance range from the
    company's previous release.
- **Code check:** as in arm A, but a quote may come from the release or from Jan's evidence. No re-tuning: P2's
  wording is unchanged, and only the evidence block and the one field definition are added.
- **Code (the judgement):** the same ridge as arm A, trained on 2024 and scored on 2025-26. Full B = base + the 8 fields.
- **Pass for arm B:**
  - the 2025-26 monthly IC of (full B − full A) has a 95% CI above 0 (paired monthly bootstrap), AND
  - full B's own IC CI is above 0 (5-day return vs sector).
  - So research must add something beyond what Bonsai reads in the release alone.
- **Registry:** one more trial. Also reported: the 20-day horizon, and the `vs_prior_guidance` field alone.
- **Context overflow (fixed 2026-09-27 ~18:40, after 960 of 1,995 2024 labels and before any arm B return was
  looked at):** one release plus its evidence came to 8,436 tokens, over Bonsai's 8,192-token window. Ollama refused it
  (HTTP 400) and the run stopped. Rule from now on, for arms B and C: a release whose input does not fit is recorded as
  unparsed (`overflow: true`, every field at its default), exactly like a reply that doesn't parse. Inputs are not
  shortened differently, the window is not changed mid-run, and the 960 labels already made stand. The overflow count
  is reported with the verdict. Arm A's verdict (below) does not depend on this.
- **Arm B verdict (27 Sep, pre-registered 5-day test): FAIL.** Full B − full A +0.007 IC [−0.037, +0.052]; full B's
  own IC +0.050 [−0.007, +0.107]. 20-day: B − A −0.019 [−0.070, +0.025]. `vs_prior_guidance` alone +0.069
  [−0.095, +0.228] on 342 releases (836 of 1,180 "not_stated"). Context overflows: 1 of 3,175 (2024), 0 (2025-26).
- **Arm A verdict (27 Sep, pre-registered 5-day test): FAIL.** Full A's IC +0.043 [+0.005, +0.082], but full − base
  +0.015 [−0.035, +0.058]: Bonsai's release-only labels add nothing the code's base features don't already carry.
  20-day: full − base −0.021 [−0.066, +0.024].

### Arm B2: the same research with its news actually gathered (spec fixed 2026-09-27 ~23:30, after arm B's verdict)

**Why a second arm B.** Arm B's gather ran 16 research workers behind the Internet Archive's pacing (one request per
4 s); 75% of news calls hit their 15 s timeout. Jan had a news page for only **5% of 2024 releases** (the training
year) and 46% of 2025-26. So arm B mostly tested "the filing twice", not web research. B2 changes only that.

- **Step 1, news (network only):** `scripts/warm_news.py` makes the exact call Jan's prefetch makes
  (`news_as_of` for the ticker at the release's acceptance time), one at a time with a long timeout, into the same
  tool cache. The as-of rule and page parsing are unchanged. Coverage is reported per sample.
- **Step 2, Jan (GPU, after arm C):** Jan-v1-4B re-gathers both samples with arm B's exact settings (model, prefetch v3,
  rounds, tools, audit) into new folders (`_v3b2_2024`, `_v3b2`). Tool results come from the cache where present,
  so the only intended difference is news being there.
- **Step 3, Bonsai:** arm B's frozen prompt `PROMPT_R` and fields on the new evidence (same 6,000 + 6,000 characters,
  same overflow rule), into `llm_fields_research_b2_<tag>.jsonl`.
- **Step 4, the one test (trial `llm_fields_research_b2`):** exactly arm B's rule: the 2025-26 monthly IC of
  (full B2 − full A) has a 95% CI above 0 (paired monthly bootstrap) AND full B2's own IC CI is above 0, 5-day vs
  sector. Reported, not deciding: 20-day, B2 − B, and B2 on the releases where news was found.
- **Stop rule:** if news coverage after step 1 is below 50% in 2024, B2 is not run (the Archive, not the pacing, is
  then the limit) and a different free news source is specced instead.

### Arm B2 result (2026-09-28): **FAIL**, the closest so far

News coverage after the warm-up: 80% of 2024 releases (1,596 of 1,995; arm B 5%) and 81% of 2025-26.
2025-26, monthly rank IC, 5-day vs sector (14 months, 1,177 releases):

| | Mean IC | 95% CI |
|---|---|---|
| full A | +0.043 | [+0.005, +0.082] |
| full B2 | +0.054 | [−0.004, +0.114] |
| **B2 − A (deciding)** | **+0.011** | **[−0.035, +0.062]** |
| B2 − B | +0.004 | [−0.033, +0.041] |
| B2 on releases with news (953) | +0.064 | [+0.001, +0.125] |
| 20-day: B2 − B (reported, not deciding) | +0.029 | [+0.009, +0.050] |

- Neither pass check holds. Gathering the news made the research labels a little better than arm B (clearly so at
  20 days), but not better than the release alone at 5 days.
- `vs_prior_guidance` is still "not_stated" for 859 of 1,180 releases: the evidence doesn't carry last quarter's
  guidance to Bonsai, as found before this verdict (arm B3 below was fixed before it).

### Arm B3: evidence that actually reaches Bonsai (spec fixed 2026-09-28 ~12:10, BEFORE arm B2's verdict)

**Why.** Looking at the evidence text (not at any returns): Bonsai's research evidence is a transcript capped at about
5,600 characters, split evenly across every tool result, and the first-look results (the oldest round) get squeezed
first. So each result reaches Bonsai as its first ~400 characters: for the news page that is Yahoo's page header
("NYSE - Nasdaq Real Time Price…"), and for the previous quarter's release it is the headline, never its outlook.
Arm B's most promising field, `vs_prior_guidance` (+0.069 on 342 releases), was "not_stated" for 71% of releases.
Also measured: the "news" pages are Internet Archive snapshots of Yahoo's quote page, often weeks old, with 2–3
headlines among boilerplate.

B3 keeps Jan's B2 research and Bonsai's frozen `PROMPT_R` and fields, and changes only how the 6,000 evidence
characters are filled. Code builds them from the same as-of material, in this order:

1. **Previous quarter's outlook (up to 2,000 chars):** the same company's previous earnings release (30–200 days
   earlier, `previous_releases`), full text from SEC (missing ones fetched once, free). Code keeps the paragraphs
   whose text matches `outlook|guidance|expects?|anticipates?|forecast|full[- ]year|fiscal (year )?20\d\d` AND a
   number (a digit), in document order, preferring those after a heading line containing "Outlook" or "Guidance".
   None found → the line "No outlook found in the previous release (<date>)."
2. **News headlines (up to 1,200 chars):** from the cached as-of news page: lines of 25–200 characters that are
   not boilerplate (a fixed list: "Yahoo", "Subscribe", "Real Time Price", "Currency in", "Trade prices",
   "Fair Value", "actionable insight", "All rights reserved", "As of "), headed by the snapshot's date and its age
   in days before the release. None → "No news headlines found."
3. **Jan's own research (the rest):** Jan's rounds after the first look, as in B2 (same renderer), then Jan's
   final reason if it gave one.

- **Test, one trial (`llm_fields_research_b3`):** exactly arm B's rule: the 2025–26 monthly IC of
  (full B3 − full A) has a 95% CI above 0 (paired monthly bootstrap) AND full B3's own IC CI is above 0, 5-day vs
  sector. Reported, not deciding: 20-day, B3 − B2, `vs_prior_guidance` coverage and its own IC, and the share of
  releases where step 1 or 2 found something.
- **Runs after B2's labels** (one model on the GPU at a time). If B2 passes, B3 still runs and is reported; it
  replaces B2 only if it passes AND B3 − B2 has a 95% CI above 0.

### Arm B3 result (2026-09-28): **FAIL**, no better than the release alone

Step 1 found an outlook for 73% of 2024 releases and 98% of 2025-26; step 2 found headlines for 80% / 81%.
2025-26, monthly rank IC, 5-day vs sector (14 months, 1,177 releases):

| | Mean IC | 95% CI |
|---|---|---|
| full A | +0.043 | [+0.005, +0.082] |
| full B3 | +0.044 | [+0.003, +0.090] |
| **B3 − A (deciding)** | **+0.001** | **[−0.032, +0.035]** |
| B3 − B2 | −0.010 | [−0.048, +0.031] |
| B3 on releases with news (953) | +0.046 | [−0.001, +0.096] |
| `vs_prior_guidance` alone (510 stated) | +0.042 | [−0.070, +0.158] |
| 20-day: B3 − A / B3 − B2 (reported) | −0.017 / −0.027 | [−0.069, +0.047] / [−0.068, +0.020] |

- Own IC is above 0, but the deciding check (better than the release alone) is not: +0.001.
- Getting last quarter's outlook to Bonsai worked mechanically (`not_stated` 859 → 669 of 1,180) but the field
  still carries no clear signal.
- Across B, B2 and B3 the research evidence adds between −0.017 and +0.011 IC over the release alone, every CI
  spanning 0. Reading: in these fields, the release already holds what Bonsai can use; more pre-release evidence
  does not help. The sleeve keeps its arm-A-based score (untested, 10%).

### Arm B4: the research arm at 20 days, on fresh releases (spec fixed 2026-09-29 ~15:45 PDT, before any B4 label)

**Why, and the honest prior.** Arm B2 at 20 days beat arm B: +0.029 [+0.009, +0.050]. That was reported, not
deciding. The comparison that matters, B2 against the release alone (arm A) at 20 days, was only +0.010
[−0.042, +0.055] in the same run. B3 − A at 20 days was −0.017. So the odds are poor. The user asked to run it
anyway ("ok start", 29 Sep); this is the one run.

**Sample (fresh for these arms).** The S&P 400 and 600 releases in `features_breadth_2025.csv` accepted
2026-01-01 .. 2026-08-24: 2,851 releases, 8 months. No arm A, B, B2 or B3 label exists for them. Their EPS and
revenue numbers were already read in the breadth test; no new Qwen run is made. Their 5-day returns were used there,
with Bonsai's decision score, not these fields. Their 20-day returns against these fields have never been computed.
Differences from B2, stated in advance: mid and small caps instead of the S&P 500, and a short 8-month window.

**Steps (Jan-v1-4B and Bonsai-27B only).**
1. News warm-up (`warm_news.py`, same call and cache). Stop rule as B2: if coverage is below 50%, B4 is not run.
2. Jan: arm B2's exact gather settings (`research_events.py`, vLLM, 16 workers) into `_v3b4`, then the audit.
3. Bonsai: arm A's frozen prompt P2 on the release (`llm_fields_b4.jsonl`), and arm B's frozen `PROMPT_R` on
   release + Jan's evidence (`llm_fields_research_b4.jsonl`). Same 6,000 + 6,000 characters and overflow rule.
4. Models: arm A's and arm B2's ridges, trained on the 2024 S&P 500 sample only (their existing labels, lam 10),
   scored on the B4 sample. No refit on B4 data.

**The one test (trial `llm_fields_research_b4`), 20-day return vs the sector ETF:** the monthly rank IC of
(full B2 − full A) has a 95% CI above 0 (paired monthly bootstrap, months with ≥ 20 releases, 5,000 draws, seed 0),
AND full B2's own 20-day IC CI is above 0. Reported, not deciding: the 5-day numbers, B2 on releases with news, and
S&P 400 vs 600 separately.

**GPU etiquette.** GPU steps pause during the live runs: 05:30–06:30 and 15:15–16:30 PDT daily, and 15:15–18:00
on Thu 1 Oct for the first long-term and theme cohorts. A paused step resumes where it stopped.
**If it passes:** a shadow at 0 weight in the forward test. The AI-picks sleeve changes only by the user's decision.

**Outcome (2026-09-29 ~18:05 PDT): STOPPED by the pre-registered news stop rule; no test run, no trial registered.**
After 150 of 2,851 releases the Archive had a news page for 31 (21%). The 50% floor was fixed in advance. The S&P
500 samples had 80%: mid and small caps rarely have archived Yahoo pages. Each release took about 50 s, so a full
warm-up would take about 40 hours. The rule was applied at 150 releases rather than 2,851, because the 95% range of
the coverage (about 15–28%) was already far below 50%. That is a stop, not a result. Bonsai's arm A labels
(960 done, `llm_fields_b4.jsonl`) are kept but not scored. Re-running B4 needs a different free news source; that
would be a new spec.

### Arm B4b: B4 with GDELT news (spec fixed 2026-09-29 ~21:47 PDT, before any B4b label or GDELT warm-up)

**Why.** B4 was stopped because the Internet Archive had news for 21% of these mid and small caps (the rule needs
50%). The user asked to continue B4. Its own stop rule names the remedy: a different free news source. A GDELT
probe (DOC 2.0 API, free) got an answer for 18 of its first 30 releases, and 17 of those 18 had ≥ 3 articles in
the 45 days before the release. Everything else is B4's spec, unchanged: sample, arms, ridges, deciding rule and
GPU quiet windows. No returns have been looked at.

**News source (frozen).**
- Query: GDELT DOC 2.0 `mode=artlist`, the exact phrase of the cleaned company name (the rules in
  `scripts/gdelt_probe.py`: legal suffixes dropped) plus `sourcelang:english`.
- A one-word cleaned name gets ` (stock OR shares OR earnings OR NYSE OR Nasdaq)` added.
- Window: 45 days before the acceptance time, ending 1 minute before it. Only articles whose `seendate` is strictly
  before the acceptance count.
- Paced at one request every 20 s or more, with 180 s backoff on refusals.
- The newest 15 as-of headlines (date, domain, title) replace the Archive page as the result of Jan's `news_as_of`
  call, stored in the same tool cache under the same key. Jan's gather (B2 settings), the evidence rules and Bonsai's
  prompts are unchanged.

**Gates before any Jan or B2 labels (both needed, else B4b stops, no test):**
1. As-of coverage (≥ 1 headline) on at least 50% of the 2,851 releases after the warm-up.
2. Relevance: 40 releases drawn at random (seed 0) from those with headlines. Claude reads the titles only. The top
   headline must be about that company in at least 70% of them.

**Arm A labels.** Bonsai P2 on the release alone does not depend on news, so it can continue now. Its 960 labels
from B4 are reused (same prompt and inputs).
**Note (22:4x PDT, before the warm-up):** the probe's 17-of-18 figure used windows shifted 7 h late: it read the
naive UTC acceptance times as local time. It was a feasibility hint only. The gates above are measured by
`scripts/warm_gdelt.py`, which keeps UTC (tested), and the probe is fixed too (892ecb9).
**Test (one trial, `llm_fields_research_b4b`):** B4's rule exactly. The 20-day (B2 − A) monthly IC paired CI must be
above 0 AND B2's own 20-day IC CI above 0.
**Engine note (2026-09-30 ~22:30 PDT, before any B4b research label exists; the user: "do the ollama measured
alternative and label client thing"):** Bonsai's label stages move from Ollama to the `llama-server` binary Ollama
itself bundles, run with 3 parallel slots (`app/sandbox/llamacpp_client.py`). Ollama 0.34.0 forces this model to one
request at a time; the measured gain is 1.63× (docs/QUANT_DASHBOARD_RESEARCH.md).
- **Unchanged:** the model file (the same GGUF blob Ollama loads), its chat template, thinking off, JSON output,
  temperature 0, 8,192 tokens of context per request, the output limit, the prompts, the quote check and every
  scoring rule. Nothing is downloaded.
- **What differs:** with 3 slots the GPU batches requests together, which changes floating-point rounding. In the
  30 Sep benchmark one slot gave Ollama's replies exactly (24 of 24) and three slots agreed on 98.2% of fields. The
  differences are random, not directional. Arm A's labels (made on Ollama, one at a time) are kept as they are; the
  B2 labels will be made with 3 slots, so (B2 − A) carries that small extra noise on the B2 side. It can only make
  the test harder to pass, never easier.
- **Adoption check (engineering, not a trial; reads cached replies, writes no label or result file):** through the
  new client, on 24 arm-A prompts (P2) and 24 research prompts (`PROMPT_R`) that Ollama already answered:
  one slot must give the identical text on at least 95% of them, and three slots must parse 100% and agree on at
  least 97% of fields. If either fails, the label stages stay on Ollama (`AIRP_LABEL_ENGINE=ollama`).
- **Scope:** research label stages in `scripts/llm_fields.py` only. The live forward test (extractor, Bonsai judge
  and the shadow arms) stays on Ollama, untouched.
- **Adoption check result (2026-09-30 ~22:35 PDT): PASS, adopted.** One slot: 48 of 48 replies identical to
  Ollama's (P2 24/24, `PROMPT_R` 24/24). Three slots: 48 of 48 parsed, 99.2% of fields equal (label and quote), and
  99.7% of the labels code keeps after the quote check. A second three-slot pass gave 98.1%, so the batching noise is
  about 1 to 2% of fields from run to run.
- **Correction to the speed figure.** On a freshly started server the gain is **1.38× on P2 (5.59 → 4.06 s per
  release) and 1.25× on `PROMPT_R` (5.84 → 4.66 s)**, not 1.63×. The 30 Sep benchmark re-ran the same prompts on one
  server, so part of each prompt came from the server's prompt cache (1,664 new prompt tokens per request then,
  2,430 on a fresh server), which flattered the three-slot number. Reading the prompt is the limit: the GPU reads
  about 1,000 to 1,200 tokens a second whatever the slot count. Smaller GPU steps (256 and 128 tokens) were timed and
  are slower (4.13 / 4.76 s and 4.33 / 5.01 s), so the step stays at 1,024. The B2 label stage (2,851 releases)
  should take about 3.7 hours instead of 4.6.


### skfolio test (spec fixed 2026-09-27, before any run)

Question: does sizing the book by risk (skfolio) beat the fixed 20% crypto capital cap?

- **Arm S, one trial (`skfolio_cvar_risk_parity`):** same rebalance days as B0 (every 5th trading day, 2018-01-02 to
  2026-09-25), same trend rule deciding which crypto assets are on, same simulator and costs. The assets are SPY plus
  each crypto asset whose trend is on. Weights come from skfolio `RiskBudgeting` on CVaR (β = 0.95), equal risk
  budgets, long only. They are fitted on the trailing 252 daily returns up to the rebalance day (at least 120; with
  fewer, B0's weights that day), then scaled to sum to 0.98. No crypto capital cap: the test is whether risk sizing
  beats the cap.
- **Pass (the adding rule):** vol-matched CAGR above B0's AND the 90% block-bootstrap CI of the Sharpe difference above
  0. Results with the drawdown brakes are reported but do not decide.
- **Even on a pass, nothing trades** until the user changes `config/mandate.json` (the crypto cap is 20%).
- **Stress test (descriptive, not a trial):**
  1. Fit skfolio's `VineCopula` to weekly returns of SPY, BTC and ETH, 2018–2026.
  2. Sample 20,000 weeks conditioned on (a) SPY −10% in the week and (b) BTC −25% in the week.
  3. Report the frozen book's weekly loss at today's targets (SPY 78%, BTC 11.6%, ETH 8.4%): median and 5th percentile,
     next to the worst historical weeks. This informs the maximum-drawdown decision; it changes nothing.
- *Implementation note (2026-09-27, before any result was seen):* the first run stopped when the CVaR solver
  (CLARABEL) failed on 2 of 194 fits (2018-03-01 and 2024-04-08, both with only BTC on). Those days use B0's weights,
  the same fallback the spec gives for too little data. Nothing was registered by the stopped run.

### skfolio result (2026-09-27): **FAIL, narrowly (a lead, like the 35% crypto cap)**

| 2018–26 | CAGR | Vol | Sharpe | Max DD | Worst year |
|---|---|---|---|---|---|
| B0 (20% crypto cap) | 21.6% | 20.6% | 1.05 | 33.8% | −23.0% |
| S (CVaR risk parity) | 27.0% | 22.7% | 1.17 | 33.9% | −26.1% |
| S, vol-matched to B0 | 24.5% | 20.6% | 1.17 | 31.2% | −23.9% |

- Sharpe difference +0.12, 90% CI [−0.01, +0.23]. The lower end is just below 0, so it fails the adding rule. With the
  brakes: +0.10 [−0.04, +0.22].
- When crypto is on, risk parity holds 28% crypto on average (up to 44%) instead of 20%. That is much of the gain, and
  2018–26 was a good period for crypto, so part of the gain is hindsight.
- 2 of 439 rebalances fell back to B0's weights (solver failures).
- **Stress test (descriptive), frozen book at today's weights, weekly loss:**
  - a week when SPY falls 10%: median −9.4%, 5th percentile −14.5%, 1st percentile −17.7%;
  - a week when BTC falls 25%: median −6.3%, 5th percentile −12.0%;
  - for comparison, the worst real week was −15.7% (13 Mar 2020) and the 1st-percentile real week −8.1%.

### Arm C: more judgement for Bonsai (spec fixed 2026-09-27, before any extraction)

The user asked for Bonsai to use more judgement. Bonsai gets it; code still checks it and decides how much it counts.

- **Input:** the same as arm B (the release plus Jan's as-of research). **Prompt `PROMPT_J`:** arm B's prompt plus
  four judgement fields. Bonsai writes a short `reason` first (up to 40 words weighing the good and the bad), then the
  labels:
  - `earnings_quality`: clean / flattered (the beat leans on one-offs, tax, share count or adjustments) / not_clear;
  - `outlook_tone`: confident / cautious / not_stated;
  - `net_read`: bullish / neutral / bearish, Bonsai's own weighed call for the next few weeks, quoting the ONE sentence
    that matters most;
  - `conviction`: high / low.
- **Code keeps the last word.** Every non-default label needs a quote found word for word in the release or the
  evidence, or it falls back to the default. The ridge learns on 2024 how much each label is worth; it is scored on
  2025–26.
- **Quality gate, before the full run (quality only, no returns):** on the same 100 dev releases, parse rate ≥ 0.95 and
  verified-quote share ≥ 0.85. If the gate fails, the run stops and nothing is extracted.
- **Pass, one trial (`bonsai_judgement_fields_code`):** the 2025–26 monthly rank IC of (C − B) has a 95% CI above 0
  (paired monthly bootstrap) AND C's own IC CI is above 0, on 5-day returns vs sector. Reported but not deciding:
  20-day returns, C against C without its judgement fields, and `net_read` alone.
- **Runs after the current Jan → Bonsai run** (one model on the GPU at a time): `scripts/bonsai_judgement_run.sh`.

### Arm C result (2026-09-28): **FAIL**

Quality gate passed; 1,995 + 1,180 releases labelled. 2025–26, monthly rank IC (14 months, 1,177 releases):

| | 5-day, mean IC | 95% CI | 20-day, mean IC | 95% CI |
|---|---|---|---|---|
| B (for reference) | +0.050 | [−0.007, +0.107] | +0.030 | [−0.039, +0.102] |
| C | +0.029 | [−0.020, +0.087] | +0.014 | [−0.047, +0.081] |
| **C − B (deciding)** | **−0.022** | **[−0.065, +0.025]** | −0.017 | [−0.073, +0.034] |
| judgement fields within C | −0.024 | [−0.052, +0.005] | −0.015 | [−0.060, +0.020] |
| `net_read` alone (reported, not deciding) | +0.067 | [+0.002, +0.136] | +0.056 | [+0.014, +0.096] |

- **Neither pass check holds.** Adding the four judgement fields to the ridge made it slightly worse: the ridge fitted
  on 2024 gives them weights that don't carry to 2025–26.
- **Caveat:** C reused arm B's evidence, so it had the same news gap (Jan had news for 5% of 2024 releases).
- **`net_read` alone** (Bonsai's own bullish/neutral/bearish call, unfitted) is positive at both horizons. It was one
  of three non-deciding diagnostics, picked after seeing them, on the same 2025–26 data. So it is **not a pass** and
  can't be tested again on that data. Its honest test is new data: see "Arm C2" below.

### Arm C2: `net_read` on new data only (spec fixed 2026-09-28, after arm C, before any live `net_read` exists)

- **What:** the live event runner already has Bonsai read each new release; C2 adds arm C's `PROMPT_J` fields to that
  read (same prompt, same quote check) and records `net_read` next to the forward ledger
  (`<events dir>/net_read.jsonl`, `scripts/net_read_shadow.py`, run after each event run). No money, a shadow only.
  The live runner has no Jan research, so the evidence part says none was found (as for most of arm C's 2024 sample).
  A label written at or after the release's entry deadline is kept but never scored.
- **Score, no fitting:** bullish = +1, neutral = 0, bearish = −1 (a failed quote check = neutral).
- **Pass (one trial, `net_read_forward`), judged at the first review with at least 150 live scored releases and
  3 months:** the monthly rank IC vs 5-day sector-relative returns has a mean above 0.02 and an 80% one-sided
  bootstrap bound above 0, AND it adds to the live Bonsai log-odds (the blend-gain test of the learning loop).
- **Until then it changes nothing** in the book. It can also enter via B2 if B2 passes (B2 decides first).

### Disagreement test: Bonsai vs the market's first reaction (spec fixed 2026-09-27, before any run)

**Why this form.** Plain post-earnings drift was measured before (`eval_baseline.txt`) and is absent in the S&P 500:
the earnings-day reaction vs the next 20 days has an IC of −0.016 and −0.034 in the two samples. So the test asks a
narrower question: when Bonsai reads a release much better or worse than the market's first reaction, does the price
move back toward Bonsai's view?

- **Signal, code only, no fitting:** D = rank(Bonsai's 1-week log-odds) − rank(earnings-day reaction vs sector ETF),
  both as percentiles within the entry month. The reaction is the stock's move vs its sector ETF on the first trading
  day the release is public (`ear` in event_eval.build).
- **Outcome:** the excess return vs the sector ETF over the 20 trading days after the reaction day (`fwd20_ear`). It
  starts at the next open, so it does not overlap the reaction or Bonsai's 1-week horizon start.
- **Samples:** 2024 (1,995 releases) and 2025–26 (1,180). With nothing fitted, both are out of sample.
- **Pass (one trial, `disagreement_bonsai_vs_reaction`), all three needed:**
  1. the monthly rank IC of D over 2024–26 pooled has a 95% CI above 0;
  2. the mean IC is positive in each period separately;
  3. the top-minus-bottom fifth of D, net of 0.4% (20 bps each way on each leg), has a 90% monthly-bootstrap CI
     above 0.
- **Reported, not deciding:** Bonsai alone and the reaction alone on `fwd20_ear`, and D over 60 days.

### Disagreement result (2026-09-27): **FAIL, clearly**

| 20 days after the reaction day | Mean IC | 95% CI |
|---|---|---|
| D, pooled 2024–26 (3,145 releases, 26 months) | +0.010 | [−0.046, +0.066] |
| D, 2024 / 2025–26 | −0.000 / +0.018 | — |
| Bonsai alone | +0.018 | [−0.034, +0.067] |
| Reaction alone | −0.003 | [−0.068, +0.057] |

- Net top-minus-bottom fifth: +0.24% per 20 days, 90% CI [−0.61, +1.07].
- **None of the three checks passes.** Past the first week, neither Bonsai's read, the market's reaction nor their
  disagreement predicts large-cap returns. Whatever Bonsai knows gets priced within days, which fits the 1-week IC of
  +0.10 fading to about 0 at 20 days.

### 8-K breaking-news watcher (spec fixed 2026-09-27, before any text is read or any return computed)

**Question:** can Bonsai read non-earnings news filings (deals, restructurings, leadership changes, distress) and tell
good from bad well enough to trade the next week?

- **Events:** `data/events/news8k_2024-01-01_2026-09-24.csv` from `scripts/build_8k_events.py`. These are 8-Ks
  (not amendments) from S&P 500 members (as of Jan 1 of the year) with a news item and no Item 2.02:
  - distress: 4.02, 3.01, 2.04, 1.03;
  - restructuring: 2.05, 2.06;
  - deal: 1.01, 2.01, 1.02;
  - leadership: 5.02.
  Items 7.01 and 8.01 on their own are left out (too broad).
- **Input:** the filing's own text plus its press-release exhibit, if any (first 6,000 characters). The acceptance time
  sets the entry: the next market open.
- **Bonsai (W1):**
  - a short `reason` first, then `direction` (positive / neutral / negative for the stock over the next week vs its
    sector) and `size` (major / minor), each with a quote;
  - code keeps a non-neutral label only if its quote is found word for word in the text.
  - **Score, code only, nothing fitted:** direction (+1 / 0 / −1) × (2 if major, else 1).
- **Quality gate first** (quality only, no returns): on 100 filings from 2024, parse rate ≥ 0.95 and verified-quote
  share ≥ 0.85. If it fails, stop.
- **Outcome:** the 5-day excess return vs the sector ETF from the entry open (the same `fwd5` as the earnings book).
- **Pass (one trial, `news8k_bonsai_direction`), all three needed:**
  1. the pooled 2024–26 monthly rank IC has a 95% CI above 0;
  2. the mean IC is positive in 2024 and in 2025–26 separately;
  3. long score > 0 minus short score < 0, per month, net of 0.4% (20 bps each way on each leg), has a 90%
     monthly-bootstrap CI above 0.
- **Reported, not deciding:** IC by category, and 20-day returns.
- **W1 verdict (27 Sep): stopped at the quality gate, as pre-registered.** On the 100 dev filings: parse rate 0.97
  (gate 0.95) but verified-quote share 0.826 (gate 0.85). Nothing was labelled and no return was looked at, so no
  trial is registered. The 8-K watcher does not go into the forward test.
- **W2 (Jan's research added) runs only if arm B passes tonight.** Otherwise research has failed four times, and W2
  would be a fifth try at the same idea.

### AI-picks paper sleeve (rules fixed 2026-09-28, before the forward test's first release)

The user decided on 28 Sep that Jan and Bonsai should pick specific stocks on paper from 5 Oct, whether or not a
version has passed its backtest, as a capped sleeve, and that a passing arm replaces its score when one passes.
**No version has passed.** The sleeve is labelled "untested" everywhere it is shown.

- **Size:** 10% of the paper account. The master book (SPY + crypto trend) keeps the other 90%: the broker mirror
  plans it on 90% of the account's equity. Simulated sleeve capital: $10,000.
- **Score and pick rule:** the live event score (Bonsai's 1-week log-odds; `source = bonsai` only, "lite" decisions
  are never traded). Pick when log-odds ≥ **2.873**, the top fifth of the 2025–26 history
  (`factsheet_secchk`). History of this exact rule, 5-day return vs the sector ETF per pick:
  2024 −0.03% (275 picks), 2025–26 +1.20% (236 picks). It did not work in 2024.
- **Trade:** long the stock and short its sector ETF, the same dollar amount (one fifth of the sleeve's equity at
  entry), so the sleeve earns exactly what the tests measure. Enter at the release's entry open (a market-on-open
  order sent by the 08:45 ET run), exit at the open 5 trading days later. At most 5 pairs open; a pick that finds no
  free slot is logged as skipped. Whole shares; a pick whose stock costs more than a slot is skipped.
- **Costs in the simulator:** 0.20% per leg each way (0.8% per pair round trip).
- **Brakes:** the sleeve opens no new pairs while its drawdown is 25% or more; the kill switch and REDUCING state
  of the master book apply to it too.
- **Replacement:** if an arm passes (B2, B3...), its score and its own top-fifth threshold (from its 2025–26 scores)
  replace these from the next run, and the change is logged.
- **Review:** after 3 months or 60 closed pairs, whichever is later: continue only if the mean net return per pair
  has an 80% bootstrap bound above 0; otherwise the sleeve stops and its 10% goes back to the master book.
- **Replay of these exact rules** (day by day, two runs a day, slots and costs included; not a test, the rules were
  not changed after it): 2024 **−13.2%** (101 pairs, −0.74% each, max drawdown 19.8%); 2025–26 **+8.6%** (131 pairs,
  +0.41% each, max drawdown 9.8%). In earnings season the 5 slots fill and later picks are skipped.

### Arm D: the AI-build-out lens (spec fixed 2026-09-28, before any live release is read with it)

The user asked for the AIs to think like Leopold Aschenbrenner ("Situational Awareness", 2024): AI capability scales
with compute, so spending on chips, datacenters, networking, power and the grid grows far faster than expected, and
companies selling into that build-out gain for years.

- **Prompt `PROMPT_AI`** (scripts/llm_fields.py, fixed): the view in five lines, then two fields with the same quote
  check as every other arm: `ai_exposure` (beneficiary / neutral / hurt) and `ai_read` (bullish / neutral / bearish
  for the next few weeks under this view). Score: bullish +1, neutral 0, bearish −1.
- **Forward only, never backtested on 2024–26.** The thesis is famous because it worked in exactly those years
  (Nvidia, power producers), and Bonsai may have learned those outcomes; a backtest would be flattered. It is judged
  only on live releases, like C2: `scripts/net_read_shadow.py` labels each live decision before its entry deadline
  into `<events dir>/ai_lens.jsonl`, no money.
- **Pass (one trial, `ai_lens_forward`), at the first review with at least 150 scored releases and 3 months:** the
  monthly rank IC of `ai_read` vs 5-day sector-relative returns has a mean above 0.02 and an 80% one-sided bootstrap
  bound above 0, AND it adds to the live Bonsai log-odds (the learning loop's blend-gain test).
- Note: this lens is about next-week earnings reactions. The view itself is a multi-year theme; a thematic sleeve
  would need its own design and test.

### Arm E: bull/bear thesis (spec fixed 2026-09-28 ~18:00, before any live release is read with it)

The user asked for a bull/bear thesis. Bonsai argues both sides of each live release before it decides.

- **Prompt `PROMPT_BB`** (scripts/llm_fields.py, fixed): up to 3 bull points and up to 3 bear points, each a short
  point with a quote copied word for word. The prompt says to make each case in earnest, even when the release
  clearly favours the other side. Then a weighed `reason` and `bb_read` (bullish / neutral / bearish), with the usual
  quote check. Score: bullish +1, neutral 0, bearish −1. Each point is stored with `verified` (quote found in the
  text or not); unverified points are shown, marked, and never scored.
- **Forward only**, like C2 and D (a judgement Bonsai could flatter with hindsight on 2024–26):
  `scripts/net_read_shadow.py` labels each live decision before its entry deadline into
  `<events dir>/bull_bear.jsonl`, no money. The dashboard's AI PICKS tab shows each pick's thesis.
- **Pass (one trial, `bull_bear_forward`):** C2's and D's rule. At the first review with at least 150 scored releases
  and 3 months, the monthly rank IC of `bb_read` vs 5-day sector-relative returns has a mean above 0.02 and an 80%
  one-sided bootstrap bound above 0, AND it adds to the live Bonsai log-odds (the learning loop's blend-gain test).
- **Quality check before going live** (10 random releases, quality only, no returns): parsed 10/10, every release has
  both sides, 53 of 60 point quotes verified; about 8 seconds per release.

### Day-trading track (spec fixed 2026-09-28, before any intraday data was downloaded)

Data: Alpaca's free historical SIP 1-minute bars (full market, 2016 onward), regular hours only, cached once.
Universe: SPY and QQQ (the most liquid; one trade a day per symbol fits the pattern-day-trader rule's spirit).
Costs: 1 bp per side (2 bps a round trip); also reported at 5 bps a side. No leverage: each trade is 1× the track's
capital. Two published rules, each tested only on data AFTER its publication, each its own trial:

- **D1, intraday momentum** (Gao, Han, Li and Zhou, *Journal of Financial Economics* 2018): the return from the
  previous close to 10:00 ET predicts the last half hour. Rule: at 15:30 ET, go long if that return is positive,
  short if negative; exit at the 15:59 bar's close. Test window: 2019-01-02 to 2026-09-25 (after publication).
  Trial `daytrade_intraday_momentum`.
- **D2, 5-minute opening-range breakout** (Zarattini and Aziz, SSRN 2023): after the first 5-minute bar, go long
  if it closed up, short if down, at 09:35; stop at the other end of that first bar; otherwise exit at 15:59. No
  trade if the first bar is flat. Test window: 2023-07-01 to 2026-09-25 (after publication); 2016–2023 is
  reported only. Trial `daytrade_orb5`.
- **Pass (each):** on its test window, at 2 bps a round trip, the annualized Sharpe of the daily P&L (SPY and QQQ,
  equal capital) is at least 0.5 AND its 95% block-bootstrap CI (21-day blocks) is above 0. Reported, not deciding:
  5 bps costs, each symbol alone, correlation with the core book, worst month.
- **Then:** a passing rule goes to paper as "untested" (the planner's 10% rung) only after 1 month of clean dry runs,
  and earns more weight only by the evidence ladder (3 months of forward paper results).

### Day-trading round 2: D3 and D4 (spec fixed 2026-09-28 ~19:30, after D1/D2 failed, before any D3/D4 code ran)

Why round 1 failed: before costs both rules earned about 0 a day (D1 was already negative in 2016–18, before its
test window), so any cost made them lose. Round 2 tests two newer published rules that trade only on stronger
signals. Same data, universe, costs, pass rule and windows-after-publication discipline as D1/D2; each its own trial.

- **D3, "noise area" breakout with a VWAP stop** (Zarattini, Aziz and Barbon, "Beat the Market", SSRN May 2024),
  as we read it, at 1× (the paper's volatility-sized leverage is left out, as in D1/D2):
  - For each minute of the day, σ(minute) = mean over the previous 14 trading days of |price at that minute / that
    day's open − 1|.
  - Upper bound = max(today's open, yesterday's close) × (1 + σ); lower bound = min(today's open, yesterday's
    close) × (1 − σ).
  - Checks at 10:00, 10:30, …, 15:30 (the price is the close of the minute before). Flat: above the upper bound →
    long, below the lower bound → short, at the next bar's open. Long: exit if price is below max(upper bound,
    VWAP since 09:30); short: exit if above min(lower bound, VWAP). A position can be re-opened at a later check.
    Everything is closed at the 15:59 close. Cost: 1 bp per side per entry and per exit.
  - Test window: 2024-06-01 to 2026-09-25 (about 580 days; short, so its CI will be wide). Trial `daytrade_noise_vwap`.
- **D4, market intraday momentum, rest-of-day signal** (Baltussen, Da, Lammers and Martens, *Journal of Financial
  Economics*, October 2021): the return from yesterday's close to 15:30 (the close of the 15:29 bar) predicts the
  last half hour. Rule: at 15:30 go long if positive, short if negative; exit at the 15:59 close. Test window:
  2021-11-01 to 2026-09-25. Trial `daytrade_rod_momentum`.
- **Pass (each):** as D1/D2 (Sharpe ≥ 0.5 and 95% block-bootstrap CI above 0 at 1 bp a side, SPY and QQQ equal
  capital). Reported: 5 bps, each symbol, trades per week (the pattern-day-trader rule allows 3 per 5 days under
  $25k), and the before-window.
- **If one passes:** it would trade micro index futures (MES/MNQ, not subject to the pattern-day-trader rule) in
  the simulator, after a month of clean dry runs, then by the evidence ladder.

### Day-trading round 3: D5 end-of-day reversal (spec fixed 2026-09-28 ~20:00, before any stock-level intraday data was downloaded)

Why this one: every failed rule (D1–D4) bet on the index's direction. D5 is market-neutral and cross-sectional,
with a stated structural cause the authors test (attention-driven retail buying of the day's losers and short-sellers
cutting risk before the close), which arbitrage does not easily remove.

- **Rule** (Baltussen, Da and Soebhag, "End-of-Day Reversal", EFMA 2024; April 2025 version), on 30-minute bars
  (Alpaca's free SIP feed, split-adjusted), for S&P 500 stocks:
  - Signal ROD3 = yesterday's close to the 15:00 price (the close of the 14:30 bar). The 15:00–15:30 half hour is
    skipped, as in the paper.
  - At 15:30: long the 10% of stocks with the lowest ROD3, short the 10% with the highest, equal weight, dollar
    neutral (each side = the track's capital × 0.5). Entry at the 15:30 bar's open, exit at its close (the last
    trade before 16:00).
  - Universe: the S&P 500 companies with an earnings release in 2024 in our event history (the membership near the
    start of the test window). Needs 20 or more stocks with both prices that day; days without a 15:30 bar (half
    days) are skipped.
  - Costs: 1 bp per side per stock (large caps near the close); also reported at 3 bps.
- **Test window: 2024-07-01 to 2026-09-25** (after the EFMA 2024 presentation). Reported, not deciding: 2016-01 to
  2024-06 (inside the paper's sample; survivorship-biased because the universe is the 2024 list).
- **Pass (trial `daytrade_eod_reversal`):** as D1–D4 (annualized Sharpe of the daily P&L ≥ 0.5 and its 95%
  block-bootstrap CI above 0, at 1 bp a side). Reported: 3 bps, long and short legs apart, correlation with the core.
- **If it passes:** a live paper shadow first (orders at 15:30, market-on-close exits) for 1 month of clean dry
  runs, then the evidence ladder.
- **Clarification (2026-09-28 ~21:30, before the full download and before any D5 result):** Alpaca returns
  after-hours prints in the 14:30 and 15:30 slots of half days (e.g. 3 Jul 2024: 115 stocks, median 6 trades per
  bar, vs 5,000-7,000 on regular days), so "days without a 15:30 bar are skipped" is implemented as: a day whose
  median 15:30-bar trade count is under 100 is a half day and is dropped before anything is computed (it is also not
  used as "yesterday's close"). Checked only on three known half-day weeks (Jul 2024, Thanksgiving 2024 and 2016).
  The download fetches each trading day's 14:30-16:00 window for all stocks at once.

- **Result (run once, 2026-09-28 ~23:00): FAIL.** Test window 2024-07-01..2026-09-25, 556 days, median 490 stocks:
  Sharpe **−1.86** [−3.65, +0.08] at 1 bp a side, CAGR −3.4%, hit rate 44%; at 3 bps Sharpe −7.28. Before the
  window (2016-01..2024-06, reported only): Sharpe −1.75 at 1 bp. Legs (gross, bp a day): long −0.13, short +1.39.
  Correlation with the core −0.04. Diagnostic, not deciding: before costs the reversal is there but small, +0.99 bp
  a day in 2016-24 (gross Sharpe 1.73) and +0.63 bp after publication (0.85 [−0.77, 2.76]); a round trip on both
  legs costs 2 bp a day at 1 bp a side, so costs take all of it. In S&P 500 names at a 30-minute resolution the
  effect is about a tenth of the paper's all-stock size. Day trading stays paused; no variant of D5 will be tried.

### Arm F: a self-improving Bonsai (spec fixed 2026-09-28 ~20:00, before any code)

The user asked for Bonsai's decision-making to improve itself. The honest version is a champion/challenger loop on
the live bull/bear lens (arm E), where fast 5-day outcomes give feedback every week, and where only matured outcomes
are ever shown to Bonsai.

- **Champion:** starts as arm E's frozen prompt (`PROMPT_BB`, version 0). Arm E's own pass test always uses version
  0 and is never changed by this loop.
- **Reflection (monthly, with the long-term schedule):** code collects the champion's matured labels (5-day
  sector-relative outcome known), up to the 80 most recent, and writes a summary: each label's count and average
  outcome, then the 15 worst misses (bullish calls with the most negative outcomes, bearish with the most positive)
  with their bull/bear points and reason. Bonsai reads it and writes at most 5 general lessons (each at most 30
  words). Code drops any lesson naming a company or ticker in the universe. The challenger's prompt is the
  champion's prompt plus "Lessons from your past calls:" and the lessons. Needs at least 40 matured labels;
  otherwise no challenger that month.
- **Challenger:** runs beside the champion on every live release after it was made (one more Bonsai call per
  release, no money). One challenger at a time; each is its own registered trial (`bb_selfimprove_v<n>`).
- **Promotion:** once the challenger has 100 or more paired scored releases over at least 2 months: the paired
  monthly IC of (challenger − champion) has a mean above 0 and an 80% one-sided bootstrap bound above 0 → the
  challenger becomes the champion. A challenger with 200 paired releases and no promotion is retired. The next
  reflection makes a new one from the current champion.
- **What it can change:** only the shadow champion. It reaches the event score only through the same test as any
  lens (150 releases, 3 months, IC > 0.02, blend gain). Every version, lesson and decision is logged.


### Arm F v2: research-grade self-improvement (spec change 2026-09-29, before any arm F reflection, challenger or data)

Arm F has produced nothing yet (its first reflection needs 40 matured labels, ~early November), so its design can
change without touching any result. v2 replaces v1's reflection and promotion rules; everything else stays (arm E's
v0 is frozen for its own test; lessons never name companies; shadow only; every step logged).

1. **Fix: anytime-valid promotion.** v1 re-ran a fixed 80% bootstrap bound on every events run; checking a fixed
   test repeatedly ("peeking") inflates false promotions. v2 uses a betting e-process (Shafer 2021; Waudby-Smith and
   Ramdas, JRSS-B 2024), valid at every look:
   - Per release: x = (challenger call − champion call) × clip(5-day sector-relative return / 5%, −1, 1) / 2, in
     [−1, 1] (calls: bullish +1, neutral 0, bearish −1). Releases are averaged by entry week (cross-sectional and
     overlapping-window dependence; weeks are processed in order once all their outcomes are known).
   - Wealth W = Π(1 + λ·x_week), λ predictable from past weeks only (aGRAPA: clip(mean / (var + mean²), 0, 0.5); 0
     until 2 weeks exist). **Promote when W ≥ 20** (5% false-promotion bound, whenever it is checked), with at least
     60 paired releases over 2+ months as a guard against a single-season fluke.
   - The mirror process on −x: **retire early when it reaches 20** (the challenger is reliably worse); otherwise
     retire at 200 paired releases.
2. **Held-out reflection.** Matured calls are split by time: the older 2/3 are shown to Bonsai, the newer 1/3 are
   kept back (validation). The summary is contrastive: per-label base rates, the 10 worst misses and the 10 best hits
   (so lessons do not over-correct). Needs 60 matured calls (40 shown, 20 held back).
3. **Several candidates, chosen offline.** Bonsai writes 3 lesson sets from the same summary with 3 fixed framings
   (errors to avoid; what separated hits from misses; when it was over-confident). Each candidate prompt re-labels
   the held-back releases (replay, same release text and quote check as arm E). The candidate with the best rank IC
   on the held-back set becomes the challenger, only if that IC beats the champion's own IC on the same releases;
   otherwise no challenger that month. Selection only picks what to test; the forward e-process still decides.
4. **Lesson memory:** lessons are de-duplicated (case and punctuation ignored), at most 8 kept (newest win), each
   stored with the version and month it came from.
5. **Rollback guard:** once a champion above v0 exists, the same e-process runs champion vs v0 (arm E, always
   labelled); if v0 is reliably better (W ≥ 20 on the mirror), the champion is demoted back to v0.
6. **Audit log:** the reflection summary, the 3 candidates, their held-back ICs, the choice, and the e-values are
   stored in bb_versions.jsonl. Each challenger is still its own registered trial (`bb_selfimprove_v<n>`).
7. **Amendment (2026-09-29, found on synthetic unit-test data, before any arm F data):** weekly means of x are
   small (|x| ≈ 0.1), so with bets capped at 0.5 even a challenger that is always right could not reach W = 20
   before the 200-release retirement: the test was valid but powerless. Fix: the bet is on y = clip(week mean of x /
   0.2, −1, 1) (H0: E[y | past] ≤ 0), and a challenger is retired after 26 scored weeks without promotion (instead of
   200 releases), or early when the mirror process reaches 20. The 5% bound, the 60-release / 2-month guard and
   everything else are unchanged. Consequence, stated up front: only a clearly better challenger gets promoted
   (roughly a weekly edge of half the scale for ~3 months); small gains will not be detectable with this much data.
### Day-trading result (2026-09-28): **both rules FAIL**

| Rule, test window (after publication) | Sharpe at 2 bps | 95% CI | CAGR | at 10 bps | hit rate |
|---|---|---|---|---|---|
| D1 intraday momentum, 2019-01 to 2026-09 (1,939 days) | **−1.00** | [−2.22, −0.08] | −5.3% | −4.79 | 44% |
| D2 5-minute ORB, 2023-07 to 2026-09 (812 days) | **−0.24** | [−1.40, +0.62] | −1.8% | −3.26 | 28% |

- Reported: D1 before its window (2016–18) Sharpe −1.42; D2 before its window (2016–23) +0.05. Correlation with the
  core book: 0.01 and 0.06.
- Checked for a bug: the raw edge (the sign of the morning return times the last half hour) is about 0 bp a day in
  every period and both symbols (−0.6 to +0.7 bp), against 2 bp of costs. The published effects are gone after
  publication. D1's CI is entirely below 0.
- The day-trading track stays at 0%. Any next rule needs its own pre-registration; the planner shows it as failed.

### Day-trading round 2 result (2026-09-28): **both FAIL**

| Rule | Test window | Sharpe (1 bp a side) | 95% CI | CAGR | Trade days / week | 5 bps Sharpe |
|---|---|---|---|---|---|---|
| D3 noise area + VWAP stop | 2024-06 → 2026-09 (581 days) | +0.20 | [−1.24, +1.20] | +1.2% | 3.4 | −2.46 |
| D4 rest-of-day momentum | 2021-11 → 2026-09 (1,227 days) | −1.21 | [−2.13, −0.33] | −5.1% | 5.0 | −5.98 |

- D3 worked before its publication: 2016 to May 2024, Sharpe +0.75 [+0.20, +1.31] (reported, not deciding). After
  publication it fell to +0.20 (QQQ +0.72, SPY −0.62), well short of the pass bar. That is the pattern of a
  published edge being traded away, the same as D1. Its window is short, so this is weak evidence either way.
- D4, the hedging-demand version of intraday momentum, loses as D1 did (−1.21), and was already below 0 before its
  publication (−0.26).
- Four published day-trading rules have now failed after publication. The track stays at 0%. A fifth rule would
  need a reason to expect it to survive publication (e.g. a structural cause that cannot be arbitraged), stated
  before it is tested.

**Correction (2026-09-28):** the pattern-day-trader rule mentioned above no longer applies. The SEC approved
FINRA's Rule 4210 amendments on 14 Apr 2026, effective 4 Jun 2026: no $25k minimum and no day-trade count;
intraday margin is checked in real time, and a margin account needs $2,000. Alpaca adopted the new framework on
4 Jun 2026. The account-size limit on this track is gone; the results above are unchanged, since they never
depended on it.

### Day-trading round 4: box theory and intraday periodicity (spec fixed 2026-09-29 ~05:20 UTC, before any D6–D8 code ran)

The user asked to try "box theory" and the other researched rules. "Box theory" means two different things, so both
are tested; the third rule is the researched cross-sectional one that needs no new data. Each is its own trial, run
once, on data already cached (SPY/QQQ 1-minute bars, the S&P 500 30-minute 14:30/15:30 bars, daily OHLCV). No
parameter below was fitted to data; each comes from the source or is stated here once.

**Multiple testing:** these are trials 6–8 of the day-trading track. Pass for each: annualized Sharpe of the daily
P&L ≥ 0.5 at the costs below AND the lower bound of a **98.3%** block-bootstrap CI (21-day blocks; 95% with a
Bonferroni split over the 3 trials of this round) above 0.

- **D6, "box theory" (previous-day range; popular on social media since 2024, floor-trader support/resistance
  lore before that)**, SPY and QQQ, equal capital, 1× per symbol, at most one trade per symbol per day:
  - Box: yesterday's regular-session high H and low L; middle M = (H + L) / 2; width W = H − L. If today's 09:30
    open is outside [L, H] (a gap), the box is redrawn from today's 09:30–09:59 high and low and checks start at
    10:00; otherwise checks start with the 09:30 bar.
  - Each minute until the 15:29 bar, while flat: close ≤ L + 0.25W → long; close ≥ H − 0.25W → short; the middle
    is never traded. Entry at the next bar's open.
  - Exit (at the next bar's open) when the close reaches M (target) or goes beyond the box by 0.25W (stop: close
    < L − 0.25W for a long, > H + 0.25W for a short); otherwise at the 15:59 close. 1 bp a side.
  - Decides on 2016-01-05 → 2026-09-25 (no publication date; no parameter was fitted). Reported: 2024-10 onward,
    5 bps, each symbol. Trial `daytrade_box_theory`.
- **D7, intraday periodicity** (Heston, Korajczyk and Sadka, *Journal of Finance* 2010: a stock's return in a given
  half hour repeats in the same half hour on following days, for weeks), S&P 500 list of D5, the 15:30–16:00 slot:
  - Signal: the mean of the stock's 15:30-bar returns (open to close) over its previous 20 full trading days (at
    least 15 present). At 15:30: long the top 10%, short the bottom 10%, equal weight, dollar neutral, 15:30 bar
    open to close. Half days dropped as in D5. 1 bp a side per stock; also reported at 3 bps.
  - Decides on 2016-02 → 2026-09-25 (all after publication). Survivorship bias is small here: the rule is
    long/short and the list is fixed. Trial `daytrade_periodicity`.
- **D8, Darvas box** (Nicolas Darvas, *How I Made $2,000,000 in the Stock Market*, 1960): a swing rule on daily bars,
  not a same-day rule, long only, D5's S&P 500 list:
  - A box can start only on a day whose high is a 52-week high (≥ every high of the previous 252 days). Box top T =
    that high once the next 3 days' highs all stay below it (a higher high first restarts the search). Box bottom B
    = the lowest low after the top, fixed once 3 days in a row each have a low above it. While T and B are set,
    a close above T is a breakout; a close below B before that cancels the box.
  - Buy on a breakout at the next open; hold. The stop is B; each new box that completes above while held moves
    the stop to its bottom. A close below the stop → sell at the next open.
  - Portfolio: 20 slots of 5% each; the money in free slots is held in SPY. More breakouts than free slots: highest
    close / T first. 10 bps a side per trade (stock trades, slippage included).
  - P&L measured as the daily return of the book minus SPY's. Decides on 2024-07-01 → 2026-09-24 (the list is the
    2024 membership, so earlier years are survivorship-biased in the rule's favour; 2016–2024-06 reported only).
    Trial `daytrade_darvas_box`.
- **If one passes:** a month of clean dry runs on paper, then the evidence ladder, as before. **If all fail:** the
  track stays at 0% and no variant of D6–D8 is tried.

- **Result (run once, 2026-09-29 ~05:45 UTC): all three FAIL** (Sharpe at the stated costs; 98.3% CI):

  | Rule | Window | Sharpe | 98.3% CI | CAGR | Before costs | Reported |
  |---|---|---|---|---|---|---|
  | D6 box theory (SPY+QQQ) | 2016-01 → 2026-09 (2,695 days) | **−0.91** | [−1.58, −0.21] | −5.7% | ≈ −0.3 bp a day | SPY −0.68, QQQ −0.87; since Oct 2024 −0.51; 5 bps −4.07 |
  | D7 intraday periodicity | 2016-02 → 2026-09 (2,658 days) | **−0.07** | [−1.09, +0.74] | −0.1% | +2.0 bp a day (long +1.0, short +2.9) | since Jul 2024 +0.57 [−1.66, +2.52]; 3 bps −8.71 |
  | D8 Darvas box (excess vs SPY) | 2024-07 → 2026-09 (561 days) | **−0.30** | [−1.84, +1.22] | −4.7% | — | book 13.2%/yr vs SPY 18.0%; 2016–24 excess +0.08 (survivorship-biased); corr with core −0.25 |

  - D6: fading yesterday's range earns about nothing before costs and trades almost every day, so it loses the
    costs; its CI is entirely below 0 even at the stricter level. Box theory as popularly taught has no edge on
    SPY/QQQ.
  - D7: the periodicity effect is real before costs (+2.0 bp a day, the biggest raw edge of the eight rules), but a
    round trip on both legs costs 2 bp a day, so it nets 0. Only a lower-cost way to trade it (e.g. closing-auction
    orders with near-zero spread) could make it pay; that would be a new, separately registered test, not a D7
    variant.
  - D8: the Darvas book trailed SPY by about 5 points a year, even with the survivorship bias in its favour before
    2024.
  - Code review note (Codex, 2026-09-29): on a sell day the freed slot also earns SPY's overnight move, and on a buy
    day the slot misses it. The two roughly cancel (as many buys as sells) and are far inside the CI; not re-run.
  - Eight published day-trading rules have now failed. The track stays at 0%; no variant of D6–D8 will be tried.

### Day-trading round 5: D9 opening-auction reversal (spec fixed 2026-09-29, before any D9 code ran)

Why this one: D5 and D7 had real edges before costs that crossing the spread twice a day ate. D9 trades only in the
opening and closing auctions (market-on-open in, market-on-close out), where there is no spread to cross, and has a
stated structural cause: attention-driven retail buying at the open (Berkman, Koch, Tuttle and Zhang, "Paying
Attention: Overnight Returns and the Hidden Cost of Buying at the Opening", *JFQA* 2012) pushes up the stocks that
jumped overnight, and the push reverses during the day. Not a variant of D5 (a 15:00 signal, last half hour) or
D7 (same-slot history): the signal and the window differ.

- **Rule:** each day, overnight return = today's open / yesterday's close − 1 (daily bars; open and close are the
  auction prints). Short the 10% of stocks with the highest overnight return, long the 10% with the lowest, equal
  weight, dollar neutral (each leg half the capital); enter at the open, exit at the close. Needs 20+ stocks with
  both prices.
- **Universe:** D5's S&P 500 list (2024 earnings reporters). Daily OHLCV already cached.
- **Costs:** 1 bp per side per stock (auction fills: fees, no spread); also reported at 2 bps.
- **Test window: 2016-01-04 → 2026-09-24** (after the 2012 publication). Reported: 2024-07 onward (the list's own
  membership period), each leg, correlation with the core.
- **Pass (trial `daytrade_open_reversal`):** annualized Sharpe ≥ 0.5 and 95% block-bootstrap CI (21-day blocks)
  above 0, at 1 bp a side. One trial in this round, so no Bonferroni split.
- **If it passes:** a live paper shadow with MOO/MOC orders (placed by the existing 5:45 AM PDT events run, which is
  before the 9:30 ET open) for a month of clean runs, then the evidence ladder. **If it fails:** no variant.
- **Result (run once, 2026-09-29): PASS on the registered data.** 2016-01 → 2026-09, 2,697 days, median 474 stocks:
  Sharpe **+0.81** [+0.21, +1.34] at 1 bp a side, CAGR +7.0%, hit rate 53%, worst month −7.9%; at 2 bps Sharpe
  +0.24. Legs (gross, bp a day): long +7.2, short +2.5. Since 2024-07: +0.53 [−0.99, +1.82]. Correlation with the
  core −0.04.
- **Validity check (stated 2026-09-29 after the pass, before running it):** a bad opening print in the free daily
  data would fake exactly this pattern (a false jump overnight that "reverses" by the close), and the 2024 list
  flatters the long leg before 2024. So D9 goes to paper only if the same rule, on Alpaca's SIP daily bars (official
  regular-session open and close, split- and dividend-adjusted; an independent source), also has Sharpe ≥ 0.5 and a
  95% CI above 0 at 1 bp a side over the same window. Reported: the result with the top and bottom 1% of stock-day
  open-to-close returns removed from both legs. If the check fails, the pass is treated as a data artifact.
- **Validity check result (run once, 2026-09-29): HOLDS.** Alpaca SIP daily bars, same window: Sharpe **+1.08**
  [+0.47, +1.63], CAGR +9.8%; legs long +8.3, short +3.5 bp a day; with the top/bottom 1% of stock-day returns
  removed +1.70 [+1.07, +2.26], so it is not bad prints. Since 2024-07: +0.57 [−0.97, +1.87].
- **Implementation flaw found (2026-09-29):** the signal uses the official open, which is set by the opening
  auction itself, so a market-on-open order cannot be conditioned on it. D9 as tested is not tradable as written;
  the paper plan above is withdrawn. A tradable version is D10.

### Day-trading round 6: D10 tradable opening reversal (spec fixed 2026-09-29, before any D10 data or code)

- **Rule:** as D9, but the signal is known before the open: pre-market return = the last trade at or before 09:25
  ET (the close of Alpaca's SIP 08:00-09:25 bar, one 85-minute bar per stock) / yesterday's official close − 1.
  Stocks without a pre-market trade in 08:00-09:25 that day are left out. Short the top 10%, long the bottom 10%,
  equal weight, dollar neutral; enter at the official open (market-on-open), exit at the official close
  (market-on-close). Prices: Alpaca SIP daily bars (split- and dividend-adjusted) and the pre-market bars
  split-adjusted, with the signal's previous close taken split-adjusted too (dividend days: the ex-dividend drop is
  in the signal, a small bias against nothing in particular; reported only).
- **Universe, window, costs, pass:** D5's list; 2016-01-04 → 2026-09-24; 1 bp a side (also 2 bps); Sharpe ≥ 0.5 and
  95% block-bootstrap CI above 0. Trial `daytrade_open_reversal_premarket`. Needs 20+ stocks a day.
- **If it passes:** a live paper shadow: the existing 5:45 AM PDT events run waits until 09:25 ET, reads the
  pre-market prices, places MOO/MOC orders on the paper account (no new timer), for a month of clean runs, then
  the evidence ladder. **If it fails:** no variant; D9 stays an untradable finding.
- **Result (run once, 2026-09-29 ~00:33 PDT): FAIL.** 2016-01 → 2026-09, 2,696 days, median 231 stocks with a pre-market
  price: Sharpe **−0.64** [−1.28, −0.06] at 1 bp a side, CAGR −6.5%; at 2 bps −1.15. Legs (gross, bp a day): long
  +0.8, short −1.8. Since 2024-07 −0.92. D9's edge exists only against the official opening auction price itself,
  which cannot be known when a market-on-open order must be placed: the pre-market price does not predict the
  day's reversal. D9 stays an untradable finding; no variant.
- **Stress report (2026-09-29 late morning PDT; reported, not deciding; Alpaca data):** D9 at 1/2/3 bps a side: Sharpe
  +1.08 / +0.53 / −0.03; positive in every year 2016–2025 (+0.26 in 2019 to +3.16 in 2017), +0.06 in 2026 so far.
  D10 at 1/2/3 bps: −0.63 / −1.15 / −1.66; negative in 8 of 11 years. The effect lives in the auction print and needs
  near-zero costs; neither changes the verdicts above.

### Crypto funding carry C1 (spec fixed 2026-09-29 ~14:48 PDT, before any funding data was downloaded or viewed)

The user asked for research on making more money. Not yet tested here: the crypto cash-and-carry (long BTC/ETH spot,
short the perpetual future, collect the funding that leveraged longs pay). Published: Schmeling, Schrimpf and Todorov,
"Crypto Carry", BIS WP 1087 (April 2023; *Management Science*), who already report that it fades from 2024 and is
negative in 2025. Market-neutral, so it is judged against cash, not SPY.

- **Data:** Deribit public API, `get_funding_rate_history`, hourly `interest_1h` for BTC-PERPETUAL and ETH-PERPETUAL
  (free; Binance blocks US users). A short perpetual receives the funding when it is positive.
- **Rule:** each Monday 00:00 UTC, per asset: if the mean hourly funding over the previous 7 days is > 0, hold the
  carry (long spot, short perpetual, equal notional) for the next 7 days, else be flat. Half the sleeve per asset.
  Capital per asset = spot notional + 25% margin on the perpetual, so return on capital = funding / 1.25.
- **P&L:** daily sum of the hourly funding while held. Not modeled (reported as a limitation): changes in the
  spot-perpetual basis (small at a weekly horizon, since funding pulls the perpetual to the index), exchange risk.
- **Costs:** each switch in or out of an asset's carry costs 15 bps of its notional (spot 10 bps + perpetual 5 bps).
- **Pass (trial `crypto_funding_carry`):** on **2023-05-01 → 2026-09-24** (after the BIS paper), the annualized
  Sharpe of daily returns **in excess of the 3-month T-bill** (FRED DTB3, cached) ≥ 0.5 and its 95% block-bootstrap
  CI (21-day blocks) above 0. Reported: each year, each asset, time in the trade, return before costs, before the
  window.
- **Tradability (stated now):** Deribit does not serve US residents; a pass would be a paper shadow first, and a real
  version would need a US venue (e.g. Coinbase's US perpetual-style futures) and a mandate change by the user.
  **If it fails:** no variant.
- **Clarification (2026-09-29 ~14:53 PDT, before any funding data was downloaded):** "annualized" uses √365, since the
  carry earns on every calendar day (stock tests use √252 trading days); CAGR uses 365 days a year.
- **Result (run once, 2026-09-29 ~14:55 PDT): FAIL.** 2023-05-01 → 2026-09-24 (1,243 days): Sharpe in excess of
  T-bills **−1.63** [−4.73, +1.37], excess CAGR −0.6%; raw carry +2.5% a year after costs (+5.0% before costs),
  below the T-bill rate. By year (excess Sharpe): 2023 +1.96, 2024 +3.43, 2025 −7.20, 2026 −8.56. Held 86% of days
  (BTC), 72% (ETH). Before the window (2019-04 → 2023-04): +4.06 [+1.53, +6.59]. Mean funding, annualized: BTC 10.3%
  (2024) → 5.4% (2025) → 2.1% (2026); ETH 8.4% → 1.0% → 1.3%. The premium the BIS paper documented has been
  arbitraged down to below cash since the spot ETFs and basis funds arrived, as the paper itself reports for 2025.

### Turn-of-the-month T1 (spec fixed 2026-09-29 ~15:17 PDT, before any turn-of-month code or number)

**Idea.** US stocks earn much of their return over the four trading days around the month change (Lakonishok and
Smidt 1988; Ariel 1987; McConnell and Xu, FAJ 2008, data through 2005). **Stated cause:** month-end cash needs.
Institutions sell near the month end to meet payments, and salaries, pensions and fund inflows are invested at the
start of the month (Ogden 1990; Etula, Rinne, Suominen and Vaittinen, RFS 2020 "Dash for cash"). The dates are
known in advance, so there is no look-ahead. Nothing about this effect has been computed in this repo before. The
SPY and BIL closes in `data/trend/etf_closes.parquet` were seen before, in the trend test.

**Use if it passes.** None now: the mandate caps gross exposure at 1.0, so timing can only lower the book's
exposure. A pass makes it a candidate for timing extra exposure once the leverage ladder
(DEV_PLAN_AUTONOMOUS Phase D) unlocks gross above 1. That change would be proposed to the user, not made.

**Rule (one trial, `turn_of_month`).**
- Data: SPY adjusted closes, `data/trend/etf_closes.parquet`. Risk-free: 3-month T-bill (`vol_target_b0.tbill()`)
  / 252 per trading day. x_t = SPY close-to-close return minus rf.
- Turn-of-month (TOM) days: the last trading day of a month and the first 3 trading days of the next, i.e. hold
  from the close of the 2nd-last trading day to the close of the 3rd trading day. Trading days come from the data's
  own index.
- Overlay stream o_t = x_t on TOM days, 0 otherwise. Cost: 1 bp a side, taken on the entry day (the first TOM
  day) and the exit day (the 3rd trading day).
- Window: 2008-01-02 .. 2026-09-25 (after McConnell and Xu's sample and publication).

**Pass (both needed, 95% level, one trial):**
1. The overlay's annualized Sharpe (sqrt 252, `stats()`) is >= 0.5 AND its 21-day block-bootstrap CI is above 0.
   This is the track's usual rule.
2. The TOM effect itself: the mean excess on TOM days minus the mean on other days has a 95% block-bootstrap CI
   (21-day blocks, 5000 draws, seed 0) above 0. Rule 1 alone can pass on the plain equity premium.

**Reported, not deciding:** Sharpe and difference by year and for 2020-07+ (after Etula et al.); the timed book
(SPY on TOM days, T-bills otherwise) against SPY; 3 bp costs; correlation with the core book.

**Result (run once, 2026-09-29 ~15:18 PDT): FAIL.** Overlay Sharpe +0.30, CI [−0.10, +0.74], CAGR 2.1% (3 bp:
+0.24). TOM days averaged 5.5 bp of excess against 4.2 bp on other days: a difference of +1.3 bp a day, CI
[−6.6, +9.4], over 899 TOM days. Both rules miss. The overlay's Sharpe is about what plain SPY exposure on 1 day in
5 would give. The difference changes sign from year to year (+31 bp in 2010 and 2026, −23 bp in 2024). Since
2020-07: overlay +0.47 [−0.28, +1.09], difference +2.4 bp [−10.4, +13.2]. Timed book (SPY on TOM days, T-bills
otherwise): Sharpe 0.47 and CAGR 3.5%, against SPY's 0.64 and 11.3%. Correlation with the core book: 0.36. Read: the
classic turn-of-month premium has not been there in SPY since its 2008 publication.

### SPY overnight premium O1 (spec fixed 2026-09-29, evening PDT, before any O1 code or number)

**Idea.** Most of the US market's return has come outside trading hours, from the close to the next open. Sources:
Cooper, Cliff and Gulen 2008; Kelly and Clark, *J. Asset Management* 2011 (SPY overnight Sharpe about 1.27 a year
before costs, data to 2008); Boyarchenko, Larsen and Whelan (NY Fed SR 917) on the overnight drift. **Stated cause,
as proposed by the authors, not established:** active traders cut positions before the close because they see
overnight risk as higher, which pushes closing prices down. Found by Codex's screen (29 Sep, papers checked on the
web). Nothing about SPY's overnight/intraday split has been computed in this repo. D9/D10 were cross-sectional
opening-gap reversals, a different rule.

**Rule (one trial, `spy_overnight`).**
- Data: `data/statarb/sector_etfs.parquet`, SPY Open/Close (Yahoo, adjusted for dividends and splits, so a hold over
  an ex-date is credited with the dividend). Risk-free: `vol_target_b0.tbill()` / 252 per trading day.
- Every trading day t with a previous close: return o_t = Open_t / Close_{t−1} − 1. Held through weekends and holidays.
  Buy at the close (market-on-close), sell at the next open (market-on-open); dates and orders are fixed in advance.
- Cost: 1 bp a side, 2 bp per night. Net excess x_t = o_t − 2 bp − rf_t.
- Window: 2012-01-03 .. 2026-09-25 (after Kelly and Clark's publication).
- Check before the test counts: on 2016+ the daily Open/Close must match `data/intraday/SPY_1min.parquet`'s first
  09:30 open and 15:59 close to within 0.5% on 99% of days (after the adjustment ratio). If not, stop and fix the data
  first, then run once.

**Pass (95%):** the annualized Sharpe of x_t (sqrt 252, `stats()`) is ≥ 0.5 AND its 21-day block-bootstrap CI is
above 0. This is the track's usual rule.

**Reported, not deciding:** gross Sharpe; 3 bp costs; the intraday leg (open to close); buy-and-hold SPY; by year;
2020+; correlation with the core book.
**Use if it passes:** nothing now. Under the mandate (gross ≤ 1.0), overnight-only SPY earns less than holding SPY all
day unless the intraday leg is ≤ 0. A pass makes it a candidate for the leverage ladder (higher Sharpe per unit of
risk), proposed to the user, not adopted.

**Result (run once, 2026-09-29 evening): FAIL.** At 1 bp a side, net excess Sharpe was +0.25, CI [−0.28, +0.81],
CAGR 2.1%; at 3 bp it was −0.70. Before costs the overnight leg is real, +0.73 [+0.15, +1.36] (7.4% a year).
The intraday leg was +0.35 and buy-and-hold +0.84. Two trades a night (5% a year at 1 bp a side) eat most of it.
Since 2020: +0.14. By year the sign flips (2022 −1.5, 2024 +1.4). The data check passed: 99.3% of 2,689 days
matched the minute bars. Correlation with the core book: 0.54. Built by Codex (6 Luna) from this spec; Claude
reviewed and ran it.

### Macro-announcement premium E1 (spec fixed 2026-09-29 evening PDT, before any E1 code, calendar or number)

**Idea.** Stocks earn much more on days when scheduled macro news comes out: 11.4 bp against 1.1 bp on other days
(Savor and Wilson, JFQA 2013). **Stated cause:** investors are paid for holding stocks through the resolution of
macro uncertainty (inflation, jobs, the Fed); it is a risk premium, not a mispricing. Found by Codex's screen
(29 Sep, paper checked). **Known risk:** the pre-FOMC drift alone faded after 2015 (Kurov, Wolfe and Gilbert).
Nothing about announcement days has been computed in this repo. T1 (month turn) is a different calendar.

**Rule (one trial, `macro_announcement`).**
- Events: first releases of the **Employment Situation** (BLS) and the **PPI** (BLS), and **scheduled** FOMC
  statement days. Unscheduled FOMC moves are excluded. For two-day meetings the statement day is the second day.
- Dates: the Employment Situation and PPI first-release dates come from ALFRED's release-date lists (free; release
  ids 50 and 46). Only each reference month's first release counts, not revisions. FOMC statement dates come from the
  Fed's historical calendars (free). A date is used only if it was a scheduled release (announced ahead). Calendar
  checks before any returns: 11–13 jobs and PPI dates a year, and 8 FOMC dates a year (2020 may differ; list any
  exception).
- Position: long SPY from the close of the trading day before an event day to the close of the event day. Adjacent
  event days are merged into one holding. Otherwise T-bills (excess 0). 1 bp a side per holding.
- Data: SPY adjusted closes (`data/trend/etf_closes.parquet`); risk-free `vol_target_b0.tbill()` / 252.
- Window: 2013-05-01 .. 2026-09-25 (after the paper's publication).

**Pass (both, 95%):**
1. The strategy's net daily excess (0 on non-event days) has annualized Sharpe ≥ 0.5 AND a 21-day block-bootstrap
   CI above 0.
2. Mean SPY excess on event days minus mean on other days has a 95% block-bootstrap CI above 0 (21-day blocks,
   5,000 draws, seed 0; `calendar_fx.diff_ci`).

**Reported, not deciding:** each event type alone (diagnostic only; no subset is ever promoted), by year, 3 bp
costs, correlation with the core book.
**Use if it passes:** a leverage-ladder candidate only, proposed to the user. Under gross 1.0 it holds SPY on only
about 32 days a year.

**Result (run once, 2026-09-29 ~19:35 PDT): FAIL.** At 1 bp a side the net Sharpe was +0.08, CI [−0.42, +0.66],
CAGR 0.3%; at 3 bp it was −0.11. Announcement days averaged 3.5 bp of excess against 5.4 bp on other days: a
difference of −1.9 bp a day, CI [−13.6, +9.1], over 416 event days. The premium the paper found (+10 bp) is gone
since publication. By type, diagnostic only: jobs +0.20, PPI −0.17, FOMC +0.10, all CIs spanning 0. Four
jobs-report dates fell on Good Friday (market closed) and were left unmapped, as the spec says. Calendar: 12
jobs, 12 PPI and 8 FOMC dates a year, matching BLS for 2015 and 2024; 2020 and the 2025 shutdown are documented in
`data/macro/announcements.csv`. Built by Codex (6 Luna, session e1); Claude reviewed and ran it.

### T1 and O1 forward shadows (spec fixed 2026-09-29 evening PDT, before any forward day; user: "set up the T1 and O1 shadow books")

Both failed their one backtest (T1 +0.30, O1 +0.25 net). Neither is re-tested or changed. They are tracked on
future days only, with the frozen code, and no money: `app/sandbox/calendar_fx.tom_overlay` (T1, 1 bp a side)
and `app/sandbox/overnight.overnight_legs` / `overnight_excess` (O1, 1 bp a side). Both are excess returns over
the 3-month T-bill.
- **Forward window:** trading days from **2026-09-30** on. Data: SPY daily Open/Close from Yahoo (adjusted), fetched
  fresh at each weekly review. The T-bill comes from FRED DTB3 (free).
- **Reported every Saturday** (weekly review): days tracked, cumulative net excess return, annualized Sharpe so far
  and its 95% CI once 60+ days exist. Reported only; nothing moves money.
- **Verdict on 2028-09-30** (two years, about 500 nights for O1 and 100 TOM days for T1), with each test's own
  pass rule: net Sharpe ≥ 0.5 AND 95% 21-day block-bootstrap CI above 0. A pass there is a candidate for the
  leverage ladder only, proposed to the user. No earlier verdict, and no rule change on the way.

### Core leads, forward check (spec fixed 2026-09-29, midday PDT, before any forward data)

The user asked to raise the book's Sharpe. The two known ways were already tested on 2018–26 and failed narrowly,
recorded as leads for a forward test: the 20% volatility target on B0 (Sharpe +0.06 [−0.14, +0.25]) and CVaR risk
parity (+0.12 [−0.01, +0.23]). They are not re-tested on old data. Both break the mandate (1.0× gross, 20% crypto;
only the user changes it), so they cannot be paper books; they are tracked as computed shadows instead.

- **What:** `scripts/core_leads.py` recomputes, with the frozen code of each trial (`vol_target_b0.managed`,
  `skfolio_test.arm_s`), the daily returns of B0, B0 + vol target and arm S, and keeps only days **from
  2026-09-30** (the forward window; both rules use trailing data only). Reported in the Saturday review.
- **Verdict** at the first review on or after **2027-09-30** (about 250 forward days), each lead alone, with its own
  trial's rule: vol-matched CAGR above B0's AND the 90% block-bootstrap CI of the Sharpe difference above 0.
  Interim numbers are reported, never judged. Trials `core_lead_voltarget_fwd`, `core_lead_riskparity_fwd`.
- **If one passes:** it is proposed to the user as a mandate change (the only way it can trade); nothing changes on
  its own.

### Pairs trading P1 (spec fixed 2026-09-29 ~00:20 PDT, before any pairs code or data view)

The user asked for pairs trading (stat-arb failed; trend following is the core; market making needs co-location and
paid order-book data, so it is not attempted). PLAN_STATARB left pairs as a separate new trial. Rule as published
(Gatev, Goetzmann and Rouwenhorst, *RFS* 2006), within sectors, on daily adjusted closes (cached yfinance):

- **Formation** (each month start): the previous 252 trading days; stocks of D5's S&P 500 list with no missing
  close. Prices normalized to 1 at the formation start. Within each sector, every pair's sum of squared differences
  (SSD); the 20 pairs with the smallest SSD overall are chosen.
- **Trading** (the next 126 trading days): when a pair's normalized spread is more than 2 formation standard
  deviations from 0 at a close, open the next day at the close (GGR's one-day wait): long the cheaper, short the
  dearer, equal dollars. Close at the close of the first day the spread crosses 0, or at the period's end; a pair
  can reopen. Each portfolio's daily return = the mean over its 20 pairs (committed capital; a closed pair earns 0).
- **Book:** 6 overlapping portfolios (one started each month); the daily return is their mean.
- **Costs:** 10 bps per side per stock trade (both legs at open and at close).
- **Pass (trial `pairs_ggr`):** annualized Sharpe ≥ 0.5 and a 95% block-bootstrap CI (21-day blocks) above 0 on
  **2024-07-01 → 2026-09-24** (the list is the 2024 membership; earlier years are survivorship-biased, reported
  only: 2016-01 → 2024-06). Reported: correlation with the core, share of days invested, 0 and 20 bps costs.
- **If it passes:** paper sleeve after a month of clean dry runs, then the evidence ladder. **If it fails:** no
  variant (other thresholds, cointegration tests or universes would each be a new, separately justified trial).
- **Result (run once, 2026-09-29 ~00:30 PDT): FAIL.** 2024-07 → 2026-09 (561 days): Sharpe **−0.61** [−1.92, +0.91] at
  10 bps, CAGR −1.7%; at 0 bps −0.24, at 20 bps −0.98; half the pairs open on a typical day. Before the window
  (2016–2024-06, survivorship-biased): −0.37. Correlation with the core +0.08. Pairs trading in large caps has no
  edge left even before costs, as later studies of GGR found (Do and Faff 2010).
- **Code review note (Codex, 2026-09-29):** (1) prices are re-based to 1 at each trading period's start and the
  2-SD test uses that spread, as common GGR replications do; the spec did not say which base, so this is recorded as
  the implemented reading, not re-run. (2) The reported "invested" share counts an open pair on a zero-P&L day as not
  invested, so 52% is a slight undercount; reporting only.

### Long-term picks track (spec fixed 2026-09-28, before any pick was made)

Jan and Bonsai pick S&P 500 stocks to hold for 3 months. **Forward-only:** Bonsai was trained on text that covers
2024–26, so a backtest of its stock picks on those years would be flattered.

- **Candidates:** every S&P 500 company with an earnings release in the last 100 days (its latest release on disk).
- **Company card, built by code:** the latest release's first 4,000 characters, the previous quarter's outlook
  (arm B3's extractor), 12-month and 1-month returns vs SPY, and (once they exist) the live lens labels.
- **Bonsai reads each card** with a fixed prompt (`PROMPT_LT`): a 3-to-6-month view, `outlook_6m` from 1 (much
  worse than the market) to 5 (much better), with a quoted reason; an unverified quote counts as 3.
- **Picks:** on the first trading day of each month, the 10 highest ratings (ties: better 12-month return vs SPY
  first), equal weight, held 3 months (three overlapping monthly cohorts). Scored vs SPY, costs 0.2% per side.
- **Machinery gate:** a shadow at 0% for its first month. Then the planner's 10% "untested" rung.
- **Pass (trial `longterm_picks_forward`), judged after 12 monthly cohorts have closed:** the mean cohort excess
  return vs SPY after costs is positive with an 80% one-sided bootstrap bound above 0.

**Change before the first cohort (2026-09-28 ~18:00, no pick made yet):** at the user's request, `PROMPT_LT` now
has Bonsai write a bull case and a bear case (up to 3 quoted points each, same form as arm E) before it weighs
them and rates. The rating field, the quote check, the pick rule and the pass rule are unchanged; the theses are
stored with each rating and shown on the dashboard. Quality check (10 random cards, quality only): parsed 10/10,
both sides on every card, 54 of 60 quotes verified; 9 of 10 cards rated 4. Time: about 8 seconds per card (was 3),
so a monthly cohort of about 490 cards takes about 70 minutes of GPU time.

### Theme track and AI-bubble gauge (spec fixed 2026-09-28 ~18:45, before any cohort was made)

The user asked for the AI to weigh investment directions by horizon (long-term: biotech, quantum computing;
medium: hyperscalers) and risks such as an AI bubble. **Forward-only:** Bonsai knows how these themes did in 2024–26.

- **Themes (fixed, `app/portfolio/themes.py`):**
  - Medium, held 6 months (126 trading days): hyperscalers (equal-weight MSFT, AMZN, GOOGL, META, ORCL), semis
    (SMH), power and grid (GRID), software (IGV), cybersecurity (CIBR), utilities (XLU).
  - Long, held 12 months (252 trading days): biotech (XBI), quantum computing (QTUM), nuclear (NLR), robotics
    (BOTZ), space (UFO), solar (TAN), batteries and lithium (LIT).
  - AI-linked: hyperscalers, semis, power and grid.
- **Cards, all numbers computed by code** from data dated before the run: 1/3/12-month return vs SPY, drawdown from
  the 12-month high, price vs its 200-day average, 12-month volatility, and the market risk register:
  - hyperscaler capex over the last 4 quarters and its growth vs a year earlier, and capex as a share of operating
    cash flow (SEC filings);
  - market concentration: SPY minus RSP over 12 months;
  - SMH vs its trend;
  - high-yield spread and its 3-month change, and VIX (FRED).
- **Bonsai (`PROMPT_RISK`, `PROMPT_TH`):** for the register and for each theme it argues bull and bear first (up to
  3 quoted points each), then rates. The register gets `bubble_risk` (low / elevated / high); each theme gets
  `theme_outlook` (1–5). The usual quote check applies; an unverified quote counts as the middle answer.
- **Picks, monthly** (the long-term picks' schedule):
  - Per horizon, up to 2 themes rated 4 or 5 (ties: better 12-1 month momentum vs SPY).
  - When `bubble_risk` is high, AI-linked themes are left out.
  - No theme qualifies → that horizon stays in SPY (0 excess, no cost).
  - Equal weight; ETF cost 0.1% per side.
- **Code-only yardstick, recorded beside each cohort:** the 2 themes per horizon with the best 12-1 month momentum.
- **Pass (trial `themes_forward`), judged after 12 medium cohorts have closed (about 18 months):**
  - the mean excess vs SPY after costs is above 0 with an 80% one-sided bootstrap bound above 0;
  - AND it is above the momentum yardstick's mean.
  - Long cohorts are judged the same way after 12 have closed.
  - Cohorts overlap, so the bootstrap overstates certainty; the result is read with that caveat.
- **The bubble cap itself is not proven.** Bubbles are rare, so a few years of data can't test the gauge. It stays
  a shadow rule, reported on the dashboard. It changes no money in the master book, whose drawdown brakes stay the
  tested risk control.
- **Quality check** (today's cards; quality only, no returns):
  - Replies read: 14/14. Point quotes verified: 69 of 84. Run time: 95 seconds.
  - `bubble_risk`: high ("capex growth is extreme and semiconductor prices are stretched").
  - Picks today would be: medium none (the AI-linked themes were capped; the rest were rated 3 or lower), long
    biotech and quantum.

**Second change before the first cohort (2026-09-28 ~21:00, no pick made yet): ties broken by Bonsai's own
probabilities.** A full rehearsal (clock set to 1 Oct, scratch folder) rated all 10 picks 4, so the 12-month-return
tie-break chose them (5 of 10 were chip makers). Now each rating also gets a score: Bonsai's next-token
probabilities for 1–5 at the point where it wrote its rating (its own reply up to the label, continued once), as
a probability-weighted rating. Picks are ordered by that score, then by 12-month return. The same applies to the
theme ratings (a theme still needs a rating of 4 or 5). A rating the quote check reset to 3 keeps 3.0. Checked on
24 real cards: identical ratings, probabilities found for 21 (the other 3 were quote-check resets), scores
among the 4s spread from 3.63 to 4.03. Parallel requests (3 / 6 / 8) gave the same answers and no speed-up (about
9.5 s per card), so the monthly run stays at about 65–75 minutes.

### Aggressive book, 2.5× (spec fixed 2026-09-30 ~21:30 PDT, before any code for it; the user's decision)

**Why it exists.** On 30 Sep the user raised the target ("the baseline is 40% a year … 60% is much more desirable")
and asked for "much more aggressive" trading. Asked to pick a size from the leverage table (the core backtest replayed
with borrowed money: 2× = 30%/yr and a −48% worst fall, 3× = 39%/yr and −64%), the user chose **2.5×**, chose that the
Alpaca paper account should trade this book, and chose to keep the Aschenbrenner AI-build-out lens (arm D) as an
opinion only. This skips the Stage 4 ladder, which would have allowed leverage only from month 6 on evidence. It is the
user's mandate decision, not something a test supports: no test has shown this book earns its risk.

- **What stays frozen.** The frozen books (`master`, `master+brakes`, `SPY`, `80/20 SPY/BTC`) and every rule they use
  are unchanged and keep running in the simulator. `master+brakes` stays the forward test's book of record and the
  control this book is compared with.
- **The book (`aggressive 2.5x`), in the simulator:**
  - Starts with 100,000 paper dollars at the first allocator run after this commit (Mon 5 Oct 2026).
  - Target weights = 2.5 × the weights `master+brakes` would hold at the same run (the same allocation and the same
    drawdown-brake multiplier, taken from the `master+brakes` book's own drawdown), without the SGOV parking leg.
    With no brake that is SPY 1.95, crypto up to 0.50, gross up to 2.45.
  - Borrowing: cash may go negative down to the amount the targets need. Borrowed cash costs **5.0% a year**
    (T-bill 4.1% on 24 Sep 2026 plus about 1%), charged for each calendar day between runs, fixed here.
  - Same fills, costs (5 bp a side), whole shares and timing rules as the other books.
- **Its own limits** (mandate section `books.aggressive 2.5x`, committed with this spec on the user's instruction):
  max gross 2.5, max single weight 2.0, max crypto 0.5, **alert at 45% below its peak, limit at 60%**. The backtest's
  worst fall at 2.5× was −57%. At the limit the kill switch goes to REDUCING for every book, as it does for the frozen
  book at 35%. The user can change these numbers; they were not asked for separately.
- **The Alpaca paper account** mirrors this book instead of `master+brakes`, as far as the account's rules allow.
  The account lends 2× overnight on stocks and nothing against crypto, so the mirrored weights are scaled by
  `min(1, 0.98 / (0.5 × stock weight + crypto weight))`. At full size that is about 0.66, or roughly **1.6× at the
  broker against 2.5× in the simulator**. The gap is reported, not hidden. Reaching 2.5× at the broker would need
  leveraged ETFs, which are not in the mandate.
- **No pass rule.** This is not a trial and is not entered in the trials registry. The weekly review reports it beside
  `master+brakes`: return, drawdown, interest paid, and whether its return is tracking 2.5× the frozen book's return
  minus financing. Its numbers never count as evidence for the frozen book.
- **What the backtest arithmetic says to expect** (30 Sep, `desktop_export.leverage_table`): about 35% a year, 41%
  volatility, a −57% worst fall, a −47% worst year, a 46% chance of a 40% year, a 32% chance of a 60% year, a 25%
  chance of a losing year and a 31% chance of a 35% fall within any year. Live results are usually worse than backtests.
