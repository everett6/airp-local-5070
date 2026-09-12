# The Sandbox: Honest Backtesting

## The problem this solves

Any system that predicts stock prices can be made to look good after the
fact if it has any way — even accidentally — to see what actually happened
before making its "prediction." This is called lookahead bias, and it's the
single easiest way to fool yourself about whether a research system is
actually any good. A UI toggle labeled "backtest mode" that just changes
which numbers get displayed doesn't prevent this; the underlying data access
has to be physically incapable of reaching future information.

## How the guarantee actually works

Three pieces, in `backend/app/sandbox/`:

### 1. `clock.py` — the enforcement point

`sandbox_scope(as_of, run_id)` activates a `contextvars.ContextVar` for the
duration of one backtest run. Every data connector call goes through
`enforce_point_in_time(timestamp, source)`, which raises `LookaheadViolation`
— immediately, without retry — if `timestamp` is after the active `as_of`
cutoff. Outside a sandbox scope (normal live research), this is a no-op:
live research is never restricted, only backtests are.

This is a `contextvars.ContextVar`, not a global variable, specifically so
concurrent backtests (different tasks testing different historical dates at
the same time) don't interfere with each other — each asyncio task sees only
its own active clock.

### 2. `synthetic_market.py` — data with no wall-clock dependency

The mock market data connector needs *some* price data to test against. It
uses `deterministic_price(ticker, date)`: a pure closed-form function of
(ticker, calendar date) only — never `datetime.now()`, never a random seed
drawn at call time. Asking for the same (ticker, date) always returns the
same price, whether you ask today or in five years. This matters because a
mock connector that secretly incorporated real wall-clock time would make
"pretend today is 2024-03-01" meaningless — it would still be leaking
information from whatever day you actually ran the test.

This is explicitly a synthetic proof-of-mechanism, not a market simulator —
see the module's docstring. Swapping in a real point-in-time market data
provider is a distinct, separate piece of future work (`docs/ROADMAP.md`);
until then, treat every backtest number as "did the plumbing work," not
"is this a real trading signal."

### 3. `backtest.py` — the runner, with a live self-check

`run_backtest(ticker, as_of, horizon_days)`:

1. Enters the sandbox scope at `as_of`.
2. Generates a prediction (currently a simple deterministic momentum
   extrapolation — see the module docstring for why the prediction strategy
   is intentionally minimal for now) using only connector calls that pass
   through the point-in-time check.
3. **Before leaving the sandbox**, deliberately attempts to fetch the future
   outcome the same way a buggy connector or agent might, and confirms that
   attempt is blocked. This is `self_check_passed` in the result — a
   guarantee that's checked live, on every single run, not just once in a
   test suite. If a future code change ever weakens the enforcement, this
   would surface as a failed run in production, not just a red CI badge that
   might go unnoticed.
4. Exits the sandbox and *then* looks up the real outcome at
   `as_of + horizon_days` — safe to do now, exactly like waiting for time to
   actually pass would be in a real backtest.
5. Grades the prediction: absolute % error, directional hit/miss.

`run_backtest_suite` runs this across many dates so you can look at a
distribution (mean error, hit rate) instead of one cherry-pickable date —
this is what the frontend's Sandbox page's "Suite" section drives.

## Using it

Via the UI: `/sandbox` — pick a ticker, a historical date, and a horizon,
or run a suite across a date range. Via the API directly:

```bash
curl -X POST http://localhost:8000/api/sandbox/backtest \
  -H "Content-Type: application/json" \
  -d '{"ticker":"ACME","as_of":"2024-03-01","horizon_days":30}'
```

The API rejects `as_of` dates that aren't in the past (HTTP 400) — the
sandbox is for testing against known history, not for constructing a "backtest"
of today.

## What would make this a real trading tool instead of a proof-of-mechanism

Two things, tracked in `docs/ROADMAP.md`:

1. A real point-in-time market data provider behind the same
   `DataConnector` interface (so `enforce_point_in_time` gates *actual*
   historical data instead of a synthetic formula).
2. A real prediction strategy — the current momentum extrapolation is
   deliberately the simplest possible thing that could be graded; an
   LLM-assisted thesis (using `app/llm/router.py` and, per
   `docs/CONTEXT_COMPRESSION.md`, a compressed context pack of the ticker's
   research material as of the cutoff date) is the natural next strategy to
   add, as long as it goes through the same sandboxed connectors.

Both are additive — neither requires changing the enforcement mechanism
itself, which is the part that has to be trustworthy.
