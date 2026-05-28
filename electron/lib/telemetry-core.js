'use strict';

// Pure telemetry helpers. ctx.now() supplies the timestamp so tests are deterministic.
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
