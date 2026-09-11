const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function browser({ online = false, response = 200, storageFails = false } = {}) {
  const events = {}, storage = new Map(), requests = [];
  const node = () => ({ textContent: '', children: [], append(...items) { this.children.push(...items); }, replaceChildren() { this.children = []; } });
  const page = { dataset: { organization: 'sar', actor: 'pilot', token: 'csrf' } };
  const status = node(), list = node();
  const fields = { actor_id: 'pilot', form_token: 'csrf', remote_id: 'RID', event_id: 'event-1', revision: '0', note: 'Broken propeller', status: 'out_of_service', self_remediation: 'yes' };
  const form = { elements: Object.fromEntries(Object.entries(fields).map(([k, value]) => [k, { value }])),
    addEventListener(name, fn) { events.submit = fn; }, reportValidity() { return true; },
    submit() { events.normalSubmitted = true; }, closest() { return {}; } };
  const navigator = { onLine: online };
  const context = {
    document: { getElementById(id) { return id === 'aircraft-service-page' ? page : id === 'service-upload-status' ? status : list; },
      querySelectorAll() { return [form]; }, createElement: node },
    localStorage: { getItem: key => storage.get(key), setItem(key, value) { if (storageFails) throw Error('storage unavailable'); storage.set(key, value); } },
    window: { addEventListener(name, fn) { events[name] = fn; } }, navigator,
    setInterval(fn) { events.retry = fn; }, crypto: { randomUUID: () => 'event-next' }, URLSearchParams,
    FormData: class { constructor() { return Object.entries(form.elements).map(([k, field]) => [k, field.value]); } },
    fetch: async (url, options) => {
      requests.push({ url, fields: Object.fromEntries(options.body), options });
      if (response === 'offline') throw Error('offline');
      return { ok: response === 200, status: response, json: async () => ({ remoteId: 'RID', revision: 1, notificationsQueued: 1 }) };
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/aircraft-service.js'), 'utf8'), context);
  return { events, storage, requests, navigator, status, form,
    submit() { events.submit({ preventDefault() {} }); },
    queue() { return JSON.parse(storage.get('r2c-service:sar:pilot') || '[]'); } };
}

test('an offline report survives until an acknowledged upload', async () => {
  const b = browser(); b.submit();
  assert.equal(b.requests.length, 0);
  assert.equal(b.queue()[0].fields.note, 'Broken propeller');
  assert.match(b.status.textContent, /not yet received/);
  b.navigator.onLine = true; await b.events.online();
  assert.equal(b.queue().length, 0);
  assert.equal(b.requests[0].fields.actor_id, 'pilot');
  assert.equal(b.requests[0].fields.form_token, 'csrf');
  assert.equal(b.form.elements.revision.value, '1');
});

test('a revision conflict is retained and never automatically retried', async () => {
  const b = browser({ response: 409 }); b.submit();
  b.navigator.onLine = true; await b.events.online(); await b.events.retry();
  assert.equal(b.requests.length, 1);
  assert.equal(b.queue()[0].conflict, true);
  assert.match(b.status.textContent, /older report will not overwrite/);
});

test('network failures preserve the same event ID for idempotent retry', async () => {
  const b = browser({ response: 'offline' }); b.submit();
  b.navigator.onLine = true; await b.events.online(); await b.events.retry();
  assert.equal(b.queue().length, 1);
  assert.equal(b.requests[0].fields.event_id, b.requests[1].fields.event_id);
});

test('unavailable browser storage still allows a normal online submission', () => {
  const b = browser({ online: true, storageFails: true }); b.submit();
  assert.equal(b.events.normalSubmitted, true);
});
