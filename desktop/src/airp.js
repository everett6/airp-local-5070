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
      const series = Object.fromEntries(names.map((n) => [n, runs.filter((r) => r.books?.[n]?.equity != null)  // a failed week has no numbers
        .map((r) => ({ date: r.data_through, equity: r.books[n].equity }))]).filter(([, s]) => s.length));
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

    // The user's 2.5x paper book next to the frozen one: size, borrowing, and distance to its own limits.
    async aggressive() {
      const NAME = 'aggressive 2.5x';
      const runs = jsonl(path.join(fwd, 'allocator', 'ledger.jsonl'));
      const limits = json(path.join(backend, 'config', 'mandate.json'), {})?.books?.[NAME] ?? null;
      const pts = (n) => runs.filter((r) => r.books?.[n]).map((r) => ({ date: r.data_through, ...r.books[n] }));
      const all = pts(NAME), frozen = pts('master+brakes');
      const agg = all.filter((x) => x.equity != null);  // a run where this book failed has no numbers
      let peak = 0;
      for (const x of agg) peak = Math.max(peak, x.equity);
      const last = agg.at(-1) ?? null;
      const order = Object.values(json(path.join(fwd, 'broker', 'orders.json'), {}) || {}).filter((o) => o.book === NAME).at(-1) ?? null;
      const next = (await timers()).find((t) => String(t.unit || t.activates).startsWith('airp-allocator'))?.next ?? null;
      const slim = (xs) => xs.map((x) => ({ date: x.date, equity: x.equity }));
      return {
        name: NAME, limits, started: all.length > 0 && !!last, nextAllocator: next,
        last: last && { date: last.date, equity: last.equity, gross: last.gross ?? null, borrowed: last.borrowed ?? null,
          interest_paid: last.interest_paid ?? null, wiped_out: !!last.wiped_out, reducing: !!last.reducing },
        lastError: all.at(-1)?.error ?? null,
        drawdown: last && peak ? 1 - last.equity / peak : null,
        series: { aggressive: slim(agg), frozen: slim(frozen) },
        broker: order && { scale: order.broker_scale ?? null, gross: order.broker_gross ?? null },
      };
    },

    // The consensus shadow: each agent's vote per release, its record so far and its weight (no money).
    consensus(limit = 30) {
      const AGENTS = ['judge', 'net_read', 'ai_read', 'bb_read', 'guidance'];
      const lines = jsonl(path.join(fwd, 'consensus', 'ledger.jsonl'));
      const outcome = Object.fromEntries(jsonl(path.join(fwd, 'events', 'ledger.jsonl'))
        .filter((r) => r.type === 'outcome').map((r) => [r.accession, r.fwd5 ?? null]));
      const last = lines.at(-1);
      return {
        recorded: lines.length,
        scored: lines.filter((x) => new Date(x.written_at) < new Date(x.entry_deadline) && outcome[x.accession] != null).length,
        agents: AGENTS.map((a) => ({ agent: a, hits: last?.records?.[a]?.[0] ?? 0, calls: last?.records?.[a]?.[1] ?? 0, weight: last?.weights?.[a] ?? null })),
        releases: lines.slice(-limit).reverse().map((x) => ({ ticker: x.ticker, entry_deadline: x.entry_deadline, votes: x.votes, eq: x.eq, rw: x.rw,
          on_time: new Date(x.written_at) < new Date(x.entry_deadline), result: outcome[x.accession] ?? null })),
      };
    },

    // The monthly long-term picks and the theme track: the newest cohort with the AI's reasons, and every scored one.
    longterm() {
      const month = (c) => (/^\d{4}-\d{2}$/.test(String(c?.month)) ? c.month : null);
      const split = (dir) => {
        const recs = jsonl(path.join(fwd, dir, 'ledger.jsonl'));
        return { cohort: recs.filter((r) => r.type === 'cohort').at(-1) ?? null, cohorts: recs.filter((r) => r.type === 'cohort').length,
          results: recs.filter((r) => r.type === 'result') };
      };
      const pts = (xs) => (xs || []).slice(0, 3).map((x) => ({ point: x.point, quote: x.quote, verified: !!x.verified }));
      const why = (r) => ({ reason: r?.reason ?? null, bull: pts(r?.bull), bear: pts(r?.bear) });
      const lt = split('longterm'), th = split('themes');
      let picks = null, themes = null;
      if (lt.cohort) {
        const c = lt.cohort;
        const rated = Object.fromEntries((month(c) ? jsonl(path.join(fwd, 'longterm', `ratings_${c.month}.jsonl`)) : []).map((r) => [r.ticker, r]));
        picks = { month: c.month, made_on: c.made_on, candidates: c.candidates ?? null, rating_counts: c.rating_counts ?? {},
          names: (c.tickers || []).map((t, i) => ({ ticker: t, rating: c.ratings?.[i] ?? null, score: c.scores?.[i] ?? null,
            r12: rated[t]?.r12 ?? null, ...why(rated[t]) })) };
      }
      if (th.cohort) {
        const c = th.cohort;
        const doc = (month(c) ? json(path.join(fwd, 'themes', `ratings_${c.month}.json`), {}) : {}) || {};
        const picked = new Set(Object.values(c.picks || {}).flat()), base = new Set(Object.values(c.baseline || {}).flat());
        themes = { month: c.month, made_on: c.made_on, bubble_risk: c.bubble_risk ?? null, risk: why(doc.risk),
          register: String(doc.register || '').split('\n').slice(1).filter(Boolean),
          rows: (doc.themes || []).map((r) => ({ key: r.key, label: r.label, horizon: r.horizon, ai_linked: !!r.ai_linked, rating: r.rating,
            score: r.score ?? null, mom: r.mom ?? null, r12: r.stats?.r12 ?? null, picked: picked.has(r.key), baseline: base.has(r.key), ...why(r) })) };
      }
      return { picks, pickResults: lt.results.map((r) => ({ month: r.month, entry: r.entry, exit: r.exit, basket: r.basket, spy: r.spy, excess_net: r.excess_net })),
        pickCohorts: lt.cohorts, themes, themeCohorts: th.cohorts,
        themeResults: th.results.map((r) => ({ month: r.month, horizon: r.horizon, entry: r.entry, exit: r.exit, picks: r.picks || [],
          excess_net: r.excess_net, baseline_excess_net: r.baseline_excess_net })) };
    },

    // What the research queue's gate is waiting for: news coverage so far against the 50% it needs.
    researchExtra() {
      const warm = jsonl(path.join(backend, 'results', 'events', 'warm_gdelt.jsonl'));
      const done = new Map();
      for (const r of warm) if (r.status === 'ok' || r.status === 'none') done.set(r.accession, r);
      const withNews = [...done.values()].filter((r) => (r.n_asof ?? 0) >= 1).length;
      return {
        b4b: { done: done.size, total: 2851, with_news: withNews, coverage: done.size ? withNews / done.size : null, needed: 0.5,
          errors: warm.filter((r) => r.status === 'error').length },
        engine: process.env.AIRP_LABEL_ENGINE === 'ollama' ? 'Ollama, one report at a time' : 'llama-server, 3 reports at once',
      };
    },

    // Every scheduled run with the alerts it raised, newest first.
    async runs(limit = 200) {
      const alerts = jsonl(path.join(fwd, 'alerts.jsonl'));
      const beats = jsonl(path.join(fwd, 'heartbeat.jsonl')).slice(-limit).reverse();
      const ms = (x) => (x ? Date.parse(x) : NaN);
      return {
        runs: beats.map((b) => {
          const a = ms(b.start), z = ms(b.end) || a;
          return { job: b.job, mode: b.mode, start: b.start, end: b.end ?? null, rc: b.rc, seconds: b.end ? Math.round((z - a) / 1000) : null,
            missed_total: b.missed_total ?? null, gaps: b.gaps ?? null, skipped: b.skipped ?? null,
            alerts: alerts.filter((x) => x.job === b.job && ms(x.at) >= a && ms(x.at) <= z + 10_000).map((x) => x.msg) };
        }),
        timers: await timers(),
      };
    },

    // What changed in the last `hours` hours: decisions, practice trades, fills, alerts, test results, runs.
    today(hours = 24, now = Date.now()) {
      const since = now - hours * 3600_000;
      const fresh = (t) => t && Date.parse(t) >= since;
      const led = jsonl(path.join(fwd, 'events', 'ledger.jsonl'));
      const book = json(path.join(fwd, 'ai_picks', 'book.json'), {}) || {};
      const thr = book.threshold ?? null;
      const legs = (book.pairs || []).flatMap((p) => (p.legs || []).map((l) => ({ ...l, pair: p.ticker })))
        .concat(Object.values(json(path.join(fwd, 'broker', 'orders.json'), {}) || {}).flatMap((o) => (o.legs || []).map((l) => ({ ...l, pair: o.book }))));
      const day = new Date(since).toISOString().slice(0, 10);
      return {
        hours,
        decisions: led.filter((r) => r.type === 'decision' && fresh(r.written_at))
          .map((r) => ({ ticker: r.ticker, logodds: r.logodds, trade: thr != null && r.logodds >= thr })),
        missed: led.filter((r) => r.type === 'missed' && fresh(r.written_at)).length,
        outcomes: led.filter((r) => r.type === 'outcome' && fresh(r.written_at)).map((r) => ({ accession: r.accession, fwd5: r.fwd5 })),
        fills: legs.filter((l) => l.status === 'filled' && fresh(l.filled_at))
          .map((l) => ({ pair: l.pair, symbol: l.symbol, side: l.side, qty: l.qty, price: l.filled_price })),
        alerts: jsonl(path.join(fwd, 'alerts.jsonl')).filter((a) => fresh(a.at)).map((a) => ({ at: a.at, job: a.job, msg: a.msg })),
        tests: jsonl(path.join(backend, 'results', 'trials_registry.jsonl')).filter((r) => String(r.date) >= day)
          .map((r) => ({ trial: r.trial || r.name, result: r.result ?? r.verdict, sharpe: r.sharpe_ann ?? null })),
        runs: jsonl(path.join(fwd, 'heartbeat.jsonl')).filter((b) => fresh(b.start)).map((b) => ({ job: b.job, rc: b.rc, start: b.start })),
      };
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
