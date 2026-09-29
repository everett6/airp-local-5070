# Plan from here (written 2026-09-27)

Paper money only, free data only. Runs are started by hand until autonomy goes live on 5 Oct (see Decisions). Every test has its
pass rule committed before it runs and is logged in `backend/results/trials_registry.jsonl`. Details and results are
in [PLAN_60_V2.md](PLAN_60_V2.md); outside ideas in [INSPIRATION.md](INSPIRATION.md).

## Where things stand

- **Book (frozen):** SPY + BTC/ETH trend sleeve (20% cap) + drawdown brakes. Backtest 2018–26: 21.6% a year without
  brakes, 17.7% with them; the realistic forward expectation is about 19–20%.
- **AI picks:** Bonsai's 1-week score has a weak real signal on S&P 500 releases (IC +0.10 in 2025–26) that has not
  survived costs. It is tracked as a shadow book at 0 weight.
- **Built today:**
  - the paper portfolio viewer (Qanat style);
  - the mandate, a fail-closed order gate and the kill switch (HALTED / REDUCING);
  - Binance, Alpaca and Polygon price sources with a Yahoo cross-check;
  - a skfolio stress test.
- **Trials:** 18 registered, 1 pass (the brakes). Leads: the 35% crypto cap, skfolio risk parity, the vol target, the
  daily check.

## Phase 0: tonight and tomorrow morning (GPU, runs on its own)

| When (about) | What | Decides |
|---|---|---|
| ~22:00 | Jan finishes as-of research on 1,995 releases from 2024, then the leak audit | — |
| ~04:00 | Arm B: Bonsai reads the release + Jan's evidence | Does Jan's research add signal? |
| ~05:00 | Arm A: release-only labels finished and scored | Does "LLM extracts, code scores" beat EPS growth? |
| ~11:00 | Arm C: Bonsai with judgement (quality gate first) | Does more judgement add signal beyond arm B? |

**Me:** record each verdict in PLAN_60_V2, the executive summary and the README, then push.

## Phase 1: prep week, Mon 28 Sep – Fri 2 Oct

