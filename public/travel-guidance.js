"use strict";

window.TripGuidance = (() => {
  const $ = id => document.getElementById(id);
  const scope = () => JSON.stringify([state.projectId, state.cityId, state.userId, state.projectUnlocked]);
  let signature = "", editor = null, conflict = null, busy = false, initialized = false, generation = 0;

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

  function init() {
    if (initialized) return;
    initialized = true;
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
    const group = (state.travelGuidance || []).find(group => group.city_id === state.cityId);
    const onPage = state.tab === "itinerary" && state.projectUnlocked && Boolean(state.userId);
    const visible = onPage && Boolean(group);
    $("travel-guidance").hidden = !visible;
    $("guidance-add").hidden = !onPage || Boolean(group);
    const next = JSON.stringify([scope(), visible, group]);
    if (next === signature) return;
    signature = next;
    const wrapper = $("travel-guidance-groups");
    wrapper.replaceChildren();
    if (!visible) return;
    const toolbar = element("div", "guidance-actions");
    toolbar.append(element("span", "guidance-source", group.manual ? "已手动维护" : "最近一次 AI 规划"));
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
