const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function harness(changes = {}) {
  class Node {
    constructor(tag) { Object.assign(this, {tag, children: [], dataset: {}, events: {}, attrs: {}}); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(key, value) { this.attrs[key] = value; }
    addEventListener(key, fn) { this.events[key] = fn; }
  }
  const visits = ['a', 'b'].map((id, i) => ({id, title: id, date: '2030-01-01', city_id: 'shanghai',
    version: 1, position: i + 1, time_block: i ? 'afternoon' : 'morning', ...(!i ? changes : {})}));
  const plan = {city_id: 'shanghai', date: '2030-01-01', version: 'day1', visits: structuredClone(visits), settings: {}};
  const cards = new Node('div'), requests = [], notices = [], cardOptions = [];
  const env = {window: {}, Date, structuredClone,
    state: {projectId: 'main', cityId: 'shanghai', userId: 'tester', projectUnlocked: true,
      items: {itinerary: visits}, dailyPlans: [plan]},
    dom: {cards}, document: {createElement: tag => new Node(tag)},
    itineraryCard: (visit, options) => { cardOptions.push(options); return new Node('article'); }, formatItineraryDate: date => date,
    requireIdentity: () => true, showToast: text => notices.push(text), fetchSnapshot: async () => {},
    requestJson: async (url, options = {}) => {
      if (options.method === 'PUT') requests.push(JSON.parse(options.body));
      return {data: structuredClone(plan)};
    }};
  vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname, '../public/daily-plans.js'), 'utf8'), env);
  env.window.TripDaily.render(visits);
  const all = node => [node, ...node.children.flatMap(all)];
  return {plan, env, requests, notices, cardOptions, nodes: () => all(cards)};
}

async function drop(h, from, block) {
  h.nodes().find(n => n.dataset.visitId === from).events.dragstart({});
  h.nodes().find(n => n.dataset.blockId === block).events.drop({preventDefault() {}});
  await new Promise(resolve => setImmediate(resolve));
}

test('dropping into a slot saves the shared order and membership atomically', async () => {
  const h = harness(); await drop(h, 'a', 'afternoon');
  assert.deepEqual(h.requests[0].order, ['b', 'a']);
  assert.deepEqual(h.requests[0].updates, [{id: 'a', changes: {time_block: 'afternoon', is_backup: false}}]);
  assert.equal(h.requests[0].version, 'day1');
});

test('saved route preview is scoped to the selected day and opens its route planner', () => {
  const h=harness(), opened=[];
  h.env.window.TripMaps={open:day=>opened.push(day)};
  h.env.window.TripProject={url:(path,project)=>`${path}&project=${project}`};
  h.plan.route_preview={version:'saved-v1',planned:1,total:1,minutes:10,stops:[{number:1,name:'公园'},{number:2,name:'广场'}]};
  h.env.window.TripDaily.render(h.env.state.items.itinerary);
  const image=h.nodes().find(n=>n.className==='daily-route-image');
  assert.match(image.src,/date=2030-01-01.*v=saved-v1.*project=main/);
  h.nodes().find(n=>n.className==='daily-route-image-button').events.click();
  assert.deepEqual(opened,['2030-01-01']);
  h.plan.route_preview=null;
  h.env.window.TripDaily.render(h.env.state.items.itinerary);
  assert.equal(h.nodes().some(n=>n.className==='daily-route-image'),false);
});

test('empty slots accept a dropped place without inventing an activity', async () => {
  const h = harness(); await drop(h, 'a', 'midday');
  assert.deepEqual(h.requests[0].order, ['a', 'b']);
  assert.equal(h.requests[0].updates[0].changes.time_block, 'midday');
  assert.equal(h.requests[0].additions, undefined);
});

test('locked blocks reject cross-slot drops', async () => {
  for (const constraint of [{block_locked: true}]) {
    const h = harness(constraint); await drop(h, 'a', 'afternoon');
    assert.equal(h.requests.length, 0);
    assert.match(h.notices[0], /解除时段锁定/);
  }
});

test('a concurrent edit prevents a stale move from being saved', async () => {
  const h = harness(); h.plan.visits[0].version++;
  await drop(h, 'a', 'afternoon');
  assert.equal(h.requests.length, 0);
  assert.match(h.notices[0], /已变化/);
});

test('places in the same slot share one compact heading and retain local reorder controls', async () => {
  const h = harness({time_block: 'afternoon'});
  const group = h.nodes().find(n => n.dataset.blockId === 'afternoon');
  assert.equal(group.children.filter(n => n.tag === 'article').length, 2);
  assert.equal(h.nodes().filter(n => n.textContent === '下午 14:00–16:00').length, 1);
  assert.equal(h.nodes().filter(n => n.tag === 'select' && n.className !== 'daily-day-select').length, 0);
  assert.ok(h.cardOptions.every(options => options.showTimeBlock === false));
  await h.nodes().find(n => n.textContent === '↓ 下移').events.click();
  assert.deepEqual(h.requests[0].order, ['b', 'a']);
  assert.deepEqual(h.requests[0].updates, []);
});

