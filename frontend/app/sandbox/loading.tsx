export default function SandboxLoading() {
  return (
    <div>
      <div className="skeleton-line" style={{ width: "30%", height: 28, marginBottom: 20 }} />
      <div className="card" aria-busy="true" aria-label="Loading sandbox">
        <div className="skeleton-line" style={{ width: "25%", height: 20 }} />
        <div className="skeleton-line" style={{ width: "100%", height: 40, marginTop: 16 }} />
      </div>
      <div className="card" aria-busy="true">
        <div className="skeleton-line" style={{ width: "35%", height: 20 }} />
        <div className="skeleton-line" style={{ width: "100%", height: 40, marginTop: 16 }} />
      </div>
    </div>
  );
}
