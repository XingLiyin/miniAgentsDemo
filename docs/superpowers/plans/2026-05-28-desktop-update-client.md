# Desktop Update — Client (Part A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add electron-updater-based self-update and update-lifecycle telemetry reporting to the NetLIVE-CoWork desktop app, validated end-to-end on a single machine via a localhost feed.

**Architecture:** All update logic lives in the Electron main process. Pure, side-effect-free logic (config/channel resolution, version-aware seed diff, telemetry event/queue) is extracted into CommonJS modules under `electron/lib/` and unit-tested with Node's built-in `node:test`. The electron-updater wiring, backend-stop coordination, preload IPC, and React UI are integration glue validated by the single-machine update loop (Task 11).

**Tech Stack:** Electron 34 (CommonJS main process), electron-updater, Node built-in `node:test`, React 19 + Vite (renderer), generic update provider (localhost static server for validation).

**Scope note:** This plan covers Part A (client) of `docs/superpowers/specs/2026-05-28-desktop-auto-update-design.md`. Part B (management service) is a separate plan. During validation the feed and telemetry endpoints are localhost stand-ins per spec §13①.

---

## Status (updated 2026-05-29)

**Validated end-to-end on branch `feat/desktop-update-client`** (20 commits, base `master`, NOT merged — held pending server-side P1).

- **Tasks 1–10: DONE** (subagent-driven; per-task + final holistic review; a final review caught packaging blockers — modules missing from electron-builder `files`, `electron-updater` in `devDependencies`).
- **Automated:** 17/17 `node:test` pass; `node --check main.js` + `npx tsc -b` clean.
- **Task 11 (E2E): DONE.** Stage A (client, localhost feed) and Stage B (real management service, localhost:8077) both PASS — stable update 0.1.2→0.1.3, beta update 0.1.3→0.1.4, telemetry landed, release mgmt + promote + admin auth + stats verified.

**Bugs found & fixed during E2E (committed):** taskkill self-kill (case-insensitive `netlive-cowork.exe` vs `NetLIVE-CoWork.exe` — caused "restart to update" to hang); silent install `quitAndInstall(true,true)`; splash charset+OS-locale; i18n fallback en + button polish + up-to-date auto-dismiss; telemetry `ts` → ISO string; built-in `DEFAULT_UPDATE_BASE` (placeholder `http://localhost:8077` — change before release).

