"use strict";

window.TripCityEditor = (() => {
  const $ = id => document.getElementById(id);
  const scope = () => JSON.stringify([state.projectId, state.userId, state.projectUnlocked, state.cityId]);
  let editor = null, busy = false, deleting = false, generation = 0;

  function shiftedDates(dates, start) {
    const timestamp = value => {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new Error("请选择有效的开始日期。");
      const time = Date.parse(`${value}T00:00:00Z`);
      if (!Number.isFinite(time) || new Date(time).toISOString().slice(0, 10) !== value || value.startsWith("0000")) throw new Error("请选择有效的开始日期。");
      return time;
    };
    const offset = timestamp(start) - timestamp(dates[0]);
    return dates.map(date => {
      const shifted = new Date(timestamp(date) + offset).toISOString().split("T")[0];
      if (!/^\d{4}-\d{2}-\d{2}$/.test(shifted) || shifted.startsWith("0000")) throw new Error("调整后部分日期超出有效范围，请更换开始日期。");
      return shifted;
    });
  }

  function setBusy(value) {
    busy = value;
    for (const id of ["city-plan-start", "city-plan-close", "city-plan-remove", "city-plan-reload", "city-plan-delete", "city-plan-delete-cancel"]) $(id).disabled = value;
    $("city-plan-save").disabled = value || deleting;
    $("city-plan-save").textContent = value ? "正在处理…" : "保存日期";
  }

  function preview() {
    if (!editor) return;
    try {
      const dates = shiftedDates(editor.city.dates, $("city-plan-start").value);
      $("city-plan-preview").textContent = `调整后：${dates.join("、")}。每天的时间、安排与日期间隔保持不变。`;
    } catch (error) { $("city-plan-preview").textContent = error.message; }
  }

  function close(force = false) {
    if (busy && !force) return;
    if (!force && editor && $("city-plan-start").value !== editor.city.dates[0] && !window.confirm("日期调整尚未保存，确定关闭吗？")) return;
    editor = null;
    generation += 1;
    deleting = false;
    setBusy(false);
    $("city-plan-dialog").close();
  }

  function open(city) {
    if (!requireIdentity() || !window.TripBatch.canNavigate() || busy || !city?.version || !city.dates?.length) return;
    generation += 1;
    editor = { city, scope: scope() };
    deleting = false;
    setBusy(false);
    $("city-plan-title").textContent = `编辑城市 · ${city.city_name}`;
    $("city-plan-description").textContent = `${city.count} 项行程 · ${city.dates.length} 个出行日。修改开始日期后，该城市的所有行程将一起平移。`;
    $("city-plan-start").value = city.dates[0];
    $("city-plan-error").textContent = "";
    $("city-plan-reload").hidden = true;
    $("city-plan-delete-confirm").hidden = true;
    preview();
    if (!$("city-plan-dialog").open) $("city-plan-dialog").showModal();
  }

  function confirmDelete() {
    if (!editor || busy) return;
    deleting = true;
    $("city-plan-delete-description").textContent = `将从本项目移除${editor.city.city_name}的全部 ${editor.city.count} 项行程，城市会从已规划列表消失。地点和美食清单保留，之后仍可重新规划。删除的行程无法在页面恢复。`;
    $("city-plan-delete-confirm").hidden = false;
    setBusy(false);
    $("city-plan-delete-cancel").focus();
  }

  async function submit(remove = false) {
    if (!editor || busy || editor.scope !== scope() || (remove && !deleting)) return;
    if (!remove && deleting) return;
    const active = editor, request = generation;
    const payload = { city_id: active.city.city_id, version: active.city.version };
    if (remove) payload.confirm_delete = true;
    else {
      try { shiftedDates(active.city.dates, $("city-plan-start").value); }
      catch (error) { $("city-plan-error").textContent = error.message; return; }
      payload.start_date = $("city-plan-start").value;
    }
    $("city-plan-error").textContent = "";
    setBusy(true);
    try {
      await requestJson("/api/project-itinerary", { method: remove ? "DELETE" : "PUT", body: JSON.stringify(payload) });
      if (request !== generation || editor !== active || scope() !== active.scope) return;
      close(true);
      await fetchSnapshot(true);
      showToast(remove ? `已移除${active.city.city_name}的全部行程` : `已更新${active.city.city_name}的出行日期`);
    } catch (error) {
      if (request !== generation || editor !== active || scope() !== active.scope) return;
      $("city-plan-error").textContent = error.message || "暂时无法完成操作，请稍后重试。";
      $("city-plan-reload").hidden = ![404, 409].includes(error.status);
    } finally {
      if (request === generation && editor === active) setBusy(false);
    }
  }

  async function reload() {
    if (!editor || busy) return;
    if (!window.confirm("载入最新行程会放弃当前未保存的日期调整，继续吗？")) return;
    const active = editor, request = generation;
    setBusy(true);
    try {
      await fetchSnapshot(true);
      if (request !== generation || editor !== active || scope() !== active.scope) return;
      const city = state.projectItinerary?.cities.find(city => city.city_id === active.city.city_id);
      setBusy(false);
      if (city) open(city);
      else { close(true); showToast("该城市已没有行程。"); }
    } finally { if (request === generation && editor === active) setBusy(false); }
  }

  function sync() {
    if (editor && editor.scope !== scope()) close(true);
  }

  function init() {
    $("city-plan-form").addEventListener("submit", event => { event.preventDefault(); return submit(); });
    $("city-plan-start").addEventListener("input", preview);
    $("city-plan-close").addEventListener("click", () => close());
    $("city-plan-dialog").addEventListener("cancel", event => { event.preventDefault(); close(); });
    $("city-plan-remove").addEventListener("click", confirmDelete);
    $("city-plan-delete").addEventListener("click", () => submit(true));
    $("city-plan-delete-cancel").addEventListener("click", () => {
      deleting = false;
      $("city-plan-delete-confirm").hidden = true;
      setBusy(false);
    });
    $("city-plan-reload").addEventListener("click", reload);
  }
  return { init, open, sync };
})();
