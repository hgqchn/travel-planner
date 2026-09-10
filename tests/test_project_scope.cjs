const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function harness(search = "") {
  const elements = new Map();
  const storage = new Map();
  const env = {
    window: {}, URL, URLSearchParams, location: { search, origin: "http://127.0.0.1:8000" },
    document: {
      getElementById(id) { if (!elements.has(id)) elements.set(id, { hidden: true, textContent: "" }); return elements.get(id); },
      querySelectorAll() { return []; }, addEventListener() {},
    },
    localStorage: { getItem: (key) => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) },
    state: { cityId: "shanghai", tab: "attraction", cacheInfo: null, revision: 1, snapshotRequestId: 1 },
    render() {}, fetchSnapshot: async () => {},
  };
  const context = vm.createContext(env);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../public/project-scope.js"), "utf8"), context);
  env.state.projectId = env.window.TripProject.id;
  env.window.TripBatch = { canNavigate: () => true };
  const loadUI = () => vm.runInContext(fs.readFileSync(path.join(__dirname, "../public/ui-enhancements.js"), "utf8"), context);
  return { env, scope: env.window.TripProject, storage, elements, loadUI };
}

test("main project keeps existing local preferences and routes", () => {
  const h = harness();
  assert.equal(h.scope.id, "main");
  assert.equal(h.scope.headers()["X-Trip-Project"], "main");
  assert.equal(h.scope.storageKey("trip-ai-current-task"), "trip-ai-current-task");
  assert.equal(h.scope.url("/admin.html"), "/admin.html");
});

test("child projects scope API headers, links, and local state independently", () => {
  const first = harness("?project=0123456789abcdef");
  const second = harness("?project=fedcba9876543210");
  assert.equal(first.scope.headers({ "X-Trip-Project": "main", "Content-Type": "application/json" })["X-Trip-Project"], "0123456789abcdef");
  assert.equal(first.scope.url("/admin.html"), "/admin.html?project=0123456789abcdef");
  assert.equal(first.scope.url("/#food"), "/?project=0123456789abcdef#food");
  assert.notEqual(first.scope.storageKey("trip-ai-current-task"), second.scope.storageKey("trip-ai-current-task"));
  first.storage.set("trip-recent-cities", '["beijing"]');
  first.storage.set(first.scope.storageKey("trip-recent-cities"), '["chengdu"]');
  first.loadUI();
  assert.equal(first.env.window.TripUI.initialCity(), "chengdu");
});

test("invalid or empty project IDs fail clearly instead of selecting main", () => {
  for (const search of ["?project=", "?project=wrong", "?project=..%2Fmain", "?project=0123456789ABCDEF"]) {
    const h = harness(search);
    assert.equal(h.scope.valid, false);
    assert.throws(() => h.scope.headers(), /项目链接无效/);
  }
});

test("cache capacity notice shows the current category count and hides for zero or other tabs", () => {
  const h = harness();
  h.loadUI();
  h.env.state.cacheInfo = { omitted: { attraction: 12, food: 0 } };
  h.env.window.TripUI.renderCacheNotice();
  assert.equal(h.elements.get("cache-notice").hidden, false);
  assert.match(h.elements.get("cache-notice").textContent, /另有 12 条仍保存在全局缓存中/);
  h.env.state.tab = "food";
  h.env.window.TripUI.renderCacheNotice();
  assert.equal(h.elements.get("cache-notice").hidden, true);
  h.env.state.tab = "itinerary";
  h.env.window.TripUI.renderCacheNotice();
  assert.equal(h.elements.get("cache-notice").hidden, true);
});

test("changing cities clears the previous city's cache notice state", async () => {
  const h = harness();
  h.loadUI();
  h.env.state.cacheInfo = { omitted: { attraction: 12 } };
  await h.env.window.TripUI.switchCity("beijing");
  assert.equal(h.env.state.cacheInfo, null);
  h.env.window.TripUI.renderCacheNotice();
  assert.equal(h.elements.get("cache-notice").hidden, true);
});
