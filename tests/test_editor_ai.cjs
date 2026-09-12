const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const controllerSource = fs.readFileSync(path.join(__dirname, "../public/editor-ai.js"), "utf8");
const appSource = fs.readFileSync(path.join(__dirname, "../public/app.js"), "utf8");
const fields = vm.runInNewContext(`${appSource.slice(appSource.indexOf("const FIELDS ="), appSource.indexOf("const state ="))}\nFIELDS;`);
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const failure = (status) => Object.assign(new Error("服务暂时不可用"), { status });

function harness({ kind = "attraction", initial = { name: "外滩" }, item = null, config, post, get } = {}) {
  const nodes = new Map(), inputs = new Map(), requests = [], timers = new Map();
  let nextTimer = 0, nextRequest = 0;
  class Node {
    constructor() { this.value = ""; this.textContent = ""; this.hidden = false; this.disabled = false; this.open = false; this.events = {}; this.children = []; }
    addEventListener(type, handler) { (this.events[type] ||= []).push(handler); }
    dispatchEvent(event) { return this.emit(event.type, event); }
    async emit(type, event = {}) { for (const handler of this.events[type] || []) await handler({ type, target: this, ...event }); }
    setAttribute(name, value) { this[name] = value; }
    replaceChildren(...children) { this.children = children; }
    checkValidity() { return (!this.required || this.value.trim()) && (!this.maxLength || this.value.length <= this.maxLength); }
    reportValidity() { this.reported = true; return this.checkValidity(); }
    focus() { this.focused = true; }
  }
  const node = (id) => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
  const installFields = (nextKind, values) => {
    inputs.clear();
    for (const definition of fields[nextKind]) {
      const field = new Node();
      field.name = definition.key; field.value = values[definition.key] || "";
      field.required = definition.required; field.maxLength = definition.maxlength;
      inputs.set(definition.key, field);
    }
  };
  installFields(kind, initial);
  const editForm = node("edit-form");
  editForm.elements = { namedItem: (name) => inputs.get(name) || null };
  const dom = { editForm, editDialog: node("edit-dialog"), editFields: node("edit-fields"), identityDialog: node("identity-dialog") };
  dom.editDialog.open = true;
  const state = { currentEdit: { kind, item, conflict: null }, projectId: "main", userId: "Alice", projectUnlocked: true, cityId: "shanghai", saving: false, editorDirty: false };
  const makeJob = (body, values = {}) => ({
    id: "a".repeat(32), city_id: body.city_id, status: "ready", request: body,
    result: {
      attractions: body.kinds[0] === "attraction" ? [{ name: body.item.name, district: "黄浦区", category: "城市观景", description: "沿江步道与历史建筑。", duration: "1–2 小时", transport: "根据出发地查询公共交通。", scenic_rating: "", tags: [], ...values }] : [],
      foods: body.kinds[0] === "food" ? [{ name: body.item.name, category: "面点饼类", cuisine: "", tags: [], description: "本地风味介绍。", where_to_try: "市区传统小吃店", tip: "按人数适量点单。", ...values }] : [],
      notices: ["出发前核实交通及预约信息。"],
    },
  });
  const env = {
    Event: class { constructor(type) { this.type = type; } },
    state, dom, FIELDS: fields, window: {},
    document: { getElementById: node },
    element: (tag, className, text) => { const result = new Node(); result.tagName = tag; result.textContent = text; return result; },
    crypto: { randomUUID: () => `request-${++nextRequest}` },
    setTimeout: (callback) => { const id = ++nextTimer; timers.set(id, callback); return id; },
    clearTimeout: (id) => timers.delete(id),
    showProjectGate() { state.projectUnlocked = false; dom.editDialog.open = false; env.window.TripEditorAI.close(); },
    openIdentity() { env.window.TripEditorAI.invalidate(); dom.identityDialog.open = true; env.window.TripEditorAI.sync(); },
    requestJson: async (url, options = {}) => {
      const body = options.body ? JSON.parse(options.body) : null;
      requests.push({ url, body, method: options.method || "GET" });
      if (url === "/api/ai/config") return typeof config === "function" ? config() : { data: config || { enabled: true, model: "deepseek-v4-flash-vision-exp" } };
      if (url === "/api/ai/jobs") return post ? post(body, makeJob) : { data: { job: makeJob(body) } };
      if (url.startsWith("/api/ai/jobs/")) return get ? get(url) : { data: { job: makeJob(requests.find((request) => request.body)?.body) } };
      throw new Error(`Unexpected request: ${url}`);
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../public/place-taxonomy.js"), "utf8"), env);
  env.window.TripTaxonomy.configure(JSON.parse(fs.readFileSync(path.join(__dirname, "../place_taxonomy.json"), "utf8")));
  vm.runInNewContext(controllerSource, env);
  const controller = env.window.TripEditorAI;
  controller.init();
  return {
    node, inputs, state, dom, controller, requests, timers, makeJob,
    open: () => controller.open(), generate: () => node("editor-ai-fill").emit("click"),
    posts: () => requests.filter((request) => request.method === "POST"),
    edit: async (key, value, type = "input") => { const target = inputs.get(key); target.value = value; await dom.editFields.emit(type, { target }); },
    newEditor: async (nextKind, values, nextItem = null) => { state.currentEdit = { kind: nextKind, item: nextItem, conflict: null }; installFields(nextKind, values); dom.editDialog.open = true; await controller.open(); },
    poll: async () => { const entry = timers.entries().next().value; if (entry) { timers.delete(entry[0]); await entry[1](); } },
  };
}

test("new attraction fills only basic blank fields, using configured model and current city", async () => {
  const h = harness({ initial: { name: "外滩", description: "自己写的简介", link: "https://example.com/travel", navigation_link: "https://example.com/map" } });
  await h.open();
  assert.equal(h.node("editor-ai").hidden, false);
  assert.equal(h.node("editor-ai-model").value, "deepseek-v4-flash-vision-exp");
  await h.generate();
  const payload = h.posts()[0].body;
  assert.equal(payload.purpose, "fill_item");
  assert.deepEqual(payload.kinds, ["attraction"]);
  assert.equal(payload.city_id, "shanghai");
  assert.equal(payload.model, "deepseek-v4-flash-vision-exp");
  assert.equal(payload.item.link, undefined);
  assert.equal(payload.item.navigation_link, undefined);
  assert.equal(h.inputs.get("description").value, "自己写的简介");
  assert.equal(h.inputs.get("district").value, "黄浦区");
  assert.equal(h.inputs.get("name").value, "外滩");
  assert.equal(h.inputs.get("link").value, "https://example.com/travel");
  assert.equal(h.inputs.get("navigation_link").value, "https://example.com/map");
  assert.equal(h.state.editorDirty, true);
  assert.match(h.node("editor-ai-status").textContent, /已补充 4 项/);
  assert.equal(h.node("editor-ai-notices").children[0].textContent, "出发前核实交通及预约信息。");
  assert.equal(h.requests.some((request) => /\/import$|\/api\/items/.test(request.url)), false);
});

test("editing a food uses the item's city and selected model", async () => {
  const h = harness({ kind: "food", initial: { name: "生煎", category: "风味小吃", tags: "早餐" }, item: { id: "food-id", city_id: "beijing", version: 2 } });
  await h.open();
  h.node("editor-ai-model").value = "deepseek-custom";
  await h.generate();
  assert.equal(h.posts()[0].body.city_id, "beijing");
  assert.equal(h.posts()[0].body.model, "deepseek-custom");
  assert.equal(h.inputs.get("category").value, "风味小吃");
  assert.equal(h.inputs.get("tags").value, "早餐");
  assert.deepEqual(h.posts()[0].body.item.tags, ["早餐"]);
  assert.equal(h.inputs.get("description").value, "本地风味介绍。");
  assert.equal(h.posts()[0].body.item.id, undefined);
});

test("verified attraction name correction updates the draft while preserving existing information", async () => {
  const correctedName = "八达岭—慕田峪长城旅游区";
  const h = harness({
    initial: { name: "八达岭长城", description: "已有的游览建议", navigation_link: "https://example.com/map" },
    post: (body, makeJob) => {
      const job = makeJob(body, { name: correctedName, scenic_rating: "5A" });
      job.result.name_correction = { from: body.item.name, to: correctedName, reason: "已匹配景区名录全称。" };
      return { data: { job } };
    },
  });
  await h.open(); await h.generate();
  assert.equal(h.inputs.get("name").value, correctedName);
  assert.equal(h.inputs.get("scenic_rating").value, "5A");
  assert.equal(h.inputs.get("description").value, "已有的游览建议");
  assert.equal(h.inputs.get("navigation_link").value, "https://example.com/map");
  assert.equal(h.state.editorDirty, true);
  assert.match(h.node("editor-ai-status").textContent, /名称已修正为“八达岭—慕田峪长城旅游区”.*请核对后保存/);
  assert.equal(h.requests.some((request) => /\/import$|\/api\/items/.test(request.url)), false);
});

test("complete attraction information can still be corrected to its verified catalog name", async () => {
  const correctedName = "八达岭—慕田峪长城旅游区";
  const initial = { name: "八达岭长城", district: "延庆区", category: "历史古迹", description: "已有介绍", duration: "半天", transport: "已有交通建议", scenic_rating: "5A" };
  const h = harness({
    initial,
    post: (body, makeJob) => {
      const job = makeJob(body, { name: correctedName, district: "", category: "", description: "", duration: "", transport: "", scenic_rating: "" });
      job.result.name_correction = { from: body.item.name, to: correctedName };
      return { data: { job } };
    },
  });
  await h.open(); await h.generate();
  assert.equal(h.posts().length, 1);
  assert.equal(h.inputs.get("name").value, correctedName);
  for (const [key, value] of Object.entries(initial)) {
    if (key !== "name") assert.equal(h.inputs.get(key).value, value);
  }
  assert.equal(h.state.editorDirty, true);
  assert.equal(h.node("editor-ai-status").textContent, `名称已修正为“${correctedName}”，请核对后保存。`);
});

test("complete attractions without a name correction keep all existing fields", async () => {
  const initial = { name: "外滩", district: "黄浦区", category: "城市观景", description: "已有介绍", duration: "半天", transport: "已有交通建议", scenic_rating: "4A" };
  const h = harness({ initial });
  await h.open(); await h.generate();
  assert.equal(h.posts().length, 1);
  for (const [key, value] of Object.entries(initial)) assert.equal(h.inputs.get(key).value, value);
  assert.equal(h.state.editorDirty, false);
  assert.match(h.node("editor-ai-status").textContent, /没有可补充的空白信息.*保留现有内容/);
});

test("unverified or invalid attraction name changes never modify the draft", async () => {
  const name = "八达岭长城", to = "八达岭—慕田峪长城旅游区";
  const cases = [
    { resultName: to },
    { resultName: to, correction: { from: "慕田峪长城", to } },
    { resultName: to, correction: { from: name, to: "其他景点" } },
    ...["", " ", "长".repeat(61), ` ${to}`, `${to}\n`, 123].map((invalid) => ({ resultName: invalid, correction: { from: name, to: invalid } })),
  ];
  for (const { resultName, correction } of cases) {
    const h = harness({ initial: { name }, post: (body, makeJob) => {
      const job = makeJob(body, { name: resultName });
      if (correction) job.result.name_correction = correction;
      return { data: { job } };
    } });
    await h.open(); await h.generate();
    assert.equal(h.inputs.get("name").value, name);
    assert.equal(h.inputs.get("description").value, "");
    assert.equal(h.state.editorDirty, false);
    assert.match(h.node("editor-ai-error").textContent, /与当前条目不匹配/);
  }
});

test("food results cannot rename the item through attraction correction metadata", async () => {
  const h = harness({ kind: "food", initial: { name: "生煎" }, post: (body, makeJob) => {
    const job = makeJob(body, { name: "生煎馒头" });
    job.result.name_correction = { from: "生煎", to: "生煎馒头" };
    return { data: { job } };
  } });
  await h.open(); await h.generate();
  assert.equal(h.inputs.get("name").value, "生煎");
  assert.equal(h.inputs.get("description").value, "");
  assert.equal(h.state.editorDirty, false);
  assert.match(h.node("editor-ai-error").textContent, /与当前条目不匹配/);
});

test("late verified name corrections cannot overwrite a manual rename or a new editor", async () => {
  for (const change of ["rename", "restore", "reopen"]) {
    const pending = deferred(); let body, makeJob;
    const h = harness({ initial: { name: "八达岭长城" }, post: (request, create) => { body = request; makeJob = create; return pending.promise; } });
    await h.open(); const generation = h.generate();
    if (change === "reopen") await h.newEditor("attraction", { name: "八达岭长城" });
    else {
      await h.edit("name", "我想去的长城");
      if (change === "restore") await h.edit("name", "八达岭长城");
    }
    const job = makeJob(body, { name: "八达岭—慕田峪长城旅游区" });
    job.result.name_correction = { from: body.item.name, to: job.result.attractions[0].name };
    pending.resolve({ data: { job } }); await generation;
    assert.equal(h.inputs.get("name").value, change === "rename" ? "我想去的长城" : "八达岭长城");
    assert.equal(h.inputs.get("description").value, "");
    assert.equal(h.state.editorDirty, false);
  }
});

test("empty model uses backend default and empty/invalid name never submits", async () => {
  const h = harness({ initial: {} });
  await h.open();
  await h.generate();
  assert.equal(h.posts().length, 0);
  assert.match(h.node("editor-ai-error").textContent, /先填写有效的游玩点名称/);
  h.inputs.get("name").value = "长".repeat(61);
  await h.generate();
  assert.equal(h.posts().length, 0);
  h.inputs.get("name").value = "外滩";
  h.node("editor-ai-model").value = "";
  await h.generate();
  assert.equal(Object.hasOwn(h.posts()[0].body, "model"), false);
});

test("complete food information avoids an unnecessary generation", async () => {
  const h = harness({ kind: "food", initial: { name: "生煎", category: "面点饼类", cuisine: "本帮菜", tags: "早餐", description: "介绍", where_to_try: "区域", tip: "提示" } });
  await h.open(); await h.generate();
  assert.equal(h.posts().length, 0);
  assert.match(h.node("editor-ai-status").textContent, /填写完整.*清空/);
});

test("AI fills cuisine and multiple tags for specialty dishes without changing the chosen category", async () => {
  const h = harness({ kind: 'food', initial: { name: '红烧肉', category: '特色菜肴' },
    post: (body, makeJob) => ({ data: { job: makeJob(body, { category: '特色菜肴', cuisine: '本帮菜', tags: ['咸甜', '适合分享', '咸甜'] }) } }) });
  await h.open(); await h.generate();
  assert.equal(h.inputs.get('category').value, '特色菜肴');
  assert.equal(h.inputs.get('cuisine').value, '本帮菜');
  assert.equal(h.inputs.get('tags').value, '咸甜，适合分享');
  assert.deepEqual(h.posts()[0].body.item.tags, []);
});

test("a complete drink does not require an unrelated cuisine to avoid repeated AI calls", async () => {
  const h = harness({ kind: 'food', initial: { name: '酸梅汤', category: '饮品', cuisine: '', tags: '酸甜', description: '介绍', where_to_try: '区域', tip: '提示' } });
  await h.open(); await h.generate();
  assert.equal(h.posts().length, 0);
});

test("existing and concurrent cuisine or tag edits survive AI completion", async () => {
  const pending = deferred(); let job;
  const h = harness({ kind: 'food', initial: { name: '红烧肉', category: '特色菜肴', cuisine: '本帮菜', tags: '夜宵' },
    post: (body, makeJob) => { job = makeJob(body, { cuisine: '川菜', tags: ['麻辣'] }); return pending.promise; } });
  await h.open(); const generating = h.generate();
  await h.edit('tags', '夜宵，适合分享');
  pending.resolve({ data: { job } }); await generating;
  assert.equal(h.inputs.get('cuisine').value, '本帮菜');
  assert.equal(h.inputs.get('tags').value, '夜宵，适合分享');
  assert.deepEqual(h.posts()[0].body.item.tags, ['夜宵']);
});

test("manual edits made while generating, including clearing back to blank, survive completion", async () => {
  const pending = deferred();
  let body, makeJob;
  const h = harness({ post: (request, create) => { body = request; makeJob = create; return pending.promise; } });
  await h.open(); const generation = h.generate();
  await h.edit("description", "生成期间写的简介");
  await h.edit("district", "自己填写"); await h.edit("district", "");
  await h.edit("scenic_rating", "5A", "change");
  pending.resolve({ data: { job: makeJob(body, { scenic_rating: "4A" }) } });
  await generation;
  assert.equal(h.inputs.get("description").value, "生成期间写的简介");
  assert.equal(h.inputs.get("district").value, "");
  assert.equal(h.inputs.get("scenic_rating").value, "5A");
  assert.equal(h.inputs.get("duration").value, "1–2 小时");
});

test("ambiguous POST failures retry with the same request id and snapshot", async () => {
  let calls = 0;
  const h = harness({ post: (body, makeJob) => { if (++calls === 1) throw failure(502); return { data: { job: makeJob(body) } }; } });
  await h.open(); await h.generate();
  assert.match(h.node("editor-ai-status").textContent, /同一次补充/);
  assert.equal(h.node("editor-ai-model").disabled, true);
  await h.edit("description", "重试前手动填写");
  await h.generate();
  assert.deepEqual(h.posts()[0].body, h.posts()[1].body);
  assert.equal(h.inputs.get("description").value, "重试前手动填写");
});

test("definite creation rejection and failed jobs permit a fresh request", async () => {
  for (const outcome of ["reject", "failed"]) {
    let calls = 0;
    const h = harness({ post: (body, makeJob) => {
      if (++calls === 1) {
        if (outcome === "reject") throw failure(429);
        return { data: { job: { id: "a".repeat(32), status: "failed", error: "模型不可用" } } };
      }
      return { data: { job: makeJob(body) } };
    } });
    await h.open(); await h.generate(); await h.generate();
    assert.notEqual(h.posts()[0].body.request_id, h.posts()[1].body.request_id);
    assert.equal(h.inputs.get("district").value, "黄浦区");
  }
});

test("queued jobs poll and failed polling retries GET without another generation", async () => {
  let body, makeJob, gets = 0;
  const h = harness({
    post: (request, create) => { body = request; makeJob = create; return { data: { job: { id: "queued-id", status: "queued" } } }; },
    get: () => { if (++gets === 1) throw failure(503); return { data: { job: makeJob(body) } }; },
  });
  await h.open(); await h.generate();
  assert.equal(h.timers.size, 1);
  await h.poll();
  assert.equal(h.node("editor-ai-fill").textContent, "重试查询结果");
  await h.generate();
  assert.equal(h.posts().length, 1);
  assert.equal(gets, 2);
  assert.equal(h.inputs.get("district").value, "黄浦区");
});

test("renaming the item invalidates the old result even if the name is restored", async () => {
  const pending = deferred(); let body, makeJob;
  const h = harness({ post: (request, create) => { body = request; makeJob = create; return pending.promise; } });
  await h.open(); const generation = h.generate();
  await h.edit("name", "豫园"); await h.edit("name", "外滩");
  pending.resolve({ data: { job: makeJob(body) } }); await generation;
  assert.equal(h.inputs.get("description").value, "");
  assert.match(h.node("editor-ai-status").textContent, /名称已修改/);
});

test("late responses cannot fill a closed or newly opened editor", async () => {
  for (const reopen of [false, true]) {
    const pending = deferred(); let body, makeJob;
    const h = harness({ post: (request, create) => { body = request; makeJob = create; return pending.promise; } });
    await h.open(); const generation = h.generate();
    h.dom.editDialog.open = false; await h.dom.editDialog.emit("close");
    if (reopen) await h.newEditor("food", { name: "生煎" });
    pending.resolve({ data: { job: makeJob(body) } }); await generation;
    assert.equal(h.inputs.get("description").value, "");
    assert.equal(h.state.editorDirty, false);
  }
});

test("city, project, identity, save, and conflict changes discard in-flight results", async () => {
  const changes = [
    (h) => { h.state.cityId = "beijing"; h.controller.sync(); h.state.cityId = "shanghai"; h.controller.sync(); },
    (h) => { h.state.projectId = "other"; h.controller.sync(); },
    (h) => { h.state.userId = "Bob"; h.controller.sync(); },
    (h) => { h.state.projectUnlocked = false; h.controller.sync(); },
    (h) => { h.controller.invalidate(); h.state.saving = true; h.controller.sync(); },
    (h) => { h.state.currentEdit.item = { id: "remote", version: 2 }; h.controller.open(); },
    (h) => { h.controller.invalidate(); h.dom.identityDialog.open = true; h.controller.sync(); },
  ];
  for (const change of changes) {
    const pending = deferred(); let body, makeJob;
    const h = harness({ post: (request, create) => { body = request; makeJob = create; return pending.promise; } });
    await h.open(); const generation = h.generate();
    change(h);
    pending.resolve({ data: { job: makeJob(body) } }); await generation;
    assert.equal(h.inputs.get("description").value, "");
  }
});

test("unconfigured AI, conflicts, and other categories keep manual editing available", async () => {
  const h = harness({ config: { enabled: false, model: "default" } });
  await h.open(); await h.generate();
  assert.equal(h.node("editor-ai-fill").disabled, true);
  assert.equal(h.inputs.get("name").disabled, false);
  assert.equal(h.posts().length, 0);
  const second = harness(); await second.open();
  second.state.currentEdit.conflict = { id: "remote" }; second.controller.invalidate(); await second.generate();
  assert.equal(second.node("editor-ai-fill").disabled, true);
  assert.equal(second.posts().length, 0);
  await second.newEditor("itinerary", { title: "安排", date: "2026-10-01" });
  assert.equal(second.node("editor-ai").hidden, true);
});

test("model input cannot pollute item FormData or block a normal save", () => {
  const html = fs.readFileSync(path.join(__dirname, "../public/index.html"), "utf8");
  const modelInput = html.match(/<input\s+id="editor-ai-model"[^>]+>/)[0];
  assert.doesNotMatch(modelInput, /\b(?:name|pattern|required)=/);
  const section = html.slice(html.indexOf('<section class="editor-ai"'), html.indexOf('<div class="edit-fields"'));
  assert.doesNotMatch(section, /<button[^>]+type="submit"/);
});
