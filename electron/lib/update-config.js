'use strict';

// Pure resolution of update configuration. No fs/env/electron access here;
// callers pass already-read env vars, parsed config-file object, and bundled defaults.
// Precedence: env > configFile > defaults.
function resolveUpdateConfig({ env = {}, configFile = {}, defaults = {} } = {}) {
  const pick = (envKey, fileKey) =>
    env[envKey] || configFile[fileKey] || defaults[fileKey] || '';
  const channelRaw = pick('IPMASTER_COWORK_UPDATE_CHANNEL', 'channel') || 'stable';
  return {
    feedUrl: pick('IPMASTER_COWORK_UPDATE_FEED_URL', 'feedUrl'),
    telemetryUrl: pick('IPMASTER_COWORK_TELEMETRY_URL', 'telemetryUrl'),
    channel: channelRaw === 'beta' ? 'beta' : 'stable',
  };
}

function shouldCheckForUpdates(cfg) { return !!(cfg && cfg.feedUrl); }
function shouldReportTelemetry(cfg) { return !!(cfg && cfg.telemetryUrl); }

module.exports = { resolveUpdateConfig, shouldCheckForUpdates, shouldReportTelemetry };
