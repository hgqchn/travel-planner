const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const appSource = fs.readFileSync(path.join(__dirname, "../public/app.js"), "utf8");
const jobId = "c".repeat(32);
const official = { status: "catalog", name: "故宫博物院", source_name: "文化和旅游部", source_url: "https://www.mct.gov.cn/catalog", as_of: "2025-12-31" };
const reference = { status: "reference", name: "参考名录景区名称", source_type: "wikipedia", source_name: "维基百科", source_url: "https://zh.wikipedia.org/wiki/国家4A级旅游景区", as_of: "2026-09-10" };

function harness({ record, importHandler } = {}) {
  const nodes = new Map(), requests = [];
  class Node {
    constructor(tag = "div") {
      this.tagName = tag; this.children = []; this.events = {}; this.dataset = {}; this.style = { setProperty() {} };
      this.textContent = ""; this.value = ""; this.disabled = false; this.hidden = false; this.checked = false; this.open = false;
      this.className = ""; this.classList = { add() {}, remove() {}, toggle() {} };
    }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; this.textContent = ""; }
    get lastElementChild() { return this.children.at(-1); }
    get options() { return this.children.filter((child) => child.tagName === "option"); }
    addEventListener(type, callback) { this.events[type] = callback; }
    dispatchEvent(event) { return this.events[event.type]?.(event); }
    setAttribute(name, value) { this[name] = value; }
    setCustomValidity(value) { this.validationMessage = value; }
    showModal() { this.open = true; } close() { this.open = false; } focus() {} scrollIntoView() {}
    querySelectorAll(selector) {
      const matches = (item, part) => {
        part = part.trim();
        const tag = part.match(/^[a-z]+/);
        if (tag && item.tagName !== tag[0]) return false;
        const classes = [...part.matchAll(/\.([\w-]+)/g)].map((match) => match[1]);
        if (classes.some((name) => !item.className.split(" ").includes(name))) return false;
        const attributes = [...part.matchAll(/\[([\w-]+)(\$?=)["']([^"']*)["']\]/g)];
        if (attributes.some(([, name, operator, value]) => operator === "$=" ? !String(item[name] || "").endsWith(value) : String(item[name] || "") !== value)) return false;
        return Boolean(tag || classes.length || attributes.length);
      };
      return this.children.filter((child) => child instanceof Node).flatMap((child) => [
        ...(selector.split(",").some((part) => matches(child, part)) ? [child] : []), ...child.querySelectorAll(selector),
      ]);
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    checkValidity() { return !this.required || Boolean(this.value); }
    reportValidity() { return this.checkValidity(); }
    reset() {}
  }
  function node(id) { if (!nodes.has(id)) { const item = new Node(); item.id = id; nodes.set(id, item); } return nodes.get(id); }
  function element(tag, className = "", text = "") { const item = new Node(tag); item.className = className; item.textContent = text; return item; }
  const form = node("ai-form"), formInputs = new Map();
  for (const [name, value] of [["model", "offline-test"], ["planning_mode", "append"], ["target_date", ""], ["start_date", "2026-10-01"], ["days", "3"], ["people", "1"], ["budget", ""], ["pace", "balanced"], ["preferences", ""], ["requirements", ""]]) {
    const input = element(["model", "planning_mode", "pace"].includes(name) ? "select" : "input"); input.name = name; input.value = value;
    formInputs.set(name, input); form.append(input);
    const id = { model: "ai-model-select", planning_mode: "ai-planning-mode", target_date: "ai-target-date", start_date: "ai-start-date" }[name];
    if (id) { input.id = id; nodes.set(id, input); }
  }
  for (const value of ["attraction", "food", "itinerary"]) {
    const input = element("input"); input.type = "checkbox"; input.name = "kinds"; input.value = value; input.checked = true; form.append(input);
  }
  form.elements = { namedItem: (name) => formInputs.get(name) };
  node("ai-import").tagName = "button"; node("ai-result-close").tagName = "button";
  node("ai-import-form").append(node("ai-preview-items"), node("ai-import"), node("ai-result-close"));
  const storage = new Map();
  if (record) storage.set("trip-ai-current-task", JSON.stringify({ jobId, userId: "Alice", projectId: "main" }));
  const state = { projectId: "main", projectUnlocked: true, userId: "Alice", cityId: "beijing", tab: "attraction", cities: [{ id: "beijing", name: "北京" }], items: { itinerary: [] } };
  const dom = {};
  for (const key of ["editFields", "editForm", "editKicker", "editTitle", "saveButton", "deleteButton", "editError", "editConflict", "conflictDetails", "editDialog", "editClose", "editReturn"])
    dom[key] = node(key);
  dom.editForm.append(dom.editFields);
  const env = {
    console, Date, Map, Set, JSON, Math, Number, String, Array, Boolean, RegExp, URL, TextEncoder, encodeURIComponent,
    document: { getElementById: node, createElement: (tag) => element(tag), createTextNode: (text) => text, querySelectorAll: () => [] },
    window: { TripProject: { storageKey: (key) => key }, TripBatch: { reset() {}, canNavigate: () => true }, confirm: () => true, navigator: {} },
    ResizeObserver: class { observe() {} }, requestAnimationFrame: (fn) => fn(), setTimeout: () => 1, clearTimeout() {},
    localStorage: { getItem: (key) => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
    Event: class { constructor(type) { this.type = type; } },
    FormData: class {
      constructor(owner) { this.inputs = owner.querySelectorAll("input,textarea,select").filter((item) => item.name && !item.disabled && (item.type !== "checkbox" || item.checked)); }
      get(name) { return this.inputs.find((input) => input.name === name)?.value ?? null; }
      getAll(name) { return this.inputs.filter((input) => input.name === name).map((input) => input.value); }
      entries() { return this.inputs.map((input) => [input.name, input.value])[Symbol.iterator](); }
    },
    state, dom, element, addTag() {}, addDetail() {}, editButton: () => element("button"), appendFooter: (card) => card.append(element("footer")),
    itemName: (kind, item) => kind === "itinerary" ? item.title : item.name,
    fetchSnapshot: async () => {}, showToast() {}, showProjectGate() {}, openIdentity() {},
    requestJson: async (url, options = {}) => {
      const body = options.body ? JSON.parse(options.body) : undefined;
      requests.push({ url, method: options.method || "GET", body });
      if (url === "/api/ai/config") return { data: { enabled: true, model: "offline-test" } };
      if (url === "/api/ai/models" || url === "/api/ai/models?refresh=1") return { data: { models: ["offline-test"], default_model: "offline-test", warning: "" } };
      if (url === `/api/ai/jobs/${jobId}`) return { data: { job: { id: jobId, city_id: "beijing", city_name: "北京", status: "ready", request: { planning_mode: "append", kinds: ["attraction"], days: 1, people: 1 }, result: { attractions: [record] } } } };
      if (url.endsWith("/import")) return importHandler ? importHandler(body) : { data: { created: 1, skipped: 0 } };
      if (url.startsWith("/api/items/attraction")) return { data: {} };
      throw new Error(`Unexpected request: ${url}`);
    },
  };
  vm.createContext(env);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../public/place-taxonomy.js"), "utf8"), env);
  env.window.TripTaxonomy.configure(JSON.parse(fs.readFileSync(path.join(__dirname, "../place_taxonomy.json"), "utf8")));
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../public/external-links.js"), "utf8"), env);
  vm.runInContext(appSource.slice(0, appSource.indexOf("const state =")), env);
  for (const [start, end] of [["function validExternalUrl(", "function formatTime("], ["function attractionCard(", "function foodCard("], ["function makeField(", "function askDelete("]])
    vm.runInContext(appSource.slice(appSource.indexOf(start), appSource.indexOf(end)), env);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../public/ui-enhancements.js"), "utf8"), env);
  env.window.TripUI.init();
  return { env, node, state, dom, requests, fields: vm.runInContext("FIELDS", env), openAi: () => node("ai-open").events.click(),
    apply: () => node("ai-import-form").events.submit({ preventDefault() {} }),
    card: () => node("ai-preview-items").querySelector("article"),
    save: () => env.saveEditor({ preventDefault() {} }),
  };
}

function allText(node) { return typeof node === "string" ? node : [node.textContent, ...node.children.map(allText)].join(" "); }

test("new and edited attractions use the same optional rating select", () => {
  const h = harness(); h.env.openEditor("attraction");
  const field = () => h.dom.editFields.querySelector('select[name="scenic_rating"]');
  assert.ok(field()); assert.equal(field().required, false); assert.equal(field().value, "");
  assert.deepEqual(field().children.map((option) => option.value), ["", "4A", "5A"]);
  assert.match(allText(h.dom.editFields), /AI 补充或手动选择/);
  assert.doesNotMatch(allText(h.dom.editFields), /待核实/);
  h.env.openEditor("attraction", { id: 1, name: "故宫", scenic_rating: "5A", scenic_rating_info: official });
  assert.equal(field().value, "5A");
  h.env.openEditor("attraction", { id: 2, name: "旧记录" }); assert.equal(field().value, "");
});

test("ordinary create and update save only the selected business value, not source metadata", async () => {
  const h = harness(); h.env.openEditor("attraction");
  h.dom.editFields.querySelector('[name="name"]').value = "新增景点";
  h.dom.editFields.querySelector('[name="scenic_rating"]').value = "4A";
  await h.save();
  assert.equal(h.requests.at(-1).method, "POST");
  assert.equal(h.requests.at(-1).body.scenic_rating, "4A");
  h.env.openEditor("attraction", { id: 7, version: 3, city_id: "beijing", name: "故宫", scenic_rating: "5A", scenic_rating_info: official });
  h.dom.editFields.querySelector('[name="scenic_rating"]').value = "";
  await h.save();
  const saved = h.requests.at(-1);
  assert.equal(saved.method, "PUT"); assert.equal(saved.body.scenic_rating, ""); assert.equal(saved.body.version, 3);
  assert.ok(!Object.hasOwn(saved.body, "scenic_rating_info")); assert.ok(!Object.hasOwn(saved.body, "source_url"));
});

test("cards show only rating information without catalog names dates or links", () => {
  const h = harness();
  const card = h.env.attractionCard({ name: "故宫", city_id: "beijing", scenic_rating: "5A", scenic_rating_info: official });
  assert.match(allText(card), /5A 景区/);
  assert.ok(!card.querySelectorAll("a").some((link) => link.href === official.source_url));
  assert.doesNotMatch(allText(card), /文化和旅游部|名录景区|名录截至|2025-12-31/);
  assert.ok(!card.querySelectorAll("a").some((link) => link.textContent === "网页搜索 ↗"));
  const referenced = h.env.attractionCard({ name: "景点", city_id: "beijing", scenic_rating: "4A", scenic_rating_info: reference });
  assert.match(allText(referenced), /4A 景区/);
  assert.doesNotMatch(allText(referenced), /待核实|维基百科|参考名录景区名称|名录截至|2026-09-10/);
  assert.ok(!referenced.querySelectorAll("a").some((link) => link.href === reference.source_url));
  for (const status of [undefined, "unverified", "ambiguous", "unknown"]) {
    const pending = h.env.attractionCard({ name: "景点", scenic_rating: "4A", scenic_rating_info: { ...official, status } });
    assert.match(allText(pending), /4A 景区/); assert.doesNotMatch(allText(pending), /待核实|文化和旅游部/);
  }
  for (const scenic_rating of [undefined, "", "3A", "其他"]) {
    const missing = h.env.attractionCard({ name: "旧景点", scenic_rating });
    assert.equal(missing.querySelector(".scenic-rating"), null);
    assert.doesNotMatch(allText(missing), /级别待核实|未评级|无级别/);
  }
});

test("AI ratings keep displaying the grade after editing", async () => {
  const h = harness({ record: { name: "景点", scenic_rating: "4A", scenic_rating_info: reference } }); await h.openAi();
  const badge = () => h.card().querySelector(".ai-scenic-rating");
  assert.match(allText(badge()), /4A 景区/);
  assert.doesNotMatch(allText(badge()), /待核实|维基百科|参考名录景区名称|名录截至|2026-09-10/);
  assert.equal(badge().querySelectorAll("a").length, 0);
  const fields = h.card().querySelector(".ai-edit-fields");
  fields.querySelector('[name="0:name"]').value = "另一景点"; fields.events.input();
  assert.match(allText(badge()), /4A 景区/);
  assert.doesNotMatch(allText(h.card()), /待核实/);
  await h.apply();
  const payload = h.requests.find((request) => request.url.endsWith("/import")).body;
  assert.equal(payload.items[0].data.scenic_rating, "4A");
  assert.ok(!Object.hasOwn(payload.items[0].data, "scenic_rating_info"));
});

test("old AI drafts gain an editable empty select and import no source metadata", async () => {
  const h = harness({ record: { name: "旧草稿景点", description: "旧介绍" } }); await h.openAi();
  const rating = h.card().querySelector('select[name="0:scenic_rating"]');
  assert.ok(rating); assert.equal(rating.value, ""); assert.equal(rating.id, "ai-0-scenic_rating");
  assert.equal(h.card().querySelector(".ai-scenic-rating").hidden, true);
  assert.equal(h.card().querySelector(".scenic-rating"), null);
  rating.value = "4A"; h.card().querySelector(".ai-edit-fields").events.change();
  assert.equal(h.card().querySelector(".ai-scenic-rating").hidden, false);
  assert.match(allText(h.card()), /4A 景区/);
  await h.apply();
  const imported = h.requests.find((request) => request.url.endsWith("/import")).body.items[0];
  assert.equal(imported.kind, "attraction"); assert.equal(imported.data.scenic_rating, "4A");
  assert.deepEqual(Object.keys(imported.data).sort(), Array.from(h.fields.attraction, (field) => field.key).sort());
  assert.ok(!Object.hasOwn(imported.data, "scenic_rating_info"));
});

test("editing an AI draft's name or rating removes stale official attribution", async () => {
  const h = harness({ record: { name: "故宫", description: "介绍", scenic_rating: "5A", scenic_rating_info: official } }); await h.openAi();
  const fields = h.card().querySelector(".ai-edit-fields");
  assert.match(allText(h.card().querySelector(".ai-scenic-rating")), /5A 景区/);
  assert.doesNotMatch(allText(h.card().querySelector(".ai-scenic-rating")), /文化和旅游部|名录景区|名录截至|2025-12-31/);
  assert.equal(h.card().querySelector(".ai-scenic-rating").querySelectorAll('a').length, 0);
  fields.querySelector('[name="0:name"]').value = "新地点"; fields.events.input();
  assert.match(allText(h.card().querySelector(".ai-scenic-rating")), /5A 景区/);
  assert.doesNotMatch(allText(h.card().querySelector(".ai-scenic-rating")), /文化和旅游部/);
  fields.querySelector('[name="0:name"]').value = "故宫"; fields.events.input();
  assert.match(allText(h.card().querySelector(".ai-scenic-rating")), /5A 景区/);
  fields.querySelector('[name="0:scenic_rating"]').value = "4A"; fields.events.change();
  assert.match(allText(h.card().querySelector(".ai-scenic-rating")), /4A 景区/);
  await h.apply();
  const payload = h.requests.find((request) => request.url.endsWith("/import")).body;
  assert.equal(payload.items[0].data.scenic_rating, "4A"); assert.ok(!JSON.stringify(payload).includes("source_url"));
});

test("rating selects lock during import and remain locked after success, with footer close available", async () => {
  let finish;
  const h = harness({ record: { name: "故宫", scenic_rating: "5A", scenic_rating_info: official }, importHandler: () => new Promise((resolve) => { finish = resolve; }) });
  await h.openAi(); const rating = h.card().querySelector("select");
  const applying = h.apply(); assert.equal(rating.disabled, true);
  finish({ data: { created: 1 } }); await applying;
  assert.equal(rating.disabled, true); assert.equal(h.node("ai-result-close").disabled, false);
});

test("a failed AI import restores the select and retains its chosen grade", async () => {
  const h = harness({ record: { name: "故宫", scenic_rating: "5A", scenic_rating_info: official }, importHandler: async () => { throw new Error("连接中断"); } });
  await h.openAi(); const rating = h.card().querySelector("select"); rating.value = "4A";
  await h.apply(); assert.equal(rating.disabled, false); assert.equal(rating.value, "4A");
});
