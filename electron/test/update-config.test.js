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
