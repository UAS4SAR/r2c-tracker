const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('recording source details update without reload or thumbnail change', async () => {
  const label = { dataset: { streamSessionId: 'recording-1' }, textContent: 'Source details pending' };
  const state = { dataset: { membershipRevision: 'same', statusUrl: '/streams/live-status', watchActive: 'true' } };
  const events = {};
  let reloads = 0;
  let sourceLabel = '1920×1088 at 30.0 fps · 271.8 MB';
  const context = {
    URLSearchParams,
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    document: {
      hidden: false,
      getElementById: id => id === 'streams-live-status' ? state : null,
      querySelectorAll: selector => selector === '.stream-source-label' ? [label] : [],
      addEventListener: () => {},
    },
    window: {
      clearTimeout: () => {}, setTimeout: () => 1, dispatchEvent: () => {},
      addEventListener: (name, callback) => { events[name] = callback; },
      location: { reload: () => { reloads++; } },
    },
    fetch: async () => ({ ok: true, json: async () => ({ membershipRevision: 'same',
      inProgressSessionIds: [], streams: [{ sessionId: 'recording-1', sourceLabel }] }) }),
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/organization_streams_live.js'), 'utf8'), context);
  await new Promise(setImmediate);
  assert.equal(label.textContent, sourceLabel);
  assert.equal(reloads, 0);
  sourceLabel = '1920×1088 at 30.0 fps · 300.0 MB';
  events.focus();
  await new Promise(setImmediate);
  assert.equal(label.textContent, sourceLabel);
  assert.equal(reloads, 0);
});
