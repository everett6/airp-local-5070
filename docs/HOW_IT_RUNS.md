# How airp runs today

The one current description of what runs, when, and what it writes. Written 1 Oct 2026; where another document or
a docstring disagrees with this page, this page is right and the other is history. Paper money only.

## What starts by itself

Four systemd **user** timers start `backend/scripts/autorun.py <job>`; one boot service finishes a run that a
restart cut off. Units are in `deploy/systemd/`, installed by `scripts/autonomy.sh install`. Nothing else is
scheduled; no new timers are added.

| Unit | When (New York time) | Job |
|---|---|---|
| `airp-events.timer` | Mon–Fri 08:45 and 18:30 | `events`: the earnings-release runner and its shadows |
| `airp-allocator.timer` | Mon 18:00 | `allocator`: the weekly rebalance of the paper books |
| `airp-review.timer` | Sat 10:00 | `review`: weekly and failure reviews; once a month the learning loop |
| `airp-check.timer` | daily 21:00 | `check`: missed runs, disk space, ledger anchor, app chart data |
| `airp-resume.service` | at boot | `resume`: re-runs an event run that a restart cut off (see below) |

A timer that was missed while the PC was off fires at the next boot (`Persistent=true`).

## Mode

`backend/results/forward/AUTORUN_MODE` holds `live` or `dry`. Live since 29 Sep 2026: runs write the real ledgers
under `backend/results/forward/` and commit and push that folder after every completed run. Dry runs write to
their own `*_autodry` folders and never touch git.

## What one event run does, in order

`autorun.commands("events", mode)` is the list. Each step is a script started as a child process:

1. `broker_sync.py`: mirror the book's pending orders to the Alpaca **paper** account, reconcile fills.
2. `forward_events.py`: find new S&P 500 earnings releases at the SEC, read them (fact sheet), score them with
   the judge, and append one `decision` (or `missed`) per release to the hash-chained ledger
   `results/forward/events/ledger.jsonl` before its entry open; later, the 5-day `outcome`. The nearest open is
   worked on first and each decision is written the moment the judge has it. A release's acceptance time is read
   from its filing's index page (the SEC's filing list was hours off; `docs/PLAN_60_V2.md`, rule 6). The `run`
   record holds the seconds each stage took.
3. `ai_picks.py`: the AI-picks sleeve's paper orders for decisions above the threshold. Exits follow what the
   broker actually holds: a part fill is closed for what it filled, and a pair whose exit run was missed is closed
   at the next order window (a late exit, alerted once).
4. Records, no money, live only, never able to change the run's result: `evidence_bundle.py` (one evidence file
   per decision: `results/forward/events/evidence/`) and `account_view.py` (the paper account across both books,
   read-only: `results/forward/account/view.json`; a position the books cannot explain is an alert).
5. Shadows, no money: `guidance_shadow.py`, `net_read_shadow.py`, `consensus_shadow.py`, `longterm_picks.py`
   (monthly cohort), `themes.py` (monthly), `m1_shadow.py` (month-end Treasuries), `self_improve.py`.
6. `learn_loop.py collect` (the releases' fields, and each field's percentile among earlier releases).

The Saturday review also writes, live only: `funnel.py --sweep` (where every release stopped and why:
`results/forward/funnel.json`), `ai_contribution.py` (the AI's picks against the same sleeve dealt at random),
`throughput.py` (stage times and time left before the open) and `evidence_bundle.py --check`.

Then the heartbeat line, alerts, the git push, the research queue's `tick`, and after the afternoon run the phone
digest.

Rules of the loop (`autorun.run`):
- one job at a time (`autorun.lock`); a job waits up to 20 minutes for the lock;
- if the event runner fails, the steps that read its ledger are skipped;
- side steps (`SIDE_STEPS`) can never change the run's result;
- a step that failed for a network reason is retried once;
- the log is written after every step (`results/forward/logs/`), the heartbeat at the end
  (`results/forward/heartbeat.jsonl`), alerts to `alerts.jsonl` and the phone.

## After a restart

A run writes `results/forward/running.json` while it works and removes it with its heartbeat. At boot,
`autorun.py resume` looks for that file: an **event** run cut off less than 6 hours ago is run again (its steps
skip what is already done), unless the next scheduled run is under 30 minutes away or another run is at work.
Anything else is alerted and left to the next scheduled run. The long-term ratings are saved as they are made, so
a resumed cohort does not start over.

## What guards the record

- **Pre-registration:** every test's rule is written into `docs/PLAN_60_V2.md` and pushed before its code runs;
  each is run once and recorded in `backend/results/trials_registry.jsonl` by `register()`.
- **Hash chain:** each ledger record carries the hash of the one before (`app/forward/ledger.py`); a changed or
  removed record inside the chain fails `verify`.
- **Outside anchor:** the push to GitHub after every run. The daily check alerts when a local ledger no longer
  begins with exactly its pushed bytes (a chain cut at the end still verifies by itself).
- **Power loss:** state files are replaced in one step and synced to disk; a line cut off mid-write is skipped by
  readers and the next record starts on a line of its own.
- **Limits:** the kill switch (`forward_allocator.py --halt "why"`), the mandate (`config/mandate.json`, changed
  only by the user), the order gate and the drawdown limits stay in force in every run.

## The books

`forward_allocator.py` keeps the paper books in `results/forward/allocator/` (state and a ledger line per run):
the frozen book (SPY + BTC/ETH trend with drawdown brakes, idle cash in SGOV) and the user's 2.5× aggressive
book beside it. The Alpaca paper account mirrors the aggressive book (about 1.6× there, the account's margin
limit) and the AI-picks sleeve.

## Where to look

- The desktop app (`desktop/`, installed as `~/Applications/airp.AppImage`): Home, books, AI picks, strategy lab,
  run history, research queue. It only reads.
- `docs/PLAN_V3.md` §2: the plan and its status. `docs/EXECUTIVE_SUMMARY.md`: every result.
  `docs/open_decisions.json`: what waits for the user. `docs/CODE_REVIEW_2026-10-01.md`: the last review.
- Before changing the reader or its prompt: `python scripts/extraction_benchmark.py` (12 hard releases with
  hand-labelled figures; the docstring says how to score a new reader).
- By hand: `scripts/autonomy.sh status`; `journalctl --user -u airp-events -n 50`.

## Install

`cd backend && python -m venv .venv && .venv/bin/pip install -e ".[dev]"` installs what the scheduled runs import
(`tests/test_runtime_deps.py` checks the list); `backend/requirements.lock` pins the versions in use. The models
(Ollama with `bonsai-27b`, a 12 GB NVIDIA card) are set up separately; see `docs/LOCAL_SETUP.md`.
