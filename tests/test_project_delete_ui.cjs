const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const main = { id: "main", name: "原旅行项目" };
const another = { id: "1234567890abcdef", name: "成都周末" };

async function harness({ currentId = "main", initialProjects = [main, another], deleteHandler } = {}) {
  const nodes = new Map(), allNodes = [], requests = [], navigation = [], documentEvents = {};
  class Node {
    constructor(tag = "div") {
      this.tagName = tag.toUpperCase();
      this.children = []; this.dataset = {}; this.events = {};
      this.disabled = false; this.hidden = false; this.open = false; this.value = ""; this.textContent = "";
      this.elements = { name: { value: "" }, project_code: { value: "" }, password: { value: "" } };
      allNodes.push(this);
    }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    setAttribute(name, value) { this[name] = value; }
    addEventListener(name, handler) { this.events[name] = handler; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    focus() {}
    reset() {}
  }
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, new Node(["project-delete-confirm", "project-delete-cancel", "projects-refresh", "admin-logout"].includes(id) ? "button" : "div"));
    return nodes.get(id);
  }
  const state = { project: initialProjects.find((project) => project.id === currentId), cities: [], users: [] };
  let activeProjects = [...initialProjects];
  const env = {
    console, URL, encodeURIComponent,
    document: {
      querySelector: (selector) => node(selector.slice(1)),
      querySelectorAll: (selector) => selector === "button" ? allNodes.filter((item) => item.tagName === "BUTTON") : [],
      createElement: (tag) => new Node(tag),
      addEventListener(name, handler) { documentEvents[name] = handler; },
    },
    window: {
      location: { assign: (url) => navigation.push(url) },
      TripProject: {
        id: currentId, headers: (extra) => extra,
        url: (pathname, id) => id === "main" ? pathname : `${pathname}?project=${id}`,
        initLinks() {},
      },
    },
    fetch: async (url, options) => {
      const body = options.body ? JSON.parse(options.body) : undefined;
      requests.push({ url, method: options.method, body });
      let data, status = 200;
      if (url === "/api/admin/session") data = { authenticated: true, enabled: true };
      else if (url === "/api/admin/api-settings") data = { configured: {} };
      else if (url === "/api/admin/projects") data = { projects: activeProjects, current_project_id: currentId };
      else if (url === "/api/admin/state") {
        assert.ok(state.project, "must not request state for a missing project");
        data = state;
      } else if (options.method === "DELETE") {
        const id = url.split("/").at(-1);
        const result = deleteHandler ? await deleteHandler(id, body) : null;
        status = result?.status || 200;
        activeProjects = result?.projects || activeProjects.filter((project) => project.id !== id);
        data = result?.data || { deleted_id: id, projects: activeProjects, next_project_id: activeProjects[0]?.id || null };
      } else throw new Error(`Unexpected request ${url}`);
      return { ok: status >= 200 && status < 300, status, json: async () => data };
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../public/admin.js"), "utf8"), env);
  await new Promise(setImmediate);
  const submit = () => node("project-delete-form").onsubmit({ preventDefault() {} });
  function open(id) {
    const rows = node("admin-projects").children;
    const row = rows.find((entry) => entry.children[0]?.children[0]?.textContent === initialProjects.find((project) => project.id === id)?.name);
    row.children[1].children.find((item) => item.tagName === "BUTTON").onclick();
  }
  function type(value) { node("project-delete-name").value = value; node("project-delete-name").events.input(); }
  const deletes = () => requests.filter((request) => request.method === "DELETE");
  return { node, open, type, submit, deletes, requests, navigation, documentEvents };
}

test("canceling a project deletion sends no deletion request", async () => {
  const h = await harness();
  h.open(another.id);
  assert.equal(h.node("project-delete-dialog").open, true);
  assert.equal(h.node("project-delete-target").textContent, another.name);
  h.node("project-delete-cancel").onclick();
  assert.equal(h.node("project-delete-dialog").open, false);
  assert.equal(h.deletes().length, 0);
});

test("exact name confirmation is required and survives unrelated busy-state resets", async () => {
  const h = await harness();
  h.open(another.id);
  assert.equal(h.node("project-delete-confirm").disabled, true);
  h.type(`${another.name} `);
  assert.equal(h.node("project-delete-confirm").disabled, true);
  await h.submit();
  assert.equal(h.deletes().length, 0);
  h.type(another.name);
  assert.equal(h.node("project-delete-confirm").disabled, false);
  await h.submit();
  assert.deepEqual(h.deletes()[0], { url: `/api/admin/projects/${another.id}`, method: "DELETE", body: { confirm_name: another.name } });
  assert.equal(h.node("project-delete-confirm").disabled, true);
  assert.deepEqual(h.navigation, []);
});

test("deleting the currently managed project navigates to the next active project", async () => {
  const h = await harness();
  h.open(main.id); h.type(main.name);
  await h.submit();
  assert.deepEqual(h.navigation, [`/admin.html?project=${another.id}`]);
});

test("deleting the final project returns to a usable project hub", async () => {
  const h = await harness({ initialProjects: [main] });
  h.open(main.id); h.type(main.name);
  await h.submit();
  assert.deepEqual(h.navigation, ["/admin.html"]);
  assert.equal(h.node("admin-content").hidden, false);
  assert.equal(h.node("admin-project-details").hidden, true);
  assert.equal(h.node("admin-return-project").hidden, true);
  assert.match(h.node("admin-projects").children[0].textContent, /还没有项目/);
});

test("a missing or deleted current project opens the hub without requesting its state", async () => {
  for (const initialProjects of [[], [another]]) {
    const h = await harness({ initialProjects });
    assert.equal(h.requests.some((request) => request.url === "/api/admin/state"), false);
    assert.equal(h.node("admin-content").hidden, false);
    assert.equal(h.node("admin-project-details").hidden, true);
  }
});

test("pending deletion blocks duplicate requests, modal cancellation, and project navigation", async () => {
  let complete;
  const h = await harness({ deleteHandler: () => new Promise((resolve) => { complete = resolve; }) });
  h.open(another.id); h.type(another.name);
  const pending = h.submit();
  assert.equal(h.node("project-delete-confirm").disabled, true);
  assert.equal(h.node("project-delete-name").disabled, true);
  await h.submit();
  let prevented = false;
  h.node("project-delete-dialog").events.cancel({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(h.node("project-delete-dialog").open, true);
  prevented = false;
  h.documentEvents.click({ target: { closest: () => true }, preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(h.deletes().length, 1);
  complete(null);
  await pending;
  assert.equal(h.node("project-delete-dialog").open, false);
});

test("renamed project conflict keeps confirmation disabled and asks for a fresh list", async () => {
  const h = await harness({ deleteHandler: async () => ({ status: 409, data: { error: "name mismatch" } }) });
  h.open(another.id); h.type(another.name);
  await h.submit();
  assert.match(h.node("project-delete-error").textContent, /刷新项目列表/);
  assert.equal(h.node("project-delete-dialog").open, true);
  assert.equal(h.node("project-delete-confirm").disabled, true);
  h.type(another.name);
  await h.submit();
  assert.equal(h.deletes().length, 1);
  assert.equal(h.node("project-delete-cancel").disabled, false);
});

test("a running AI job conflict preserves the server explanation and requires a fresh list", async () => {
  const error = "正在生成 AI 内容，请等待生成结束后再删除。";
  const h = await harness({ deleteHandler: async () => ({ status: 409, data: { error } }) });
  h.open(another.id); h.type(another.name);
  await h.submit();
  assert.ok(h.node("project-delete-error").textContent.includes(error));
  assert.match(h.node("project-delete-error").textContent, /关闭此窗口，刷新项目列表后重试/);
  assert.equal(h.node("project-delete-confirm").disabled, true);
  assert.equal(h.node("project-delete-cancel").disabled, false);
});

test("deleting a just-created project removes its stale share links", async () => {
  const h = await harness();
  h.node("project-created").dataset.projectId = another.id;
  h.node("project-created").append("old share links");
  h.open(another.id); h.type(another.name);
  await h.submit();
  assert.equal(h.node("project-created").hidden, true);
  assert.equal(h.node("project-created").children.length, 0);
});
