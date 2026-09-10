const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function harness() {
  const nodes = new Map(), requests = [];
  class Node {
    constructor(tag = "div", className = "", text = "") {
      this.tagName = tag; this.className = className; this.textContent = text;
      this.children = []; this.events = {}; this.dataset = {}; this.open = false;
      this.hidden = false; this.disabled = false;
    }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    setAttribute(key, value) { this[key] = value; }
    addEventListener(type, action) { this.events[type] = action; }
    querySelectorAll(selector) { return this.children.filter(child => child.tagName === selector); }
    querySelector() { return null; }
    focus() {} showModal() { this.open = true; } close() { this.open = false; }
  }
  const node = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
  const state = {
    projectId: "main", projectName: "测试项目", projectUnlocked: true, cityId: "shanghai", tab: "attraction", userId: "Alice",
    cities: [{ id: "shanghai", name: "上海" }], items: {
      attraction: [{ id: "a", version: 1, city_id: "shanghai", category: " 博物馆 " },
                   { id: "b", version: 1, city_id: "shanghai", category: "公园" },
                   { id: "c", version: 1, city_id: "shanghai", category: "博物馆" },
                   { id: "d", version: 1, city_id: "shanghai", category: "" },
                   { id: "other", version: 1, city_id: "beijing", category: "异地" }],
      food: [{ id: "f", city_id: "shanghai", category: "早餐" }], itinerary: [],
    },
  };
  const env = { window: {}, document: { getElementById: node }, state, requireIdentity: () => true,
    element: (tag, cls, text) => new Node(tag, cls, text), showToast() {}, fetchSnapshot: async () => {},
    requestJson: async (url, options) => { requests.push({ url, body: JSON.parse(options.body) }); return { data: { deleted_count: 2 } }; },
  };
  for (const file of ["place-taxonomy.js", "type-filter.js", "batch-selection.js"]) vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../public", file), "utf8"), env);
  const filter = env.window.TripTypeFilter;
  env.render = () => { filter.render(); env.window.TripBatch.renderToolbar(); };
  env.window.TripBatch.init(); env.render();
  const choose = value => node("type-filter-options").children.find(button => button.dataset.filterKey === JSON.stringify(value)).events.click();
  const visible = () => filter.filter(state.items[state.tab].filter(item => item.city_id === state.cityId));
  return { env, state, filter, node, choose, visible, requests, click: id => node(id).events.click() };
}

test("types and counts reflect current city, normalize surrounding whitespace and include uncategorized", () => {
  const h = harness();
  assert.deepEqual(h.node("type-filter-options").children.map(button => button.textContent), ["全部 4", "博物馆 2", "公园 1", "未分类 1"]);
  h.choose("博物馆");
  assert.deepEqual(h.visible().map(item => item.id), ["a", "c"]);
  assert.equal(h.node("type-filter-count").textContent, "显示 2 / 4 项地点");
  h.choose(""); assert.deepEqual(h.visible().map(item => item.id), ["d"]);
  h.choose(null); assert.equal(h.visible().length, 4);
});

test("switching page, city, project or identity resets the type selection", () => {
  for (const [key, value] of [["tab", "food"], ["cityId", "beijing"], ["projectId", "other"], ["userId", "Bob"]]) {
    const h = harness(); h.choose("公园"); h.state[key] = value; h.env.render();
    assert.equal(h.filter.selection(), null);
  }
  const h = harness(); h.state.tab = "itinerary"; h.env.render(); assert.equal(h.node("type-filter").hidden, true);
});

test("polling preserves the selection and DOM; an empty selected type can return to all", () => {
  const h = harness(); h.choose("公园");
  const button = h.node("type-filter-options").children[0]; h.env.render();
  assert.equal(h.node("type-filter-options").children[0], button);
  h.state.items.attraction = h.state.items.attraction.filter(item => item.id !== "b"); h.env.render();
  assert.equal(h.visible().length, 0); assert.equal(h.filter.selection(), "公园");
  assert.ok(h.filter.emptyState()); h.choose(null); assert.equal(h.visible().length, 3);
});

test("batch select all and deletion apply only to filtered visible results", async () => {
  const h = harness(); h.choose("博物馆"); h.click("batch-open");
  assert.equal(h.node("batch-select-all").textContent, "全选筛选结果");
  h.click("batch-select-all"); h.click("batch-delete"); await h.click("batch-confirm-delete");
  assert.deepEqual(JSON.parse(JSON.stringify(h.requests[0].body.items)), [{ id: "a", version: 1 }, { id: "c", version: 1 }]);
});

test("changing type clears previous batch selections", () => {
  const h = harness(); h.choose("博物馆"); h.click("batch-open"); h.click("batch-select-all");
  h.choose("公园"); assert.equal(h.node("batch-toolbar").hidden, true);
  h.click("batch-open"); assert.equal(h.node("batch-delete").disabled, true);
});

test("multi-tag any/all selection composes with category and resets across cities", () => {
  const h = harness();
  h.state.items.attraction[0].tags = ['亲子', '室内'];
  h.state.items.attraction[1].tags = ['亲子'];
  h.state.items.attraction[2].tags = ['室内'];
  h.env.render();
  const tag = name => h.node('tag-filter-options').children.find(button => button.dataset.tagKey === name).events.click();
  tag('亲子'); tag('室内');
  assert.deepEqual(h.visible().map(item => item.id), ['a', 'b', 'c']);
  h.node('tag-filter-mode').children.find(button => button.dataset.mode === 'all').events.click();
  assert.deepEqual(h.visible().map(item => item.id), ['a']);
  h.choose('公园'); assert.equal(h.visible().length, 0); assert.ok(h.filter.emptyState());
  h.click('filter-reset'); assert.equal(h.visible().length, 4);
  tag('室内'); h.state.cityId = 'beijing'; h.env.render();
  assert.equal(h.filter.active(), false);
});

