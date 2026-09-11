const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function response(city = "beijing", options = {}) {
  return { city_id: city, city_name: city === "beijing" ? "北京" : "广州", supported: true, available: true,
    map: { id: city, name: `${city} metro`, image_url: `/api/metro-maps/image/${city}/v1.svg`, source_url: `https://commons.wikimedia.org/wiki/File:${city}.svg`,
      source_name: "Wikimedia Commons", author: "Community cartographer", license: "CC BY-SA 4.0", license_url: "https://creativecommons.org/licenses/by-sa/4.0/",
      official_url: `https://example.com/${city}/metro`, source_updated_at: "2026-08-03T10:00:00Z", checked_at: "2026-09-10T01:02:00Z", downloaded_at: "2026-09-09T01:02:00Z",
      format: "svg", width: 6000, height: 4000, version: "v1" }, update: { status: "idle", error: "" }, message: "", ...options };
}

function harness(handler = (url) => ({ data: response(url.includes("guangzhou") ? "guangzhou" : "beijing") })) {
  const nodes = new Map(), requests = [], timers = new Map();
  let nextTimer = 0, gate = 0, identity = 0;
  class Node {
    constructor(tag = "div") {
      this.tagName = tag; this.children = []; this.events = {}; this.dataset = {}; this.attributes = {};
      this.textContent = ""; this.className = ""; this.disabled = false; this.hidden = false; this.open = false; this.srcWrites = 0;
      this.value = ""; this.style = {}; this.clientWidth = 900; this.clientHeight = 600;
      this.classList = { add() {}, remove() {}, toggle() {} };
    }
    get firstElementChild() { return this.children.find((child) => child instanceof Node); }
    get src() { return this.attributes.src || ""; }
    set src(value) { this.attributes.src = value; this.srcWrites += 1; }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = items; this.textContent = ""; }
    addEventListener(type, listener) { this.events[type] = listener; }
    setAttribute(name, value) { this.attributes[name] = value; }
    getAttribute(name) { return this.attributes[name] ?? null; }
    showModal() { this.open = true; }
    close() { this.open = false; this.events.close?.(); }
    focus() {}
    querySelectorAll() { return []; }
    reset() {}
    getBoundingClientRect() { return { left: 0, top: 0, width: 900, height: 600 }; }
  }
  function node(id) { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); }
  const state = { projectId: "main", projectUnlocked: true, userId: "Alice", cityId: "beijing", tab: "itinerary",
    cities: [{ id: "beijing", name: "北京", canonical_name: "北京", map_query: "北京市" },
      { id: "guangzhou", name: "广州", canonical_name: "广州", map_query: "广东省广州市" }], items: { itinerary: [] } };
  function element(tag, className = "", text = "") { const result = new Node(tag); result.className = className; result.textContent = text; return result; }
  const env = {
    console, Date, Map, Set, JSON, Math, Number, String, Array, Boolean, RegExp, URL, URLSearchParams, TextEncoder, encodeURIComponent,
    requestAnimationFrame: (fn) => fn(), ResizeObserver: class { observe() {} },
    setTimeout(fn, delay) { const id = ++nextTimer; timers.set(id, { fn, delay }); return id; }, clearTimeout: (id) => timers.delete(id),
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    document: { getElementById: node, createElement: (tag) => new Node(tag), createTextNode: (text) => text, querySelectorAll: () => [] },
    window: { TripProject: { storageKey: (key) => key }, TripBatch: { reset() {}, canNavigate: () => true } },
    state, dom: { cards: new Node() }, element,
    externalAnchor(text, url) { const result = element("a", "map-link", text); result.href = url; return result; },
    appExternalAnchor(text, url) { const result = element("a", "map-link", text); result.href = url; return result; },
    requestJson(url, options = {}) { requests.push({ url, method: options.method || "GET", body: options.body ? JSON.parse(options.body) : undefined }); return Promise.resolve(handler(url, options, requests.length)); },
    showProjectGate() { gate += 1; }, openIdentity() { identity += 1; }, showToast() {}, fetchSnapshot: async () => {},
  };
  vm.createContext(env);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../public/external-links.js"), "utf8"), env);
  env.amapUrl = (city, keyword, options) => env.window.TripLinks.amapSearch(city, keyword, options);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../public/ui-enhancements.js"), "utf8"), env);
  env.window.TripUI.init();
  const controller = env.window.TripUI;
  function walk(root = node("city-metro-content")) { return [root, ...root.children.filter((item) => item instanceof Node).flatMap((item) => walk(item))]; }
  const byClass = (name) => walk().find((item) => item.className.split(" ").includes(name));
  const text = () => walk().map((item) => item.textContent).join(" ");
  function select(city) { state.cityId = city; controller.renderCity(); }
  async function open() { controller.renderCity(); await flush(); controller.openCityMetro(); await flush(); }
  async function tick() { const [id, timer] = timers.entries().next().value || []; assert.ok(timer, "a polling timer should exist"); timers.delete(id); timer.fn(); await flush(); }
  return { env, state, requests, timers, controller, open, node, walk, byClass, text, select, tick, get gate() { return gate; }, get identity() { return identity; } };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

