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
