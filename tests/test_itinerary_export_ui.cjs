const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness(handler) {
  const nodes = new Map(), requests = [], downloads = [], timers = new Map();
  let nextTimer = 0, gated = false;
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, {
      value: '', textContent: '', disabled: false, hidden: false, open: false, events: {},
      addEventListener(event, callback) { this.events[event] = callback; },
      setAttribute(key, value) { this[key] = value; },
      showModal() { this.open = true; }, close() { this.open = false; this.events.close?.(); },
    });
    return nodes.get(id);
  }
  const state = { projectUnlocked: true, tab: 'itinerary', cityId: 'shanghai',
    cities: [{ id: 'shanghai', name: '上海' }, { id: 'beijing', name: '北京' }] };
  const env = {
    state, AbortController, URLSearchParams, decodeURIComponent,
    URL: { createObjectURL: () => 'blob:download', revokeObjectURL() {} },
    setTimeout: (fn) => { timers.set(++nextTimer, fn); return nextTimer; },
    clearTimeout: (id) => timers.delete(id),
    document: { getElementById: node, body: { append() {} }, createElement: () => ({
      click() { downloads.push({ href: this.href, name: this.download }); }, remove() {},
    }) },
    window: { TripProject: { headers: () => ({ 'X-Trip-Project': 'a'.repeat(16) }) } },
    showProjectGate() { gated = true; state.projectUnlocked = false; },
    fetch: async (url, options) => {
      requests.push({ url, options });
      return handler ? handler(url, options) : {
        ok: true, blob: async () => ({}),
        headers: { get: () => "attachment; filename*=UTF-8''" + encodeURIComponent('国庆-上海-行程.xlsx') },
      };
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../public/itinerary-export.js'), 'utf8'), env);
  env.window.TripExport.init();
  return { node, state, requests, downloads, timers, api: env.window.TripExport, gated: () => gated,
    click: (id) => node(id).events.click() };
}

test('only the unlocked itinerary tab shows export and the current city is labelled', () => {
  const h = harness(); h.api.sync(); assert.equal(h.node('export-open').hidden, false);
  h.click('export-open'); assert.equal(h.node('export-city-option').textContent, '当前城市 · 上海');
  assert.equal(h.node('export-scope').value, 'city');
  h.state.tab = 'food'; h.api.sync(); assert.equal(h.node('export-open').hidden, true);
  h.state.projectUnlocked = false; h.api.sync(); assert.equal(h.node('export-dialog').open, false);
});

test('city download uses the project header, credentials and UTF-8 filename', async () => {
  const h = harness(); h.click('export-open'); await h.click('export-excel');
  const { url, options } = h.requests[0];
  assert.equal(new URL(url, 'http://test').searchParams.get('city_id'), 'shanghai');
  assert.equal(options.headers['X-Trip-Project'], 'a'.repeat(16));
  assert.equal(options.credentials, 'same-origin');
  assert.equal(h.downloads[0].name, '国庆-上海-行程.xlsx');
  assert.equal(h.node('export-excel').disabled, false);
  assert.match(h.node('export-status').textContent, /已发起下载/);
});

test('all cities omits the current city parameter and supports Word', async () => {
  const h = harness(); h.click('export-open'); h.node('export-scope').value = 'all';
  await h.click('export-word');
  const query = new URL(h.requests[0].url, 'http://test').searchParams;
  assert.equal(query.get('format'), 'docx'); assert.equal(query.get('scope'), 'all');
  assert.equal(query.has('city_id'), false);
});

test('empty or failed exports show the server error and never download JSON', async () => {
  const h = harness(() => ({ ok: false, json: async () => ({ error: '还没有已保存的行程' }) }));
  h.click('export-open'); await h.click('export-word');
  assert.match(h.node('export-error').textContent, /还没有/);
  assert.equal(h.downloads.length, 0); assert.equal(h.node('export-word').disabled, false);
});

test('expired project access opens the gate without downloading', async () => {
  const h = harness(() => ({ ok: false, json: async () => ({ code: 'PROJECT_LOCKED' }) }));
  h.click('export-open'); await h.click('export-word');
  assert.equal(h.gated(), true); assert.equal(h.node('export-dialog').open, false);
  assert.equal(h.downloads.length, 0);
});

test('closing aborts the request and ignores a late successful response', async () => {
  let resolve;
  const h = harness(() => new Promise((done) => { resolve = done; }));
  h.click('export-open'); const first = h.click('export-word');
  await h.click('export-word'); assert.equal(h.requests.length, 1);
  h.click('export-close'); assert.equal(h.requests[0].options.signal.aborted, true);
  resolve({ ok: true, blob: async () => ({}) }); await first;
  assert.equal(h.downloads.length, 0); assert.equal(h.node('export-word').disabled, false);
});

test('switching cities cancels an open export to prevent stale scope', async () => {
  const h = harness(); h.click('export-open'); h.state.cityId = 'beijing'; h.api.sync();
  assert.equal(h.node('export-dialog').open, false);
  h.click('export-open'); assert.equal(h.node('export-city-option').textContent, '当前城市 · 北京');
});
