// Server + data layer against a fake airp folder: auth, reading, and that actions need confirmation.
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { startServer } from '../src/server.js';

function fakeAirp() {
  const root = mkdtempSync(path.join(os.tmpdir(), 'airp-'));
  const fwd = path.join(root, 'backend', 'results', 'forward');
  mkdirSync(path.join(fwd, 'events'), { recursive: true });
  writeFileSync(path.join(fwd, 'AUTORUN_MODE'), 'live\n');
  writeFileSync(path.join(fwd, 'heartbeat.jsonl'), '{"job":"events","mode":"live","start":"2026-09-30T12:45:22+00:00","rc":0}\nnot json\n');
  writeFileSync(path.join(fwd, 'events', 'ledger.jsonl'), '{"type":"decision","ticker":"JBL","logodds":4.1}\n{"type":"run"}\n');
  writeFileSync(path.join(fwd, 'review_2026-10-03.md'), '# Review\n');
  writeFileSync(path.join(root, 'backend', 'results', 'trials_registry.jsonl'), '{"trial":"t1","result":"fail"}\n');
  return root;
}

async function session() {
  const s = await startServer({ airpRoot: fakeAirp() });
  const r = await fetch(s.launchUrl, { redirect: 'manual' });
  const cookie = r.headers.get('set-cookie').split(';')[0];
  const get = (p, init = {}) => fetch(`${s.origin}${p}`, { ...init, headers: { cookie, ...(init.headers || {}) } });
  return { s, get };
}

test('the API needs the launch token', async (t) => {
  const s = await startServer({ airpRoot: fakeAirp() });
  t.after(() => s.close());
  assert.equal((await fetch(`${s.origin}/api/overview`)).status, 401);
  assert.equal((await fetch(`${s.origin}/launch?t=wrong`, { redirect: 'manual' })).status, 403);
});

test('reads mode, runs, decisions, tests and reviews; skips torn lines', async (t) => {
  const { s, get } = await session();
  t.after(() => s.close());
  const o = await (await get('/api/overview')).json();
  assert.equal(o.mode, 'live'); assert.equal(o.halted, false); assert.equal(o.recentRuns.length, 1);
  const d = await (await get('/api/decisions')).json();
  assert.deepEqual(d.map((x) => x.ticker), ['JBL']);
  assert.equal((await (await get('/api/tests')).json())[0].trial, 't1');
  assert.equal((await (await get('/api/reviews')).json())[0].date, '2026-10-03');
  assert.equal((await get('/api/review?name=../../etc/passwd')).status, 404);
});