test("cuisine filters as a tag, deduplicates counts, and tag search does not change results", () => {
  const h = harness(); h.state.tab = 'food';
  h.state.items.food = [{ id: 'f1', city_id: 'shanghai', category: '特色菜肴', cuisine: '川菜', tags: ['川菜', '麻辣'] },
    { id: 'f2', city_id: 'shanghai', category: '特色菜肴', cuisine: '本帮菜', tags: ['甜口'] }];
  h.env.render();
  const option = h.node('tag-filter-options').children.find(button => button.dataset.tagKey === '川菜');
  assert.equal(option.textContent, '川菜 1'); option.events.click();
  assert.deepEqual(h.visible().map(item => item.id), ['f1']);
  h.node('tag-filter-search').value = '甜'; h.node('tag-filter-search').events.input();
  assert.deepEqual(h.node('tag-filter-options').children.map(button => button.dataset.tagKey), ['川菜', '甜口']);
  assert.deepEqual(h.visible().map(item => item.id), ['f1']);
});

test("tag-only batch deletion is limited to visible items and changing tags clears selection", async () => {
  const h = harness(); h.state.items.attraction[0].tags = ['亲子']; h.state.items.attraction[2].tags = ['室内']; h.env.render();
  h.node('tag-filter-options').children.find(button => button.dataset.tagKey === '亲子').events.click();
  h.click('batch-open'); assert.equal(h.node('batch-select-all').textContent, '全选筛选结果');
  h.click('batch-select-all'); h.click('batch-delete'); await h.click('batch-confirm-delete');
  assert.deepEqual(JSON.parse(JSON.stringify(h.requests[0].body.items)), [{ id: 'a', version: 1 }]);
  h.click('batch-open'); h.click('batch-select-all');
  h.node('tag-filter-options').children.find(button => button.dataset.tagKey === '室内').events.click();
  assert.equal(h.node('batch-toolbar').hidden, true);
});

test("polling retains selected tags with zero matches and preserves unrelated DOM", () => {
  const h = harness(); h.state.items.attraction[0].tags = ['亲子']; h.env.render();
  h.node('tag-filter-options').children[0].events.click();
  const button = h.node('tag-filter-options').children[0]; h.env.render();
  assert.equal(h.node('tag-filter-options').children[0], button);
  h.state.items.attraction[0].tags = []; h.env.render();
  assert.equal(h.visible().length, 0);
  assert.equal(h.node('tag-filter-options').children[0].textContent, '亲子 0');
  assert.equal(h.filter.active(), true);
});

test("itinerary status composes with categories and tags, including old records", () => {
  const h = harness();
  h.state.items.attraction[0].in_itinerary = true;
  h.state.items.attraction[0].tags = ['室内'];
  h.state.items.attraction[2].itinerary_refs = [{ id: 'plan', date: '2026-10-01' }];
  h.env.render();
  const status = value => h.node('itinerary-filter-options').children.find(button => button.dataset.itineraryStatus === value).events.click();
  assert.deepEqual(h.node('itinerary-filter-options').children.map(button => button.textContent), ['全部 4', '已排行程 2', '未排行程 2']);
  status('scheduled'); assert.deepEqual(h.visible().map(item => item.id), ['a', 'c']);
  h.choose('博物馆');
  h.node('tag-filter-options').children.find(button => button.dataset.tagKey === '室内').events.click();
  assert.deepEqual(h.visible().map(item => item.id), ['a']);
  status('unscheduled'); assert.equal(h.visible().length, 0); assert.ok(h.filter.emptyState());
  h.click('filter-reset'); status('unscheduled'); assert.deepEqual(h.visible().map(item => item.id), ['b', 'd']);
  h.state.tab = 'food'; h.env.render(); assert.equal(h.node('itinerary-filter').hidden, true); assert.equal(h.filter.active(), false);
});

test("itinerary status limits batch deletion and resets selection on change", async () => {
  const h = harness(); h.state.items.attraction[0].in_itinerary = true; h.env.render();
  const status = value => h.node('itinerary-filter-options').children.find(button => button.dataset.itineraryStatus === value).events.click();
  status('scheduled'); h.click('batch-open'); h.click('batch-select-all');
  h.click('batch-delete'); await h.click('batch-confirm-delete');
  assert.deepEqual(JSON.parse(JSON.stringify(h.requests[0].body.items)), [{ id: 'a', version: 1 }]);
  h.click('batch-open'); h.click('batch-select-all'); status('unscheduled');
  assert.equal(h.node('batch-toolbar').hidden, true);
  h.click('batch-open'); assert.equal(h.node('batch-delete').disabled, true);
});

test("itinerary status survives polling but resets across scope changes", () => {
  for (const [key, value] of [['cityId', 'beijing'], ['projectId', 'other'], ['userId', 'Bob']]) {
    const h = harness(); h.state.items.attraction[0].in_itinerary = true; h.env.render();
    h.node('itinerary-filter-options').children.find(button => button.dataset.itineraryStatus === 'scheduled').events.click();
    const button = h.node('itinerary-filter-options').children[1]; h.env.render();
    assert.equal(h.node('itinerary-filter-options').children[1], button);
    h.state.items.attraction[0].in_itinerary = false; h.env.render();
    assert.equal(h.visible().length, 0); assert.equal(h.filter.active(), true);
    h.state[key] = value; h.env.render(); assert.equal(h.filter.active(), false);
  }
});
