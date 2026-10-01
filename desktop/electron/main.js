// airp desktop shell (same hardening as Lumina Studio): one window on the local server, no Node in the page.
import { app, BrowserWindow, dialog, shell } from 'electron';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { startServer } from '../src/server.js';

let srv = null;
let win = null;

// The packaging smoke test uses its own profile, so it can run while the real app is open.
const SMOKE = !!process.env.AIRP_SMOKE_SCREENSHOT;
if (SMOKE) app.setPath('userData', path.join(app.getPath('temp'), 'airp-smoke-profile'));
if (SMOKE) app.disableHardwareAcceleration();  // screenshots never touch the graphics card
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) app.quit();  // the open window is focused by its own 'second-instance' handler

// Where the airp checkout lives: $AIRP_ROOT, else the saved choice, else ~/airp-local-5070, else ask once.
function airpRoot() {
  const cfg = path.join(app.getPath('userData'), 'config.json');
  let saved = null;
  try { saved = JSON.parse(readFileSync(cfg, 'utf8')).airpRoot; } catch { /* first run */ }
  const candidates = [process.env.AIRP_ROOT, saved, path.join(app.getPath('home'), 'airp-local-5070')].filter(Boolean);
  const found = candidates.find((p) => existsSync(path.join(p, 'backend', 'results', 'forward')));
  if (found) return found;
  const pick = dialog.showOpenDialogSync({ title: 'Choose the airp-local-5070 folder', properties: ['openDirectory'] });
  if (!pick?.[0]) return null;
  writeFileSync(cfg, JSON.stringify({ airpRoot: pick[0] }));
  return pick[0];
}

function lockDown(contents, origin) {
  contents.setWindowOpenHandler(({ url }) => {
    if (/^https:\/\//.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  contents.on('will-navigate', (event, url) => { if (!url.startsWith(origin)) event.preventDefault(); });
  contents.on('will-attach-webview', (event) => event.preventDefault());
}

async function createWindow() {
  win = new BrowserWindow({
    width: 1400, height: 900, minWidth: 960, minHeight: 640, backgroundColor: '#11110f', title: 'airp', show: false,
    icon: path.join(app.getAppPath(), 'build', 'icon.png'),
    // the smoke test renders off-screen: no window appears, so it cannot steal focus or catch a stray click
    webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false, webSecurity: true, offscreen: SMOKE },
  });
  win.setMenuBarVisibility(false);
  lockDown(win.webContents, srv.origin);
  if (!SMOKE) win.once('ready-to-show', () => win.show());
  await win.loadURL(srv.launchUrl);
  if (process.env.AIRP_SMOKE_SCREENSHOT) {  // packaging smoke test: capture the window and exit
    await new Promise((r) => setTimeout(r, 3000));
    const view = process.env.AIRP_SMOKE_VIEW;  // optional: open one page first (e.g. "lab")
    if (/^[a-z]+$/.test(view || '')) {
      await win.loadURL(`${srv.origin}/#${view}`);
      await new Promise((r) => setTimeout(r, 4500));
    }
    const tab = process.env.AIRP_SMOKE_TAB;  // optional: a tab inside that page (e.g. "longterm" on Strategies)
    if (/^[a-z]+$/.test(tab || '')) {
      await win.webContents.executeJavaScript(`document.querySelector('[data-strat="${tab}"]')?.click(); 0`);
      await new Promise((r) => setTimeout(r, 1500));
    }
    const scroll = Number(process.env.AIRP_SMOKE_SCROLL || 0);  // optional: scroll the page before the capture
    if (scroll > 0) {
      await win.webContents.executeJavaScript(`document.querySelector('.main-area').scrollTop = ${scroll}; 0`);
      await new Promise((r) => setTimeout(r, 600));
    }
    writeFileSync(process.env.AIRP_SMOKE_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
    app.quit();
  }
}

app.on('second-instance', () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });

app.whenReady().then(async () => {
  if (!gotLock) return;
  const root = airpRoot();
  if (!root) { app.quit(); return; }
  try {
    srv = await startServer({ airpRoot: root });
  } catch (error) {
    dialog.showErrorBox('airp could not start', String(error?.stack || error));
    app.exit(1);
    return;
  }
  await createWindow().catch((e) => { console.error('airp: window failed', e); app.exit(1); });
});

app.on('window-all-closed', () => app.quit());
app.on('will-quit', () => { srv?.close(); });
