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

## Also fixed in this pass

| # | Where | What | Commit |
|---|---|---|---|
| 9 | `scripts/digest.py` | The phone digest called a run that did its job but raised a broker alert "FAILED". It now says "finished with an alert". | 8824bf9 |
| 10 | `app/portfolio/broker.py`, `scripts/ai_picks.py` | Class shares (BRK-B, BF-B) were sent to Alpaca with a dash. Alpaca only knows BRK.B (read-only lookup, 1 Oct): the order would have been rejected, leaving the pair's sector-ETF short open without its stock, and one such name in a price request makes the whole request fail. Not hit yet (no class share has been picked). | The book keeps the dash; orders, price requests and positions use the broker's dot. | daf49b0 |
| 11 | `app/sandbox/walkforward.py` | The one-token probability call behind the monthly long-term and theme ratings had no retry: one dropped connection in about 500 calls would have failed the whole cohort (75 minutes of GPU, repeated at the next run). | Three attempts, as the main call already had. Answers unchanged. | 69c6574 |
| 12 | `scripts/weekly_review.py`, `forward_allocator.py --status`, `desktop_export.py`, `desktop/src/airp.js` | A week in which the aggressive book fails is stored as an error entry without numbers (fix 3). Four readers took `equity` from every entry: the Saturday review would have crashed, the status command too, and the app's book table would have shown broken numbers. Found by rehearsing the review on a scratch copy. | They leave such an entry out; the review says the book's last run failed. The review and the health report also skip a ledger line cut off by a crash. | this commit |

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
