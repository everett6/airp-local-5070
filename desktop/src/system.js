// Read-only machine stats for the System page: temperatures, load, memory, disk and the GPU. Nothing here changes
// any setting; it reads /proc, /sys and `nvidia-smi` only.
import { execFile } from 'node:child_process';
import { readdirSync, readFileSync, statfsSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const read = (p) => { try { return readFileSync(p, 'utf8').trim(); } catch { return null; } };
const cpuTimes = () => {
  const f = (read('/proc/stat') || '').split('\n')[0].split(/\s+/).slice(1).map(Number);
  return { idle: (f[3] || 0) + (f[4] || 0), total: f.reduce((a, v) => a + v, 0) };
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function sensors() {
  const out = [];
  let dirs = [];
  try { dirs = readdirSync('/sys/class/hwmon'); } catch { return out; }
  for (const d of dirs) {
    const base = path.join('/sys/class/hwmon', d), chip = read(path.join(base, 'name'));
    let files = [];
    try { files = readdirSync(base); } catch { continue; }
    for (const f of files.filter((x) => /^temp\d+_input$/.test(x))) {
      const v = Number(read(path.join(base, f)));
      if (!Number.isFinite(v) || v <= 0) continue;
      out.push({ chip, label: read(path.join(base, f.replace('_input', '_label'))) || f.replace('_input', ''), c: Math.round(v / 100) / 10 });
    }
  }
  return out;
}

function gpu() {
  const q = 'name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,fan.speed';
  return new Promise((resolve) => {
    execFile('nvidia-smi', [`--query-gpu=${q}`, '--format=csv,noheader,nounits'], { timeout: 5000 }, (err, stdout) => {
      if (err) return resolve(null);
      const [name, temp, util, used, total, power, fan] = stdout.trim().split('\n')[0].split(',').map((s) => s.trim());
      const n = (v) => (Number.isFinite(Number(v)) ? Number(v) : null);
      execFile('nvidia-smi', ['--query-compute-apps=process_name,used_memory', '--format=csv,noheader,nounits'], { timeout: 5000 }, (e2, apps) => {
        const procs = e2 ? [] : apps.trim().split('\n').filter(Boolean).map((l) => { const [p, m] = l.split(',').map((s) => s.trim()); return { name: path.basename(p), mb: n(m) }; });
        resolve({ name, temp_c: n(temp), util: n(util), mem_used_mb: n(used), mem_total_mb: n(total), power_w: n(power), fan: n(fan), procs });
      });
    });
  });
}

export async function systemStats(root) {
  const a = cpuTimes();
  const [g] = await Promise.all([gpu(), sleep(250)]);
  const b = cpuTimes();
  const mem = Object.fromEntries((read('/proc/meminfo') || '').split('\n').map((l) => l.split(':')).filter((x) => x.length === 2).map(([k, v]) => [k, parseInt(v, 10) * 1024]));
  const s = sensors(), find = (chip, label) => s.find((x) => x.chip === chip && (!label || x.label === label))?.c ?? null;
  let disk = null;
  try { const st = statfsSync(root); disk = { total: st.blocks * st.bsize, free: st.bavail * st.bsize }; } catch { /* not readable */ }
  return {
    at: new Date().toISOString(), host: os.hostname(), uptime_s: os.uptime(),
    cpu: { model: os.cpus()[0]?.model ?? null, cores: os.cpus().length, usage: b.total > a.total ? 1 - (b.idle - a.idle) / (b.total - a.total) : null,
      load: os.loadavg(), temp_c: find('k10temp', 'Tctl') ?? find('coretemp') ?? find('zenpower') },
    mem: { total: mem.MemTotal ?? null, available: mem.MemAvailable ?? null, swap_total: mem.SwapTotal ?? null, swap_free: mem.SwapFree ?? null },
    disk, gpu: g, sensors: s,
  };
}
