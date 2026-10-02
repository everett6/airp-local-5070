# Development plan: a fully autonomous research-and-quant trading agent (written 2026-09-27)

**Goal:** an agent that runs by itself every day, researches live (Jan on the web, Bonsai reading filings and news),
turns that research into decisions with quant algorithms (code scores, risk models, sizing), trades a paper account,
and grows it as fast as the evidence safely allows, with 60% a year as the stretch target.

**Fixed boundaries:** paper money only; free data only; the kill switch, mandate and order gate always sit above the
agent; every new signal passes a pre-registered test before it gets money.

## 1. What 60% a year really requires

| Forward Sharpe (lower 80% bound, 12+ months) | Volatility the book may run | Expected a year | Typical worst drop |
|---|---|---|---|
| < 0.6 | 20% (1.0×) | 12–18% | ~25% |
| 0.6–0.9 | 25–30% | 20–30% | ~35% |
| 0.9–1.2 | 35% | 35–45% | ~40% |
| **≥ 1.25** | **42%** | **~60%** | **~45–50%** |

- **Today:** the book's backtest Sharpe is about 1.05 at 20% volatility, which gives 19–21% a year. Of 20 registered
  trials, 1 passed. The AI stock picks carry a real but small 1-week signal (IC +0.066) that has not survived costs.
- **Two things must both happen for 60%:**
  1. the agent finds enough independent, cost-surviving signals to lift the Sharpe from about 1.05 to 1.25+, and
     keeps it there live for 12+ months;
  2. the drawdown limit is raised from today's **35%** to about **50%**.
- **The 35% limit caps the book at the 20–30% row.** Only you can change that (in `config/mandate.json`). The plan
  builds everything 60% needs, but the leverage ladder only climbs as far as the live evidence and your limit allow.
- **Honest odds:** a live Sharpe of 1.25+ from free data is rare. The likely landing zone is 20–35% a year. The plan
  is built so that failing at 60% still leaves the best book the evidence supports, not a blow-up.

## 2. Target architecture

```
                 ┌─────────────── Orchestrator (systemd user timers + autorun.py) ───────────────┐
                 │  schedule · locks · heartbeat · alerts · git-pushed ledgers · missed-run check │
                 └───────────────────────────────────────────────────────────────────────────────┘
 DATA            RESEARCH (LLMs)              SIGNALS (code)            PORTFOLIO (code)          RISK + EXECUTION
 prices (Yahoo,  Jan: live web research,      one scorer per signal,    sleeves: SPY core,        mandate + order gate
 Binance, Alpaca,  as of each event           pre-registered, IC-       crypto trend, event       (fail closed), kill
 Polygon)        Bonsai: reads filings,       tracked live             satellites, futures        switch (HALTED /
 SEC 8-K / 10-Q  news, research; quote-       (ridge on verified        sizing: risk budgets      REDUCING), drawdown
 XBRL, FRED      checked labels + reason      labels; no LLM number     (skfolio), vol target,    limit → paper broker
 archived news   spike check, 8-K watcher     is trusted unchecked)     leverage ladder           (simulator; Alpaca
                                                                                                  paper API)
                 └──────────────── LEARNING LOOP (bounded) ────────────────┘
                 LLM proposes hypotheses → code tests on train data only → held-out batch once a month →
                 every candidate registered → Deflated Sharpe bar rises with each try
```

**Built already:** most boxes exist:
- `forward_events.py`, `forward_allocator.py`, `research_events.py`, `llm_fields.py`, `news8k.py`;
- `app/portfolio/{master,forward,guard,futures}.py`, `bars.py`, `autorun.py`;
- the viewer, the trials registry and the DSR.

**Built 27 Sep (no GPU needed):**
- the broker adapter: `app/portfolio/broker.py` + `scripts/broker_sync.py`, run by autorun around the events and
  allocator jobs (a no-op until the Alpaca paper keys are in `backend/.env`; `--dry` in the dry-run week);
- GPU priority: the forward runner raises a flag and every other Bonsai/qwen job unloads and waits
  (`app/sandbox/gpu_lock.py`), so research never pushes a live decision onto the CPU fallback;
- phone alerts (ntfy.sh).

- the bounded learning loop and signal registry (below);
- the automatic dry → live switch: from Fri 2 Oct the 21:00 ET check switches to live if the dry run was clean
  (≥ 8 good event runs, at most 1 failed run, the last two event runs good, a good allocator run, an intact dry
  ledger); otherwise it stays dry and sends a phone alert with the reasons, every evening until fixed.

**Missing:**
- futures-based leverage in the forward book.

### The learning loop (rules fixed 2026-09-27, before any candidate was proposed)

Code: `app/signals/registry.py` (rules and budget), `scripts/learn_loop.py` (collect / monthly / review / status).

