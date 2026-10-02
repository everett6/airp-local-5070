# Code review, 1 Oct 2026

The user asked for a read-through of the whole code for bugs and optimizations, before the earnings season
(mid-October) multiplies the load on the live runner. Rules kept for every change: the frozen books' results and
the registered decision rules do not change; anything that would change what a live decision is goes to the user;
each fix has a test and its own commit.

**Result: 24 faults fixed, 6 questions left for the user, no live decision lost or changed so far.** Most faults were
found by running each scheduled job's first-time path on a scratch copy and by writing end-to-end tests with a
stand-in model, not by reading. 642 tests pass (592 in the morning).

## The most serious finding (fix 16)

Since the health checks added on 29 Sep, one new release that could not be decided made the whole live run fail
before any decision was written, and the same release would have failed every later run. A filing without a
press-release exhibit is enough: 80 of 5,751 past S&P 500 earnings filings (1.4%), at least one on 12% of release
days. It had not happened yet (six releases so far). In the earnings season it would have within days, and every
decision after it would have been missed until someone repaired it by hand. The registered rule ("late or impossible
decisions are logged as missed") is restored: that release is left out, tried again by the next run while its open
is still ahead, then logged as missed with its reason; the others are decided. A broken pipeline or missing market
data still fails the run. Dated correction in `PLAN_60_V2.md`; the test fails on the old code with exactly this
fault.

## Fixed

Numbers are the order of discovery.

### Would have stopped the live runner, or lost decisions or labels

| # | Where | What was wrong | Fix | Commit |
|---|---|---|---|---|
| **16** | `scripts/forward_events.py` | One undecidable release (no press release, or no price for the stock) failed the whole run and every later one. See above. | Left out, retried, then logged as missed; the others are decided. | cd377e3 |
| 15 | `extract_events.py`, `build_features.py`, `decide_events.py`, `forward_events.py`, the label and consensus shadows | The reader's and the judge's files are appended one line per release. A power cut during a write leaves half a line; every later live run would have failed at it, and the next record would have been glued onto it. | Reads skip a cut-off line (that release is done again); appends start on a fresh line. Judge output and live fact sheets byte-identical before and after. | 12b719a |
| 24 | `app/forward/ledger.py`, then `forward_allocator.py`, `broker_sync.py`, `learn_loop.py` | The tamper-proof ledgers (and the rebalance's own ledger) stopped at a half-written line: after a power cut, every later run failed until it was removed by hand. This machine has had such a line (the model cache, 16 Sep). | The line is skipped and stays in the file; the next record starts on its own line. Tamper evidence is unchanged and tested: a whole record removed, damaged or edited still fails the chain check. | 6f8d97a, 4be9033 |
| 14 | `forward_allocator.py`, `broker_sync.py`, `forward_events.py` | The books' state, the broker order record, the list of past releases and the price file were rewritten in place; a power cut could leave half a file. | Each is replaced in one step. Contents identical. | e6e34ca |
| 13 | `self_improve.py`, `net_read_shadow.py` | The self-improvement versions file is rewritten after every run. Cut off, it would have stopped all three opinion agents from labelling live releases (labels cannot be made afterwards). | Written in one step; if unreadable the fixed agents still label and an alert is raised; never rewritten from a damaged read. | 9b79e9c |
| 18 | `net_read_shadow.py` | One failed label lost every label of that run, for all three agents. | The good ones are kept; the failed ones are retried while their open is ahead. | 8d6809a |
| 20 | `net_read_shadow.py` | All labels of a run got one time stamp, after the last label. On a morning with 70+ reports that is after the 09:30 open, so the busiest days would have dropped out of the agents' tests. | Each label is written when it is made, with its own time, release by release. | 91ade9c |
| 7 | `net_read_shadow.py`, `consensus_shadow.py` | A cut-off label line stopped all later labelling. | Tolerant reader. | 13686be |
| 4 | `autorun.py` | An event run that did its job but ended with a broker alert was not pushed (no outside time stamp for its decisions) and would have been called a missed run. Both live runs with the JBL alert were affected. | Such a run counts as a run and is pushed; their files were committed as written (49a427b). | 8758d8f |
| 5 | `autorun.py` | A cut-off heartbeat line, a step hanging past its limit, a hung git or a missing notifier each killed a run without a trace. | Tolerant reader, safe append, time limits, guarded notifier. | 8758d8f |
| 8 | `autorun.py` | When Yahoo returned no prices the run failed without a retry. | Retried once after 90 s. | 13686be |
| 19 | `forward_events.py` | Prices were fetched one ticker at a time; at a few hundred names that is minutes, twice a day, in the 45 minutes before the open. | One call for all tickers (the downloads run side by side), single requests as the fallback. Price file byte-identical; 510 symbols in 7.8 s. | 4ecd409 |

### Orders at the paper broker

| # | Where | What was wrong | Fix | Commit |
|---|---|---|---|---|
| 1 | `ai_picks.py` | An exit was sent for a pair whose entry never filled. JBL's entry orders expired on 30 Sep; on 7 Oct the exit would have shorted 6 JBL and bought back 9 XLK of the other pairs' hedge. | Exit orders only for entries that filled. | f208053 |
| 10 | `app/portfolio/broker.py`, `ai_picks.py` | Class shares (BRK-B, BF-B) were sent with a dash; Alpaca only knows BRK.B. The stock order would have been rejected and the ETF hedge left open alone. | The book keeps the dash; orders, price requests and positions use the dot. | daf49b0 |
| 22 | `app/data_ingestion/tickers.py`, `weekly_review.py` | Two wrong symbols: Fiserv was mapped to FI, which died when it went back to FISV; EQR became VMRK. Both report in late October and could not have been priced or traded. (Five more index names have no prices because the companies are gone.) | Mapping corrected and checked at Yahoo and Alpaca. The Saturday review now lists every index member without prices. | 152f081, 4404174 |
| 6 | `broker_sync.py` | One failed order lookup aborted the sync before its record was saved, so the failure repeated every run. | Each order reports its own problem; the record is saved. | 6a7b882 |

### The aggressive 2.5× book (before its first run on Mon 5 Oct)

| # | Where | What was wrong | Fix | Commit |
|---|---|---|---|---|
| 2 | `guard.py`, `forward.py`, `forward_allocator.py` | Its −60% limit switched the shared kill switch to sell-only, which would also have stopped the frozen books and the AI-picks sleeve from buying. | The limit sets a flag on that book only. | 9633438 |
| 3 | `forward.py` | Any error inside it failed the whole weekly run, frozen books included. | Its errors are reported, the book is put back, the run goes on. | d361aa4 |
| 12 | `weekly_review.py`, `forward_allocator.py --status`, `desktop_export.py`, the app | A failed week of that book is stored without numbers; four readers would have crashed or shown nonsense, the Saturday review among them. | They leave such an entry out and say so. | d3e917f |

### Monthly cohorts and reports

| # | Where | What was wrong | Fix | Commit |
|---|---|---|---|---|
| 17 | `longterm_picks.py` | Once a long-term pick stops trading before its exit (bought out, delisted), scoring raised at every run, before the new month's cohort was made: the track would have stopped for good (earliest about 30 Dec). | That cohort is reported and left unscored; the others are scored, new cohorts are made. | bc65f8b |
| 11 | `app/sandbox/walkforward.py` | The one-token probability call behind the monthly ratings had no retry: one dropped connection in ~500 calls failed the whole cohort. | Three attempts. Answers unchanged. | 69c6574 |
| 23 | `digest.py` | The phone digest chose records by UTC date; on the cohort day the afternoon run ends after midnight UTC and the digest would have been empty. It would have happened today. | The local day. | 8064b5d |
| 9 | `digest.py` | A run that did its job but raised an alert was called "FAILED". | "Finished with an alert". | 8824bf9 |
| 21 | `autorun.py` | The consensus shadow ran after the push, so its lines got their outside time stamp a run late, and its output was not logged. | It runs right after the labels, as a side step that cannot change the run's result. | 2eb11f3 |

### After the restart of 1 Oct, 16:33 (the afternoon run was killed an hour in)

| # | Where | What was wrong | Fix | Commit |
|---|---|---|---|---|
| 25 | `longterm_picks.py` | Ratings were only written at the end: 449 finished ratings of the first cohort were lost. | Each rating is saved as made; a cut-off run reuses a saved rating only when its card is unchanged. | 30cefca |
| 26 | `autorun.py`, `airp-resume.service` | A run killed by a restart left no heartbeat, no log, no push and no digest, and nothing ran until the next slot (`Persistent=true` only covers a slot missed while the PC was off). | A run marks itself in `running.json` and writes its log after every step. At boot `autorun.py resume` (a one-shot service, not a timer) finishes a cut-off event run: within 6 hours, not within 30 minutes of the next slot, never while another run works; otherwise it only alerts. | this commit |

### Outside review pasted by the user (1 Oct, evening): seven points, all confirmed against the code

| Point | Verdict | What was done |
|---|---|---|
| On-time uses the run's start time | true; already open question "on-time-rule" | left for the user (changes the frozen runner's record) |
| Entry time differs between orders (09:30 New York) and scoring (13:00 UTC); holidays | true; already "winter-entry" | left for the user; needed before 1 Nov |
| Figures validated for presence, not meaning (period, units, basis) | true; related to "reader-prior" | a new reader version to pre-register and test; left for the user |
| A failed SEC lookup looks like "no filing" | true | failed lookups are retried once, counted in the run record (`lookups`), and alerted if still unread (5e7baeb) |
| A chain with its end removed still verifies; no write durability | true | daily check compares every ledger with its pushed copy; records and state files are synced to disk (5e7baeb) |
| pandas, Parquet engine, yfinance missing from the base install | true | declared; `requirements.lock`; `tests/test_runtime_deps.py`. A clean-install test needs a download: left for the user |
| Docs still say "run by hand"; alerts parsed from console text | true | `docs/HOW_IT_RUNS.md` is the one current description; README and docstrings corrected. Structured step results: proposed, left for the user |

### Second outside review (1 Oct, night): five faults and seven records

All five faults were reproduced on made-up data before any change. Three fixes change a rule that was fixed in
advance; the new rules were written into `docs/PLAN_60_V2.md` ("Outside review, second part") and pushed first. No
signal had been proposed and no pair had reached its exit, so no recorded result rests on the old rules. Three of
the files below are on this page's "read and found sound" list; that list was wrong about them.

| Fault | Reproduced as | Fix | Commit |
|---|---|---|---|
| A part-filled order leaves shares behind (`broker.py`, `ai_picks.py`) | 10 ordered, 4 filled, cancelled: the 4 were never sold and the whole hedge was bought back | Filled quantity is kept whatever the final status; exits are sized to what the broker holds | 261a4d8 |
| A missed exit run strands a filled pair (`ai_picks.py`) | Simulator marks the pair closed; no exit order is ever sent | Exits follow the broker's remaining exposure from the scheduled exit on, whatever the simulator says; short exits are re-sent (up to 5); a late exit is recorded and alerted once | 261a4d8 |
| Later releases re-rank earlier ones (`registry.py`) | A above B, then B above A after later same-month releases | A score uses only releases accepted earlier; per-field percentiles are stored when a live release is collected (`live_pit.csv`) | f03006c |
| No lifetime error budget (`registry.py`, `learn_loop.py`) | 0.05/k sums to 0.155 over 12 tests; a weekly 80% bound | Holdout test k needs p < 0.05/(k(k+1)); promotion is looked at 4 fixed times at 0.20/(j(j+1)) with a Student's t bound (a bootstrap of 3 or 4 months passed a 10% look 7 times in 40 on no-edge data) | f03006c |
| The answer cache is keyed by model name (`walkforward.py`) | New weights behind `bonsai-27b:latest` would replay old answers | The key holds Ollama's digest and the output length; old entries are served only for the digest recorded on 1 Oct (`config/model_digests.json`) | dee30cf |

| Record asked for | What exists now | Where |
|---|---|---|
| The AI's contribution, apart from execution | The sleeve replayed with the AI's scores, with the same scores dealt at random (500 deals), and with every release; same slots, sizing, costs and run times. Its replay reproduces the live book. Weekly | `scripts/ai_contribution.py` → `results/forward/ai_contribution.json` |
| A hand-checked extraction benchmark | 12 hard releases (3 banks, 5 odd fiscal years, 2 losses, 2 guidance changes, heavy tables), 65 figures labelled from their text, each with the wrong figures printed next to it. The scorer names the mistake: other period, units, basis, sign, missed, invented | `benchmarks/extraction/gold.json`, `scripts/extraction_benchmark.py` |
| The whole funnel | Every release, the last stage it reached and why it stopped; weekly it asks the SEC again so a release no run saw is counted | `scripts/funnel.py` → `results/forward/funnel.json` |
| Predictable throughput | Stage times in every `run` record; nearest open first; each decision written the moment the judge has it; weekly median and worst, and an alert under 10 minutes to spare | `forward_events.py`, `scripts/throughput.py` |
| An evidence bundle per decision | Press release hash and download time, the reader's record, the exact fact sheet, both models' digests, prompt hashes and settings, code version, entry session, ledger line; written once in the run that decided, indexed by hash, `--check` weekly | `scripts/evidence_bundle.py` → `results/forward/events/evidence/` |
| One view of the account | Gross and net exposure, leverage, buying power, shared ETF hedges, and any position the two books' orders do not add up to (an alert). Read-only. Today the account reconciles to the share | `scripts/account_view.py` → `results/forward/account/` |
| Recovery as a workflow | 13 rehearsals against a stand-in for the Alpaca API: lost connections before and after submission, a crash before the book was saved, part fills, a rejected hedge leg, missing prices, missed entry and exit runs, the kill switch. Each ends with no duplicate order, no unexplained position and the simulator's record unchanged | `tests/test_recovery.py` |

**Baseline of the live reader on the benchmark** (run 1 Oct): 42 of 65 labelled figures right. It almost never
gives a wrong number (2 from another period, none invented, no unit, basis or sign mistakes) but misses 21 that the
release states: Boeing's whole table (losses in brackets), Intel's and Nike's revenue, most prior-year figures. The
labels are mine, read from each release; they are not yet checked by the user.

**Busy-morning rehearsal** (the real SEC lookups, reader and judge on the releases of 30 Apr 2026, the busiest
morning of the year; answers not replayed from the cache): 38 new releases decided in 3 min 48 s (lookup 124 s,
reading 70 s, judging 31 s), 41 minutes before the open. The 48 releases of the evening before took 3 min 50 s.
Downloading the 38 press releases afresh took 8 s and gave the same text byte for byte. Without the GPU
(Bonsai-lite) the morning took 2 min 3 s. The slowest path is a busy GPU: the runner waits up to 15 minutes for
it, then uses Bonsai-lite, still half an hour early.

**Found by the rehearsal: the SEC's filing list gives unreliable acceptance times.** Two identical lookups returned
38 and 34 new releases and neither reported a problem. The list's `acceptanceDateTime` was the New York clock time
labelled as UTC for every live release so far (4 hours early) and 4 or 5 hours late for 7 of 20 filings checked
that night; the history files hold the true time. The entry day hangs on that time. The runner now reads it from
each filing's own index page (rule 6 in the plan; 5b5f839). None of the seven live decisions is affected: all were
filed before 08:00 or after 16:00 New York. Unfixed, a release filed between 09:30 and 13:30 would have been logged
as missed (2.7% of the history), and one read 4 hours late would have been entered a day late as if on time.

## Left for the user (also on the app's Home page, `docs/open_decisions.json`)

1. **"On time" is judged by the run's start, not the moment of writing.** A run that starts at 09:10 New York time
   and writes at 09:35 would be recorded as on time. It has not happened (the six live decisions were written at
   least 42 minutes early). Fix: three lines in the frozen runner.
2. **A gap longer than 3 days is never back-filled as "missed".** If the PC is off for a week, releases in the gap
   are not in the ledger at all (the daily check does alert on the missing runs).
3. **The broker mirror after a wipe-out of the 2.5× book.** The paper book would close, its broker positions would
   stay open. Needs a rule.
4. **Entry day in winter (from 1 Nov).** A release filed between 08:00 and 09:30 New York time is traded at that
   day's open but scored from the next day's (the scoring cut-off is a fixed 13:00 UTC). The score stays honest but
   measures a slightly different trade. Both rules were fixed in advance.
5. **How to score a long-term pick that stops trading.** Usual practice: its last traded price. Needed before the
   first cohort closes (about 30 Dec).
6. **The reader sometimes takes the previous quarter for the year-ago quarter.** Live on MU (30 Sep): "sales
   54,229M vs 41,456M a year earlier (+30.8%)"; 41,456M was the quarter before, the year-ago quarter was 11,315M.
   On 546 past releases with both figures: exactly the previous quarter in 3.1%, more than 10% away from the
   SEC-filed year-ago figure in 15.2% (partly banks, whose revenue has several definitions). Using the SEC figure
   whenever it exists would change what the judge sees: a new version to test (needs the graphics card).

Checked and closed: MKC's sales showed as 17.4 million in the app (the reader took the "17%" growth for the
amount). The code's SEC cross-check had dropped that number; the judge's sheet said "Revenue: not stated". The app
now shows, for every release, the fact sheet the judge read and what the cross-check dropped.

