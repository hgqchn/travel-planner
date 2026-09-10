const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function harness() {
  const nodes = new Map();
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, {
      hidden: false, disabled: false, open: false, textContent: "", events: {},
      addEventListener(name, handler) { this.events[name] = handler; },
      showModal() { this.open = true; }, close() { this.open = false; },
      focus() {}, querySelector() { return null; },
    });
    return nodes.get(id);
  }
  const state = {
    projectId: "first-project", projectName: "预览项目", projectUnlocked: true,
    cityId: "shanghai", tab: "attraction", userId: "Alice",
    cities: [{ id: "shanghai", name: "上海" }],
    items: { attraction: [{ id: "a", version: 1, city_id: "shanghai" }, { id: "b", version: 3, city_id: "shanghai" }] },
  };
  const requests = [], toasts = [];
  const env = { window: {}, document: { getElementById: node }, state, requireIdentity: () => true,
    showToast: (message) => toasts.push(message), fetchSnapshot: async () => {},
    requestJson: async (url, options) => { requests.push({ url, body: JSON.parse(options.body) }); return { data: { deleted_count: 2 } }; },
  };
  env.render = () => env.window.TripBatch.renderToolbar();
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../public/batch-selection.js"), "utf8"), env);
  const controller = env.window.TripBatch;
  controller.init();
  env.render();
  const click = (id) => node(id).events.click();
  return { env, state, node, click, controller, requests, toasts };
}

test("snapshot changes preserve the versions originally selected for deletion", async () => {
  const h = harness();
  h.click("batch-open");
  h.click("batch-select-all");
  h.state.items.attraction[0].version = 2;
  h.env.render();
  h.click("batch-delete");
  await h.click("batch-confirm-delete");
  assert.deepEqual(h.requests[0].body, { city_id: "shanghai", items: [{ id: "a", version: 1 }, { id: "b", version: 3 }] });
});

test("sync drops removed selections and only targets the current city and category", async () => {
  const h = harness();
  h.state.items.attraction.push({ id: "elsewhere", version: 1, city_id: "beijing" });
  h.click("batch-open");
  h.click("batch-select-all");
  h.state.items.attraction = h.state.items.attraction.filter((item) => item.id !== "a");
  h.env.render();
  h.click("batch-delete");
  await h.click("batch-confirm-delete");
  assert.equal(h.requests[0].url, "/api/items/attraction/batch-delete");
  assert.deepEqual(h.requests[0].body.items, [{ id: "b", version: 3 }]);
});

test("version conflicts require refresh and deliberate re-selection", async () => {
  const h = harness();
  h.env.requestJson = async () => { const error = new Error("冲突"); error.status = 409; throw error; };
  h.click("batch-open");
  h.click("batch-select-all");
  h.click("batch-delete");
  await h.click("batch-confirm-delete");
  assert.match(h.node("batch-confirm-error").textContent, /没有删除任何内容/);
  assert.equal(h.node("batch-confirm-delete").disabled, true);
  assert.equal(h.node("batch-confirm-refresh").hidden, false);
  await h.click("batch-confirm-refresh");
  assert.equal(h.node("batch-confirm-dialog").open, false);
  assert.equal(h.node("batch-delete").disabled, true);
});

test("deletion blocks navigation and ignores completion after a forced project reset", async () => {
  const h = harness();
  let complete;
  let snapshots = 0;
  h.env.fetchSnapshot = async () => { snapshots += 1; };
  h.env.requestJson = () => new Promise((resolve) => { complete = resolve; });
  h.click("batch-open");
  h.click("batch-select-all");
  h.click("batch-delete");
  const pending = h.click("batch-confirm-delete");
  assert.equal(h.controller.canNavigate(), false);
  h.controller.reset();
  h.state.projectId = "another-project";
  h.env.render();
  complete({ data: { deleted_count: 2 } });
  await pending;
  assert.equal(snapshots, 0);
  assert.equal(h.node("batch-toolbar").hidden, true);
  assert.equal(h.controller.canNavigate(), true);
});

test("changing city, category, identity, or project discards old selections", () => {
  for (const [key, value] of [["cityId", "beijing"], ["tab", "food"], ["userId", "Bob"], ["projectId", "another-project"]]) {
    const h = harness();
    h.click("batch-open");
    h.click("batch-select-all");
    h.state[key] = value;
    h.env.render();
    assert.equal(h.node("batch-toolbar").hidden, true, key);
    assert.equal(h.node("batch-delete").disabled, true, key);
  }
});
