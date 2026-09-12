const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const jobId = "a".repeat(32);
const firstDate = "2026-10-01";
const secondDate = "2026-10-02";
const fields = [
  { key: "date", required: true, type: "date" },
  { key: "title", required: true },
  { key: "start_time" },
  { key: "location" },
  { key: "opening_start", type: "time" },
  { key: "opening_end", type: "time" },
  { key: "attraction_names", label: "关联景点", attractionNames: true, multiline: true },
];

function harness({ storedJob = null, postHandler, importHandler, confirm = true, items } = {}) {
  const nodes = new Map(), allNodes = [], requests = [], confirmations = [];
  class Node {
    constructor(tag = "div") {
      this.tagName = tag.toLowerCase(); this.children = []; this.events = {}; this.dataset = {};
      this.value = ""; this.textContent = ""; this.disabled = false; this.hidden = false; this.checked = false; this.open = false;
      this.classList = { add() {}, remove() {}, toggle() {} };
      this.style = { setProperty() {} }; allNodes.push(this);
    }
    append(...children) { this.children.push(...children); }
    get options() { return this.children; }
    replaceChildren(...children) { this.children = children; }
    addEventListener(name, callback) { this.events[name] = callback; }
    dispatchEvent(event) { return this.events[event.type]?.(event); }
    setAttribute(name, value) { this[name] = value; }
    showModal() { this.open = true; } close() { this.open = false; } focus() {}
    querySelectorAll(selector) {
      const result = [];
      const matches = (item, part) => {
        part = part.trim();
        const tag = part.match(/^[a-z]+/);
        if (tag && item.tagName !== tag[0]) return false;
        const attributes = [...part.matchAll(/\[([\w-]+)(\$?=)["']([^"']*)["']\]/g)];
        if (attributes.some(([, name, operator, value]) => operator === "$=" ? !String(item[name] || "").endsWith(value) : String(item[name] || "") !== value)) return false;
        return Boolean(tag || attributes.length);
      };
      const walk = (parent) => {
        for (const child of parent.children) {
          if (!child || typeof child !== "object") continue;
          if (selector.split(",").some((part) => matches(child, part))) result.push(child);
          walk(child);
        }
      };
      walk(this); return result;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    setCustomValidity(message) { this.validationMessage = message; }
    checkValidity() { return !this.validationMessage && (!this.required || Boolean(this.value)) && (!this.min || this.value >= this.min) && (!this.max || this.value <= this.max); }
    reportValidity() { return this.checkValidity(); }
    reset() { for (const input of this.querySelectorAll("input,textarea,select")) { if (input.type === "checkbox") input.checked = true; else input.value = input.initialValue || ""; } }
  }
  function node(id) { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); }
  const form = node("ai-form");
  const formInputs = new Map();
  for (const [name, value, id] of [
    ["planning_mode", "append", "ai-planning-mode"], ["target_date", "", "ai-target-date"], ["start_date", firstDate, "ai-start-date"],
    ["days", "3"], ["people", "1"], ["budget", ""], ["pace", "balanced"], ["preferences", ""], ["requirements", ""],
  ]) {
    const input = id ? node(id) : new Node("input");
    input.tagName = ["planning_mode", "pace"].includes(name) ? "select" : "input";
    input.name = name; input.value = value; input.initialValue = value;
    formInputs.set(name, input); form.append(input);
  }
  const kinds = ["attraction", "food", "itinerary"].map((value) => {
    const input = new Node("input"); input.name = "kinds"; input.value = value; input.type = "checkbox"; input.checked = true; form.append(input); return input;
  });
  const templates = ["classic", "family", "food", "extra"].map((value) => { const button = new Node("button"); button.dataset.aiTemplate = value; form.append(button); return button; });
  node("ai-generate").tagName = "button"; form.append(node("ai-generate"));
  form.elements = { namedItem: (name) => formInputs.get(name) || null };
  const model = node("ai-model-input"); model.tagName = "input"; model.name = "model";
  formInputs.set("model", model); form.append(model);
  const importForm = node("ai-import-form");
  node("ai-import").tagName = "button"; importForm.append(node("ai-preview-items"), node("ai-import"));
  const storage = new Map();
  if (storedJob) storage.set("trip-ai-current-task", JSON.stringify({ jobId: storedJob.id, userId: "Alice", projectId: "main" }));
  const state = { projectId: "main", projectUnlocked: true, userId: "Alice", cityId: "shanghai", cities: [{ id: "shanghai", name: "上海" }, { id: "beijing", name: "北京" }],
    items: { itinerary: items || [{ date: firstDate, city_id: "shanghai" }, { date: secondDate, city_id: "shanghai" }, { date: "2026-12-01", city_id: "beijing" }] } };
  let uuid = 0;
  const env = {
    console, Date, Map, Set, JSON, Math, Number, String, Array, Boolean, RegExp, URL, TextEncoder, encodeURIComponent,
    setTimeout: () => 1, clearTimeout() {}, ResizeObserver: class { observe() {} },
    Event: class { constructor(type) { this.type = type; } },
    crypto: { randomUUID: () => `request-${++uuid}` },
    localStorage: { getItem: (key) => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
    FormData: class {
      constructor(owner) { this.inputs = owner.querySelectorAll("input,textarea,select").filter((input) => !input.disabled && (input.type !== "checkbox" || input.checked)); }
      get(name) { return this.inputs.find((input) => input.name === name)?.value ?? null; }
      getAll(name) { return this.inputs.filter((input) => input.name === name).map((input) => input.value); }
    },
    document: { getElementById: node, createElement: (tag) => new Node(tag), createTextNode: (text) => text,
      querySelectorAll: (selector) => selector === "[data-ai-template]" ? templates : selector === '[data-ai-template="food"]' ? templates.filter((button) => button.dataset.aiTemplate === "food") : [],
    },
    state, CATEGORY: { itinerary: { title: "行程" }, attraction: { title: "景点" }, food: { title: "美食" } }, FIELDS: { itinerary: fields, attraction: [], food: [] },
    element(tag, className = "", text = "") { const item = new Node(tag); item.className = className; item.textContent = text; return item; },
    makeField(definition, value) { const label = new Node("label"), input = new Node("input"); input.value = value || ""; input.type = definition.type; label.append(input); return label; },
    itemName: (kind, record) => kind === "itinerary" ? record.title : record.name,
    fetchSnapshot: async () => {}, showToast() {}, showProjectGate() {}, openIdentity() {},
    requestJson: async (url, options = {}) => {
      const body = options.body ? JSON.parse(options.body) : undefined;
      requests.push({ url, method: options.method || "GET", body });
      if (url === "/api/ai/config") return { data: { enabled: true, model: "test-offline" } };
      if (url === `/api/ai/jobs/${jobId}`) return { data: { job: storedJob } };
      if (url.endsWith("/import")) return importHandler ? importHandler(body) : { data: { created: body.items.length, deleted: 2 } };
      if (url === "/api/ai/jobs") {
        if (postHandler) return postHandler(body);
        const dates = Array.from({ length: body.days }, (_, index) => new Date(new Date(`${body.start_date}T00:00:00Z`).getTime() + index * 86400000).toISOString().slice(0, 10));
        return { data: { job: { id: jobId, city_id: body.city_id, city_name: "上海", status: "ready", request: body, replacement: { count: 2 }, result: { itineraries: dates.map((date) => ({ date, title: `计划 ${date}` })) } } } };
      }
      throw new Error(`Unexpected URL: ${url}`);
    },
    window: { TripProject: { storageKey: (key) => key }, TripBatch: { reset() {}, canNavigate: () => true }, confirm(message) { confirmations.push(message); return typeof confirm === "function" ? confirm(message) : confirm; } },
  };
  vm.createContext(env);
  const appSource = fs.readFileSync(path.join(__dirname, "../public/app.js"), "utf8");
  vm.runInContext(appSource.slice(appSource.indexOf("function parseAttractionNames("), appSource.indexOf("function requireIdentity(")), env);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../public/ui-enhancements.js"), "utf8"), env);
  const controller = env.window.TripUI;
  controller.init();
  const click = (id) => node(id).events.click();
  const submit = () => node("ai-form").events.submit({ preventDefault() {} });
  const apply = () => importForm.events.submit({ preventDefault() {} });
  const mode = (value) => { formInputs.get("planning_mode").value = value; return node("ai-planning-mode").events.change(); };
  const cards = () => node("ai-preview-items").children.flatMap((group) => group.children.filter((item) => item.tagName === "article"));
  const imports = () => requests.filter((request) => request.url.endsWith("/import"));
  const generations = () => requests.filter((request) => request.url === "/api/ai/jobs");
  return { env, state, node, controller, click, submit, apply, mode, input: (name) => formInputs.get(name), kinds, cards, imports, generations, requests, confirmations, storage };
}

function readyJob(mode = "replace_day") {
  return { id: jobId, city_id: "shanghai", city_name: "上海", status: "ready", request: { planning_mode: mode, target_date: firstDate, start_date: firstDate, days: 1, kinds: ["itinerary"], people: 1 }, replacement: { count: 4 }, result: { itineraries: [{ date: firstDate, title: "新方案", duplicate: true }] } };
}

test("planning entry presets distinguish a complete draft from collecting places and food", async () => {
  for (const [flow, expected] of [["full", ["attraction", "food", "itinerary"]], ["collect", ["attraction", "food"]]]) {
    const h = harness(); await h.controller.openPlanning(flow);
    await h.submit();
    assert.deepEqual(h.generations()[0].body.kinds, expected);
    assert.equal(h.generations()[0].body.planning_mode, "append");
  }
});

test("changing the planning entry preserves an unconfirmed existing draft", async () => {
  const h = harness({storedJob:readyJob()});
  await h.controller.openPlanning("collect");
  assert.equal(h.cards().length, 1);
  assert.equal(h.generations().length, 0);
  assert.equal(h.imports().length, 0);
  assert.match(h.node("ai-status").textContent, /先确认当前草稿/);
});

test("confirmed itinerary drafts lead to route verification without silently querying maps", async () => {
  const job = readyJob("append"); job.result.itineraries[0].duplicate = false;
  const h = harness({storedJob:job});
  await h.click("ai-open");
  assert.equal(h.node("ai-next-step").hidden, true);
  await h.apply();
  assert.equal(h.node("ai-next-step").hidden, false);
  assert.match(h.node("ai-next-step-text").textContent, /逐天确认地图位置/);
  assert.equal(h.requests.filter(request => request.url.includes("/maps/")).length, 0);
});

test("AI preview edits linked attraction names as an array and reports automatic additions", async () => {
  const job = readyJob('append'); job.result.itineraries[0].duplicate = false;
  job.result.itineraries[0].attraction_names = ['外滩', '上海博物馆'];
  const h = harness({ storedJob: job, importHandler: () => ({ data: { created: 1, skipped: 0, auto_added_attractions: 2 } }) });
  await h.click('ai-open');
  const field = h.cards()[0].querySelector('[name$=":attraction_names"]');
  assert.equal(field.value, '外滩、上海博物馆');
  assert.equal(h.node('ai-attraction-sync-note').hidden, false);
  field.value = '外滩，上海博物馆\n外滩';
  await h.apply();
  assert.deepEqual(h.imports()[0].body.items[0].data.attraction_names, ['外滩', '上海博物馆']);
  assert.match(h.node('ai-status').textContent, /自动补充 2 个(?:行程[景地]点|游玩点)/);
});

test("AI preview preserves opening hours and marks edited hours as user input", async () => {
  for (const edited of [false, true]) {
    const job=readyJob('append');
    Object.assign(job.result.itineraries[0], {duplicate:false, opening_start:'00:00', opening_end:'23:59', opening_source:'ai_estimate'});
    const h=harness({storedJob:job}); await h.click('ai-open');
    if (edited) h.cards()[0].querySelector('[name$=":opening_end"]').value='18:00';
    await h.apply();
    const data=h.imports()[0].body.items[0].data;
    assert.equal(data.opening_start,'00:00');
    assert.equal(data.opening_end,edited ? '18:00' : '23:59');
    assert.equal(data.opening_source,edited ? 'user' : 'ai_estimate');
  }
});

test("old AI previews omit no relationship field and import an empty array", async () => {
  const job = readyJob('append'); job.result.itineraries[0].duplicate = false;
  const h = harness({ storedJob: job }); await h.click('ai-open');
  assert.equal(h.cards()[0].querySelector('[name$=":attraction_names"]').value, '');
  await h.apply(); assert.deepEqual(h.imports()[0].body.items[0].data.attraction_names, []);
});

test("AI preview refuses more than twelve explicit attractions without truncating", async () => {
  const job = readyJob('append'); job.result.itineraries[0].duplicate = false;
  const h = harness({ storedJob: job }); await h.click('ai-open');
  h.cards()[0].querySelector('[name$=":attraction_names"]').value = Array.from({length: 13}, (_, i) => `景点${i}`).join('、');
  await h.apply(); assert.equal(h.imports().length, 0); assert.match(h.node('ai-error').textContent, /12/);
  assert.equal(h.node('ai-import-error-dialog').open, true);
  assert.match(h.node('ai-import-error-message').textContent, /12/);
});

test("day entry locks the date and one day, and sends only itinerary requests", async () => {
  const h = harness();
  await h.controller.openReplan("replace_day", secondDate);
  assert.equal(h.input("target_date").value, secondDate);
  assert.equal(h.input("start_date").value, secondDate);
  assert.equal(h.input("start_date").disabled, true);
  assert.equal(h.input("days").value, "1");
  assert.equal(h.input("days").disabled, true);
  assert.ok(h.kinds.every((input) => input.disabled));
  await h.submit();
  assert.equal(h.generations()[0].body.planning_mode, "replace_day");
  assert.equal(h.generations()[0].body.target_date, secondDate);
  assert.equal(h.generations()[0].body.start_date, secondDate);
  assert.equal(h.generations()[0].body.days, 1);
  assert.deepEqual(h.generations()[0].body.kinds, ["itinerary"]);
  assert.equal(h.input("days").disabled, true, "ready state must retain mode locks");
});

test("all entry infers the current city's date span and warns it replaces outside the new dates", async () => {
  const h = harness();
  await h.click("ai-replan-all");
  assert.equal(h.input("start_date").value, firstDate);
  assert.equal(h.input("days").value, "2");
  assert.match(h.node("ai-replan-scope").textContent, /新日期范围之外/);
  await h.submit();
  assert.equal(h.generations()[0].body.planning_mode, "replace_all");
  assert.equal(h.generations()[0].body.city_id, "shanghai");
  assert.equal(h.generations()[0].body.target_date, undefined);
  assert.match(h.node("ai-import").textContent, /确认替换全部行程/);
});

test("a longer than seven-day trip preserves its full range and can be generated", async () => {
  const h = harness({ items: [{ date: firstDate, city_id: "shanghai" }, { date: "2026-10-12", city_id: "shanghai" }] });
  await h.controller.openReplan("replace_all");
  assert.equal(h.input("start_date").value, firstDate);
  assert.equal(h.input("days").value, "12");
  assert.equal(h.node("ai-replan-range-note").hidden, true);
  await h.submit();
  assert.equal(h.generations().length, 1);
  assert.equal(h.generations()[0].body.days, 12);
});

test("canceling final replacement confirmation sends no import request", async () => {
  const h = harness({ confirm: false });
  await h.controller.openReplan("replace_day", firstDate);
  await h.submit();
  await h.apply();
  assert.equal(h.imports().length, 0);
  assert.match(h.confirmations[0], /上海.*2026-10-01/);
  assert.match(h.confirmations[0], /2 项旧行程.*1 项新行程/s);
  assert.equal(h.node("ai-preview").hidden, false);
});

test("restored requests determine replacement scope, irrespective of changed form values", async () => {
  const h = harness({ storedJob: readyJob() });
  await h.click("ai-open");
  assert.equal(h.input("planning_mode").value, "replace_day");
  assert.equal(h.input("days").disabled, true);
  assert.equal(h.node("ai-import").disabled, false, "matching old items remain selectable for replacement");
  h.input("planning_mode").value = "append";
  h.input("target_date").value = secondDate;
  await h.apply();
  assert.equal(h.imports().length, 1);
  assert.equal(h.imports()[0].body.confirm_replace, true);
  assert.equal(h.imports()[0].body.city_id, "shanghai");
  assert.equal(h.imports()[0].body.items[0].data.date, firstDate);
  assert.match(h.confirmations[0], /2026-10-01/);
  assert.match(h.confirmations[0], /4 项旧行程/);
});

test("all replacement needs every requested date selected, including after editing a date", async () => {
  const h = harness();
  await h.controller.openReplan("replace_all");
  await h.submit();
  const secondCard = h.cards()[1];
  const checkbox = secondCard.querySelector('[type="checkbox"]');
  checkbox.checked = false; checkbox.dispatchEvent({ type: "change" });
  assert.equal(h.node("ai-import").disabled, true);
  assert.match(h.node("ai-selection-count").textContent, /2026-10-02.*至少选择/);
  await h.apply(); assert.equal(h.imports().length, 0);
  checkbox.checked = true; checkbox.dispatchEvent({ type: "change" });
  const date = secondCard.querySelector('[name$=":date"]');
  date.value = firstDate;
  const editFields = secondCard.children[2].children[1]; editFields.events.input();
  assert.equal(h.node("ai-import").disabled, true);
  date.value = secondDate; editFields.events.input();
  assert.equal(h.node("ai-import").disabled, false);
  await h.apply(); assert.equal(h.imports().length, 1);
});

test("a restored queued task is kept when a direct replan entry is clicked", async () => {
  const job = readyJob(); job.status = "queued"; delete job.result;
  const h = harness({ storedJob: job });
  await h.controller.openReplan("replace_all");
  assert.equal(h.input("planning_mode").value, "replace_day");
  assert.equal(h.input("target_date").value, firstDate);
  assert.equal(h.input("planning_mode").disabled, true);
  await h.submit(); assert.equal(h.generations().length, 0);
  assert.ok(h.storage.has("trip-ai-current-task"));
});

test("uncertain submissions retain their request ID and payload across direct-entry retries", async () => {
  let attempts = 0;
  const h = harness({ postHandler: async (body) => {
    attempts += 1;
    if (attempts === 1) throw new Error("Network interrupted");
    return { data: { job: { ...readyJob(), request: body, status: "queued" } } };
  } });
  await h.controller.openReplan("replace_day", firstDate);
  await h.submit();
  assert.equal(h.node("ai-generate").textContent, "重试本次请求");
  await h.controller.openReplan("replace_all");
  assert.equal(h.input("planning_mode").value, "replace_day");
  await h.submit();
  assert.equal(h.generations().length, 2);
  assert.deepEqual(h.generations()[0].body, h.generations()[1].body);
});

test("canceling a mode switch keeps a ready draft and its import scope", async () => {
  const h = harness({ storedJob: readyJob(), confirm: false });
  await h.click("ai-open");
  h.mode("replace_all");
  assert.equal(h.input("planning_mode").value, "replace_day");
  assert.equal(h.node("ai-preview").hidden, false);
  assert.match(h.node("ai-import").textContent, /2026-10-01/);
});

test("empty itineraries still allow choosing a day from the AI form", async () => {
  const h = harness({ items: [] });
  await h.click("ai-open");
  h.mode("replace_day");
  h.input("target_date").value = secondDate;
  h.node("ai-target-date").events.change();
  await h.submit();
  assert.equal(h.generations()[0].body.target_date, secondDate);
  assert.equal(h.generations()[0].body.days, 1);
});

test("confirmed mode changes discard a ready draft, but applied jobs need no extra discard prompt", async () => {
  const h = harness({ storedJob: readyJob() });
  await h.click("ai-open");
  h.mode("replace_all");
  assert.equal(h.confirmations.length, 1);
  assert.equal(h.node("ai-preview").hidden, true);
  assert.equal(h.storage.has("trip-ai-current-task"), false);
  await h.submit(); await h.apply();
  const previousCount = h.confirmations.length;
  await h.controller.openReplan("replace_day", secondDate);
  assert.equal(h.confirmations.length, previousCount);
  assert.equal(h.input("target_date").value, secondDate);
  assert.equal(h.node("ai-preview").hidden, true);
});

test("a replacement conflict keeps editable preview but requires regeneration", async () => {
  const h = harness({ importHandler: async () => { const error = new Error("conflict"); error.status = 409; throw error; } });
  await h.controller.openReplan("replace_day", firstDate); await h.submit(); await h.apply();
  assert.equal(h.node("ai-preview").hidden, false);
  assert.match(h.node("ai-error").textContent, /未替换任何内容.*预览已保留/);
  assert.equal(h.node("ai-import-error-dialog").open, true);
  assert.equal(h.node("ai-import-error-message").textContent, h.node("ai-error").textContent);
  assert.equal(h.node("ai-import").disabled, true);
  await h.apply(); assert.equal(h.imports().length, 1);
  assert.equal(h.node("ai-generate").disabled, false);
});

test("busy import rejects duplicate application and ignores UI work after identity reset", async () => {
  let complete;
  const h = harness({ importHandler: () => new Promise((resolve) => { complete = resolve; }) });
  await h.controller.openReplan("replace_day", firstDate); await h.submit();
  const pending = h.apply();
  await h.apply(); assert.equal(h.imports().length, 1);
  h.controller.reset(); h.state.userId = "Bob";
  complete({ data: { created: 1, deleted: 2 } }); await pending;
  assert.equal(h.node("ai-preview").hidden, true);
  assert.equal(h.node("ai-status").textContent, "");
});

test("network import failure opens a dialog, preserves edits and selection, and allows retry", async () => {
  let attempts = 0;
  const job = readyJob("append"); job.result.itineraries[0].duplicate = false;
  const h = harness({ storedJob: job, importHandler: () => {
    if (++attempts === 1) throw new TypeError("Failed to fetch");
    return { data: { created: 1 } };
  } });
  await h.click("ai-open");
  const title = h.cards()[0].querySelector('[name$=":title"]');
  title.value = "我修改过的安排";
  await h.apply();
  assert.equal(h.node("ai-import-error-dialog").open, true);
  assert.match(h.node("ai-import-error-message").textContent, /网络连接异常.*无法确认导入结果/);
  assert.equal(h.node("ai-preview").hidden, false);
  assert.equal(title.value, "我修改过的安排");
  assert.equal(title.disabled, false);
  assert.equal(h.cards()[0].querySelector('[type="checkbox"]').checked, true);
  assert.equal(h.node("ai-import").disabled, false);
  h.node("ai-import-error-dialog").close();
  await h.apply();
  assert.equal(h.imports().length, 2);
  assert.deepEqual(h.imports()[1].body, h.imports()[0].body);
  assert.equal(h.node("ai-import-error-dialog").open, false);
  assert.equal(h.node("ai-import").textContent, "已导入");
});

test("server import errors appear as text and resetting identity dismisses the dialog", async () => {
  const reason = '条目数量已达上限。<img src=x onerror=alert(1)>';
  const h = harness({ importHandler: () => { const error = new Error(reason); error.status = 400; throw error; } });
  await h.controller.openReplan("replace_day", firstDate); await h.submit(); await h.apply();
  assert.equal(h.node("ai-import-error-dialog").open, true);
  assert.equal(h.node("ai-import-error-message").textContent, reason);
  assert.equal(h.node("ai-import-error-message").children.length, 0);
  assert.equal(h.node("ai-import").disabled, false);
  h.controller.reset();
  assert.equal(h.node("ai-import-error-dialog").open, false);
});

test("invalid preview fields open a failure dialog without sending an import request", async () => {
  const job = readyJob("append"); job.result.itineraries[0].duplicate = false;
  const h = harness({ storedJob: job }); await h.click("ai-open");
  const title = h.cards()[0].querySelector('[name$=":title"]');
  title.value = ""; title.validationMessage = "请填写此字段。";
  await h.apply();
  assert.equal(h.imports().length, 0);
  assert.equal(h.node("ai-import-error-dialog").open, true);
  assert.match(h.node("ai-import-error-message").textContent, /请填写此字段/);
  assert.equal(h.cards()[0].querySelector("details").open, true);
});

test("expired access still opens the identity or project gate and explains the import failure", async () => {
  for (const projectLocked of [false, true]) {
    let gateOpened = false;
    const h = harness({ importHandler: () => {
      const error = new Error("access expired"); error.status = projectLocked ? 403 : 401;
      error.data = projectLocked ? { code: "PROJECT_LOCKED" } : {};
      throw error;
    } });
    h.env.openIdentity = () => { gateOpened = true; };
    h.env.showProjectGate = () => { h.controller.reset(); gateOpened = true; };
    await h.controller.openReplan("replace_day", firstDate); await h.submit(); await h.apply();
    assert.equal(gateOpened, true);
    assert.equal(h.node("ai-import-error-dialog").open, true);
    assert.match(h.node("ai-import-error-message").textContent, projectLocked ? /重新输入口令/ : /重新选择用户 ID/);
  }
});

test("an import failure arriving after an identity reset does not reopen a stale dialog", async () => {
  let reject;
  const h = harness({ importHandler: () => new Promise((resolve, fail) => { reject = fail; }) });
  await h.controller.openReplan("replace_day", firstDate); await h.submit();
  const pending = h.apply();
  h.controller.reset(); h.state.userId = "Bob";
  reject(new TypeError("Failed to fetch")); await pending;
  assert.equal(h.node("ai-import-error-dialog").open, false);
  assert.equal(h.node("ai-error").textContent, "");
});

test("itinerary day buttons preserve each group's own date and omit undated groups", async () => {
  const h = harness(); const calls = [];
  h.env.window.TripUI.openReplan = (mode, date) => calls.push({ mode, date });
  h.env.dom = { cards: h.node("cards") };
  h.env.formatItineraryDate = (date) => date;
  h.env.itineraryCard = () => h.env.element("article");
  const source = fs.readFileSync(path.join(__dirname, "../public/app.js"), "utf8");
  vm.runInContext(source.slice(source.indexOf("function renderItinerary("), source.indexOf("function emptyState(")), h.env);
  h.env.renderItinerary([{ date: firstDate }, { date: secondDate }, { date: "" }]);
  const buttons = h.node("cards").querySelectorAll("button").filter(button => button.className.includes("ai-replan-day"));
  assert.equal(buttons.length, 2);
  buttons.forEach((button) => button.events.click());
  assert.deepEqual(calls, [{ mode: "replace_day", date: firstDate }, { mode: "replace_day", date: secondDate }]);
  const maps = [];
  h.env.window.TripMaps = { open: date => maps.push(date) };
  h.node("cards").querySelectorAll("button").filter(button => button.textContent === "地图与路线").forEach(button => button.events.click());
  assert.deepEqual(maps, [firstDate, secondDate]);
});

test("manual model defaults to configured value and is sent without a model list request", async () => {
  const h = harness();
  await h.controller.openReplan("replace_day", firstDate);
  assert.equal(h.input("model").value, "test-offline");
  h.input("model").value = "deepseek-v4-pro";
  assert.equal(h.input("model").value, "deepseek-v4-pro");
  assert.ok(!h.requests.some((request) => request.url.startsWith("/api/ai/models")));
  await h.submit();
  assert.equal(h.generations()[0].body.model, "deepseek-v4-pro");
});

test("restored running task keeps its own model and locks model selection", async () => {
  const job = readyJob();
  job.status = "running"; job.model = "deepseek-v4-pro"; job.request.model = job.model;
  const h = harness({ storedJob: job });
  await h.controller.openReplan("replace_day", firstDate);
  assert.equal(h.input("model").value, job.model);
  assert.equal(h.input("model").disabled, true);
  assert.match(h.node("ai-model").textContent, /deepseek-v4-pro/);
});

test('all itinerary previews omit guidance, including historical results', async () => {
  for (const mode of ['replace_day', 'replace_all', 'append']) {
    const job = readyJob(mode);
    job.result.summary = '本次为你规划了行程'; job.result.notices = ['外滩与陆家嘴隔江相望，按片区组织动线。'];
    const h = harness({ storedJob: job }); await h.click('ai-open');
    assert.equal(h.node('ai-travel-guidance').hidden, true);
    assert.equal(h.node('ai-notices').children.length, 0);
    assert.equal(h.node('ai-summary').textContent, '');
  }
  const job = readyJob('append'); job.request.kinds = ['food'];
  job.result = { summary: '仅生成美食', notices: ['不应作为整个城市行程的建议'], foods: [], itineraries: [] };
  const h = harness({ storedJob: job }); await h.click('ai-open');
  assert.equal(h.node('ai-travel-guidance').hidden, true);
});


test("a running task can be left in history while another task is submitted", async () => {
  let count = 0;
  const h = harness({ postHandler: async (request) => ({ data: { job: {
    id: (++count).toString(16).padStart(32, "0"), city_id: "shanghai", city_name: "上海",
    status: "queued", request,
  } } }) });
  await h.controller.openReplan("replace_all");
  await h.submit();
  assert.equal(h.node("ai-new-task").disabled, false);
  await h.click("ai-new-task");
  assert.equal(h.node("ai-generate").disabled, false);
  await h.submit();
  assert.equal(h.generations().length, 2);
  assert.equal(h.node("ai-task-select").children.length, 3);
});

test("a previous task can be reopened after starting a new task", async () => {
  const previous = readyJob();
  const h = harness({ storedJob: previous });
  await h.controller.openReplan("replace_all");
  await h.submit();
  await h.click("ai-new-task");
  h.node("ai-task-select").value = jobId;
  await h.node("ai-task-select").events.change();
  assert.equal(h.node("ai-task-select").value, jobId);
  assert.equal(h.input("planning_mode").value, "replace_day");
  assert.equal(h.cards().length, 1);
});
