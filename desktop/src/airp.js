// Reads airp's forward-test files and runs the few safe airp commands the app may trigger.
// Paper money only. Nothing here edits a ledger, a rule or the mandate; the only actions are airp's own kill switch
// (forward_allocator.py --halt / --resume) and pausing or resuming a research-queue job.
import { execFile } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';

export function createAirp(root) {
  const backend = path.join(root, 'backend');
  const fwd = path.join(backend, 'results', 'forward');
  const py = path.join(backend, '.venv', 'bin', 'python');

  const read = (p, fallback = null) => { try { return readFileSync(p, 'utf8'); } catch { return fallback; } };
  const json = (p, fallback = null) => { const t = read(p); if (t == null) return fallback; try { return JSON.parse(t); } catch { return fallback; } };
  const jsonl = (p) => (read(p, '') || '').split('\n').filter(Boolean).flatMap((l) => { try { return [JSON.parse(l)]; } catch { return []; } });

  function run(file, args = [], timeout = 120_000) {
    return new Promise((resolve) => {
      execFile(py, [path.join('scripts', file), ...args], { cwd: backend, timeout, maxBuffer: 8 << 20 },
        (error, stdout, stderr) => resolve({ ok: !error, code: error?.code ?? 0, stdout, stderr }));
    });
  }
  async function runJson(file, args = []) {
    const r = await run(file, args);
    try { return JSON.parse(r.stdout); } catch { return { error: (r.stderr || r.stdout || 'no output').slice(-400) }; }
  }
  function timers() {
    return new Promise((resolve) => {
      execFile('systemctl', ['--user', 'list-timers', 'airp-*', '--all', '--output=json'], { timeout: 10_000 },
        (error, stdout) => { try { resolve(JSON.parse(stdout)); } catch { resolve([]); } });
    });
  }

  return {
    root,
    exists: () => existsSync(fwd),

    async overview() {
      const beats = jsonl(path.join(fwd, 'heartbeat.jsonl'));
      const last = {};
      for (const b of beats) last[`${b.job}:${b.mode}`] = b;
      const halt = json(path.join(fwd, 'HALT'));
      return {
        mode: (read(path.join(fwd, 'AUTORUN_MODE'), 'dry') || 'dry').trim(),
        halted: halt != null || existsSync(path.join(fwd, 'HALT')),
        halt,
        lastRuns: Object.values(last).sort((a, b) => String(b.start).localeCompare(String(a.start))),
        recentRuns: beats.slice(-30).reverse(),
        alerts: jsonl(path.join(fwd, 'alerts.jsonl')).slice(-40).reverse(),
        digest: jsonl(path.join(fwd, 'digests.jsonl')).at(-1) ?? null,
        timers: await timers(),
      };
    },

    books() {
      const runs = jsonl(path.join(fwd, 'allocator', 'ledger.jsonl'));
      const names = [...new Set(runs.flatMap((r) => Object.keys(r.books || {})))];
      const series = Object.fromEntries(names.map((n) => [n, runs.filter((r) => r.books?.[n])
        .map((r) => ({ date: r.data_through, equity: r.books[n].equity }))]));
      const state = json(path.join(fwd, 'allocator', 'state.json'), {});
      const targets = Object.fromEntries(Object.entries(state || {}).map(([k, v]) => [k, { pending: v?.pending ?? null, decided_at: v?.decided_at ?? null }]));
      return { series, targets, aiPicks: json(path.join(fwd, 'ai_picks', 'book.json')), orders: json(path.join(fwd, 'broker', 'orders.json'), {}) };
    },

    decisions(limit = 80) {
      const led = jsonl(path.join(fwd, 'events', 'ledger.jsonl'));
      return led.filter((r) => r.type === 'decision' || r.type === 'missed').slice(-limit).reverse()
        .map((r) => ({ type: r.type, ticker: r.ticker, sector: r.sector, logodds: r.logodds, on_time: r.on_time,
          guidance: r.guidance, accepted_utc: r.accepted_utc, as_of: r.as_of, reason: r.reason }));
    },

    // Each live release with what every agent said about it: the reader's checked numbers, the three opinion agents
    // (scored in the background, not yet used) and the judge's score. Read-only joins of airp's own ledgers.
    team(limit = 30) {
      const ev = path.join(fwd, 'events');
      const by = (file) => Object.fromEntries(jsonl(path.join(ev, file)).map((r) => [r.accession, r]));
      const extract = by('extract.jsonl'), net = by('net_read.jsonl'), bb = by('bull_bear.jsonl'), lens = by('ai_lens.jsonl');
      const led = jsonl(path.join(ev, 'ledger.jsonl'));
      const outcome = Object.fromEntries(led.filter((r) => r.type === 'outcome').map((r) => [r.accession, r]));
      const pts = (xs) => (xs || []).slice(0, 3).map((x) => ({ point: x.point, quote: x.quote, verified: !!x.verified }));
      return led.filter((r) => r.type === 'decision').slice(-limit).reverse().map((r) => {
        const x = extract[r.accession] || {}, n = net[r.accession], b = bb[r.accession], l = lens[r.accession];
        return {
          accession: r.accession, ticker: r.ticker, sector: r.sector, accepted_utc: r.accepted_utc, source: r.source,
          logodds: r.logodds, on_time: r.on_time, guidance: r.guidance,
          reader: { revenue: x.revenue ?? null, eps: x.eps ?? null, adj_eps: x.adj_eps ?? null, guidance: x.guidance ?? null,
            tone: x.tone ?? null, highlights: (x.highlights || []).slice(0, 3), rejected: x.rejected || [], period_end: x.period_end ?? null },
          net_read: n ? { read: n.net_read, conviction: n.fields?.conviction ?? null, fields: n.fields || {} } : null,
          bull_bear: b ? { read: b.bb_read, bull: pts(b.bull), bear: pts(b.bear) } : null,
          ai_lens: l ? { read: l.ai_read, exposure: l.fields?.ai_exposure ?? null } : null,
          outcome: outcome[r.accession] ? { excess: outcome[r.accession].fwd5 ?? null } : null,
        };
      });
    },

    health: () => runJson('trading_health.py'),
    metrics() { return json(path.join(backend, 'results', 'desktop', 'metrics.json'), null); },
    rebuildMetrics: () => run('desktop_export.py', [], 600_000),
    research: () => runJson('research_queue.py', ['status']),

    tests() {
      const reg = jsonl(path.join(backend, 'results', 'trials_registry.jsonl'));
      return reg.map((r) => ({ trial: r.trial || r.name, date: r.date, kind: r.kind, result: r.result ?? r.verdict,
        sharpe: r.sharpe_ann ?? null, ic: r.ic ?? null, window: r.window ?? null })).reverse();
    },

    reviews() {
      if (!existsSync(fwd)) return [];
      return readdirSync(fwd).filter((f) => /^review_\d{4}-\d{2}-\d{2}\.md$/.test(f)).sort().reverse()
        .map((f) => ({ name: f, date: f.slice(7, 17), size: statSync(path.join(fwd, f)).size }));
    },
    review(name) {
      if (!/^review_\d{4}-\d{2}-\d{2}\.md$/.test(name)) return null;
      return read(path.join(fwd, name));
    },

    // --- the only actions ---
    halt: (reason) => run('forward_allocator.py', ['--halt', `desktop app: ${String(reason).slice(0, 200)}`]),
    resume: () => run('forward_allocator.py', ['--resume']),
    researchFlag: (action, name) => {
      if (!['pause', 'resume'].includes(action) || !/^[a-z0-9_]+$/.test(name)) return Promise.resolve({ ok: false, stderr: 'bad request' });
      return run('research_queue.py', [action, name]);
    },
  };
}