**Open before finalizing (held by user 2026-05-29):**
- [ ] Server-side P1 (parallel session): server generates/corrects feed `url`+`sha512`+`size` from the stored artifact, so publishers can upload raw electron-builder output (currently the publisher must hand-rewrite the yml to `artifacts/<file>`). Re-verify "upload raw artifacts" once P1 lands.
- [x] Set `DEFAULT_UPDATE_BASE` to the real intranet URL → `http://10.25.228.203:8077`.
- [ ] Finish the branch (merge/PR) via superpowers:finishing-a-development-branch.
- [ ] (optional) client emit `update_download_started` (server enum has it; client doesn't send → `started:0`).

## Post-plan work on this branch (rebrand + hardening, 2026-05-29)

Beyond this plan, the same branch now also carries the **IPMaster Cowork rebrand** and a hardening batch (current build **0.1.8**, unpushed). Authoritative details are in memory `desktop-update-system-status`. Summary:
- Full rebrand NetLIVE CoWork → IPMaster Cowork (appId, env prefix `IPMASTER_COWORK_`, AppData dir, exe/dist names) + one-time AppData migration. **Requires a real PyInstaller backend rebuild** — the fast path (rename exe + swap frontend_dist) does NOT carry Python changes.
- skills + agents moved to `%APPDATA%\IPMaster-Cowork\{skills,agents}` (survive updates); no default skills shipped; default MCP `tech-kb-mcp` (`http://10.25.228.203:8000/mcp/`) shipped + added to the default agent template; Skill Market cards fixed-height.
- Servers: skill `http://10.25.228.203:8080/api`, update `http://10.25.228.203:8077` (HTTP).
- **Security:** stopped bundling dev `data/llm_configs` (leaked a glm API key in 0.1.0–0.1.7 / published 0.1.4–0.1.6); added root `.gitignore`. Rotate the key if any old build leaked externally. Don't distribute old `Setup 0.1.4–0.1.7.exe`.
- Deferred: integrate `upstream/master` UX-optimize merge (`2881228`) after it lands on master (heavy file overlap).

---

### Task 1: Test infra + electron-updater dependency

**Files:**
- Modify: `electron/package.json`
- Create: `electron/lib/.gitkeep`
- Create: `electron/test/.gitkeep`

- [ ] **Step 1: Add electron-updater dep and a test script**

In `electron/package.json`, add `"test": "node --test test/"` to `scripts`, and add electron-updater to `devDependencies` (it is bundled into the app via `node_modules/**/*` already in `build.files`):

```json
  "scripts": {
    "start": "electron .",
    "dev": "cross-env ELECTRON_DEV=1 electron .",
    "build": "electron-builder build --win",
    "build:dir": "electron-builder build --win --dir",
    "test": "node --test test/"
  },
  "devDependencies": {
    "cross-env": "^10.1.0",
    "electron": "^34.3.0",
    "electron-builder": "^25.1.8",
    "electron-updater": "^6.3.9"
  },
```

- [ ] **Step 2: Install**

Run: `cd electron && npm install`
Expected: `electron-updater` and its dep `electron-log` appear under `electron/node_modules`; exit code 0.

- [ ] **Step 3: Create empty lib/ and test/ dirs**

Create `electron/lib/.gitkeep` and `electron/test/.gitkeep` (empty files) so the directories exist.

- [ ] **Step 4: Verify the test runner works (empty run)**

Run: `cd electron && npm test`
Expected: node test runner prints `tests 0` / `pass 0` and exits 0 (no test files yet).

- [ ] **Step 5: Commit**

```bash
git add electron/package.json electron/package-lock.json electron/lib/.gitkeep electron/test/.gitkeep
git commit -m "desktop: add electron-updater dep and node:test runner"
```

---

### Task 2: Update-config resolver (pure)

Resolves feed URL / channel / telemetry URL with precedence env > config file > bundled defaults (spec §A3).

**Files:**
- Create: `electron/lib/update-config.js`
- Test: `electron/test/update-config.test.js`

- [ ] **Step 1: Write the failing test**

```js
// electron/test/update-config.test.js
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const { resolveUpdateConfig, shouldCheckForUpdates, shouldReportTelemetry } = require('../lib/update-config');

test('env overrides config file overrides defaults', () => {
  const cfg = resolveUpdateConfig({
    env: { NETLIVE_COWORK_UPDATE_FEED_URL: 'http://env' },
    configFile: { feedUrl: 'http://file', channel: 'beta' },
    defaults: { feedUrl: 'http://default', channel: 'stable' },
  });
  assert.strictEqual(cfg.feedUrl, 'http://env');
  assert.strictEqual(cfg.channel, 'beta');
});

test('falls back to config file then defaults', () => {
  const cfg = resolveUpdateConfig({
    env: {},
    configFile: { telemetryUrl: 'http://file-telemetry' },
    defaults: { feedUrl: 'http://default' },
  });
  assert.strictEqual(cfg.feedUrl, 'http://default');
  assert.strictEqual(cfg.telemetryUrl, 'http://file-telemetry');
});

test('unknown channel normalizes to stable', () => {
  const cfg = resolveUpdateConfig({ env: {}, configFile: { channel: 'weird' }, defaults: {} });
  assert.strictEqual(cfg.channel, 'stable');
});

test('missing feed/telemetry yields empty strings and false guards', () => {
  const cfg = resolveUpdateConfig({ env: {}, configFile: {}, defaults: {} });
  assert.strictEqual(cfg.feedUrl, '');
  assert.strictEqual(cfg.telemetryUrl, '');
  assert.strictEqual(shouldCheckForUpdates(cfg), false);
  assert.strictEqual(shouldReportTelemetry(cfg), false);
});

test('guards true when urls present', () => {
  const cfg = resolveUpdateConfig({ env: { NETLIVE_COWORK_UPDATE_FEED_URL: 'http://x', NETLIVE_COWORK_TELEMETRY_URL: 'http://y' }, configFile: {}, defaults: {} });
  assert.strictEqual(shouldCheckForUpdates(cfg), true);
  assert.strictEqual(shouldReportTelemetry(cfg), true);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd electron && node --test test/update-config.test.js`
Expected: FAIL — `Cannot find module '../lib/update-config'`.

- [ ] **Step 3: Write minimal implementation**

```js
// electron/lib/update-config.js
'use strict';

// Pure resolution of update configuration. No fs/env/electron access here;
// callers pass already-read env vars, parsed config-file object, and bundled defaults.
// Precedence: env > configFile > defaults.
function resolveUpdateConfig({ env = {}, configFile = {}, defaults = {} } = {}) {
  const pick = (envKey, fileKey) =>
    env[envKey] || configFile[fileKey] || defaults[fileKey] || '';
  const channelRaw = pick('NETLIVE_COWORK_UPDATE_CHANNEL', 'channel') || 'stable';
  return {
    feedUrl: pick('NETLIVE_COWORK_UPDATE_FEED_URL', 'feedUrl'),
    telemetryUrl: pick('NETLIVE_COWORK_TELEMETRY_URL', 'telemetryUrl'),
    channel: channelRaw === 'beta' ? 'beta' : 'stable',
  };
}

function shouldCheckForUpdates(cfg) { return !!(cfg && cfg.feedUrl); }
function shouldReportTelemetry(cfg) { return !!(cfg && cfg.telemetryUrl); }

module.exports = { resolveUpdateConfig, shouldCheckForUpdates, shouldReportTelemetry };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd electron && node --test test/update-config.test.js`
Expected: PASS — `pass 5`, `fail 0`.

- [ ] **Step 5: Commit**

```bash
git add electron/lib/update-config.js electron/test/update-config.test.js
git commit -m "desktop: add pure update-config resolver"
```

---

### Task 3: Version-aware seed-migration diff (pure)

Computes which bundled default config files are missing and which `.env` keys are absent, plus whether the app version changed (spec §A6).

**Files:**
- Create: `electron/lib/seed-migration.js`
- Test: `electron/test/seed-migration.test.js`

- [ ] **Step 1: Write the failing test**

```js
// electron/test/seed-migration.test.js
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const { planSeedMigration } = require('../lib/seed-migration');

test('same version still reports missing items but versionChanged=false', () => {
  const r = planSeedMigration({
    installedVersion: '0.1.0', currentVersion: '0.1.0',
    bundledConfigFiles: ['a.json', 'b.json'], existingConfigFiles: ['a.json'],
    bundledEnvKeys: ['K1', 'K2'], existingEnvKeys: ['K1'],
  });
  assert.strictEqual(r.versionChanged, false);
  assert.deepStrictEqual(r.filesToCopy, ['b.json']);
  assert.deepStrictEqual(r.envKeysToAdd, ['K2']);
});

test('upgrade reports version change and missing items', () => {
  const r = planSeedMigration({
    installedVersion: '0.1.0', currentVersion: '0.1.1',
    bundledConfigFiles: ['a.json', 'b.json'], existingConfigFiles: ['a.json'],
    bundledEnvKeys: ['K1', 'K2', 'K3'], existingEnvKeys: ['K1', 'K2'],
  });
  assert.strictEqual(r.versionChanged, true);
  assert.deepStrictEqual(r.filesToCopy, ['b.json']);
  assert.deepStrictEqual(r.envKeysToAdd, ['K3']);
});

test('first install (no installed version) is a version change', () => {
  const r = planSeedMigration({
    installedVersion: null, currentVersion: '0.1.0',
    bundledConfigFiles: ['a.json'], existingConfigFiles: [],
    bundledEnvKeys: ['K1'], existingEnvKeys: [],
  });
  assert.strictEqual(r.versionChanged, true);
  assert.deepStrictEqual(r.filesToCopy, ['a.json']);
  assert.deepStrictEqual(r.envKeysToAdd, ['K1']);
});

test('nothing missing yields empty lists', () => {
  const r = planSeedMigration({
    installedVersion: '0.1.0', currentVersion: '0.1.1',
    bundledConfigFiles: ['a.json'], existingConfigFiles: ['a.json'],
    bundledEnvKeys: ['K1'], existingEnvKeys: ['K1'],
  });
  assert.deepStrictEqual(r.filesToCopy, []);
  assert.deepStrictEqual(r.envKeysToAdd, []);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd electron && node --test test/seed-migration.test.js`
Expected: FAIL — `Cannot find module '../lib/seed-migration'`.

- [ ] **Step 3: Write minimal implementation**

```js
// electron/lib/seed-migration.js
'use strict';

// Pure diff for version-aware seeding (spec §A6). No fs/electron access.
// Caller applies filesToCopy / envKeysToAdd when versionChanged is true
// (which also covers first install where installedVersion is null/absent).
function planSeedMigration({
  installedVersion = null,
  currentVersion,
  bundledConfigFiles = [],
  existingConfigFiles = [],
  bundledEnvKeys = [],
  existingEnvKeys = [],
} = {}) {
  const versionChanged = installedVersion !== currentVersion;
  const haveFile = new Set(existingConfigFiles);
  const haveEnv = new Set(existingEnvKeys);
  return {
    versionChanged,
    filesToCopy: bundledConfigFiles.filter((f) => !haveFile.has(f)),
    envKeysToAdd: bundledEnvKeys.filter((k) => !haveEnv.has(k)),
  };
}

module.exports = { planSeedMigration };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd electron && node --test test/seed-migration.test.js`
Expected: PASS — `pass 4`, `fail 0`.

- [ ] **Step 5: Commit**

```bash
git add electron/lib/seed-migration.js electron/test/seed-migration.test.js
git commit -m "desktop: add pure version-aware seed-migration diff"
```

---

### Task 4: Telemetry event builder + queue (pure)

Builds a telemetry event with common fields and manages a bounded offline queue (spec §A8, §C1).

**Files:**
- Create: `electron/lib/telemetry-core.js`
- Test: `electron/test/telemetry-core.test.js`

- [ ] **Step 1: Write the failing test**

```js
// electron/test/telemetry-core.test.js
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const { buildEvent, enqueue } = require('../lib/telemetry-core');

const ctx = {
  installId: 'iid-1', appVersion: '0.1.0', channel: 'stable',
  os: 'win32', arch: 'x64', now: () => 1700000000000,
};

test('buildEvent sets common fields and event_type', () => {
  const ev = buildEvent('app_launch', ctx);
  assert.deepStrictEqual(ev, {
    event_type: 'app_launch', install_id: 'iid-1', app_version: '0.1.0',
    channel: 'stable', os: 'win32', arch: 'x64', ts: 1700000000000,
  });
});

test('buildEvent merges extra fields', () => {
  const ev = buildEvent('update_download_failed', ctx, { error: 'boom' });
  assert.strictEqual(ev.event_type, 'update_download_failed');
  assert.strictEqual(ev.error, 'boom');
});

test('enqueue appends', () => {
  assert.deepStrictEqual(enqueue([], { a: 1 }), [{ a: 1 }]);
});

test('enqueue trims to maxLen keeping newest', () => {
  const q = enqueue(enqueue(enqueue([], { n: 1 }, 2), { n: 2 }, 2), { n: 3 }, 2);
  assert.deepStrictEqual(q, [{ n: 2 }, { n: 3 }]);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd electron && node --test test/telemetry-core.test.js`
Expected: FAIL — `Cannot find module '../lib/telemetry-core'`.

- [ ] **Step 3: Write minimal implementation**

```js
// electron/lib/telemetry-core.js
'use strict';

// Pure telemetry helpers (spec §A8, §C1). ctx.now() supplies the timestamp
// so tests are deterministic.
function buildEvent(eventType, ctx, extra = {}) {
  return {
    event_type: eventType,
    install_id: ctx.installId,
    app_version: ctx.appVersion,
    channel: ctx.channel,
    os: ctx.os,
    arch: ctx.arch,
    ts: ctx.now(),
    ...extra,
  };
}

function enqueue(queue, event, maxLen = 200) {
  const next = [...queue, event];
  return next.length > maxLen ? next.slice(next.length - maxLen) : next;
}

module.exports = { buildEvent, enqueue };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd electron && node --test test/telemetry-core.test.js`
Expected: PASS — `pass 4`, `fail 0`.

- [ ] **Step 5: Commit**

```bash
git add electron/lib/telemetry-core.js electron/test/telemetry-core.test.js
git commit -m "desktop: add pure telemetry event builder and queue"
```

---

### Task 5: Telemetry reporter (send + offline-queue drain)

Wraps the pure core with a sender that POSTs events and persists failures to a queue, draining on the next flush. `fetchImpl`, `loadQueue`, `saveQueue` are injected so the drain logic is unit-testable (spec §A8).

**Files:**
- Create: `electron/telemetry.js`
- Test: `electron/test/telemetry-reporter.test.js`

- [ ] **Step 1: Write the failing test**

```js
// electron/test/telemetry-reporter.test.js
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const { createReporter } = require('../telemetry');

const context = {
  installId: 'iid', appVersion: '0.1.0', channel: 'stable',
  os: 'win32', arch: 'x64', now: () => 1,
};

function memQueue() {
  let q = [];
  return { load: () => q, save: (v) => { q = v; }, current: () => q };
}

test('successful send leaves queue empty', async () => {
  const mq = memQueue();
  let posts = 0;
  const r = createReporter({
    endpoint: 'http://t', context,
    loadQueue: mq.load, saveQueue: mq.save,
    fetchImpl: async () => { posts += 1; return { ok: true }; },
  });
  await r.report('app_launch');
  assert.strictEqual(posts, 1);
  assert.deepStrictEqual(mq.current(), []);
});

test('failed send keeps event queued for later', async () => {
  const mq = memQueue();
  const r = createReporter({
    endpoint: 'http://t', context,
    loadQueue: mq.load, saveQueue: mq.save,
    fetchImpl: async () => { throw new Error('offline'); },
  });
  await r.report('app_launch');
  assert.strictEqual(mq.current().length, 1);
  assert.strictEqual(mq.current()[0].event_type, 'app_launch');
});

test('no endpoint is a no-op', async () => {
  const mq = memQueue();
  let posts = 0;
  const r = createReporter({
    endpoint: '', context, loadQueue: mq.load, saveQueue: mq.save,
    fetchImpl: async () => { posts += 1; return { ok: true }; },
  });
  await r.report('app_launch');
  assert.strictEqual(posts, 0);
  assert.deepStrictEqual(mq.current(), []);
});

test('flush drains a previously queued event once back online', async () => {
  const mq = memQueue();
  mq.save([{ event_type: 'app_launch', install_id: 'iid' }]);
  let posts = 0;
  const r = createReporter({
    endpoint: 'http://t', context,
    loadQueue: mq.load, saveQueue: mq.save,
    fetchImpl: async () => { posts += 1; return { ok: true }; },
  });
  await r.flush();
  assert.strictEqual(posts, 1);
  assert.deepStrictEqual(mq.current(), []);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd electron && node --test test/telemetry-reporter.test.js`
Expected: FAIL — `Cannot find module '../telemetry'`.

- [ ] **Step 3: Write minimal implementation**

```js
// electron/telemetry.js
'use strict';
const { buildEvent, enqueue } = require('./lib/telemetry-core');

// createReporter(deps) returns { report, flush }. Side effects (HTTP, queue
// persistence) are injected so drain logic is unit-testable. In production,
// main.js passes the global fetch and AppData-backed loadQueue/saveQueue.
function createReporter({ endpoint, context, loadQueue, saveQueue, fetchImpl = fetch }) {
  let queue = loadQueue() || [];

  async function flush() {
    if (!endpoint || queue.length === 0) return;
    const pending = queue;
    queue = [];
    for (const ev of pending) {
      try {
        await fetchImpl(`${endpoint}/events`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(ev),
        });
      } catch (_) {
        queue = enqueue(queue, ev);
      }
    }
    saveQueue(queue);
  }

  async function report(eventType, extra) {
    if (!endpoint) return;
    queue = enqueue(queue, buildEvent(eventType, context, extra));
    saveQueue(queue);
    await flush();
  }

  return { report, flush };
}

module.exports = { createReporter };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd electron && node --test test/telemetry-reporter.test.js`
Expected: PASS — `pass 4`, `fail 0`.

- [ ] **Step 5: Run the full electron test suite**

Run: `cd electron && npm test`
Expected: PASS — all 17 tests across the four files, `fail 0`.

- [ ] **Step 6: Commit**

```bash
git add electron/telemetry.js electron/test/telemetry-reporter.test.js
git commit -m "desktop: add telemetry reporter with offline-queue drain"
```

---

### Task 6: Updater module (electron-updater wiring)

Wires electron-updater against the resolved feed/channel and forwards events through a callback. Integration glue — validated in Task 11, not unit-tested (requires a packaged app + feed).

**Files:**
- Create: `electron/updater.js`

- [ ] **Step 1: Write the module**

```js
// electron/updater.js
'use strict';
const { autoUpdater } = require('electron-updater');

// initUpdater wires electron-updater. Returns autoUpdater when active, else null.
// - Only active in a packaged app (autoUpdater is a no-op/throws otherwise).
// - Only active when a feed URL is configured (spec §A3: no feed -> skip).
// - ALWAYS registers an 'error' handler (spec §A3: unhandled 'error' on the
//   EventEmitter would throw an uncaught exception).
function initUpdater({ config, isPackaged, onEvent, logger }) {
  const log = logger || (() => {});
  if (!isPackaged) { log('updater: skipped (not packaged)'); return null; }
  if (!config.feedUrl) { log('updater: skipped (no feedUrl configured)'); return null; }

  const channel = config.channel === 'beta' ? 'beta' : 'latest';
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.channel = channel;
  autoUpdater.setFeedURL({ provider: 'generic', url: config.feedUrl, channel });

  autoUpdater.on('checking-for-update', () => onEvent({ status: 'checking' }));
  autoUpdater.on('update-available', (info) => onEvent({ status: 'available', version: info && info.version }));
  autoUpdater.on('update-not-available', () => onEvent({ status: 'not-available' }));
  autoUpdater.on('download-progress', (p) => onEvent({ status: 'downloading', percent: Math.round(p && p.percent || 0) }));
  autoUpdater.on('update-downloaded', (info) => onEvent({ status: 'downloaded', version: info && info.version }));
  autoUpdater.on('error', (err) => { log('updater error: ' + String(err)); onEvent({ status: 'error', message: String(err && err.message || err) }); });

  log(`updater: initialized channel=${channel} feed=${config.feedUrl}`);
  return autoUpdater;
}

module.exports = { initUpdater };
```

- [ ] **Step 2: Smoke-check it loads (no crash on import in plain Node)**

Run: `cd electron && node -e "require('./updater'); console.log('ok')"`
Expected: prints `ok` (module imports without throwing; electron-updater is only invoked inside initUpdater).

- [ ] **Step 3: Commit**

```bash
git add electron/updater.js
git commit -m "desktop: add electron-updater wiring module"
```

---

### Task 7: main.js integration (config, updater, telemetry, seeding, backend stop)

Wire the new modules into the app lifecycle and strengthen backend shutdown for the install step (spec §A4, §A5, §A6).

**Files:**
- Modify: `electron/main.js`

- [ ] **Step 1: Add requires and config/state near the top**

After the existing requires (`electron/main.js:1-7`), add:

```js
const { resolveUpdateConfig, shouldCheckForUpdates, shouldReportTelemetry } = require('./lib/update-config');
const { planSeedMigration } = require('./lib/seed-migration');
const { createReporter } = require('./telemetry');
const { initUpdater } = require('./updater');
```

After `let backendProcess = null;` (`electron/main.js:15`), add:

```js
let updateConfig = null;     // resolved update config
let telemetry = null;        // telemetry reporter
let autoUpdaterRef = null;   // active autoUpdater or null
```

- [ ] **Step 2: Add config-resolution + installId helpers**

Add these functions (after `getAppDataDir`, `electron/main.js:34-36`):

```js
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
    const id = require('crypto').randomUUID();
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
```

- [ ] **Step 3: Replace stopBackend with an async, lock-safe version**

Replace the existing `stopBackend` (`electron/main.js:211-216`) with:

```js
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
    // exe file lock is released before NSIS overwrites it (spec §A5).
    setTimeout(() => {
      if (done) return;
      try {
        if (process.platform === 'win32' && pid) {
          spawn('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true });
        } else { proc.kill('SIGKILL'); }
      } catch (e) { elog('force kill failed: ' + e.message); }
      setTimeout(finish, 1500);
    }, 5000);
  });
}
```

- [ ] **Step 4: Apply version-aware seeding on version change**

Add this function and call it from `seedDefaultData`'s caller. First add the function (after `seedDefaultData`, `electron/main.js:108-138`):

```js
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
    for (const f of filesToCopy) { fs.copyFileSync(path.join(src, f), path.join(dst, f)); elog(`seed(upgrade): ${subdir}/${f}`); }
  }
  try { fs.writeFileSync(markerPath, currentVersion, 'utf8'); } catch (e) { elog('write installed-version failed: ' + e.message); }
}
```

Then, in `app.whenReady().then(...)` right after the existing `seedDefaultData();` call (`electron/main.js:376`), add:

```js
  applyVersionAwareSeed();

  updateConfig = resolveUpdateConfig({
    env: process.env,
    configFile: readUpdateConfigFile(),
    defaults: {},
  });
  if (shouldReportTelemetry(updateConfig)) {
    telemetry = createReporter({
      endpoint: updateConfig.telemetryUrl,
      context: {
        installId: getOrCreateInstallId(), appVersion: app.getVersion(),
        channel: updateConfig.channel, os: process.platform, arch: process.arch,
        now: () => Date.now(),
      },
      loadQueue: loadTelemetryQueue, saveQueue: saveTelemetryQueue,
    });
    telemetry.report('app_launch').catch(() => {});
  }
```

- [ ] **Step 5: Initialize the updater after the window is created**

At the end of `createWindow()` (after `mainWindow.on('closed', ...)`, `electron/main.js:333`), add:

```js
  autoUpdaterRef = initUpdater({
    config: updateConfig,
    isPackaged: app.isPackaged,
    logger: elog,
    onEvent: (payload) => {
      if (telemetry) {
        if (payload.status === 'available') telemetry.report('update_available', { target_version: payload.version }).catch(() => {});
        if (payload.status === 'downloading') { /* progress is noisy; not reported */ }
        if (payload.status === 'downloaded') telemetry.report('update_download_completed', { target_version: payload.version }).catch(() => {});
        if (payload.status === 'error') telemetry.report('update_check_failed', { error: payload.message }).catch(() => {});
      }
      if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('update-status', payload);
    },
  });
  if (autoUpdaterRef && shouldCheckForUpdates(updateConfig)) {
    autoUpdaterRef.checkForUpdates().catch((e) => elog('checkForUpdates failed: ' + e.message));
  }
```

- [ ] **Step 6: Add IPC handlers for manual check + install**

Near the other `ipcMain.handle` calls (`electron/main.js:349-361`), add:

```js
ipcMain.handle('update-check', async () => {
  if (autoUpdaterRef) { try { await autoUpdaterRef.checkForUpdates(); } catch (e) { elog('manual check failed: ' + e.message); } }
});

ipcMain.handle('update-install', async () => {
  await stopBackend();
  if (autoUpdaterRef) autoUpdaterRef.quitAndInstall(false, true);
});
```

- [ ] **Step 7: Make window-all-closed await backend stop**

Replace `app.on('window-all-closed', ...)` (`electron/main.js:390-393`) with:

```js
app.on('window-all-closed', async () => {
  await stopBackend();
  app.quit();
});
```

- [ ] **Step 8: Smoke-check main.js parses**

Run: `cd electron && node --check main.js`
Expected: no output, exit 0 (syntax valid).

- [ ] **Step 9: Commit**

```bash
git add electron/main.js
git commit -m "desktop: wire updater, telemetry, version-aware seeding, lock-safe backend stop"
```

---

### Task 8: Preload IPC bridge

Expose update controls and a status subscription to the renderer (spec §A2).

**Files:**
- Modify: `electron/preload.js`

- [ ] **Step 1: Extend the exposed API**

Replace `electron/preload.js` contents with:

```js
'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  selectDirectory: () => ipcRenderer.invoke('select-directory'),
  openPath: (p) => ipcRenderer.invoke('open-path', p),
  getVersion: () => ipcRenderer.invoke('app-version'),
  checkForUpdates: () => ipcRenderer.invoke('update-check'),
  installUpdate: () => ipcRenderer.invoke('update-install'),
  onUpdateStatus: (cb) => {
    const handler = (_e, payload) => cb(payload);
    ipcRenderer.on('update-status', handler);
    return () => ipcRenderer.removeListener('update-status', handler);
  },
});
```

- [ ] **Step 2: Smoke-check preload parses**

Run: `cd electron && node --check preload.js`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add electron/preload.js
git commit -m "desktop: expose update IPC in preload"
```

---

### Task 9: React settings UI

Add an update status line + "检查更新" / "重启以更新" controls to the settings popup, beside the existing version row (spec §A9). Renders only when `electronAPI.checkForUpdates` exists (Electron).

**Files:**
- Modify: `frontend-desktop/src/components/NewSessionDialog.tsx:11-19` (Window type)
- Modify: `frontend-desktop/src/components/SessionList.tsx`
- Modify: `frontend-desktop/src/i18n.tsx`

- [ ] **Step 1: Extend the Window.electronAPI type**

In `frontend-desktop/src/components/NewSessionDialog.tsx`, extend the `electronAPI` interface (currently `electronAPI?: { selectDirectory; openPath; getVersion? }`) to add:

```ts
      checkForUpdates?: () => Promise<void>
      installUpdate?: () => Promise<void>
      onUpdateStatus?: (cb: (p: { status: string; version?: string; percent?: number; message?: string }) => void) => (() => void)
```

- [ ] **Step 2: Add i18n strings**

In `frontend-desktop/src/i18n.tsx`, add to the zh block (near `settings.version`, line 55) and the en block (near line 199) respectively:

zh:
```ts
  'update.check': '检查更新',
  'update.checking': '检查中…',
  'update.available': '发现新版本',
  'update.downloading': '下载中',
  'update.downloaded': '已下载，重启以更新',
  'update.restart': '立即重启更新',
  'update.uptodate': '已是最新',
  'update.error': '更新检查失败',
```
en:
```ts
  'update.check': 'Check for updates',
  'update.checking': 'Checking…',
  'update.available': 'Update available',
  'update.downloading': 'Downloading',
  'update.downloaded': 'Downloaded — restart to update',
  'update.restart': 'Restart to update',
  'update.uptodate': 'Up to date',
  'update.error': 'Update check failed',
```

- [ ] **Step 3: Add update state + subscription in SessionList**

In `frontend-desktop/src/components/SessionList.tsx`, after the `version` state (line 34) add:

```ts
  const [update, setUpdate] = useState<{ status: string; percent?: number; version?: string } | null>(null)
```

After the version `useEffect` (lines 38-40) add:

```ts
  useEffect(() => {
    const off = window.electronAPI?.onUpdateStatus?.((p) => setUpdate(p))
    return () => { off?.() }
  }, [])
```

- [ ] **Step 4: Render the update row**

In `frontend-desktop/src/components/SessionList.tsx`, replace the version row block (lines 212-216) with the version row followed by an update row:

```tsx
              {/* 版本号 */}
              <div className="flex items-center justify-between px-3 pb-1" style={{ fontSize: 11, color: 'var(--t3)' }}>
                <span>{t('settings.version')}</span>
                <span style={{ fontFamily: 'monospace' }}>{version ? `V${version}` : '—'}</span>
              </div>

              {/* 更新 */}
              {window.electronAPI?.checkForUpdates && (
                <div className="flex items-center justify-between px-3 pb-2" style={{ fontSize: 11 }}>
                  <span style={{ color: 'var(--t3)' }}>
                    {update?.status === 'checking' && t('update.checking')}
                    {update?.status === 'available' && `${t('update.available')} ${update.version ?? ''}`}
                    {update?.status === 'downloading' && `${t('update.downloading')} ${update.percent ?? 0}%`}
                    {update?.status === 'downloaded' && t('update.downloaded')}
                    {update?.status === 'not-available' && t('update.uptodate')}
                    {update?.status === 'error' && t('update.error')}
                    {!update && ' '}
                  </span>
                  {update?.status === 'downloaded' ? (
                    <button onClick={() => window.electronAPI?.installUpdate?.()}
                      style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, border: 'none', cursor: 'pointer', background: 'var(--blue)', color: '#fff' }}>
                      {t('update.restart')}
                    </button>
                  ) : (
                    <button onClick={() => window.electronAPI?.checkForUpdates?.()}
                      style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, border: '1px solid var(--border)', cursor: 'pointer', background: 'var(--bg3)', color: 'var(--t2)' }}>
                      {t('update.check')}
                    </button>
                  )}
                </div>
              )}
