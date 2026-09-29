# Plan V3: from paper tests to evidence (written 2026-09-28)

This plan replaces the "what next" parts of `PLAN_60_V2.md`. That file stays the record of every pre-registered
test and result; nothing here changes a registered rule. Paper money only; no paid data; no real-money trades.

## 1. Where things stand (28 Sep 2026)

**Goal (saved):** $10,000 → $25,000 by 31 Dec 2029, which needs **32.5% a year**.

| | Today's evidence |
|---|---|
| Best evidence-based mix (90% core, 10% AI-picks sleeve) | median **$15,962** (15.4%/yr), odds of the goal **5%** |
| Core alone (SPY + crypto trend, with brakes) | median $16,854, odds 9%; backtest 17.7%/yr, max drawdown 27% |
| Gap | **17 points a year** |

**What has been tested (all pre-registered, all on data the models could not have seen, or forward-only):**

| Track | Status | Evidence |
|---|---|---|
| Core: SPY + BTC/ETH trend, drawdown brakes | **working** | the only track with a passing backtest |
| Event picks (Jan research + Bonsai, 5 days) | untested, 10% paper sleeve from 5 Oct | arms A–D and B2/B3 failed; research adds ≈0 over the release alone |
| Live AI reads: net_read (C2), AI build-out lens (D), bull/bear (E) | shadow, no money | forward-only; judged at 150 releases + 3 months |
| Long-term picks (top 10 of the S&P 500, 3 months) | shadow; first cohort Thu 1 Oct | forward-only; judged after 12 cohorts |
| Themes by horizon + AI-bubble gauge | shadow; first cohort Thu 1 Oct | forward-only; judged after 12 six-month cohorts |
| Day trading | **failed** (9 tradable rules) | D1 −1.00, D2 −0.24, D3 +0.20, D4 −1.21, D5 −1.86, D6 box −0.91, D7 −0.07, D8 Darvas −0.30, D10 −0.64; D9 passed (+0.81) but can't be traded (needs the auction's own open price) |
| Pairs trading | **failed** | P1 (GGR) −0.61 at 10 bps, −0.24 before costs |
| Crypto funding carry | **failed** | C1 −1.63 vs T-bills after 2023 (was +4.06 before; funding now below cash) |
| Private companies | not built | later stage |

**The honest reading:** the system is well built and tests itself honestly, but so far only the core has an edge.
Every AI stock-picking idea is either failed or still unproven. The next months are about collecting live evidence,
not about building more.

## 2. This week (28 Sep – 5 Oct): go live on paper

