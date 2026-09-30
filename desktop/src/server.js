// Local HTTP server for the airp desktop app: static UI + a small JSON API over airp's files (127.0.0.1 only).
// The UI gets a random token as an HttpOnly cookie on the launch URL; every /api call must carry it.
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { createReadStream, existsSync, statSync } from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createAirp } from './airp.js';

export const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.png': 'image/png' };

function send(res, status, body, type = 'application/json') {
  res.writeHead(status, { 'content-type': type, 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' });
  res.end(type === 'application/json' ? JSON.stringify(body) : body);
}

function readBody(req) {
  return new Promise((resolve) => {
    let data = '';
    req.on('data', (c) => { data += c; if (data.length > 10_000) req.destroy(); });
    req.on('end', () => { try { resolve(JSON.parse(data || '{}')); } catch { resolve({}); } });
  });
}

export async function startServer({ airpRoot, port = 0, publicDir = path.join(appRoot, 'public') } = {}) {
  const airp = createAirp(airpRoot);
  const token = randomBytes(24).toString('base64url');
  const ok = (req) => {
    const m = /(?:^|;\s*)airp_token=([^;]+)/.exec(req.headers.cookie || '');
    const got = Buffer.from(m?.[1] || '');
    const want = Buffer.from(token);
    return got.length === want.length && timingSafeEqual(got, want);
  };

  const routes = {
    'GET /api/overview': () => airp.overview(),
    'GET /api/books': () => airp.books(),
    'GET /api/decisions': () => airp.decisions(),
    'GET /api/health': () => airp.health(),
    'GET /api/research': () => airp.research(),
    'GET /api/tests': () => airp.tests(),
    'GET /api/reviews': () => airp.reviews(),
    'GET /api/info': () => ({ airpRoot, found: airp.exists() }),
    'GET /api/metrics': () => airp.metrics() ?? { missing: true },
    'POST /api/metrics': () => airp.rebuildMetrics(),
    'POST /api/halt': async (req) => {
      const { reason, confirm } = await readBody(req);
      if (confirm !== 'HALT' || !reason) return { status: 400, body: { error: 'type HALT and give a reason' } };
      return airp.halt(reason);
    },
    'POST /api/resume': async (req) => {
      const { confirm } = await readBody(req);
      if (confirm !== 'RESUME') return { status: 400, body: { error: 'type RESUME to confirm' } };
      return airp.resume();
    },
    'POST /api/research': async (req) => {
      const { action, name } = await readBody(req);
      return airp.researchFlag(action, name);
    },
  };

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    try {
      if (url.pathname === '/launch') {
        if (url.searchParams.get('t') !== token) return send(res, 403, { error: 'forbidden' });
        res.writeHead(302, { 'set-cookie': `airp_token=${token}; HttpOnly; SameSite=Strict; Path=/`, location: '/' });
        return res.end();
      }
      if (url.pathname.startsWith('/api/')) {
        if (!ok(req)) return send(res, 401, { error: 'unauthorized' });
        if (req.method === 'POST' && req.headers['content-type'] !== 'application/json') return send(res, 415, { error: 'json only' });
        if (url.pathname === '/api/review') {
          const text = airp.review(url.searchParams.get('name') || '');
          return text == null ? send(res, 404, { error: 'no such review' }) : send(res, 200, { text });
        }
        const handler = routes[`${req.method} ${url.pathname}`];
        if (!handler) return send(res, 404, { error: 'not found' });
        const out = await handler(req);
        return out?.status && out?.body ? send(res, out.status, out.body) : send(res, 200, out);
      }
      if (!ok(req)) return send(res, 401, 'open the app from its launcher', 'text/plain');
      const rel = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
      const file = path.join(publicDir, rel);
      if (!file.startsWith(publicDir) || !existsSync(file) || !statSync(file).isFile()) return send(res, 404, 'not found', 'text/plain');
      res.writeHead(200, { 'content-type': TYPES[path.extname(file)] || 'application/octet-stream', 'cache-control': 'no-store' });
      createReadStream(file).pipe(res);
    } catch (error) {
      send(res, 500, { error: String(error?.message || error).slice(0, 300) });
    }
  });

  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', resolve); });
  const origin = `http://127.0.0.1:${server.address().port}`;
  return { server, origin, launchUrl: `${origin}/launch?t=${token}`, close: () => new Promise((r) => { server.close(r); server.closeAllConnections(); }) };
}

// `node src/server.js` runs it without Electron (prints the launch URL).
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const s = await startServer({ airpRoot: process.env.AIRP_ROOT || path.resolve(appRoot, '..'), port: Number(process.env.AIRP_PORT || 8765) });
  console.log(`airp desktop: ${s.launchUrl}`);
}
