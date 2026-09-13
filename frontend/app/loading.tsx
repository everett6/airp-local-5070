export default function Loading() {
  return (
    <div className="card" aria-busy="true" aria-label="Loading">
      <div className="skeleton-line" style={{ width: "40%", height: 24 }} />
      <div className="skeleton-line" style={{ width: "90%", marginTop: 14 }} />
      <div className="skeleton-line" style={{ width: "75%" }} />
    </div>
  );
}
