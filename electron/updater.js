'use strict';

// initUpdater wires electron-updater. Returns autoUpdater when active, else null.
// - Only active in a packaged app (autoUpdater is a no-op/throws otherwise).
// - Only active when a feed URL is configured (no feed -> skip).
// - ALWAYS registers an 'error' handler: an unhandled 'error' on the
//   EventEmitter would throw an uncaught exception.
// electron-updater is required lazily (inside, after the guards) because its
// autoUpdater getter executes eagerly and can throw under plain Node.
function initUpdater({ config, isPackaged, onEvent, logger }) {
  const log = logger || (() => {});
  if (!isPackaged) { log('updater: skipped (not packaged)'); return null; }
  if (!config.feedUrl) { log('updater: skipped (no feedUrl configured)'); return null; }

  const { autoUpdater } = require('electron-updater');
  const channel = config.channel === 'beta' ? 'beta' : 'latest';
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.channel = channel;
  autoUpdater.setFeedURL({ provider: 'generic', url: config.feedUrl, channel });

  autoUpdater.on('checking-for-update', () => onEvent({ status: 'checking' }));
  autoUpdater.on('update-available', (info) => onEvent({ status: 'available', version: info && info.version }));
  autoUpdater.on('update-not-available', () => onEvent({ status: 'not-available' }));
  autoUpdater.on('download-progress', (p) => onEvent({ status: 'downloading', percent: Math.round((p && p.percent) || 0) }));
  autoUpdater.on('update-downloaded', (info) => onEvent({ status: 'downloaded', version: info && info.version }));
  autoUpdater.on('error', (err) => { log('updater error: ' + String(err)); onEvent({ status: 'error', message: String((err && err.message) || err) }); });

  log(`updater: initialized channel=${channel} feed=${config.feedUrl}`);
  return autoUpdater;
}

module.exports = { initUpdater };