| Step | When | Rule |
|---|---|---|
| Collect | after every event run | the live releases' fields as they were at decision time → `results/forward/signals/live_features.csv`, and each field's percentile among the releases accepted before it → `live_pit.csv` (stored once, never rewritten) |
| Propose | first Saturday review of each month (live mode only) | Bonsai proposes 5 recipes from the fixed menu (EPS and revenue growth, momentum, Bonsai's log-odds, guidance, tone; optional sector filter; ≤ 3 terms, ±1 each, no fitted weights). GPU busy or bad reply → seeded draw of untried recipes. A recipe tried before is refused |
| Train test | same run | 2024 only: mean monthly IC ≥ 0.02 and its 95% CI above 0 |
| Holdout test | same run | 2025-26, once per recipe: mean IC ≥ 0.02, one-sided p < 0.05 / (k (k + 1)) for holdout test number k (under 0.05 over any number of tests; was 0.05 / k until 1 Oct 2026), and blending it with Bonsai's score beats the score alone (paired 95% CI above 0) |
| Shadow | weekly review | scored on live releases, no money. Promotion is looked at four times, not weekly: the first review on or after day 90, 120, 150 and 180 with ≥ 100 scored releases. Look j passes if the live blend gain's one-sided lower bound (Student's t over months) at level 0.20 / (j (j + 1)) is above 0 and its IC is positive; retired at 180 days otherwise |
| Promoted | weekly review | a 10% paper sleeve (long the top fifth of live releases by the blended score, 5-day holds, 0.4% costs); at most 2 signals (20%). Retired if its live blend gain since promotion turns negative (≥ 100 releases) |

Scores use only what was known when a release was accepted: each term is the field's percentile among earlier
releases (history and live), not within its month (changed 1 Oct 2026 after the outside review, before any signal
existed; `docs/PLAN_60_V2.md`, "Outside review, second part").

Every train and holdout test is registered in `results/trials_registry.jsonl`. The loop can add or retire signals
only; it can't change the master book, the mandate, the kill switch, these thresholds or its budget. Promotions and
retirements send a phone alert. The sleeve is reported next to the master book in the weekly review; folding it
into the broker-mirrored book stays a rule for the 3-month review.

## 3. Phases

Each phase has an **exit gate**. Nothing moves forward on schedule alone; it moves when the gate passes.

### Phase A: autonomous operation (Oct 2026, weeks 1–4)

| Work | Deliverable |
|---|---|
| Autonomy live | `scripts/autonomy.sh live` on Fri 2 Oct after the dry run; twice-daily events, weekly allocator, reviews, missed-run check |
| Live research in the loop | The best of arms A/B/C (verdicts 28 Sep) as the event pipeline; the 8-K watcher as a shadow book if W1 passes |
| Paper broker adapter | `app/portfolio/broker.py`: mirror each simulator order to the **Alpaca paper API** (your keys in `.env`), record the broker's fill next to the simulator's, alert on any gap over 0.5%. Gives real fills and real market hours without real money |
| Phone alerts | ntfy.sh topic (free, needs your OK because it sends alert text to an outside service) or email; desktop alerts already work |
| GPU safety | Research runners take `autorun.lock` / the GPU lock so they never collide with the forward runner (a concurrent load once crashed the card, Xid 79) |

**Exit gate A (1 Nov):** 4 weeks with at least 95% of scheduled runs, 0 broken ledgers, broker and simulator fills
within 0.5%, and no manual fixes needed in the last 2 weeks.

### Phase B: signal factory (Nov – Dec 2026)

The agent needs more independent signals. Each one enters through the same door:

1. **Signal registry** (`app/signals/registry.py`): each signal has a spec (inputs, scorer, horizon, costs), a
   pre-registered pass rule, and a live IC tracker. Passed signals feed the portfolio layer; failed ones are logged.