test('the kill switch needs the typed word and a reason; research flags are whitelisted', async (t) => {
  const { s, get } = await session();
  t.after(() => s.close());
  const post = (p, body) => get(p, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  assert.equal((await post('/api/halt', { reason: 'x', confirm: 'halt' })).status, 400);
  assert.equal((await post('/api/halt', { confirm: 'HALT' })).status, 400);
  assert.equal((await post('/api/resume', { confirm: 'yes' })).status, 400);
  const bad = await (await post('/api/research', { action: 'rm', name: 'x' })).json();
  assert.equal(bad.ok, false);
});

test('new panels: aggressive book, consensus, gate status, run history, last 24 hours', async (t) => {
  const root = fakeAirp();
  const be = path.join(root, 'backend'), fwd = path.join(be, 'results', 'forward');
  for (const d of ['allocator', 'consensus', 'broker', 'ai_picks']) mkdirSync(path.join(fwd, d), { recursive: true });
  mkdirSync(path.join(be, 'config'), { recursive: true });
  mkdirSync(path.join(be, 'results', 'events'), { recursive: true });
  const now = new Date().toISOString(), old = '2026-01-01T00:00:00+00:00';
  writeFileSync(path.join(be, 'config', 'mandate.json'), JSON.stringify({ books: { 'aggressive 2.5x': { max_gross: 2.5, alert_drawdown: 0.45, max_drawdown: 0.6 } } }));
  writeFileSync(path.join(fwd, 'allocator', 'ledger.jsonl'), [
    { data_through: '2026-10-05', books: { 'master+brakes': { equity: 100000 }, 'aggressive 2.5x': { equity: 100000, gross: 2.45, borrowed: 145000, interest_paid: 0 } } },
    { data_through: '2026-10-12', books: { 'master+brakes': { equity: 99000 }, 'aggressive 2.5x': { equity: 90000, gross: 2.6, borrowed: 144000, interest_paid: 140 } } },
  ].map((x) => JSON.stringify(x)).join('\n'));
  writeFileSync(path.join(fwd, 'broker', 'orders.json'), JSON.stringify({ a: { book: 'aggressive 2.5x', broker_scale: 0.664, broker_gross: 1.63,
    legs: [{ symbol: 'SPY', side: 'buy', qty: 3, status: 'filled', filled_at: now, filled_price: 700 }] } }));
  writeFileSync(path.join(fwd, 'consensus', 'ledger.jsonl'), JSON.stringify({ accession: 'x', ticker: 'JBL', entry_deadline: '2026-10-02T09:30:00-04:00',
    written_at: '2026-10-01T12:00:00+00:00', votes: { judge: 1 }, eq: 0.4, rw: 0.5, records: { judge: [3, 4] }, weights: { judge: 0.3 } }) + '\n');
  writeFileSync(path.join(fwd, 'events', 'ledger.jsonl'), [
    { type: 'decision', ticker: 'JBL', logodds: 4.1, written_at: now }, { type: 'decision', ticker: 'OLD', logodds: 1, written_at: old },
    { type: 'outcome', accession: 'x', fwd5: 0.02, written_at: now }].map((x) => JSON.stringify(x)).join('\n'));
  writeFileSync(path.join(fwd, 'ai_picks', 'book.json'), JSON.stringify({ threshold: 2.873, pairs: [] }));
  writeFileSync(path.join(fwd, 'alerts.jsonl'), JSON.stringify({ at: '2026-09-30T12:46:00+00:00', job: 'events', msg: 'during the run' }) + '\n'
    + JSON.stringify({ at: now, job: 'events', msg: 'fresh' }) + '\n');
  writeFileSync(path.join(fwd, 'heartbeat.jsonl'), JSON.stringify({ job: 'events', mode: 'live', start: '2026-09-30T12:45:22+00:00', end: '2026-09-30T12:47:22+00:00', rc: 1 }) + '\n');
  writeFileSync(path.join(be, 'results', 'events', 'warm_gdelt.jsonl'), [
    { accession: 'a', status: 'ok', n_asof: 5 }, { accession: 'b', status: 'none', n_asof: 0 }, { accession: 'c', status: 'error' },
    { accession: 'c', status: 'ok', n_asof: 0 }].map((x) => JSON.stringify(x)).join('\n'));
  const s = await startServer({ airpRoot: root });
  t.after(() => s.close());
  const cookie = (await fetch(s.launchUrl, { redirect: 'manual' })).headers.get('set-cookie').split(';')[0];
  const get = async (p) => (await fetch(`${s.origin}${p}`, { headers: { cookie } })).json();

  const a = await get('/api/aggressive');
  assert.equal(a.started, true); assert.equal(a.last.gross, 2.6); assert.equal(a.limits.max_drawdown, 0.6);
  assert.ok(Math.abs(a.drawdown - 0.1) < 1e-9); assert.equal(a.broker.gross, 1.63); assert.equal(a.series.frozen.length, 2);
  const c = await get('/api/consensus');
  assert.equal(c.recorded, 1); assert.equal(c.scored, 1); assert.deepEqual(c.agents[0], { agent: 'judge', hits: 3, calls: 4, weight: 0.3 });
  assert.equal(c.releases[0].on_time, true); assert.equal(c.releases[0].result, 0.02); assert.equal(c.agents[1].calls, 0);
  const x = await get('/api/research_extra');
  assert.deepEqual(x.b4b, { done: 3, total: 2851, with_news: 1, coverage: 1 / 3, needed: 0.5, errors: 1 });
  const r = await get('/api/runs');
  assert.equal(r.runs[0].seconds, 120); assert.deepEqual(r.runs[0].alerts, ['during the run']);
  const d = await get('/api/today');
  assert.deepEqual(d.decisions, [{ ticker: 'JBL', logodds: 4.1, trade: true }]);
  assert.equal(d.fills[0].symbol, 'SPY'); assert.equal(d.alerts.length, 1); assert.equal(d.outcomes.length, 1); assert.equal(d.runs.length, 0);
});

test('new panels on an empty airp folder do not fail', async (t) => {
  const { s, get } = await session();
  t.after(() => s.close());
  const a = await (await get('/api/aggressive')).json();
  assert.equal(a.started, false); assert.equal(a.last, null); assert.equal(a.limits, null);
  assert.equal((await (await get('/api/consensus')).json()).recorded, 0);
  assert.equal((await (await get('/api/research_extra')).json()).b4b.coverage, null);
  assert.equal((await (await get('/api/runs')).json()).runs.length, 1);
  assert.deepEqual((await (await get('/api/today')).json()).fills, []);
});

test('long-term picks and themes: newest cohort with reasons, results, and an empty state', async (t) => {
  const { s, get } = await session();
  t.after(() => s.close());
  const empty = await (await get('/api/longterm')).json();
  assert.equal(empty.picks, null); assert.equal(empty.themes, null); assert.deepEqual(empty.pickResults, []);
  const fwd = path.join(s.airpRoot, 'backend', 'results', 'forward');
  mkdirSync(path.join(fwd, 'longterm')); mkdirSync(path.join(fwd, 'themes'));
  const line = (o) => `${JSON.stringify(o)}\n`;
  writeFileSync(path.join(fwd, 'longterm', 'ledger.jsonl'),
    line({ type: 'cohort', month: '2026-10', made_on: '2026-10-01', tickers: ['AAA', 'BBB'], ratings: [5, 4], scores: [4.8, 4.1], candidates: 489, rating_counts: { 5: 3, 4: 40 } })
    + line({ type: 'result', month: '2026-10', entry: '2026-10-02', exit: '2027-01-04', basket: 0.05, spy: 0.02, priced: 10, excess_net: 0.026 }));
  writeFileSync(path.join(fwd, 'longterm', 'ratings_2026-10.jsonl'),
    line({ ticker: 'AAA', r12: 0.2, reason: 'orders up', bull: [{ point: 'backlog', quote: 'q', verified: true }], bear: [] }) + 'torn');
  writeFileSync(path.join(fwd, 'themes', 'ledger.jsonl'),
    line({ type: 'cohort', month: '2026-10', made_on: '2026-10-01', bubble_risk: 'elevated', picks: { medium: ['cyber'], long: [] }, baseline: { medium: ['semis', 'cyber'], long: ['quantum'] } }));
  writeFileSync(path.join(fwd, 'themes', 'ratings_2026-10.json'), JSON.stringify({ register: '=== Market risk register ===\nVIX: 16.3',
    risk: { bubble_risk: 'elevated', reason: 'capex is high', bull: [], bear: [] },
    themes: [{ key: 'cyber', label: 'Cybersecurity', horizon: 'medium', ai_linked: false, mom: 0.1, stats: { r12: 0.2 }, rating: 4, score: 4.2, reason: 'r', bull: [], bear: [] },
      { key: 'semis', label: 'Semiconductors', horizon: 'medium', ai_linked: true, mom: 0.7, stats: {}, rating: 3, score: 3.1 }] }));
  const d = await (await get('/api/longterm')).json();
  assert.equal(d.picks.month, '2026-10'); assert.equal(d.picks.candidates, 489);
  assert.deepEqual(d.picks.names.map((n) => [n.ticker, n.rating, n.r12, n.reason]), [['AAA', 5, 0.2, 'orders up'], ['BBB', 4, null, null]]);
  assert.equal(d.picks.names[0].bull[0].verified, true);
  assert.equal(d.pickResults[0].excess_net, 0.026); assert.equal(d.pickCohorts, 1);
  assert.equal(d.themes.bubble_risk, 'elevated'); assert.deepEqual(d.themes.register, ['VIX: 16.3']);
  assert.deepEqual(d.themes.rows.map((r) => [r.key, r.picked, r.baseline]), [['cyber', true, true], ['semis', false, true]]);
  assert.deepEqual(d.themeResults, []);
  // a cohort written in the last 24 hours shows on Home; an older one does not
  assert.equal((await (await get('/api/today')).json()).longterm, null);
  writeFileSync(path.join(fwd, 'themes', 'ledger.jsonl'),
    line({ type: 'cohort', month: '2026-11', written_at: new Date().toISOString(), bubble_risk: 'high', picks: { medium: ['cyber'], long: ['nuclear'] } }));
  const td = await (await get('/api/today')).json();
  assert.deepEqual(td.themes, { month: '2026-11', bubble_risk: 'high', picks: ['cyber', 'nuclear'] });
});

test('a file outside the public folder is not served', async (t) => {
  const { s, get } = await session();
  t.after(() => s.close());
  assert.equal((await get('/..%2fpackage.json')).status, 404);
  assert.equal((await get('/styles.css')).status, 200);
});

test('each release carries the fact sheet the judge read and what the SEC check dropped', async (t) => {
  const { s, get } = await session();
  t.after(() => s.close());
  const ev = path.join(s.airpRoot, 'backend', 'results', 'events');
  mkdirSync(ev, { recursive: true });
  writeFileSync(path.join(s.airpRoot, 'backend', 'results', 'forward', 'events', 'ledger.jsonl'), '{"type":"decision","accession":"a1","ticker":"MKC","logodds":-1.2}\n');
  writeFileSync(path.join(ev, 'decide_bonsai-27b_latest_forward_h5.jsonl'),
    `${JSON.stringify({ accession: 'a1', prompt_user: 'Company: MKC\nRevenue: not stated\nSEC cross-check: the release\'s revenue (17.4M as read) was dropped: it does not match the SEC-filed scale.' })}\n`);
  const [r] = await (await get('/api/team')).json();
  assert.match(r.sheet, /Revenue: not stated/);
  assert.deepEqual(r.dropped, ["the release's revenue (17.4M as read) was dropped: it does not match the SEC-filed scale."]);
});

test('open decisions come from docs/open_decisions.json; a missing or broken file is an empty list', async (t) => {
  const { s, get } = await session();
  t.after(() => s.close());
  assert.deepEqual(await (await get('/api/open_decisions')).json(), []);
  mkdirSync(path.join(s.airpRoot, 'docs'));
  writeFileSync(path.join(s.airpRoot, 'docs', 'open_decisions.json'), JSON.stringify([{ id: 'a', title: 'Restart', detail: 'd', kind: 'do' }, { title: 'Q' }, { nope: 1 }]));
  assert.deepEqual(await (await get('/api/open_decisions')).json(),
    [{ id: 'a', since: null, title: 'Restart', detail: 'd', kind: 'do' }, { id: '', since: null, title: 'Q', detail: '', kind: 'decide' }]);
  writeFileSync(path.join(s.airpRoot, 'docs', 'open_decisions.json'), '{not json');
  assert.deepEqual(await (await get('/api/open_decisions')).json(), []);
});

test('month-end Treasuries shadow: empty before the first month, then one row per month and the verdict', async (t) => {
  const { s, get } = await session();
  t.after(() => s.close());
  const w0 = await (await get('/api/month_end')).json();
  assert.equal(w0.months, 0); assert.equal(w0.mean_net, null); assert.deepEqual(w0.rows, []); assert.equal(w0.verdict, null);
  const dir = path.join(s.airpRoot, 'backend', 'results', 'forward', 'm1');
  mkdirSync(dir, { recursive: true });
  const lines = [{ type: 'month', month: '2026-10', days: ['2026-10-28', '2026-10-29', '2026-10-30'], net: 0.006, other_sum: -0.019, other_n: 19 },
    { type: 'month', month: '2026-11', days: ['2026-11-25', '2026-11-27', '2026-11-30'], net: -0.002, other_sum: 0.02, other_n: 16 }];
  writeFileSync(path.join(dir, 'ledger.jsonl'), lines.map((x) => JSON.stringify(x)).join('\n') + '\n{"type": "month", "mon');
  const w = await (await get('/api/month_end')).json();
  assert.equal(w.months, 2); assert.equal(w.hit_rate, 0.5); assert.ok(Math.abs(w.mean_net - 0.002) < 1e-12);
  assert.equal(w.rows[0].month, '2026-11'); assert.ok(Math.abs(w.rows[1].other_mean + 0.001) < 1e-12);
  writeFileSync(path.join(dir, 'ledger.jsonl'), JSON.stringify(lines[0]) + '\n' + JSON.stringify({ type: 'verdict', pass: false, sharpe: 0.31, diff_bp: 4.2, diff_lo80_bp: -1.1 }) + '\n');
  assert.deepEqual((await (await get('/api/month_end')).json()).verdict, { pass: false, sharpe: 0.31, diff_bp: 4.2, diff_lo80_bp: -1.1 });
});

test('records: every record is null or empty until its script has written it, then read as written', async (t) => {
  const { s, get } = await session();
  t.after(() => s.close());
  const r0 = await (await get('/api/records')).json();
  assert.deepEqual(r0, { funnel: null, contribution: null, throughput: null, account: null, evidence: [], benchmark: null });
  const be = path.join(s.airpRoot, 'backend', 'results'), fwd = path.join(be, 'forward');
  for (const d of [path.join(fwd, 'events', 'evidence'), path.join(fwd, 'account'), path.join(be, 'events')]) mkdirSync(d, { recursive: true });
  writeFileSync(path.join(fwd, 'funnel.json'), JSON.stringify({ at: 'x', stages: { eligible: 7, discovered: 7 }, stopped: [], rows: [{ big: 1 }] }));
  writeFileSync(path.join(fwd, 'account', 'view.json'), JSON.stringify({ equity: 100, alerts: [] }));
  writeFileSync(path.join(fwd, 'events', 'evidence', 'index.jsonl'), JSON.stringify({ accession: 'a-1', sha256: 'h', at: '2026-10-02T06:00:00+00:00' }) + '\n'
    + JSON.stringify({ accession: '../../x', sha256: 'h', at: 'y' }) + '\n{"cut off');
  writeFileSync(path.join(fwd, 'events', 'evidence', 'a-1.json'), JSON.stringify({ ticker: 'NKE', entry: { session: '2026-10-02' }, backfilled: true,
    fact_sheet: { text: 's', version: 2 }, ledger: { logodds: -2.86 }, code: { commit: '0123456789abcdef' } }));
  writeFileSync(path.join(be, 'events', 'bench_reader_live_score.json'), JSON.stringify({ fields: 65, right: 42, cases: 12, outcomes: { missed: 21 },
    rows: [{ ticker: 'MU', fields: { 'revenue.q': 'correct', 'revenue.prior': 'wrong_period', 'adj_eps.q': 'correct_absent' } }] }));
  const r = await (await get('/api/records')).json();
  assert.equal(r.funnel.stages.eligible, 7); assert.equal(r.funnel.rows, undefined); assert.equal(r.account.equity, 100);
  assert.deepEqual(r.evidence[1], { accession: 'a-1', at: '2026-10-02T06:00:00+00:00', ticker: 'NKE', session: '2026-10-02', backfilled: true, sheet_version: 2, has_sheet: true, logodds: -2.86, commit: '0123456' });
  assert.equal(r.evidence[0].ticker, null);  // an index line that names a path outside the folder is not opened
  assert.deepEqual(r.benchmark.rows, [{ ticker: 'MU', wrong: ['revenue.prior: wrong_period'] }]);
});

test('Jan paper decisions show their own reader and enriched judge evidence', async (t) => {
  const root = fakeAirp();
  const ev = path.join(root, 'backend', 'results', 'forward', 'events');
  mkdirSync(path.join(ev, 'evidence'), { recursive: true });
  writeFileSync(path.join(ev, 'ledger.jsonl'), JSON.stringify({ type: 'decision', accession: 'jan-case', ticker: 'AAA', pipeline: 'jan_bonsai_v1', source: 'bonsai' }) + '\n');
  writeFileSync(path.join(ev, 'extract.jsonl'), JSON.stringify({ accession: 'jan-case', model: 'wrong-baseline-reader', revenue: 999 }) + '\n');
  writeFileSync(path.join(ev, 'evidence', 'jan-case.json'), JSON.stringify({
    reader: { model: 'Jan', record: { revenue: 100, model: 'Jan' } },
    fact_sheet: { text: 'base sheet plus Jan research' },
    research: { model: 'Jan', record: { brief: { facts: [{ text: 'revenue 100', source: 'https://www.sec.gov/a.htm' }] } } },
  }));
  const s = await startServer({ airpRoot: root });
  t.after(() => s.close());
  const launch = await fetch(s.launchUrl, { redirect: 'manual' });
  const cookie = launch.headers.get('set-cookie').split(';')[0];
  const rows = await (await fetch(`${s.origin}/api/team`, { headers: { cookie } })).json();
  assert.equal(rows[0].reader.revenue, 100);
  assert.equal(rows[0].reader_model, 'Jan');
  assert.equal(rows[0].sheet, 'base sheet plus Jan research');
  assert.equal(rows[0].research.facts[0].text, 'revenue 100');
});

test('the Day window is validated before it is saved', async (t) => {
  const { createAirp } = await import('../src/airp.js');
  const { mkdtempSync, mkdirSync, readFileSync, rmSync } = await import('node:fs');
  const os = await import('node:os'); const path = await import('node:path');
  const root = mkdtempSync(path.join(os.tmpdir(), 'airp-day-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(path.join(root, 'backend', 'config'), { recursive: true });
  const airp = createAirp(root);
  assert.equal(airp.setDayWindow({ start: '09:00', end: '15:55' }).status, 400);
  assert.equal(airp.setDayWindow({ start: '10:00', end: '10:05' }).status, 400);
  assert.deepEqual(airp.setDayWindow({ start: '10:00', end: '14:00' }), { start: '10:00', end: '14:00' });
  assert.equal(JSON.parse(readFileSync(path.join(root, 'backend', 'config', 'autopilot_day.json'), 'utf8')).end, '14:00');
  assert.equal(airp.autopilotDay().live, false);
});