## Noted, not changed

- A monthly cohort rates all its cards in one go (about 490). A card that keeps failing fails the cohort, and the
  75 minutes repeat at the next run. Left on purpose: rating a failed card as "unclear" would hide a model server
  that died half-way, and a cohort cannot be remade.
- `learn_loop.py monthly` (first run Sat 3 Oct): a crash between testing a proposal and saving would make the next
  Saturday test and register the same proposals again (identical results, duplicate registry lines). Its data path
  was checked on the real tables without computing any test.
- The index membership list is the 2026 list; index changes after it are not followed.

## Tests added (paths that had none)

Coverage showed that the main bodies of the live scripts had only ever been exercised by real runs. Now covered
end to end, with a stand-in model or broker and synthetic data:
`tests/test_forward_events_main.py` (the live runner), `test_decide_events.py` (reader and judge steps),
`test_monthly_cohorts.py` (theme and long-term cohorts), `test_forward_allocator_main.py` (weekly rebalance),
`test_sleeve.py` (order mirror through one pick's whole life), `test_net_read_shadow.py` (labelling run),
`test_weekly_review.py`, `test_forward_ledger.py`, plus cases in `test_autorun.py`, `test_broker.py`,
`test_digest.py`, `test_learn_loop.py`, `test_self_improve.py`, `test_tickers.py`. Desktop: 9 server tests and a
smoke mode that opens every page and reports any that shows an error.

## Rehearsed on scratch copies (nothing live was touched, no graphics card)

- Saturday's weekly review and learning review (found fix 12).
- Monday's rebalance with the aggressive book, and the order mirror (dry): 3 orders planned, 1.63× at the broker.
- The event runner, five times, after each change to it; every step of the event job with the card off.
- The AI-picks sleeve day by day to 9 Oct with made-up prices and a stand-in broker: MU and ACN exits go out on the
  morning of 8 Oct; JBL (never filled) closes on 7 Oct with no order and no new alert.
- The consensus shadow on the six live releases (recorded late, never scored, as planned).
- The whole S&P 500 priced in one call (found fix 22).

## Read and found sound

(The second outside review, above, found faults in three of these: `broker.py`, the model client's cache key and
`app/signals/registry.py`.)
`app/portfolio/master.py`, `sleeve.py`, `broker.py`, `themes.py`; `app/forward/schedule.py`;
`app/sandbox/events.py`, `gpu_lock.py`, the model client in `walkforward.py`; `app/signals/registry.py`;
`app/data_ingestion/edgar.py`; `scripts/guidance_shadow.py`, `themes.py`, `research_queue.py` (no GPU job waiting),
`trading_health.py`, `failure_review.py`, `learn_loop.py`, `self_improve.py`; the desktop server (token on every
call, local address only). A stricter lint pass over `app/` and `scripts/` found nothing in live code.

## Not read line by line

The research-only library (`app/sandbox/walkforward.py` beyond the model client, `agent_worker.py`, `intraday.py`,
`jail.py`, `app/llm`), the finished trial scripts (they are the record and are not edited), and
`scripts/warm_gdelt.py` (running).
