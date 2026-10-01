# Code review, 1 Oct 2026

Scope: the user asked for a read-through of the whole code for bugs and optimizations. Order: the live trading path
first, then the library, the desktop app and the scripts still in use. Rules for every change: the frozen books'
results must not change; a finding that would change a live decision is reported here, not changed; each fix has a
test and its own commit.

## Fixed

| # | Where | What was wrong | Fix | Commit |
|---|---|---|---|---|
| 1 | `scripts/ai_picks.py` | An exit was sent for a pair whose entry never filled at the broker. JBL's entry legs expired on 30 Sep; on 7 Oct the exit would have shorted 6 JBL and bought back 9 XLK of MU's and ACN's hedge. | Exit legs only for entry legs that filled; closing such a pair raises no second alert. | f208053 |
| 2 | `app/portfolio/guard.py`, `forward.py`, `scripts/forward_allocator.py` | The aggressive 2.5× book's −60% limit switched the **shared** kill switch to sell-only, which would also have stopped the frozen books and the AI-picks sleeve from buying. Found before the book's first run. | The limit sets a flag on that book only (`Book.reducing`, cleared by `--resume`). | 9633438 |
| 3 | `app/portfolio/forward.py` | Any exception inside the aggressive book (for example its cash assertion) failed the whole weekly allocator run, frozen books included. | Its errors are reported in the ledger and the app, the book is put back as it was, the run goes on. | d361aa4 |
| 4 | `scripts/autorun.py` | An event run that did its job but ended with a broker alert (exit 1) was not pushed, so its decisions had no outside timestamp, and the daily check would have called it a missed run. Both live runs with the JBL alert were affected. | A run whose event runner finished counts as a run and is pushed. Their result files were committed as written (49a427b). | 8758d8f |
| 5 | `scripts/autorun.py` | A heartbeat line cut off by a crash, a step that hung past its limit, a hung git, or a missing `notify-send` each killed a run without a heartbeat or alert. | Tolerant reader, safe append, `safe_step`, timeouts on git, guarded notifier. | 8758d8f |
| 6 | `scripts/broker_sync.py` | One failed order lookup or send aborted the sync before `orders.json` was saved, so the failure repeated every run and later decisions could not be mirrored. | Each leg reports its own problem and the file is still saved. | 6a7b882 |
| 7 | `scripts/net_read_shadow.py`, `consensus_shadow.py` | A label line cut off by a crash stopped all later labelling; labels on live releases cannot be made afterwards. | `app/forward/ledger.jsonl_records` skips a torn line. | 13686be |
| 8 | `scripts/autorun.py` | When Yahoo returned no prices the event run failed without a retry (the message was not in the transient list). | Retried once after 90 s. | 13686be |

## Reported, not changed (would change a live decision, or needs the user)

1. **`forward_events.py`: "on time" is judged by the run's start time, not the write time.** The rule says a decision
   counts only if it was written before its entry open. The code compares the open with the time the run started.
   A run that starts at 09:10 ET and writes at 09:35 ET would be recorded as on time. It has not happened: the six
   live decisions were written at least 42 minutes before their opens. Proposed fix: compare with the clock at the
   moment of writing (three lines). It touches the frozen runner, so it waits for the user's yes.
2. **`forward_events.py`: a gap longer than 3 days is never back-filled as "missed".** Discovery looks back 3 days
   from the last run. If the PC is off for a week, releases in the gap are not in the ledger at all. The daily check
   does alert on the missing runs.
3. **`forward_events.py`: prices are fetched one ticker at a time.** The list grows with every release since the
   start; at a few hundred names it adds minutes to each run. A batched download would be faster but changes the
   data path of the frozen runner.
4. **Broker mirror after a wipe-out.** If the aggressive book is ever closed, its positions at Alpaca stay open:
   the mirror only follows pending targets. Paper only; needs a decision on what the mirror should then do.
5. **Reader oddity seen in the app:** MKC's sales are recorded as 17.4 million dollars (they are about 1.7 billion). The reader's
   number passed the quote check, so the release likely states it in a unit the reader misread. The judge saw that
   number. Worth a look in the Saturday review; the frozen reader is not changed here.

6. **Entry day in winter.** The live runner lets a release filed before 09:30 New York time enter at that day's
   open, and the AI-picks sleeve trades that open. The scoring code (`entry_index`) uses a fixed cut-off of 13:00 UTC,
   which is 09:00 New York in summer and 08:00 in winter. From 1 Nov, a release filed between 08:00 and 09:30 New
   York time will be scored from the next day's open while the sleeve trades the same day's open. The score stays
   honest (the decision is still made before the scored entry) but it will no longer measure the trade the sleeve
   makes for those releases. Both rules were fixed before the forward test, so nothing is changed here.

