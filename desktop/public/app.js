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
        ? lineChart(names.slice(0, 4).map((n) => ({ name: n, x: b.series[n].map((r) => r.date), y: b.series[n].map((r) => r.equity) })), { yFmt: (v) => v.toFixed(0) })
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
  'Pre-registered': 'The test rules were written down and published before the test ran, so the result cannot be bent afterwards.',
};
const help = (term, text = GLOSS[term]) => (text ? `<span class="help" tabindex="0" data-tip="${esc(text)}">?</span>` : '');
const KPI_TERM = { 'Growth per year': 'CAGR', 'Quality score (Sharpe)': 'Sharpe', 'Worst fall': 'Max drawdown', 'Bumpiness (volatility)': 'Volatility', 'Max DD': 'Max drawdown', 'Sharpe (net)': 'Sharpe', 'SPY Sharpe': 'Sharpe', 'Above threshold': 'Pick threshold' };
const kpi = (label, value, sub = '') => `<div class="kpi"><div class="label">${esc(label)}${help(KPI_TERM[label] || label)}</div><div class="stat">${value}</div><div class="sub">${sub}</div></div>`;
const plain = (html) => `<div class="plain"><span class="plain-tag">In plain words</span><p>${html}</p></div>`;
const h3 = (title, term) => `<h3>${esc(title)}${help(term || title)}</h3>`;
const statsRow = (s) => kpi('Growth per year', pct(s.cagr)) + kpi('Quality score (Sharpe)', esc(s.sharpe)) + kpi('Bumpiness (volatility)', pct(s.vol)) + kpi('Worst fall', pct(s.max_dd)) + kpi('Period', `<span style="font-size:13px">${esc(s.start?.slice(0, 7))} → ${esc(s.end?.slice(0, 7))}</span>`);
const noMetrics = '<p class="empty">Chart data not built yet — press “Rebuild charts”.</p>';
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
    return `<h2>Strategy lab</h2><p class="lede">Trading ideas we tested, and whether they held up. ${passed} of ${tests.length} passed.</p>
      ${plain('Each row is one idea (day trading, calendar effects, pairs, crypto). The dot is its score after costs and the line is how uncertain that score is. To pass, the dot must be right of the dashed line <b>and</b> the whole line must be right of zero. Grey rows failed, so they do not trade.')}
      <div class="card wide">${h3('Score after costs, with its uncertainty range', '95% interval')}${ciChart(rows, { refs: [{ v: 0.5, label: 'pass line 0.5' }] })}</div>
      <div class="split">${curveCards}</div>
      <p class="muted">D9 passed but trades the official open auction, which is not tradable in practice; its tradable version (D10) failed.</p>`;
  },

  async signals() {
    const [d, m] = await Promise.all([api('decisions'), metrics().catch(() => ({}))]);
    const vals = d.filter((r) => r.type === 'decision' && r.logodds != null).map((r) => Number(r.logodds));
    const thr = m?.live?.ai_threshold;
    return `<h2>AI picks</h2><p class="lede">What the AI judge thought of each new earnings report.</p>
      ${plain('When a big company reports earnings, a reader AI pulls out the key numbers and the judge AI (Bonsai) gives a confidence score that the stock will beat its industry group over the next week. Only scores above the threshold become a practice trade. This part is <b>still unproven</b>: it is being tested with pretend money.')}
      <div class="kpis">${kpi('Live decisions', vals.length)}${kpi('Above threshold', vals.filter((v) => thr != null && v >= thr).length, `threshold ${esc(thr ?? '–')}`)}${kpi('Missed', d.filter((r) => r.type === 'missed').length)}</div>
      <div class="card wide">${h3('How confident the judge was (each bar counts reports)', 'Log-odds')}${histogram(vals, { mark: thr, markLabel: 'pick threshold', bins: 16 })}</div>
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