2. **Candidates, in order of prior odds** (each one trial, each needing a pass):
   - the 8-K watcher W2 (with Jan's research), only if arm B passed;
   - analyst-revision momentum from free archived pages (weak priors: all four target features failed before);
   - earnings-call language, if a free transcript source proves point-in-time (else skip);
   - cross-asset trend on futures (MES, micro BTC) with vol targeting: the simulator already prices futures.
3. **Bounded learning loop** (after RD-Agent), the only "self-improving" part:
   - Bonsai proposes up to **5 hypotheses a month** from a fixed menu of data fields;
   - code writes and tests them on **2024 data only**;
   - once a month, the survivors are tested **together, once**, on the held-back 2025–26 data;
   - every candidate goes into the trials registry, so the Deflated Sharpe bar rises with each try;
   - the loop can't change the book, the mandate or its own budget.
4. **Model upgrades:**
   - PrismML llama.cpp for Bonsai (needs your build permission): 1.5× or more faster, which allows asking Bonsai 3
     times and averaging, as its own trial;
   - a fixed re-benchmark of Jan and Bonsai each quarter.

**Exit gate B (early Jan 2027, the 3-month review, rules already fixed in PLAN_FORWARD.md):** implementation check
passed; a signal gets money only if it passed its test and its forward IC holds up.

### Phase C: portfolio engine (Jan – Mar 2027)

| Work | Deliverable |
|---|---|
| Combine signals | Passed sleeves sized by risk budget (skfolio `RiskBudgeting`), re-tested on backtest + forward data as one pre-registered trial (the risk-parity lead failed narrowly, CI [−0.01, +0.23]) |
| Volatility target | A book-level target (20% → 25% → 30% as evidence allows), the pre-registered vol-target lead re-tested with forward data |
| Leverage by futures | MES and micro-BTC in the forward book (margin, rolls, daily settlement already in `app/portfolio/futures.py`); leverage only through futures, never margin on stocks |
| Execution costs | No-trade bands (trade only when a weight leaves its band), measured slippage from the broker adapter fed back into the backtests |

**Exit gate C (6 forward months, ~Apr 2027):** forward Sharpe lower 80% bound ≥ 0.6 → the book may run at 25–30%
volatility (leverage up to about 1.5×), within the 35% drawdown limit.

### Phase D: the leverage ladder toward 60% (Apr 2027 on)

- **Monthly:** the Stage 4 table sets the volatility target from the forward Sharpe's lower bound. It moves down the
  same month the bound falls, and a −25% drop from peak halves it until a new high.
- **The 42% volatility (≈60%) row unlocks only when all three hold:**
  1. 12+ forward months with a lower bound ≥ 1.25;
  2. you raise `max_drawdown` to about 50% in the mandate;
  3. the 1.5× / 2.0× shadow books have tracked their targets for 6+ months.
- **Stress gate before each step up:** the copula stress test (skfolio) at the new leverage must keep a bad week
  (SPY −10%) above −25% of equity.

### Out of scope

Real money. That is a separate decision with legal, tax and broker questions. The plan stays paper-only, and the code
never holds real-account credentials.

## 4. How the agent decides, once it is all built

```
every weekday 08:45 and 18:30 ET        every Monday 18:00 ET                 continuous safety
  new filings (8-K, earnings)             re-mark every book                    order gate on every order
  → Jan researches as of now              → trend, vol and drawdown state       drawdown 25% alert,
  → Bonsai labels with quotes             → each passed signal's positions      35% (or your limit) REDUCING
  → code checks quotes, scores            → risk-budget sizing + vol target     kill switch any time
  → decision logged before the open       → orders → gate → broker (paper)      heartbeat + missed-run check
```

The LLMs judge what news means; code decides what to do with money.

## 5. Risks and what stops them

| Risk | Guard |
|---|---|
| Overfitting (the big one) | Pre-registration, the trials registry, the Deflated Sharpe Ratio, held-back data used once a month, forward test before money |
| LLM invents facts | Every label needs a verified quote; numbers come only from code-checked extraction |
| Look-ahead in research | As-of tools and the leak audit on every research run |
| Leverage blow-up | Leverage only after forward evidence; the drawdown limit switches to REDUCING; futures margin simulated |
| Missed runs / PC off | Heartbeat, missed-run check, alerts; late decisions logged as missed, never backfilled |
| GPU crash from two models at once | The shared GPU lock; research runners and the forward runner never overlap |
| Regime change | Monthly Stage 4 re-check; the vol target and brakes cut exposure the same month |

## 6. Decisions only you can make

| Decision | When | Default if you don't decide |
|---|---|---|
| Raise the drawdown limit toward 50% (needed for the 60% row) | **approved 27 Sep**; applied only when Phase D unlocks the 42% row | — |
| Alpaca paper keys in `backend/.env` (regenerated) | Phase A (you add them) | the broker adapter is skipped; simulator fills only |
| Phone alerts via ntfy.sh | **approved and connected 27 Sep** | — |
| PrismML build | **approved 27 Sep**; benchmarked after the GPU runs | — |
| Real money | never, in this plan | — |

## 7. Milestones

| Date | Milestone |
|---|---|
| Fri 2 Oct 2026 | Autonomy dry run checked, switched to live |
| Mon 5 Oct | Forward test starts, fully autonomous |
| 1 Nov | Exit gate A; signal registry and learning loop start |
| Early Jan 2027 | 3-month review (exit gate B) |
| ~Apr 2027 | Exit gate C: first volatility step-up if the evidence allows |
| Oct 2027 | Earliest date the 60% row can unlock (12 forward months at ≥ 1.25) |
