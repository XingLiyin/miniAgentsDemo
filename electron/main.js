'use strict';

const { app, BrowserWindow, Menu, dialog, shell, ipcMain, session } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');
const fs = require('fs');
const crypto = require('crypto');
const { resolveUpdateConfig, shouldCheckForUpdates, shouldReportTelemetry } = require('./lib/update-config');
const { planSeedMigration } = require('./lib/seed-migration');
const { createReporter } = require('./telemetry');
const { initUpdater } = require('./updater');

const PORT = parseInt(process.env.IPMASTER_COWORK_BACKEND_PORT || '15926', 10);
const BACKEND_URL = `http://localhost:${PORT}`;
const IS_DEV = !!process.env.ELECTRON_DEV;
const DEV_VITE_URL = `http://localhost:${process.env.VITE_PORT || '5173'}`;

// Built-in default update/telemetry server. Used when neither an env var
// (IPMASTER_COWORK_UPDATE_FEED_URL / _TELEMETRY_URL / _UPDATE_CHANNEL) nor
// %APPDATA%\IPMaster-Cowork\update-config.json overrides it.
// CHANGE THIS to the production intranet URL before a real release.
const DEFAULT_UPDATE_BASE = 'http://10.25.228.203:8077';

let mainWindow = null;
let backendProcess = null;
let electronLogStream = null;
let updateConfig = null;     // resolved update config
let telemetry = null;        // telemetry reporter
let autoUpdaterRef = null;   // active autoUpdater or null

// ── Paths ─────────────────────────────────────────────────────────────────────

function getBackendExePath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'backend', 'ipmaster-cowork.exe');
  }
  return path.join(__dirname, '..', 'build', 'dist', 'ipmaster-cowork', 'ipmaster-cowork.exe');
}

function getBundledResourcesPath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'backend', 'resources');
  }
  return path.join(__dirname, '..', 'resources');
}

function getAppDataDir() {
  return path.join(app.getPath('appData'), 'IPMaster-Cowork');
}

// One-time migration from the legacy NetLIVE-CoWork AppData dir to IPMaster-Cowork
// (rebrand). Runs before anything creates the new dir, so "new dir absent" is a
// reliable signal. Copies all user data and rewrites the migrated .env (env-var
// prefix + embedded paths) so the new backend reads the carried-over config.
function migrateLegacyAppData() {
  try {
    const newDir = getAppDataDir();
    const oldDir = path.join(app.getPath('appData'), 'NetLIVE-CoWork');
    if (fs.existsSync(newDir)) return;   // already migrated / fresh new-layout run
    if (!fs.existsSync(oldDir)) return;  // fresh install, nothing to migrate
    fs.cpSync(oldDir, newDir, { recursive: true });
    const envPath = path.join(newDir, '.env');
    if (fs.existsSync(envPath)) {
      const txt = fs.readFileSync(envPath, 'utf8')
        .replace(/NETLIVE_COWORK_/g, 'IPMASTER_COWORK_')
        .replace(/NetLIVE-CoWork/g, 'IPMaster-Cowork');
      fs.writeFileSync(envPath, txt, 'utf8');
    }
    process.stdout.write(`Migrated legacy AppData ${oldDir} -> ${newDir}\n`);
  } catch (e) {
    try { process.stdout.write('migrateLegacyAppData failed: ' + e.message + '\n'); } catch (_) {}
  }
}