test('empty saved days retain every slot without creating placeholder places', () => {
  const h = harness(); h.env.state.items.itinerary = [];
  h.env.window.TripDaily.render([]);
  assert.equal(h.nodes().filter(n => n.className === 'daily-slot').length, 7);
  assert.equal(h.nodes().filter(n => n.tag === 'article').length, 0);
  assert.ok(h.nodes().some(n => n.textContent === '午后休息 13:00–14:00'));
  assert.ok(h.nodes().some(n => n.textContent === '机动 16:00–17:00'));
  assert.ok(h.nodes().some(n => n.textContent === '未开启'));
  assert.ok(h.nodes().some(n => n.attrs['aria-label'] === '向上午添加地点'));
});

test('nonconsecutive saved places in the same slot appear together only once', () => {
  const h = harness();
  h.env.state.items.itinerary.push({...h.env.state.items.itinerary[0], id:'c', position:3});
  h.env.window.TripDaily.render(h.env.state.items.itinerary);
  const group = h.nodes().find(n => n.dataset.blockId === 'morning');
  assert.deepEqual(group.children.filter(n => n.tag === 'article').map(n => n.dataset.visitId), ['a', 'c']);
  assert.equal(h.nodes().filter(n => n.textContent === '上午 09:00–11:00').length, 1);
});

test('backup itinerary has a direct restore action that preserves its time block', async () => {
  const h=harness({is_backup:true,block_locked:true,time_block:'morning'});
  assert.ok(h.nodes().some(n=>n.textContent==='备选行程'));
  const action=h.nodes().find(n=>n.textContent==='添加到行程中');
  await action.events.click();
  assert.equal(h.requests.length,1);
  assert.deepEqual(h.requests[0].updates,[{id:'a',changes:{is_backup:false}}]);
  assert.equal(h.requests[0].version,'day1');
});

test('restoring a concurrently edited backup cannot overwrite it', async () => {
  const h=harness({is_backup:true});h.plan.visits[0].version++;
  await h.nodes().find(n=>n.textContent==='添加到行程中').events.click();
  assert.equal(h.requests.length,0);assert.match(h.notices[0],/已变化/);
});

test('date selection shows one day and routes actions to that day, surviving synchronization', () => {
  const h=harness(), seen=[];
  h.env.state.items.itinerary.push({...h.env.state.items.itinerary[0], id:'c', date:'2030-01-03'});
  h.env.state.dailyPlans.push({city_id:'shanghai',date:'2030-01-02',settings:{}});
  h.env.window.TripMaps={open:date=>seen.push(date)};
  const render=()=>h.env.window.TripDaily.render(h.env.state.items.itinerary);
  const select=()=>h.nodes().find(n=>n.className==='daily-day-select');
  render();
  assert.equal(select().children.length,3);
  assert.equal(h.nodes().filter(n=>n.className==='itinerary-day').length,1);
  assert.deepEqual(h.nodes().filter(n=>n.tag==='article').map(n=>n.dataset.visitId),['a','b']);
  assert.equal(h.nodes().find(n=>n.textContent==='← 上一天').disabled,true);
  h.nodes().find(n=>n.textContent==='下一天 →').events.click();
  assert.equal(select().value,'2030-01-02');
  assert.equal(h.nodes().filter(n=>n.tag==='article').length,0);
  select().value='2030-01-03';select().events.change();
  assert.deepEqual(h.nodes().filter(n=>n.tag==='article').map(n=>n.dataset.visitId),['c']);
  h.nodes().find(n=>n.textContent==='规划路线').events.click();
  assert.deepEqual(seen,['2030-01-03']);
  render();assert.equal(select().value,'2030-01-03');
  assert.equal(h.nodes().find(n=>n.textContent==='下一天 →').disabled,true);
  assert.equal(h.requests.length,0);
});

test('date selection is scoped to city and project and falls back when its day disappears', () => {
  const h=harness();
  const first=h.env.state.items.itinerary;
  h.env.state.dailyPlans.push({city_id:'shanghai',date:'2030-01-02',settings:{}});
  const render=items=>h.env.window.TripDaily.render(items);
  const select=()=>h.nodes().find(n=>n.className==='daily-day-select');
  render(first); select().value='2030-01-02';select().events.change();
  h.env.state.cityId='beijing';render([]);
  assert.equal(select(),undefined);assert.equal(h.env.window.TripDaily.selectedDate(),'');
  h.env.state.cityId='shanghai';render(first);assert.equal(select().value,'2030-01-02');
  h.env.state.projectId='other';render(first);assert.equal(select().value,'2030-01-01');
  h.env.state.projectId='main';render(first);assert.equal(select().value,'2030-01-02');
  h.env.state.dailyPlans=h.env.state.dailyPlans.slice(0,1);render(first);
  assert.equal(select().value,'2030-01-01');
});