test("any supported city shows its local map, attribution and dynamic zoom title", async () => {
  const h = harness(); await h.open();
  assert.equal(h.byClass("metro-image").src, response().map.image_url);
  assert.match(h.text(), /北京轨道交通线路图/);
  assert.match(h.text(), /Community cartographer/);
  assert.match(h.text(), /源文件修订.*最后检查.*本地下载/);
  assert.match(h.text(), /不代表运营更新日期/);
  assert.ok(h.walk().some((node) => node.href === response().map.source_url));
  assert.ok(h.walk().some((node) => node.href === response().map.license_url));
  assert.ok(h.walk().some((node) => node.href === response().map.official_url));
  h.byClass("metro-preview").events.click();
  assert.equal(h.node("metro-title").textContent, "北京轨道交通线路图");
  assert.equal(h.node("metro-full-image").src, response().map.image_url);
  assert.equal(h.node("metro-full-image").alt, "北京轨道交通高清线路图");
  assert.equal(h.node("metro-dialog").open, true);
});

test("shared snapshot refresh keeps the same preview node and current zoom", async () => {
  const h = harness(); await h.open();
  const preview = h.byClass("metro-preview"), img = h.byClass("metro-image");
  preview.events.click(); h.node("metro-full-image").events.load(); h.node("metro-plus").events.click();
  for (let i = 0; i < 4; i += 1) h.controller.renderCity();
  assert.equal(h.byClass("metro-preview"), preview);
  assert.equal(img.srcWrites, 1);
  assert.equal(h.node("metro-scale").textContent, "150%");
  assert.equal(h.requests.length, 1);
});

test("update is single flight, polls, replaces the preview but preserves an open old image", async () => {
  const post = deferred(); let reads = 0;
  const newer = response(); newer.map = { ...newer.map, version: "v2", image_url: "/api/metro-maps/image/beijing/v2.svg" }; newer.update.status = "ready";
  const h = harness((url, options) => options.method === "POST" ? post.promise : { data: ++reads === 1 ? response() : newer });
  await h.open();
  h.byClass("metro-preview").events.click(); h.node("metro-full-image").events.load();
  const button = h.byClass("metro-update-button");
  const first = button.events.click(); const second = button.events.click();
  assert.equal(button.disabled, true);
  assert.equal(h.requests.filter((item) => item.method === "POST").length, 1);
  assert.deepEqual(h.requests.at(-1).body, { city_id: "beijing" });
  post.resolve({ data: response("beijing", { update: { status: "running", error: "" } }) });
  await Promise.all([first, second]);
  assert.equal([...h.timers.values()][0].delay, 2000);
  await h.tick();
  assert.equal(h.byClass("metro-image").src, newer.map.image_url);
  assert.equal(h.node("metro-full-image").src, response().map.image_url);
  assert.match(h.node("metro-help").textContent, /已更新/);
  h.node("metro-close").events.click(); h.byClass("metro-preview").events.click();
  assert.equal(h.node("metro-full-image").src, newer.map.image_url);
  assert.equal(button.disabled, false);
  assert.match(h.byClass("metro-update-status").textContent, /线路图检查完成/);
  assert.equal(h.timers.size, 0);
});