function readUpdateConfigFile() {
  try {
    const p = path.join(getAppDataDir(), 'update-config.json');
    if (fs.existsSync(p)) return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch (e) { elog('readUpdateConfigFile failed: ' + e.message); }
  return {};
}

function getOrCreateInstallId() {
  const p = path.join(getAppDataDir(), 'install-id');
  try {
    if (fs.existsSync(p)) return fs.readFileSync(p, 'utf8').trim();
    const id = crypto.randomUUID();
    fs.mkdirSync(getAppDataDir(), { recursive: true });
    fs.writeFileSync(p, id, 'utf8');
    return id;
  } catch (e) { elog('getOrCreateInstallId failed: ' + e.message); return 'unknown'; }
}

function telemetryQueuePath() { return path.join(getAppDataDir(), 'telemetry-queue.json'); }
function loadTelemetryQueue() {
  try { const p = telemetryQueuePath(); if (fs.existsSync(p)) return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (_) {}
  return [];
}
function saveTelemetryQueue(q) {
  try { fs.writeFileSync(telemetryQueuePath(), JSON.stringify(q), 'utf8'); } catch (e) { elog('saveTelemetryQueue failed: ' + e.message); }
}

// ── Electron-side log file ────────────────────────────────────────────────────
// Captures backend stdout/stderr before Python's own logger starts.
// Written to %APPDATA%\IPMaster-Cowork\logs\electron.log

function openElectronLog() {
  try {
    const logsDir = path.join(getAppDataDir(), 'logs');
    fs.mkdirSync(logsDir, { recursive: true });
    const logPath = path.join(logsDir, 'electron.log');
    electronLogStream = fs.createWriteStream(logPath, { flags: 'a' });
    const ts = new Date().toISOString();
    electronLogStream.write(`\n${'='.repeat(60)}\n[${ts}] IPMaster-Cowork started\n`);
    return logPath;
  } catch (e) {
    return null;
  }
}

function elog(line) {
  const ts = new Date().toISOString();
  const msg = `[${ts}] ${line}\n`;
  if (electronLogStream) electronLogStream.write(msg);
  process.stdout.write(msg);
}

// ── .env bootstrap (first run) ────────────────────────────────────────────────

function ensureUserEnvFile() {
  const appDataDir = getAppDataDir();
  const envPath = path.join(appDataDir, '.env');

  if (!fs.existsSync(appDataDir)) {
    fs.mkdirSync(appDataDir, { recursive: true });
  }

  if (!fs.existsSync(envPath)) {
    const templatePath = app.isPackaged
      ? path.join(process.resourcesPath, 'backend', '.env.example')
      : path.join(__dirname, '..', '.env.example');

    const toUnix = (p) => p.replace(/\\/g, '/');
    const resourcesPath = toUnix(getBundledResourcesPath());

    let content = '';
    if (fs.existsSync(templatePath)) {
      content = fs.readFileSync(templatePath, 'utf8');
      content = content.replace(/^IPMASTER_COWORK_DATA_DIR=.*/m,           `IPMASTER_COWORK_DATA_DIR=${toUnix(path.join(appDataDir, 'data'))}`);
      content = content.replace(/^IPMASTER_COWORK_LOG_DIR=.*/m,            `IPMASTER_COWORK_LOG_DIR=${toUnix(path.join(appDataDir, 'logs'))}`);
      content = content.replace(/^IPMASTER_COWORK_SKILLS_DIR=.*/m,         `IPMASTER_COWORK_SKILLS_DIR=${toUnix(getUserSkillsDir())}`);
      content = content.replace(/^IPMASTER_COWORK_AGENTS_DIR=.*/m,         `IPMASTER_COWORK_AGENTS_DIR=${toUnix(getUserAgentsDir())}`);
      content = content.replace(/^IPMASTER_COWORK_WORKSPACE_BASE_DIR=.*/m, `IPMASTER_COWORK_WORKSPACE_BASE_DIR=${toUnix(path.join(appDataDir, 'workspace'))}`);
    } else {
      content = [
        `IPMASTER_COWORK_DATA_DIR=${toUnix(path.join(appDataDir, 'data'))}`,
        `IPMASTER_COWORK_SKILLS_DIR=${toUnix(getUserSkillsDir())}`,
        `IPMASTER_COWORK_AGENTS_DIR=${toUnix(getUserAgentsDir())}`,
        `IPMASTER_COWORK_LOG_DIR=${toUnix(path.join(appDataDir, 'logs'))}`,
      ].join('\n');
    }
    fs.writeFileSync(envPath, content, 'utf8');
    elog(`Created .env at ${envPath}`);
  }

  return envPath;
}

// ── Default config seeding (first run) ───────────────────────────────────────
// Copies bundled llm_configs / mcp_configs into AppData on first install.
// Only copies a subdir if it doesn't already exist — never overwrites user edits.

function seedDefaultData() {
  const defaultDataDir = app.isPackaged
    ? path.join(process.resourcesPath, 'default_data')
    : path.join(__dirname, '..', 'data');

  if (!fs.existsSync(defaultDataDir)) return;

  const appDataDir = path.join(getAppDataDir(), 'data');

  for (const subdir of ['llm_configs', 'mcp_configs']) {
    const src = path.join(defaultDataDir, subdir);
    const dst = path.join(appDataDir, subdir);

    if (!fs.existsSync(src)) continue;
    if (fs.existsSync(dst)) {
      elog(`${subdir} already exists in AppData, skipping seed`);
      continue;
    }

    try {
      fs.mkdirSync(dst, { recursive: true });
      for (const file of fs.readdirSync(src)) {
        if (!file.endsWith('.json')) continue;
        fs.copyFileSync(path.join(src, file), path.join(dst, file));
        elog(`Seeded ${subdir}/${file}`);
      }
    } catch (e) {
      elog(`Failed to seed ${subdir}: ${e.message}`);
    }
  }
}

function applyVersionAwareSeed() {
  const appDataDir = getAppDataDir();
  const markerPath = path.join(appDataDir, 'installed-version');
  let installedVersion = null;
  try { if (fs.existsSync(markerPath)) installedVersion = fs.readFileSync(markerPath, 'utf8').trim(); } catch (_) {}
  const currentVersion = app.getVersion();

  const defaultDataDir = app.isPackaged
    ? path.join(process.resourcesPath, 'default_data')
    : path.join(__dirname, '..', 'data');

  for (const subdir of ['llm_configs', 'mcp_configs']) {
    const src = path.join(defaultDataDir, subdir);
    const dst = path.join(appDataDir, 'data', subdir);
    if (!fs.existsSync(src)) continue;
    const bundled = fs.readdirSync(src).filter((f) => f.endsWith('.json'));
    const existing = fs.existsSync(dst) ? fs.readdirSync(dst).filter((f) => f.endsWith('.json')) : [];
    const { versionChanged, filesToCopy } = planSeedMigration({
      installedVersion, currentVersion, bundledConfigFiles: bundled, existingConfigFiles: existing,
    });
    if (!versionChanged) continue;
    fs.mkdirSync(dst, { recursive: true });
    for (const f of filesToCopy) {
      try {
        fs.copyFileSync(path.join(src, f), path.join(dst, f));
        elog(`seed(upgrade): ${subdir}/${f}`);
      } catch (e) { elog(`seed(upgrade): failed to copy ${subdir}/${f}: ${e.message}`); }
    }
  }
  try { fs.writeFileSync(markerPath, currentVersion, 'utf8'); } catch (e) { elog('write installed-version failed: ' + e.message); }
}

// User skills live in AppData (NOT the install dir) so they survive app updates
// (NSIS overwrites the install dir, which would otherwise wipe pulled/imported skills).
function getUserSkillsDir() { return path.join(getAppDataDir(), 'skills'); }
function getUserAgentsDir() { return path.join(getAppDataDir(), 'agents'); }

// Seed bundled <name> (skills / agents) into its AppData copy so content lives in
// AppData and survives app updates (NSIS overwrites the install dir).
// - overwrite=false (skills): missing-only — never clobbers user pulled/edited skills.
// - overwrite=true (agents): refresh bundled templates from the app on every launch
//   (they're app-shipped/canonical, so updates like default-template changes land),
//   while user-ADDED agent dirs (not in the bundle) are left untouched.
function seedBundledResource(name, { overwrite = false } = {}) {
  try {
    const src = path.join(getBundledResourcesPath(), name);
    const dst = path.join(getAppDataDir(), name);
    if (!fs.existsSync(src)) return;
    fs.mkdirSync(dst, { recursive: true });
    for (const entry of fs.readdirSync(src)) {
      const s = path.join(src, entry);
      try {
        if (!fs.statSync(s).isDirectory()) continue;
        const d = path.join(dst, entry);
        if (fs.existsSync(d)) {
          if (!overwrite) continue;                            // keep the user's version
          fs.rmSync(d, { recursive: true, force: true });      // refresh bundled template
        }
        fs.cpSync(s, d, { recursive: true });
        elog(`Seeded bundled ${name}: ${entry}`);
      } catch (e) { elog(`seedBundledResource(${name}): failed ${entry}: ${e.message}`); }
    }
  } catch (e) { elog(`seedBundledResource(${name}) failed: ${e.message}`); }
}

// ── Backend lifecycle ─────────────────────────────────────────────────────────

// Collected stderr lines for crash dialog
const stderrLines = [];

function startBackend() {
  const exePath = getBackendExePath();
  elog(`Backend exe: ${exePath}`);
  elog(`Exists: ${fs.existsSync(exePath)}`);

  if (!fs.existsSync(exePath)) {
    dialog.showErrorBox('IPMaster-Cowork — 启动失败', `找不到后端程序：\n${exePath}\n\n请重新安装应用。`);
    app.quit();
    return false;
  }

  const envFilePath = ensureUserEnvFile();
  elog(`Env file: ${envFilePath}`);

  backendProcess = spawn(exePath, [], {
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      IPMASTER_COWORK_BACKEND_PORT: String(PORT),
      IPMASTER_COWORK_ENV_FILE: envFilePath,
      // Force skills/agents to the AppData dirs (survive updates), overriding any .env value.
      IPMASTER_COWORK_SKILLS_DIR: getUserSkillsDir(),
      IPMASTER_COWORK_AGENTS_DIR: getUserAgentsDir(),
    },
    cwd: getAppDataDir(),
    windowsHide: true,
  });

  elog(`Backend PID: ${backendProcess.pid ?? 'none'}`);

  backendProcess.stdout.on('data', (d) => elog('[stdout] ' + d.toString().trimEnd()));
  backendProcess.stderr.on('data', (d) => {
    const text = d.toString().trimEnd();
    elog('[stderr] ' + text);
    stderrLines.push(text);
    if (stderrLines.length > 60) stderrLines.shift();
  });

  backendProcess.on('error', (err) => {
    elog(`[spawn-error] ${err.message}`);
    dialog.showErrorBox(
      'IPMaster-Cowork — 无法启动后端',
      `启动后端进程时出错：\n${err.message}\n\n日志文件：${path.join(getAppDataDir(), 'logs', 'electron.log')}`,
    );
  });

  backendProcess.on('exit', (code, signal) => {
    elog(`[exit] code=${code} signal=${signal}`);
    if (code !== null && code !== 0 && mainWindow && !mainWindow.isDestroyed()) {
      const logPath = path.join(getAppDataDir(), 'logs', 'electron.log');
      const lastLines = stderrLines.slice(-20).join('\n');
      dialog.showMessageBox(mainWindow, {
        type: 'error',
        title: 'IPMaster-Cowork — 后端异常退出',
        message: `后端进程退出（退出码 ${code}）`,
        detail: lastLines
          ? `最近输出：\n${lastLines}\n\n完整日志：${logPath}`
          : `完整日志：${logPath}`,
        buttons: ['打开日志', '关闭'],
        defaultId: 0,
      }).then(({ response }) => {
        if (response === 0) shell.openPath(logPath);
      });
    }
  });

  return true;
}

function stopBackend() {
  return new Promise((resolve) => {
    if (!backendProcess) { resolve(); return; }
    const proc = backendProcess;
    const pid = proc.pid;
    backendProcess = null;
    if (proc.exitCode !== null) { resolve(); return; }

    let done = false;
    const finish = () => { if (!done) { done = true; resolve(); } };
    proc.once('exit', finish);

    try { proc.kill('SIGTERM'); } catch (_) {}

    // Windows: if it hasn't exited in 5s, force-kill the process tree so the
    // exe file lock is released before NSIS overwrites it.
    setTimeout(() => {
      if (done) return;
      try {
        if (process.platform === 'win32' && pid) {
          spawn('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
        } else { proc.kill('SIGKILL'); }
      } catch (e) { elog('force kill failed: ' + e.message); }
      setTimeout(finish, 1500);
    }, 5000);
  });
}

// ── Backend readiness poll ────────────────────────────────────────────────────

function waitForBackend(maxAttempts = 60) {
  return new Promise((resolve, reject) => {
    let attempts = 0;

    const check = () => {
      const req = http.get(`${BACKEND_URL}/health`, { timeout: 1000 }, (res) => {
        res.resume();
        if (res.statusCode === 200) resolve();
        else retry();
      });
      req.on('error', retry);
      req.on('timeout', () => { req.destroy(); retry(); });
    };

    const retry = () => {
      // If the backend process has already exited, no point waiting
      if (backendProcess && backendProcess.exitCode !== null) {
        reject(new Error(`后端进程已退出（退出码 ${backendProcess.exitCode}）`));
        return;
      }
      if (++attempts >= maxAttempts) {
        const logPath = path.join(getAppDataDir(), 'logs', 'electron.log');
        reject(new Error(`后端 ${(maxAttempts * 0.5).toFixed(0)} 秒内未能启动。\n\n日志：${logPath}`));
      } else {
        setTimeout(check, 500);
      }
    };

    check();
  });
}

// ── Window ────────────────────────────────────────────────────────────────────

// Splash shown before the renderer (and its i18n) loads. The main process can't
// read the renderer's saved language choice, so follow the OS locale here.
function loadingHtml() {
  const zh = app.getLocale().toLowerCase().startsWith('zh');
  const text = zh ? '正在启动 IPMaster-Cowork…' : 'Starting IPMaster-Cowork…';
  return (
    'data:text/html;charset=utf-8,' +
    encodeURIComponent(
      // Match the React app's actual background (index.css --bg0) so launch
      // doesn't flash from dark splash to light app.
      '<html style="background:#f0f4fa;margin:0"><body style="display:flex;align-items:center;' +
      'justify-content:center;height:100vh;margin:0"><p style="color:#8aa3bf;font-family:' +
      'system-ui,sans-serif;font-size:15px">' + text + '</p></body></html>'
    )
  );
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    title: 'IPMaster-Cowork',
    // 窗口 / 任务栏图标，与界面内 logo (icon.svg) 同一品牌图
    icon: path.join(__dirname, 'assets', 'icon.ico'),
    show: false,
    // Same color as the React app's body (index.css --bg0) — prevents a
    // black flash before the renderer paints.
    backgroundColor: '#f0f4fa',
    // 隐藏原生标题栏（包含左上角的应用图标）；保留 min/max/close 控件作为 overlay
    titleBarStyle: 'hidden',
    titleBarOverlay: {
      color: '#f5f8fe',       // 跟顶部条 / 灰色边框 var(--bg2) 一致
      symbolColor: '#3d5a80', // 跟字色 var(--t2) 一致
      height: 36,             // 跟顶部条高度对齐
    },
    // 隐藏 File/Edit/View/Window/Help 原生菜单栏
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });

  // 移除应用菜单，连 Alt 键唤起也禁掉
  mainWindow.setMenuBarVisibility(false);

  // Re-register DevTools shortcuts manually — Menu.setApplicationMenu(null) below
  // wipes the default accelerators (F12 / Ctrl+Shift+I), making in-prod debugging
  // impossible without --remote-debugging-port=9222.
  mainWindow.webContents.on('before-input-event', (event, input) => {
    if (input.type !== 'keyDown') return;
    const isF12 = input.key === 'F12';
    const isCtrlShiftI = (input.control || input.meta) && input.shift && input.key.toLowerCase() === 'i';
    if (isF12 || isCtrlShiftI) {
      mainWindow.webContents.toggleDevTools();
      event.preventDefault();
    }
  });

  mainWindow.loadURL(loadingHtml());
  mainWindow.show();

  if (IS_DEV) {
    elog(`Dev mode: loading Vite dev server at ${DEV_VITE_URL}`);
    mainWindow.loadURL(DEV_VITE_URL);
    mainWindow.webContents.openDevTools();
    mainWindow.on('closed', () => { mainWindow = null; });
    return;
  }

  try {
    await waitForBackend();
    elog('Backend ready, loading UI');
    mainWindow.loadURL(BACKEND_URL);
  } catch (err) {
    elog(`waitForBackend failed: ${err.message}`);
    const logPath = path.join(getAppDataDir(), 'logs', 'electron.log');
    const lastLines = stderrLines.slice(-20).join('\n');
    const detail = lastLines
      ? `最近输出：\n${lastLines}\n\n完整日志：${logPath}`
      : `完整日志：${logPath}`;
    const { response } = await dialog.showMessageBox({
      type: 'error',
      title: 'IPMaster-Cowork — 启动失败',
      message: err.message,
      detail,
      buttons: ['打开日志文件', '退出'],
      defaultId: 0,
    });
    if (response === 0) shell.openPath(logPath);
    app.quit();
    return;
  }

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => { mainWindow = null; });

  autoUpdaterRef = initUpdater({
    config: updateConfig,
    isPackaged: app.isPackaged,
    logger: elog,
    onEvent: (payload) => {
      if (telemetry) {
        if (payload.status === 'available') telemetry.report('update_available', { target_version: payload.version }).catch(() => {});
        if (payload.status === 'downloaded') telemetry.report('update_download_completed', { target_version: payload.version }).catch(() => {});
        if (payload.status === 'error') telemetry.report('update_check_failed', { error: payload.message }).catch(() => {});
      }
      if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('update-status', payload);
    },
  });
  if (autoUpdaterRef && shouldCheckForUpdates(updateConfig)) {
    autoUpdaterRef.checkForUpdates().catch((e) => elog('checkForUpdates failed: ' + e.message));
  }
}

