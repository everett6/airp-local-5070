"use client";

import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  BacktestResult,
  RequestAbortedError,
  getBacktestHistory,
  runBacktest,
  runBacktestSuite,
} from "@/lib/api";

function todayMinus(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function SelfCheckBadge({ result }: { result: BacktestResult }) {
  if (!result.self_check_passed) {
    return (
      <div className="self-check-fail">
        ⚠ Sandbox self-check FAILED for this run — the lookahead guarantee did
        not hold. Do not trust this result. Detail: {result.self_check_detail}
      </div>
    );
  }
  return (
    <span className="badge good">✓ lookahead guarantee verified this run</span>
  );
}

function SingleResultCard({ result }: { result: BacktestResult }) {
  return (
    <div className="card">
      <h2>
        {result.ticker} — as of {result.as_of.slice(0, 10)}
      </h2>
      <SelfCheckBadge result={result} />
      <div className="metric-grid" style={{ marginTop: 16 }}>
        <div className="metric">
          <div className="label">Price at cutoff</div>
          <div className="value">${result.price_at_as_of.toFixed(2)}</div>
        </div>
        <div className="metric">
          <div className="label">Predicted ({result.horizon_days}d)</div>
          <div className="value">${result.predicted_price.toFixed(2)}</div>
        </div>
        <div className="metric">
          <div className="label">Actual ({result.horizon_days}d)</div>
          <div className="value">${result.actual_price.toFixed(2)}</div>
        </div>
        <div className="metric">
          <div className="label">Abs % error</div>
          <div className="value">{result.absolute_pct_error.toFixed(2)}%</div>
        </div>
        <div className="metric">
          <div className="label">Direction</div>
          <div className="value">
            <span className={`badge ${result.directional_hit ? "good" : "bad"}`}>
              {result.directional_hit ? "correct" : "wrong"}
            </span>
          </div>
        </div>
      </div>
      <p className="muted">
        Predicted move: {result.predicted_return_pct.toFixed(2)}% · Actual
        move: {result.actual_return_pct.toFixed(2)}% · run_id {result.run_id}
      </p>
    </div>
  );
}

function ResultsTable({ results }: { results: BacktestResult[] }) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Ticker</th>
            <th>As of</th>
            <th>Price</th>
            <th>Predicted</th>
            <th>Actual</th>
            <th>Abs % err</th>
            <th>Direction</th>
            <th>Self-check</th>
          </tr>
        </thead>
        <tbody>
          {results.map((r) => (
            <tr key={r.run_id}>
              <td>{r.ticker}</td>
              <td>{r.as_of.slice(0, 10)}</td>
              <td>${r.price_at_as_of.toFixed(2)}</td>
              <td>${r.predicted_price.toFixed(2)}</td>
              <td>${r.actual_price.toFixed(2)}</td>
              <td>{r.absolute_pct_error.toFixed(2)}%</td>
              <td>
                <span className={`badge ${r.directional_hit ? "good" : "bad"}`}>
                  {r.directional_hit ? "hit" : "miss"}
                </span>
              </td>
              <td>
                <span className={`badge ${r.self_check_passed ? "good" : "bad"}`}>
                  {r.self_check_passed ? "ok" : "FAILED"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function SandboxPage() {
  // Date-based defaults come from `new Date()`, which is unsafe to evaluate
  // during the initial render: this "use client" component is still
  // server-rendered once for the initial HTML, then the client re-runs the
  // same render logic to hydrate it. If those two moments land on different
  // calendar days (rare, but real right around midnight), React would warn
  // about a hydration mismatch on these inputs' value/max attributes. Using
  // an empty string — identical on server and client — as the initial value,
  // then filling in the real defaults from an effect (which only ever runs
  // client-side, after hydration) avoids the possibility entirely rather
  // than just making it unlikely.
  const [ticker, setTicker] = useState("ACME");
  const [asOf, setAsOf] = useState("");
  const [horizonDays, setHorizonDays] = useState(30);
  const [maxDate, setMaxDate] = useState<string | undefined>(undefined);

  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [stepDays, setStepDays] = useState(30);

  useEffect(() => {
    // Deliberate exception to react-hooks/set-state-in-effect: these values
    // are client-only by necessity (see the comment above this component) —
    // there is no way to compute "today" identically on the server and
    // client without this pattern, and this effect runs exactly once on
    // mount, so there's no cascading-render risk the rule is protecting
    // against here.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setAsOf(todayMinus(60));
    setStartDate(todayMinus(365));
    setEndDate(todayMinus(60));
    setMaxDate(todayMinus(1));
  }, []);

  const [singleResult, setSingleResult] = useState<BacktestResult | null>(null);
  const [singleLoading, setSingleLoading] = useState(false);
  const [singleError, setSingleError] = useState<string | null>(null);
  const singleAbortRef = useRef<AbortController | null>(null);

  const [suiteResults, setSuiteResults] = useState<BacktestResult[] | null>(null);
  const [suiteLoading, setSuiteLoading] = useState(false);
  const [suiteError, setSuiteError] = useState<string | null>(null);
  const suiteAbortRef = useRef<AbortController | null>(null);

  const [history, setHistory] = useState<BacktestResult[] | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const historyAbortRef = useRef<AbortController | null>(null);

  async function loadHistory() {
    // Cancel any in-flight history request before starting a new one — a
    // slow earlier request landing after a faster later one would otherwise
    // silently show stale data as if it were current.
    historyAbortRef.current?.abort();
    const controller = new AbortController();
    historyAbortRef.current = controller;

    setHistoryLoading(true);
    try {
      const results = await getBacktestHistory({ limit: 20 }, controller.signal);
      setHistory(results);
    } catch (err) {
      if (!(err instanceof RequestAbortedError)) {
        // History is supplementary — a failure here shouldn't block the
        // rest of the page, just leave the section empty.
        setHistory([]);
      }
    } finally {
      if (historyAbortRef.current === controller) setHistoryLoading(false);
    }
  }

  useEffect(() => {
    // Same deliberate exception: an on-mount data fetch is the standard
    // pattern for "load this section's data once when the page opens", and
    // loadHistory manages its own abort/loading state internally.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadHistory();
    return () => historyAbortRef.current?.abort();
  }, []);

  async function handleSingleRun() {
    singleAbortRef.current?.abort();
    const controller = new AbortController();
    singleAbortRef.current = controller;

    setSingleLoading(true);
    setSingleError(null);
    try {
      const result = await runBacktest(
        { ticker, as_of: asOf, horizon_days: horizonDays },
        controller.signal,
      );
      setSingleResult(result);
      loadHistory();
    } catch (err) {
      if (err instanceof RequestAbortedError) return; // superseded by a newer request
      setSingleError(err instanceof ApiError ? err.message : "Request failed — is the backend running?");
      setSingleResult(null);
    } finally {
      if (singleAbortRef.current === controller) setSingleLoading(false);
    }
  }

  async function handleSuiteRun() {
    suiteAbortRef.current?.abort();
    const controller = new AbortController();
    suiteAbortRef.current = controller;

    setSuiteLoading(true);
    setSuiteError(null);
    try {
      const results = await runBacktestSuite(
        { ticker, start_date: startDate, end_date: endDate, horizon_days: horizonDays, step_days: stepDays },
        controller.signal,
      );
      setSuiteResults(results);
      loadHistory();
    } catch (err) {
      if (err instanceof RequestAbortedError) return;
      setSuiteError(err instanceof ApiError ? err.message : "Request failed — is the backend running?");
      setSuiteResults(null);
    } finally {
      if (suiteAbortRef.current === controller) setSuiteLoading(false);
    }
  }

  const suiteSummary = suiteResults?.length
    ? {
        meanAbsError:
          suiteResults.reduce((sum, r) => sum + r.absolute_pct_error, 0) / suiteResults.length,
        hitRate:
          (suiteResults.filter((r) => r.directional_hit).length / suiteResults.length) * 100,
        allSelfChecksPassed: suiteResults.every((r) => r.self_check_passed),
      }
    : null;

  return (
    <div>
      <h1>Sandbox / Backtest</h1>
      <p className="muted">
        Pick a historical date. The prediction is generated as if that date
        were &quot;today&quot; — every data connector call is checked against
        that cutoff, and anything timestamped after it is rejected rather
        than silently used. This is what makes the result below a real test
        instead of a demo.
      </p>

      <div className="card">
        <h2>Single run</h2>
        <div className="form-row">
          <div className="field">
            <label>Ticker</label>
            <input value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase())} />
          </div>
          <div className="field">
            <label>As of (must be in the past)</label>
            <input type="date" value={asOf} max={maxDate} onChange={(e) => setAsOf(e.target.value)} />
          </div>
          <div className="field">
            <label>Horizon (days)</label>
            <input
              type="number"
              min={1}
              max={365}
              value={horizonDays}
              onChange={(e) => setHorizonDays(Number(e.target.value))}
            />
          </div>
          <button onClick={handleSingleRun} disabled={singleLoading || !asOf}>
            {singleLoading ? "Running…" : "Run backtest"}
          </button>
        </div>
        {singleError && <div className="error-banner">{singleError}</div>}
        {singleResult && <SingleResultCard result={singleResult} />}
      </div>

      <div className="card">
        <h2>Suite (many dates at once)</h2>
        <p className="muted">
          Runs one backtest every <code>step_days</code> between the two
          dates, so accuracy can be judged from a distribution instead of one
          cherry-pickable date.
        </p>
        <div className="form-row">
          <div className="field">
            <label>Start date</label>
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
          </div>
          <div className="field">
            <label>End date (must be in the past)</label>
            <input type="date" value={endDate} max={maxDate} onChange={(e) => setEndDate(e.target.value)} />
          </div>
          <div className="field">
            <label>Step (days)</label>
            <input
              type="number"
              min={1}
              max={180}
              value={stepDays}
              onChange={(e) => setStepDays(Number(e.target.value))}
            />
          </div>
          <button onClick={handleSuiteRun} disabled={suiteLoading || !startDate || !endDate}>
            {suiteLoading ? "Running…" : "Run suite"}
          </button>
        </div>
        {suiteError && <div className="error-banner">{suiteError}</div>}

        {suiteSummary && (
          <>
            {!suiteSummary.allSelfChecksPassed && (
              <div className="self-check-fail" style={{ marginBottom: 16 }}>
                ⚠ At least one run in this suite failed its lookahead
                self-check. Inspect the table below before trusting any
                number in this summary.
              </div>
            )}
            <div className="metric-grid">
              <div className="metric">
                <div className="label">Runs</div>
                <div className="value">{suiteResults!.length}</div>
              </div>
              <div className="metric">
                <div className="label">Mean abs % error</div>
                <div className="value">{suiteSummary.meanAbsError.toFixed(2)}%</div>
              </div>
              <div className="metric">
                <div className="label">Directional hit rate</div>
                <div className="value">{suiteSummary.hitRate.toFixed(0)}%</div>
              </div>
            </div>
          </>
        )}

        {suiteResults && <ResultsTable results={suiteResults} />}
      </div>

      <div className="card">
        <div className="card-header-row">
          <h2>Recent history</h2>
          <button className="secondary" onClick={loadHistory} disabled={historyLoading}>
            {historyLoading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
        <p className="muted">
          Persisted across restarts (SQLite on disk) — every run above, plus
          any you&apos;ve made before, up to the last 20.
        </p>
        {history === null && <p className="muted">Loading…</p>}
        {history?.length === 0 && <p className="muted">No backtests run yet.</p>}
        {history && history.length > 0 && <ResultsTable results={history} />}
      </div>

      <p className="muted">
        Note: the backtest&apos;s price data is a deterministic synthetic
        series (a pure function of ticker + date), not real market history —
        it exists to prove the point-in-time isolation mechanism works, not
        to produce real trading signals. See{" "}
        <code>backend/app/sandbox/synthetic_market.py</code>. Swapping in a
        real point-in-time market data provider is the next step for actually
        meaningful backtests (docs/ROADMAP.md).
      </p>
    </div>
  );
}
