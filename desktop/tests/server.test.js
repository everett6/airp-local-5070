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