// ── App lifecycle ─────────────────────────────────────────────────────────────

function isBackendAlreadyRunning() {
  return new Promise((resolve) => {
    const req = http.get(`${BACKEND_URL}/health`, { timeout: 800 }, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

ipcMain.handle('open-path', async (_, p) => {
  await shell.openPath(p);
});

// Convert EMF/WMF (Windows vector metafiles, which Chromium cannot render in
// <img>) to PNG using the OS's built-in GDI+ via PowerShell System.Drawing.
// Input:  items = [{ key, b64 }]  (b64 = raw EMF/WMF bytes, base64)
// Output: [{ key, png }]          (png = base64 PNG, or null on failure)
// All items are converted in ONE PowerShell invocation to amortise its ~300ms
// startup. Renderer batches a whole deck's metafiles into a single call.
ipcMain.handle('convert-emf', async (_e, items) => {
  if (!Array.isArray(items) || items.length === 0) return [];
  const tmpDir = path.join(app.getPath('temp'), 'ipm-emf-' + crypto.randomBytes(6).toString('hex'));
  try {
    fs.mkdirSync(tmpDir, { recursive: true });
    const jobs = items.map((it, i) => {
      const inPath = path.join(tmpDir, `in${i}.emf`);
      const outPath = path.join(tmpDir, `out${i}.png`);
      try { fs.writeFileSync(inPath, Buffer.from(String(it.b64 || ''), 'base64')); } catch { /* skip */ }
      return { key: it.key, inPath, outPath };
    });
    const manifestPath = path.join(tmpDir, 'manifest.json');
    fs.writeFileSync(manifestPath, JSON.stringify(jobs.map((j) => ({ inPath: j.inPath, outPath: j.outPath }))), 'utf8');
    // Render each metafile at 2x onto a white background (PPT composites these
    // on the slide, usually white). Per-item try/catch so one bad file doesn't
    // abort the batch.
    const ps = [
      'Add-Type -AssemblyName System.Drawing',
      '$jobs = Get-Content -LiteralPath $env:IPM_EMF_MANIFEST -Raw | ConvertFrom-Json',
      'foreach ($j in $jobs) {',
      '  try {',
      '    $img = [System.Drawing.Image]::FromFile($j.inPath)',
      '    $w = [int]($img.Width * 2); $h = [int]($img.Height * 2)',
      '    if ($w -lt 1) { $w = 1 }; if ($h -lt 1) { $h = 1 }',
      '    if ($w -gt 4000) { $w = 4000 }; if ($h -gt 4000) { $h = 4000 }',
      '    $bmp = New-Object System.Drawing.Bitmap($w, $h)',
      '    $g = [System.Drawing.Graphics]::FromImage($bmp)',
      '    $g.Clear([System.Drawing.Color]::White)',
      '    $g.DrawImage($img, 0, 0, $w, $h)',
      '    $bmp.Save($j.outPath, [System.Drawing.Imaging.ImageFormat]::Png)',
      '    $g.Dispose(); $bmp.Dispose(); $img.Dispose()',
      '  } catch {}',
      '}',
    ].join('\n');
    const scriptPath = path.join(tmpDir, 'convert.ps1');
    fs.writeFileSync(scriptPath, ps, 'utf8');
    await new Promise((resolve) => {
      const child = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', scriptPath], {
        windowsHide: true,
        env: { ...process.env, IPM_EMF_MANIFEST: manifestPath },
      });
      const timer = setTimeout(() => { try { child.kill(); } catch { /* ignore */ } resolve(); }, 30000);
      child.on('exit', () => { clearTimeout(timer); resolve(); });
      child.on('error', () => { clearTimeout(timer); resolve(); });
    });
    return jobs.map((j) => {
      try { return { key: j.key, png: fs.readFileSync(j.outPath).toString('base64') }; }
      catch { return { key: j.key, png: null }; }
    });
  } catch (e) {
    elog('convert-emf error: ' + (e && e.message));
    return items.map((it) => ({ key: it.key, png: null }));
  } finally {
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch { /* ignore */ }
  }
});

ipcMain.handle('app-version', () => app.getVersion());

ipcMain.handle('select-directory', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory'],
    title: '选择工作目录',
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle('update-check', async () => {
  if (autoUpdaterRef) { try { await autoUpdaterRef.checkForUpdates(); } catch (e) { elog('manual check failed: ' + e.message); } }
});

ipcMain.handle('update-install', async () => {
  if (!autoUpdaterRef) return;   // updater inactive (dev mode or no feed configured)
  // stopBackend() kills the tracked backend by PID, releasing the exe lock so
  // NSIS can overwrite during install.
  //
  // Do NOT taskkill /IM ipmaster-cowork.exe here: image-name matching is
  // case-insensitive on Windows, and the backend ('ipmaster-cowork.exe') collides
  // with the Electron app ('IPMaster-Cowork.exe') — so /IM would kill THIS app
  // before quitAndInstall runs, aborting the update. (Orphan backends reused
  // from a prior session are a separate, rarer case to handle by port/PID.)
  await stopBackend();
  // Silent install (NSIS /S) + relaunch.
  autoUpdaterRef.quitAndInstall(true, true);
});

app.whenReady().then(async () => {
  // Windows 任务栏图标分组标识：与 appId 一致，确保任务栏使用我们的图标（含 dev 模式）
  if (process.platform === 'win32') {
    app.setAppUserModelId('com.ipmaster-cowork.desktop');
  }

  // 全局移除应用菜单（File/Edit/View/Window/Help）
  Menu.setApplicationMenu(null);

  // Rebrand: carry over data from the legacy NetLIVE-CoWork AppData dir.
  // Must run before openElectronLog (which would create the new dir).
  migrateLegacyAppData();

  openElectronLog();
  elog(`Electron version: ${process.versions.electron}`);
  elog(`App path: ${app.getAppPath()}`);
  elog(`Resources: ${process.resourcesPath}`);
  seedDefaultData();
  applyVersionAwareSeed();
  seedBundledResource('skills');
  seedBundledResource('agents', { overwrite: true });

  updateConfig = resolveUpdateConfig({
    env: process.env,
    configFile: readUpdateConfigFile(),
    defaults: { feedUrl: DEFAULT_UPDATE_BASE, telemetryUrl: DEFAULT_UPDATE_BASE, channel: 'stable' },
  });
  if (shouldReportTelemetry(updateConfig)) {
    telemetry = createReporter({
      endpoint: updateConfig.telemetryUrl,
      context: {
        installId: getOrCreateInstallId(), appVersion: app.getVersion(),
        channel: updateConfig.channel, os: process.platform, arch: process.arch,
        now: () => new Date().toISOString(),  // server expects ts as ISO date-time string
      },
      loadQueue: loadTelemetryQueue, saveQueue: saveTelemetryQueue,
    });
    telemetry.report('app_launch').catch(() => {});
  }

  // Clear renderer chunk cache on every version change. Prevents Chromium from
  // holding onto a stale index.html that references hashed JS chunks no longer
  // on disk after an OTA update (which manifests as "Failed to fetch
  // dynamically imported module" when the user opens any code-split route).
  try {
    const versionFile = path.join(getAppDataDir(), 'last-version');
    const currentVersion = app.getVersion();
    let priorVersion = null;
    try { priorVersion = fs.readFileSync(versionFile, 'utf8').trim(); } catch {}
    if (priorVersion !== currentVersion) {
      try { fs.writeFileSync(versionFile, currentVersion, 'utf8'); }
      catch (e) { elog('persist last-version failed: ' + e.message); }
      // Clear unconditionally on any version change — INCLUDING the first run
      // after upgrading from a version that predates this last-version file
      // (priorVersion === null). That first upgrade is exactly when a stale
      // index.html from the old build is still cached; skipping it (the old
      // `if (priorVersion)` guard) left the very upgrade that introduced this
      // mechanism uncleared. On a genuine fresh install there's no cache to
      // clear, so this is harmless.
      await session.defaultSession.clearCache();
      elog(`Cleared renderer cache on version change ${priorVersion || '(fresh/legacy)'} -> ${currentVersion}`);
    }
  } catch (e) { elog('cache-clear-on-upgrade failed: ' + e.message); }

  if (!IS_DEV) {
    const alreadyRunning = await isBackendAlreadyRunning();
    if (alreadyRunning) {
      elog('Port already in use and healthy — reusing existing backend');
    } else {
      if (!startBackend()) return;
    }
  }

  createWindow();
});

app.on('window-all-closed', async () => {
  await stopBackend();
  app.quit();
});

app.on('before-quit', () => {
  // Best-effort synchronous safety net. The graceful, awaited stop happens in
  // window-all-closed and the update-install IPC handler; this only fires a
  // synchronous SIGTERM for quit paths that bypass those, then closes the log.
  if (backendProcess) { try { backendProcess.kill('SIGTERM'); } catch (_) {} }
  if (electronLogStream) electronLogStream.end();
});
