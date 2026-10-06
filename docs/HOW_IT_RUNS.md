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
   **Entry rule (one for decisions, orders and scoring):** a release accepted before 13:00 UTC on a trading
   session enters at that session's open, anything later at the next session's; sessions follow the exchange's
   holiday calendar (`app/forward/schedule.py`). The deadline is 09:30 New York on that session and is stored
   with the decision (`entry_session`). **On time** is judged on the clock just before the line is written
   (`decided_at`), not on the run's start (`as_of`).
   **Fact sheet version 2** (live since its check passed on 1 Oct): the reader's year-earlier revenue and EPS
   are reconciled with the SEC-filed quarters before the judge sees them (`app/sandbox/reconcile.py`); each
   decision stores `sheet_version`.
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
  run history, research queue, and Run Center. Run Center can start the current AI paper strategy, sync pending
  paper orders, and run backend, app, or broker recovery regression tests. Paper actions require typing `PAPER`.
  Software tests use synthetic data; they do not rerun registered strategy trials or place orders.
- `docs/PLAN_V3.md` §2: the plan and its status. `docs/EXECUTIVE_SUMMARY.md`: every result.
  `docs/open_decisions.json`: what waits for the user. `docs/CODE_REVIEW_2026-10-01.md`: the last review.
- Before changing the reader or its prompt: `python scripts/extraction_benchmark.py` (12 hard releases with
  hand-labelled figures; the docstring says how to score a new reader).
- By hand: `scripts/autonomy.sh status`; `journalctl --user -u airp-events -n 50`.

### Run Center

Choose **Run AI paper trades** for Jan extraction and jailed research using sources available at filing acceptance,
then Bonsai judgment of the source-checked enriched fact sheets. Jan and Bonsai use the GPU sequentially.
Missing GPU capacity, failed extraction or unusable research defers the filing; this app path has no lite fallback.
No new filings means neither model runs. Research files, hashes, model details and exact judge inputs are saved
with each decision. This new paper input path is marked `jan_bonsai_v1`; its added research has no proven return
advantage. Scheduled baseline runs retain their existing pipeline. Eligible picks use current sizing and entry rules. Choose **Sync paper
orders** to reconcile both books, submit pending orders, and update the account snapshot. These actions use
the fixed Alpaca paper endpoint. They do not change the strategy, mandate, mode, or scheduled jobs. A completed
AI run may select nothing; a submitted order is not a fill. Check AI picks and Live book & orders for positions.

The detached `scripts/desktop_run.py` worker holds the same `autorun.lock` as the automatic jobs. A conflict
blocks the manual action immediately. Paper runs cannot start within 30 minutes of an existing timer slot;
steps have a time limit that releases the lock before that slot. The app never runs `autorun.py` manually or
counts an app run as a scheduled heartbeat. The current timer slots are mirrored in `desktop_run.next_slot`;
update that list if the installed schedule changes. AI starts are refused while halted or reducing. Order sync
still follows the broker's existing halt/cancel rules.

Progress, step exit codes, and logs live in `backend/results/desktop_runs/<id>/`. The Run Center refreshes every
five seconds and keeps the latest 30 runs visible. Closing the app leaves its worker running. After a PC restart,
an unfinished worker appears as interrupted; inspect its log and orders, then start a new run if needed. Existing
client order IDs prevent duplicate submissions. The worker does not commit or push; the usual automatic run
anchors any new live decision records on its next push. The daily anchor check can flag records awaiting that push.

### Self-improvement in the app

The Self-improvement page exposes the existing bounded learning engines and their fixed evidence gates.
It shows the current bull/bear shadow prompt, challenger, learned lessons, matured-call count, paired forward
evidence, recipe stages, rejection/retirement reasons, and the latest signal review. Missing or damaged engine
files appear as errors rather than an invented healthy state. These engines learn during the existing automatic
earnings and Saturday review jobs; no extra timer is installed.

**Review self-improvement** collects existing decision-time features and scores previously created shadows under
the same scheduler lock as other runs. It can qualify, retire, or roll back a shadow under the already registered
rules. It does not generate new trial proposals, use the GPU, place orders, or change the frozen book. Monthly
proposal generation remains on the existing schedule. Manual AI paper runs also collect their learning evidence.
The app calls a qualified recipe "Qualified in simulation": the registry's promoted status is a research sleeve,
not permission to change the broker's live strategy. Strategy activation remains a separate user decision.

## Install

`cd backend && python -m venv .venv && .venv/bin/pip install -e ".[dev]"` installs what the scheduled runs import
(`tests/test_runtime_deps.py` checks the list); `backend/requirements.lock` pins the versions in use. The models
(Ollama with `bonsai-27b`, a 12 GB NVIDIA card) are set up separately; see `docs/LOCAL_SETUP.md`.

### Engineering checks and source review

The desktop has an Engineering checks page for account risk, measured execution, factor attribution, checked
release candidates, backup/restore and reviewer attestations. Source review opens the cached benchmark filings
and their provisional labels for a person to correct and verify. Engineering actions run through Run Center,
share the scheduler lock and do not submit orders. Paper-order actions additionally enforce the shared account
policy in `backend/config/account_risk.json` before every new submission. Details and remaining prerequisites
are in `docs/INSTITUTIONAL_UPGRADE.md`.

Jan → Bonsai app update validated 3 October 2026: 770 backend regression tests and 20 app tests passed;
changed runner/evidence modules passed Ruff and strict type checking. An offscreen Run Center smoke test
confirmed the new controls, and the rebuilt AppImage was installed with a matching SHA-256. Model calls
in the regression tests were simulated; this validation did not place paper orders or rerun old decisions.

