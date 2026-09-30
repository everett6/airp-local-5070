// Dependency-free SVG charts for the airp desktop app (dataviz rules: one y-axis, thin marks, recessive grid,
// legend for 2+ series plus direct end labels, text in text colors, crosshair + tooltip on hover).
// Categorical palette validated for the dark surface #181816 (blue, orange, aqua, yellow; all checks pass).
export const PALETTE = ['#3987e5', '#d95926', '#199e70', '#c98500'];
export const LIME = '#d4f579';
const INK = '#eeede8', MUTED = '#8f8f87', GRID = '#2b2b27', ZERO = '#55554c';
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const pct = (v, d = 1) => (v == null ? '–' : `${(v * 100).toFixed(d)}%`);
const niceTicks = (lo, hi, n = 5) => {
  const span = hi - lo || Math.abs(hi) || 1, step0 = span / n, mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0);
  const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
};

let uid = 0;
// series: [{ name, x: ['YYYY-MM-DD'...], y: [number|null...], color? }]; opts: { height, yFmt, area, zero, log }
export function lineChart(series, opts = {}) {
  const id = `c${++uid}`, W = opts.width || 900, H = opts.height || 260, L = 56, R = series.length > 1 ? 110 : 20, T = 14, B = 26;
  const all = series.flatMap((s) => s.y.filter((v) => v != null));
  if (!all.length) return '<p class="empty">No data yet.</p>';
  let lo = Math.min(...all, opts.zero ? 0 : Infinity), hi = Math.max(...all, opts.zero ? 0 : -Infinity);
  const pad = (hi - lo) * 0.06 || 1; lo -= opts.area ? 0 : pad; hi += pad;
  const dates = [...new Set(series.flatMap((s) => s.x))].sort();
  const xi = new Map(dates.map((d, i) => [d, i]));
  const X = (d) => L + (xi.get(d) / Math.max(1, dates.length - 1)) * (W - L - R);
  const Y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const fmt = opts.yFmt || ((v) => v.toFixed(0));
  const ticks = niceTicks(lo, hi, 5);
  const years = [...new Set(dates.map((d) => d.slice(0, 4)))];
  const yearStep = Math.ceil(years.length / 9);
  let svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(series.map((s) => s.name).join(', '))}" data-chart="${id}">`;
  for (const t of ticks) svg += `<line x1="${L}" x2="${W - R}" y1="${Y(t)}" y2="${Y(t)}" stroke="${t === 0 ? ZERO : GRID}" stroke-width="1"/><text x="${L - 8}" y="${Y(t) + 4}" fill="${MUTED}" font-size="11" text-anchor="end">${esc(fmt(t))}</text>`;
  years.forEach((y, i) => { if (i % yearStep) return; const d = dates.find((x) => x.startsWith(y)); svg += `<text x="${X(d)}" y="${H - 6}" fill="${MUTED}" font-size="11">${y}</text>`; });
  const labels = [];
  series.forEach((s, k) => {
    const color = s.color || (series.length === 1 ? LIME : PALETTE[k]);
    const pts = s.x.map((d, i) => [d, s.y[i]]).filter(([, v]) => v != null);
    if (!pts.length) return;
    const path = pts.map(([d, v], i) => `${i ? 'L' : 'M'}${X(d).toFixed(1)},${Y(v).toFixed(1)}`).join('');
    if (opts.area) svg += `<path d="${path}L${X(pts.at(-1)[0])},${Y(0)}L${X(pts[0][0])},${Y(0)}Z" fill="${color}" opacity=".16"/>`;
    svg += `<path d="${path}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>`;
    if (series.length > 1) labels.push({ y: Y(pts.at(-1)[1]), name: s.name, color, v: pts.at(-1)[1] });
  });
  labels.sort((a, b) => a.y - b.y).forEach((l, i, arr) => { if (i && l.y - arr[i - 1].y < 14) l.y = arr[i - 1].y + 14; });
  for (const l of labels) svg += `<rect x="${W - R + 8}" y="${l.y - 4}" width="8" height="8" rx="2" fill="${l.color}"/><text x="${W - R + 20}" y="${l.y + 4}" fill="${INK}" font-size="11">${esc(l.name)} ${esc(fmt(l.v))}</text>`;
  svg += `<line class="xh" x1="0" x2="0" y1="${T}" y2="${H - B}" stroke="${MUTED}" stroke-dasharray="3 3" visibility="hidden"/>`;
  svg += `<rect class="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent"/></svg>`;
  const legend = series.length > 1 ? `<div class="legend">${series.map((s, k) => `<span><i style="background:${s.color || PALETTE[k]}"></i>${esc(s.name)}</span>`).join('')}</div>` : '';
  registry.set(id, { series, dates, X, L, R, W, fmt });
  return `<div class="chart-wrap">${legend}${svg}<div class="tip" hidden></div></div>`;
}

