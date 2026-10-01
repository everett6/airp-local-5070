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
const sysHistory = [];  // recent machine readings for the System page charts (kept while the app is open)

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
    return `<h2>Home</h2><p class="lede">Your practice-money trading system at a glance.</p>
      <div class="banner ${o.halted ? 'bad' : clean === Math.min(10, runs.length) ? 'ok' : 'warn'}">${o.halted
        ? '<b>Trading is stopped.</b> The kill switch is on; nothing will be bought or sold until you resume.'
        : clean === Math.min(10, runs.length) ? '<b>Everything is running normally.</b> The system trades practice money by itself; you do not need to do anything.'
          : `<b>Running, but ${Math.min(10, runs.length) - clean} of the last ${Math.min(10, runs.length)} runs had a problem.</b> See Alerts below for what happened.`}
        ${next[0] ? ` Next automatic run: <b>${esc(when(Number(next.slice().sort((a, b) => a.next - b.next)[0].next) / 1000))}</b>.` : ''}</div>
      ${plain('airp follows a fixed set of rules to invest <b>pretend money</b>. The numbers below compare the main rule set (the "core book") with simply buying the whole US market (SPY) since 2018. Hover any <b>?</b> for what a word means, or open <b>How it works</b>.')}
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
    const b = await api('books');
    const names = Object.keys(b.series);
    const main = b.series['master+brakes'] || b.series.master || [];
    const ai = b.aiPicks;
    const legs = Object.entries(b.orders || {}).flatMap(([k, d]) => (d.legs || []).map((l) => ({ ...l, decision: k })))
      .concat((ai?.pairs || []).flatMap((p) => (p.legs || []).map((l) => ({ ...l, decision: `AI ${p.ticker}` }))));
    return `<h2>Live book &amp; orders</h2><p class="lede">What the system holds right now and the practice orders it has sent.</p>
      ${plain('The system keeps its own record of every trade (the "simulator") and also sends the same trades to a practice account at the broker Alpaca. This page shows both, so you can see they agree. No real money is involved.')}
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
    const q = await api('research');
    if (!Array.isArray(q)) return `<h2>Research queue</h2><p class="err">${esc(q.error)}</p>`;
    return `<h2>Research queue</h2><p class="lede">Long background jobs that test new ideas. They restart themselves.</p>
      ${plain('These jobs gather news and have the AI label thousands of past earnings releases, to test whether research agents can improve the picks. Jobs that need the graphics card only run at night (18:00–05:25) so they never get in the way of live trading. You can pause one if the PC feels slow.')}
      <div class="grid">${q.map((j) => { const [d, t] = j.progress; const pct = t ? Math.min(100, (100 * d) / t) : 0;
        const state = j.done ? pill('done', 'ok') : j.paused ? pill('paused', 'warn') : j.running ? pill('running', 'lime') : pill(j.kind === 'gate' ? 'waiting for check' : 'queued');
        return `<div class="card wide"><div class="row"><h3 style="margin:0">${esc(j.name)}</h3>${pill(j.kind)}${state}
          <span style="margin-left:auto" class="row">${j.kind === 'gate' || j.done ? '' : `<button class="btn ghost" data-q="${j.paused ? 'resume' : 'pause'}" data-name="${esc(j.name)}">${j.paused ? 'Resume' : 'Pause'}</button>`}</span></div>
          <p class="muted">${esc(j.note)}</p>${j.kind === 'gate' ? '' : `<div class="bar"><span style="width:${pct.toFixed(1)}%"></span></div><p class="tiny muted">${fmt(d)} / ${fmt(t)}</p>`}</div>`; }).join('')}</div>`;
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
          ${step(4, 'Research agents', 'in testing', 'Gather news and opinions', 'Extra agents (a news researcher, a bull-versus-bear debate, a summary reader) form views. They are scored in the background and only count once they prove useful.')}
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
    if (stratTab === 'longterm') {
      return `${head}<div class="card wide"><h3>Long-term picks (monthly, 10 names) and theme track</h3>
        <p>Once a month the AI picks 10 stocks to hold for the long run, plus a set of big themes. The first picks are made on <b>Thu 1 Oct at 15:30</b>.</p>
        <p>Each month's picks are later compared with simply holding the market. The track is only judged after 12 months of scored picks, and it gets no money before then.</p>
        <p class="muted">This page fills in by itself once the first picks exist.</p></div>`;
    }
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
    return `<h2>Strategy lab</h2><p class="lede">Trading ideas we tested, and whether they held up. ${passed} of ${tests.length} passed.</p>
      ${plain('Each row is one idea (day trading, calendar effects, pairs, crypto). The dot is its score after costs and the line is how uncertain that score is. To pass, the dot must be right of the dashed line <b>and</b> the whole line must be right of zero. Grey rows failed, so they do not trade.')}
      <div class="card wide">${h3('Score after costs, with its uncertainty range', '95% interval')}${ciChart(rows, { refs: [{ v: 0.5, label: 'pass line 0.5' }] })}</div>
      <div class="card wide"><h3>The detail behind each test</h3><p class="muted">"Score by trading cost" shows the same idea at cheap and expensive trading: an idea that only works when trading is free is not real. "Before it was published" is the score on the years before the idea became widely known.</p>
        <table><tr><th>Test</th><th>Tested on</th><th class="num">Days</th><th class="num">Score by trading cost</th><th class="num">Before it was published</th><th class="num">Up days</th><th class="num">Worst month</th><th class="num">Moves with core book${help('Correlation')}</th><th>Verdict</th></tr>
        ${tests.map((x) => `<tr><td>${esc(x.id)} · ${esc(x.name)}</td><td>${esc((x.window || []).map((d) => String(d).slice(0, 7)).join(' → '))}</td><td class="num">${fmt(x.days)}</td>
          <td class="num">${Object.entries(x.costs || {}).map(([c, v]) => `${esc(c)}: ${v == null ? '–' : fmt(v, 2)}`).join(' · ') || fmt(x.sharpe, 2)}</td><td class="num">${x.before_sharpe == null ? '–' : fmt(x.before_sharpe, 2)}</td>
          <td class="num">${x.hit_rate == null ? '–' : pct(x.hit_rate, 0)}</td><td class="num">${x.worst_month == null ? '–' : pct(x.worst_month)}</td><td class="num">${x.corr_core == null ? '–' : fmt(x.corr_core, 2)}</td><td>${x.pass ? pill('passed', 'ok') : pill('failed', 'bad')}</td></tr>`).join('')}</table></div>
      <div class="split">${curveCards}</div>
      <p class="muted">D9 passed but trades the official open auction, which is not tradable in practice; its tradable version (D10) failed.</p>`;
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
    sysHistory.push({ t: s.at, cpu: s.cpu.temp_c, gpu: s.gpu?.temp_c ?? null, cpuUse: s.cpu.usage, gpuUse: s.gpu ? s.gpu.util / 100 : null });
    if (sysHistory.length > 240) sysHistory.shift();
    const gb = (b) => (b == null ? '–' : `${(b / 2 ** 30).toFixed(1)} GB`), heat = (c, warn, hot) => (c == null ? '' : c >= hot ? pill('hot', 'bad') : c >= warn ? pill('warm', 'warn') : pill('ok', 'ok'));
    const g = s.gpu, memUsed = s.mem.total != null ? 1 - s.mem.available / s.mem.total : null, x = sysHistory.map((h) => h.t.slice(11, 19));
    const hist = (keys, names, f) => (sysHistory.length > 1 ? lineChart(keys.map((k, i) => ({ name: names[i], x, y: sysHistory.map((h) => h[k]) })), { width: 560, height: 220, yFmt: f }) : '<p class="empty">The chart fills in as this page refreshes (every few seconds).</p>');
    const up = `${Math.floor(s.uptime_s / 86400)}d ${Math.floor((s.uptime_s % 86400) / 3600)}h ${Math.floor((s.uptime_s % 3600) / 60)}m`;
    return `<h2>System</h2><p class="lede">How hard this PC is working right now. ${pill('experimental', 'warn')}</p>
      ${plain('The AI models run on the graphics card (GPU) and the trading code on the processor (CPU). If the GPU memory is full or something is running hot, research jobs slow down or stop. This page only reads the sensors; it changes nothing.')}
      <div class="kpis">
        ${kpi('CPU temperature', s.cpu.temp_c == null ? '–' : `${s.cpu.temp_c.toFixed(0)} °C`, heat(s.cpu.temp_c, 80, 92))}
        ${kpi('GPU temperature', g?.temp_c == null ? '–' : `${g.temp_c} °C`, heat(g?.temp_c, 75, 85))}
        ${kpi('CPU in use', pct(s.cpu.usage, 0), `load ${s.cpu.load.map((v) => v.toFixed(1)).join(' · ')} on ${s.cpu.cores} threads`)}
        ${kpi('GPU in use', g ? `${g.util}%` : '–', g ? `${g.power_w?.toFixed(0) ?? '–'} W${g.fan != null ? ` · fan ${g.fan}%` : ''}` : 'no NVIDIA GPU found')}
        ${kpi('GPU memory', g ? `${(g.mem_used_mb / 1024).toFixed(1)} / ${(g.mem_total_mb / 1024).toFixed(1)} GB` : '–', g ? `${pct(g.mem_used_mb / g.mem_total_mb, 0)} used` : '')}
        ${kpi('Memory (RAM)', memUsed == null ? '–' : pct(memUsed, 0), `${gb(s.mem.available)} free of ${gb(s.mem.total)}`)}
        ${kpi('Disk', s.disk ? gb(s.disk.free) : '–', s.disk ? `free of ${gb(s.disk.total)}` : '')}
        ${kpi('Up for', up, esc(s.host))}
      </div>
      <div class="split">
        <div class="card"><h3>Temperature (°C)</h3>${hist(['cpu', 'gpu'], ['CPU', 'GPU'], (v) => v.toFixed(0))}</div>
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
    const [d, m, team] = await Promise.all([api('decisions'), metrics().catch(() => ({})), api('team').catch(() => [])]);
    const vals = d.filter((r) => r.type === 'decision' && r.logodds != null).map((r) => Number(r.logodds));
    const thr = m?.live?.ai_threshold, ai = m?.ai && !m.ai.error ? m.ai : null;
    const tone = (v) => pill(v ?? 'no view', v === 'bullish' || v === 'positive' || v === 'raised' ? 'ok' : v === 'bearish' || v === 'negative' || v === 'lowered' ? 'bad' : '');
    const num = (x, unit = '', d = 2) => (x?.q == null ? 'not found' : `${unit === 'M' ? '$' : ''}${fmt(x.q, d)}${unit}${x.prior != null ? ` <span class="muted">(a year ago ${fmt(x.prior, d)}, ${x.prior ? `${x.q >= x.prior ? '+' : ''}${pct(x.q / x.prior - 1, 0)}` : '–'})</span>` : ''}`);
    const pts = (xs) => (xs.length ? `<ul>${xs.map((x) => `<li>${esc(x.point)}${x.verified ? '' : ' <span class="muted">(quote not verified)</span>'}</li>`).join('')}</ul>` : '<p class="muted">none given</p>');
    const card = (r) => `<div class="team-card"><div class="team-head"><span class="tk">${esc(r.ticker)}</span><span class="muted">${esc(r.sector)} · ${esc(when(r.accepted_utc ? `${r.accepted_utc}Z` : null))}</span>
        ${thr != null && r.logodds >= thr ? pill('practice trade', 'lime') : pill('no trade')}${r.outcome?.excess != null ? pill(`result ${pct(r.outcome.excess)} vs sector`, r.outcome.excess > 0 ? 'ok' : 'bad') : pill('result due in a week', 'warn')}</div>
      <div class="agents">
        <div class="agent"><div class="who"><span>Reader agent · checked facts</span>${tone(r.reader.tone)}</div>
          Sales ${num(r.reader.revenue, 'M', 0)}<br>Profit per share ${num(r.reader.eps)}<br>Outlook: ${tone(r.reader.guidance)}
          ${r.reader.rejected.length ? `<br><span class="muted">${r.reader.rejected.length} number(s) thrown out: not found word-for-word</span>` : ''}</div>
        <div class="agent"><div class="who"><span>Summary agent · in testing</span>${tone(r.net_read?.read)}</div>
          ${r.net_read ? `Conviction: ${esc(r.net_read.conviction ?? '–')}<br>Margins: ${esc(r.net_read.fields.margin ?? '–')} · Demand: ${esc(r.net_read.fields.demand ?? '–')}<br>Earnings quality: ${esc(r.net_read.fields.earnings_quality ?? '–')}` : '<span class="muted">not run</span>'}</div>
        <div class="agent"><div class="who"><span>Bull case · in testing</span></div>${r.bull_bear ? pts(r.bull_bear.bull) : '<span class="muted">not run</span>'}</div>
        <div class="agent"><div class="who"><span>Bear case · in testing</span>${r.bull_bear ? pill(`debate verdict: ${r.bull_bear.read ?? 'none'}`, r.bull_bear.read === 'bullish' ? 'ok' : r.bull_bear.read === 'bearish' ? 'bad' : '') : ''}</div>${r.bull_bear ? pts(r.bull_bear.bear) : '<span class="muted">not run</span>'}</div>
        <div class="agent"><div class="who"><span>Aschenbrenner AI build-out view · in testing</span>${tone(r.ai_lens?.read)}</div>${r.ai_lens ? `AI exposure: ${esc(r.ai_lens.exposure ?? '–')}` : '<span class="muted">not run</span>'}</div>
        <div class="agent verdict"><div class="who"><span>Master judge · decides</span>${pill(r.logodds > 0 ? 'BUY' : 'PASS', r.logodds > 0 ? 'ok' : '')}</div>
          Confidence score <b>${fmt(r.logodds, 2)}</b>${thr != null ? `<br><span class="muted">needs ${esc(thr)} to trade</span>` : ''}</div>
      </div></div>`;
    const b = ai?.buckets || [];
    return `<h2>AI picks</h2><p class="lede">What the AI team thought of each new earnings report, and how well that has worked.</p>
      ${plain('When a big company reports earnings, a reader AI pulls out the key numbers and the judge AI (Bonsai) gives a confidence score that the stock will beat its industry group over the next week. Only scores above the threshold become a practice trade. The other agents give opinions that are scored in the background. This part is <b>still unproven</b>: it trades pretend money only.')}
      <div class="kpis">${kpi('Live decisions', vals.length)}${kpi('Above threshold', vals.filter((v) => thr != null && v >= thr).length, `threshold ${esc(thr ?? '–')}`)}${kpi('Missed', d.filter((r) => r.type === 'missed').length)}</div>
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

saveSettings();
const start = location.hash.slice(1);
show(views[start] ? start : views[settings.startPage] ? settings.startPage : 'overview');
window.addEventListener('hashchange', () => { const v = location.hash.slice(1); if (views[v]) show(v); });
sidebarState();
// Auto-reload: every page except the ones you read or type in. The System page ticks every 5 s so its charts move.
const NO_RELOAD = new Set(['settings', 'how', 'reviews']);
let lastReload = Date.now();
setInterval(() => {
  const due = current === 'system' ? 5 : settings.reloadSec;
  if (!settings.autoReload || NO_RELOAD.has(current) || document.querySelector('dialog[open]') || document.hidden) return;
  if (Date.now() - lastReload < due * 1000) return;
  lastReload = Date.now();
  if (current !== 'system') { metricsCache = null; sidebarState(); }
  show(current, true);
}, 1000);
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