### Fresh research and company latency

Run Center's **Live research test** runs prospective experiment LRF1 on AAPL, MSFT, NVDA, JPM and XOM.
Jan fetches current public news, SEC filings and recent prices using a free-only live gateway, without the
historical research dataset or its cache. Evidence is saved after retrieval so decisions can be audited.
Bonsai rates day, 21-session medium and 63-session long theses. Source quotes and numeric citations are
checked; they are not human-verified facts or calibrated return probabilities. Server startup is measured
separately; actual cold model loading is included in the first company's model stage.

The latest cohort appears in Run Center with research time, judge time, queue wait, cohort-to-decision
latency, failures, median/p95/max, and bottleneck suggestions. Each company is saved immediately; an
interruption preserves completed attempts. The three algorithm names identify experimental thesis
families, not validated execution algorithms. Fixed virtual caps are day 10%, medium 30%, long 50%, cash
at least 10%, no leverage, and each company at most 10% across horizons. Unused capacity stays cash.
Experimental targets are not submitted and their returns remain unavailable without future entry/exit
fills. An equal-weight no-AI proposal uses the same watchlist/company cap, including failed research names.

**Live research + AI paper trades** runs that experiment, then the existing Jan→Bonsai earnings-filing
workflow, evidence capture, approved paper-order synchronization and account snapshot. Only those
existing five-day filing picks can become broker orders; they can involve different companies from the
experiment. A run may submit none. Both actions share the scheduler lock and defer near automatic jobs;
the combined paper action requires PAPER confirmation and honors the halt and account risk controls.
No new timer, paid API, model download, strategy-trial registration or frozen-book change is involved.

External references reviewed: the user's Jev article describes bounded structured decisions; its vendor
lists paid API plans (https://thejevai.com/). Jev is not enabled in this free-only system. FidetoLabs' Qanat
README (https://github.com/fidetolabs/qanat/blob/main/README.md) describes timestamped processing and
net-of-fee evaluation. The supplied Instagram post could not be read; no video claim has been verified.

LRF1 implementation validation on 3 October 2026: 776 synthetic backend tests and 21 app tests passed.
The new experiment/worker passed Ruff and strict type checks. Run Center rendered in a separate offscreen
Electron smoke test; the rebuilt installed AppImage hash matches the build. No real-model research cohort,
performance trial, paper order or new scheduled job was run during this implementation. Company timings
and forward outcomes remain unmeasured until the user starts the appropriate action.

### Economic discipline comparison

Run Center's **Test economic discipline** starts BE1, a research-only matched prompt experiment.
Jan gathers one current evidence snapshot per company. Bonsai receives the identical card in two arms:
neutral LRF1 wording, and LRF1 plus resource-discipline wording. Both have the same code-enforced 120-second
inference timeout, 700 generated-token cap, 8192 context, one request at a time, no cache and no additional
tools. PASS is valid; no profit quota, survival threat, wallet, paid API, automatic winner or order is involved.
Arm order alternates by company index. Each result or failure is saved immediately with prompt, reply,
card hash, model digest, limits, timing and quote checks. Common Jan costs are shared, not counted twice.

Run Center displays arm timing/failure summaries. Reports also retain separate bounded virtual targets,
unsupported claims, response character counts and paired timing differences. Characters are not token
counts; verified quotes are not independent factual accuracy. Cold model loading, order and small samples
can affect timing. This tests Bonsai judgment wording, not budget-aware Jan research or a retrained model.
No return, drawdown, actual-fill turnover, calibration or statistical advantage is reported without future
matched executions/outcomes and independent truth labels. No compute or capital promotion is automatic.

BE1 implementation validation: 782 backend regression tests and 22 app tests passed; Ruff and strict mypy passed for the experiment and worker scripts. An offscreen Run Center check caught and corrected the latency number formatter, then verified the experiment control renders. All model tests used synthetic responses. No real BE1 cohort or paper orders were started during implementation.

### Alpaca account tab

The Alpaca account sidebar tab reads current paper-account details through Alpaca’s read-only `/v2/account` endpoint ([API reference](https://docs.alpaca.markets/us/reference/getaccount-1)). It shows balances, buying power, margin, broker multiplier, reported permissions and restrictions, and a masked account number. Refresh reads the account again; it sends no orders and writes no trading records. The reconciled exposure panel uses the existing saved account snapshot and shows its separate timestamp. Missing fields are shown as not reported. API response fields are allowlisted; credentials and full identifiers are excluded. Synthetic account-client checks, all 22 app tests, syntax checks, Ruff and strict mypy passed.

### Reliability revisions and Horizon shadows

Restart the app for the Horizon shadows page. New Live research test cohorts lock an HS1 AI/no-AI plan; Run Center’s Review horizon shadows refreshes free IEX price proxies without orders or models. Existing cohorts are excluded. Day trades enter the next session open and exit that close; medium/long hold 21/63 sessions. Full rules and limitations are in PLAN_60_V2 and INSTITUTIONAL_UPGRADE.

Update engineering evidence now also refreshes AI-contribution reconciliation. The legacy comparison stays blocked while it differs from the simulator. New paper syncs preserve exact simulator inputs for reproducible prospective comparisons. Source review includes candidate numeric excerpts; independent human verification is still required. Cost evidence binds to known fills as well as execution records. Missing arrivals and unknown fees remain incomplete, not zero. Quote-blocked paper intents retry the original client IDs without exhausting the actual broker-order retry allowance.

Validation: 793 backend and 23 app tests passed, Ruff/strict mypy passed, and all app pages rendered without errors. No real-model cohort or order was executed. Closed-market quotes currently block the read-only exit probe under the unchanged risk policy.