// Grouped bars (e.g. yearly returns per series): cats = ['2019', ...], series = [{ name, y: [...] }]
export function barChart(cats, series, opts = {}) {
  const W = opts.width || 900, H = opts.height || 230, L = 56, R = 20, T = 14, B = 26;
  const all = series.flatMap((s) => s.y.filter((v) => v != null));
  if (!all.length) return '<p class="empty">No data yet.</p>';
  const lo = Math.min(0, ...all), hi = Math.max(0, ...all), padv = (hi - lo) * 0.08;
  const Y = (v) => T + (1 - (v - (lo - padv)) / (hi + padv - (lo - padv))) * (H - T - B);
  const fmt = opts.yFmt || ((v) => pct(v, 0));
  const band = (W - L - R) / cats.length, bw = Math.max(3, (band - 8) / series.length - 2);
  let svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img">`;
  for (const t of niceTicks(lo - padv, hi + padv, 5)) svg += `<line x1="${L}" x2="${W - R}" y1="${Y(t)}" y2="${Y(t)}" stroke="${t === 0 ? ZERO : GRID}"/><text x="${L - 8}" y="${Y(t) + 4}" fill="${MUTED}" font-size="11" text-anchor="end">${esc(fmt(t))}</text>`;
  cats.forEach((c, i) => {
    const x0 = L + i * band + 4;
    series.forEach((s, k) => {
      const v = s.y[i]; if (v == null) return;
      const x = x0 + k * (bw + 2), y = Math.min(Y(v), Y(0)), h = Math.max(1, Math.abs(Y(v) - Y(0)));
      svg += `<rect x="${x}" y="${y}" width="${bw}" height="${h}" rx="2" fill="${series.length === 1 ? LIME : PALETTE[k]}"><title>${esc(s.name)} ${esc(c)}: ${esc(fmt(v))}</title></rect>`;
    });
    svg += `<text x="${x0 + (band - 8) / 2}" y="${H - 6}" fill="${MUTED}" font-size="11" text-anchor="middle">${esc(c)}</text>`;
  });
  svg += '</svg>';
  const legend = series.length > 1 ? `<div class="legend">${series.map((s, k) => `<span><i style="background:${PALETTE[k]}"></i>${esc(s.name)}</span>`).join('')}</div>` : '';
  return `<div class="chart-wrap">${legend}${svg}</div>`;
}

// Estimate with 95% CI per row (strategy tests): rows = [{ label, group, v, lo, hi, pass }]; reference lines.
export function ciChart(rows, opts = {}) {
  const W = 900, rowH = 26, L = 330, R = 80, T = 24, H = T + rows.length * rowH + 20;
  const vals = rows.flatMap((r) => [r.v, r.lo, r.hi]).filter((v) => v != null);
  const lo = Math.min(-1, ...vals) - 0.2, hi = Math.max(1.5, ...vals) + 0.2;
  const X = (v) => L + ((v - lo) / (hi - lo)) * (W - L - R);
  let svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img">`;
  for (const t of niceTicks(lo, hi, 8)) svg += `<line x1="${X(t)}" x2="${X(t)}" y1="${T - 6}" y2="${H - 16}" stroke="${t === 0 ? ZERO : GRID}"/><text x="${X(t)}" y="${H - 2}" fill="${MUTED}" font-size="11" text-anchor="middle">${t}</text>`;
  for (const ref of opts.refs || []) svg += `<line x1="${X(ref.v)}" x2="${X(ref.v)}" y1="${T - 10}" y2="${H - 16}" stroke="${LIME}" stroke-dasharray="4 3"/><text x="${X(ref.v) + 4}" y="${T - 12}" fill="${INK}" font-size="11">${esc(ref.label)}</text>`;
  rows.forEach((r, i) => {
    const y = T + i * rowH + rowH / 2;
    svg += `<text x="${L - 12}" y="${y + 4}" fill="${INK}" font-size="12" text-anchor="end">${esc(r.label)}</text><text x="12" y="${y + 4}" fill="${MUTED}" font-size="10">${esc(r.group)}</text>`;
    if (r.v == null) { svg += `<text x="${X(0) + 6}" y="${y + 4}" fill="${MUTED}" font-size="11">no Sharpe recorded</text>`; return; }
    const c = r.pass ? LIME : '#8f8f87';
    if (r.lo != null) svg += `<line x1="${X(r.lo)}" x2="${X(r.hi)}" y1="${y}" y2="${y}" stroke="${c}" stroke-width="2" stroke-linecap="round"/>`;
    svg += `<circle cx="${X(r.v)}" cy="${y}" r="5" fill="${c}" stroke="#181816" stroke-width="2"><title>${esc(r.label)}: Sharpe ${r.v} (95% CI ${r.lo} to ${r.hi})</title></circle>`;
    svg += `<text x="${W - R + 10}" y="${y + 4}" fill="${r.pass ? INK : MUTED}" font-size="11">${r.pass ? '✓ pass' : '✕ fail'} ${esc(r.v)}</text>`;
  });
  return `<div class="chart-wrap">${svg}</div>`;
}

