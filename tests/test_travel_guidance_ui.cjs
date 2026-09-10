const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness({ handler, confirm = true } = {}) {
  const nodes = new Map(), requests = [];
  class Node {
    constructor(tag, cls, text) { this.tagName = tag; this.className = cls; this.textContent = text || ''; this.children = []; this.events = {}; this.value = ''; this.open = false; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    addEventListener(name, fn) { this.events[name] = fn; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    emit(name = 'click') { return this.events[name]?.({ preventDefault() {} }); }
  }
  const node = id => { if (!nodes.has(id)) nodes.set(id, new Node('div')); return nodes.get(id); };
  const state = { projectId: 'main', cityId: 'shanghai', tab: 'itinerary', projectUnlocked: true, userId: 'test', revision: 1,
    travelGuidance: [
      { id: 'one', city_id: 'shanghai', city_name: '上海', version: 'a'.repeat(24), summary: '建议性安排', notices: ['预约提醒', '<img src=x onerror=alert(1)>'] },
      { id: 'two', city_id: 'beijing', city_name: '北京', version: 'b'.repeat(24), summary: '', notices: ['北京提示'] },
    ] };
  const env = { window: { confirm: () => confirm }, state, document: { getElementById: node },
    element: (tag, cls, text) => new Node(tag, cls, text), requireIdentity: () => Boolean(state.userId), showToast() {}, fetchSnapshot: async () => {},
    requestJson: async (url, options = {}) => {
      const body = options.body ? JSON.parse(options.body) : undefined;
      requests.push({ url, method: options.method || 'GET', body });
      if (handler) return handler(url, options, body);
      const group = state.travelGuidance.find(g => g.city_id === (body?.city_id || state.cityId));
      return { data: { guidance: options.method ? { ...group, ...body, manual: true, deleted: options.method === 'DELETE', version: 'c'.repeat(24) } : group,
        revision: 2 } };
    } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../public/travel-guidance.js'), 'utf8'), env);
  const render = () => env.window.TripGuidance.render();
  render();
  const edit = () => node('travel-guidance-groups').children[0].children[1].emit();
  return { state, node, render, edit, requests, submit: () => node('guidance-form').emit('submit'), remove: () => node('guidance-delete').emit() };
}

test('one city note is visible only on itinerary; an empty note offers creation', () => {
  const h = harness();
  assert.equal(h.node('travel-guidance').hidden, false);
  assert.equal(h.node('guidance-add').hidden, true);
  for (const tab of ['attraction', 'food', 'transit', 'activity']) {
    h.state.tab = tab; h.render(); assert.equal(h.node('travel-guidance').hidden, true);
    assert.equal(h.node('guidance-add').hidden, true);
  }
  h.state.tab = 'itinerary'; h.state.cityId = 'beijing'; h.render();
  assert.equal(h.node('travel-guidance-groups').children[1].children[0].textContent, '北京提示');
  h.state.travelGuidance = []; h.render(); assert.equal(h.node('guidance-add').hidden, false);
  h.state.projectUnlocked = false; h.render(); assert.equal(h.node('guidance-add').hidden, true);
});

test('polling preserves the open editor and draft; save sends original version and trimmed lines', async () => {
  const h = harness(); await h.edit();
  h.node('guidance-notices').value = '  带相机\n\n 提前预约  ';
  h.state.travelGuidance[0] = { ...h.state.travelGuidance[0], summary: '其他人更新', version: 'd'.repeat(24) };
  h.render(); assert.equal(h.node('guidance-notices').value, '  带相机\n\n 提前预约  ');
  await h.submit();
  assert.deepEqual(h.requests[0].body, { city_id: 'shanghai', version: 'a'.repeat(24), notices: ['带相机', '提前预约'] });
  assert.equal(h.node('guidance-dialog').open, false);
  assert.deepEqual(h.state.travelGuidance.find(g => g.city_id === 'shanghai').notices, ['带相机', '提前预约']);
});

test('409 preserves draft and requires explicit reload before retrying', async () => {
  const current = { city_id: 'shanghai', city_name: '上海', summary: '', notices: ['最新建议'], version: 'e'.repeat(24) };
  const h = harness({ handler: async () => { throw Object.assign(new Error('出行提示已更新'), { status: 409, data: { current } }); } });
  await h.edit(); h.node('guidance-notices').value = '不能丢'; await h.submit();
  assert.equal(h.node('guidance-notices').value, '不能丢');
  assert.equal(h.node('guidance-reload').hidden, false);
  await h.submit(); assert.equal(h.requests.length, 1);
  h.node('guidance-reload').emit(); assert.equal(h.node('guidance-notices').value, '最新建议');
});

test('confirmed deletion hides only the current note; cancel sends nothing', async () => {
  const cancelled = harness({ confirm: false }); await cancelled.edit(); await cancelled.remove();
  assert.equal(cancelled.requests.length, 0);
  const h = harness(); await h.edit(); await h.remove();
  assert.equal(h.requests[0].method, 'DELETE');
  assert.equal(h.node('travel-guidance').hidden, true);
  assert.equal(h.state.travelGuidance.length, 1);
  assert.equal(h.state.travelGuidance[0].city_id, 'beijing');
  assert.equal(h.node('guidance-add').hidden, false);
});

test('scope change closes stale editor; late requests cannot replace another city', async () => {
  let resolve;
  const h = harness({ handler: () => new Promise(done => { resolve = done; }) });
  await h.edit(); const saving = h.submit();
  h.state.cityId = 'beijing'; h.render();
  assert.equal(h.node('guidance-dialog').open, false);
  resolve({ data: { guidance: { city_id: 'shanghai', deleted: true }, revision: 2 } }); await saving;
  assert.equal(h.state.travelGuidance.length, 2);
  assert.equal(h.node('guidance-save').disabled, false);
});

test('errors remain retryable and pending save prevents duplicate submissions', async () => {
  let reject;
  const h = harness({ handler: () => new Promise((resolve, fail) => { reject = fail; }) });
  await h.edit(); const saving = h.submit(); await h.submit();
  assert.equal(h.requests.length, 1); reject(new Error('网络暂时不可用')); await saving;
  assert.equal(h.node('guidance-error').textContent, '网络暂时不可用');
  assert.equal(h.node('guidance-save').disabled, false);
});

test('generated content stays plain text and old snapshots hide it cleanly', () => {
  const h = harness();
  const notice = h.node('travel-guidance-groups').children[1].children[1];
  assert.equal(notice.textContent, '<img src=x onerror=alert(1)>');
  assert.equal(notice.children.length, 0);
  delete h.state.travelGuidance; h.render();
  assert.equal(h.node('travel-guidance').hidden, true);
});