test("late metadata from a previous city cannot replace the current city map", async () => {
  const old = deferred();
  const h = harness((url) => url.includes("beijing") ? old.promise : { data: response("guangzhou") });
  h.controller.renderCity(); h.select("guangzhou"); await h.open();
  old.resolve({ data: response() }); await flush();
  assert.equal(h.byClass("metro-image").src, response("guangzhou").map.image_url);
  assert.match(h.text(), /广州轨道交通线路图/);
  h.byClass("metro-preview").events.click();
  assert.equal(h.node("metro-title").textContent, "广州轨道交通线路图");
});

test("changing city closes the old map and ignores a late update response", async () => {
  const old = deferred();
  const h = harness((url, options) => options.method === "POST" ? old.promise : { data: response(url.includes("guangzhou") ? "guangzhou" : "beijing") });
  await h.open(); h.byClass("metro-preview").events.click();
  const updating = h.byClass("metro-update-button").events.click();
  h.select("guangzhou"); await h.open();
  assert.equal(h.node("metro-dialog").open, false);
  old.resolve({ data: response("beijing", { update: { status: "running", error: "" } }) }); await updating;
  assert.equal(h.byClass("metro-image").src, response("guangzhou").map.image_url);
  assert.equal(h.timers.size, 0);
});

test("an unlisted city hides the city entry without creating a transport page", async () => {
  const h = harness(() => ({ data: response("beijing", { supported: false, available: false, map: null }) }));
  await h.open();
  assert.equal(h.node("city-metro-open").hidden, true);
  assert.equal(h.node("city-metro-dialog").open, false);
  assert.equal(h.node("city-metro-content").children.length, 0);
});

test("a supported city without an offline file can request its first download", async () => {
  const h = harness((url, options) => ({ data: options.method === "POST" ? response() : response("beijing", { available: false, map: null }) }));
  await h.open();
  assert.equal(h.byClass("metro-update-button").textContent, "下载高清线路图");
  assert.equal(h.byClass("metro-preview").hidden, true);
  await h.byClass("metro-update-button").events.click();
  assert.equal(h.byClass("metro-preview").hidden, false);
  assert.equal(h.byClass("metro-update-button").textContent, "检查并更新线路图");
});

test("update connection failure leaves city navigation usable and status retry uses GET", async () => {
  let calls = 0;
  const h = harness((url, options) => options.method === "POST" ? Promise.reject(new Error("暂时无法连接")) : { data: response() });
  await h.open();
  await h.byClass("metro-update-button").events.click();
  assert.match(h.text(), /暂时无法连接/);
  const cityMap = h.walk().find((node) => node.href?.startsWith("https://uri.amap.com/search?"));
  assert.ok(cityMap);
  const url = new URL(cityMap.href);
  assert.equal(url.searchParams.get("keyword"), "北京市");
  assert.equal(url.searchParams.get("city"), "北京");
  assert.equal(url.searchParams.get("view"), "map");
  assert.equal(url.searchParams.get("callnative"), "1");
  assert.ok(h.walk().some((node) => node.href?.startsWith("https://api.map.baidu.com/")));
  const retry = h.walk().find((node) => node.textContent === "重新加载状态");
  await retry.events.click();
  assert.equal(h.byClass("metro-preview").hidden, false);
  assert.equal(h.requests.at(-1).method, "GET");
});

test("a failed download preserves the existing map and allows another update", async () => {
  const h = harness((url, options) => ({ data: response("beijing", options.method === "POST" ? { update: { status: "failed", error: "来源站暂时不可用" } } : {}) }));
  await h.open();
  const img = h.byClass("metro-image"); await h.byClass("metro-update-button").events.click();
  assert.equal(h.byClass("metro-image"), img);
  assert.equal(img.srcWrites, 1);
  assert.match(h.text(), /来源站暂时不可用/);
  assert.equal(h.byClass("metro-update-button").disabled, false);
  assert.equal(h.timers.size, 0);
});

test("a polling failure preserves the available image and permits status recovery", async () => {
  let reads = 0;
  const h = harness(() => ++reads === 2 ? Promise.reject(new Error("检查连接中断")) : { data: response("beijing", { update: { status: reads === 1 ? "running" : "ready", error: "" } }) });
  await h.open();
  const img = h.byClass("metro-image"); await h.tick();
  assert.equal(h.byClass("metro-image"), img);
  assert.equal(img.srcWrites, 1);
  assert.match(h.text(), /检查连接中断/);
  assert.equal(h.timers.size, 0);
  await h.walk().find((node) => node.textContent === "重新加载状态").events.click();
  assert.equal(h.byClass("metro-update-button").disabled, false);
  assert.equal(img.srcWrites, 1);
});