// Histogram with an optional threshold marker (AI decision scores).
export function histogram(values, opts = {}) {
  if (!values.length) return '<p class="empty">No live decisions yet.</p>';
  const W = opts.width || 900, H = opts.height || 200, L = 40, R = 20, T = 14, B = 26, bins = opts.bins || 20;
  const lo = Math.min(...values, opts.mark ?? Infinity), hi = Math.max(...values, opts.mark ?? -Infinity), w = (hi - lo) / bins || 1;
  const counts = Array(bins).fill(0); for (const v of values) counts[Math.min(bins - 1, Math.floor((v - lo) / w))]++;
  const top = Math.max(...counts), X = (v) => L + ((v - lo) / (hi - lo || 1)) * (W - L - R), Y = (c) => T + (1 - c / top) * (H - T - B);
  let svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img">`;
  counts.forEach((c, i) => { if (c) svg += `<rect x="${X(lo + i * w) + 1}" y="${Y(c)}" width="${Math.max(2, X(lo + w) - X(lo) - 2)}" height="${Y(0) - Y(c)}" rx="2" fill="${LIME}"><title>${(lo + i * w).toFixed(1)} to ${(lo + (i + 1) * w).toFixed(1)}: ${c}</title></rect>`; });
  for (const t of niceTicks(lo, hi, 8)) svg += `<text x="${X(t)}" y="${H - 6}" fill="${MUTED}" font-size="11" text-anchor="middle">${t}</text>`;
  if (opts.mark != null) svg += `<line x1="${X(opts.mark)}" x2="${X(opts.mark)}" y1="${T}" y2="${H - B}" stroke="${INK}" stroke-dasharray="4 3"/><text x="${X(opts.mark) + 4}" y="${T + 10}" fill="${INK}" font-size="11">${esc(opts.markLabel || '')}</text>`;
  return `<div class="chart-wrap">${svg + '</svg>'}</div>`;
}

// Hover: crosshair + tooltip for every line chart on the page (one delegated listener).
const registry = new Map();
export function attachHover(root) {
  root.querySelectorAll('svg[data-chart]').forEach((svg) => {
    const meta = registry.get(svg.dataset.chart); if (!meta) return;
    const tip = svg.parentElement.querySelector('.tip'), xh = svg.querySelector('.xh');
    svg.querySelector('.hit').addEventListener('mousemove', (e) => {
      const box = svg.getBoundingClientRect(), x = ((e.clientX - box.left) / box.width) * meta.W;
      const i = Math.round(((x - meta.L) / (meta.W - meta.L - meta.R)) * (meta.dates.length - 1));
      const d = meta.dates[Math.max(0, Math.min(meta.dates.length - 1, i))];
      xh.setAttribute('x1', meta.X(d)); xh.setAttribute('x2', meta.X(d)); xh.setAttribute('visibility', 'visible');
      const rows = meta.series.map((s, k) => { const j = s.x.indexOf(d); const v = j >= 0 ? s.y[j] : null;
        return v == null ? '' : `<div><i style="background:${s.color || (meta.series.length === 1 ? LIME : PALETTE[k])}"></i>${esc(s.name)} <b>${esc(meta.fmt(v))}</b></div>`; }).join('');
      tip.innerHTML = `<div class="tip-date">${esc(d)}</div>${rows}`; tip.hidden = false;
      tip.style.left = `${Math.min(box.width - 170, (meta.X(d) / meta.W) * box.width + 12)}px`;
    });
    svg.querySelector('.hit').addEventListener('mouseleave', () => { tip.hidden = true; xh.setAttribute('visibility', 'hidden'); });
  });
}
