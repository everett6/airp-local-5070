import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { ACTIONS, createRunner } from '../src/runner.js';
import { startServer } from '../src/server.js';

function fixture(t) {
  const root = mkdtempSync(path.join(os.tmpdir(), 'airp-runner-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const backend = path.join(root, 'backend'), fwd = path.join(backend, 'results', 'forward');
  for (const p of [fwd, path.join(backend, '.venv', 'bin'), path.join(backend, 'scripts')]) mkdirSync(p, { recursive: true });
  writeFileSync(path.join(backend, '.venv', 'bin', 'python'), 'fake');
  writeFileSync(path.join(backend, 'scripts', 'desktop_run.py'), 'fake');
  writeFileSync(path.join(fwd, 'AUTORUN_MODE'), 'live');
  const calls = [];
  const child = new EventEmitter(); child.unref = () => {};
  const launch = (...args) => { calls.push(args); return child; };
  return { root, backend, fwd, calls, child, launch };
}

test('only allowlisted actions run, paper requires confirmation, and double starts are refused', (t) => {
  const f = fixture(t), r = createRunner(f.root, { launch: f.launch });
  assert.equal(r.start('rm -rf /', 'PAPER').status, 400);
  assert.equal(r.start('paper_ai', 'yes').status, 400);
  assert.equal(f.calls.length, 0);
  const started = r.start('paper_ai', 'PAPER');
  assert.equal(started.status, 202);
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0][1][2], 'paper_ai');
  assert.equal(f.calls[0][2].detached, true);
  assert.equal(f.calls[0][2].shell, undefined);
  assert.equal(r.start('backend_tests').status, 409);
  assert.equal(createRunner(f.root, { launch: f.launch }).status().active.id, started.body.id);
});

test('halt and dry mode block AI trading without changing either control; CPU tests remain available', (t) => {
  const f = fixture(t), r = createRunner(f.root, { launch: f.launch });
  writeFileSync(path.join(f.fwd, 'HALT'), '{}');
  assert.equal(r.start('paper_ai', 'PAPER').status, 409);
  writeFileSync(path.join(f.fwd, 'AUTORUN_MODE'), 'dry');
  assert.equal(r.start('paper_sync', 'PAPER').status, 409);
  assert.equal(readFileSync(path.join(f.fwd, 'AUTORUN_MODE'), 'utf8'), 'dry');
  assert.equal(r.start('recovery_tests').status, 202);
});

test('restarts retain results and identify a dead worker or reused PID as interrupted', (t) => {
  const f = fixture(t), r = createRunner(f.root, { launch: f.launch });
  const { body: job } = r.start('backend_tests');
  const p = path.join(f.backend, 'results', 'desktop_runs', job.id);
  writeFileSync(path.join(p, 'status.json'), JSON.stringify({ ...job, state: 'running', pid: process.pid, identity: 'old-boot:1' }));
  writeFileSync(path.join(p, 'output.log'), '<script>fake output</script>');
  const reopened = createRunner(f.root, { launch: f.launch });
  assert.equal(reopened.status().active, null);
  assert.equal(reopened.job(job.id, true).state, 'interrupted');
  assert.equal(reopened.job(job.id, true).output, '<script>fake output</script>');
  assert.equal(reopened.job('../../.env', true), null);
  writeFileSync(path.join(p, 'status.json'), JSON.stringify({ ...job, state: 'succeeded', code: 0 }));
  assert.equal(reopened.job(job.id).state, 'succeeded');
});

test('worker launch errors are durable failures and logs are bounded', (t) => {
  const f = fixture(t), r = createRunner(f.root, { launch: f.launch });
  const { body: job } = r.start('backend_tests');
  f.child.emit('error', new Error('fake executable failed'));
  assert.equal(r.job(job.id).state, 'failed');
  const p = path.join(f.backend, 'results', 'desktop_runs', job.id);
  writeFileSync(path.join(p, 'output.log'), 'a'.repeat(90_000) + 'THE END');
  const out = r.job(job.id, true).output;
  assert.ok(out.length < 66_000);
  assert.ok(out.endsWith('THE END'));
  assert.equal(r.start('desktop_tests').status, 202);
});

test('learning review is a bounded action with no order confirmation or arbitrary arguments', (t) => {
  const f = fixture(t), r = createRunner(f.root, { launch: f.launch });
  const action = r.status().actions.find((a) => a.id === 'improvement_review');
  assert.equal(action.paper, false);
  assert.equal(action.learning, true);
  assert.equal(r.start('improvement_review').status, 202);
  assert.equal(f.calls[0][1][2], 'improvement_review');
  assert.ok(!f.calls[0][1].includes('monthly'));
});

test('run endpoints enforce auth, same-origin JSON, confirmation and valid log IDs', async (t) => {
  const f = fixture(t);
  const s = await startServer({ airpRoot: f.root, runnerOptions: { launch: f.launch } });
  t.after(() => s.close());
  assert.equal((await fetch(`${s.origin}/api/operations`)).status, 401);
  assert.equal((await fetch(`${s.origin}/api/alpaca-account`)).status, 401);
  const cookie = (await fetch(s.launchUrl, { redirect: 'manual' })).headers.get('set-cookie').split(';')[0];
  const post = (body, headers = {}) => fetch(`${s.origin}/api/operations`, { method: 'POST', headers: { cookie, 'content-type': 'application/json', ...headers }, body: JSON.stringify(body) });
  assert.equal((await post({ action: 'paper_ai' })).status, 400);
  assert.equal((await post({ action: 'backend_tests' }, { origin: 'https://other.example' })).status, 403);
  assert.equal((await post({ action: 'backend_tests' }, { 'content-type': 'text/plain' })).status, 415);
  assert.equal((await post({ action: 'backend_tests; echo x' })).status, 400);
  const response = await post({ action: 'paper_ai', confirm: 'PAPER' }, { origin: s.origin });
  assert.equal(response.status, 202);
  const job = await response.json();
  const get = (url) => fetch(`${s.origin}${url}`, { headers: { cookie } });
  assert.equal((await get(`/api/operation?id=${job.id}`)).status, 200);
  assert.equal((await get('/api/operation?id=../../.env')).status, 404);
  assert.equal(f.calls.length, 1);
});