test("image errors reset when reopening or changing city, including loading text", async () => {
  const h = harness(); await h.open();
  h.byClass("metro-preview").events.click();
  h.node("metro-full-image").events.error();
  assert.match(h.node("metro-loading").textContent, /加载失败/);
  h.node("metro-close").events.click(); h.byClass("metro-preview").events.click();
  assert.equal(h.node("metro-full-image").srcWrites, 2);
  assert.equal(h.node("metro-loading").textContent, "正在加载高清线路图…");
  h.node("metro-full-image").events.load();
  assert.equal(h.node("metro-loading").hidden, true);
  h.select("guangzhou"); await h.open(); h.byClass("metro-preview").events.click();
  assert.equal(h.node("metro-loading").hidden, false);
  assert.equal(h.node("metro-full-image").hidden, true);
  assert.equal(h.node("metro-full-image").src, response("guangzhou").map.image_url);
});

test("updating requires an unlocked project and selected user", async () => {
  const h = harness(); await h.open();
  h.state.projectUnlocked = false; await h.byClass("metro-update-button").events.click();
  assert.equal(h.gate, 1);
  h.state.projectUnlocked = true; h.state.userId = ""; await h.byClass("metro-update-button").events.click();
  assert.equal(h.identity, 1);
  assert.equal(h.requests.filter((item) => item.method === "POST").length, 0);
});

test("polling follows the city dialog, regardless of the selected page", async () => {
  const h = harness(() => ({ data: response("beijing", { update: { status: "running", error: "" } }) }));
  await h.open(); assert.equal(h.timers.size, 1);
  h.state.tab = "food"; h.controller.renderCity(); assert.equal(h.timers.size, 1);
  h.node("city-metro-close").events.click(); assert.equal(h.timers.size, 0);
  h.controller.openCityMetro(); assert.equal(h.timers.size, 1);
  await h.tick(); assert.equal(h.requests.length, 2);
});

test("keyboard zoom remains clamped between fit and 32 times magnification", async () => {
  const h = harness(); await h.open(); h.byClass("metro-preview").events.click();
  const key = (value) => h.node("metro-stage").events.keydown({ key: value, preventDefault() {} });
  for (let i = 0; i < 15; i += 1) key("+");
  assert.equal(h.node("metro-scale").textContent, "3200%");
  assert.equal(h.node("metro-plus").disabled, true);
  key("0"); key("-");
  assert.equal(h.node("metro-scale").textContent, "100%");
  assert.equal(h.node("metro-minus").disabled, true);
});

test("city entry works across pages without replacing their content", async () => {
  const h = harness();
  const original = h.env.dom.cards;
  for (const tab of ["itinerary", "attraction", "food", "activity"]) {
    h.state.tab = tab; await h.open();
    assert.equal(h.node("city-metro-open").hidden, false);
    assert.equal(h.node("city-metro-open").attributes["aria-label"], "查看北京轨道交通图");
    assert.equal(h.node("city-metro-dialog").open, true);
    assert.equal(h.env.dom.cards, original);
    assert.equal(original.children.length, 0);
    h.node("city-metro-close").events.click();
  }
});

test("city switch immediately hides the old entry and closes both dialogs", async () => {
  const next = deferred();
  const h = harness(url => url.includes("guangzhou") ? next.promise : { data: response() });
  await h.open(); h.byClass("metro-preview").events.click();
  h.select("guangzhou");
  assert.equal(h.node("city-metro-open").hidden, true);
  assert.equal(h.node("city-metro-dialog").open, false);
  assert.equal(h.node("metro-dialog").open, false);
  next.resolve({ data: response("guangzhou", { supported: false, available: false, map: null }) });
  await flush();
  assert.equal(h.node("city-metro-open").hidden, true);
});

test("locked projects do not request or expose city maps", async () => {
  const h = harness(); h.state.projectUnlocked = false;
  await h.open();
  assert.equal(h.node("city-metro-open").hidden, true);
  assert.equal(h.node("city-metro-dialog").open, false);
  assert.equal(h.requests.length, 0);
});
