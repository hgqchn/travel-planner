const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../public/app.js'), 'utf8');

function harness() {
  class Node {
    constructor(tag, className = '', text = '') {
      this.tagName = tag; this.className = className; this.textContent = text; this.children = [];
      this.dataset = {}; this.events = {}; this.style = { setProperty() {} }; this.value = ''; this.open = false;
    }
    append(...children) { for (const child of children) { this.children.push(child); if (typeof child === 'object') child.parent = this; } }
    replaceChildren(...children) { this.children = []; this.append(...children); }
    get lastElementChild() { return this.children.at(-1); }
    setAttribute(key, value) { this[key] = value; }
    addEventListener(type, action) { (this.events[type] ||= []).push(action); }
    dispatchEvent(event) { for (const callback of this.events[event.type] || []) callback(event); if (event.bubbles) this.parent?.dispatchEvent(event); }
    click() { this.dispatchEvent({ type: 'click' }); }
    setCustomValidity(value) { this.validationMessage = value; }
    showModal() { this.open = true; } close() { this.open = false; } focus() {} scrollIntoView() {}
    querySelectorAll(selector) {
      const matches = (node, part) => {
        const tag = part.trim().match(/^[a-z][a-z0-9]*/);
        if (tag && node.tagName !== tag[0]) return false;
        const attrs = [...part.matchAll(/\[([\w-]+)=["']([^"']+)["']\]/g)];
        if (attrs.some(([, name, value]) => String(node[name] || '') !== value)) return false;
        const classes = [...part.matchAll(/\.([\w-]+)/g)];
        if (classes.some(([, name]) => !node.className.split(' ').includes(name))) return false;
        return !!(tag || attrs.length || classes.length);
      };
      return this.children.filter(child => typeof child === 'object').flatMap(child => [
        ...(selector.split(',').some(part => matches(child, part)) ? [child] : []), ...child.querySelectorAll(selector),
      ]);
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  }
  const element = (tag, className, text) => new Node(tag, className, text), requests = [], toasts = [];
  const state = { userId: 'Alice', cityId: 'beijing', saving: false, items: { attraction: [
    { id: 'a', city_id: 'beijing', name: '故宫博物院', version: 1 },
    { id: 'b', city_id: 'beijing', name: '景山公园', version: 1 },
    { id: 'c', city_id: 'shanghai', name: '上海博物馆', version: 1 },
  ] }, cities: [{ id: 'beijing', name: '北京' }] };
  const dom = Object.fromEntries(['editFields', 'editForm', 'editKicker', 'editTitle', 'saveButton', 'deleteButton', 'editError', 'editConflict', 'conflictDetails', 'editDialog', 'editClose', 'editReturn'].map(key => [key, element('div')]));
  dom.editForm.append(dom.editFields);
  const env = {
    window: { TripBatch: { canNavigate: () => true }, TripTaxonomy: { effectiveTags: () => [], categoryOptions: () => [], formatTags: () => '', enhanceTags() {} }, confirm: () => true, navigator: {} },
    document: { createElement: tag => element(tag), createTextNode: text => text },
    state, dom, element, Date, URL, requestAnimationFrame: callback => callback(),
    Event: class { constructor(type, options) { this.type = type; Object.assign(this, options); } },
    FormData: class { constructor(form) { this.inputs = form.querySelectorAll('input,textarea,select'); } entries() { return this.inputs.filter(input => input.name && !input.disabled).map(input => [input.name, input.value])[Symbol.iterator](); } },
    showToast: message => toasts.push(message), fetchSnapshot: async () => {}, openIdentity() {}, showProjectGate() {},
    requestJson: async (url, options) => { requests.push({ url, body: JSON.parse(options.body) }); return { data: {} }; },
    addTag: (container, text) => { if (text) container.append(element('span', 'tag', text)); },
    addDetail: (container, label, value) => { if (value) container.append(element('dt', '', label), element('dd', '', value)); },
    editButton: () => element('button', 'edit-card-button'), appendFooter: card => card.append(element('footer')),
    itemName: (kind, item) => kind === 'itinerary' ? item.title : item.name,
  };
  vm.createContext(env);
  vm.runInContext(source.slice(0, source.indexOf('const state =')), env);
  vm.runInContext(source.slice(source.indexOf('function appendAttractionHighlights('), source.indexOf('function appendFooter(')), env);
  vm.runInContext(source.slice(source.indexOf('function attractionCard('), source.indexOf('function scenicRatingState(')), env);
  vm.runInContext(source.slice(source.indexOf('function parseAttractionNames('), source.indexOf('function askDelete(')), env);
  env.scenicRatingBadge = () => null; env.itemCity = () => ({ name: '北京' }); env.appExternalAnchor = () => element('a');
  env.window.TripLinks = { attractionUrl: () => 'https://example.test/' };
  return { env, state, dom, requests, toasts, element, names: () => dom.editFields.querySelector('[name="attraction_names"]'),
    save: () => env.saveEditor({ preventDefault() {} }) };
}

function text(node) { return typeof node === 'string' ? node : node.textContent + node.children.map(text).join(''); }
const plain = value => JSON.parse(JSON.stringify(value));

test('linked itinerary names are highlighted safely with longest aliases first', () => {
  const h = harness();
  const card = h.env.itineraryCard({ id: 'i', city_id: 'beijing', title: '<b>故宫博物院</b>与故宫', location: '故宫博物院北门', linked_attractions: [{ id: 'a', name: '故宫博物院', matched_name: '故宫' }] });
  assert.deepEqual(card.querySelectorAll('mark').map(mark => mark.textContent), ['故宫博物院', '故宫', '故宫博物院']);
  assert.equal(card.querySelectorAll('b').length, 0);
  assert.equal(text(card.querySelector('h3')), '<b>故宫博物院</b>与故宫');
  card.querySelector('.linked-attraction-chip').click();
  assert.equal(h.state.currentEdit.kind, 'attraction'); assert.equal(h.state.currentEdit.item.id, 'a');
});

test('renamed linked attractions remain reachable by chips, removed references never open another city', () => {
  const h = harness();
  const card = h.env.itineraryCard({ title: '上午安排', city_id: 'beijing', linked_attractions: [{ id: 'a', name: '旧名称' }] });
  assert.equal(card.querySelectorAll('mark').length, 0);
  card.querySelector('.linked-attraction-chip').click(); assert.equal(h.state.currentEdit.item.name, '故宫博物院');
  h.env.closeEditor(true); h.state.items.attraction[0].city_id = 'shanghai';
  card.querySelector('.linked-attraction-chip').click(); assert.equal(h.state.currentEdit, null); assert.equal(h.toasts.length, 1);
});

test('attraction cards distinguish scheduled status and list dates with usage count', () => {
  const h = harness();
  const card = h.env.attractionCard({ name: '故宫', in_itinerary: true, itinerary_refs: [{ date: '2026-10-02' }, { date: '2026-10-01' }, { date: '2026-10-01' }] });
  assert.match(text(card), /已排行程/); assert.match(text(card), /3 次 · 2026-10-01、2026-10-02/);
  assert.match(text(h.env.attractionCard({ name: '旧记录' })), /未排行程/);
});

test('manual itinerary editing round trips only explicit names and supports city-scoped suggestions', async () => {
  const h = harness();
  h.env.openEditor('itinerary', { id: 'i', city_id: 'beijing', version: 4, title: '计划', attraction_names: ['故宫博物院'], linked_attractions: [{ id: 'a', name: '故宫博物院' }] });
  assert.equal(h.names().value, '故宫博物院');
  const buttons = h.dom.editFields.querySelectorAll('.linked-attraction-chip');
  assert.deepEqual(buttons.map(button => button.textContent), ['故宫博物院', '景山公园']);
  buttons[1].click(); assert.equal(h.names().value, '故宫博物院、景山公园');
  assert.equal(buttons[1]['aria-pressed'], 'true');
  buttons[0].click(); assert.equal(h.names().value, '景山公园');
  await h.save(); assert.deepEqual(h.requests[0].body.attraction_names, ['景山公园']);
  assert.equal(h.requests[0].body.version, 4); assert.ok(!Object.hasOwn(h.requests[0].body, 'linked_attractions'));
});

test('old manual itineraries save an empty array for automatic identification', async () => {
  const h = harness(); h.env.openEditor('itinerary', { id: 'i', title: '旧行程', version: 1 });
  assert.equal(h.names().value, ''); await h.save(); assert.deepEqual(h.requests[0].body.attraction_names, []);
});

test('new itinerary uses the selected day without changing an existing record date', () => {
  const h=harness();h.env.window.TripDaily={selectedDate:()=> '2030-02-12'};
  h.env.openEditor('itinerary');
  assert.equal(h.dom.editFields.querySelector('[name="date"]').value,'2030-02-12');
  h.env.openEditor('itinerary',{id:'i',version:1,date:'2030-03-01'});
  assert.equal(h.dom.editFields.querySelector('[name="date"]').value,'2030-03-01');
});

test('locked editor preserves time block while saving other fields and reflects conflict locks', async () => {
  const h = harness();
  const item = {id:'i', version:1, title:'公园', time_block:'morning', block_locked:true};
  h.env.openEditor('itinerary', item);
  const block = () => h.dom.editFields.querySelector('[name="time_block"]');
  assert.equal(block().disabled, true);
  assert.equal(block().value, 'morning');
  assert.match(text(h.dom.editFields), /解除时段锁定/);
  h.dom.editFields.querySelector('[name="notes"]').value = '新备注';
  await h.save();
  assert.equal(h.requests.at(-1).body.time_block, 'morning');
  assert.equal(h.requests.at(-1).body.notes, '新备注');
  h.env.openEditor('itinerary', {...item, block_locked:false});
  assert.equal(block().disabled, false);
  block().value = 'afternoon';
  h.env.showConflict({...item, version:2}); h.env.keepLocalConflict();
  assert.equal(block().disabled, true); assert.equal(block().value, 'morning');
  h.env.showConflict({...item, version:3, block_locked:false}); h.env.loadRemoteConflict();
  assert.equal(block().disabled, false);
});

test('explicit names deduplicate separators, preserve official punctuation and reject oversized inputs', async () => {
  const h = harness();
  assert.deepEqual(plain(h.env.parseAttractionNames('故宫，景山\n故宫、苏州市苏州园林（拙政园－留园－虎丘）')), ['故宫', '景山', '苏州市苏州园林（拙政园－留园－虎丘）']);
  assert.deepEqual(plain(h.env.parseAttractionNames(undefined)), []);
  h.env.openEditor('itinerary'); h.names().value = Array.from({ length: 13 }, (_, i) => `景点${i}`).join('、');
  await h.save(); assert.equal(h.requests.length, 0); assert.match(h.dom.editError.textContent, /12/);
  h.names().value = '景'.repeat(61); await h.save(); assert.equal(h.requests.length, 0); assert.match(h.dom.editError.textContent, /60/);
});

test('conflict reload updates linked names while preserving explicit keep-local edits', () => {
  const h = harness(); h.env.openEditor('itinerary', { id: 'i', attraction_names: ['故宫'], version: 1 });
  h.env.showConflict({ id: 'i', attraction_names: ['景山'], version: 2 });
  assert.match(text(h.dom.conflictDetails), /关联游玩点的最新内容：景山/);
  h.env.loadRemoteConflict(); assert.equal(h.names().value, '景山');
  h.names().value = '故宫、景山'; h.env.showConflict({ id: 'i', attraction_names: ['天坛'], version: 3 });
  h.env.keepLocalConflict(); assert.equal(h.names().value, '故宫、景山'); assert.equal(h.state.currentEdit.item.version, 3);
});

test('itinerary cards show only opening time points, all-day hours or missing hours', () => {
  const h=harness();
  const card=h.env.itineraryCard({title:'公园',priority:'must',opening_start:'08:00',opening_end:'17:30',opening_source:'ai_estimate',opening_note:'以官方公告为准'});
  assert.match(text(card),/优先级：必去/);assert.match(text(card),/08:00–17:30/);
  assert.doesNotMatch(text(card),/以官方公告为准|AI 参考，待核实/);
  assert.match(text(h.env.itineraryCard({title:'外滩',opening_start:'00:00',opening_end:'23:59'})),/全天开放/);
  assert.match(text(h.env.itineraryCard({title:'未查询地点'})),/开放时间待补充/);
});

test('legacy is only offered for existing multi-place records; hours provenance survives unrelated edits', async () => {
  const h=harness();h.env.openEditor('itinerary');
  assert.equal(h.dom.editFields.querySelector('[name="visit_kind"]').children.some(n=>n.value==='legacy'),false);
  const item={id:'i',version:1,title:'公园',date:'2030-01-01',city_id:'beijing',visit_kind:'legacy',duration_minutes:90,
    opening_start:'08:00',opening_end:'17:00',opening_source:'ai_estimate',opening_note:'AI 参考',attraction_names:[]};
  h.env.openEditor('itinerary',item);
  assert.ok(h.dom.editFields.querySelector('[name="visit_kind"]').children.some(n=>n.value==='legacy'));
  await h.save();assert.equal(h.requests.at(-1).body.opening_source,'ai_estimate');
  h.env.openEditor('itinerary',item);h.dom.editFields.querySelector('[name="opening_end"]').value='18:00';
  await h.save();assert.equal(h.requests.at(-1).body.opening_source,'user');assert.equal(h.requests.at(-1).body.opening_note,'');
});
