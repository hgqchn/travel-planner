"use strict";

// A fill belongs to one editor session. Closing or replacing that editor discards
// its result; normal save remains the only operation that writes an item.
window.TripEditorAI = (() => {
  const $ = (id) => document.getElementById(id);
  const basicKeys = {
    attraction: ["name", "district", "category", "description", "duration", "transport", "scenic_rating", "tags"],
    food: ["name", "category", "cuisine", "tags", "description", "where_to_try", "tip"],
  };
  let session = null;

  function isCurrent(owner) {
    return session === owner && owner && dom.editDialog.open && state.currentEdit === owner.edit &&
      state.projectUnlocked && state.projectId === owner.projectId && state.userId === owner.userId &&
      state.cityId === owner.viewCityId && owner.edit.item === owner.item;
  }

  function isAttemptCurrent(owner, attempt) {
    return isCurrent(owner) && owner.attempt === attempt && !state.saving && !dom.identityDialog.open &&
      [...attempt.fields].every(([key, field]) => dom.editForm.elements.namedItem(key) === field) &&
      attempt.fields.get("name").value.trim() === attempt.request.item.name;
  }

  function renderControls() {
    if (!session) return;
    const retryJob = session.attempt?.job && !session.busy;
    $("editor-ai-fill").disabled = session.loading || session.busy || state.saving || dom.identityDialog.open || Boolean(session.edit.conflict) || session.config?.enabled === false;
    $("editor-ai-fill").textContent = session.loading ? "连接 AI 服务…" : session.busy ? "正在补充…" : retryJob ? "重试查询结果" : session.attempt ? "重试本次补充" : "AI 补充基本信息";
    $("editor-ai-model").disabled = session.loading || Boolean(session.attempt) || state.saving;
    $("editor-ai").setAttribute("aria-busy", String(session.loading || session.busy));
  }

  function invalidate(message = "") {
    if (!session) return;
    clearTimeout(session.timer);
    session.timer = null;
    session.attempt = null;
    session.busy = false;
    $("editor-ai-status").textContent = message;
    $("editor-ai-error").textContent = "";
    $("editor-ai-notices").replaceChildren();
    $("editor-ai-notices").hidden = true;
    renderControls();
  }

  function close() {
    if (session) clearTimeout(session.timer);
    session = null;
    $("editor-ai").hidden = true;
  }

  function handleError(error) {
    $("editor-ai-error").textContent = error.message || "暂时无法完成补充，请稍后重试。";
    if (error.data?.code === "PROJECT_LOCKED") showProjectGate("项目访问已过期，请重新输入口令。", true);
    else if (error.status === 401) openIdentity(true);
  }

  async function loadConfig(owner) {
    owner.loading = true;
    renderControls();
    try {
      const { data } = await requestJson("/api/ai/config");
      if (!isCurrent(owner)) return false;
      owner.config = data;
      if (!$("editor-ai-model").value) $("editor-ai-model").value = data.model || "";
      $("editor-ai-error").textContent = data.enabled ? "" : "AI 服务尚未配置，仍可手动填写和保存。";
      return Boolean(data.enabled);
    } catch (error) {
      if (isCurrent(owner)) handleError(error);
      return false;
    } finally {
      if (isCurrent(owner)) { owner.loading = false; renderControls(); }
    }
  }

  async function open() {
    close();
    if (!dom.editDialog.open || !basicKeys[state.currentEdit?.kind] || !state.projectUnlocked || !state.userId) return;
    const owner = session = {
      edit: state.currentEdit, item: state.currentEdit.item, projectId: state.projectId, userId: state.userId,
      viewCityId: state.cityId, cityId: state.currentEdit.item?.city_id || state.cityId,
      config: null, loading: false, busy: false, attempt: null, timer: null,
    };
    $("editor-ai").hidden = false;
    $("editor-ai-model").value = "";
    invalidate();
    await loadConfig(owner);
  }

  function sync() {
    if (session && !isCurrent(session)) { void open(); return; }
    renderControls();
  }

  function schedulePoll(owner, attempt, delay = 2000) {
    clearTimeout(owner.timer);
    owner.timer = setTimeout(() => poll(owner, attempt), delay);
  }

  function applyResult(owner, attempt, job) {
    const list = job.result?.[owner.edit.kind === "attraction" ? "attractions" : "foods"];
    const correction = job.result?.name_correction;
    const correctedName = owner.edit.kind === "attraction" && !attempt.touched.has("name") && correction?.from === attempt.request.item.name &&
      typeof correction.to === "string" && correction.to.length > 0 && correction.to.length <= 60 &&
      correction.to.trim() === correction.to && !/[\u0000-\u001f\u007f]/.test(correction.to) &&
      correction.to === list?.[0]?.name && correction.to !== attempt.request.item.name;
    if (!Array.isArray(list) || list.length !== 1 || !list[0] || (list[0].name !== attempt.request.item.name && !correctedName)) {
      throw new Error("AI 返回的内容与当前条目不匹配，请重新补充。");
    }
    if (correctedName) attempt.fields.get("name").value = correction.to;
    let count = 0;
    for (const definition of FIELDS[owner.edit.kind]) {
      if (definition.key === "name" || !basicKeys[owner.edit.kind].includes(definition.key)) continue;
      const field = attempt.fields.get(definition.key);
      const value = definition.tagsKind ? window.TripTaxonomy.formatTags(list[0][definition.key]) : list[0][definition.key];
      const initial = attempt.request.item[definition.key];
      if (!field || (Array.isArray(initial) ? initial.length : initial) || field.value.trim() || attempt.touched.has(definition.key) || typeof value !== "string" || !value.trim()) continue;
      if (definition.maxlength && value.length > definition.maxlength) continue;
      if (definition.options && !definition.options.some((option) => option.value === value)) continue;
      if (definition.categoryKind && !window.TripTaxonomy.categoryOptions(definition.categoryKind).some(option => option.value === value)) continue;
      field.value = value;
      if (definition.tagsKind) field.dispatchEvent(new Event("input", { bubbles: true }));
      count += 1;
    }
    if (count || correctedName) state.editorDirty = true;
    owner.attempt = null;
    owner.busy = false;
    $("editor-ai-status").textContent = correctedName
      ? `名称已修正为“${correction.to}”${count ? `，并补充 ${count} 项基本信息` : ""}，请核对后保存。`
      : count ? `已补充 ${count} 项基本信息，请核对后保存。` : "没有可补充的空白信息。已保留现有内容及你刚才的修改。";
    const notices = Array.isArray(job.result.notices) ? job.result.notices.filter((notice) => typeof notice === "string") : [];
    $("editor-ai-notices").replaceChildren(...notices.map((notice) => element("li", "", notice)));
    $("editor-ai-notices").hidden = notices.length === 0;
    renderControls();
  }

  function updateJob(owner, attempt, job) {
    if (!isAttemptCurrent(owner, attempt)) return;
    if (!job || !["queued", "running", "ready", "failed"].includes(job.status)) throw new Error("AI 任务响应无法识别，请重试查询。");
    attempt.job = job;
    $("editor-ai-error").textContent = "";
    if (job.status === "ready") { applyResult(owner, attempt, job); return; }
    if (job.status === "failed") {
      owner.attempt = null;
      owner.busy = false;
      $("editor-ai-status").textContent = "补充未完成，表单内容已保留。";
      $("editor-ai-error").textContent = job.error || "AI 生成失败，请稍后重新补充。";
      renderControls();
      return;
    }
    owner.busy = true;
    $("editor-ai-status").textContent = job.status === "queued" ? "已排队，稍后自动补充。可以继续编辑；保存或关闭后本次结果不再填入。" : "正在补充基本信息…可以继续编辑，已有内容和本次手动修改都会保留。";
    renderControls();
    schedulePoll(owner, attempt);
  }

  async function poll(owner, attempt) {
    if (!isAttemptCurrent(owner, attempt) || !attempt.job?.id) return;
    clearTimeout(owner.timer);
    owner.busy = true;
    renderControls();
    try {
      const { data } = await requestJson(`/api/ai/jobs/${encodeURIComponent(attempt.job.id)}`);
      if (isAttemptCurrent(owner, attempt)) updateJob(owner, attempt, data.job || data);
    } catch (error) {
      if (!isAttemptCurrent(owner, attempt)) return;
      owner.busy = false;
      if ([403, 404].includes(error.status)) owner.attempt = null;
      $("editor-ai-status").textContent = owner.attempt ? "暂时无法查询结果，可重试查询本次任务；表单内容已保留。" : "任务已不可用，请重新补充。";
      renderControls();
      handleError(error);
    }
  }

  async function generate() {
    const owner = session;
    if (!isCurrent(owner) || owner.loading || owner.busy || state.saving || owner.edit.conflict || dom.identityDialog.open) return;
    if (!owner.config && !await loadConfig(owner)) return;
    if (!isCurrent(owner) || !owner.config?.enabled) return;
    $("editor-ai-error").textContent = "";
    if (owner.attempt?.job) { await poll(owner, owner.attempt); return; }
    if (!owner.attempt) {
      const fields = new Map(basicKeys[owner.edit.kind].map((key) => [key, dom.editForm.elements.namedItem(key)]));
      const name = fields.get("name");
      if (!name?.value.trim() || !name.checkValidity()) {
        $("editor-ai-error").textContent = `请先填写有效的${owner.edit.kind === "attraction" ? "游玩点" : "美食"}名称。`;
        name?.focus();
        name?.reportValidity();
        return;
      }
      const item = Object.fromEntries([...fields].map(([key, field]) => [key, field?.value.trim() || ""]));
      item.tags = window.TripTaxonomy.parseTags(item.tags);
      if (owner.edit.kind === "food" && [...fields].every(([key, field]) => key === "name" ||
          (key === "cuisine" && item.category !== "特色菜肴") || field?.value.trim())) {
        $("editor-ai-status").textContent = "基本信息已填写完整；清空需要重新补充的字段后再试。";
        return;
      }
      const model = $("editor-ai-model").value.trim();
      if (model && !/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$/.test(model)) {
        $("editor-ai-error").textContent = "请输入有效的模型 ID（英文字母、数字或 . _ : / -）。";
        $("editor-ai-model").focus();
        return;
      }
      owner.attempt = { fields, touched: new Set(), job: null, request: {
        purpose: "fill_item", city_id: owner.cityId, kinds: [owner.edit.kind], item, ...(model ? { model } : {}),
        request_id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      } };
    }
    const attempt = owner.attempt;
    owner.busy = true;
    $("editor-ai-notices").hidden = true;
    $("editor-ai-status").textContent = "正在提交补充需求…";
    renderControls();
    try {
      const { data } = await requestJson("/api/ai/jobs", { method: "POST", body: JSON.stringify(attempt.request) });
      if (isAttemptCurrent(owner, attempt)) updateJob(owner, attempt, data.job || data);
    } catch (error) {
      if (!isAttemptCurrent(owner, attempt)) return;
      owner.busy = false;
      if (error.status && error.status < 500) owner.attempt = null;
      $("editor-ai-status").textContent = owner.attempt ? "连接中断，重试将继续同一次补充，不会重复创建任务。" : "补充未完成，表单内容已保留。";
      renderControls();
      handleError(error);
    }
  }

  function edited(event) {
    const attempt = session?.attempt;
    if (!attempt) return;
    for (const [key, field] of attempt.fields) {
      if (event.target !== field) continue;
      attempt.touched.add(key);
      if (key === "name") invalidate("名称已修改，本次 AI 结果不再填入；可按新名称重新补充。");
      return;
    }
  }

  function init() {
    $("editor-ai-fill").addEventListener("click", generate);
    dom.editFields.addEventListener("input", edited);
    dom.editFields.addEventListener("change", edited);
    dom.editDialog.addEventListener("close", () => { if (!dom.editDialog.open) close(); });
    dom.identityDialog.addEventListener("close", sync);
  }

  return { init, open, close, invalidate, sync };
})();
