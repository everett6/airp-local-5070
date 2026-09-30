// airp desktop UI: plain modules, no framework. Every value is escaped before it reaches the page.
import { attachHover, barChart, ciChart, histogram, lineChart, pct } from './charts.js';
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (n, d = 0) => (n == null || Number.isNaN(n) ? '–' : Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d }));
const when = (s) => (s ? new Date(s).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '–');
const pill = (text, kind = '') => `<span class="pill ${kind}">${esc(text)}</span>`;

async function api(path, body) {
  const r = await fetch(`/api/${path}`, body ? { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) } : {});
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

const views = {
  async overview() {
    const [o, m] = await Promise.all([api('overview'), metrics().catch(() => ({ missing: true }))]);
    const runs = o.recentRuns.filter((r) => r.job !== 'check');
    const clean = runs.slice(0, 10).filter((r) => r.rc === 0).length;
    const next = (o.timers || []).map((t) => ({ unit: t.unit || t.activates, next: t.next })).filter((t) => t.next);
    return `<h2>Overview</h2><p class="lede">The paper book runs itself on timers; this is what it has done.</p>
      ${m.missing ? '' : `<div class="kpis">
        ${kpi('Core CAGR', pct(m.tracks.core.stats.cagr), `SPY ${pct(m.tracks.SPY.stats.cagr)}`)}
        ${kpi('Core Sharpe', esc(m.tracks.core.stats.sharpe), `SPY ${esc(m.tracks.SPY.stats.sharpe)}`)}
        ${kpi('Core max DD', pct(m.tracks.core.stats.max_dd), `SPY ${pct(m.tracks.SPY.stats.max_dd)}`)}
        ${kpi('Tests passed', `${m.lab.tests.filter((x) => x.pass).length}/${m.lab.tests.length}`, 'strategy lab')}
        ${kpi('Live AI decisions', m.live.decisions.length, `pick threshold ${esc(m.live.ai_threshold ?? '–')}`)}
      </div>
      <div class="card wide"><h3>Core book vs SPY · growth of 100 (backtest)</h3>${lineChart([
        { name: 'Core book', x: m.tracks.core.dates, y: m.tracks.core.equity },
        { name: 'SPY', x: m.tracks.SPY.dates, y: m.tracks.SPY.equity }], { height: 220, yFmt: (v) => v.toFixed(0) })}</div>`}
      <div class="grid">
        <div class="card"><div class="label">Mode</div><div class="stat">${esc(o.mode)}</div>${o.halted ? pill('HALTED', 'bad') : pill('trading', 'ok')}</div>
        <div class="card"><div class="label">Last 10 runs</div><div class="stat">${clean}/${Math.min(10, runs.length)}</div>${pill('clean', clean === Math.min(10, runs.length) ? 'ok' : 'warn')}</div>
        <div class="card"><div class="label">Alerts (last 40)</div><div class="stat">${o.alerts.length}</div>${o.alerts[0] ? `<span class="muted">latest ${esc(when(o.alerts[0].at))}</span>` : ''}</div>
        <div class="card"><div class="label">Last digest</div><div class="stat">${o.digest ? esc(when(o.digest.at)) : '–'}</div>${o.digest ? pill(o.digest.sent ? 'sent to phone' : 'not sent', o.digest.sent ? 'ok' : 'warn') : ''}</div>
        ${o.digest ? `<div class="card wide"><h3>Today's digest</h3><pre class="md">${esc(o.digest.text)}</pre></div>` : ''}
        <div class="card wide"><h3>Scheduled jobs</h3><table><tr><th>Job</th><th>Next run</th></tr>
          ${next.map((t) => `<tr><td>${esc(String(t.unit).replace('.service', ''))}</td><td>${esc(when(Number(t.next) / 1000))}</td></tr>`).join('') || '<tr><td colspan="2" class="muted">timers not visible</td></tr>'}</table></div>
        <div class="card wide"><h3>Recent runs</h3><table><tr><th>Job</th><th>Mode</th><th>Started</th><th>Result</th><th class="num">Missed</th></tr>
          ${o.recentRuns.slice(0, 15).map((r) => `<tr><td>${esc(r.job)}</td><td>${esc(r.mode)}</td><td>${esc(when(r.start))}</td><td>${r.rc === 0 ? pill('ok', 'ok') : pill(`rc ${r.rc}`, 'bad')}</td><td class="num">${esc(r.missed_total ?? '')}</td></tr>`).join('')}</table></div>
        <div class="card wide"><h3>Alerts</h3><table><tr><th>When</th><th>Job</th><th>Message</th></tr>
          ${o.alerts.slice(0, 15).map((a) => `<tr><td>${esc(when(a.at))}</td><td>${esc(a.job)}</td><td>${esc(a.msg)}</td></tr>`).join('') || '<tr><td colspan="3" class="muted">none</td></tr>'}</table></div>
      </div>`;
  },

  async books() {
    const b = await api('books');
    const names = Object.keys(b.series);
    const main = b.series['master+brakes'] || b.series.master || [];
    const ai = b.aiPicks;
    const legs = Object.entries(b.orders || {}).flatMap(([k, d]) => (d.legs || []).map((l) => ({ ...l, decision: k })))
      .concat((ai?.pairs || []).flatMap((p) => (p.legs || []).map((l) => ({ ...l, decision: `AI ${p.ticker}` }))));
    return `<h2>Live book &amp; orders</h2><p class="lede">Simulator equity by weekly run; paper orders at Alpaca next to the simulator's fills.</p>
      <div class="card wide"><h3>Live equity by book (simulator, weekly runs)</h3>${main.length > 1
        ? lineChart(names.slice(0, 4).map((n) => ({ name: n, x: b.series[n].map((r) => r.date), y: b.series[n].map((r) => r.equity) })), { yFmt: (v) => v.toFixed(0) })
        : '<p class="empty">The first live allocator run is Mon 5 Oct 15:00; the curve starts after two weekly runs.</p>'}</div>
      <div class="card wide"><h3>All books</h3><table><tr><th>Book</th><th class="num">Start</th><th class="num">Now</th><th class="num">Return</th><th>Pending targets</th></tr>
        ${names.map((n) => { const s = b.series[n]; const r = s.at(-1).equity / s[0].equity - 1; const t = b.targets[n]?.pending;
          return `<tr><td>${esc(n)}</td><td class="num">${fmt(s[0].equity)}</td><td class="num">${fmt(s.at(-1).equity)}</td><td class="num">${fmt(100 * r, 2)}%</td><td class="mono">${esc(t ? Object.entries(t).map(([k, v]) => `${k} ${fmt(100 * v, 1)}%`).join(' · ') : '–')}</td></tr>`; }).join('') || '<tr><td colspan="5" class="muted">no allocator runs yet (first live run Monday)</td></tr>'}</table></div>
      <div class="card wide"><h3>AI-picks sleeve (untested, 10%)</h3>
        ${ai ? `<p class="muted">Equity ${fmt(ai.equity)} · threshold log-odds ${esc(ai.threshold)} · broker audit ${esc(ai.broker_audit_status)}</p>
        <table><tr><th>Ticker</th><th>ETF hedge</th><th class="num">Log-odds</th><th>Entry</th><th>Status</th><th>Audit</th></tr>
        ${(ai.pairs || []).map((p) => `<tr><td>${esc(p.ticker)}</td><td>${esc(p.etf)}</td><td class="num">${fmt(p.logodds, 2)}</td><td>${esc(p.entry_day)}</td><td>${pill(p.status, p.status === 'closed' ? '' : 'lime')}</td><td>${esc((p.audit_flags || []).join('; ') || 'ok')}</td></tr>`).join('')}</table>` : '<p class="empty">No AI picks yet.</p>'}</div>
      <div class="card wide"><h3>Paper broker orders</h3><table><tr><th>Decision</th><th>Symbol</th><th>Side</th><th class="num">Qty</th><th>Status</th><th class="num">Fill</th><th class="num">Simulator</th><th class="num">Gap</th></tr>
        ${legs.map((l) => `<tr><td>${esc(l.decision)}</td><td>${esc(l.symbol)}</td><td>${esc(l.side)}</td><td class="num">${esc(l.qty)}</td><td>${pill(l.status, l.status === 'filled' ? 'ok' : ['rejected', 'canceled', 'expired'].includes(l.status) ? 'bad' : '')}</td><td class="num">${fmt(l.filled_price, 2)}</td><td class="num">${fmt(l.sim_price, 2)}</td><td class="num">${l.gap == null ? '–' : `${fmt(l.gap * 1e4, 1)} bp`}</td></tr>`).join('') || '<tr><td colspan="8" class="muted">no orders yet</td></tr>'}</table></div>`;
  },

  async health() {
    const h = await api('health');
    const s = h.stage4 || {}, e = h.execution || {};
    return `<h2>Trading health</h2><p class="lede">The leverage gate (Stage 4) and paper execution quality. Report only.</p>
      <div class="grid">
        <div class="card"><div class="label">Forward Sharpe</div><div class="stat">${esc(s.sharpe ?? '–')}</div><span class="muted">lower 80% bound ${esc(s.lower80 ?? '–')}</span></div>
        <div class="card"><div class="label">Evidence</div><div class="stat">${esc(s.weeks ?? 0)} wk</div><span class="muted">${esc(s.months ?? 0)} months · book ${esc(s.book ?? '')}</span></div>
        <div class="card"><div class="label">Max drawdown</div><div class="stat">${esc(s.max_drawdown_pct ?? '–')}%</div><span class="muted">vol ${esc(s.vol_pct ?? '–')}%</span></div>
        <div class="card"><div class="label">Stage 4 row</div><div class="stat" style="font-size:15px">${esc(s.stage4_row || s.note || '–')}</div></div>
        <div class="card"><div class="label">Broker legs</div><div class="stat">${esc(e.legs ?? 0)}</div><span class="muted">${esc(JSON.stringify(e.by_status || {}))}</span></div>
        <div class="card"><div class="label">Fill gap vs simulator</div><div class="stat">${esc(e.mean_abs_gap_bp ?? '–')} bp</div><span class="muted">worst ${esc(e.worst_gap_bp ?? '–')} bp · ${esc(e.gaps_over_alert ?? 0)} over 0.5%</span></div>
        <div class="card"><div class="label">Problem orders</div><div class="stat">${esc(e.problem_legs ?? 0)}</div><span class="muted">rejected / canceled / expired</span></div>
        <div class="card"><div class="label">AI pair audit</div><div class="stat">${esc(e.ai_audit_flags ?? 0)}</div><span class="muted">${esc(e.ai_audit_status || '')}</span></div>
      </div>`;
  },

  async research() {
    const q = await api('research');
    if (!Array.isArray(q)) return `<h2>Research queue</h2><p class="err">${esc(q.error)}</p>`;
    return `<h2>Research queue</h2><p class="lede">Long research jobs that restart themselves; GPU jobs only run 18:00–05:25.</p>
      <div class="grid">${q.map((j) => { const [d, t] = j.progress; const pct = t ? Math.min(100, (100 * d) / t) : 0;
        const state = j.done ? pill('done', 'ok') : j.paused ? pill('paused', 'warn') : j.running ? pill('running', 'lime') : pill(j.kind === 'gate' ? 'waiting for check' : 'queued');
        return `<div class="card wide"><div class="row"><h3 style="margin:0">${esc(j.name)}</h3>${pill(j.kind)}${state}
          <span style="margin-left:auto" class="row">${j.kind === 'gate' || j.done ? '' : `<button class="btn ghost" data-q="${j.paused ? 'resume' : 'pause'}" data-name="${esc(j.name)}">${j.paused ? 'Resume' : 'Pause'}</button>`}</span></div>
          <p class="muted">${esc(j.note)}</p>${j.kind === 'gate' ? '' : `<div class="bar"><span style="width:${pct.toFixed(1)}%"></span></div><p class="tiny muted">${fmt(d)} / ${fmt(t)}</p>`}</div>`; }).join('')}</div>`;
  },

  async tests() {
    const t = await api('tests');
    const pass = t.filter((x) => String(x.result).startsWith('pass')).length;
    return `<h2>Tests</h2><p class="lede">Every pre-registered trial, run once. ${pass} passed of ${t.length}.</p>
      <div class="card wide"><table><tr><th>Date</th><th>Trial</th><th>Kind</th><th class="num">Sharpe / IC</th><th>Result</th></tr>
      ${t.map((x) => `<tr><td>${esc(x.date)}</td><td>${esc(x.trial)}</td><td>${esc(x.kind)}</td><td class="num">${esc(x.sharpe ?? x.ic ?? '')}</td><td>${pill(x.result, String(x.result).startsWith('pass') ? 'ok' : String(x.result).startsWith('fail') ? 'bad' : '')}</td></tr>`).join('')}</table></div>`;
  },

  async reviews() {
    const list = await api('reviews');
    if (!list.length) return '<h2>Weekly reviews</h2><p class="empty">The first review is written Saturday 07:00.</p>';
    const r = await fetch(`/api/review?name=${encodeURIComponent(list[0].name)}`).then((x) => x.json());
    return `<h2>Weekly reviews</h2><div class="row">${list.map((x, i) => `<button class="btn ${i ? 'ghost' : 'lime'}" data-review="${esc(x.name)}">${esc(x.date)}</button>`).join('')}</div>
      <div class="card wide"><pre class="md" id="review-text">${esc(r.text)}</pre></div>`;
  },
};


let metricsCache = null;
async function metrics(force = false) {
  if (!metricsCache || force) metricsCache = await api('metrics');
  return metricsCache;
}
const kpi = (label, value, sub = '') => `<div class="kpi"><div class="label">${esc(label)}</div><div class="stat">${value}</div><div class="sub">${sub}</div></div>`;
const statsRow = (s) => kpi('CAGR', pct(s.cagr)) + kpi('Sharpe', esc(s.sharpe)) + kpi('Volatility', pct(s.vol)) + kpi('Max drawdown', pct(s.max_dd)) + kpi('Period', `<span style="font-size:13px">${esc(s.start?.slice(0, 7))} → ${esc(s.end?.slice(0, 7))}</span>`);
const noMetrics = '<p class="empty">Chart data not built yet — press “Rebuild charts”.</p>';
let stratTab = 'core';

Object.assign(views, {
  async strategies() {
    const m = await metrics(); if (m.missing) return `<h2>Strategies</h2>${noMetrics}`;
    const t = m.tracks, tabs = [['core', 'Core book (long-term)'], ['event', 'AI events (short-term)'], ['longterm', 'Long-term picks & themes']];
    const head = `<h2>Strategies</h2><p class="lede">Backtests with the frozen code (2018→today). Live results build up in Live book.</p>
      <div class="tabs">${tabs.map(([k, n]) => `<button class="btn ${k === stratTab ? 'on' : 'ghost'}" data-strat="${k}">${esc(n)}</button>`).join('')}</div>`;
    if (stratTab === 'longterm') {
      return `${head}<div class="card wide"><h3>Long-term picks (monthly, 10 names) and theme track</h3>
        <p>The first cohorts are made on <b>Thu 1 Oct 15:30</b>. Each cohort is scored against SPY after it matures; the track is judged after 12 scored cohorts (no money until then).</p>
        <p class="muted">This page fills in automatically as cohorts are written (results/forward/longterm, results/forward/themes).</p></div>`;
    }
    const a = t[stratTab], spy = t.SPY, years = Object.keys(a.yearly);
    const bench = stratTab === 'core';
    const eqSeries = [{ name: stratTab === 'core' ? 'Core book' : 'AI event book', x: a.dates, y: a.equity }];
    if (bench) eqSeries.push({ name: 'SPY', x: spy.dates, y: spy.equity });
    return `${head}
      <div class="kpis">${statsRow(a.stats)}${bench ? kpi('SPY Sharpe', esc(spy.stats.sharpe), `CAGR ${pct(spy.stats.cagr)} · max DD ${pct(spy.stats.max_dd)}`) : ''}</div>
      <div class="card wide"><h3>Growth of 100</h3>${lineChart(eqSeries, { yFmt: (v) => v.toFixed(0) })}</div>
      <div class="split">
        <div class="card"><h3>Drawdown</h3>${lineChart(bench ? [{ name: eqSeries[0].name, x: a.dates, y: a.drawdown }, { name: 'SPY', x: spy.dates, y: spy.drawdown }] : [{ name: eqSeries[0].name, x: a.dates, y: a.drawdown }], { yFmt: (v) => pct(v, 0), zero: true, width: 560, height: 260 })}</div>
        <div class="card"><h3>Rolling 1-year Sharpe</h3>${lineChart(bench ? [{ name: eqSeries[0].name, x: a.dates, y: a.rolling_sharpe }, { name: 'SPY', x: spy.dates, y: spy.rolling_sharpe }] : [{ name: eqSeries[0].name, x: a.dates, y: a.rolling_sharpe }], { yFmt: (v) => v.toFixed(1), zero: true, width: 560, height: 260 })}</div>
      </div>
      <div class="card wide"><h3>Return by year</h3>${barChart(years, bench ? [{ name: eqSeries[0].name, y: years.map((y) => a.yearly[y]) }, { name: 'SPY', y: years.map((y) => spy.yearly[y] ?? null) }] : [{ name: eqSeries[0].name, y: years.map((y) => a.yearly[y]) }])}</div>`;
  },

  async lab() {
    const m = await metrics(); if (m.missing) return `<h2>Strategy lab</h2>${noMetrics}`;
    const tests = m.lab.tests;
    const order = ['day trading', 'calendar', 'pairs', 'crypto carry'];
    const rows = tests.slice().sort((a, b) => order.indexOf(a.group) - order.indexOf(b.group) || (b.sharpe ?? -9) - (a.sharpe ?? -9))
      .map((x) => ({ label: `${x.id} · ${x.name}`, group: x.group, v: x.sharpe, lo: x.ci?.[0], hi: x.ci?.[1], pass: x.pass }));
    const c = m.lab.curves, names = { D9: 'D9 opening-auction reversal', T1: 'T1 turn of the month', O1: 'O1 SPY overnight', E1: 'E1 macro announcements' };
    const curveCards = ['D9', 'T1', 'O1', 'E1'].filter((k) => c[k]).map((k) => `<div class="card"><h3>${esc(names[k])}</h3>
      <div class="kpis">${kpi('Sharpe (net)', esc(c[k].stats.sharpe))}${kpi('CAGR', pct(c[k].stats.cagr))}${kpi('Max DD', pct(c[k].stats.max_dd))}</div>
      ${lineChart([{ name: names[k], x: c[k].dates, y: c[k].equity }], { width: 560, height: 220, yFmt: (v) => v.toFixed(0) })}</div>`).join('');
    const passed = tests.filter((x) => x.pass).length;
    return `<h2>Strategy lab</h2><p class="lede">Every pre-registered strategy test, run once on its post-publication window at 1 bp a side. ${passed} of ${tests.length} passed (Sharpe ≥ 0.5 with the 95% interval above 0).</p>
      <div class="card wide"><h3>Net Sharpe with 95% interval</h3>${ciChart(rows, { refs: [{ v: 0.5, label: 'pass line 0.5' }] })}</div>
      <div class="split">${curveCards}</div>
      <p class="muted">D9 passed but trades the official open auction, which is not tradable in practice; its tradable version (D10) failed.</p>`;
  },

  async signals() {
    const [d, m] = await Promise.all([api('decisions'), metrics().catch(() => ({}))]);
    const vals = d.filter((r) => r.type === 'decision' && r.logodds != null).map((r) => Number(r.logodds));
    const thr = m?.live?.ai_threshold;
    return `<h2>AI signals</h2><p class="lede">Bonsai-27B scores every new earnings release (log-odds of beating its sector over a week). Picks above the threshold go to the paper AI sleeve.</p>
      <div class="kpis">${kpi('Live decisions', vals.length)}${kpi('Above threshold', vals.filter((v) => thr != null && v >= thr).length, `threshold ${esc(thr ?? '–')}`)}${kpi('Missed', d.filter((r) => r.type === 'missed').length)}</div>
      <div class="card wide"><h3>Score distribution</h3>${histogram(vals, { mark: thr, markLabel: 'pick threshold', bins: 16 })}</div>
      <div class="card wide"><table><tr><th>Accepted</th><th>Ticker</th><th>Sector</th><th class="num">Log-odds</th><th>Guidance</th><th>Result</th></tr>
      ${d.map((r) => `<tr><td>${esc(when(r.accepted_utc ? `${r.accepted_utc}Z` : r.as_of))}</td><td>${esc(r.ticker)}</td><td>${esc(r.sector)}</td><td class="num">${fmt(r.logodds, 2)}</td><td>${esc(r.guidance || '')}</td><td>${r.type === 'missed' ? pill('missed', 'bad') : pill('on time', 'ok')}</td></tr>`).join('') || '<tr><td colspan="6" class="muted">no live decisions yet</td></tr>'}</table></div>`;
  },
});

let current = 'overview';
let shown = 0;  // ignore a slower, older page load that finishes after a newer click
async function show(name, keepScroll = false) {
  current = name;
  if (location.hash.slice(1) !== name) history.replaceState(null, '', `#${name}`);
  const mine = ++shown;
  document.querySelectorAll('.nav-item').forEach((b) => b.classList.toggle('active', b.dataset.view === name));
  const item = document.querySelector(`[data-view="${name}"]`);
  $('#crumb').textContent = [...item.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('').trim();
  let html;
  try { html = await views[name](); } catch (e) { html = `<p class="err">${esc(e.message)}</p>`; }
  if (mine !== shown) return;
  $('#view').innerHTML = html;
  if (!keepScroll) $('.main-area').scrollTop = 0;
  attachHover($('#view'));
  $('#updated').textContent = `updated ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
}

async function sidebarState() {
  try {
    const o = await api('overview');
    $('#mode-text').innerHTML = `${esc(o.mode)} · ${o.halted ? '<span class="err">HALTED</span>' : 'trading'}`;
    $('#halt-btn').textContent = o.halted ? 'Resume trading' : 'Kill switch';
    $('#halt-btn').dataset.halted = o.halted ? '1' : '';
  } catch { $('#mode-text').textContent = 'airp not reachable'; }
}

function confirmDialog({ title, text, word, needReason }) {
  return new Promise((resolve) => {
    $('#dlg-title').textContent = title; $('#dlg-text').textContent = text; $('#dlg-error').textContent = '';
    $('#dlg-reason').value = ''; $('#dlg-confirm').value = ''; $('#dlg-reason').hidden = !needReason;
    $('#dlg-confirm').placeholder = `Type ${word} to confirm`;
    $('#dlg').returnValue = '';
    $('#dlg').showModal();
    $('#dlg').addEventListener('close', () => resolve($('#dlg').returnValue === 'ok' ? { reason: $('#dlg-reason').value, confirm: $('#dlg-confirm').value } : null), { once: true });
  });
}

$('#nav').addEventListener('click', (e) => { const b = e.target.closest('[data-view]'); if (b) show(b.dataset.view); });
$('#refresh').addEventListener('click', () => { metricsCache = null; show(current); sidebarState(); });
$('#rebuild').addEventListener('click', async () => {
  $('#rebuild').textContent = 'Rebuilding…'; $('#rebuild').disabled = true;
  try { await api('metrics', {}); metricsCache = null; await show(current); } catch (e) { alert(e.message); }
  $('#rebuild').textContent = 'Rebuild charts'; $('#rebuild').disabled = false;
});
$('#halt-btn').addEventListener('click', async () => {
  const halted = !!$('#halt-btn').dataset.halted;
  const r = await confirmDialog(halted
    ? { title: 'Resume trading', text: 'Turns the kill switch off. The next scheduled runs trade the paper book again.', word: 'RESUME' }
    : { title: 'Kill switch', text: 'Stops all paper trading: later runs only mark the books and cancel open paper orders. It stays on until you resume.', word: 'HALT', needReason: true });
  if (!r) return;
  try { await api(halted ? 'resume' : 'halt', r); } catch (e) { alert(e.message); }
  sidebarState(); show(current);
});
$('#view').addEventListener('click', async (e) => {
  const q = e.target.closest('[data-q]');
  if (q) { await api('research', { action: q.dataset.q, name: q.dataset.name }).catch((x) => alert(x.message)); show('research'); }
  const st = e.target.closest('[data-strat]');
  if (st) { stratTab = st.dataset.strat; show('strategies'); return; }
  const rv = e.target.closest('[data-review]');
  if (rv) {
    const r = await fetch(`/api/review?name=${encodeURIComponent(rv.dataset.review)}`).then((x) => x.json());
    $('#review-text').textContent = r.text;
    document.querySelectorAll('[data-review]').forEach((b) => b.classList.toggle('lime', b === rv));
  }
});

const start = location.hash.slice(1);
show(views[start] ? start : 'overview');
window.addEventListener('hashchange', () => { const v = location.hash.slice(1); if (views[v]) show(v); });
sidebarState();
setInterval(() => { sidebarState(); if (current === 'overview' || current === 'research') show(current, true); }, 60_000);
