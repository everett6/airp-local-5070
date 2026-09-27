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

## Mon 28 item 1: 20-day Bonsai satellite test (spec fixed before the run, 2026-09-27 00:45)

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

### Mon 28 item 1 result (2026-09-27 00:55): FAIL

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

## Mon 28 item 2: PC-off fallback rule (pre-registered 2026-09-27 00:55, before any forward data)

- **When it applies:** on a forward-test day, an earnings release whose decision is not written by Bonsai-27B by the next open (PC off, GPU busy or a crash).
- **What happens:** Bonsai-lite (`scripts/bonsai_lite.py`, the ridge trained on every Bonsai decision known before that day) scores the release on CPU.
- **Flags:** the decision is flagged `source=lite` and scored as its own book, next to the Bonsai book. It is never mixed into Bonsai's IC.
- **Backfill:** never. If the PC comes back later, Bonsai does **not** re-decide a release that lite already traded. A missed day with no lite run either is marked missed.
- **Promotion rule, checked at the end of the forward test:**
  - Lite becomes the default only if its forward IC is within 0.03 of Bonsai's on the same releases, AND its 90% CI is above 0.
  - Otherwise it stays a fallback only.
- **The weight is fixed in advance:** lite picks get half of Bonsai's Kelly weight. It was the weaker arm in the back test (IC 0.086 vs 0.103).

## Optimization research (2026-09-27 01:05, user request: "do research for the optomizations")

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
  - [Benchmarks](https://particula.tech/blog/ollama-vs-vllm-comparison) show vLLM about matching Ollama at 1 request, but up to about 16–19× the throughput at high concurrency.
  - Serving Bonsai-27B on vLLM (as Jan already is) with 8–16 in flight could cut the 3 h brief phase to well under 1 h.
  - **Needs your OK:** it needs a vLLM-loadable copy of Bonsai (a download), and a check that it fits in 16 GB VRAM at 4-bit.
  - Not done tonight.
- **Spike cause briefs** (88 triggers): at about 12 s each they take about 18 min. No change needed.

## Optimization 1: volatility target on B0 (spec fixed before the run, 2026-09-27 01:05)

- **Rule:** B0's daily returns (2018-01-02 .. 2026-09-24) are scaled by exposure e_d = min(1.5, 20% / σ̂_d).
  - σ̂_d is the annualized 20-day realized vol of B0, up to the close of d−1.
  - **Band:** e only changes when the new value is more than 0.10 away from the current one.
  - **Cost:** 10 bps × |Δe|. Leverage above 1 pays financing of rf + 1.5% per year on the borrowed part.
  - rf is the 3-month T-bill, FRED DTB3.
- **Pass (the adoption rule):**
  - scaled to B0's realized vol, the managed book has the higher CAGR, AND
  - the 90% CI of the Sharpe difference is above 0 (63-day block bootstrap).
- **Also reported, not in the verdict:** the same comparison with the drawdown brakes applied on top.

### Optimization 1 result (2026-09-27 01:15): FAIL

| 2018–26 | CAGR | vol | Sharpe | max DD |
|---|---|---|---|---|
| B0 | 21.6% | 20.6% | 1.05 | 33.8% |
| vol-targeted | 22.8% | 20.3% | 1.11 | 30.7% |
| vol-targeted at B0's vol | 23.1% | 20.6% | 1.11 | 31.1% |

- The Sharpe difference is +0.06, with a 90% CI of [−0.14, +0.25]. With the brakes it is +0.04 [−0.16, +0.24].
- The direction is right: +1.5 points of CAGR and 3 points less drawdown. But the gain is inside the noise, which matches Cederburg et al.
- Exposure averaged 1.17×, and was above 1 on 71% of days.
- **Not adopted as a rule.** It goes to Stage 4 as a lead: if the forward test agrees, it combines with the leverage table.
