"use strict";

window.TripGuidance = (() => {
  const $ = id => document.getElementById(id);
  const scope = () => JSON.stringify([state.projectId, state.cityId, state.userId, state.projectUnlocked]);
  let signature = "", editor = null, conflict = null, busy = false, initialized = false, generation = 0;

  let generator = null, timer = null;

  function setBusy(value) {
    busy = value;
    for (const id of ["guidance-save", "guidance-delete", "guidance-close", "guidance-reload", "guidance-notices"]) $(id).disabled = value;
    $("guidance-save").textContent = value ? "正在保存…" : "保存提示";
  }

  function close() {
    generation += 1;
    editor = null;
    conflict = null;
    setBusy(false);
    $("guidance-dialog").close();
  }

  function load(group) {
    editor = { group, scope: scope() };
    conflict = null;
    $("guidance-notices").value = (group.notices || []).join("\n");
    $("guidance-city").textContent = `${group.city_name} · 同行者共享`;
    $("guidance-delete").hidden = group.deleted || !group.notices?.length;
    $("guidance-error").textContent = "";
    $("guidance-reload").hidden = true;
  }

  async function open(group) {
    if (!requireIdentity() || busy) return;
    const activeScope = scope(), request = ++generation;
    try {
      if (!group) {
        const { data } = await requestJson(`/api/travel-guidance?city_id=${encodeURIComponent(state.cityId)}`);
        group = data.guidance;
      }
      if (request !== generation || scope() !== activeScope) return;
      load(group);
      if (!$("guidance-dialog").open) $("guidance-dialog").showModal();
    } catch (error) {
      if (request === generation && scope() === activeScope) showToast(error.message);
    }
  }

  async function submit(deleting = false) {
    if (!editor || busy || editor.scope !== scope()) return;
    const active = editor, request = generation;
    if (deleting && !window.confirm(`删除${active.group.city_name}的出行提示？行程、地点和美食都会保留。`)) return;
    if (conflict) {
      $("guidance-error").textContent = "请先核对并载入最新版，再保存或删除。";
      return;
    }
    setBusy(true);
    $("guidance-error").textContent = "";
    const payload = { city_id: active.group.city_id, version: active.group.version };
    if (!deleting) {
      if (active.planVersion) payload.plan_version = active.planVersion;
      payload.notices = $("guidance-notices").value.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
    }
    try {
      const { data } = await requestJson("/api/travel-guidance", { method: deleting ? "DELETE" : "PUT", body: JSON.stringify(payload) });
      if (request !== generation || editor !== active || scope() !== active.scope) return;
      state.travelGuidance = (state.travelGuidance || []).filter(group => group.city_id !== active.group.city_id);
      if (!data.guidance.deleted) state.travelGuidance.push(data.guidance);
      state.revision = Math.max(state.revision || 0, data.revision);
      close();
      render();
      showToast(deleting ? "出行提示已删除" : "出行提示已保存");
      await fetchSnapshot(true);
    } catch (error) {
      if (request !== generation || editor !== active || scope() !== active.scope) return;
      $("guidance-error").textContent = error.message;
      if (error.status === 409 && error.data?.current) {
        conflict = error.data.current;
        $("guidance-reload").hidden = false;
      }
    } finally {
      if (request === generation && editor === active) setBusy(false);
    }
  }

  const requestId = () => crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;

  function stopGeneration() {
    clearTimeout(timer); timer = null; generator = null;
    $("guidance-confirm-dialog").close();
  }
  function beginGeneration() {
    if (!requireIdentity() || busy) return;
    stopGeneration();
    generator = { scope:scope(), cityId:state.cityId, pending:false, job:null,
      requestId:requestId() };
    $("guidance-confirm-city").textContent = `当前城市：${state.cities?.find(city => city.id === state.cityId)?.name || state.travelGuidance?.find(group => group.city_id === state.cityId)?.city_name || "当前城市"}。重新生成不会直接覆盖现有提示。`;
    $("guidance-generate-status").textContent = "";
    $("guidance-generate-confirm").disabled = false;
    $("guidance-generate-confirm").textContent = "已规划完毕，生成提示";
    $("guidance-generate-cancel").textContent = "还没规划好";
    $("guidance-confirm-dialog").showModal();
  }
  function generationActive(owner) { return generator === owner && scope() === owner.scope; }
  async function generate() {
    const owner = generator;
    if (!owner || owner.pending || !generationActive(owner)) return;
    owner.pending = true;
    $("guidance-generate-confirm").disabled = true;
    $("guidance-generate-cancel").textContent = "关闭";
    $("guidance-generate-status").textContent = owner.job ? "正在查询生成结果…" : "正在读取已保存的行程并提交生成…";
    try {
      const { data:job } = owner.job
        ? await requestJson(`/api/ai/jobs/${encodeURIComponent(owner.job.id)}`)
        : await requestJson('/api/ai/jobs', {method:'POST',body:JSON.stringify({purpose:'travel_guidance',
            city_id:owner.cityId,confirmed_complete:true,request_id:owner.requestId})});
      if (!generationActive(owner)) return;
      owner.job = job;
      if (job.status === 'ready') {
        const { data } = await requestJson(`/api/travel-guidance?city_id=${encodeURIComponent(owner.cityId)}`);
        if (!generationActive(owner)) return;
        load({...data.guidance, version:job.request.guidance_version});
        editor.planVersion = job.request.plan_version;
        $("guidance-notices").value = (job.result.notices || []).join('\n');
        $("guidance-city").textContent = `${data.guidance.city_name} · AI 已按保存的行程生成，请核对后保存`;
        stopGeneration();
        $("guidance-dialog").showModal();
        return;
      }
      if (job.status === 'failed') { owner.job = null; owner.requestId = requestId(); throw new Error(job.error || '生成失败，请重试。'); }
      if (!['queued','running'].includes(job.status)) throw new Error('生成状态无法识别，请重试查询。');
      $("guidance-generate-status").textContent = job.status === 'queued' ? '已排队，稍后自动生成…' : 'AI 正在结合每日行程生成提示…';
      timer = setTimeout(() => generate(), 1800);
    } catch (error) {
      if (!generationActive(owner)) return;
      $("guidance-generate-status").textContent = error.message;
      if (error.status && error.status < 500) { owner.job = null; owner.requestId = requestId(); }
      $("guidance-generate-confirm").textContent = owner.job ? '重试查询' : '已规划完毕，重试生成';
      $("guidance-generate-confirm").disabled = false;
    } finally {
      if (generationActive(owner)) owner.pending = false;
    }
  }

  function init() {
    if (initialized) return;
    initialized = true;
    $("guidance-generate").addEventListener("click", beginGeneration);
    $("guidance-generate-confirm").addEventListener("click", generate);
    $("guidance-generate-cancel").addEventListener("click", stopGeneration);
    $("guidance-confirm-dialog").addEventListener("cancel", event => { event.preventDefault(); stopGeneration(); });
    $("guidance-add").addEventListener("click", () => open());
    $("guidance-close").addEventListener("click", () => { if (!busy) close(); });
    $("guidance-dialog").addEventListener("cancel", event => { event.preventDefault(); if (!busy) close(); });
    $("guidance-form").addEventListener("submit", event => { event.preventDefault(); return submit(); });
    $("guidance-delete").addEventListener("click", () => submit(true));
    $("guidance-reload").addEventListener("click", () => {
      if (conflict && !busy && window.confirm("载入最新版会替换当前编辑框中的草稿，继续吗？")) load(conflict);
    });
  }

  function render() {
    init();
    if (editor && editor.scope !== scope()) close();
    if (generator && generator.scope !== scope()) stopGeneration();
    const group = (state.travelGuidance || []).find(group => group.city_id === state.cityId);
    const onPage = state.tab === "itinerary" && state.projectUnlocked && Boolean(state.userId);
    const visible = onPage && Boolean(group);
    $("guidance-tools").hidden = !onPage;
    $("travel-guidance").hidden = !visible;
    $("guidance-add").hidden = !onPage || Boolean(group);
    const next = JSON.stringify([scope(), visible, group]);
    if (next === signature) return;
    signature = next;
    const wrapper = $("travel-guidance-groups");
    wrapper.replaceChildren();
    if (!visible) return;
    const toolbar = element("div", "guidance-actions");
    toolbar.append(element("span", "guidance-source", group.manual ? "已保存提示" : "旧版行程附带提示"));
    const edit = element("button", "link-button", "编辑提示");
    edit.type = "button";
    edit.addEventListener("click", () => open(group));
    const remove = element("button", "link-button guidance-remove", "删除");
    remove.type = "button";
    remove.addEventListener("click", async () => { await open(group); if (editor?.group === group) await submit(true); });
    toolbar.append(edit, remove);
    wrapper.append(toolbar);
    if (group.notices?.length) {
      const list = element("ul", "travel-guidance-list");
      list.append(...group.notices.map(notice => element("li", "", notice)));
      wrapper.append(list);
    }
  }

  return { render };
})();
