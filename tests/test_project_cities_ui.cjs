const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness() {
  const nodes = new Map(), listeners = {}, switches = [];
  class Node {
    constructor(tag, cls, text) { Object.assign(this, { tag, className: cls, textContent: text || '', children: [], dataset: {}, attributes: {}, listeners: {}, open: false, shows: 0 }); }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    showModal() { this.open = true; this.shows++; }
    close() { this.open = false; listeners.close?.(); }
    focus() {}
  }
  const node = id => { if (!nodes.has(id)) nodes.set(id, new Node('div')); return nodes.get(id); };
  const cities = [
    { city_id: 'shanghai', city_name: '上海', count: 1, dates: ['2026-10-01'] },
    { city_id: 'beijing', city_name: '北京', count: 1, dates: ['2026-10-01'] },
  ];
  const conflict = { date: '2026-10-01', items: cities.map((city, i) => ({ ...city, id: String(i), title: '<img onerror=alert(1)>', start_time: i ? '' : '09:00' })) };
  const state = { cityId: 'shanghai', userId: 'tester', projectUnlocked: true, projectItinerary: { cities, conflicts: [conflict] } };
  const env = { state, window: { TripUI: { switchCity: id => switches.push(id) } },
    document: { getElementById: node, querySelector: () => env.editing ? {} : null, addEventListener: (name, fn) => { listeners[name] = fn; } },
    element: (tag, cls, text) => new Node(tag, cls, text) };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../public/project-cities.js'), 'utf8'), env);
  const ui = env.window.TripCities; ui.init();
  return { env, state, node, ui, switches, listeners };
}

test('overview shows all planned cities, dates and current selection; buttons switch cities', () => {
  const h = harness(); h.ui.render();
  assert.equal(h.node('project-cities-count').textContent, '已规划 2 个城市');
  const cards = h.node('project-city-list').children.map(card => card.children[0]);
  assert.equal(cards.length, 2);
  assert.equal(cards[0].attributes['aria-pressed'], 'true');
  assert.equal(cards[1].children[2].textContent, '2026-10-01');
  cards[1].listeners.click(); assert.deepEqual(h.switches, ['beijing']);
});

test('closing a warning keeps data intact and polling/city switching does not reopen it', () => {
  const h = harness(), original = JSON.stringify(h.state.projectItinerary); h.ui.render();
  const dialog = h.node('city-conflict-dialog'); assert.equal(dialog.shows, 1);
  h.node('city-conflict-keep').listeners.click(); h.ui.render();
  h.state.cityId = 'beijing'; h.ui.render();
  assert.equal(dialog.open, false); assert.equal(dialog.shows, 1);
  assert.equal(JSON.stringify(h.state.projectItinerary), original);
  h.node('city-conflict-open').listeners.click(); assert.equal(dialog.shows, 2);
});

test('changed, newly introduced and resolved/reintroduced conflicts prompt again', () => {
  const h = harness(); h.ui.render(); h.node('city-conflict-dialog').close();
  h.state.projectItinerary.conflicts[0].items[0].start_time = '10:00'; h.ui.render();
  assert.equal(h.node('city-conflict-dialog').shows, 2);
  const saved = h.state.projectItinerary.conflicts;
  h.state.projectItinerary.conflicts = []; h.ui.render();
  assert.equal(h.node('city-conflict-dialog').open, false);
  assert.equal(h.node('city-conflict-open').hidden, true);
  h.state.projectItinerary.conflicts = saved; h.ui.render();
  assert.equal(h.node('city-conflict-dialog').shows, 3);
});

test('waits for an active editor to close and uses the latest snapshot for its warning', () => {
  const h = harness(); h.env.editing = true; h.ui.render();
  assert.equal(h.node('city-conflict-dialog').open, false);
  h.env.editing = false; h.listeners.close();
  assert.equal(h.node('city-conflict-dialog').open, true);
  const list = h.node('city-conflict-list').children[0].children[1];
  assert.match(list.children[0].textContent, /09:00 · 上海 · <img onerror=alert\(1\)>/);
  assert.match(list.children[1].textContent, /时间待定 · 北京/);
  assert.equal(list.children[0].children.length, 0);
});

test('empty or older snapshots remain usable and locking clears all project data and reminders', () => {
  const h = harness(); h.ui.render();
  h.state.projectUnlocked = false; h.ui.render();
  assert.equal(h.node('project-cities').hidden, true);
  assert.equal(h.node('project-city-list').children.length, 0);
  assert.equal(h.node('city-conflict-list').children.length, 0);
  assert.equal(h.node('city-conflict-dialog').open, false);
  h.state.projectUnlocked = true; h.state.projectItinerary = null; h.ui.render();
  assert.equal(h.node('project-cities-empty').hidden, false);
  h.state.projectItinerary = { cities: [], conflicts: [] }; h.ui.render();
  assert.match(h.node('project-cities-empty').textContent, /添加或导入行程/);
});
