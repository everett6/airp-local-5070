// airp desktop UI: plain modules, no framework. Every value is escaped before it reaches the page.
import { attachHover, barChart, ciChart, fanChart, heatmap, histogram, lineChart, pct, PALETTE, scatter, shareBars, stackArea } from './charts.js';
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (n, d = 0) => (n == null || Number.isNaN(n) ? '–' : Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d }));
const when = (s) => (s ? new Date(s).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '–');
const pill = (text, kind = '') => `<span class="pill ${kind}">${esc(text)}</span>`;

// App settings live in this window's own storage (nothing here touches airp's trading rules).
const DEFAULTS = { autoReload: true, reloadSec: 30, plainWords: true, startPage: 'overview', target: 40, stretch: 60 };
const settings = (() => { try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem('airp.settings') || '{}') }; } catch { return { ...DEFAULTS }; } })();
function saveSettings() {
  try { localStorage.setItem('airp.settings', JSON.stringify(settings)); } catch { /* storage unavailable: settings last for this session */ }
  document.body.classList.toggle('no-plain', !settings.plainWords);
}
let alpacaTab = 'main';  // which Alpaca account the Alpaca page shows
const sysHistory = [];  // recent machine readings for the System page charts (kept while the app is open)

async function api(path, body) {
  const r = await fetch(`/api/${path}`, body ? { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) } : {});
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

const JOBS = { events: 'Earnings run', check: 'Daily check', allocator: 'Weekly rebalance', review: 'Weekly review', learn: 'Learning loop' };
const AGENT_NAMES = { judge: 'Master judge (Bonsai)', net_read: 'Summary agent', ai_read: 'AI build-out view', bb_read: 'Bull vs bear debate', guidance: 'Company outlook (checked by code)' };
const plural = (n, word) => `<b>${n}</b> ${word}${n === 1 ? '' : 's'}`;
const vote = (v) => (v > 0 ? pill('▲ up', 'ok') : v < 0 ? pill('▼ down', 'bad') : pill('– no view'));

function todayCard(td) {
  if (!td) return '';
  const items = [];
  if (td.decisions.length) items.push(`${plural(td.decisions.length, 'new earnings report')} judged: ${td.decisions.map((d) => `${esc(d.ticker)} ${d.trade ? pill('practice trade', 'lime') : pill('no trade')}`).join(' ')}`);
  if (td.fills.length) items.push(`${plural(td.fills.length, 'practice order')} filled at the broker: ${td.fills.map((f) => `${esc(f.side)} ${esc(f.qty)} ${esc(f.symbol)} at ${fmt(f.price, 2)}`).join(' · ')}`);
  if (td.outcomes.length) items.push(`${plural(td.outcomes.length, 'earlier pick')} got a one-week result`);
  if (td.tests.length) items.push(`${plural(td.tests.length, 'strategy test')} finished: ${td.tests.map((t) => `${esc(t.trial)} ${pill(t.result, String(t.result).startsWith('pass') ? 'ok' : String(t.result).startsWith('fail') ? 'bad' : '')}`).join(' ')}`);
  if (td.longterm) items.push(`This month's <b>10 long-term picks</b> were made: ${esc(td.longterm.tickers.join(', '))} (see Strategies → Long-term picks & themes)`);
  if (td.themes) items.push(`Themes rated: AI-bubble risk ${pill(td.themes.bubble_risk ?? '–', { low: 'ok', elevated: 'warn', high: 'bad' }[td.themes.bubble_risk] || '')}, picked ${esc(td.themes.picks.join(', ') || 'none')}`);
  if (td.alerts.length) items.push(`${plural(td.alerts.length, 'alert')}, the latest: ${esc(td.alerts.at(-1).msg)}`);
  if (td.missed) items.push(`${plural(td.missed, 'report')} missed (not decided before the market opened)`);
  const bad = td.runs.filter((r) => r.rc !== 0 && !r.alert_only).length, warned = td.runs.filter((r) => r.alert_only).length;
  items.push(td.runs.length ? `${plural(td.runs.length, 'automatic run')}: ${[bad ? `${bad} failed` : '', warned ? `${warned} did its job but raised an alert` : '', !bad && !warned ? 'all clean' : ''].filter(Boolean).join(', ')}` : 'No automatic run in this period');
  return `<div class="card wide"><h3>What changed in the last ${td.hours} hours</h3><ul class="changes">${items.map((x) => `<li>${x}</li>`).join('')}</ul></div>`;
}

// The AI's reason with both sides it argued; a point whose quote is not on the card word for word is marked.
function whyCell(x) {
  const side = (name, xs) => (xs.length ? `<p class="tiny"><b>${name}</b> ${xs.map((q) => `${esc(q.point)}${q.verified ? '' : ' <span class="muted">(quote not found)</span>'}`).join(' · ')}</p>` : '');
  return `${esc(x.reason || '–')}${x.bull.length || x.bear.length ? `<details><summary>Both sides it argued</summary>${side('For:', x.bull)}${side('Against:', x.bear)}</details>` : ''}`;
}

function longtermTab(d) {
  const p = d.picks, t = d.themes, signed = (v) => (v == null ? '–' : `${v > 0 ? '+' : ''}${pct(v)}`);
  const judged = (n) => `<p class="muted">Scored so far: <b>${n}</b>. The track is judged only after 12 scored months, and it gets no money before then.</p>`;
  const picks = !p
    ? '<div class="card wide"><h3>Long-term picks</h3><p class="empty">No picks yet. The first 10 are made in the afternoon run on the first weekday of the month.</p></div>'
    : `<div class="card wide"><h3>Long-term picks · ${esc(p.month)}</h3>
      <p>Made on <b>${esc(p.made_on)}</b> from <b>${fmt(p.candidates)}</b> company cards (each company's latest earnings report). The AI rated every card from 1 to 5; these are the 10 with the highest scores. They are held for about 3 months and compared with simply holding the market. Practice only.</p>
      <p class="muted">How it rated all the cards: ${[5, 4, 3, 2, 1].map((k) => `${pill(`${k}: ${p.rating_counts[k] ?? 0}`)}`).join(' ')}</p>
      <table><tr><th>Company</th><th class="num">Rating</th><th class="num">Score${help('Long-term score')}</th><th class="num">Last 12 months vs market</th><th>Why</th></tr>
      ${p.names.map((n) => `<tr><td><b>${esc(n.ticker)}</b></td><td class="num">${esc(n.rating ?? '–')} / 5</td><td class="num">${fmt(n.score, 2)}</td><td class="num">${signed(n.r12)}</td><td>${whyCell(n)}</td></tr>`).join('')}</table></div>`;
  const pickResults = !d.pickCohorts ? '' : `<div class="card wide"><h3>How earlier picks did</h3>${judged(d.pickResults.length)}
      ${d.pickResults.length ? `<table><tr><th>Month</th><th>Bought</th><th>Sold</th><th class="num">The 10 picks</th><th class="num">Market</th><th class="num">Difference after costs</th></tr>
      ${d.pickResults.map((r) => `<tr><td>${esc(r.month)}</td><td>${esc(r.entry)}</td><td>${esc(r.exit)}</td><td class="num">${signed(r.basket)}</td><td class="num">${signed(r.spy)}</td><td class="num">${signed(r.excess_net)}</td></tr>`).join('')}</table>` : '<p class="empty">The first result arrives about 3 months after the first picks.</p>'}</div>`;
  const kind = { low: 'ok', elevated: 'warn', high: 'bad' };
  const held = { medium: '6 months', long: '12 months' };
  const themes = !t
    ? '<div class="card wide"><h3>Themes</h3><p class="empty">No theme ratings yet. They are made in the same run as the long-term picks.</p></div>'
    : `<div class="card wide"><h3>Themes · ${esc(t.month)}</h3>
      <p>AI-bubble risk: ${pill(t.bubble_risk ?? '–', kind[t.bubble_risk] || '')} ${esc(t.risk.reason || '')}</p>
      ${t.register.length ? `<details><summary>The numbers this reading is based on</summary><ul class="changes">${t.register.map((l) => `<li>${esc(l)}</li>`).join('')}</ul></details>` : ''}
      <p class="muted">The AI rates each theme from 1 to 5. Up to 2 per holding period rated 4 or 5 are picked; AI-linked themes are left out while the bubble risk reads high. The yardstick is the 2 themes with the best past-year price trend: the AI's picks have to beat it.</p>
      <table><tr><th>Theme</th><th>Held for</th><th class="num">Rating</th><th class="num">Score${help('Long-term score')}</th><th class="num">Trend vs market${help('Past-year trend')}</th><th></th><th style="width:44%">Why</th></tr>
      ${t.rows.map((r) => `<tr><td><b>${esc(r.label)}</b>${r.ai_linked ? ' <span class="muted tiny">AI-linked</span>' : ''}</td><td style="white-space:nowrap">${esc(held[r.horizon] || r.horizon)}</td><td class="num">${esc(r.rating ?? '–')} / 5</td><td class="num">${fmt(r.score, 2)}</td><td class="num">${signed(r.mom)}</td>
        <td>${r.picked ? pill('picked', 'lime') : ''} ${r.baseline ? pill('yardstick') : ''}</td><td>${whyCell(r)}</td></tr>`).join('')}</table></div>`;
  const themeResults = !d.themeCohorts ? '' : `<div class="card wide"><h3>How earlier theme picks did</h3>${judged(d.themeResults.length)}
      ${d.themeResults.length ? `<table><tr><th>Month</th><th>Held for</th><th>Picked</th><th class="num">Picks vs market, after costs</th><th class="num">Yardstick vs market</th></tr>
      ${d.themeResults.map((r) => `<tr><td>${esc(r.month)}</td><td>${esc(held[r.horizon] || r.horizon)}</td><td>${esc(r.picks.join(', ') || 'none (stayed in the market)')}</td><td class="num">${signed(r.excess_net)}</td><td class="num">${signed(r.baseline_excess_net)}</td></tr>`).join('')}</table>` : '<p class="empty">The first result arrives 6 months after the first theme picks.</p>'}</div>`;
  return `${plain('Once a month the AI reads every large company\'s latest earnings report and picks <b>10 stocks</b> for the next few months, and rates a fixed list of <b>big themes</b> (chips, biotech, nuclear and so on) together with the risk of an AI bubble. Both are only watched and scored. No money follows them until a year of results says they work.')}${picks}${pickResults}${themes}${themeResults}`;
}

// What waits for the user: things to do, then questions only they can answer (the list is kept by Claude).
function openCard(xs) {
  if (!xs?.length) return '';
  const row = (x) => `<li><b>${esc(x.title)}</b>${x.kind === 'do' ? ` ${pill('to do', 'lime')}` : ''}<br><span class="muted">${esc(x.detail)}</span></li>`;
  const sorted = [...xs].sort((a, b) => (a.kind === 'do' ? 0 : 1) - (b.kind === 'do' ? 0 : 1));
  return `<div class="card wide"><h3>Waiting for you (${xs.length})</h3>
    <p class="muted">Nothing here stops the system. Tell Claude yes or no on any of them; until then things stay as they are.</p>
    <details${xs.length <= 4 ? ' open' : ''}><summary>Show the list</summary><ul class="changes">${sorted.map(row).join('')}</ul></details></div>`;
}

function aggressiveCard(a) {
  if (!a) return '';
  const L = a.limits || {};
  const head = '<h3>Aggressive book (2.5×) · your decision of 30 Sep</h3>';
  const rules = `It holds 2.5 times the frozen book's positions on borrowed practice money (5% a year interest). It warns at a ${pct(L.alert_drawdown, 0)} fall from its peak and stops buying at ${pct(L.max_drawdown, 0)}.`;
  if (!a.started) return `<div class="card wide">${head}<p class="empty">Not started yet: it begins at the next weekly rebalance${a.nextAllocator ? ` (${esc(when(Number(a.nextAllocator) / 1000))})` : ''}.</p><p class="muted">${rules} In the backtest this size made about 35% a year with falls of up to 57%.</p></div>`;
  const x = a.last, dd = a.drawdown ?? 0, room = L.max_drawdown ? Math.min(100, (100 * dd) / L.max_drawdown) : 0;
  const s = a.series;
  return `<div class="card wide">${head}${x.wiped_out ? '<div class="banner bad"><b>Wiped out.</b> The loan grew larger than the holdings; this book is closed for good.</div>' : ''}
    ${a.lastError ? `<div class="banner warn"><b>Its last weekly run failed</b> (${esc(a.lastError)}). The other books were not affected; the numbers below are from its last good run.</div>` : ''}
    ${x.reducing ? '<div class="banner warn"><b>Past its limit: it may only sell.</b> It buys again only after you press Resume trading.</div>' : ''}
    <p class="muted">${rules} It is a what-if beside the frozen book, not a tested strategy.</p>
    <div class="kpis">${kpi('Value now', fmt(x.equity))}${kpi('Size', x.gross == null ? '–' : `${fmt(x.gross, 2)}×`, 'holdings ÷ own money')}${kpi('Borrowed', fmt(x.borrowed))}${kpi('Interest paid so far', fmt(x.interest_paid))}
      ${kpi('Fall from its peak', pct(dd), `warns at ${pct(L.alert_drawdown, 0)} · stops buying at ${pct(L.max_drawdown, 0)}`)}${kpi('Size at the broker', a.broker?.gross == null ? '–' : `${fmt(a.broker.gross, 2)}×`, 'Alpaca lends 2× on stocks, nothing on crypto')}</div>
    <div class="bar ${dd >= (L.alert_drawdown ?? 1) ? 'hot' : ''}"><span style="width:${room.toFixed(1)}%"></span></div><p class="tiny muted">How much of the room before it stops buying is used up</p>
    ${s.aggressive.length > 1 ? lineChart([{ name: 'Aggressive 2.5×', x: s.aggressive.map((r) => r.date), y: s.aggressive.map((r) => r.equity) }, { name: 'Frozen book', x: s.frozen.map((r) => r.date), y: s.frozen.map((r) => r.equity) }], { height: 200, yFmt: (v) => v.toFixed(0) }) : '<p class="empty">The chart starts after two weekly runs.</p>'}</div>`;
}

function monthEndCard(w) {
  if (!w) return '';
  const head = '<h3>Being watched on new data · month-end Treasuries (no money)</h3>';
  const what = 'Long Treasury bonds have tended to rise in the last 3 trading days of a month, when big funds must buy. In the test on past data (M1 below) the effect was there, but not surely enough to pass. So the same rule is now recorded on months nobody has seen, starting October 2026, and judged once after 24 months. Nothing is bought.';
  if (w.verdict) return `<div class="card wide">${head}<p class="muted">${what}</p><p>${w.verdict.pass ? pill('passed', 'ok') : pill('failed', 'bad')} after ${fmt(w.months)} months: score ${fmt(w.verdict.sharpe, 2)}; month-end days beat other days by ${fmt(w.verdict.diff_bp, 1)} bp a day (cautious estimate ${fmt(w.verdict.diff_lo80_bp, 1)}).</p></div>`;
  const used = Math.min(100, 100 * w.months / w.target);
  return `<div class="card wide">${head}<p class="muted">${what}</p>
    <div class="kpis">${kpi('Months recorded', `${fmt(w.months)} of ${fmt(w.target)}`, 'verdict after the last one')}${kpi('Average month-end gain', w.mean_net == null ? '–' : pct(w.mean_net, 2), 'TLT above T-bills, after costs')}${kpi('Months it gained', w.hit_rate == null ? '–' : pct(w.hit_rate, 0))}</div>
    <div class="bar"><span style="width:${used.toFixed(1)}%"></span></div>
    ${w.rows.length ? `<table><tr><th>Month</th><th>Days held</th><th class="num">Gain over those days</th><th class="num">An average other day that month</th></tr>
      ${w.rows.map((r) => `<tr><td>${esc(r.month)}</td><td>${esc(r.days.map((d) => String(d).slice(5)).join(', '))}</td><td class="num">${pct(r.net, 2)}</td><td class="num">${r.other_mean == null ? '–' : pct(r.other_mean, 2)}</td></tr>`).join('')}</table>`
      : '<p class="empty">The first month (October 2026) is recorded in early November.</p>'}</div>`;
}

function consensusCard(c) {
  if (!c) return '';
  const head = '<h3>Master algorithm · combines every agent (in testing, no money)</h3>';
  const what = 'Each agent votes up, down or no view on every report. One score is the plain average of the votes. The other gives more say to agents that have been right more often so far, using only results that were already known at the time.';
  if (!c.recorded) return `<div class="card wide">${head}<p class="muted">${what}</p><p class="empty">Starts recording with the next earnings run.</p></div>`;
  return `<div class="card wide">${head}<p class="muted">${what} ${fmt(c.recorded)} reports recorded, ${fmt(c.scored)} with a result. It is judged at 150 results and 3 months, like the other tests.</p>
    <div class="split"><div><table><tr><th>Agent</th><th class="num">Calls with a known result</th><th class="num">Right</th><th class="num">Say (weight)</th></tr>
      ${c.agents.map((a) => `<tr><td>${esc(AGENT_NAMES[a.agent] || a.agent)}</td><td class="num">${fmt(a.calls)}</td><td class="num">${a.calls ? pct(a.hits / a.calls, 0) : '–'}</td><td class="num">${a.weight == null ? '–' : fmt(a.weight, 2)}</td></tr>`).join('')}</table>
      <p class="tiny muted">Every agent starts with the same small say (0.10). An agent only gains say once its own record beats a coin flip.</p></div>
    <div><table><tr><th>Report</th>${Object.keys(AGENT_NAMES).map((k) => `<th>${esc(AGENT_NAMES[k].split(' (')[0])}</th>`).join('')}<th class="num">Plain average</th><th class="num">Record-weighted</th><th>Result</th></tr>
      ${c.releases.slice(0, 12).map((r) => `<tr><td>${esc(r.ticker)}${r.on_time ? '' : ' <span class="muted">(recorded late, not scored)</span>'}</td>${Object.keys(AGENT_NAMES).map((k) => `<td>${vote(r.votes?.[k] ?? 0)}</td>`).join('')}<td class="num">${fmt(r.eq, 2)}</td><td class="num">${fmt(r.rw, 2)}</td><td>${r.result == null ? pill('due in a week', 'warn') : pill(pct(r.result), r.result > 0 ? 'ok' : 'bad')}</td></tr>`).join('')}</table></div></div></div>`;
}

