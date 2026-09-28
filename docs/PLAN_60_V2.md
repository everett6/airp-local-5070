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
