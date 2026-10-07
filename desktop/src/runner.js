// Fixed local actions; detached workers own execution and durable status across app restarts.
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { closeSync, existsSync, mkdirSync, openSync, readdirSync, readFileSync, readSync, renameSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';

const AUTO_ACTIONS = new Set(["paper_auto", "paper_auto_tech100", "full_auto"]);
// Autopilot Day runs beside Autopilot Night (same paper account, separate symbols); both are continuous workers.
const DAY_ACTIONS = new Set(["autopilot_day"]);
const CONTINUOUS = new Set([...AUTO_ACTIONS, ...DAY_ACTIONS]);
export const ACTIONS = [
  { id: "full_auto", label: "Autopilot Night: research + long/short swing book", paper: true, description: "Runs on its own in the SEPARATE Alpaca paper account: Jan and Bonsai research the 150 technology companies and the theme stocks (with saved research memory), and their bull and bear calls become long and short swing trades (5, 21 and 63 sessions) with the hedge, Kelly sizing, theme caps, stops, a 4% daily circuit breaker and a 35% drawdown limit. Paper money only. Scheduled jobs take priority." },
  { id: "autopilot_day", label: "Autopilot Day: algorithmic day trading", paper: true, description: "Price-only day trading (no AI) of 14 liquid ETFs in the SEPARATE Alpaca paper account, inside the hours you set (default 09:35-15:55 New York). 3x leverage for this book, 3.9x for the account, flat at the window's end, 4% circuit breaker and 5% daily stop, no trades on stale data. Its strategy failed its backtest; it trades because you switched it live." },
  { id: "paper_auto_tech100", label: "Auto-trade 100 technology stocks", paper: true, description: "Shorter continuous workflow: resume up to 100 companies classified Information Technology in the cached list. Same Jan/Bonsai research, paper sync and learning reviews. Separate saved queue; no downloads. Activates a persistent 100-stock entry allowlist for all AI paper runs. Existing exits and paired sector ETF hedges remain allowed. Day/short/medium/long budgets and cases are simulations; current five-day orders keep their existing sizing." },
  { id: "paper_auto", label: "Start continuous paper workflow", paper: true, description: "Resume up to 1,000 cached companies: Jan public-news research (10-minute maximum), Bonsai proposals, filing checks every 15 minutes and evidence review, and order sync every minute while idle. Scheduled jobs take priority. Proposals cannot change the trading mandate. Stop after current step; restart manually after reboot." },
  { id: 'horizon_review', label: 'Review horizon shadows', paper: false, description: 'Refresh free IEX price proxies for new day, medium and long virtual vintages. Compare AI and no-AI targets with fixed costs. No orders or automatic promotion.' },
  { id: 'institutional_report', label: 'Update engineering evidence', description: 'Summarize reviewed extraction labels, execution costs, factors, risk limits and recovery records.', paper: false },
  { id: 'release_check', label: 'Check and preserve release', description: 'Run backend and app tests against source hashes, then preserve a portable candidate. Independent review remains a separate step.', paper: false },
  { id: 'backup', label: 'Back up trading records', description: 'Create a hashed local archive of forward records. Copy the archive to separate storage for recovery after losing this PC.', paper: false },
  { id: 'restore_probe', label: 'Rehearse record restore', description: 'Verify the latest backup and restore its files into a separate folder.', paper: false },
  { id: 'rollback_probe', label: 'Rehearse release rollback', description: 'Verify and restore the latest release candidate into a separate folder for review.', paper: false },
  { id: 'budget_experiment', label: 'Test economic discipline', paper: false, description: 'Compare neutral and budget-aware Bonsai prompts on the same fresh Jan evidence with identical enforced limits. Save both arms, failures, timings and quote checks. Research only; no orders or automatic winner.' },
  { id: 'live_research_test', label: 'Live research test', paper: false, description: 'Fetch current public evidence with Jan, score day/medium/long theses with Bonsai, and report each company’s latency and bounded virtual capital proposals. Experimental targets are not broker orders.' },
  { id: 'paper_research_test', label: 'Live research + AI paper trades', paper: true, description: 'Run the fresh-research experiment, then the approved filing strategy and Alpaca paper order workflow. Only existing five-day filing picks can become orders; experimental horizon targets stay separate.' },
  { id: 'paper_ai', label: 'Run AI paper trades', paper: true, description: 'Jan reads and researches new filings, then Bonsai judges the source-checked fact sheets. Save evidence and submit eligible picks to the Alpaca paper account. The models run sequentially on the GPU; no new filings means neither model runs.' },
  { id: 'paper_sync', label: 'Sync paper orders', paper: true, description: 'Refresh and reconcile existing core and AI orders, submit pending paper orders, and save an account snapshot. While halted, the existing cancellation rules apply.' },
  { id: 'recovery_tests', label: 'Test trading recovery', paper: false, description: 'Check lost replies, partial fills, rejected hedges, and duplicate prevention using a simulated broker.' },
  { id: 'backend_tests', label: 'Run backend tests', paper: false, description: 'Run the Python regression suite on synthetic data.' },
  { id: 'desktop_tests', label: 'Run app tests', paper: false, description: 'Check the app API, authentication, and run controls using temporary folders.' },
  { id: 'improvement_review', label: 'Review self-improvement', paper: false, learning: true, description: 'Collect completed decision evidence and evaluate existing challengers. Qualify or retire shadows using the fixed evidence rules.' },
];
const ACTIVE = new Set(['starting', 'running']);
const json = (file, fallback = null) => { try { return JSON.parse(readFileSync(file, 'utf8')); } catch { return fallback; } };
function identity(pid) {
  try {
    const stat = readFileSync(`/proc/${pid}/stat`, 'utf8').split(')').at(-1).trim().split(/\s+/);
    if (stat[0] === 'Z') return '';
    return `${readFileSync('/proc/sys/kernel/random/boot_id', 'utf8').trim()}:${stat[19]}`;
  } catch { return ''; }
}
function tail(file) {
  let fd;
  try {
    const size = statSync(file).size, count = Math.min(size, 64 * 1024);
    fd = openSync(file, 'r');
    const buf = Buffer.alloc(count);
    readSync(fd, buf, 0, count, size - count);
    return (size > count ? '[Earlier output omitted]\n' : '') + buf.toString('utf8');
  } catch { return ''; }
  finally { if (fd !== undefined) closeSync(fd); }
}

export function createRunner(root, { launch = spawn } = {}) {
  const backend = path.join(root, 'backend'), dir = path.join(backend, 'results', 'desktop_runs');
  const py = path.join(backend, '.venv', 'bin', 'python');
  const worker = path.join(backend, 'scripts', 'desktop_run.py');
  const validId = (id) => typeof id === 'string' && /^[a-f0-9-]{36}$/.test(id);
  function job(id, output = false) {
    if (!validId(id)) return null;
    const p = path.join(dir, id), data = json(path.join(p, 'status.json'));
    if (!data) return null;
    if (ACTIVE.has(data.state)) {
      const alive = data.pid && data.identity && identity(data.pid) === data.identity;
      const starting = data.state === 'starting' && Date.now() - Date.parse(data.created_at) < 30_000;
      if (!alive && !starting) Object.assign(data, { state: 'interrupted', message: 'Worker stopped, possibly during a PC restart. Inspect orders and the log, then start a new run. Existing order IDs prevent duplicate submissions.' });
    }
    if (output) data.output = tail(path.join(p, 'output.log'));
    return data;
  }
  function status() {
    const jobs = (existsSync(dir) ? readdirSync(dir) : []).map((id) => job(id)).filter(Boolean)
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
    const fwd = path.join(backend, 'results', 'forward');
    let mode = 'dry';
    try { mode = readFileSync(path.join(fwd, 'AUTORUN_MODE'), 'utf8').trim(); } catch { /* default */ }
    const halted = existsSync(path.join(fwd, 'HALT'));
    const ready = existsSync(py) && existsSync(worker);
    return {
      actions: ACTIONS.map((a) => ({ ...a, disabled: !ready || jobs.some((j) => ACTIVE.has(j.state) && !CONTINUOUS.has(j.action)) || (AUTO_ACTIONS.has(a.id) && jobs.some((j) => ACTIVE.has(j.state) && AUTO_ACTIONS.has(j.action))) || (DAY_ACTIONS.has(a.id) && jobs.some((j) => ACTIVE.has(j.state) && DAY_ACTIONS.has(j.action))) || (a.paper && (mode !== 'live' || (['paper_ai', 'paper_research_test'].includes(a.id) && halted))) })),
      jobs: jobs.slice(0, 30), active: jobs.find((j) => ACTIVE.has(j.state) && !CONTINUOUS.has(j.action)) ?? null, autopilot: jobs.find((j) => ACTIVE.has(j.state) && AUTO_ACTIONS.has(j.action)) ?? null, day: jobs.find((j) => ACTIVE.has(j.state) && DAY_ACTIONS.has(j.action)) ?? null,
      stockPolicy: json(path.join(fwd, "paper_autopilot", "stock_policy.json")),
      researchQueue: (() => { const latest = jobs.find((j) => AUTO_ACTIONS.has(j.action)); const q = json(path.join(fwd, "paper_autopilot", latest?.action === "paper_auto_tech100" ? "queue_tech100.json" : "queue.json")); return q ? { total: q.companies.length, profile: q.profile || "all1000", snapshotYear: q.snapshot_year, recent: q.companies.filter((c) => c.state !== "pending").slice(-15) } : null; })(),
      ready, mode, halted, scheduled: json(path.join(fwd, 'running.json')),
    };
  }
  function start(action, confirm) {
    const spec = ACTIONS.find((a) => a.id === action);
    if (!spec) return { status: 400, body: { error: 'Unknown run action' } };
    if (spec.paper && confirm !== 'PAPER') return { status: 400, body: { error: 'Type PAPER to confirm paper orders' } };
    const current = status();
    if (!current.ready) return { status: 409, body: { error: 'Backend Python or the app worker is missing from this checkout' } };
    if (current.active) return { status: 409, body: { error: 'An app run is already active' } };
    if (current.actions.find((a) => a.id === action).disabled) return { status: 409, body: { error: 'Paper mode or the kill switch prevents this action' } };
    const id = randomUUID(), p = path.join(dir, id);
    mkdirSync(p, { recursive: true });
    const data = { id, action, label: spec.label, state: 'starting', created_at: new Date().toISOString(), steps: [], message: 'Checking the scheduler lock and runtime' };
    const save = () => { writeFileSync(path.join(p, "initial.tmp"), `${JSON.stringify(data)}\n`); renameSync(path.join(p, "initial.tmp"), path.join(p, "status.json")); };
    save();
    let fd;
    try {
      fd = openSync(path.join(p, 'output.log'), 'a');
      const child = launch(py, ['-u', worker, action, p, '--node', process.execPath], { cwd: backend, detached: true, stdio: ['ignore', fd, fd] });
      child.once('error', () => { Object.assign(data, { state: 'failed', message: 'The worker could not start', finished_at: new Date().toISOString() }); save(); });
      child.unref();
    } catch {
      Object.assign(data, { state: 'failed', message: 'The worker could not start', finished_at: new Date().toISOString() }); save();
    } finally { if (fd !== undefined) closeSync(fd); }
    return { status: 202, body: data };
  }
  function stop(id) {
    const data = job(id);
    if (!data || !CONTINUOUS.has(data.action) || !ACTIVE.has(data.state)) return { status: 409, body: { error: "No active continuous worker with this ID" } };
    writeFileSync(path.join(dir, id, "STOP"), "Stop requested\n");
    return { status: 202, body: { message: "Stop requested; current step finishes safely." } };
  }
  return { status, start, job, stop };
}