```

- [ ] **Step 5: Type-check the renderer**

Run: `cd frontend-desktop && npx tsc -b`
Expected: no type errors, exit 0.

- [ ] **Step 6: Commit**

```bash
git add frontend-desktop/src/components/SessionList.tsx frontend-desktop/src/components/NewSessionDialog.tsx frontend-desktop/src/i18n.tsx
git commit -m "desktop: add update status UI in settings popup"
```

---

### Task 10: electron-builder publish config

Generate `latest.yml`/`beta.yml` + blockmap so a feed can serve updates (spec §A2, §C2). Runtime `setFeedURL` (Task 6) overrides this URL; the build-time value is only the bundled default.

**Files:**
- Modify: `electron/package.json` (`build` block)

- [ ] **Step 1: Add publish provider**

In `electron/package.json`, inside `"build"`, add a `publish` entry (placeholder URL; overridden at runtime by env/update-config.json):

```json
    "publish": [
      {
        "provider": "generic",
        "url": "http://localhost:8384",
        "channel": "latest"
      }
    ],
```

- [ ] **Step 2: Build a directory package and confirm manifest generation**

Run: `cd electron && npm run build`
Expected: build succeeds; under `build/electron-dist` there is `latest.yml`, `NetLIVE-CoWork Setup <version>.exe`, and a matching `.exe.blockmap`.

- [ ] **Step 3: Commit**

```bash
git add electron/package.json
git commit -m "desktop: add generic publish config for update feed"
```

---

### Task 11: Single-machine end-to-end validation (manual)

Proves the full loop and the app-specific risks (spec §13①). This task has no unit tests; follow the checklist and record results.

**Files:** none (manual validation)

- [ ] **Step 1: Build and install v0.1.0**

Set `electron/package.json` version to `0.1.0`. Run `cd electron && npm run build`. Run the produced NSIS installer (`NetLIVE-CoWork Setup 0.1.0.exe`) to a real install (auto-update only works on the installed NSIS build, not dev/portable).

- [ ] **Step 2: Serve a localhost feed**

Create a folder `C:\tmp\feed`. Copy `latest.yml`, `NetLIVE-CoWork Setup 0.1.0.exe`, and the `.blockmap` into it. Serve it: `npx serve -l 8384 C:\tmp\feed` (or `python -m http.server 8384 --directory C:\tmp\feed`).

- [ ] **Step 3: Point the install at the localhost feed + telemetry**

Create `%APPDATA%\NetLIVE-CoWork\update-config.json`:
```json
{ "feedUrl": "http://localhost:8384", "channel": "stable", "telemetryUrl": "http://localhost:8385" }
```
(Optionally run a trivial localhost listener on 8385 to observe telemetry POSTs; otherwise telemetry will queue offline — also a valid check.)

- [ ] **Step 4: Build and publish v0.1.1**

Bump `electron/package.json` version to `0.1.1`. Run `cd electron && npm run build`. Copy the new `latest.yml`, `Setup 0.1.1.exe`, and `.blockmap` into `C:\tmp\feed` (keep the 0.1.0 files for differential).

- [ ] **Step 5: Trigger and observe the update**

Launch the installed app. In the settings popup, confirm status shows "发现新版本 0.1.1" → "下载中 N%" → "已下载，重启以更新". Click "立即重启更新".

- [ ] **Step 6: Verify the critical risks**

Confirm all of:
- App relaunches and version reads `V0.1.1` (no file-lock failure — backend exe was released; spec §A5).
- Existing sessions/config under `%APPDATA%\NetLIVE-CoWork\data` survived (spec §A6).
- Version-aware seeding: add a new default json to the bundled `default_data` before the 0.1.1 build and confirm it appears in AppData after upgrade, while a user-edited existing file is unchanged.
- The Electron log (`%APPDATA%\NetLIVE-CoWork\logs\electron.log`) shows blockmap differential download (partial download size << full installer).
- Set `update-config.json` channel to `beta` (with a `beta.yml` in the feed) and confirm the client requests `beta.yml`.

- [ ] **Step 7: Record results in the plan**

Check off each item in Step 6 with a one-line result. If any fails, file a fix task before considering Part A done.

---

## Self-Review

- **Spec coverage:** A1 architecture (Tasks 6-7), A2 components (Tasks 6-9), A3 config resolution (Task 2, wired Task 7), A4 update flow (Task 7 + IPC Task 8), A5 backend stop (Task 7 Step 3), A6 seeding (Task 3 + Task 7 Step 4), A7 channel (Task 2 channel + Task 6 setFeedURL + Task 11 Step 6), A8 telemetry (Tasks 4-5, wired Task 7), A9 UX (Task 9). C1 schema (Task 4 buildEvent fields), C2 feed layout (Tasks 10-11). Validation/testing §13① (Task 11), pure-logic unit tests (Tasks 2-5). Build §A2 publish (Task 10).
- **Not in this plan (correct):** Part B management service (separate plan); app_version single-source alignment (spec §17 open item, belongs with release process/Part B); `stagingPercentage` (dropped).
- **Type consistency:** `resolveUpdateConfig` shape `{feedUrl, telemetryUrl, channel}` used identically in Tasks 2 and 7; `createReporter({endpoint, context, loadQueue, saveQueue, fetchImpl})` and `report/flush` consistent across Tasks 5 and 7; `initUpdater({config, isPackaged, onEvent, logger})` consistent Tasks 6-7; `onUpdateStatus` payload `{status, version?, percent?, message?}` consistent across Tasks 7 (emit), 8 (bridge), 9 (consume).
- **Placeholder scan:** none — every code/command step has concrete content.