| Day | Me | You |
|---|---|---|
| Mon | Write up verdicts A/B/C. **Freeze the event pipeline** for the forward test: the best arm that passed, otherwise Bonsai's 1-week score as now. PrismML build + benchmark (if permitted) | Regenerate the Alpaca keys and put them in `backend/.env` |
| Mon–Tue (CPU) | Pre-register and run **"first reaction"** (the earnings-day move + Bonsai's labels, 20–40 day hold) and **"tilt SPY"** (over/underweight picked stocks inside the SPY holding instead of a separate sleeve). Build the autonomy runner | — |
| Wed–Thu (GPU nights) | Build the **8-K breaking-news watcher** (deals, CEO changes, layoffs → Jan → Bonsai → code) and backtest it on 2024–26 under its own rule | Practice the routine: `forward_events.py` at ~08:45 ET and in the evening; open the viewer |
| Mon–Fri | **Autonomy dry run (installed Sun 27 Sep):** the timers run everything into dry-run folders (events from Mon 28 Sep's real releases). Fri: check the heartbeats and alerts, SPY price cross-check against Alpaca, tag the frozen version in git, then `scripts/autonomy.sh live` for Mon 5 Oct | Keep the PC on and awake (it can't wake itself); glance at the viewer |

**Rule for the week:** a test that passes joins the forward test as a **shadow** book at 0 weight. Nothing changes the
real book before the 3-month review.

## Phase 2: forward test, Mon 5 Oct – early Jan 2027 (about 13 weeks)

| Cadence | Command | Who |
|---|---|---|
| Every weekday, ~08:45 ET and evening | `python scripts/forward_events.py` | autonomy (you, if it is off) |
| Weekly (Mondays) | `python scripts/forward_allocator.py` | autonomy (you, if it is off) |
| Saturdays | `python scripts/weekly_review.py` and `python scripts/failure_review.py` | you, then I read them |
| Any time | `scripts/portfolio_ui.sh --lan` (phone too) | you |
| Emergency | `forward_allocator.py --halt "why"` or `--reduce "why"`; `--resume` to undo | you |

- **Monthly (me):** a report with the forward Sharpe and its CI, fills vs the backtest, each shadow book's IC, and
  missed runs and failures.
- **Autonomy (from 5 Oct, if the Wed–Fri dry run is clean):** timers with wake from sleep run the commands above; a
  heartbeat and alerts report missed runs. The kill switch, mandate, order gate and 35% drawdown limit stay in force,
  and `--halt` stops everything at any time.

## Phase 3: 3-month review, early Jan 2027 (rules fixed 2026-09-27, before the forward test starts)

Window: every decision and fill from Mon 5 Oct 2026 to Thu 31 Dec 2026, scored with outcomes known by the review
date. Backtest baseline for the frozen 1-week pipeline: pooled IC **+0.066** (95% CI [+0.027, +0.106], 2024–26,
3,160 releases); monthly IC spread 0.105, so 3 months pin the forward IC down only to about ±0.06.

**1. Implementation check (all must hold, else the forward test is fixed and restarted, nothing is promoted):**
- the event and allocator ledgers verify (hash chains intact);
- at least 95% of scheduled live event runs happened (heartbeat);
- missed decisions are at most 10% of discovered releases (not counting those with no press release);
- every allocator fill is at the first open after its decision date, within 0.5% of Yahoo's open that day, at 5 bps
  cost;
- there are no mandate rejections on the frozen book;
- the forward event-book IC is not clearly below the backtest: the upper end of its 95% CI (bootstrap over weeks) is
  at least +0.033, half the baseline.

**2. AI picks (only if 1 passes):**
- The 1-week event book becomes **eligible** for money only if its forward IC is above 0 AND the pooled
  backtest + forward IC has a 95% lower bound above 0.
- Eligible means a pre-registered paper satellite: 10% of the book, equal-weight top-fifth picks, held 1 week,
  brakes on. It needs your mandate commit, since stocks are outside today's universe.
- Otherwise it stays a shadow book.

**3. The book:** returns vs SPY and 80/20 are reported, not judged; 3 months is too short. Leverage stays at 1.0×.

**4. Shadow arms** (the best of arms A/B/C if one passed, the 8-K watcher if W1 passes): the same test as in 2. Any
arm that becomes eligible counts as one more trial in the registry.

## Phase 4: month 6 on, about Apr 2027

- The Stage 4 table in PLAN_60_V2 sets volatility and leverage from the forward Sharpe's lower bound.
- **Realistic range:** 20–30% a year. 60% only if the forward Sharpe holds at 1.25 or more for 12+ months and you
  accept drops of about half the account.
- **Queued for then** (each needs a new test):
  - the crypto-cap leads (35% cap, skfolio risk parity);
  - Bonsai asked several times and averaged (needs PrismML);
  - a bounded RD-Agent-style signal search: a fixed budget, a held-back period, every candidate registered.
  - satellite data, free only: Sentinel-5P NO₂ (daily, ~5 km) as a factory-activity signal for industrial and
    materials stocks or sector ETFs. One pre-registered test, low prior odds: free imagery is regional, too coarse for
    company-level signals like parking lots (those need paid sub-meter imagery, which is ruled out).

## Decisions (the user delegated 1, 3 and 4 on 2026-09-27; my choices and why)

| Decision | Choice | Why |
|---|---|---|
| **Maximum drawdown** | **35% from the peak** (alert at 25%), in `config/mandate.json` | The frozen book's worst backtest drop was 27% with brakes and 34% without, so a tighter limit would trip in an ordinary bad year and sell at the bottom. 35% still rules out the 60% setting (drops of 45–50%) and caps any later leverage at the 25–30% volatility row (about 20–30% a year). At the limit the allocator switches to **REDUCING** (sells only) until `--resume`; at 25% it flags the run |
| Alpaca keys in `backend/.env` | yours to do | Only you add credentials; regenerate them first, since the old ones are in the chat |
| **PrismML llama.cpp fork** | **Yes, from the official PrismML-Eng repo, after tonight's runs** | The only real speed-up for Bonsai. It is benchmarked on the 100 dev releases and used only if quality (parse rate, verified quotes) matches Ollama and it is at least 1.5× faster. Arms A–C stay on Ollama so they are comparable. The build needs a permission you grant (see below) |
| **Autonomy** | **On for the 5 Oct start**, after a dry run Wed–Fri | A missed 08:45 ET run can never be backfilled, so manual runs are the forward test's weakest link. Built this week (timers with wake from sleep, heartbeat, alerts); it runs Wed–Fri into a dry-run ledger, and goes live only if those runs are clean. The kill switch, mandate, order gate and drawdown limit are in place |
| Mandate changes (crypto cap, AI picks weight) | only after a passed test | Code never changes the mandate on its own |

## Autonomy: how it runs

- `scripts/autonomy.sh status | dry | live | uninstall`. User timers (no system service), New York times:
  events weekdays 08:45 and 18:30, allocator Mondays 18:00, reviews Saturdays 10:00, missed-run check daily 21:00.
- Each run takes a lock, writes a heartbeat and alerts (desktop + `results/forward/alerts.jsonl`, also in the
  viewer). Live runs commit and push the ledgers.
- The PC must be on and awake at those times; the timers cannot wake it from sleep. A missed time runs when the PC
  comes back (a late event run logs late releases as missed, never backfilled).
- From 5 Oct, GPU research jobs must take the same lock (`results/forward/autorun.lock`) so they never kill the
  forward runner's models.

## Standing rules

- Paper money only; no paid data or subscriptions.
- Never shut the PC down unless you say so.
- Research uses Jan and Bonsai. Stock picking is never dropped without your say.
- Failed tests are not re-proposed as new ideas; leads wait for a new pre-registered test.

## Early go-live (user's call, 2026-09-29 ~15:39 PDT)
The user asked to go live at once ("GO LIVE RIGHT NOW"). Autorun mode switched dry → live by hand
(`scripts/autonomy.sh live`) on Tue 29 Sep, before the Friday check. Its record at the switch: 3 good dry event runs
and 1 failed (the boot catch-up, fixed). The dry run caught the extract-date bug (fixed in 2ab2774).
- Live event decisions cover filings from **Wed 30 Sep 2026** (was Fri 2 Oct; no 30 Sep filing existed at the
  change). Same frozen pipeline, git tag `forward-v1-live`.
- Allocator unchanged: weekly on Mondays, so the first live decision is Mon 5 Oct as planned (paper orders fill Tue).
- Paper money only; the broker mirror sends orders to the Alpaca PAPER account only.
