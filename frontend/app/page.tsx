import Link from "next/link";

export default function HomePage() {
  return (
    <div>
      <h1>AIRP Local</h1>
      <p className="muted">
        Runs against your own local LLM endpoints — no cloud API keys required.
        See <code>docs/LOCAL_SETUP.md</code> for pointing this at Ollama on your
        two GPU boxes.
      </p>

      <div className="card">
        <h2>Sandbox / Backtest</h2>
        <p>
          Test whether the system&apos;s predictions are worth anything by
          running it against a historical date. The prediction is generated
          using <em>only</em> data available as of that date — the underlying
          data connectors physically reject anything timestamped later, so
          this isn&apos;t just a UI toggle. Every run includes a live
          self-check proving that guarantee held.
        </p>
        <Link href="/sandbox">
          <button>Go to Sandbox →</button>
        </Link>
      </div>

      <div className="card">
        <h2>Live Research</h2>
        <p className="muted">
          The full multi-agent debate pipeline (fundamental/macro/risk
          analysis, bull/bear thesis, evidence verification) is wired on the
          backend but not yet exposed through this UI — see the repo&apos;s
          <code> docs/ROADMAP.md</code> for status. The Sandbox above is fully
          functional today.
        </p>
      </div>
    </div>
  );
}