// Live progress bar: frac in [0,1] or null for a running bar of unknown length
const progressBar = (frac, label, running = true) => `<div class="progress${running ? ' live' : ''}${frac == null ? ' indeterminate' : ''}"><div style="width:${frac == null ? 35 : Math.max(0, Math.min(100, 100 * frac)).toFixed(1)}%"></div></div>${label ? `<p class="tiny muted">${label}</p>` : ''}`;
const nyMinutes = (d = new Date()) => { const [h, m] = new Intl.DateTimeFormat('en-GB', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false }).format(d).split(':').map(Number); return h * 60 + m; };
const hmMin = (x) => (x ? Number(x.slice(0, 2)) * 60 + Number(x.slice(3, 5)) : null);
const views = {
  async horizons() {
    const d = await api('horizon-shadows');
    return `<h2>Horizon shadows</h2><p class="lede">Separate virtual vintages for day, 21-session medium and 63-session long theses. Each fixes its weights before any entry prices are observed.</p>
      <button class="btn ghost" data-operation="horizon_review">Review horizon shadows</button>
      <p>New Live research test cohorts create prospective plans. Existing cohorts are excluded. Entries use the first session open after the decision. Day exits at that session close; medium/long exit 21/63 sessions later. Caps: day 10%, medium 30%, long 50%, each company 10% total, cash at least 10%, no borrowing.</p>
      <p class="muted">Free IEX daily prices are proxies, not broker or consolidated auction fills. Costs are assumed at 10 bp per side, stressed at 20 bp. Vintages are not pooled as one account. No automatic qualification or promotion.</p>
      ${d.error ? `<p class="err">${esc(d.error)}</p>` : !(d.vintages || []).length ? '<p class="empty">No prospective horizon outcomes yet. Run a new Live research test, then review horizon shadows.</p>' : `<p>Observed ${esc(when(d.at))}</p>${d.vintages.map((v) => `<div class="card wide"><h3>${esc(v.id)}</h3><p>Decision ${esc(when(v.plan.decided_at))}</p><table><tr><th>Horizon</th><th>Status</th><th>AI net</th><th>No-AI net</th><th>Difference</th><th>AI at 20 bp</th><th>Turnover AI / no-AI</th><th>Missing prices</th></tr>${Object.entries(v.outcomes.horizons).map(([h,x]) => `<tr><td>${esc(h)}</td><td>${esc(x.status)}</td><td>${pct(x.arms.ai?.net_return)}</td><td>${pct(x.arms.no_ai?.net_return)}</td><td>${pct(x.incremental_net_return)}</td><td>${pct(x.arms.ai?.stress_net_return)}</td><td>${fmt(x.arms.ai?.turnover, 3)} / ${fmt(x.arms.no_ai?.turnover, 3)}</td><td>${esc(x.missing.join(', ') || '—')}</td></tr>`).join('')}</table><details><summary>Locked plan and outcome evidence</summary><pre class="operation-log">${esc(JSON.stringify(v, null, 2))}</pre></details></div>`).join('')}`}`;
  },
  async planning() {
    const d = await api('trading-plan');
    if (d.error) throw new Error(d.error);
    const horizons = ['day', 'short', 'medium', 'long'];
    const latency = d.evidence.filter((r) => r.research_s != null || r.judge_s != null);
    return `<h2>AI planning &amp; case simulation</h2><p class="lede">Evidence-linked budgets for day (1 session), short (5), medium (21) and long term (63). Cases show hypothetical gains and losses, not expected returns or trading instructions.</p>
      <div class="banner ${d.policy_error ? 'bad' : ''}">${esc(d.policy_error || (d.policy_active ? `New AI stock entries restricted to ${d.allowed_symbols.length} selected stocks. Existing exits and sector ETF hedges remain allowed.` : 'Stock entry restriction activates when Auto-trade 100 technology stocks starts.'))}</div>
      <p>${d.evidence.length} recent company attempts in the selected universe. Evidence older than ${d.freshness_hours} hours is excluded. Missing or failed ratings hold cash. Simulations use a $10,000 virtual budget, no borrowing, and a 10% cap per company across horizons.</p>
      ${(d.plans || []).map((p) => `<div class="card wide"><h3>${esc(p.profile.replaceAll('_', ' '))} budget</h3>
        ${shareBars([...horizons.map((h) => ({ label: h, v: p.allocation[h], note: `cap ${pct(p.caps[h])}` })), { label: 'cash', v: p.cash }])}
        <h4>Hypothetical net gains / losses by case</h4>${barChart(p.cases.map((c) => c.name.replaceAll('_', ' ')), horizons.map((h) => ({ name: h, y: p.cases.map((c) => c.contribution[h]) })), { height: 220 })}
        <table><tr><th>Case</th><th>Combined budget contribution</th><th>Hypothetical P&amp;L</th><th>Costs per side</th></tr>${p.cases.map((c) => `<tr><td>${esc(c.name)}</td><td>${pct(c.net_return)}</td><td>$${fmt(c.pnl, 2)}</td><td>${fmt(c.cost_bps_per_side)} bp</td></tr>`).join('')}</table>
        <p class="muted">${esc(p.explanation)}</p><details><summary>Exact weights, assumed shocks and source-linked plan</summary><pre class="operation-log">${esc(JSON.stringify(p, null, 2))}</pre></details></div>`).join('')}
      <div class="card wide"><h3>Research and decision latency</h3>${latency.length ? barChart(latency.map((r) => r.ticker), [{ name: 'Jan research', y: latency.map((r) => r.research_s ?? null) }, { name: 'Bonsai judgment', y: latency.map((r) => r.judge_s ?? null) }], { yFmt: (n) => fmt(n) + 's' }) : '<p class="empty">Timings appear after company attempts finish.</p>'}</div>
      <div class="card wide"><h3>Selected stock universe</h3><table><tr><th>Stock</th><th>Company</th><th>Research state</th><th>Day / short / medium / long ratings</th></tr>${d.universe.map((c) => { const r = d.evidence.find((r) => r.ticker === c.ticker); return `<tr><td>${esc(c.ticker)}</td><td>${esc(c.name)}</td><td>${esc(r?.status || c.state)}</td><td>${horizons.map((h) => esc(r?.ratings?.[h] ?? 'PASS / unmeasured')).join(' / ')}</td></tr>`; }).join('') || '<tr><td colspan="4">Start the 100-stock workflow to freeze the universe.</td></tr>'}</table></div>`;
  },
  async alpaca() {
    const auto = alpacaTab === 'auto';
    const d = await api(auto ? 'alpaca-account-auto' : 'alpaca-account'), a = d.account || {}, e = auto ? null : d.exposure;
    const tabs = `<div class="row tabs">${[['main', 'Main account (core book + AI picks)'], ['auto', 'Autopilot account']].map(([k, n]) => `<button class="btn ${alpacaTab === k ? 'lime' : 'ghost'}" data-acct="${k}">${esc(n)}</button>`).join('')}</div>`;
    const money = (v) => v == null ? '—' : `${esc(a.currency || 'USD')} ${fmt(Number(v), 2)}`;
    const rows = (fields, format) => fields.map(([key, label]) => `<tr><td>${esc(label)}</td><td>${format(a[key])}</td></tr>`).join('');
    const flag = (v) => v == null ? 'Not reported' : v === true ? 'Yes' : v === false ? 'No' : esc(v);
    return `<h2>Alpaca accounts</h2><p class="lede">Your two Alpaca paper accounts: the main one and the autopilot's separate one. Use Refresh to read the latest details.</p>${tabs}
      ${d.error ? `<div class="banner warn">${esc(d.error)}</div>` : `<p>Account ${esc(d.account_number || 'number not reported')} · ${pill(a.status || 'Not reported')} · fetched ${esc(when(d.at))}</p>
      <div class="kpis">${kpi('Equity', money(a.equity))}${kpi('Cash', money(a.cash))}${kpi('Buying power', money(a.buying_power))}${kpi('Broker multiplier', a.multiplier == null ? '—' : esc(a.multiplier) + '×')}</div>
      <div class="split"><div class="card"><h3>Balances &amp; margin</h3><table>${rows([
        ['portfolio_value','Portfolio value'],['last_equity','Previous equity'],['regt_buying_power','Regulation T buying power'],
        ['daytrading_buying_power','Day trading buying power'],['non_marginable_buying_power','Non-marginable buying power'],
        ['initial_margin','Initial margin'],['maintenance_margin','Maintenance margin'],['last_maintenance_margin','Previous maintenance margin'],
        ['long_market_value','Long market value'],['short_market_value','Short market value'],['accrued_fees','Accrued fees'],
        ['pending_transfer_in','Pending transfer in'],['pending_transfer_out','Pending transfer out'],['options_buying_power','Options buying power']], money)}</table></div>
      <div class="card"><h3>Account permissions &amp; restrictions</h3><table>${rows([
        ['pattern_day_trader','Pattern day trader flag'],['daytrade_count','Day trade count'],['shorting_enabled','Shorting enabled'],
        ['trading_blocked','Trading blocked'],['transfers_blocked','Transfers blocked'],['account_blocked','Account blocked'],
        ['trade_suspended_by_user','Trading suspended by user'],['options_approved_level','Options approved level'],
        ['options_trading_level','Options trading level']], flag)}<tr><td>Created</td><td>${esc(when(a.created_at))}</td></tr></table></div></div>
      <p class="muted">The broker multiplier is an account setting; actual leverage depends on positions. Missing fields mean Alpaca did not report them.</p>`}
      <div class="split"><div class="card"><h3>Alpaca account equity — last month</h3>${d.history?.timestamp?.length ? lineChart([{ name: 'Broker equity', x: d.history.timestamp.map((t) => new Date(t * 1000).toISOString().slice(0, 10)), y: (d.history.equity || []).map((v) => v == null ? null : Number(v)) }], { height: 220, yFmt: (v) => '$' + fmt(v), xTicks: (t) => t.slice(5, 10) }) : `<p class="empty">${esc(d.history_error || 'No account history yet.')}</p>`}</div>
      <div class="card"><h3>Broker-reported P&amp;L</h3>${d.history?.timestamp?.length ? lineChart([{ name: 'P&L', x: d.history.timestamp.map((t) => new Date(t * 1000).toISOString().slice(0, 10)), y: (d.history.profit_loss || []).map((v) => v == null ? null : Number(v)) }], { height: 220, yFmt: (v) => '$' + fmt(v), zero: true, xTicks: (t) => t.slice(5, 10) }) : '<p class="empty">No broker P&amp;L history yet.</p>'}<p class="muted">Account-level P&amp;L includes all books and account cashflows; it does not isolate AI contribution.</p></div></div>
      <div class="card wide"><h3>Actual paper positions</h3>${d.positions_error ? `<p class="err">${esc(d.positions_error)}</p>` : ''}${d.positions?.length ? barChart(d.positions.map((p) => p.symbol), [{ name: 'Market value', y: d.positions.map((p) => Number(p.market_value)) }], { yFmt: (v) => '$' + fmt(v), height: 220 }) : ''}<table><tr><th>Symbol</th><th>Side</th><th>Shares</th><th>Market value</th><th>Unrealized P&amp;L</th></tr>${(d.positions || []).map((p) => `<tr><td>${esc(p.symbol)}</td><td>${esc(p.side)}</td><td>${esc(p.qty)}</td><td>${money(p.market_value)}</td><td>${money(p.unrealized_pl)} (${pct(Number(p.unrealized_plpc))})</td></tr>`).join('') || '<tr><td colspan="5">No positions reported.</td></tr>'}</table></div>
      <div class="card wide"><h3>Recent paper orders — latest 100</h3>${d.orders_error ? `<p class="err">${esc(d.orders_error)}</p>` : ''}<table><tr><th>Submitted</th><th>Symbol</th><th>Side</th><th>Quantity / filled</th><th>Status</th><th>Fill price</th></tr>${(d.orders || []).map((o) => `<tr><td>${esc(when(o.submitted_at))}</td><td>${esc(o.symbol)}</td><td>${esc(o.side)}</td><td>${esc(o.qty)} / ${esc(o.filled_qty)}</td><td>${esc(o.status)}</td><td>${money(o.filled_avg_price)}</td></tr>`).join('') || '<tr><td colspan="6">No orders reported.</td></tr>'}</table></div>
      ${auto ? '' : `<div class="card wide"><h3>Latest reconciled exposure</h3>${e ? `<p>Snapshot ${esc(when(e.at))} · gross ${money(e.gross)} · net ${money(e.net)} · actual gross leverage ${fmt(e.leverage, 3)}× · open orders ${esc(e.open_orders)}</p><p class="muted">This saved snapshot updates during account reconciliation and may be older than the balances above.</p>${(e.alerts || []).map((x) => `<p class="err">${esc(x)}</p>`).join('')}` : '<p class="empty">No exposure snapshot yet. Run Sync paper orders in Run Center to reconcile the account.</p>'}</div>`}`;
  },
  async institutional() {
    const [d, r] = await Promise.all([api('institutional'), api('operations')]);
    if (d.error) throw new Error(d.error);
    const b = d.benchmark, x = d.execution, f = d.attribution, risk = d.risk.policy;
    const money = (value) => '$' + fmt(value, 2);
    const run = (id) => { const a = r.actions.find((v) => v.id === id); return `<button class="btn ghost" data-operation="${esc(id)}" ${a?.disabled ? 'disabled' : ''}>${esc(a?.label || id)}</button>`; };
    const reviewForm = (action, digest) => `<form data-engineering-form data-action="${action}" data-digest="${esc(digest)}">
      ${action === 'costs' ? `<p>Supply total broker costs in dollars for the reported execution period, including legacy fills. New fills make this cost review stale.</p>${['fees', 'borrow', 'financing'].map((c) => `<label>${c} <input name="${c}" type="number" min="0" step="any" required></label>`).join(' ')}` : `<p>Review candidate ${esc(d.release.candidate?.id)} and its recorded test log before attesting.</p>${['source', 'tests', 'risk', 'recovery'].map((c) => `<label><input type="checkbox" name="checks" value="${c}" required> Reviewed ${c}</label>`).join(' · ')}`}
      <label>Your name <input name="reviewer" required maxlength="100"></label> <label>Evidence and review notes <input name="note" required maxlength="2000"></label>
      <label>Type REVIEWED <input name="confirm" required pattern="REVIEWED"></label><button class="btn ghost">Record my review</button></form>`;
    return `<h2>Engineering checks</h2><p class="lede">Evidence for reliable paper trading, with missing verification shown explicitly.</p>
      <div class="grid">
      <div class="card"><h3>Source accuracy</h3><p>${b.human_attested} / ${b.cases} cases reviewed by a person. ${b.remaining} remain.</p><p>Provisional reader result: ${b.provisional_score.right} / ${b.provisional_score.fields} fields right. ${esc(b.label_origin)}</p><details><summary>Reader coverage and results</summary>${(b.readers || []).map((r) => `<p>${esc(r.source)}: ${esc(r.provisional.right)} / ${esc(r.provisional.fields)} fields, ${esc(r.provisional.scored_cases)} / ${esc(r.provisional.cases)} cases read. Human score: ${r.human_verified ? esc(r.human_verified.right) + ' / ' + esc(r.human_verified.fields) : 'pending'}.</p>`).join('')}</details><button class="btn lime" data-go="benchmark">Review source labels</button></div>
      <div class="card"><h3>Account order limits</h3><p>Gross ${risk.max_gross}× equity · single asset ${risk.max_asset}× · crypto ${fmt(risk.max_crypto * 100)}% · daily loss ${fmt(risk.daily_loss * 100)}%.</p><p>Every new paper submission checks both books and outstanding orders. Missing account data or unusable quotes block the submission. Valid closes can reduce an existing limit breach.</p></div>
      <div class="card"><h3>Execution measurement</h3><p>${x.fills_measured} fills measured · ${x.orders_submitted} submissions recorded · arrival slippage ${fmt(x.weighted_slippage_bp, 2)} bp.</p><p>Arrival shortfall plus fees, borrow and financing: ${x.period_cost_dollars ? money(x.period_cost_dollars.total) : 'awaiting broker cost inputs'}.</p><p>Cost coverage: ${esc(x.cost_status || 'incomplete')} · ${esc(x.unmeasured_fill_snapshots)} fill(s) lack arrival measurements.</p><details><summary>Unmeasured fills and blocked orders</summary><pre class="operation-log">${esc(JSON.stringify({unmeasured: x.unmeasured_fills, blocked: x.blocked}, null, 2))}</pre></details><p class="muted">${esc(x.note)}</p>${x.stress.map((s) => `<p>Extra ${s.extra_cost_bp_per_side} bp / side: ${money(s.extra_dollars_on_measured_turnover)} on measured turnover.</p>`).join('')}</div>
      <div class="card"><h3>Factor attribution</h3><p>${esc(f.status)} · ${f.observations ?? 0} matched observations / ${f.required ?? 120} required.</p><p>Factors: ${esc((f.factors || []).join(', ') || 'none yet')}. Missing: ${esc((f.missing_factors || f.missing || []).join(', ') || 'none')}.</p>${f.loadings ? `<pre>${esc(JSON.stringify({ loadings: f.loadings, intercept_95: f.intercept_95 }, null, 2))}</pre>` : ''}<p>Uses matched dates and uncertainty adjusted for serial correlation. More forward observations are needed to judge the AI’s incremental value.</p></div>
      <div class="card"><h3>Release and review</h3><p>${d.release.snapshots.length} recent snapshots. ${esc(d.release.independent_review)}.</p>${run('release_check')} ${run('rollback_probe')}<p>Source hashes bind each candidate to its checks. Candidates do not automatically activate trading changes.</p></div>
      <div class="card"><h3>Recovery and run health</h3><p>Last restore: ${esc(d.recovery.last_restore?.status || 'no rehearsal yet')}.</p>${run('backup')} ${run('restore_probe')} ${run('recovery_tests')}<p>${esc(d.recovery.off_machine_copy)}</p><p>${d.steps.native} structured stage records · ${d.steps.legacy} legacy stage records. Legacy success codes remain unverified stage outcomes.</p></div></div>
      <div class="card wide"><h3>Engineering records</h3>${run('institutional_report')}<p>${d.evidence_bundles} reproducible decision bundles. The Records page shows the full opportunity funnel, contribution comparison and decision latency.</p>
      <details><summary>Recent blocked orders</summary><pre class="operation-log">${esc(JSON.stringify(x.blocked, null, 2))}</pre></details>
      <details><summary>Snapshot identifiers and digests</summary><pre class="operation-log">${esc(JSON.stringify(d.release.snapshots, null, 2))}</pre></details>
      ${x.ledger_sha256 ? `<details><summary>Supply broker cost evidence</summary>${reviewForm('costs', x.cost_scope_sha256 || x.ledger_sha256)}</details>` : '<p>Cost review becomes available after the first audited submission.</p>'}
      ${d.release.candidate ? `<details><summary>Record release review</summary>${reviewForm('release', d.release.candidate.archive_sha256)}</details>` : '<p>Preserve a checked release to make its review available here.</p>'}</div>`;
  },
  async benchmark() {
    const cases = await api('benchmark');
    if (!Array.isArray(cases)) throw new Error(cases.error || 'Benchmark unavailable');
    return `<h2>Source review</h2><p class="lede">Read the filing and verify each labelled period, unit, accounting basis and absence. Original AI labels remain provisional until you record a review.</p>
      ${cases.map((m) => `<div class="card wide"><h3>${esc(m.case.ticker)} · ${esc(m.case.accession)}</h3><p>${m.approved ? `Reviewed by ${esc(m.review.reviewer)}` : 'Needs human review'} · ${esc((m.case.categories || []).join(', '))}</p>
      <details><summary>Source text</summary><pre class="operation-log">${esc(m.source || 'Source is not cached. Review cannot be approved.')}</pre></details>
      <details><summary>Find labelled numbers in the source (candidate matches, not verification)</summary>${(m.label_excerpts || []).map((x) => `<h4>${esc(x.field)}: ${esc(x.label)} · ${esc(x.occurrences)} occurrence(s)</h4><p>${esc(x.note)}</p>${x.snippets.map((s) => `<pre class="operation-log">${esc(s)}</pre>`).join('') || '<p>No text match. Check units, formatting and the original table.</p>'}`).join('')}</details>
      <form data-benchmark-form data-accession="${esc(m.case.accession)}" data-source-hash="${esc(m.source_hash)}" data-case-hash="${esc(m.case_hash)}">
      <label>Labels and corrections (JSON)<textarea name="corrected" rows="14" required>${esc(JSON.stringify(m.review?.corrected_case || m.case, null, 2))}</textarea></label>
      <p>${['period', 'units', 'basis', 'absence'].map((c) => `<label><input type="checkbox" name="checks" value="${c}" required> Verified ${c}</label>`).join(' · ')}</p>
      <label>Your name <input name="reviewer" required maxlength="100"></label> <label>Review notes <input name="note" required maxlength="2000"></label>
      <label>Decision <select name="decision"><option value="approved">Approve reviewed labels</option><option value="rejected">Reject labels</option></select></label>
      <label>Type VERIFIED <input name="confirm" required pattern="VERIFIED"></label>
      <button class="btn lime" ${m.source_available ? '' : 'disabled'}>Record my review</button></form></div>`).join('')}`;
  },
  async operations() {
    const [r, research, budget] = await Promise.all([api('operations'), api('live-research'), api('budget-experiment')]);
    if (!operationId || !r.jobs.some((j) => j.id === operationId)) operationId = r.active?.id || r.autopilot?.id || r.jobs[0]?.id;
    const selected = operationId ? await api(`operation?id=${encodeURIComponent(operationId)}`) : null;
    const states = { starting: 'Starting', running: 'Running', succeeded: 'Passed / finished', warning: 'Finished with warnings', failed: 'Failed', blocked: 'Blocked', interrupted: 'Interrupted', stopped: 'Stopped' };
    const color = (s) => s === 'succeeded' ? 'ok' : ['failed', 'interrupted'].includes(s) ? 'bad' : 'warn';
    return `<h2>Run Center</h2><p class="lede">Run the current AI paper strategy and check the system while the assistant is away.</p>
      ${!r.ready ? '<div class="banner bad">The checkout is missing its Python runtime or run worker.</div>' : ''}
      ${r.active ? `<div class="banner warn"><b>${esc(r.active.label)} is active.</b> ${esc(r.active.message)}</div>` : ''}
      ${r.active ? progressBar((r.active.steps || []).length ? (r.active.steps.filter((x) => !['running', 'starting'].includes(x.state)).length) / Math.max(1, r.active.steps.length) : null, `<b>${esc(r.active.label)}</b> · step ${fmt((r.active.steps || []).length || 1)} · running ${esc(when(r.active.started_at || r.active.created_at))}`) : ''}
      ${r.autopilot?.action === 'full_auto' ? progressBar(null, `<b>Autopilot Night</b> running: ${esc(r.autopilot.message || 'researching and trading')} · <a href="#autopilot">open the Autopilot page</a> · <button class="btn ghost" data-stop-auto="${esc(r.autopilot.id)}">Stop Night</button>`) : ''}
      ${r.day ? progressBar((() => { const n = nyMinutes(), wd = new Date().toLocaleString('en-US', { timeZone: 'America/New_York', weekday: 'short' }); return n < 575 || n > 955 || ['Sat', 'Sun'].includes(wd) ? 0 : (n - 575) / 380; })(), `<b>Autopilot Day</b> running · ${(() => { const n = nyMinutes(); return n < 575 || n > 955 ? 'waiting for the next trading window' : 'trading window in progress'; })()} · <button class="btn ghost" data-stop-auto="${esc(r.day.id)}">Stop Day</button>`) : ''}
      ${r.autopilot && r.autopilot.action !== 'full_auto' ? `<div class="banner warn"><b>Continuous workflow</b> ${esc(r.autopilot.message)}<p>Companies: ${esc(r.autopilot.progress?.attempted ?? 0)} / ${esc(r.autopilot.progress?.total ?? "loading")}; failed: ${esc(r.autopilot.progress?.failed ?? 0)}. Previous failed attempts retained: ${esc(r.autopilot.progress?.previous_failed_attempts ?? 0)}.</p><button class="btn ghost" data-stop-auto="${esc(r.autopilot.id)}">Stop after current step</button></div>` : ""}
      ${r.stockPolicy ? `<div class="banner">AI stock entries restricted to ${esc(r.stockPolicy.symbols?.length ?? 0)} selected technology stocks. <button class="btn ghost" data-view="planning">View universe and cases</button></div>` : ""}
      ${r.halted ? '<div class="banner warn">The kill switch is on. AI trade runs are disabled; order sync follows the existing halt rules.</div>' : ''}
      ${r.mode !== 'live' ? '<div class="banner warn">The system is in rehearsal mode. Paper order actions require the existing live paper mode.</div>' : ''}
      <div class="grid">${r.actions.map((a) => `<div class="card"><h3>${esc(a.label)}</h3><p>${esc(a.description)}</p>
        <button class="btn ${a.paper ? 'lime' : 'ghost'}" data-operation="${esc(a.id)}" ${a.disabled ? 'disabled' : ''}>${a.paper ? 'Start paper run' : a.learning ? 'Review now' : a.label}</button></div>`).join('')}</div>
      <p class="muted">Paper orders use the current strategy, mandate, position sizing, and entry deadlines. Filled trades appear under Live book &amp; orders and AI picks. Each run saves its status and log. Closing the app leaves the run working; after a PC restart an unfinished run is marked interrupted. Automatic jobs take priority; blocked runs can be started again after they finish.</p>
      ${r.researchQueue ? `<div class="card wide"><h3>Company research queue</h3><p>${esc(r.researchQueue.total)} companies · ${r.researchQueue.profile === "tech100" ? "Information Technology only" : "all sectors"} · cached membership ${esc(r.researchQueue.snapshotYear)}. Timing includes model startup, research and judgment. Individual evidence is saved with each attempt.</p><table><tr><th>Company</th><th>State</th><th>Elapsed</th></tr>${r.researchQueue.recent.map((c) => `<tr><td>${esc(c.ticker)} · ${esc(c.name)}</td><td>${esc(c.state)}</td><td>${c.elapsed_s == null ? "—" : fmt(c.elapsed_s) + "s"}</td></tr>`).join("")}</table></div>` : ""}
      <div class="card wide"><h3>Live research experiment</h3><p>Fresh public evidence; experimental horizon targets are separate from approved filing trades. No measured returns until post-decision entry and exit fills exist.</p>
      ${research.error ? `<p class="err">${esc(research.error)}</p>` : !research.companies?.length ? '<p class="empty">No completed cohort yet. Start Live research test above.</p>' : `<p class="tiny muted">${esc(when(research.created_at))} · ${(research.watchlist || []).map(esc).join(', ')}</p>
      <table><tr><th>Company</th><th>Status</th><th>Research</th><th>Judge</th><th>Queue</th><th>Cohort to decision</th><th>Day / medium / long ratings</th></tr>
      ${research.companies.map((x) => `<tr><td>${esc(x.ticker)}</td><td>${esc(x.status)}${x.error_reason || x.error ? ' · ' + esc(x.error_reason || x.error) : ''}</td><td>${x.research_s == null ? '—' : fmt(x.research_s) + 's'}</td><td>${x.judge_s == null ? '—' : fmt(x.judge_s) + 's'}</td><td>${fmt(x.queue_wait_s)}s</td><td>${fmt(x.total_latency_s)}s</td><td>${x.ratings ? ['day','medium','long'].map((h) => esc(x.ratings[h])).join(' / ') : '—'}</td></tr>`).join('')}</table>
      <p>Virtual caps: day 10%, medium 30%, long 50%; each company ≤10% across all horizons. Proposed cash: ${pct(research.portfolio?.cash)}.</p>
      <p>All attempts: median cohort latency ${fmt(research.timing?.all_attempts?.total_latency_s?.median)}s · p95 ${fmt(research.timing?.all_attempts?.total_latency_s?.p95)}s · max ${fmt(research.timing?.all_attempts?.total_latency_s?.max)}s.</p>
      <p>${(research.suggestions || []).map(esc).join(' ')}</p>`}</div>
      <div class="card wide"><h3>Economic discipline comparison</h3><p>Same Jan evidence, same Bonsai model, same limits. Budget-aware wording is compared with neutral wording. Both can PASS; neither can submit orders or change evaluation rules.</p>
      ${budget.error ? `<p class="err">${esc(budget.error)}</p>` : !budget.comparison ? '<p class="empty">Not run yet. Start Test economic discipline above.</p>' : `<p>${esc(budget.comparison.interpretation)}</p>
      <table><tr><th>Arm</th><th>Companies</th><th>Failed</th><th>Median judge time</th><th>p95 judge time</th></tr>
      ${Object.entries(budget.comparison.arms).map(([name,a]) => `<tr><td>${esc(name)}</td><td>${esc(a.attempts)}</td><td>${esc(a.failed)}</td><td>${fmt(a.timing?.all_attempts?.judge_s?.median)}s</td><td>${fmt(a.timing?.all_attempts?.judge_s?.p95)}s</td></tr>`).join('')}</table>
      <p>No automatic winner. Trading returns, factual accuracy and uncertainty are unmeasured; no extra capital or compute has been assigned.</p>`}</div>
      <div class="card wide"><h3>Run status and output</h3>${selected ? `<p><b>${esc(selected.label)}</b> ${pill(states[selected.state] || selected.state, color(selected.state))}</p>
        <p>${esc(selected.message)}</p><p class="tiny muted">Started ${esc(when(selected.started_at || selected.created_at))}${selected.finished_at ? ` · ended ${esc(when(selected.finished_at))}` : ''}</p>
        <ol>${(selected.steps || []).map((s) => `<li>${esc(s.label)} — ${esc(states[s.state] || s.state)}${s.code != null ? ` (exit ${esc(s.code)})` : ''}</li>`).join('')}</ol>
        <pre class="operation-log">${esc(selected.output || 'Waiting for output…')}</pre>` : '<p class="empty">Choose an action above to start your first run.</p>'}</div>
      <div class="card wide"><h3>Recent manual runs</h3><table><tr><th>Started</th><th>Action</th><th>Result</th><th></th></tr>
        ${r.jobs.map((j) => `<tr><td>${esc(when(j.created_at))}</td><td>${esc(j.label)}</td><td>${pill(states[j.state] || j.state, color(j.state))}</td><td><button class="btn ghost" data-operation-log="${esc(j.id)}">View log</button></td></tr>`).join('') || '<tr><td colspan="4" class="muted">No manual runs yet.</td></tr>'}</table></div>`;
  },
  async improvement() {
    const [d, runs] = await Promise.all([api('improvement'), api('operations')]);
    if (d.error) throw new Error(d.error);
    const p = d.prompt, s = d.recipes;
    const review = runs.actions.find((a) => a.id === 'improvement_review');
    const status = { champion: 'Current shadow prompt', challenger: 'Challenger', shadow: 'Forward shadow', promoted: 'Qualified in simulation', rejected_train: 'Failed training check', rejected_holdout: 'Failed holdout check', retired: 'Retired', skipped: 'Deferred' };
    const c = p.comparison;
    return `<h2>Self-improvement</h2><p class="lede">Learn from completed calls, test a candidate, watch it on new releases, and retire it if its edge disappears.</p>
      <div class="banner ok"><b>Learning runs with the existing automatic jobs.</b> Prompt reflection runs after earnings jobs when eligible; signal proposals run monthly during Saturday review. ${runs.mode !== 'live' ? '<b>Rehearsal mode: monthly signal proposals are currently inactive.</b>' : ''}</div>
      <div class="card wide"><h3>Automatic implementation audit</h3><p>Every completed one-click AI paper run reviews existing candidates. Continuous mode reviews hourly; the scheduled jobs propose and evaluate candidates on their existing schedule. Qualified shadow changes, retirement and rollback are applied by the evidence gates. Live trading rules remain under separate deployment control.</p>
        <table><tr><th>Review</th><th>Result</th><th>Prompt shadow changed</th><th>Signal transitions</th></tr>${(d.automatic_reviews || []).slice().reverse().map((r) => `<tr><td>${esc(when(r.at))}</td><td>${esc(r.status)}</td><td>${r.prompt_changed ? 'Yes' : 'No'}</td><td>${r.signal_transitions.map((x) => `${esc(x.name)}: ${esc(x.before)} → ${esc(x.after)}`).join('<br>') || 'None — evidence gates still apply'}</td></tr>`).join('') || '<tr><td colspan="4">No audited review yet. The next successful paper run or hourly review will record one.</td></tr>'}</table></div>
      ${(d.errors || []).map((e) => `<div class="banner bad">${esc(e)}</div>`).join('')}
      <div class="card wide"><h3>Review the evidence now</h3><p>Review previously created candidates using completed outcomes. The review collects decision-time features and updates shadow qualification or retirement. New AI proposals follow the existing monthly schedule.</p>
        <button class="btn lime" data-operation="improvement_review" ${review?.disabled ? 'disabled' : ''}>Review self-improvement</button>
        <p class="muted">Qualified candidates stay in research and paper simulation. Changing the frozen trading rules still requires your approval. This workflow measures evidence; it does not guarantee higher returns.</p></div>
      ${p.available ? `<div class="card wide"><h3>AI prompt learning</h3><p>The bull/bear agent keeps a current prompt and tests one challenger. Bonsai reviews the older calls, checks lessons on held-back calls, then compares both prompts on future releases.</p>
        <div class="kpis">${kpi('Current shadow prompt', `v${esc(p.champion.v)}`)}${kpi('Challenger', p.challenger ? `v${esc(p.challenger.v)}` : 'none')}${kpi('Matured calls', `${fmt(p.matured)} / ${p.reflection_min}`)}${kpi('Needed before reflection', fmt(p.remaining))}</div>
        <p>Promotion requires at least ${p.gates.min_paired} paired calls across ${p.gates.min_months} entry months and evidence score ≥ ${p.gates.e_bound}. A reliably worse challenger retires; a worse current shadow prompt rolls back to v0.</p>
        ${c ? `<p>Current comparison: ${fmt(c.paired)} paired calls · ${fmt(c.months)} months · ${fmt(c.weeks)} completed weeks · evidence for improvement ${fmt(c.e_better, 2)} / ${p.gates.e_bound} · evidence for deterioration ${fmt(c.e_worse, 2)}.</p>` : '<p class="muted">No challenger has accumulated a paired forward comparison yet.</p>'}
        <h4>Lessons in the current shadow prompt</h4>${p.champion.lessons?.length ? `<ul>${p.champion.lessons.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '<p class="muted">No learned lessons yet; the original prompt remains current.</p>'}
        <table><tr><th>Version / month</th><th>State</th><th>Reason or new lessons</th></tr>${p.history.slice().reverse().map((v) => `<tr><td>${esc(v.v == null ? v.month : `v${v.v}`)}</td><td>${esc(status[v.status] || v.status)}</td><td>${esc(v.why || v.new_lessons?.join(' · ') || 'Original baseline')}</td></tr>`).join('')}</table></div>` : ''}
      ${s.available ? `<div class="card wide"><h3>Trading-signal learning</h3><p>At most ${s.gates.proposals_per_month} new recipes per month. Each must pass training and a separate holdout before entering a forward shadow. Qualification requires at least ${s.gates.min_days} days, ${s.gates.min_events} scored releases, and a positive uncertainty bound on added value.</p>
        <div class="kpis">${kpi('In forward shadow', fmt(s.counts.shadow))}${kpi('Qualified in simulation', fmt(s.counts.promoted))}${kpi('Rejected', fmt(s.counts.rejected_train + s.counts.rejected_holdout))}${kpi('Retired', fmt(s.counts.retired))}</div>
        <p class="muted">Promotion checks happen at days ${s.gates.look_days.join(', ')}. Unqualified signals retire after ${s.gates.retire_days} days. Qualified simulation sleeves total at most ${pct(s.gates.sleeve_cap, 0)}. Last review: ${esc(s.review?.at || 'not yet run')}.</p>
        <table><tr><th>Candidate</th><th>Stage</th><th>Recipe / rationale</th><th>Latest evidence</th></tr>${s.signals.map((x) => { const last = x.history.at(-1) || {}; return `<tr><td>${esc(x.name)}</td><td>${esc(status[x.status] || x.status)}</td><td>${esc(x.recipe)}<p class="tiny muted">${esc(x.rationale)}</p></td><td>${esc(last.event || 'none')}${last.events != null ? ` · ${fmt(last.events)} releases` : ''}${last.blend_gain_lo != null ? ` · gain lower bound ${fmt(last.blend_gain_lo, 4)}` : ''}${last.reason ? ` · ${esc(last.reason)}` : ''}</td></tr>`; }).join('') || '<tr><td colspan="4" class="muted">No recipes yet. The next eligible monthly review proposes them.</td></tr>'}</table></div>` : ''}`;
  },
  async overview() {
    const [o, m, td, open] = await Promise.all([api('overview'), metrics().catch(() => ({ missing: true })), api('today').catch(() => null), api('open_decisions').catch(() => [])]);
    const runs = o.recentRuns.filter((r) => r.job !== 'check');
    const clean = runs.slice(0, 10).filter((r) => r.rc === 0 || (r.job === 'events' && r.missed_total != null)).length;  // an alert-only run did its job
    const next = (o.timers || []).map((t) => ({ unit: t.unit || t.activates, next: t.next })).filter((t) => t.next);
    return `<h2>Home</h2><p class="lede">Your practice-money trading system at a glance.</p>
      <div class="banner ${o.halted ? 'bad' : clean === Math.min(10, runs.length) ? 'ok' : 'warn'}">${o.halted
        ? '<b>Trading is stopped.</b> The kill switch is on; nothing will be bought or sold until you resume.'
        : clean === Math.min(10, runs.length) ? '<b>Everything is running normally.</b> The system trades practice money by itself; you do not need to do anything.'
          : `<b>Running, but ${Math.min(10, runs.length) - clean} of the last ${Math.min(10, runs.length)} runs failed.</b> See Alerts below for what happened.`}
        ${next[0] ? ` Next automatic run: <b>${esc(when(Number(next.slice().sort((a, b) => a.next - b.next)[0].next) / 1000))}</b>.` : ''}</div>
      ${plain('airp follows a fixed set of rules to invest <b>pretend money</b>. The numbers below compare the main rule set (the "core book") with simply buying the whole US market (SPY) since 2018. Hover any <b>?</b> for what a word means, or open <b>How it works</b>.')}
      ${todayCard(td)}
      ${openCard(open)}
      ${m.missing ? '' : `<div class="kpis">
        ${kpi('Growth per year', pct(m.tracks.core.stats.cagr), `market (SPY) ${pct(m.tracks.SPY.stats.cagr)}`)}
        ${kpi('Quality score (Sharpe)', esc(m.tracks.core.stats.sharpe), `market (SPY) ${esc(m.tracks.SPY.stats.sharpe)}`)}
        ${kpi('Worst fall', pct(m.tracks.core.stats.max_dd), `market (SPY) ${pct(m.tracks.SPY.stats.max_dd)}`)}
        ${kpi(`Chance of a ${settings.target}% year`, pct(m.tracks.core.tear?.cone?.prob_40, 0), `your baseline · ${settings.stretch}% year: ${pct(m.tracks.core.tear?.cone?.prob_60, 0)}`)}
        ${kpi('Ideas that passed', `${m.lab.tests.filter((x) => x.pass).length}/${m.lab.tests.length}`, 'strategy lab')}
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
    const [b, agg] = await Promise.all([api('books'), api('aggressive').catch(() => null)]);
    const names = Object.keys(b.series);
    const main = b.series['master+brakes'] || b.series.master || [];
    const ai = b.aiPicks;
    const legs = Object.entries(b.orders || {}).flatMap(([k, d]) => (d.legs || []).map((l) => ({ ...l, decision: k })))
      .concat((ai?.pairs || []).flatMap((p) => (p.legs || []).map((l) => ({ ...l, decision: `AI ${p.ticker}` }))));
    return `<h2>Live book &amp; orders</h2><p class="lede">What the system holds right now and the practice orders it has sent.</p>
      ${plain('The system keeps its own record of every trade (the "simulator") and also sends the same trades to a practice account at the broker Alpaca. This page shows both, so you can see they agree. No real money is involved.')}
      ${aggressiveCard(agg)}
      <div class="card wide"><h3>Live equity by book (simulator, weekly runs)</h3>${main.length > 1
        ? lineChart(['aggressive 2.5x', 'master+brakes', 'master', 'SPY'].filter((n) => b.series[n]).concat(names).filter((n, i, a) => a.indexOf(n) === i).slice(0, 4).map((n) => ({ name: n, x: b.series[n].map((r) => r.date), y: b.series[n].map((r) => r.equity) })), { yFmt: (v) => v.toFixed(0) })
        : '<p class="empty">The first live allocator run is Mon 5 Oct 15:00; the curve starts after two weekly runs.</p>'}</div>
      <div class="card wide"><h3>All books</h3><table><tr><th>Book</th><th class="num">Start</th><th class="num">Now</th><th class="num">Return</th><th>Pending targets</th></tr>
        ${names.map((n) => { const s = b.series[n]; const r = s.at(-1).equity / s[0].equity - 1; const t = b.targets[n]?.pending;
          return `<tr><td>${esc(n)}</td><td class="num">${fmt(s[0].equity)}</td><td class="num">${fmt(s.at(-1).equity)}</td><td class="num">${fmt(100 * r, 2)}%</td><td class="mono">${esc(t ? Object.entries(t).map(([k, v]) => `${k} ${fmt(100 * v, 1)}%`).join(' · ') : '–')}</td></tr>`; }).join('') || '<tr><td colspan="5" class="muted">no allocator runs yet (first live run Monday)</td></tr>'}</table></div>
      <div class="card wide"><h3>AI-picks sleeve (untested, 10%)</h3>
        ${ai ? `<p class="muted">Equity ${fmt(ai.equity)} · threshold log-odds ${esc(ai.threshold)} · broker audit ${esc(ai.broker_audit_status)}</p>
        <table><tr><th>Ticker</th><th>ETF hedge</th><th class="num">Score${help('Log-odds')}</th><th>Entry</th><th>Status</th><th>Audit</th></tr>
        ${(ai.pairs || []).map((p) => `<tr><td>${esc(p.ticker)}</td><td>${esc(p.etf)}</td><td class="num">${fmt(p.logodds, 2)}</td><td>${esc(p.entry_day)}</td><td>${pill(p.status, p.status === 'closed' ? '' : 'lime')}</td><td>${esc((p.audit_flags || []).join('; ') || 'ok')}</td></tr>`).join('')}</table>` : '<p class="empty">No AI picks yet.</p>'}</div>
      <div class="card wide"><h3>Paper broker orders</h3><table><tr><th>Decision</th><th>Symbol</th><th>Side</th><th class="num">Qty</th><th>Status</th><th class="num">Fill</th><th class="num">Simulator</th><th class="num">Gap</th></tr>
        ${legs.map((l) => `<tr><td>${esc(l.decision)}</td><td>${esc(l.symbol)}</td><td>${esc(l.side)}</td><td class="num">${esc(l.qty)}</td><td>${pill(l.status, l.status === 'filled' ? 'ok' : ['rejected', 'canceled', 'expired'].includes(l.status) ? 'bad' : '')}</td><td class="num">${fmt(l.filled_price, 2)}</td><td class="num">${fmt(l.sim_price, 2)}</td><td class="num">${l.gap == null ? '–' : `${fmt(l.gap * 1e4, 1)} bp`}</td></tr>`).join('') || '<tr><td colspan="8" class="muted">no orders yet</td></tr>'}</table></div>`;
  },

  async health() {
    const h = await api('health');
    const s = h.stage4 || {}, e = h.execution || {};
    return `<h2>Trading health</h2><p class="lede">Is live trading behaving like the tests said it would?</p>
      ${plain('Two checks. First: is the live result good enough, for long enough, to justify taking more risk later (it needs months of data, so expect "information only" for now). Second: did the practice broker fill orders at the prices the system expected.')}
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
    const [q, x] = await Promise.all([api('research'), api('research_extra').catch(() => null)]);
    if (!Array.isArray(q)) return `<h2>Research queue</h2><p class="err">${esc(q.error)}</p>`;
    return `<h2>Research queue</h2><p class="lede">Long background jobs that test new ideas. They restart themselves.</p>
      ${plain('These jobs gather news and have the AI label thousands of past earnings releases, to test whether research agents can improve the picks. Jobs that need the graphics card only run at night (18:00–05:25) so they never get in the way of live trading. You can pause one if the PC feels slow.')}
      ${x ? `<div class="card wide"><h3>The check this research is waiting for</h3>
        <p class="muted">The news crawl must find at least one headline for ${pct(x.b4b.needed, 0)} of the reports, or the test is stopped without being run. So far ${fmt(x.b4b.done)} of ${fmt(x.b4b.total)} reports are checked${x.b4b.errors ? ` (${fmt(x.b4b.errors)} requests were refused by the news service and are retried)` : ''}.</p>
        <div class="kpis">${kpi('Reports with news so far', x.b4b.coverage == null ? '–' : pct(x.b4b.coverage, 0), `needs ${pct(x.b4b.needed, 0)}`)}${kpi('Checked', `${fmt(x.b4b.done)} / ${fmt(x.b4b.total)}`)}${kpi('AI label engine', esc(x.engine.split(',')[0]), esc(x.engine.split(', ')[1] || ''))}</div></div>` : ''}
      <div class="grid">${q.map((j) => { const [d, t] = j.progress; const pct = t ? Math.min(100, (100 * d) / t) : 0;
        const state = j.done ? pill('done', 'ok') : j.paused ? pill('paused', 'warn') : j.running ? pill('running', 'lime') : pill(j.kind === 'gate' ? 'waiting for check' : 'queued');
        return `<div class="card wide"><div class="row"><h3 style="margin:0">${esc(j.name)}</h3>${pill(j.kind)}${state}
          <span style="margin-left:auto" class="row">${j.kind === 'gate' || j.done ? '' : `<button class="btn ghost" data-q="${j.paused ? 'resume' : 'pause'}" data-name="${esc(j.name)}">${j.paused ? 'Resume' : 'Pause'}</button>`}</span></div>
          <p class="muted">${esc(j.note)}</p>${j.kind === 'gate' ? '' : `<div class="bar"><span style="width:${pct.toFixed(1)}%"></span></div><p class="tiny muted">${fmt(d)} / ${fmt(t)}</p>`}</div>`; }).join('')}</div>`;
  },

  async runs() {
    const r = await api('runs');
    const live = r.runs.filter((x) => x.mode === 'live'), bad = live.filter((x) => x.rc !== 0);
    const next = (r.timers || []).map((t) => ({ unit: String(t.unit || t.activates).replace('.timer', '').replace('.service', '').replace('airp-', ''), next: t.next })).filter((t) => t.next).sort((a, b) => a.next - b.next);
    const took = (s) => (s == null ? '–' : s < 90 ? `${s} s` : `${Math.round(s / 60)} min`);
    return `<h2>Run history</h2><p class="lede">Every automatic run, newest first, with what went wrong if anything did.</p>
      ${plain('The PC runs the system by itself four ways: an <b>earnings run</b> twice a day reads new company reports, a <b>daily check</b> looks for gaps, a <b>weekly rebalance</b> on Monday adjusts the main book, and a <b>weekly review</b> on Saturday writes a report. A run "with a problem" still finished; the alert says what needs attention.')}
      <div class="kpis">${kpi('Live runs recorded', fmt(live.length))}${kpi('Ended clean', live.length ? pct((live.length - bad.length) / live.length, 0) : '–')}${kpi('Last problem', bad[0] ? esc(when(bad[0].start)) : 'none', bad[0] ? esc(JOBS[bad[0].job] || bad[0].job) : '')}${kpi('Next run', next[0] ? esc(when(Number(next[0].next) / 1000)) : '–', next[0] ? esc(JOBS[next[0].unit] || next[0].unit) : '')}</div>
      <div class="card wide"><h3>Coming up</h3><table><tr><th>Job</th><th>Next run</th></tr>${next.map((t) => `<tr><td>${esc(JOBS[t.unit] || t.unit)}</td><td>${esc(when(Number(t.next) / 1000))}</td></tr>`).join('') || '<tr><td colspan="2" class="muted">timers not visible</td></tr>'}</table></div>
      <div class="card wide"><h3>All runs</h3><table><tr><th>Started</th><th>Job</th><th>Mode</th><th class="num">Took</th><th>Result</th><th>What it reported</th></tr>
        ${r.runs.map((x) => `<tr><td>${esc(when(x.start))}</td><td>${esc(JOBS[x.job] || x.job)}</td><td>${esc(x.mode === 'live' ? 'live' : 'rehearsal')}</td><td class="num">${took(x.seconds)}</td><td>${x.rc === 0 ? pill('clean', 'ok') : x.skipped ? pill('skipped', 'warn') : pill('problem', 'bad')}</td><td>${x.alerts.length ? x.alerts.map(esc).join('<br>') : x.gaps ? `${esc(x.gaps)} gap(s)` : '<span class="muted">nothing</span>'}</td></tr>`).join('')}</table></div>`;
  },

  async autopilot() {
    const [d, ops, day] = await Promise.all([api('autopilot'), api('operations'), api('autopilot-day')]);
    const job = ops.autopilot?.action === 'full_auto' ? ops.autopilot : null;
    const other = ops.autopilot && !job ? ops.autopilot : null;
    const action = (ops.actions || []).find((a) => a.id === 'full_auto');
    const st = d.status || {}, a = d.account || {};
    const money = (v) => (v == null ? '–' : `$${fmt(Number(v), 2)}`);
    const HOR = { day: 'Day trade', short: '5 sessions', medium: '21 sessions', long: '63 sessions' };
    const LABEL = { 1: ['strong bear', 'bad'], 2: ['bear', 'bad'], 3: ['no call', ''], 4: ['bull', 'ok'], 5: ['strong bull', 'ok'] };
    const side = (s) => (s > 0 ? pill('LONG (buy)', 'ok') : pill('SHORT (sell)', 'bad'));
    const dayJob = ops.day || null, dayAction = (ops.actions || []).find((a) => a.id === 'autopilot_day');
    const button = job
      ? `<button class="btn" data-stop-auto="${esc(job.id)}" data-back="autopilot">■ Stop Night</button> <span class="muted">Running since ${esc(when(job.started_at || job.created_at))}</span>`
      : `<button class="btn lime big" data-operation="full_auto" ${action?.disabled ? 'disabled' : ''}>▶ Run Autopilot Night</button>
         ${other ? `<span class="err">Stop the other continuous workflow first (${esc(other.label)}).</span>` : action?.disabled ? `<span class="muted">${esc(ops.mode !== 'live' ? 'Paper mode is not on.' : 'Another app run is active.')}</span>` : ''}`;
    const dayButton = dayJob
      ? `<button class="btn" data-stop-auto="${esc(dayJob.id)}" data-back="autopilot">■ Stop Day</button> <span class="muted">Running since ${esc(when(dayJob.started_at || dayJob.created_at))}</span>`
      : `<button class="btn lime big" data-operation="autopilot_day" ${dayAction?.disabled ? 'disabled' : ''}>▶ Run Autopilot Day</button>`;
    const rs = st.research || {}, cur = rs.current;
    const fresh = (d.universe || []).length ? d.fresh / d.universe.length : null;
    const batch = cur ? Math.min(1, (Date.now() - Date.parse(cur.started_at)) / Math.max(1, Date.parse(cur.deadline_at) - Date.parse(cur.started_at))) : null;
    const nightBars = `${progressBar(fresh, `Research fresh (last 20 h): <b>${fmt(d.fresh)} of ${fmt((d.universe || []).length)}</b> companies${rs.memory != null ? ` · ${fmt(rs.memory)} with saved memory` : ''}`, !!job)}
      ${cur ? progressBar(batch, `Now researching <b>${cur.tickers.map(esc).join(', ')}</b> · started ${esc(when(cur.started_at))} · time budget ${fmt(cur.budget_min)} min a company`, true) : job && rs.paused ? `<div class="banner warn"><b>Research is waiting for the GPU.</b> ${esc(rs.paused)}. Trading continues; no company is charged a failed attempt. A reboot after a driver update usually fixes it.</div>` : `<p class="tiny muted">${job ? 'Between research batches (scheduled jobs and the GPU come first).' : 'Not running.'}</p>`}`;
    const ds = day.status || {}, dc = day.config || {};
    const w0 = hmMin(dc.start || '09:35'), w1 = hmMin(dc.end || '15:55'), nowM = nyMinutes();
    const closed = ds.state === 'waiting' || !dayJob;
    const winFrac = closed || nowM <= w0 ? 0 : nowM >= w1 ? 1 : (nowM - w0) / (w1 - w0);
    const dayBars = `${progressBar(dayJob ? winFrac : 0, `${closed && dayJob ? 'Waiting for the market to open · ' : ''}Trading window ${esc(dc.start || '09:35')}–${esc(dc.end || '15:55')} New York · now ${String(Math.floor(nowM / 60)).padStart(2, '0')}:${String(nowM % 60).padStart(2, '0')} · ${fmt(ds.decisions_today ?? 0)} of ${fmt(ds.expected_decisions ?? Math.floor((w1 - w0) / 5))} decisions`, !!dayJob && winFrac > 0 && winFrac < 1)}
      <div class="kpis">${kpi('Mode', day.live ? pill('LIVE paper orders', 'warn') : pill('shadow', ''), 'delete results/forward/algo/LIVE for shadow')}${kpi('Positions', fmt(Object.keys(ds.weights || {}).length), 'of 14 ETFs')}${kpi('Signal speed', ds.latency ? `${fmt(ds.latency.signal_ms)} ms` : '–', ds.latency ? `orders ${fmt(ds.latency.to_orders_ms)} ms after the bar` : 'after each minute bar')}${kpi('Risk', ds.breaker ? pill('breaker on', 'bad') : ds.stale ? pill('stale data', 'warn') : pill('normal', 'ok'), ds.breaker || ds.stale || '4% breaker · 5% stop')}</div>
      <p class="tiny muted">${esc(ds.message || (dayJob ? 'Starting…' : 'Not running.'))}${ds.updated_at ? ` · ${esc(when(ds.updated_at))}` : ''}</p>
      <form class="row" data-day-window><label class="tiny">Start <input type="time" name="start" min="09:35" max="15:55" value="${esc(dc.start || '09:35')}"></label> <label class="tiny">End <input type="time" name="end" min="09:35" max="15:55" value="${esc(dc.end || '15:55')}"></label> <button class="btn ghost" type="submit">Save hours</button> <span class="tiny muted">takes effect at the next decision</span></form>`;
    const acct = !d.account_configured
      ? `<div class="banner warn"><b>The separate Alpaca paper account is not connected yet.</b> Until it is, the autopilot researches and logs what it would trade, and sends no orders.
         <ol class="tiny"><li>At alpaca.markets, open a second <b>paper</b> account (Paper Trading → the account menu → open a new paper account) and generate its API keys.</li>
         <li>Add two lines to <code>backend/.env</code>: <code>AIRP_AUTO_ALPACA_KEY_ID=…</code> and <code>AIRP_AUTO_ALPACA_SECRET_KEY=…</code></li>
         <li>Within a minute the running autopilot connects (keys of the main paper account are refused, so the two books stay apart).</li></ol></div>`
      : d.account_error ? `<p class="err">The separate account did not answer: ${esc(d.account_error)}</p>`
      : `<div class="kpis">${kpi('Total money', money(a.equity), `start ${money(st.start_equity)}`)}${kpi('Today', money(Number(a.equity) - Number(a.last_equity)), pct((Number(a.equity) - Number(a.last_equity)) / Number(a.last_equity || 1), 2))}${kpi('Since start', st.start_equity ? money(Number(a.equity) - st.start_equity) : '–', st.start_equity ? pct(Number(a.equity) / st.start_equity - 1, 2) : '')}${kpi('Cash', money(a.cash))}${kpi('Long / short', `${money(a.long_market_value)} / ${money(a.short_market_value)}`)}${kpi('Day trades (5 days)', fmt(a.daytrade_count))}</div>`;
    const iso = (t) => new Date(t * 1000).toISOString();
    const hd = d.history_day || {}, hm = d.history_month || {};
    const chart = (h, name, f) => (h.timestamp?.length > 1 ? lineChart([{ name, x: h.timestamp.map(f), y: (h.equity || []).map((v) => (v == null ? null : Number(v))) }], { height: 220, yFmt: (v) => '$' + fmt(v), xTicks: name === 'Today' ? (t) => new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : (t) => t.slice(5, 10) }) : '<p class="empty">The graph starts after the first trading session.</p>');
    const local = d.equity?.length > 1 ? lineChart([{ name: 'Total money', x: d.equity.map((r) => r.t), y: d.equity.map((r) => r.v) }], { height: 220, yFmt: (v) => '$' + fmt(v), xTicks: (t) => new Date(t).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }) : '<p class="empty">Recorded every minute while the market is open, once the account is connected.</p>';
    const lots = (d.lots || []).map((x) => `<tr><td><b>${esc(x.symbol)}</b>${x.theme ? ` <span class="muted tiny">${esc(x.theme)}</span>` : ''}</td><td>${side(x.side)}</td><td>${esc(HOR[x.horizon] || x.horizon)}</td><td class="num">${pct(x.weight, 1)}</td><td class="num">${x.entry_price ? fmt(x.entry_price, 2) : '–'}</td><td>${esc(x.exit_session || '')}</td><td class="tiny muted">${esc((x.sizing || []).join(' · '))}</td></tr>`).join('');
    const pos = (d.positions || []).map((p) => `<tr><td><b>${esc(p.symbol)}</b></td><td>${esc(p.side)}</td><td class="num">${fmt(Number(p.qty))}</td><td class="num">${fmt(Number(p.avg_entry_price), 2)}</td><td class="num">${fmt(Number(p.current_price), 2)}</td><td class="num">${money(p.market_value)}</td><td class="num ${Number(p.unrealized_pl) < 0 ? 'err' : ''}">${money(p.unrealized_pl)} (${pct(Number(p.unrealized_plpc), 2)})</td></tr>`).join('');
    const fills = [...(d.trades || [])].reverse().map((f) => `<tr><td>${esc(when(f.time))}</td><td>${f.side === 'buy' ? pill('buy', 'ok') : pill(f.side, 'bad')}</td><td><b>${esc(f.symbol)}</b></td><td class="num">${fmt(f.qty)}</td><td class="num">${fmt(f.price, 2)}</td><td class="num">${money(f.qty * f.price)}</td><td class="tiny">${esc(f.why)}</td></tr>`).join('');
    const ords = (d.orders || []).slice(0, 60).map((o) => `<tr><td>${esc(when(o.submitted_at))}</td><td>${esc(o.side)}</td><td><b>${esc(o.symbol)}</b></td><td class="num">${fmt(Number(o.qty))}</td><td class="num">${fmt(Number(o.filled_qty))}</td><td class="num">${o.filled_avg_price ? fmt(Number(o.filled_avg_price), 2) : '–'}</td><td>${esc(o.status)}</td></tr>`).join('');
    const thought = (t) => `<details><summary><b>${esc(t.ticker)}</b> ${Object.entries(t.ratings || {}).map(([h, l]) => pill(`${HOR[h] || h}: ${(LABEL[l] || [l])[0]}`, (LABEL[l] || [, ''])[1])).join(' ')} <span class="muted tiny">${esc(when(t.at))} · ${esc(t.regime)} market · ${t.trades?.length ? `${fmt(t.trades.length)} trade(s)` : 'no trade'}</span></summary>
        <h4>Jan (researcher)</h4>${(t.jan_steps || []).map((s, i) => `<div class="step"><b>Step ${i + 1}.</b> ${esc(s.thought || '(no thought written)')}${s.tools ? `<div class="tools">→ ${s.tools.map(esc).join('<br>→ ')}</div>` : ''}${s.estimate?.reason ? `<p class="tiny muted">${esc(s.estimate.reason)}</p>` : ''}</div>`).join('') || '<p class="muted tiny">No steps recorded.</p>'}
        ${(t.facts || []).length ? `<details><summary class="tiny">Facts it found (${fmt(t.facts.length)})</summary><ul class="tiny">${t.facts.map((f) => `<li>${esc(f)}</li>`).join('')}</ul></details>` : ''}
        <h4>Bonsai (judge)</h4>${Object.entries(t.bonsai || {}).map(([h, b]) => `<div class="step"><b>${esc(HOR[h] || h)}</b> ${b.label != null ? pill((LABEL[b.label] || [b.label])[0], (LABEL[b.label] || [, ''])[1]) : ''}${b.quote ? `<p class="tiny">“${esc(b.quote)}”</p>` : ''}${b.why ? `<p class="tiny muted">${esc(b.why)}</p>` : ''}${b.risk ? `<p class="tiny muted">What would prove it wrong: “${esc(b.risk)}”</p>` : ''}</div>`).join('')}
        ${t.bull_case || t.bear_case ? `<div class="cases"><div><b>Bull case</b><p class="tiny">${esc(t.bull_case || '–')}</p></div><div><b>Bear case</b><p class="tiny">${esc(t.bear_case || '–')}</p></div></div>` : ''}
        ${t.double_check ? `<p class="tiny">Double-check of its draft: ${t.double_check.changed?.length ? pill(`changed ${t.double_check.changed.join(', ')}`, 'warn') : pill('draft confirmed', 'ok')}</p>` : ''}${t.primary ? `<p class="tiny">Primary horizon: <b>${esc(HOR[t.primary] || t.primary)}</b></p>` : ''}
        <h4>What the autopilot did</h4>${t.trades?.length ? t.trades.map((x) => `<div class="step">${side(x.side)} ${esc(HOR[x.horizon] || x.horizon)} · ${pct(x.weight, 1)} of the account · exits ${esc(x.exit_session)}</div>`).join('') : '<p class="tiny muted">No trade (no side, not tradable on this horizon, too old, or not shortable).</p>'}
        <p class="tiny muted">${t.research_s ? `Research ${fmt(t.research_s)} s · Bonsai ${fmt(t.judge_s)} s · ` : ''}<a href="#researchlog">Full log and websites →</a></p></details>`;
    const ev = (d.events || []).slice(0, 40).map((e) => `<tr><td>${esc(when(e.at))}</td><td>${esc(e.type)}</td><td class="tiny">${esc(JSON.stringify(Object.fromEntries(Object.entries(e).filter(([k]) => !['at', 'type', 'seq', 'hash', 'prev'].includes(k))))).slice(0, 300)}</td></tr>`).join('');
    return `<h2>Autopilot</h2><p class="lede">Two buttons, one separate paper account. <b>Night</b>: Jan and Bonsai research the companies and trade their bull and bear calls long and short over days to months. <b>Day</b>: a price-only algorithm day-trades 14 ETFs inside the hours you set.</p>
      ${plain('<b>Night</b>: Jan reads the news and filings (skipping pages it already read and reusing saved facts), Bonsai calls each horizon bull or bear, and the autopilot buys the bull calls and short-sells the bear calls for 5 to 63 sessions in its <b>own practice account</b> (paper money). <b>Day</b>: a price-only algorithm trades 14 ETFs every 5 minutes inside your hours and is flat at the end. Leverage stays the same; the risk rules cap any one theme at 30% net, stop new risk after a 4% down day, and skip stale data. A 35% fall from the start closes everything. Every day-trading rule we tested lost money after costs, so watch Day as an experiment.')}
      <div class="split"><div class="card">${h3('Autopilot Night: research + long/short swing book', 'Autopilot Night')}<div class="row">${button}</div>${nightBars}</div>
        <div class="card">${h3('Autopilot Day: algorithmic day trading (no AI)', 'Autopilot Day')}<div class="row">${dayButton}</div>${dayBars}</div></div>
      <div class="card wide"><p class="muted">${esc(st.message || 'Not started yet.')}${st.updated_at ? ` · ${esc(when(st.updated_at))}` : ''}</p>
        ${d.killed ? `<p class="err"><b>Stopped by the 35% drawdown limit:</b> ${esc(d.killed)}</p>` : ''}${st.last_error ? `<p class="tiny err">Last error (${esc(when(st.last_error_at))}): ${esc(st.last_error)}</p>` : ''}
        <div class="kpis">${kpi('Market', esc(st.regime || '–'), 'QQQ trend')}${kpi('Open calls', fmt(st.open_calls ?? (d.lots || []).length), `${fmt(st.longs ?? 0)} long · ${fmt(st.shorts ?? 0)} short`)}${kpi('Fresh research', `${fmt(d.fresh)} of ${fmt((d.universe || []).length)}`, 'decided in the last 20 hours')}${kpi('Phase', esc(st.phase || '–'))}</div></div>
      <div class="card wide">${h3('Separate paper account', 'Autopilot account')}${acct}</div>
      <div class="split"><div class="card">${h3('Total money: today', 'Autopilot equity')}${chart(hd, 'Today', iso)}</div><div class="card">${h3('Total money: last month', 'Autopilot equity')}${chart(hm, 'Month', (t) => iso(t).slice(0, 10))}</div></div>
      <div class="card wide">${h3('Total money: every minute since the start', 'Autopilot equity')}${local}</div>
      <div class="card wide">${h3('Combined fund: core trend book + autopilot', 'Combined fund')}${d.combined?.timestamp?.length > 1 ? `
        ${lineChart([{ name: 'Combined fund', x: d.combined.timestamp.map((t) => iso(t).slice(0, 10)), y: d.combined.total }, { name: 'Main account (core book)', x: d.combined.timestamp.map((t) => iso(t).slice(0, 10)), y: d.combined.main }, { name: 'Autopilot', x: d.combined.timestamp.map((t) => iso(t).slice(0, 10)), y: d.combined.auto }], { height: 240, yFmt: (v) => '$' + fmt(v), xTicks: (t) => t.slice(5, 10) })}
        <div class="kpis">${kpi('Combined money', money(d.combined.total.at(-1)))}${kpi('Combined drawdown', pct(d.combined.drawdown, 1), 'from its peak this month')}${kpi('Daily-change correlation', d.combined.correlation == null ? '–' : fmt(d.combined.correlation, 2), 'near 0 = the two books steady each other')}</div>` : '<p class="empty">Appears after two trading days with both accounts.</p>'}
        <p class="tiny muted">The two accounts stay separate; this only adds them up. The main account also holds the AI-picks sleeve.</p></div>
      <div class="card wide">${h3('Stability rules', 'Stability rules')}
        <p class="tiny">${pill('Market-neutral hedge', 'ok')} Short ${pct(Math.abs(st.hedge ?? 0), 0)} of the account in QQQ, QQQM, XLK and VGT against the stocks’ market exposure (beta), so market swings largely cancel out.</p>
        <table><tr><th>Horizon</th><th class="num">Finished trades</th><th class="num">Average edge over the market</th><th class="num">Kelly size</th></tr>
        ${Object.entries(d.kelly || {}).map(([h, k]) => `<tr><td>${esc(HOR[h] || h)}</td><td class="num">${fmt(k.n)}</td><td class="num">${k.mean == null ? '–' : pct(k.mean, 2)}</td><td class="num">${k.n < 100 ? `×1 <span class="muted tiny">(needs 100)</span>` : k.mult === 0 ? pill('off: no edge', 'bad') : `×${fmt(k.mult, 2)}`}</td></tr>`).join('') || '<tr><td colspan="4" class="muted">Starts with the first finished trades.</td></tr>'}</table>
        <p class="tiny muted">Kelly (Berlekamp’s rule at Renaissance): bet in proportion to the measured edge. Half Kelly, at most ×2; a horizon whose finished trades lose is switched off until its record turns positive.</p></div>
      <div class="card wide">${h3('Open calls (the plan)', 'Autopilot calls')}${lots ? `<table><tr><th>Stock</th><th>Side</th><th>Horizon</th><th class="num">Size</th><th class="num">Entry</th><th>Exit day</th><th>How the size was set</th></tr>${lots}</table>` : '<p class="empty">No open calls.</p>'}</div>
      <div class="card wide">${h3('Positions at the broker', 'Autopilot positions')}${pos ? `<table><tr><th>Stock</th><th>Side</th><th class="num">Shares</th><th class="num">Bought at</th><th class="num">Now</th><th class="num">Value</th><th class="num">Profit</th></tr>${pos}</table>` : '<p class="empty">No positions.</p>'}</div>
      <div class="card wide">${h3('Every trade (fills)', 'Autopilot trades')}${fills ? `<table><tr><th>When</th><th>Side</th><th>Stock</th><th class="num">Shares</th><th class="num">Price</th><th class="num">Amount</th><th>Why</th></tr>${fills}</table>` : '<p class="empty">No trades yet.</p>'}
        ${ords ? `<details><summary>Orders (latest 60)</summary><table><tr><th>Sent</th><th>Side</th><th>Stock</th><th class="num">Shares</th><th class="num">Filled</th><th class="num">Price</th><th>Status</th></tr>${ords}</table></details>` : ''}</div>
      <div class="card wide rlog">${h3("The AI models' chain of thought, newest first", 'Autopilot reasoning')}${(d.thoughts || []).length ? d.thoughts.map(thought).join('') : '<p class="empty">Written as each company’s research is decided.</p>'}</div>
      <div class="card wide">${h3('Autopilot log', 'Autopilot log')}${ev ? `<table><tr><th>When</th><th>What</th><th>Details</th></tr>${ev}</table>` : '<p class="empty">Nothing yet.</p>'}</div>`;
  },

  // O1 Fund control (review #41-#50, #30): one page for the account's risk, the user's controls and the evidence
  async fund() {
    const [r, fr, day, ops] = await Promise.all([api('fund-report'), api('fact-review'), api('autopilot-day'), api('operations')]);
    if (r.error) return `<h2>Fund control</h2><p class="err">${esc(r.error)}</p>`;
    const rk = r.risk || {}, ex = rk.exposure || {}, st = rk.states || {};
    const bp = (v) => (v == null ? '–' : `${fmt(v * 1e4, 1)} bp`);
    const stateKind = (s) => ({ trading: 'ok', waiting: '', ready: 'ok', reduce_only: 'warn', reconciling: 'warn', recovering: 'warn', halted: 'bad' }[s] || '');
    const nightOn = !!(ops.autopilot && ops.autopilot.action === 'full_auto'), dayOn = !!ops.day;
    const mode = (on, live, extra) => (!on ? pill('stopped') : live ? pill('ALPACA PAPER ORDERS', 'warn') : pill('SIMULATED (shadow, no orders)'))
      + (extra ? ` ${pill(extra, 'bad')}` : '');
    // #41: the exact mode of each part, prominently
    const banner = `<div class="modebar">
      <div><b>Research</b> ${pill(nightOn ? 'Jan + Bonsai running' : 'idle', nightOn ? 'ok' : '')}</div>
      <div><b>Night</b> ${mode(nightOn, true)}</div>
      <div><b>Day</b> ${mode(dayOn, day.live, day.status?.qualified === false ? 'failed strategy: experiment' : '')}</div>
      <div><b>Real money</b> ${pill('NEVER: paper endpoint only', 'ok')}</div></div>`;
    // #50: separate pause / cancel / close controls, with the broker-confirmed result
    const ctl = (s, label, cur) => {
      const c = (rk.controls || {})[s] || [], paused = c.some(([n]) => n === 'PAUSE');
      const conf = cur?.controls || {};
      const done = (k) => (conf[k] ? `${conf[k].confirmed ? pill('confirmed by the broker', 'ok') : pill('NOT yet confirmed', 'bad')} <span class="tiny muted">${esc(when(conf[k].at))}</span>` : '');
      return `<div class="card"><h3>${esc(label)}</h3>
        <p>State: ${pill(st[s]?.state || '–', stateKind(st[s]?.state))} <span class="tiny muted">${esc(st[s]?.reason || '')} · ${esc(st[s]?.allows || '')}</span></p>
        <div class="row">${paused ? `<button class="btn" data-control="${s}" data-act="resume">▶ Resume new entries</button>` : `<button class="btn ghost" data-control="${s}" data-act="pause">⏸ Pause new entries</button>`}
        <button class="btn ghost" data-control="${s}" data-act="cancel">✕ Cancel pending entries</button>
        <button class="btn" data-control="${s}" data-act="flatten">⏹ Close owned positions</button></div>
        <p class="tiny muted">Pause: only orders that shrink positions. Cancel: open orders of this autopilot are withdrawn. Close: its positions are sold or bought back, then checked at the broker; until the broker shows them gone an incident stays open.</p>
        <p class="tiny">Cancel ${done('cancel') || '<span class="muted">not used</span>'} · Close ${done('flatten') || '<span class="muted">not used</span>'}</p></div>`;
    };
    // #45: one account-risk dashboard (broker data only)
    const owners = Object.entries(ex.by_owner || {}).map(([k, v]) => `<tr><td>${esc(k === 'day' ? 'Day (14 ETFs)' : 'Night (stocks + hedge)')}</td><td class="num">${pct(v.gross, 1)}</td><td class="num">${pct(v.net, 1)}</td></tr>`).join('');
    const themes = Object.entries(ex.themes || {}).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${pct(v.gross, 1)}</td><td class="num">${pct(v.net, 1)}</td><td class="tiny muted">cap 50% gross / 30% net</td></tr>`).join('');
    const riskCard = rk.error ? `<p class="err">${esc(rk.error)}</p>` : `<div class="kpis">${kpi('Gross exposure', pct(ex.gross, 0), `cap ${fmt(rk.ceilings?.max_gross, 1)}x`)}${kpi('Net exposure', pct(ex.net, 0))}${kpi('Beta-weighted net', pct(ex.beta_net, 0), 'Night cap ±30%')}${kpi('Pending orders', pct(ex.pending_gross, 0), `worst case total ${pct(ex.worst_case_gross, 0)}`)}${kpi('Headroom', pct(ex.headroom_gross, 0), `buying power $${fmt(ex.buying_power)}`)}</div>
      <div class="split"><table><tr><th>Owner</th><th class="num">Gross</th><th class="num">Net</th></tr>${owners || '<tr><td colspan="3" class="muted">no positions</td></tr>'}</table>
      <table><tr><th>Theme</th><th class="num">Gross</th><th class="num">Net</th><th></th></tr>${themes || '<tr><td colspan="4" class="muted">no theme positions</td></tr>'}</table></div>
      <p class="tiny">Largest: ${(ex.top || []).map((t) => `${esc(t.symbol)} ${pct(t.weight, 1)}`).join(' · ') || '–'} · Breakers: Night ${esc(rk.breakers?.night || 'off')}, Day ${esc(rk.breakers?.day || 'off')} · Hard ceilings: gross ${fmt(rk.ceilings?.max_gross, 1)}x, one asset ${pct(rk.ceilings?.max_asset, 0)}, daily loss ${pct(rk.ceilings?.daily_loss, 0)}, drawdown ${pct(rk.ceilings?.max_drawdown, 0)} (no config can loosen them)</p>`;
    // #46: acknowledgments are not fills
    const SK = { filled: 'ok', submitted: 'warn', intended: '', rejected: 'bad', canceled: '', expired: '', never_sent: '', unexplained: 'bad' };
    const orders = `<div class="row">${Object.entries(r.orders?.by_state || {}).map(([k, v]) => pill(`${k}: ${v}`, SK[k] || '')).join(' ') || '<span class="muted">No orders yet.</span>'}</div>
      ${(r.orders?.recent || []).length ? `<details><summary>Latest orders (${fmt(r.orders.recent.length)})</summary><table><tr><th>When</th><th>Who</th><th>Order</th><th>State</th><th class="num">Filled</th><th>Note</th></tr>${r.orders.recent.map((o) => `<tr><td>${esc(when(o.at))}</td><td>${esc(o.strategy)}</td><td>${esc(o.side)} ${fmt(o.qty)} <b>${esc(o.symbol)}</b>${o.limit ? ` @ ${fmt(o.limit, 2)}` : ''}</td><td>${pill(o.state, SK[o.state] || '')}</td><td class="num">${o.filled_qty == null ? '–' : fmt(o.filled_qty)}</td><td class="tiny muted">${esc(o.note || '')}</td></tr>`).join('')}</table></details>` : ''}
      <p class="tiny muted">Submitted = the broker acknowledged the order; only "filled" means shares changed hands. Rejected, canceled and expired orders are listed with their reason.</p>`;
    // #43: why no trade
    const wn = Object.entries(r.why_no_trade?.night || {}).sort((a, b) => (a[1].at < b[1].at ? 1 : -1)).slice(0, 40);
    const wd = Object.entries(r.why_no_trade?.day || {});
    const why = `<div class="split"><div><h4>Night (latest 40 companies)</h4>${wn.length ? `<details><summary>${fmt(wn.length)} companies held back: ${esc([...new Set(wn.flatMap(([, v]) => v.reasons.map((x) => x.split(':').pop().trim())))].slice(0, 3).join('; '))}</summary><table>${wn.map(([t, v]) => `<tr><td><b>${esc(t)}</b></td><td class="tiny">${v.reasons.map(esc).join('<br>')}</td></tr>`).join('')}</table></details>` : '<p class="empty">Nothing held back yet.</p>'}</div>
      <div><h4>Day (last decision)</h4>${wd.length ? `<table>${wd.map(([s, v]) => `<tr><td><b>${esc(s)}</b></td><td class="tiny">${esc(v)}</td></tr>`).join('')}</table>` : '<p class="empty">No decision yet today.</p>'}</div></div>`;
    // #44: the opportunity funnel
    const F = { companies: 'Companies', researched: 'Researched', decided: 'Decided', eligible_calls: 'Eligible calls', risk_approved: 'Risk-approved orders', submitted: 'Submitted', filled: 'Filled', exited: 'Exited', evaluated: 'Evaluated' };
    const fs = r.funnel?.stages || {};
    const drops = (name, rows) => (rows?.length ? `<h4>${esc(name)}</h4><table>${rows.map(([w, n]) => `<tr><td class="tiny">${esc(w)}</td><td class="num">${fmt(n)}</td></tr>`).join('')}</table>` : '');
    const funnel = `<div class="funnel">${Object.entries(F).map(([k, l]) => `<div><div class="tiny muted">${esc(l)}</div><b>${fmt(fs[k] ?? 0)}</b></div>`).join('<span class="muted">→</span>')}</div>
      <div class="split"><div>${drops('Research that failed', r.funnel?.drops?.['research failed'])}</div><div>${drops('Calls not traded', r.funnel?.drops?.['no trade'])}${drops('Orders rejected', r.funnel?.drops?.rejected)}</div></div>`;
    // #34 #35 #38 #39: scored calls
    const sm = (x) => (!x || !x.n ? '<td class="num">0</td><td class="num">–</td><td class="num">–</td><td class="num">–</td>' : `<td class="num">${fmt(x.n)}</td><td class="num ${x.mean < 0 ? 'err' : ''}">${pct(x.mean, 2)}</td><td class="num">${pct(x.hit, 0)}</td><td class="num tiny">${x.ci ? `${pct(x.ci[0], 2)} … ${pct(x.ci[1], 2)}` : '–'}</td>`);
    const tbl = (title, obj) => `<h4>${esc(title)}</h4><table><tr><th></th><th class="num">Calls</th><th class="num">Mean vs market</th><th class="num">Right</th><th class="num">95% range</th></tr>${Object.entries(obj || {}).map(([k, v]) => `<tr><td>${esc(k)}</td>${sm(v)}</tr>`).join('')}</table>`;
    const c = r.calls || {};
    const calls = `<p class="tiny">${esc(c.target || '')}. Before trading costs. Only horizons whose exit day has closed.</p>
      <table><tr><th></th><th class="num">Calls</th><th class="num">Mean vs market</th><th class="num">Right</th><th class="num">95% range</th></tr><tr><td><b>All AI calls</b></td>${sm(c.all)}</tr></table>
      <div class="split"><div>${tbl('Calibration: by label (#34)', c.calibration_by_label)}${tbl('By horizon', c.by_horizon)}${tbl('By market regime (#39)', c.by_regime)}</div><div>${tbl('By side (1 = long)', c.by_side)}${tbl('By evidence support', c.by_support)}${tbl('With research memory', c.by_memory)}</div></div>
      <p class="tiny">Abstention (#35): ${fmt(c.abstention?.n ?? 0)} horizon calls had no side; the market-relative move missed on them averaged ${c.abstention?.mean_abs_move_missed == null ? '–' : pct(c.abstention.mean_abs_move_missed, 2)} (in either direction).</p>
      ${tbl('AI calls against simple controls, same dates and sizes (#38)', Object.fromEntries(Object.entries(r.controls || {}).filter(([k]) => k !== 'note')))}`;
    // #47 execution quality
    const exq = `<div class="kpis">${kpi('Fills', fmt(r.execution?.fills ?? 0))}${Object.entries(r.execution?.slippage_bp || {}).map(([k, v]) => kpi(`Slippage ${k === 'dy' ? 'Day' : k === 'nt' ? 'Night' : k}`, `${fmt(v.mean, 1)} bp`, `${fmt(v.n)} fills vs decision price`)).join('')}${kpi('Turnover', '$' + fmt(r.execution?.turnover_total ?? 0, 0), r.execution?.turnover_equity_multiple != null ? `${fmt(r.execution.turnover_equity_multiple, 2)}x equity` : 'filled notional')}${Object.entries(r.execution?.spread_paid_bp || {}).map(([k, v]) => kpi(`Half-spread ${k === 'dy' ? 'Day' : k === 'nt' ? 'Night' : k}`, `${fmt(v.mean, 1)} bp`, `${fmt(v.n)} fills, quoted at the decision`)).join('')}</div><p class="tiny muted">Slippage: fill price against the price when the order was decided (positive = paid more). ${esc(r.execution?.spread_note || '')} Equity, drawdown and the combined fund are on the Autopilot page.</p>`;
    // #48 evidence, #28 feed check, #30 fact check, #29 audit
    const ev = (r.evidence || []).slice().reverse().map((e) => `<tr><td>${esc(e.name)}</td><td>${esc(e.evidence)}</td><td class="num">${e.sharpe == null ? (e.mean_net_bp == null ? '–' : `${fmt(e.mean_net_bp)} bp/call`) : fmt(e.sharpe, 2)}</td><td class="num">${e.n == null ? '–' : fmt(e.n)}</td><td class="tiny">${esc(e.window || '')}</td><td>${pill(e.result || '–', String(e.result).startsWith('pass') ? 'ok' : String(e.result).startsWith('fail') ? 'bad' : 'warn')}</td></tr>`).join('');
    const fc = r.feed_check || {};
    const facts = (fr.queue || []).slice(0, 10).map((f) => `<tr><td><b>${esc(f.ticker)}</b></td><td class="tiny">${esc(f.text)}<br><a href="${esc(f.source)}" target="_blank" rel="noreferrer">${esc(String(f.source).slice(0, 80))}</a> ${esc(f.date || '')}</td><td class="row"><button class="btn ghost" data-fact="${esc(f.id)}" data-verdict="correct">✓</button><button class="btn ghost" data-fact="${esc(f.id)}" data-verdict="wrong">✗</button><button class="btn ghost" data-fact="${esc(f.id)}" data-verdict="unclear">?</button></td></tr>`).join('');
    // #49 incidents
    const inc = (r.incidents || []).map((i) => `<tr><td>${esc(when(i.at))}</td><td>${pill(i.state, i.state === 'open' ? 'bad' : 'ok')}</td><td>${esc(i.kind)}</td><td class="tiny">${esc(i.detail)}${Object.keys(i.exposed || {}).length ? `<br>Exposed: ${esc(JSON.stringify(i.exposed))}` : ''}</td><td class="tiny">${esc(i.fallback || '')}</td><td class="tiny">${esc(i.resolution || i.resume_when || '')}</td></tr>`).join('');
    return `<h2>Fund control</h2><p class="lede">The autopilot account in one place: what mode each part is in, your controls, actual risk, why trades did or did not happen, and how good the evidence is.</p>
      ${banner}
      <div class="split">${ctl('night', 'Autopilot Night', (await api('autopilot')).status)}${ctl('day', 'Autopilot Day', day.status)}</div>
      <div class="card wide">${h3('Account risk (broker data)', 'Account risk')}${riskCard}</div>
      <div class="card wide">${h3('Orders: acknowledged is not filled', 'Order states')}${orders}</div>
      <div class="card wide">${h3('Why no trade?', 'Why no trade')}${why}</div>
      <div class="card wide">${h3('Opportunity funnel', 'Opportunity funnel')}${funnel}</div>
      <div class="card wide">${h3('How the AI calls did (scored on the defined target)', 'Call evaluation')}${calls}</div>
      <div class="card wide">${h3('Execution quality', 'Execution quality')}${exq}</div>
      <div class="card wide">${h3('Evidence behind each strategy', 'Model evidence')}<table><tr><th>Strategy or test</th><th>Evidence</th><th class="num">Sharpe / result</th><th class="num">Sample</th><th>Window</th><th>Verdict</th></tr>${ev}</table>
        <p class="tiny">Day's live data (#28): ${esc(fc.verdict || 'not checked yet')}${fc.features ? ` · volume feature correlation ${fmt(fc.features.volz?.corr, 2)}, 1-minute return ${fmt(fc.features.r1?.corr, 2)}` : ''}</p>
        <p class="tiny">Research timestamp audit (#29): ${fmt((r.audit_exceptions || []).length)} record(s) with exceptions${(r.audit_exceptions || []).length ? `: ${r.audit_exceptions.slice(0, 5).map((x) => esc(x.ticker)).join(', ')}` : ''}.</p></div>
      <div class="card wide">${h3("Check Jan's facts (human-verified accuracy)", 'Fact check')}<p>Verified accuracy so far: <b>${fr.accuracy == null ? '–' : pct(fr.accuracy, 0)}</b> of ${fmt(fr.n_scored ?? 0)} facts marked. Open the source, then mark whether the fact says what the page says.</p>
        ${facts ? `<table>${facts}</table>` : '<p class="empty">Queue empty.</p>'}</div>
      <div class="card wide">${h3('Incidents and recovery', 'Incidents')}${inc ? `<table><tr><th>When</th><th>State</th><th>What</th><th>Detail</th><th>Fallback</th><th>Resumes when / resolved by</th></tr>${inc}</table>` : '<p class="empty">No incidents.</p>'}</div>`;
  },

  async researchlog() {
    const r = await api('research_log');
    const p = r.progress || {}, total = p.total || 150, done = p.done || 0;
    const dur = (x) => (x == null ? '–' : x < 90 ? `${fmt(x)} s` : x < 5400 ? `${fmt(x / 60)} min` : `${fmt(x / 3600, 1)} h`);
    const LABEL = { 1: ['strong bear', 'bad'], 2: ['bear', 'bad'], 4: ['bull', 'ok'], 5: ['strong bull', 'ok'] };
    const HOR = { day: 'Next session', short: '5 sessions', medium: '21 sessions', long: '63 sessions' };
    const SUP = { event: 'backed by a company event', quoted: 'quote is only about the share price', none: 'no valid quote' };
    const finish = p.eta_s && p.state === 'running' ? new Date(Date.now() + p.eta_s * 1000) : null;
    const bar = !p.total ? '<p class="empty">No 150-company run yet.</p>' : `
      <div class="progress"><div style="width:${Math.min(100, (100 * done) / total).toFixed(1)}%"></div></div>
      <div class="kpis">${kpi('Companies done', `${fmt(done)} of ${fmt(total)}`, pct(done / total, 0))}${kpi('Time left', p.state === 'running' ? dur(p.eta_s) : '–', finish ? `about ${esc(when(finish.toISOString()))}` : esc(p.state || ''))}${kpi('Per company', dur(p.per_company_s), 'median of finished batches')}${kpi('Batches run', fmt((p.batches || []).length), `${fmt(Object.keys(p.attempts || {}).length)} companies failed at least once`)}</div>
      <p class="muted">${esc(p.message || '')}${r.active ? ` · in progress: ${r.active.companies.map((c) => `${esc(c.ticker)} (${esc(c.stage || c.status || '…')})`).join(', ')}` : ''}</p>`;
    const call = (h, c) => { const [name, kind] = LABEL[c.label] || [c.label, '']; return `<div class="step"><b>${esc(HOR[h] || h)}</b> ${pill(name, kind)} <span class="muted tiny">${esc(SUP[c.support] || c.support)}${c.weakened ? ', weakened from strong' : ''}</span>
      ${c.quote ? `<p class="tiny">“${esc(c.quote)}”</p>` : ''}${c.why ? `<p class="tiny muted">${esc(c.why)}</p>` : ''}</div>`; };
    const one = (c) => {
      const b = c.bonsai, j = c.jan, calls = Object.entries(b.calls || {});
      const summary = calls.length ? calls.map(([h, x]) => pill(`${h}: ${(LABEL[x.label] || [x.label])[0]}`, (LABEL[x.label] || [, ''])[1])).join(' ') : pill(c.status || 'running', c.status === 'decided' ? '' : 'bad');
      return `<details><summary><b>${esc(c.ticker)}</b> ${summary} <span class="muted tiny">${esc(when(c.decided_at || c.as_of))} · research ${dur(c.research_s)} · Bonsai ${dur(c.judge_s)}${c.error ? ` · ${esc(c.error)}` : ''}</span></summary>
        <h4>Jan (researcher)</h4>
        ${j.searches.length ? `<p class="tools">Searches: ${j.searches.map(esc).join(' · ')}</p>` : ''}
        ${j.steps.map((s, i) => `<div class="step"><b>Step ${i + 1}.</b> ${esc(s.thought || '(no thought written)')}${s.tools ? `<div class="tools">→ ${s.tools.map(esc).join('<br>→ ')}</div>` : ''}${s.conclusion ? `<p class="tiny muted">Conclusion: ${esc(s.conclusion)}</p>` : ''}</div>`).join('')}
        ${j.readers.length ? `<p class="muted tiny">${fmt(j.readers.length)} parallel readers kept ${fmt(j.readers.reduce((a, x) => a + (x.kept || 0), 0))} checked facts; ${fmt(j.facts.length)} went on the fact sheet.</p>` : ''}
        ${j.facts.length ? `<details><summary class="tiny">Fact sheet (${fmt(j.facts.length)} facts)</summary><ul class="tiny">${j.facts.map((f) => `<li>${esc(f)}</li>`).join('')}</ul></details>` : ''}
        <h4>Bonsai (judge)</h4>
        ${(b.lookups || []).map((s) => `<div class="step"><b>Lookup ${esc(s.round)}.</b> ${esc(s.thought || s.error || '')}${s.done ? ' <span class="muted">(enough)</span>' : ''}${(s.calls || []).length ? `<div class="tools">${s.calls.map((x) => `→ ${esc(x.tool)} ${esc(JSON.stringify(x.args).slice(0, 180))}${x.ok ? '' : ` <span class="err">(${esc(x.error || 'failed')})</span>`}`).join('<br>')}</div>` : ''}</div>`).join('')}
        ${b.double_check ? `<h4>Bonsai's double-check of its draft ${b.double_check.changed?.length ? pill(`changed: ${b.double_check.changed.join(', ')}`, 'warn') : pill('draft confirmed', 'ok')}</h4>
          ${(b.double_check.steps || []).map((s) => `<div class="step"><b>Check ${esc(s.round)}.</b> ${esc(s.thought || s.error || '')}${s.done ? ' <span class="muted">(checked)</span>' : ''}${(s.calls || []).length ? `<div class="tools">${s.calls.map((x) => `→ ${esc(x.tool)} ${esc(JSON.stringify(x.args).slice(0, 180))}${x.ok ? '' : ` <span class="err">(${esc(x.error || 'failed')})</span>`}`).join('<br>')}</div>` : ''}</div>`).join('')}
          <p class="tiny muted">Draft: ${Object.entries(b.double_check.draft || {}).map(([h, l]) => `${esc(h)} ${esc((LABEL[l] || [l])[0])}`).join(' · ')} · ${fmt(b.double_check.s)} s</p>` : ''}
        ${b.bull_case || b.bear_case ? `<div class="cases"><div><b>Bull case</b><p class="tiny">${esc(b.bull_case || '–')}</p></div><div><b>Bear case</b><p class="tiny">${esc(b.bear_case || '–')}</p></div></div>` : ''}
        ${b.primary ? `<p class="tiny">Primary horizon: <b>${esc(HOR[b.primary] || b.primary)}</b></p>` : ''}
        ${calls.map(([h, x]) => call(h, x)).join('')}
        <h4>Websites visited (${fmt(c.sites.length)})</h4>
        ${c.sites.map((x) => `<div class="site">${x.used ? pill('used', 'ok') : x.skipped ? pill('skipped: robots.txt') : x.ok ? pill('opened') : pill('blocked', 'bad')} ${esc(x.by)} · <a href="${esc(x.url)}" target="_blank" rel="noreferrer">${esc(x.url)}</a>${x.note ? ` <span class="muted">${esc(x.note)}</span>` : ''}</div>`).join('')}
      </details>`;
    };
    return `<h2>Research log</h2><p class="lede">The 150 technology companies: how far the run is, and what both AIs thought and read.</p>
      ${plain('Jan searches the news and the company’s own SEC filings, and parallel readers pull checked facts from every page. Bonsai then looks up what is still missing, argues the bull case and the bear case, and calls each horizon bull or bear. Quotes are checked word for word by code. Research only: nothing here places orders.')}
      <div class="card wide">${h3('Progress', 'Research progress')}${bar}</div>
      <div class="card wide rlog">${h3('Reasoning and sources, newest first', 'Research log')}${r.companies.length ? r.companies.map(one).join('') : '<p class="empty">No research yet.</p>'}</div>`;
  },

  async records() {
    const r = await api('records');
    const none = (what) => `<p class="empty">${esc(what)}</p>`;
    const money = (v) => fmt(v);
    const dur = (x) => (x == null ? '–' : x < 90 ? `${fmt(x)} s` : x < 5400 ? `${fmt(x / 60)} min` : `${fmt(x / 3600, 1)} h`);
    const STAGE = { eligible: 'Published', discovered: 'Found', downloaded: 'Downloaded', extracted: 'Read', scored_on_time: 'Scored before its open', evaluated: 'Result known',
      selected: 'Picked', submitted: 'Order sent', filled: 'Filled', closed: 'Closed' };
    const chain = (st) => Object.entries(st || {}).map(([k, v]) => `<div class="kpi"><div class="label">${esc(STAGE[k] || k)}</div><div class="stat">${fmt(v)}</div></div>`).join('<span class="muted" style="align-self:center">→</span>');
    const why = (rows, head) => (rows?.length ? `<table><tr><th>${esc(head)}</th><th>Why</th><th class="num">Reports</th></tr>${rows.map((x) => `<tr><td>${esc(STAGE[x.after] || x.after)}</td><td>${esc(x.why)}</td><td class="num">${fmt(x.releases)}</td></tr>`).join('')}</table>` : '');
    const f = r.funnel, c = r.contribution, t = r.throughput, a = r.account, b = r.benchmark;
    const funnel = !f ? none('Written by the next weekly run.') : `<p class="muted">Every earnings report since the live test began, and how far each one got. A report that drops out is listed with the reason, so the results cannot quietly describe only the easy ones. As of ${esc(when(f.at))}.</p>
      <div class="kpis">${chain(f.stages)}</div>${why(f.stopped, 'Stopped after')}${why(f.waiting, 'Waiting after')}
      <h4>Of the scored reports, the AI-picks trades</h4><div class="kpis">${chain(f.trade_stages)}</div>${why(f.trade_stopped, 'Stopped after')}${why(f.trade_waiting, 'Waiting after')}`;
    const row = (name, x) => `<tr><td>${esc(name)}</td><td class="num">${pct(x.net_return, 2)}</td><td class="num">${fmt(x.pairs)}</td><td class="num">${fmt(x.turnover, 2)}×</td><td class="num">${fmt(x.mean_gross_exposure, 2)}×</td></tr>`;
    const contrib = !c ? none('Written by the next weekly run.') : c.replay_matches_live_book === false ? `<div class="banner warn">Legacy AI contribution comparison blocked: the replay does not match the live simulator. Update engineering evidence for reconciliation details.</div><pre class="operation-log">${esc(JSON.stringify({reconciliation: c.reconciliation, prospective: c.prospective_replay}, null, 2))}</pre>` : `<p class="muted">The same small book traded three ways with identical timing, sizes and costs: with the AI's real scores, with the same scores dealt out at random, and buying every report. These are simulated counterfactuals. A mismatch blocks any claim about live AI contribution. ${esc(c.note || '')}</p>
      <table><tr><th>Version</th><th class="num">Return after costs</th><th class="num">Trades</th><th class="num">Turnover</th><th class="num">Average size</th></tr>
        ${row("AI's picks", c.ai)}${row('Every report', c.every_release)}
        <tr><td>Scores dealt at random (${fmt(c.shuffled.deals)} deals)</td><td class="num">${pct(c.shuffled.mean_net_return, 2)} <span class="muted">(${pct(c.shuffled.p05, 2)} to ${pct(c.shuffled.p95, 2)})</span></td><td class="num">${fmt(c.shuffled.mean_pairs, 1)}</td><td></td><td></td></tr></table>
      <div class="kpis">${kpi('AI minus random', `${fmt(100 * c.ai_minus_shuffled, 2)} pts`, 'return after costs')}${kpi('Random deals the AI beats', pct(c.share_of_deals_ai_beats, 0), '50% would be chance')}${kpi('Replay matches the live book', c.replay_matches_live_book ? 'yes' : 'NO')}</div>`;
    const pd = t?.per_decision_seconds, sl = t?.slack_before_open_seconds;
    const through = !t ? none('Written by the next weekly run.') : `<p class="muted">How long a report waits for a run, how long the run takes to decide it, and how much time is left before the market opens. A decision with under 10 minutes to spare raises an alert.</p>
      <div class="kpis">${kpi('Time left before the open', dur(sl?.median), `least ${dur(sl?.least)} · ${fmt(sl?.n)} decisions`)}${kpi('Work per decision', dur(pd?.work?.median), `worst ${dur(pd?.work?.worst)}`)}${kpi('Wait for a run to start', dur(pd?.queue_wait?.median), `worst ${dur(pd?.queue_wait?.worst)}`)}${kpi('Too late to trade', fmt(t.missed_because_late))}</div>
      ${Object.keys(t.stage_seconds || {}).length ? `<table><tr><th>Stage</th><th class="num">Usual</th><th class="num">Worst</th><th class="num">Runs</th></tr>${Object.entries(t.stage_seconds).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${dur(v.median)}</td><td class="num">${dur(v.worst)}</td><td class="num">${fmt(v.n)}</td></tr>`).join('')}</table>` : '<p class="tiny muted">Stage-by-stage times appear after the next live run (they are recorded since 1 Oct).</p>'}
      <p class="tiny muted">The waits of the seven decisions of 30 Sep and 1 Oct read 4 hours too long: their stored filing time was 4 hours early (fixed since).</p>
      ${(t.tight || []).length ? `<p class="err">${fmt(t.tight.length)} decision(s) had under 10 minutes to spare.</p>` : ''}`;
    const account = !a ? none('Written by the next earnings run.') : `<p class="muted">The paper account at the broker as a whole. The mirrored book and the AI picks are kept in separate files but share one account, one borrowing limit and some hedges. Any position neither book can explain raises an alert. As of ${esc(when(a.at))}.</p>
      <div class="kpis">${kpi('Account value', money(a.equity))}${kpi('Total invested', `${fmt(a.leverage, 2)}×`, `${money(a.gross)} long plus short`)}${kpi('Net market exposure', `${fmt(a.net_leverage, 3)}×`, money(a.net))}${kpi('Buying power left', money(a.buying_power))}${kpi('AI picks share', pct(a.sleeve?.gross_share, 1), `limit ${pct(a.sleeve?.target_share, 0)} · ${fmt(a.sleeve?.open_pairs)} open`)}</div>
      ${(a.alerts || []).length ? `<p class="err">${a.alerts.map(esc).join('<br>')}</p>` : '<p class="tiny muted">Every position is explained by one of the two books.</p>'}
      ${Object.keys(a.shared_hedges || {}).length ? `<p class="tiny muted">Hedges shared by more than one pick: ${Object.entries(a.shared_hedges).map(([k, v]) => `${esc(k)} ${fmt(v.qty)} for ${esc((v.pairs || []).join(', '))}`).join('; ')}</p>` : ''}
      <table><tr><th>Asset</th><th class="num">At the broker</th><th class="num">Mirrored book</th><th class="num">AI picks</th><th class="num">Unexplained</th><th class="num">Value</th></tr>
        ${(a.positions || []).map((x) => `<tr><td>${esc(x.asset)}</td><td class="num">${fmt(x.broker_qty, 2)}</td><td class="num">${fmt(x.mirror_qty, 2)}</td><td class="num">${fmt(x.sleeve_qty, 2)}</td><td class="num">${x.unexplained_qty ? `<b>${fmt(x.unexplained_qty, 2)}</b>` : '0'}</td><td class="num">${money(x.market_value)}</td></tr>`).join('')}</table>`;
    const evidence = !r.evidence.length ? none('Written by the next earnings run.') : `<p class="muted">One file per decision with what is needed to rebuild it: the press release's fingerprint and download time, what the reader took from it, the exact fact sheet the judge saw, both models' versions, the code version and the entry day. Checked weekly against the files on disk.</p>
      <table><tr><th>Stock</th><th>Entry day</th><th class="num">Score</th><th>Fact sheet</th><th>Code</th><th>Written</th></tr>
        ${r.evidence.map((x) => `<tr><td>${esc(x.ticker || x.accession)}</td><td>${esc(x.session || '–')}</td><td class="num">${x.logodds == null ? '–' : fmt(x.logodds, 2)}</td><td>${x.has_sheet ? `version ${esc(x.sheet_version ?? 1)}` : '–'}</td><td>${esc(x.commit || '–')}</td><td>${esc(when(x.at))}${x.backfilled ? ' <span class="muted">(added later)</span>' : ''}</td></tr>`).join('')}</table>`;
    const bench = !b ? none('Not scored yet.') : `<p class="muted">Twelve hard earnings reports (banks, odd fiscal years, losses, changed guidance, dense tables) with ${fmt(b.fields)} figures labelled by hand from the text. It scores the reader on the right period, units and accounting basis, and on saying "not stated" when a figure is absent. The labels were made by the AI assistant and still need a person's check.</p>
      <div class="kpis">${kpi('Figures right', `${fmt(b.right)} of ${fmt(b.fields)}`, pct(b.right / b.fields, 0))}${kpi('Stated but not found', fmt(b.outcomes?.missed ?? 0))}${kpi('From the wrong period', fmt(b.outcomes?.wrong_period ?? 0))}${kpi('Made up', fmt(b.outcomes?.invented ?? 0))}</div>
      <table><tr><th>Report</th><th>What the reader got wrong</th></tr>${b.rows.map((x) => `<tr><td>${esc(x.ticker)}</td><td>${x.wrong.length ? esc(x.wrong.join(', ')) : '<span class="muted">all right</span>'}</td></tr>`).join('')}</table>`;
    return `<h2>Records</h2><p class="lede">Checks kept beside the books: nothing here trades or decides anything.</p>
      ${plain('Good returns can hide problems: reports that were never seen, an AI that adds nothing, decisions made too close to the open, positions nobody can explain. Each card below is one record that would show such a problem.')}
      <div class="card wide">${h3('Where every report ended up', 'Funnel')}${funnel}</div>
      <div class="card wide">${h3("What the AI's choice adds", 'AI contribution')}${contrib}</div>
      <div class="card wide">${h3('How fast decisions are made', 'Throughput')}${through}</div>
      <div class="card wide">${h3('The whole paper account', 'Account view')}${account}</div>
      <div class="card wide">${h3('Evidence kept for each decision', 'Evidence')}${evidence}</div>
      <div class="card wide">${h3('How well the reader reads', 'Extraction benchmark')}${bench}</div>`;
  },

  async tests() {
    const t = await api('tests');
    const pass = t.filter((x) => String(x.result).startsWith('pass')).length;
    return `<h2>Tests</h2><p class="lede">The full record of every experiment. ${pass} passed of ${t.length}.</p>
      ${plain('Every idea is written down before it is tested and then run exactly once, so results cannot be cherry-picked. Most ideas fail; that is normal and it is why only tested rules trade.')}
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
// Plain-language meanings, shown as a "?" beside the term and listed on the How it works page.
const GLOSS = {
  'CAGR': 'Average growth per year. 17% means 100 became about 117 after a typical year.',
  'Sharpe': 'Return compared with how bumpy the ride was. Under 0.5 is weak, around 1 is good, 2 is rare.',
  'Volatility': 'How much the value swings in a typical year. Higher means a rougher ride.',
  'Max drawdown': 'The worst fall from a high point to the next low. -27% means it once dropped by about a quarter.',
  'Drawdown': 'How far the value is below its earlier high at each moment. 0% means it is at a new high.',
  'Rolling 1-year Sharpe': 'The Sharpe score measured over the previous 12 months, so you can see good and bad stretches.',
  'Past-year trend': 'How the theme did against the market over the past year, leaving out the latest month (12-1 momentum).',
  'Long-term score': 'The rating from 1 to 5, weighted by how sure the AI was of each digit when it wrote it. It breaks ties between equal ratings.',
  'Log-odds': 'The AI judge\'s confidence. 0 is a coin flip; higher means it is more sure the stock will beat its sector.',
  'Pick threshold': 'The score a release needs before the system places a paper trade on it. Most releases stay below it.',
  '95% interval': 'The range the true result probably sits in. If the range crosses 0, the idea may simply not work.',
  'Backtest': 'Running the rules on past data to see what would have happened. It is evidence, not a promise.',
  'Paper trading': 'Practice trading with pretend money at a real broker. No real money can be lost.',
  'Sector': 'The stock\'s industry group (for example technology). A pick is judged against its group, not the whole market.',
  'SPY': 'A fund that tracks the 500 largest US companies. It is the "just buy the market" yardstick.',
  'Sortino': 'Like the Sharpe score, but it only counts the bad swings as risk. Higher is better.',
  'Calmar': 'Yearly growth divided by the worst fall. Above 0.5 is decent; it says how much pain bought the gain.',
  'VaR 95%': 'On a bad day (the worst 1 day in 20) the loss is at least this big.',
  'CVaR 95%': 'The average loss on those worst 1-in-20 days. It shows how bad "bad" usually is.',
  'Beta': 'How much it moves when the market moves. 1 means in step with the market; 0.7 means about 70% as much.',
  'Alpha': 'Yearly return left over after removing what simply came from moving with the market.',
  'Correlation': 'How closely two things move together. 1 = always together, 0 = unrelated, -1 = opposite.',
  'Up capture': 'Share of the market\'s gains it kept in months the market rose. 0.96 = it kept 96%.',
  'Down capture': 'Share of the market\'s losses it took in months the market fell. Lower is better.',
  'Rank correlation (IC)': 'Whether higher AI scores went with better results. 0 = no link, 0.05 is a weak but real link, 0.1 is strong for stocks.',
  'Hit rate': 'How often the pick beat its industry group. 50% is a coin flip.',
  'Risk contribution': 'How much of the portfolio\'s total swings each holding causes. A small holding can cause a big share if it is jumpy.',
  'Diversification ratio': 'Above 1 means the holdings partly cancel each other\'s swings. 1 means no benefit from mixing.',
  'Independent bets': 'How many truly separate risks the portfolio holds. 3 holdings that move together count as fewer than 3.',
  'Chance it is real (PSR)': 'The probability the true Sharpe score is above zero, given how long and how bumpy the record is.',
  'Bootstrap cone': 'We reshuffle real past months 2,000 times to see the range of years that history could have produced. A what-if, not a forecast.',
  'Drawdown brake': 'A safety rule: after a 10% fall the book cuts its positions to two-thirds, after 20% to half, until it recovers.',
  'Leverage': 'Trading with borrowed money. 2x doubles every gain and every loss, and you pay interest on the borrowed half.',
  'Pre-registered': 'The test rules were written down and published before the test ran, so the result cannot be bent afterwards.',
};
const help = (term, text = GLOSS[term]) => (text ? `<span class="help" tabindex="0" data-tip="${esc(text)}">?</span>` : '');
const KPI_TERM = { 'Growth per year': 'CAGR', 'Quality score (Sharpe)': 'Sharpe', 'Worst fall': 'Max drawdown', 'Bumpiness (volatility)': 'Volatility', 'Max DD': 'Max drawdown', 'Sharpe (net)': 'Sharpe', 'SPY Sharpe': 'Sharpe', 'Above threshold': 'Pick threshold' };
const kpi = (label, value, sub = '') => `<div class="kpi"><div class="label">${esc(label)}${help(KPI_TERM[label] || label)}</div><div class="stat">${value}</div><div class="sub">${sub}</div></div>`;
const plain = (html) => `<div class="plain"><span class="plain-tag">In plain words</span><p>${html}</p></div>`;
const h3 = (title, term) => `<h3>${esc(title)}${help(term || title)}</h3>`;
const statsRow = (s) => kpi('Growth per year', pct(s.cagr)) + kpi('Quality score (Sharpe)', esc(s.sharpe)) + kpi('Bumpiness (volatility)', pct(s.vol)) + kpi('Worst fall', pct(s.max_dd)) + kpi('Period', `<span style="font-size:13px">${esc(s.start?.slice(0, 7))} → ${esc(s.end?.slice(0, 7))}</span>`);
const noMetrics = '<p class="empty">Chart data not built yet — press “Rebuild charts”.</p>';

function tearSheet(a, name, bench) {
  const t = a.tear; if (!t) return '';
  const s = t.stats, row = (label, value, term) => `<div><span>${esc(label)}${help(term || label)}</span><b>${value}</b></div>`;
  const dd = t.drawdowns.map((d) => `<tr><td>${esc(d.start)}</td><td>${esc(d.trough)}</td><td>${d.recovered ? esc(d.recovered) : pill('still under', 'warn')}</td><td class="num">${pct(d.depth)}</td><td class="num">${fmt(d.days)}</td></tr>`).join('');
  const c = t.cone, h = t.month_hist, mid = h.edges.slice(0, -1).map((e, i) => (i % 2 ? '' : `${(((e + h.edges[i + 1]) / 2) * 100).toFixed(0)}%`));
  const roll = [{ name: `${name} 6-month volatility`, x: t.rolling.dates, y: t.rolling.vol }];
  return `<div class="card wide"><h3>Full scorecard</h3><div class="statgrid">
      ${row('Sortino', esc(s.sortino))}${row('Calmar', esc(s.calmar))}${row('Chance it is real (PSR)', pct(s.psr, 1))}
      ${row('VaR 95%', pct(s.var95, 2))}${row('CVaR 95%', pct(s.cvar95, 2))}${row('Best day', pct(s.best_day), 'x')}
      ${row('Worst day', pct(s.worst_day), 'x')}${row('Best month', pct(s.best_month), 'x')}${row('Worst month', pct(s.worst_month), 'x')}
      ${row('Days that went up', pct(s.pos_days, 0), 'x')}${row('Months that went up', pct(s.pos_months, 0), 'x')}${row('Average up month', pct(s.avg_up_month), 'x')}
      ${row('Average down month', pct(s.avg_down_month), 'x')}${row('Longest time below a high', `${fmt(s.longest_underwater_days)} trading days`, 'x')}
      ${s.beta != null ? row('Beta', esc(s.beta)) + row('Alpha', pct(s.alpha)) + row('Correlation', esc(s.corr)) + row('Up capture', esc(s.up_capture)) + row('Down capture', esc(s.down_capture)) : ''}
    </div></div>
    <div class="card wide"><h3>Every month's return (%)</h3><p class="muted">Blue months gained, orange months lost; stronger colour means a bigger move. The last column is the whole year.</p>${heatmap(t.monthly.years, t.monthly.cells)}</div>
    <div class="split">
      <div class="card">${h3('The five worst falls', 'Max drawdown')}<table><tr><th>From high</th><th>Lowest point</th><th>Back to high</th><th class="num">Fall</th><th class="num">Days</th></tr>${dd}</table></div>
      <div class="card"><h3>How months were spread</h3><p class="muted">Each bar counts months with a return in that range.</p>${barChart(mid, [{ name: 'months', y: h.counts }], { width: 560, height: 230, yFmt: (v) => v.toFixed(0) })}</div>
    </div>
    <div class="split">
      <div class="card">${h3('Bumpiness over time (6-month volatility)', 'Volatility')}${lineChart(roll, { width: 560, height: 240, yFmt: (v) => pct(v, 0) })}</div>
      ${t.rolling.beta ? `<div class="card">${h3('How tied to the market (6-month beta)', 'Beta')}${lineChart([{ name: `${name} beta to ${bench}`, x: t.rolling.dates, y: t.rolling.beta }], { width: 560, height: 240, yFmt: (v) => v.toFixed(1), zero: true })}</div>` : ''}
    </div>
    <div class="card wide">${h3('What could the next 12 months look like?', 'Bootstrap cone')}
      <div class="kpis">${kpi('Middle outcome', `${c.p50.at(-1).toFixed(0)}`, '100 today')}${kpi('Chance of ending lower', pct(c.prob_loss, 0), 'after 12 months')}${kpi('Chance of a 20%+ fall', pct(c.prob_dd20, 0), 'at some point in the year')}${kpi('Typical worst dip', pct(c.median_dd, 0), 'during the year')}</div>
      ${fanChart(c)}<p class="muted">Dark band: the middle half of ${fmt(c.paths)} reshuffled histories. Light band: all but the best and worst 5%. Built only from this book's own past, so it cannot see events that never happened in it.</p></div>`;
}

let stratTab = 'core';

Object.assign(views, {
  async how() {
    const [d, m] = await Promise.all([api('decisions').catch(() => []), metrics().catch(() => ({ missing: true }))]);
    const thr = m?.live?.ai_threshold;
    const ex = d.filter((r) => r.type === 'decision' && r.logodds != null).sort((a, b) => b.logodds - a.logodds)[0];
    const step = (n, who, kind, title, text) => `<div class="step"><div class="step-n">${n}</div><div class="step-who">${esc(who)} ${pill(kind, kind === 'AI' ? 'lime' : kind === 'in testing' ? 'warn' : '')}</div><h3>${esc(title)}</h3><p>${text}</p></div>`;
    return `<h2>How it works</h2><p class="lede">The whole system on one page, in plain words.</p>
      ${plain('airp is a <b>practice</b> trading system. It has two parts: a simple rule-based book that holds most of the pretend money, and an AI team that reads company earnings reports and makes small practice bets. A master judge AI makes the call, and plain maths decides whether its opinion is strong enough to act on.')}
      <div class="card wide"><h3>The AI team: from a news release to a practice trade</h3>
        <div class="pipeline">
          ${step(1, 'Watcher', 'code', 'Spots new earnings reports', 'Twice every weekday it checks the official SEC feed for S&amp;P 500 companies that just reported.')}
          ${step(2, 'Reader agent', 'AI', 'Pulls out the facts', 'A small AI reads the report for sales, profit, outlook and tone. Code then checks every number is quoted word-for-word, so it cannot make things up.')}
          ${step(3, 'Fact tools', 'algorithm', 'Adds hard numbers', 'Formulas add the company\'s filing history and how the stock has moved against its industry group.')}
          ${step(4, 'Research agents', 'in testing', 'Gather news and opinions', 'Extra agents (a news researcher, a bull-versus-bear debate, a summary reader) form views. They are scored in the background and only count once they prove useful. A <b>master algorithm</b> (new, also in testing) keeps each agent\'s track record and combines their votes, giving more say to the ones that have been right.')}
          ${step(5, 'Master judge', 'AI', 'Says BUY or PASS', 'The large AI (Bonsai) reads the fact sheet and answers one word. Maths reads how sure it was and turns that into a confidence score.')}
          ${step(6, 'Decision rule', 'algorithm', 'Acts only on strong scores', `A score above ${esc(thr ?? 'the threshold')} becomes a practice trade: buy the stock, and bet against its industry fund so only the company's own news matters.`)}
          ${step(7, 'Scorekeeper', 'code', 'Checks who was right', 'A week later it compares the stock with its industry group and records the result. Nothing can be edited afterwards.')}
        </div>
        ${ex ? `<p class="muted">Latest example: <b>${esc(ex.ticker)}</b> scored <b>${fmt(ex.logodds, 2)}</b>${thr != null ? (ex.logodds >= thr ? `, above the ${esc(thr)} threshold, so it became a practice trade.` : `, below the ${esc(thr)} threshold, so no trade.`) : '.'}</p>` : ''}
      </div>
      <div class="split">
        <div class="card"><h3>What it predicts (and what it does not)</h3>
          <p>It does <b>not</b> predict a share price. It predicts one thing: <b>will this stock do better than its industry group over the next week?</b> That is an easier and more honest question.</p>
          <p class="muted">So far the AI picks have not proven themselves on past data. That is why they trade a small pretend sleeve and why the research agents are still being tested.</p></div>
        <div class="card"><h3>The aggressive book (your choice)</h3>
          <p>From Mon 5 Oct a second paper book holds <b>2.5 times</b> the core book's positions using borrowed pretend money, because you set the baseline at 40% a year. Borrowing costs it 5% a year.</p>
          <p class="muted">If it ever falls 60% from its peak, only this book stops buying; the frozen book is never affected by it.</p>
          <p class="muted">On past data that is about 35% a year with falls of up to 57%. It is a bet on the core book's rules, not a tested strategy, and the frozen 1x book keeps running beside it as the honest comparison.</p></div>
        <div class="card"><h3>The core book (no AI)</h3>
          <p>Most of the pretend money follows simple rules: hold the US market (SPY), add a little Bitcoin and Ether when they are rising, and cut back automatically in a big fall. Spare cash sits in short-term government bills.</p>
          <p class="muted">${m.missing ? '' : `On past data since 2018 it grew ${pct(m.tracks.core.stats.cagr)} a year against ${pct(m.tracks.SPY.stats.cagr)} for the market, and its worst fall was ${pct(m.tracks.core.stats.max_dd)} against ${pct(m.tracks.SPY.stats.max_dd)}.`}</p></div>
      </div>
      <div class="card wide"><h3>Safety rules</h3><div class="rules">
        <div><b>Pretend money only.</b> No real trades, ever, unless you change that yourself.</div>
        <div><b>Rules are frozen.</b> The live rules cannot be tweaked after seeing results.</div>
        <div><b>Ideas are tested once.</b> Written down first, run once, failures are kept on record.</div>
        <div><b>Kill switch.</b> The red button (bottom left) stops all trading at once.</div>
      </div></div>
      <div class="card wide"><h3>Words used in this app</h3><dl class="gloss">${Object.entries(GLOSS).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl></div>`;
  },
  async strategies() {
    const m = await metrics(); if (m.missing) return `<h2>Strategies</h2>${noMetrics}`;
    const t = m.tracks, tabs = [['core', 'Core book (long-term)'], ['event', 'AI events (short-term)'], ['longterm', 'Long-term picks & themes']];
    const head = `<h2>Strategies</h2><p class="lede">How each rule set would have done on past data (2018 to today).</p>
      <div class="tabs">${tabs.map(([k, n]) => `<button class="btn ${k === stratTab ? 'on' : 'ghost'}" data-strat="${k}">${esc(n)}</button>`).join('')}</div>`;
    if (stratTab === 'longterm') return `${head}${longtermTab(await api('longterm'))}`;
    const a = t[stratTab], spy = t.SPY, years = Object.keys(a.yearly);
    const about = stratTab === 'core'
      ? 'The <b>core book</b> is where the money is. It holds the US stock market (SPY) plus a little Bitcoin and Ether, and automatically cuts back when prices are falling. No AI is involved; it is simple, tested rules. Blue is the core book, orange is just holding the market.'
      : 'The <b>AI events book</b> buys stocks the AI judge likes right after earnings and holds them for a week. On past data it did <b>not</b> beat doing nothing, so it only trades a small pretend sleeve while we look for a version that works.';
    const bench = stratTab === 'core';
    const eqSeries = [{ name: stratTab === 'core' ? 'Core book' : 'AI event book', x: a.dates, y: a.equity }];
    if (bench) eqSeries.push({ name: 'SPY', x: spy.dates, y: spy.equity });
    return `${head}${plain(about)}
      <div class="kpis">${statsRow(a.stats)}${bench ? kpi('SPY Sharpe', esc(spy.stats.sharpe), `CAGR ${pct(spy.stats.cagr)} · max DD ${pct(spy.stats.max_dd)}`) : ''}</div>
      <div class="card wide">${h3('Growth of 100', 'Backtest')}${lineChart(eqSeries, { yFmt: (v) => v.toFixed(0) })}</div>
      <div class="split">
        <div class="card">${h3('Drawdown')}${lineChart(bench ? [{ name: eqSeries[0].name, x: a.dates, y: a.drawdown }, { name: 'SPY', x: spy.dates, y: spy.drawdown }] : [{ name: eqSeries[0].name, x: a.dates, y: a.drawdown }], { yFmt: (v) => pct(v, 0), zero: true, width: 560, height: 260 })}</div>
        <div class="card">${h3('Rolling 1-year Sharpe')}${lineChart(bench ? [{ name: eqSeries[0].name, x: a.dates, y: a.rolling_sharpe }, { name: 'SPY', x: spy.dates, y: spy.rolling_sharpe }] : [{ name: eqSeries[0].name, x: a.dates, y: a.rolling_sharpe }], { yFmt: (v) => v.toFixed(1), zero: true, width: 560, height: 260 })}</div>
      </div>
      <div class="card wide"><h3>Return by year</h3>${barChart(years, bench ? [{ name: eqSeries[0].name, y: years.map((y) => a.yearly[y]) }, { name: 'SPY', y: years.map((y) => spy.yearly[y] ?? null) }] : [{ name: eqSeries[0].name, y: years.map((y) => a.yearly[y]) }])}</div>
      ${tearSheet(a, eqSeries[0].name, 'SPY')}`;
  },

  async lab() {
    const [m, watch] = await Promise.all([metrics(), api('month_end').catch(() => null)]); if (m.missing) return `<h2>Strategy lab</h2>${noMetrics}`;
    const tests = m.lab.tests;
    const order = ['day trading', 'calendar', 'flows', 'pairs', 'crypto carry'];
    const rows = tests.slice().sort((a, b) => order.indexOf(a.group) - order.indexOf(b.group) || (b.sharpe ?? -9) - (a.sharpe ?? -9))
      .map((x) => ({ label: `${x.id} · ${x.name}`, group: x.group, v: x.sharpe, lo: x.ci?.[0], hi: x.ci?.[1], pass: x.pass }));
    const c = m.lab.curves, names = { D9: 'D9 opening-auction reversal', T1: 'T1 turn of the month', O1: 'O1 SPY overnight', E1: 'E1 macro announcements',
      R1: 'R1 rebalancing pressure', M1: 'M1 month-end Treasuries', A1: 'A1 Treasury auction cycle' };
    const curveCards = ['D9', 'T1', 'O1', 'E1', 'R1', 'M1', 'A1'].filter((k) => c[k]).map((k) => `<div class="card"><h3>${esc(names[k])}</h3>
      <div class="kpis">${kpi('Sharpe (net)', esc(c[k].stats.sharpe))}${kpi('CAGR', pct(c[k].stats.cagr))}${kpi('Max DD', pct(c[k].stats.max_dd))}</div>
      ${lineChart([{ name: names[k], x: c[k].dates, y: c[k].equity }], { width: 560, height: 220, yFmt: (v) => v.toFixed(0) })}</div>`).join('');
    const passed = tests.filter((x) => x.pass).length;
    return `<h2>Strategy lab</h2><p class="lede">Trading ideas we tested, and whether they held up. ${passed} of ${tests.length} passed.</p>
      ${plain('Each row is one idea (day trading, calendar effects, money flows of big funds, pairs, crypto). The dot is its score after costs and the line is how uncertain that score is. To pass, the dot must be right of the dashed line <b>and</b> the whole line must be right of zero. Grey rows failed, so they do not trade.')}
      <div class="card wide">${h3('Score after costs, with its uncertainty range', '95% interval')}${ciChart(rows, { refs: [{ v: 0.5, label: 'pass line 0.5' }] })}</div>
      ${monthEndCard(watch)}
      <div class="card wide"><h3>The detail behind each test</h3><p class="muted">"Score by trading cost" shows the same idea at cheap and expensive trading: an idea that only works when trading is free is not real. "Before it was published" is the score on the years before the idea became widely known.</p>
        <table><tr><th>Test</th><th>Tested on</th><th class="num">Days</th><th class="num">Score by trading cost</th><th class="num">Before it was published</th><th class="num">Up days</th><th class="num">Worst month</th><th class="num">Moves with core book${help('Correlation')}</th><th>Verdict</th></tr>
        ${tests.map((x) => `<tr><td>${esc(x.id)} · ${esc(x.name)}</td><td>${esc((x.window || []).map((d) => String(d).slice(0, 7)).join(' → '))}</td><td class="num">${fmt(x.days)}</td>
          <td class="num">${Object.entries(x.costs || {}).map(([c, v]) => `${esc(c)}: ${v == null ? '–' : fmt(v, 2)}`).join(' · ') || fmt(x.sharpe, 2)}</td><td class="num">${x.before_sharpe == null ? '–' : fmt(x.before_sharpe, 2)}</td>
          <td class="num">${x.hit_rate == null ? '–' : pct(x.hit_rate, 0)}</td><td class="num">${x.worst_month == null ? '–' : pct(x.worst_month)}</td><td class="num">${x.corr_core == null ? '–' : fmt(x.corr_core, 2)}</td><td>${x.pass ? pill('passed', 'ok') : pill('failed', 'bad')}</td></tr>`).join('')}</table></div>
      <div class="split">${curveCards}</div>
      <p class="muted">D9 passed but trades the official open auction, which is not tradable in practice; its two tradable versions (D10, D11) failed.</p>`;
  },


  async risk() {
    const m = await metrics(); if (m.missing || !m.portfolio || m.portfolio.error) return `<h2>Portfolio &amp; risk</h2>${m.portfolio?.error ? `<p class="err">${esc(m.portfolio.error)}</p>` : noMetrics}`;
    const f = m.portfolio, names = { SPY: 'US stocks (SPY)', 'BTC-USD': 'Bitcoin', 'ETH-USD': 'Ether', Cash: 'Cash / T-bills' };
    const colors = [PALETTE[0], PALETTE[1], PALETTE[2], '#55554c'], order = ['SPY', 'BTC-USD', 'ETH-USD', 'Cash'];
    return `<h2>Portfolio &amp; risk</h2><p class="lede">What the core book holds, and where its risk really comes from.</p>
      ${plain('Money and risk are not the same thing. Crypto is a small slice of the money but a big slice of the swings, because it jumps around far more than stocks. This page shows both views, how the mix changed over time, and how the safety brake stepped in.')}
      <div class="kpis">${kpi('Bumpiness (volatility)', pct(f.port_vol), 'expected swings per year')}${kpi('Diversification ratio', esc(f.div_ratio))}${kpi('Independent bets', esc(f.eff_bets), `out of ${f.assets.length} holdings`)}
        ${kpi('Drawdown brake', f.brake >= 1 ? 'off' : `${pct(f.brake, 0)} size`, `on for ${pct(f.brake_days, 0)} of days since 2018`)}${kpi('Trades since 2018', fmt(f.trades), `costs paid: ${pct(f.cost_paid_pct, 1)} of starting money`)}</div>
      <div class="split">
        <div class="card"><h3>Where the money is today</h3>${shareBars(order.map((a) => ({ label: names[a], v: f.now[a] ?? 0 })), colors)}</div>
        <div class="card">${h3('Where the risk comes from today', 'Risk contribution')}${shareBars(f.assets.map((a) => ({ label: names[a], v: f.risk_contrib[a], note: `swings ${pct(f.asset_vol[a], 0)} a year on its own` })), colors)}</div>
      </div>
      ${m.tracks.core.leverage ? `<div class="card wide">${h3('What would more risk do? (borrowing to trade bigger)', 'Leverage')}
        <p class="muted">The core book's own history replayed with borrowed money, paying T-bill + 1% a year on the borrowed part. Your baseline is ${settings.target}% a year and ${settings.stretch}% is the stretch. This is arithmetic on a backtest, not a tested strategy, and live results are usually worse.</p>
        <table><tr><th>Size</th><th class="num">Growth per year</th><th class="num">Bumpiness</th><th class="num">Worst fall</th><th class="num">Worst year</th><th class="num">Best year</th><th class="num">Chance of a ${settings.target}% year</th><th class="num">Chance of a ${settings.stretch}% year</th><th class="num">Chance of a losing year</th><th class="num">Chance of a 35% fall in a year</th></tr>
        ${m.tracks.core.leverage.map((r) => `<tr><td>${r.lev === 1 ? `1.0x ${pill('frozen book')}` : r.lev === 2.5 ? `2.5x ${pill('your aggressive book', 'lime')}` : `${r.lev.toFixed(1)}x`}</td><td class="num">${pct(r.cagr)}</td><td class="num">${pct(r.vol, 0)}</td><td class="num">${pct(r.max_dd, 0)}</td><td class="num">${pct(r.worst_year, 0)}</td><td class="num">${pct(r.best_year, 0)}</td><td class="num">${pct(r.prob_40, 0)}</td><td class="num">${pct(r.prob_60, 0)}</td><td class="num">${pct(r.prob_loss, 0)}</td><td class="num">${pct(r.prob_dd35, 0)}</td></tr>`).join('')}</table>
        <p class="muted">Reading it: more size raises the average and the chance of a big year, but the worst fall grows just as fast. From Mon 5 Oct an aggressive paper book runs at 2.5x beside the frozen 1x book (your choice on 30 Sep). It alerts at a 45% fall and stops buying at 60%. The Alpaca practice account can only hold about 1.6x of it, because the broker lends 2x on stocks and nothing against crypto.</p></div>` : ''}
      <div class="card wide"><h3>How the mix changed over time</h3><p class="muted">Grey is cash. It grows when crypto is in a downtrend or when the drawdown brake cuts positions.</p>${stackArea(f.dates, order.map((a) => ({ name: names[a], y: f.weights[a] })), { colors })}</div>
      <div class="card wide">${h3('How the holdings move together (last 12 months)', 'Correlation')}${heatmap(f.assets.map((a) => names[a]), f.corr, { cols: f.assets.map((a) => names[a]), total: false, fmt: (v) => v.toFixed(2), unit: '', max: 1 })}
        <p class="muted">1.00 means two holdings always move together and 0 means they are unrelated. ${f.corr[1][2] >= 0.8 ? 'Bitcoin and Ether move almost as one, so together they act like a single bet. ' : ''}The lower the stock-to-crypto numbers, the more mixing them smooths the ride.</p></div>`;
  },


  async system() {
    const s = await api('system');
    sysHistory.push({ t: s.at, cpu: s.cpu.temp_c, cores: s.cpu.cores_c ?? null, gpu: s.gpu?.temp_c ?? null, cpuUse: s.cpu.usage, gpuUse: s.gpu ? s.gpu.util / 100 : null });
    if (sysHistory.length > 450) sysHistory.shift();  // 15 minutes at one reading every 2 s
    const gb = (b) => (b == null ? '–' : `${(b / 2 ** 30).toFixed(1)} GB`), heat = (c, warn, hot) => (c == null ? '' : c >= hot ? pill('hot', 'bad') : c >= warn ? pill('warm', 'warn') : pill('ok', 'ok'));
    const g = s.gpu, memUsed = s.mem.total != null ? 1 - s.mem.available / s.mem.total : null, x = sysHistory.map((h) => h.t);
    const clock = (d) => new Date(d).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const hist = (keys, names, f) => (sysHistory.length > 1 ? lineChart(keys.map((k, i) => ({ name: names[i], x, y: sysHistory.map((h) => h[k]) })), { width: 560, height: 220, yFmt: f, xTicks: clock }) : '<p class="empty">The chart fills in as this page refreshes (every 2 seconds).</p>');
    const up = `${Math.floor(s.uptime_s / 86400)}d ${Math.floor((s.uptime_s % 86400) / 3600)}h ${Math.floor((s.uptime_s % 3600) / 60)}m`;
    return `<h2>System</h2><p class="lede">How hard this PC is working right now. ${pill('experimental', 'warn')}</p>
      ${plain('The AI models run on the graphics card (GPU) and the trading code on the processor (CPU). If the GPU memory is full or something is running hot, research jobs slow down or stop. This page only reads the sensors; it changes nothing.')}
      <div class="kpis">
        ${kpi('CPU temperature', s.cpu.cores_c == null ? (s.cpu.temp_c == null ? '–' : `${s.cpu.temp_c.toFixed(1)} °C`) : `${s.cpu.cores_c.toFixed(1)} °C`, `${heat(s.cpu.cores_c ?? s.cpu.temp_c, 80, 92)}${s.cpu.cores_c != null && s.cpu.temp_c != null ? ` cores · hottest point ${s.cpu.temp_c.toFixed(1)} °C` : ''}`)}
        ${kpi('GPU temperature', g?.temp_c == null ? '–' : `${g.temp_c} °C`, heat(g?.temp_c, 75, 85))}
        ${kpi('CPU in use', pct(s.cpu.usage, 0), `load ${s.cpu.load.map((v) => v.toFixed(1)).join(' · ')} on ${s.cpu.cores} threads`)}
        ${kpi('GPU in use', g ? `${g.util}%` : '–', g ? `${g.power_w?.toFixed(0) ?? '–'} W${g.fan != null ? ` · fan ${g.fan}%` : ''}` : 'no NVIDIA GPU found')}
        ${kpi('GPU memory', g ? `${(g.mem_used_mb / 1024).toFixed(1)} / ${(g.mem_total_mb / 1024).toFixed(1)} GB` : '–', g ? `${pct(g.mem_used_mb / g.mem_total_mb, 0)} used` : '')}
        ${kpi('Memory (RAM)', memUsed == null ? '–' : pct(memUsed, 0), `${gb(s.mem.available)} free of ${gb(s.mem.total)}`)}
        ${kpi('Disk', s.disk ? gb(s.disk.free) : '–', s.disk ? `free of ${gb(s.disk.total)}` : '')}
        ${kpi('Up for', up, esc(s.host))}
      </div>
      <div class="split">
        <div class="card"><h3>Temperature (°C), last 15 minutes</h3>${hist(['cores', 'cpu', 'gpu'], ['CPU cores', 'CPU hottest point', 'GPU'], (v) => v.toFixed(0))}</div>
        <div class="card"><h3>How busy</h3>${hist(['cpuUse', 'gpuUse'], ['CPU', 'GPU'], (v) => pct(v, 0))}</div>
      </div>
      <div class="split">
        <div class="card"><h3>What is using the GPU</h3><table><tr><th>Program</th><th class="num">GPU memory</th></tr>
          ${(g?.procs || []).map((p) => `<tr><td>${esc(p.name)}</td><td class="num">${p.mb == null ? '–' : `${fmt(p.mb)} MB`}</td></tr>`).join('') || '<tr><td colspan="2" class="muted">nothing</td></tr>'}</table>
          <p class="muted">${esc(g?.name ?? '')}</p></div>
        <div class="card"><h3>All temperature sensors</h3><table><tr><th>Part</th><th>Sensor</th><th class="num">°C</th></tr>
          ${s.sensors.map((t) => `<tr><td>${esc({ k10temp: 'CPU', nvme: 'SSD', amdgpu: 'Built-in graphics', coretemp: 'CPU' }[t.chip] || t.chip)}</td><td>${esc(t.label)}</td><td class="num">${t.c.toFixed(1)}</td></tr>`).join('')}</table>
          <p class="muted">${esc(s.cpu.model ?? '')}</p></div>
      </div>`;
  },

  async settings() {
    const info = await api('info').catch(() => ({}));
    const pages = [...document.querySelectorAll('#nav [data-view]')].map((b) => [b.dataset.view, [...b.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('').trim() || b.dataset.view]);
    const sw = (key, label, note) => `<label class="set-row"><span><b>${esc(label)}</b><em>${esc(note)}</em></span><input type="checkbox" data-set="${key}" ${settings[key] ? 'checked' : ''}></label>`;
    const sel = (key, label, note, opts) => `<label class="set-row"><span><b>${esc(label)}</b><em>${esc(note)}</em></span><select data-set="${key}">${opts.map(([v, n]) => `<option value="${esc(v)}" ${String(settings[key]) === String(v) ? 'selected' : ''}>${esc(n)}</option>`).join('')}</select></label>`;
    return `<h2>Settings</h2><p class="lede">How this app looks and refreshes. ${pill('experimental', 'warn')}</p>
      ${plain('These settings only change this window. They never change what airp trades: the trading rules, the risk limits and the kill switch are not here on purpose.')}
      <div class="card wide"><h3>Refreshing</h3>
        ${sw('autoReload', 'Reload pages automatically', 'Keeps the numbers fresh without pressing Refresh.')}
        ${sel('reloadSec', 'Reload every', 'The System page always refreshes every 5 seconds while it is open.', [[10, '10 seconds'], [30, '30 seconds'], [60, '1 minute'], [300, '5 minutes']])}
      </div>
      <div class="card wide"><h3>Display</h3>
        ${sw('plainWords', 'Show the "In plain words" boxes', 'The short explanations at the top of each page.')}
        ${sel('startPage', 'Open the app on', 'The first page you see.', pages.filter(([v]) => v !== 'settings'))}
      </div>
      <div class="card wide"><h3>Your return targets</h3>
        ${sel('target', 'Baseline target per year', 'Shown on Home and on the risk table. It is a goal, not a promise.', [[20, '20%'], [30, '30%'], [40, '40%'], [50, '50%']])}
        ${sel('stretch', 'Stretch target per year', 'The result you would love to see.', [[40, '40%'], [60, '60%'], [80, '80%'], [100, '100%']])}
        <p class="muted">The chances shown in the app are worked out for 40% and 60%; other values change the labels only.</p>
      </div>
      <div class="card wide"><h3>About</h3><div class="statgrid">
        <div><span>airp folder</span><b>${esc(info.airpRoot ?? '–')}</b></div><div><span>Folder found</span><b>${info.found ? 'yes' : 'no'}</b></div>
        <div><span>Electron</span><b>${esc(info.versions?.electron ?? 'browser')}</b></div><div><span>Node</span><b>${esc(info.versions?.node ?? '–')}</b></div>
      </div><div class="row" style="margin-top:12px"><button class="btn ghost" id="reset-settings">Reset to defaults</button></div></div>`;
  },

  async signals() {
    const [d, m, team, cons] = await Promise.all([api('decisions'), metrics().catch(() => ({})), api('team').catch(() => []), api('consensus').catch(() => null)]);
    const vals = d.filter((r) => r.type === 'decision' && r.logodds != null).map((r) => Number(r.logodds));
    const thr = m?.live?.ai_threshold, ai = m?.ai && !m.ai.error ? m.ai : null;
    const tone = (v) => pill(v ?? 'no view', v === 'bullish' || v === 'positive' || v === 'raised' ? 'ok' : v === 'bearish' || v === 'negative' || v === 'lowered' ? 'bad' : '');
    const num = (x, unit = '', d = 2) => (x?.q == null ? 'not found' : `${unit === 'M' ? '$' : ''}${fmt(x.q, d)}${unit}${x.prior != null ? ` <span class="muted">(a year ago ${fmt(x.prior, d)}, ${x.prior ? `${x.q >= x.prior ? '+' : ''}${pct(x.q / x.prior - 1, 0)}` : '–'})</span>` : ''}`);
    const pts = (xs) => (xs.length ? `<ul>${xs.map((x) => `<li>${esc(x.point)}${x.verified ? '' : ' <span class="muted">(quote not verified)</span>'}</li>`).join('')}</ul>` : '<p class="muted">none given</p>');
    const card = (r) => `<div class="team-card"><div class="team-head"><span class="tk">${esc(r.ticker)}</span><span class="muted">${esc(r.sector)} · ${esc(when(r.accepted_utc ? `${r.accepted_utc}Z` : null))}</span>
        ${thr != null && r.logodds >= thr ? pill('practice trade', 'lime') : pill('no trade')}${r.outcome?.excess != null ? pill(`result ${pct(r.outcome.excess)} vs sector`, r.outcome.excess > 0 ? 'ok' : 'bad') : pill('result due in a week', 'warn')}</div>
      <div class="agents">
        <div class="agent"><div class="who"><span>${r.pipeline === 'jan_bonsai_v1' ? 'Jan reader & research' : 'Reader agent'} · checked facts</span>${tone(r.reader.tone)}</div>
          Sales ${num(r.reader.revenue, 'M', 0)}<br>Profit per share ${num(r.reader.eps)}<br>Outlook: ${tone(r.reader.guidance)}
          ${r.reader.rejected.length ? `<br><span class="muted">${r.reader.rejected.length} number(s) thrown out: not found word-for-word</span>` : ''}
          ${(r.dropped || []).map((x) => `<br><span class="warn-text">Not passed to the judge: ${esc(x)}</span>`).join('')}</div>
        <div class="agent"><div class="who"><span>Summary agent · in testing</span>${tone(r.net_read?.read)}</div>
          ${r.net_read ? `Conviction: ${esc(r.net_read.conviction ?? '–')}<br>Margins: ${esc(r.net_read.fields.margin ?? '–')} · Demand: ${esc(r.net_read.fields.demand ?? '–')}<br>Earnings quality: ${esc(r.net_read.fields.earnings_quality ?? '–')}` : '<span class="muted">not run</span>'}</div>
        <div class="agent"><div class="who"><span>Bull case · in testing</span></div>${r.bull_bear ? pts(r.bull_bear.bull) : '<span class="muted">not run</span>'}</div>
        <div class="agent"><div class="who"><span>Bear case · in testing</span>${r.bull_bear ? pill(`debate verdict: ${r.bull_bear.read ?? 'none'}`, r.bull_bear.read === 'bullish' ? 'ok' : r.bull_bear.read === 'bearish' ? 'bad' : '') : ''}</div>${r.bull_bear ? pts(r.bull_bear.bear) : '<span class="muted">not run</span>'}</div>
        <div class="agent"><div class="who"><span>Aschenbrenner AI build-out view · in testing</span>${tone(r.ai_lens?.read)}</div>${r.ai_lens ? `AI exposure: ${esc(r.ai_lens.exposure ?? '–')}` : '<span class="muted">not run</span>'}</div>
        <div class="agent verdict"><div class="who"><span>Master judge · decides</span>${pill(r.logodds > 0 ? 'BUY' : 'PASS', r.logodds > 0 ? 'ok' : '')}</div>
          Confidence score <b>${fmt(r.logodds, 2)}</b>${thr != null ? `<br><span class="muted">needs ${esc(thr)} to trade</span>` : ''}
          ${r.sheet ? `<details><summary>The fact sheet it read</summary><pre class="sheet">${esc(r.sheet)}</pre></details>` : ''}</div>
      </div></div>`;
    const b = ai?.buckets || [];
    return `<h2>AI picks</h2><p class="lede">What the AI team thought of each new earnings report, and how well that has worked.</p>
      ${plain('When a big company reports earnings, a reader AI pulls out the key numbers and the judge AI (Bonsai) gives a confidence score that the stock will beat its industry group over the next week. Only scores above the threshold become a practice trade. The other agents give opinions that are scored in the background. This part is <b>still unproven</b>: it trades pretend money only.')}
      <div class="kpis">${kpi('Live decisions', vals.length)}${kpi('Above threshold', vals.filter((v) => thr != null && v >= thr).length, `threshold ${esc(thr ?? '–')}`)}${kpi('Missed', d.filter((r) => r.type === 'missed').length)}</div>
      ${consensusCard(cons)}
      <h3>The team's view on each report</h3>
      <div class="team">${team.map(card).join('') || '<p class="empty">No live reports yet.</p>'}</div>
      <div class="card wide">${h3('How confident the judge was (each bar counts reports)', 'Log-odds')}${histogram(vals, { mark: thr, markLabel: 'pick threshold', bins: 16 })}</div>
      ${ai ? `<h2>Does the judge's score mean anything?</h2><p class="lede">Evidence from ${fmt(ai.n)} past earnings reports (2024 to 2026).</p>
      ${plain(`Each past report got a score, and we then looked at how the stock did against its industry group over the next 5 days. A useful judge should show <b>higher scores going with better results</b>. The link is there but weak: reports above the threshold beat their group ${pct(ai.above.hit, 0)} of the time against ${pct(ai.below.hit, 0)} for the rest. The threshold was chosen on this same data, so the live results are the real test.`)}
      <div class="kpis">${kpi('Reports scored', fmt(ai.n))}${kpi('Rank correlation (IC)', esc(ai.ic))}${kpi('Hit rate above threshold', pct(ai.above.hit, 1), `${fmt(ai.above.n)} reports`)}${kpi('Hit rate below', pct(ai.below.hit, 1), `${fmt(ai.below.n)} reports`)}
        ${kpi('Avg result above', pct(ai.above.excess, 2), '5 days vs sector')}${kpi('Avg result below', pct(ai.below.excess, 2), '5 days vs sector')}</div>
      <div class="split">
        <div class="card"><h3>Average 5-day result by score group</h3><p class="muted">Reports sorted into 10 equal groups, lowest score on the left.</p>${barChart(b.map((x) => String(x.score)), [{ name: 'avg result vs sector', y: b.map((x) => x.excess) }], { width: 560, height: 240, yFmt: (v) => pct(v, 1) })}</div>
        <div class="card">${h3('How often each group beat its sector', 'Hit rate')}<p class="muted">Above 50% is better than a coin flip.</p>${barChart(b.map((x) => String(x.score)), [{ name: 'hit rate minus 50%', y: b.map((x) => x.hit - 0.5) }], { width: 560, height: 240, yFmt: (v) => `${((v + 0.5) * 100).toFixed(0)}%` })}</div>
      </div>
      <div class="card wide">${h3('Was the link there every month?', 'Rank correlation (IC)')}<p class="muted">Bars above zero are months where higher scores did go with better results.</p>${barChart(ai.monthly_ic.map((x) => x.month.slice(2)), [{ name: 'monthly IC', y: ai.monthly_ic.map((x) => x.ic) }], { yFmt: (v) => v.toFixed(2) })}</div>
      <div class="card wide"><h3>Every dot is one past report</h3>${scatter(ai.scatter, { mark: thr, markLabel: 'pick threshold', xLabel: 'judge confidence score  →', yFmt: (v) => pct(v, 0) })}<p class="muted">Up is better (the stock beat its sector over 5 days). The cloud is wide: any single pick is close to a coin flip, the edge only shows on average.</p></div>
      <div class="card wide"><h3>By industry group</h3><table><tr><th>Sector</th><th class="num">Reports</th><th class="num">Avg score</th><th class="num">Avg 5-day result</th><th class="num">Hit rate</th></tr>
        ${ai.sectors.map((x) => `<tr><td>${esc(x.sector)}</td><td class="num">${fmt(x.n)}</td><td class="num">${fmt(x.score, 2)}</td><td class="num">${pct(x.excess, 2)}</td><td class="num">${pct(x.hit, 0)}</td></tr>`).join('')}</table></div>` : ''}`;
  },
});

let current = 'overview';
let operationId = null;
let operationStarting = false;
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
  // A refresh keeps what you opened: expanded sections stay expanded (keyed by their summary text and order)
  const keys = (root) => { const seen = {}; return [...root.querySelectorAll('details')].map((d) => {
    const t = d.querySelector(':scope > summary')?.textContent.trim().slice(0, 120) ?? ''; seen[t] = (seen[t] || 0) + 1; return `${t}#${seen[t]}`; }); };
  const before = keepScroll ? keys($('#view')) : [];
  const open = new Set([...$('#view').querySelectorAll('details')].map((d, i) => (keepScroll && d.open ? before[i] : null)).filter(Boolean));
  const top = $('.main-area').scrollTop;
  $('#view').innerHTML = html;
  if (open.size) { const after = keys($('#view')); $('#view').querySelectorAll('details').forEach((d, i) => { if (open.has(after[i])) d.open = true; }); }
  $('.main-area').scrollTop = keepScroll ? top : 0;
  attachHover($('#view'));
  $('#updated').textContent = `updated ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
}

async function sidebarState() {
  try {
    const o = await api('overview');
    $('#mode-text').innerHTML = `${esc(o.mode)} · ${o.halted ? '<span class="err">HALTED</span>' : 'trading'}`;
    $('#halt-btn').textContent = o.halted ? 'Resume trading' : 'Kill switch';
    $('#halt-btn').dataset.halted = o.halted ? '1' : '';
    const modeBadge = $('#mode-badge');
    if (modeBadge) {
      modeBadge.textContent = o.halted ? 'HALTED' : (o.mode ? o.mode.toUpperCase() : 'PAPER');
      modeBadge.classList.toggle('halted', !!o.halted);
    }
    const topStatus = $('#top-status');
    const topText = $('#top-status-text');
    if (topStatus && topText) {
      topStatus.classList.toggle('halted', !!o.halted);
      topText.textContent = o.halted ? 'KILL SWITCH ACTIVE · HALTED' : `${o.mode || 'Paper book'} · Active`;
    }
  } catch {
    $('#mode-text').textContent = 'airp not reachable';
    const topStatus = $('#top-status');
    const topText = $('#top-status-text');
    if (topStatus && topText) {
      topStatus.classList.add('halted');
      topText.textContent = 'Server unreachable';
    }
  }
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

const searchInput = $('#nav-search');
const searchClear = $('#nav-search-clear');
if (searchInput) {
  const filterNav = () => {
    const q = (searchInput.value || '').trim().toLowerCase();
    if (searchClear) searchClear.hidden = !q;
    const items = document.querySelectorAll('#nav .nav-item');
    items.forEach((item) => {
      const text = item.textContent.toLowerCase();
      const match = !q || text.includes(q);
      item.hidden = !match;
    });
    document.querySelectorAll('#nav .nav-section-title').forEach((title) => {
      let next = title.nextElementSibling;
      let hasVisible = false;
      while (next && !next.classList.contains('nav-section-title')) {
        if (!next.hidden) hasVisible = true;
        next = next.nextElementSibling;
      }
      title.hidden = !hasVisible;
    });
  };

  searchInput.addEventListener('input', filterNav);
  searchClear?.addEventListener('click', () => {
    searchInput.value = '';
    filterNav();
    searchInput.focus();
  });

  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const first = document.querySelector('#nav .nav-item:not([hidden])');
      if (first) {
        show(first.dataset.view);
        searchInput.blur();
      }
    } else if (e.key === 'Escape') {
      searchInput.value = '';
      filterNav();
      searchInput.blur();
    }
  });

  window.addEventListener('keydown', (e) => {
    if ((e.key === '/' || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k')) && !['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) {
      e.preventDefault();
      searchInput.focus();
      searchInput.select();
    }
  });
}

$('#refresh').addEventListener('click', async () => {
  const btn = $('#refresh');
  const orig = btn.innerHTML;
  btn.innerHTML = '<span class="btn-icon spin">↻</span>Refreshing…';
  btn.disabled = true;
  metricsCache = null;
  try {
    await show(current);
    await sidebarState();
    btn.innerHTML = '<span class="btn-icon">✓</span>Refreshed';
    setTimeout(() => { btn.innerHTML = orig; btn.disabled = false; }, 800);
  } catch {
    btn.innerHTML = orig;
    btn.disabled = false;
  }
});
$('#rebuild').addEventListener('click', async () => {
  const btn = $('#rebuild');
  const orig = btn.innerHTML;
  btn.innerHTML = '<span class="btn-icon spin">⚡</span>Rebuilding…';
  btn.disabled = true;
  try {
    await api('metrics', {});
    metricsCache = null;
    await show(current);
    btn.innerHTML = '<span class="btn-icon">✓</span>Rebuilt';
    setTimeout(() => { btn.innerHTML = orig; btn.disabled = false; }, 1200);
  } catch (e) {
    alert(e.message);
    btn.innerHTML = orig;
    btn.disabled = false;
  }
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
  const go = e.target.closest('[data-go]');
  if (go) { await show(go.dataset.go); return; }
  const navigate = e.target.closest('[data-view]');
  if (navigate) { await show(navigate.dataset.view); return; }
  const log = e.target.closest('[data-operation-log]');
  if (log) { operationId = log.dataset.operationLog; await show('operations', true); return; }
  const stopAuto = e.target.closest('[data-stop-auto]');
  if (stopAuto) {
    try { await api('operations/stop', { id: stopAuto.dataset.stopAuto }); await show(stopAuto.dataset.back || 'operations', true); }
    catch (error) { alert(error.message); }
    return;
  }
  const op = e.target.closest('[data-operation]');
  if (op) {
    if (operationStarting) return;
    operationStarting = true;
    try {
      const action = op.dataset.operation;
      const paper = ['paper_ai', 'paper_sync', 'paper_research_test', 'paper_auto', 'paper_auto_tech100', 'full_auto', 'autopilot_day'].includes(action);
      const confirmation = paper ? await confirmDialog({ title: op.closest('.card')?.querySelector('h3')?.textContent || op.textContent,
        text: action === 'autopilot_day' ? 'Start Autopilot Day: price-only day trading of 14 ETFs in the SEPARATE Alpaca paper account, inside the hours you set. 3x leverage for this book (3.9x for the account), flat at the end of the window, a 4% circuit breaker and 5% daily stop. Its strategy failed its backtest; it sends orders only while the LIVE switch is on.' : action === 'full_auto' ? 'Start Autopilot Night: it researches the 150 technology companies and the theme stocks (reusing saved research memory), and trades their bull and bear calls long and short over 5 to 63 sessions in the SEPARATE Alpaca paper account (paper money only). It runs until you stop it and uses the GPU between scheduled jobs. A 4% daily fall stops new risk; a 35% fall from the start closes everything.' : action === 'paper_auto_tech100' ? 'Activate the cached 100 technology stocks as the persistent entry universe for ALL AI paper runs, including scheduled runs. Existing positions can exit and paired sector ETF hedges remain allowed. No model or dataset downloads. New horizon budgets stay simulations; approved five-day sizing remains in force. Stop the current continuous worker before switching profiles.' : action === 'paper_auto' ? 'Start continuous paper sync, filing checks every 15 minutes and evidence review, plus a resumable news research queue. This can use the GPU for days. Ten minutes is a maximum per company, not a forced delay. Proposals cannot change the mandate. Stop finishes the current step; restart manually after reboot.' : action === 'paper_research_test' ? 'Run fresh public research and virtual horizon proposals, then the approved filing strategy and Alpaca paper orders. Experimental horizon allocations are not submitted. This can use the GPU for several minutes.' : action === 'paper_ai' ? 'Jan reads and researches new filings, then Bonsai judges them and eligible paper trades are submitted. This uses the GPU sequentially and can take time. Failed research is deferred, with no lite fallback. Existing entry sessions and sizing apply.' : 'Reconcile existing orders and submit any pending paper orders. This may change holdings in the practice account.', word: 'PAPER' }) : {};
      if (!confirmation) return;
      op.disabled = true;
      const run = await api('operations', { action, ...confirmation });
      operationId = run.id;
      await show(['full_auto', 'autopilot_day'].includes(action) ? 'autopilot' : 'operations', true);
    } catch (error) { alert(error.message); await show('operations', true); }
    finally { operationStarting = false; }
    return;
  }
  const ctl = e.target.closest('[data-control]');
  if (ctl) {
    const act = ctl.dataset.act, strategy = ctl.dataset.control;
    let confirm = '';
    if (act === 'flatten') {
      const c = await confirmDialog({ title: `Close all ${strategy === 'day' ? 'Day' : 'Night'} positions`, text: `Sell or buy back every position Autopilot ${strategy === 'day' ? 'Day' : 'Night'} owns in the paper account, then keep new entries paused until you resume. The result is confirmed from the broker.`, word: 'FLATTEN' });
      if (!c) return; confirm = c.confirm;
    }
    try { await api('control', { strategy, action: act, confirm }); } catch (error) { alert(error.message); }
    await show('fund', true); return;
  }
  const fact = e.target.closest('[data-fact]');
  if (fact) { try { await api('fact-review', { id: fact.dataset.fact, verdict: fact.dataset.verdict }); } catch (error) { alert(error.message); } await show('fund', true); return; }
  const q = e.target.closest('[data-q]');
  if (q) { await api('research', { action: q.dataset.q, name: q.dataset.name }).catch((x) => alert(x.message)); show('research'); }
  const acct = e.target.closest('[data-acct]');
  if (acct) { alpacaTab = acct.dataset.acct; show('alpaca'); return; }
  const st = e.target.closest('[data-strat]');
  if (st) { stratTab = st.dataset.strat; show('strategies'); return; }
  const rv = e.target.closest('[data-review]');
  if (rv) {
    const r = await fetch(`/api/review?name=${encodeURIComponent(rv.dataset.review)}`).then((x) => x.json());
    $('#review-text').textContent = r.text;
    document.querySelectorAll('[data-review]').forEach((b) => b.classList.toggle('lime', b === rv));
  }
});
$('#view').addEventListener('submit', async (e) => {
  const engineering = e.target.closest('[data-engineering-form]');
  if (engineering) {
    e.preventDefault();
    const data = new FormData(engineering);
    const body = { action: engineering.dataset.action, digest: engineering.dataset.digest,
      reviewer: data.get('reviewer'), note: data.get('note'), confirm: data.get('confirm'), checks: data.getAll('checks') };
    if (body.action === 'costs') body.costs = Object.fromEntries(['fees', 'borrow', 'financing'].map((key) => [key, Number(data.get(key))]));
    try { await api('engineering_review', body); await show('institutional', true); } catch (error) { alert(error.message); }
    return;
  }
  const form = e.target.closest('[data-benchmark-form]');
  if (!form) return;
  e.preventDefault();
  const data = new FormData(form);
  try {
    await api('benchmark', { accession: form.dataset.accession, expected_source_hash: form.dataset.sourceHash,
      expected_case_hash: form.dataset.caseHash, reviewer: data.get('reviewer'), note: data.get('note'),
      checks: data.getAll('checks'), decision: data.get('decision'), confirm: data.get('confirm'),
      corrected: JSON.parse(data.get('corrected')) });
    await show('benchmark', true);
  } catch (error) { alert(error.message); }
});

saveSettings();
const start = location.hash.slice(1);
show(views[start] ? start : views[settings.startPage] ? settings.startPage : 'overview');
window.addEventListener('hashchange', () => { const v = location.hash.slice(1); if (views[v]) show(v); });
sidebarState();
// Auto-reload: every page except the ones you read or type in. The System page ticks every 5 s so its charts move.
const NO_RELOAD = new Set(['settings', 'how', 'reviews', 'benchmark', 'institutional']);
let lastReload = Date.now();
setInterval(() => {
  const due = current === 'system' ? 2 : ['operations', 'autopilot'].includes(current) ? 5 : current === 'fund' ? 15 : settings.reloadSec;
  if ((!settings.autoReload && !['operations', 'autopilot'].includes(current)) || NO_RELOAD.has(current) || document.querySelector('dialog[open]') || operationStarting || document.hidden) return;
  if (Date.now() - lastReload < due * 1000) return;
  lastReload = Date.now();
  if (current !== 'system') { metricsCache = null; sidebarState(); }
  show(current, true);
}, 1000);
document.addEventListener('submit', async (e) => {
  const f = e.target.closest('[data-day-window]'); if (!f) return;
  e.preventDefault();
  try { await api('autopilot-day-window', { start: f.start.value, end: f.end.value }); await show('autopilot', true); }
  catch (error) { alert(error.message); }
});
document.addEventListener('change', (e) => {
  const el = e.target.closest('[data-set]'); if (!el) return;
  const k = el.dataset.set;
  settings[k] = el.type === 'checkbox' ? el.checked : (typeof DEFAULTS[k] === 'number' ? Number(el.value) : el.value);
  saveSettings();
});
document.addEventListener('click', (e) => {
  if (e.target.id !== 'reset-settings') return;
  Object.assign(settings, DEFAULTS); saveSettings(); show('settings');
});
