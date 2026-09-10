const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function harness() {
  const nodes = new Map(), requests = [], messages = [];
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, { value: '', textContent: '', hidden: false, disabled: false, open: false, events: {},
      addEventListener(name, fn) { this.events[name] = fn; }, focus() {},
      showModal() { this.open = true; }, close() { this.open = false; } });
    return nodes.get(id);
  };
  const city = { city_id: 'shanghai', city_name: '上海', count: 3, version: 'v1', dates: ['2026-12-31', '2027-01-02'] };
  const state = { projectId: 'main', userId: 'tester', projectUnlocked: true, cityId: 'beijing', projectItinerary: { cities: [city] } };
  const env = { state, window: { confirm: () => true, TripBatch: { canNavigate: () => true } },
    document: { getElementById: node }, requireIdentity: () => true,
    requestJson: async (url, options) => { requests.push({ url, ...options, body: JSON.parse(options.body) }); if (env.error) throw env.error; return {}; },
    fetchSnapshot: async () => { env.fetches = (env.fetches || 0) + 1; }, showToast: text => messages.push(text),
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../public/project-city-editor.js'), 'utf8'), env);
  const ui = env.window.TripCityEditor; ui.init(); ui.open(city);
  const submit = () => node('city-plan-form').events.submit({ preventDefault() {} });
  return { env, state, city, node, ui, requests, messages, submit };
}

test('editing a city preserves its scope and previews the whole trip across year boundaries', async () => {
  const h = harness();
  assert.equal(h.node('city-plan-dialog').open, true);
  assert.match(h.node('city-plan-title').textContent, /上海/);
  h.node('city-plan-start').value = '2028-02-28'; h.node('city-plan-start').events.input();
  assert.match(h.node('city-plan-preview').textContent, /2028-02-28、2028-03-01/);
  await h.submit();
  assert.deepEqual(h.requests[0], { url: '/api/project-itinerary', method: 'PUT', body: {city_id:'shanghai',version:'v1',start_date:'2028-02-28'} });
  assert.equal(h.node('city-plan-dialog').open, false);
  assert.equal(h.env.fetches, 1);
});

test('delete requires a separate confirmation and cancel makes no request', async () => {
  const h = harness();
  await h.node('city-plan-delete').events.click(); assert.equal(h.requests.length, 0);
  h.node('city-plan-remove').events.click();
  assert.match(h.node('city-plan-delete-description').textContent, /上海.*3 项行程.*地点和美食清单保留/);
  assert.equal(h.node('city-plan-save').disabled, true);
  await h.submit(); assert.equal(h.requests.length, 0);
  h.node('city-plan-delete-cancel').events.click();
  assert.equal(h.node('city-plan-delete-confirm').hidden, true);
  assert.equal(h.node('city-plan-save').disabled, false);
  h.node('city-plan-remove').events.click(); await h.node('city-plan-delete').events.click();
  assert.deepEqual(h.requests[0].body, {city_id:'shanghai',version:'v1',confirm_delete:true});
  assert.equal(h.requests[0].method, 'DELETE');
});

test('invalid dates never submit, including normalized invalid days and overflow', async () => {
  for (const value of ['', '2027-02-29', '0000-01-01', '9999-12-31']) {
    const h = harness(); h.node('city-plan-start').value = value; await h.submit();
    assert.equal(h.requests.length, 0); assert.ok(h.node('city-plan-error').textContent);
  }
});

test('conflicts keep draft and version until user explicitly reloads', async () => {
  const h = harness(); h.node('city-plan-start').value = '2027-01-04';
  h.env.error = {status:409, message:'行程已变更'}; await h.submit();
  assert.equal(h.node('city-plan-dialog').open, true);
  assert.equal(h.node('city-plan-start').value, '2027-01-04');
  assert.equal(h.node('city-plan-reload').hidden, false);
  h.state.projectItinerary.cities = [{...h.city, version:'v2', dates:['2027-01-06']}];
  await h.node('city-plan-reload').events.click();
  assert.equal(h.node('city-plan-start').value, '2027-01-06');
  h.env.error = null; await h.submit(); assert.equal(h.requests[1].body.version, 'v2');
});

test('busy submissions cannot double-save or close; late responses after scope change are ignored', async () => {
  const h = harness(); let finish;
  h.env.requestJson = () => new Promise(resolve => { finish = resolve; });
  const pending = h.submit();
  assert.equal(h.node('city-plan-save').disabled, true);
  h.node('city-plan-close').events.click(); assert.equal(h.node('city-plan-dialog').open, true);
  h.state.userId = 'someone-else'; h.ui.sync();
  assert.equal(h.node('city-plan-dialog').open, false);
  finish({}); await pending;
  assert.equal(h.env.fetches, undefined); assert.equal(h.messages.length, 0);
});

test('unsaved date edits are retained when closing is cancelled', () => {
  const h = harness(); h.node('city-plan-start').value = '2027-01-04'; h.env.window.confirm = () => false;
  h.node('city-plan-close').events.click(); assert.equal(h.node('city-plan-dialog').open, true);
});