test('engineering actions preserve records without a paper order action', (t) => {
  for (const action of ['release_check', 'backup', 'restore_probe', 'rollback_probe', 'institutional_report']) {
    const f = fixture(t), r = createRunner(f.root, { launch: f.launch });
    assert.equal(r.status().actions.find((a) => a.id === action).paper, false);
    assert.equal(r.start(action).status, 202);
    assert.equal(f.calls[0][1][2], action);
  }
});

test('human reviews require authentication, same origin and explicit review words', async (t) => {
  const f = fixture(t), s = await startServer({ airpRoot: f.root, runnerOptions: { launch: f.launch } });
  t.after(() => s.close());
  assert.equal((await fetch(`${s.origin}/api/benchmark`)).status, 401);
  const cookie = (await fetch(s.launchUrl, { redirect: 'manual' })).headers.get('set-cookie').split(';')[0];
  const post = (route, body, extra = {}) => fetch(`${s.origin}/api/${route}`, { method: 'POST',
    headers: { cookie, 'content-type': 'application/json', ...extra }, body: JSON.stringify(body) });
  assert.equal((await post('benchmark', { reviewer: 'AI' })).status, 400);
  assert.equal((await post('benchmark', { confirm: 'VERIFIED' }, { origin: 'https://untrusted.example' })).status, 403);
  assert.equal((await post('engineering_review', { action: 'shell', confirm: 'REVIEWED' })).status, 400);
  assert.equal((await post('engineering_review', { action: 'release' })).status, 400);
  assert.equal(f.calls.length, 0);
});

test('research-only action has no paper confirmation; combined action requires PAPER', (t) => {
  const research = ACTIONS.find((a) => a.id === 'live_research_test');
  const combined = ACTIONS.find((a) => a.id === 'paper_research_test');
  assert.equal(research.paper, false);
  assert.equal(combined.paper, true);
  assert.match(combined.description, /experimental horizon targets stay separate/i);
});

test('economic discipline action launches without paper orders or confirmation', (t) => {
  const f = fixture(t), r = createRunner(f.root, { launch: f.launch });
  assert.equal(ACTIONS.find((a) => a.id === 'budget_experiment').paper, false);
  assert.equal(r.start('budget_experiment').status, 202);
  assert.equal(f.calls[0][1][2], 'budget_experiment');
});

test('horizon review is a research action without paper confirmation', (t) => {
  const f = fixture(t), r = createRunner(f.root, { launch: f.launch });
  assert.equal(ACTIONS.find((a) => a.id === 'horizon_review').paper, false);
  assert.equal(r.start('horizon_review').status, 202);
  assert.equal(f.calls[0][1][2], 'horizon_review');
});

test('continuous workflow has separate status, rejects duplicates, and stops cooperatively', (t) => {
  const f = fixture(t), r = createRunner(f.root, { launch: f.launch });
  assert.equal(r.start('paper_auto', 'yes').status, 400);
  const started = r.start('paper_auto', 'PAPER');
  assert.equal(started.status, 202);
  assert.equal(r.status().active, null);
  assert.equal(r.status().autopilot.id, started.body.id);
  assert.equal(r.start('paper_auto', 'PAPER').status, 409);
  assert.equal(r.stop('../../outside').status, 409);
  assert.equal(r.stop(started.body.id).status, 202);
  assert.ok(readFileSync(path.join(f.backend, 'results', 'desktop_runs', started.body.id, 'STOP'), 'utf8'));
  assert.equal(r.start('backend_tests').status, 202);
});

test('technology profile launches its own action and shares continuous-worker exclusion and stop', (t) => {
  const f = fixture(t), r = createRunner(f.root, { launch: f.launch });
  assert.equal(r.start('paper_auto_tech100', 'yes').status, 400);
  const started = r.start('paper_auto_tech100', 'PAPER');
  assert.equal(started.status, 202);
  assert.equal(f.calls[0][1][2], 'paper_auto_tech100');
  assert.equal(r.status().autopilot.id, started.body.id);
  assert.equal(r.start('paper_auto', 'PAPER').status, 409);
  assert.equal(r.stop(started.body.id).status, 202);
});

test('Autopilot Day runs beside Autopilot Night, but only one of each', (t) => {
  const f = fixture(t), r = createRunner(f.root, { launch: f.launch });
  assert.equal(r.start('full_auto', 'PAPER').status, 202);
  const night = r.status();
  assert.equal(night.actions.find((a) => a.id === 'autopilot_day').disabled, false);
  assert.equal(night.actions.find((a) => a.id === 'full_auto').disabled, true);
  assert.equal(r.start('autopilot_day', 'PAPER').status, 202);
  const both = r.status();
  assert.ok(both.day && both.autopilot && !both.active);
  assert.equal(both.actions.find((a) => a.id === 'autopilot_day').disabled, true);
  assert.equal(r.stop(both.day.id).status, 202);
});