7. **The hash-chained ledgers stop at a cut-off last line.** `app/forward/ledger.Ledger` (events, guidance,
   long-term, themes) checks every line; a last line cut off by a power loss would make every later run fail until
   the line is removed by hand. That strictness is the point of the ledger (nothing can be dropped silently), so it
   is not changed here. Proposed, if the user wants it: treat only an unterminated last line as never written,
   and say so in an alert.

8. **How to score a long-term pick that stops trading.** The first version of the scoring left such a pick out
   of the average (kind to the result: a bankrupt pick would vanish). The 29 Sep health check refuses to score the
   cohort at all. Neither is a good rule. Usual practice: value it at its last traded price (a buyout is then
   counted at about the deal price, a collapse at about zero). Needs the user's yes before the first cohort closes
   (about 30 Dec); until then such a cohort stays unscored and is flagged.

## Also fixed in this pass

| # | Where | What | Commit |
|---|---|---|---|
| 9 | `scripts/digest.py` | The phone digest called a run that did its job but raised a broker alert "FAILED". It now says "finished with an alert". | 8824bf9 |
| 10 | `app/portfolio/broker.py`, `scripts/ai_picks.py` | Class shares (BRK-B, BF-B) were sent to Alpaca with a dash. Alpaca only knows BRK.B (read-only lookup, 1 Oct): the order would have been rejected, leaving the pair's sector-ETF short open without its stock, and one such name in a price request makes the whole request fail. Not hit yet (no class share has been picked). | The book keeps the dash; orders, price requests and positions use the broker's dot. | daf49b0 |
| 11 | `app/sandbox/walkforward.py` | The one-token probability call behind the monthly long-term and theme ratings had no retry: one dropped connection in about 500 calls would have failed the whole cohort (75 minutes of GPU, repeated at the next run). | Three attempts, as the main call already had. Answers unchanged. | 69c6574 |
| 12 | `scripts/weekly_review.py`, `forward_allocator.py --status`, `desktop_export.py`, `desktop/src/airp.js` | A week in which the aggressive book fails is stored as an error entry without numbers (fix 3). Four readers took `equity` from every entry: the Saturday review would have crashed, the status command too, and the app's book table would have shown broken numbers. Found by rehearsing the review on a scratch copy. | They leave such an entry out; the review says the book's last run failed. The review and the health report also skip a ledger line cut off by a crash. | d3e917f |
| 13 | `scripts/self_improve.py`, `scripts/net_read_shadow.py` | The self-improvement versions file is rewritten after every event run, in place. Had a crash cut it off, the label shadows (which read it first) would have stopped labelling live releases with all three lenses; those labels cannot be made afterwards. | The file is written through a temporary file; if it is ever unreadable the fixed lenses still label and an alert is raised; it is never rewritten from a damaged read. A cut-off label line no longer stops the champion/challenger scoring. | 9b79e9c |
| 14 | `scripts/forward_allocator.py`, `broker_sync.py`, `forward_events.py` | The books' state, the broker order record, the list of past releases and the price file were rewritten in place. A crash or power loss mid-write would have left half a file: the next weekly rebalance or order sync could not start, or past releases would silently drop out of scoring. | Each is now replaced in one step (`app/forward/ledger.write_atomic`). Contents are identical; the rebalance and the order mirror were rehearsed on a scratch copy. | e6e34ca |
| 15 | `scripts/extract_events.py`, `build_features.py`, `decide_events.py`, `forward_events.py`, the label and consensus shadows | The reader's and the judge's output files are appended one line per release. A power loss during such a write leaves a cut-off last line; every later live run would then have failed at that line, so every later decision would have been missed until the file was repaired by hand, and the next record would have been glued onto the broken line. | Reads skip a cut-off line (that release is read or judged again) and appends start on a fresh line (`app/forward/ledger.open_append`). Checked: the judge's output and the live fact sheets are byte-identical before and after; new end-to-end tests of the reader and judge steps with a stub model. | 12b719a |
| **16** | `scripts/forward_events.py` | **The most serious finding.** Since the health checks of 29 Sep, one new release that could not be decided made the whole run fail before any decision was written, and the same release would have failed every later run. A filing without a press-release exhibit is enough: 80 of 5,751 past S&P 500 earnings filings (1.4%), at least one on 12% of release days. It had not happened yet (six releases so far); in the earnings season from mid-October it would have within days, and every decision after it would have been missed. The same for a stock without a price (a renamed ticker). | The registered rule is restored: such a release is left out and the others are decided; it is tried again by the next run while its open is still ahead, then logged as missed with its reason. A broken pipeline or missing market data still fails the run. The runner's main body now has an end-to-end test (`tests/test_forward_events_main.py`); it fails on the old code with exactly this fault. Dated correction in PLAN_60_V2. | cd377e3 |
| 17 | `scripts/longterm_picks.py` | Same kind of fault, later in the year: once a long-term pick stops trading before its exit day (bought out, delisted), scoring that cohort fails at every run, and it failed before the new month's cohort was made, so the track would have stopped for good. Earliest date: about 30 Dec. | That cohort is reported at each run and left unscored; the other cohorts are scored and new cohorts are made. No result is invented (see item 8). | bc65f8b |
| 18 | `scripts/net_read_shadow.py` | One label that failed (after the client's own three attempts) lost every label of that run for all three lenses; labels on live releases cannot be made after their open. On a busy earnings morning that is dozens of releases. | The labels that succeeded are kept; the failed ones are reported and tried again by the next run if their open is still ahead. | this commit |

## Tests added for paths that had none
Measured with the coverage tool: the run functions of the two monthly cohort scripts (`scripts/themes.py` 0%,
`scripts/longterm_picks.py` 52%) and of the reader and judge steps had only ever been exercised by real runs. They
now have end-to-end tests with a stub model (`tests/test_monthly_cohorts.py`, `tests/test_decide_events.py`): what is
written, what is picked, that a second run in the same month does nothing, and that AI-linked themes are left out
while the bubble gauge reads high. Also new: `tests/test_weekly_review.py`, `tests/test_forward_ledger.py`.
Still only rehearsed, not unit-tested: the `main()` bodies of `forward_events.py` and `forward_allocator.py`.

## Rehearsed on scratch copies (nothing live was touched)
- Saturday's weekly review and the weekly learning review (found fix 12).
- Monday's rebalance with the aggressive book and the order mirror (dry): 3 legs planned at 1.63× at the broker.
- The event runner without the graphics card, twice, after the file-write changes.
- The AI-picks sleeve day by day to 9 Oct with made-up prices and a stand-in broker: MU and ACN exits are sent on
  the morning of 8 Oct, JBL (never filled) closes on 7 Oct with no order and no new alert, and the fill audit is
  written when a pair closes.
- The consensus shadow on the six live releases (they are recorded late and never scored, as planned).

## Noted, not changed
- Monthly cohorts rate all cards in one go (about 490 for the long-term picks). A card that keeps failing would fail
  the whole cohort, and the 75 minutes are repeated at the next run. Left as is on purpose: rating a failed card as
  "unclear" would hide a model server that died half-way, and a cohort cannot be remade. If it ever happens the
  alert names the error.
- `scripts/learn_loop.py monthly` (first run Sat 3 Oct): if it crashed between testing a proposal and saving, the
  next Saturday would test and register the same proposals again (duplicate registry lines; the results would be
  identical). Its data path was checked on the real tables today without computing any test. Left as is: the loop's
  rules were fixed with the user.

## Read and found sound, second pass
`app/portfolio/themes.py` and `scripts/themes.py` (the cards for today's first theme cohort were built on the CPU and
look right), `app/data_ingestion/edgar.py`, `app/forward/schedule.py`, the model client in `app/sandbox/walkforward.py`,
`scripts/research_queue.py` (no GPU job is waiting; the news crawl is its only running job), `scripts/trading_health.py`,
the desktop server (token on every call, local address only).

## Read and found sound
`app/portfolio/master.py` (calibration, Kelly sizing, the weight simulator), `app/portfolio/sleeve.py`,
`app/portfolio/broker.py`, `app/forward/ledger.py`, `app/sandbox/events.py`, `app/sandbox/gpu_lock.py`,
`app/signals/registry.py`, `scripts/guidance_shadow.py`, `scripts/longterm_picks.py` (rehearsed on the CPU: 489
company cards are ready for today's first cohort). A stricter lint pass over all of `app/` and `scripts/`
(bug-prone patterns, async misuse, naive datetimes) found nothing in live code. The test suite takes 26 s; its
slowest tests are deliberate timeouts.

## Still to read
Library (`app/sandbox`, `app/data_ingestion`, `app/llm`), the research runners, the desktop app's server and UI,
and the slow tests.