| When (PDT) | What | Who | Done when |
|---|---|---|---|
| Mon 28 Sep, evening | Thursday rehearsal (real long-term + theme runs, clock set to 1 Oct, scratch folder) | Claude | both finish, cohorts written, time measured |
| Mon 28 Sep, evening | Speed test: 3 / 6 / 8 parallel Bonsai requests; check the new rating-probability reader on Bonsai | Claude | fastest setting whose ratings match 3-at-once; setting applied to long-term, themes and live lenses (not the frozen event scorer) |
| Tue–Fri, 5:45 AM and 3:30 PM | Dry event runs (need **8 clean of 10, at most 1 failure** by Friday) | automatic | heartbeat shows rc 0 |
| **All week** | **Keep the PC on and awake** (runs don't happen while it sleeps) | **you** | — |
| Thu 1 Oct, 3:30 PM | First real long-term cohort (~490 cards) and theme cohort | automatic | check rating spread, 10 picks, bubble gauge, no alerts |
| Fri 2 Oct, 6:00 PM | Automatic go-live check | automatic | "switched to LIVE" alert, or a stated reason to stay dry |
| Sat 3 Oct, 7:00 AM | Weekly review (now lists every AI test) | automatic | review file written |
| Mon 5 Oct | Paper trading for real: core (90%) + AI-picks sleeve (10%) | automatic | first fills match the plan; broker mirror clean |

If the go-live check says "still dry", Claude fixes the cause and you can switch by hand
(`scripts/autonomy.sh live`) once it's clean.

## 3. October – December 2026: collect evidence

Earnings season (mid-Oct to mid-Nov) gives the live AI reads most of their releases.

| Month | Milestone | What it can change |
|---|---|---|
| Oct | Daily runs; weekly reviews; fix anything that breaks | nothing moves money |
| early Nov | Long-term and theme tracks: first clean month → **10% paper each** (the "untested" rung) | planner weights |
| Nov | Second long-term and theme cohorts; first live-lens IC readings (not yet judgeable) | nothing |
| late Dec / early Jan | **First judgements:** live lenses C2, D, E reach 150 releases + 3 months; AI-picks sleeve has 3 months forward | a passing lens can join the event score (blend-gain test); the sleeve stays or goes |
| ~30 Dec | First long-term cohort closes (63 trading days) | reported only; judged after 12 |

**Monthly routine (first weekday after the close, automatic):** new long-term cohort, new theme cohort, bubble
gauge. **Weekly (Sat):** review. **Quarterly:** Claude reruns `goal_plan.py` and updates this plan.

## 4. 2027 – 2028: the evidence ladder decides

Money moves only by this ladder (planner, fixed): not built / failed / shadow **0%** → untested **10%** → passed +
3 months forward **25%** → 12 months forward **40%**. Stock picking at most 60% in total; the core at least 40%;
the whole book's max drawdown 35%.

| When (approx.) | Judgement |
|---|---|
| Jan 2027 | Live lenses (C2, D, E); AI-picks sleeve (3 months) |
| Mar 2027 | First six-month theme cohort closes |
| Dec 2027 | Long-term picks: 12 cohorts closed → pass/fail |
| Mar 2028 | Themes (medium): 12 cohorts closed → pass/fail, vs SPY and vs the momentum yardstick |
| Sep 2028 | Themes (long): 12 twelve-month cohorts closed |

A track that passes can reach 25% three months after passing and 40% after twelve. That is the only way picks earn
the weight the goal needs (see §6).

## 5. Research and engineering backlog (in priority order)

1. **Speed** (this week): parallel Bonsai requests (§2). Expected: the 70-minute monthly run drops a lot.
2. **Rating-probability scores** (built, needs a live check): breaks the many ties at rating 4 by Bonsai's own
   confidence. Recorded in PLAN_60_V2 before the first cohort.
3. **Started 29 Sep (user: "ok start"):** *B4*, the 20-day version of the research arm, tested only on 2026+ data
   (B2 looked better at 20 days: +0.029 [+0.009, +0.050]). Pre-registered before any run.
4. **Optional, needs your OK (a download, and it is Qwen-based):** *Kev 4B* speed test: a local decision model
   scores all ~490 cards in minutes; Bonsai writes bull/bear only for the top 30. Adopted only if its ratings
   match Bonsai's closely.
5. Day trading: **paused.** Eight published rules failed (round 4, 29 Sep: box theory, intraday periodicity, Darvas box; the periodicity edge is +2 bp a day before costs, exactly its costs). D5 (end-of-day reversal, market-neutral,
   with a stated structural cause) was the one with a reason it can't be traded away; the effect is real before
   costs but about 0.6 bp a day, a third of its trading costs. (The $25k rule is gone since 4 Jun 2026; it was never the reason they failed.)
6. Private companies: later stage (needs access and a data source).

## 6. The goal, honestly

- On today's evidence the goal has about **5–9% odds**. The median lands near **$16,000–17,000** by end-2029.
- Picking would need **55% a year at a 40% share** of the book to close the gap. Nothing tested so far comes close.
- Levers, by how reliable they are:
  1. **Adding money:** on the core's median path, about **$155–185 a month** added from now to end-2029 reaches
     $25,000 without any picking edge (arithmetic on the planner's median, not a forecast).
  2. **More time:** at the 15–18%/yr medians, $10,000 reaches $25,000 around 2032–2033.
  3. **A passing picking track:** possible, not provable in advance; it earns weight only through §4.
  4. **More risk** (leverage): the shadow 1.5×/2× books are tracked on paper; they raise the drawdown with the
     return, and the 35% max drawdown stays.
- Real money: not before a track passes and has 3 months of forward results. Then, if you want, a live mode with a
  $100 hard cap that **you** switch on with your own keys. Not financial advice.

## 7. Who does what

| You | Claude |
|---|---|
| Keep the PC on and awake for the scheduled runs | Watch every run; fix failures; weekly and quarterly updates |
| Decide on B4 and the Kev test (§5) | Pre-register every new test before running it; never rig one |
| Switch models: **Sonnet 5.5** for routine watching and fixes, **Opus 5.5** for new tests and verdicts | Report results as they are, pass or fail |
| Only you can: add keys, switch to real money, power off (only when you say) | Never place real trades, spend money, or shut down without your command |

## 7b. Mandate changes

- **2026-09-29, by the user:** SGOV (0–3 month T-bill ETF) added to the mandate. The master+brakes book parks cash the
  drawdown brakes leave idle in SGOV (only when 5% or more is idle; the 2% buffer stays cash). 2018–26 backtest
  arithmetic: parked on 38% of days, about 35% of the book when on, +0.4% a year to the core (17.7% → 18.2%); nearer
  +0.5–0.6% at today's T-bill rate. Not a strategy test (it moves no risk), so no trial.

## 8. Decision rules that never change

- Money moves only on evidence, within the 35% max drawdown.
- Every test is written down and pushed before it runs; one trial each; failures are recorded.
- Forward-only testing wherever the models could know the answer.
- Free data only; paper money only; secrets never printed.
