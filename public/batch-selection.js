"use strict";

// Keep selected versions stable across polling so another person's edit cannot
// accidentally be deleted without the current user reviewing it first.
window.TripBatch = (() => {
  const $ = (id) => document.getElementById(id);
  const batch = { active: false, selected: new Map(), scope: "", epoch: 0, pending: null, deleting: false, conflict: false };
  const supported = () => ["attraction", "food"].includes(state.tab);
  const scope = () => JSON.stringify([state.projectId || "", state.cityId, state.tab, state.userId, window.TripTypeFilter?.key?.() ?? window.TripTypeFilter?.selection()]);
  const currentItems = () => {
    const items = (state.items[state.tab] || []).filter((item) => item.city_id === state.cityId);
    return window.TripTypeFilter?.filter(items) || items;
  };

  function reset() {
    batch.active = false;
    batch.selected.clear();
    batch.scope = "";
    batch.pending = null;
    batch.deleting = false;
    batch.conflict = false;
    batch.epoch += 1;
    if ($("batch-confirm-dialog").open) $("batch-confirm-dialog").close();
    $("batch-toolbar").hidden = true;
  }

  function canNavigate() {
    if (!batch.deleting) return true;
    showToast("正在删除所选内容，请稍候再切换。");
    return false;
  }

  function renderToolbar() {
    const nextScope = scope();
    if (nextScope !== batch.scope) {
      reset();
      batch.scope = nextScope;
    }
    const visible = supported() && state.projectUnlocked && Boolean(state.userId);
    const items = visible ? currentItems() : [];
    const ids = new Set(items.map((item) => item.id));
    for (const id of batch.selected.keys()) if (!ids.has(id)) batch.selected.delete(id);
    $("batch-open").hidden = !visible || batch.active || !items.length;
    $("batch-toolbar").hidden = !visible || !batch.active;
    $("batch-count").textContent = `已选 ${batch.selected.size} / ${items.length} 项${state.tab === "food" ? "美食" : "地点"}`;
    const allSelected = items.length > 0 && items.every((item) => batch.selected.has(item.id));
    $("batch-select-all").textContent = allSelected ? "取消全选" : (window.TripTypeFilter?.active?.() ?? window.TripTypeFilter?.selection() != null) ? "全选筛选结果" : "全选当前分类";
    $("batch-select-all").disabled = batch.deleting || !items.length;
    $("batch-cancel").disabled = batch.deleting;
    $("batch-delete").disabled = batch.deleting || !batch.selected.size;
    $("batch-delete").textContent = batch.deleting ? "正在删除…" : "删除所选";
  }

  function decorate(card, kind, item) {
    if (!batch.active || kind !== state.tab || item.city_id !== state.cityId) return card;
    card.classList.add("is-batch-selectable");
    card.classList.toggle("is-batch-selected", batch.selected.has(item.id));
    const label = element("label", "batch-item-choice");
    const checkbox = element("input");
    checkbox.type = "checkbox";
    checkbox.checked = batch.selected.has(item.id);
    checkbox.disabled = batch.deleting;
    checkbox.dataset.batchId = item.id;
    checkbox.setAttribute("aria-label", `选择${kind === "food" ? "美食" : "地点"}：${item.name}`);
    label.append(checkbox, element("span", "", checkbox.checked ? "已选择" : "选择此项"));
    checkbox.addEventListener("change", () => {
      if (batch.deleting) return;
      if (checkbox.checked) batch.selected.set(item.id, { id: item.id, version: item.version });
      else batch.selected.delete(item.id);
      card.classList.toggle("is-batch-selected", checkbox.checked);
      label.lastElementChild.textContent = checkbox.checked ? "已选择" : "选择此项";
      renderToolbar();
    });
    card.querySelector(".card-body").prepend(label);
    // Selection mode has one purpose; editing is available again after cancel.
    card.querySelector(".edit-card-button").hidden = true;
    return card;
  }

  function startSelection() {
    if (!supported() || !state.projectUnlocked || !requireIdentity()) return;
    batch.active = true;
    batch.scope = scope();
    render();
    $("cards").querySelector("[data-batch-id]")?.focus({ preventScroll: true });
  }

  function selectAll() {
    if (batch.deleting) return;
    const items = currentItems();
    if (items.every((item) => batch.selected.has(item.id))) batch.selected.clear();
    else for (const item of items) {
      if (!batch.selected.has(item.id)) batch.selected.set(item.id, { id: item.id, version: item.version });
    }
    render();
  }

  function askDelete() {
    if (batch.deleting || !batch.selected.size || !supported() || !requireIdentity()) return;
    batch.pending = { city_id: state.cityId, kind: state.tab, userId: state.userId, scope: scope(), items: Array.from(batch.selected.values(), (item) => ({ ...item })) };
    batch.conflict = false;
    const cityName = state.cities.find((city) => city.id === state.cityId)?.name || "当前城市";
    $("batch-confirm-message").textContent = `将从“${state.projectName}”中删除${cityName}的 ${batch.pending.items.length} 项${state.tab === "food" ? "美食" : "地点"}。`;
    $("batch-confirm-error").textContent = "";
    $("batch-confirm-refresh").hidden = true;
    setDeleting(false);
    $("batch-confirm-dialog").showModal();
    $("batch-confirm-cancel").focus();
  }

  function setDeleting(value) {
    batch.deleting = value;
    $("batch-confirm-cancel").disabled = value;
    $("batch-confirm-refresh").disabled = value;
    $("batch-confirm-delete").disabled = value || batch.conflict;
    $("batch-confirm-delete").textContent = value ? "正在删除…" : "确认删除";
    renderToolbar();
  }

  async function confirmDelete() {
    if (!batch.pending || batch.deleting || batch.conflict) return;
    const request = batch.pending;
    const epoch = batch.epoch;
    if (request.scope !== scope()) { reset(); render(); return; }
    setDeleting(true);
    $("batch-confirm-error").textContent = "";
    try {
      const { data } = await requestJson(`/api/items/${request.kind}/batch-delete`, {
        method: "POST", body: JSON.stringify({ city_id: request.city_id, items: request.items }),
      });
      if (epoch !== batch.epoch || request.scope !== scope()) return;
      reset();
      render();
      await fetchSnapshot(true, true);
      showToast(`已从当前项目删除 ${data.deleted_count ?? request.items.length} 项，其他项目和全局缓存保持不变。`);
    } catch (error) {
      if (epoch !== batch.epoch || request.scope !== scope()) return;
      if (error.data?.code === "PROJECT_LOCKED") {
        showProjectGate("项目访问已过期，请重新输入口令。", true);
      } else if (error.status === 401) {
        reset();
        render();
        openIdentity(true);
      } else {
        batch.conflict = true;
        $("batch-confirm-error").textContent = error.status === 409
          ? "所选内容已被修改或删除，本次没有删除任何内容。请刷新并核对，重新选择后再删除。"
          : `${error.message || "删除结果暂时无法确认。"} 请刷新并核对当前清单后再操作。`;
        $("batch-confirm-refresh").hidden = false;
      }
    } finally {
      if (epoch === batch.epoch && request.scope === scope()) setDeleting(false);
    }
  }

  function closeConfirm() {
    if (batch.deleting) return;
    batch.pending = null;
    $("batch-confirm-dialog").close();
  }

  async function refreshSelection() {
    if (batch.deleting) return;
    closeConfirm();
    batch.selected.clear();
    batch.conflict = false;
    render();
    await fetchSnapshot(true, true);
    showToast(state.previouslyOffline ? "暂时无法刷新，请恢复连接后再核对清单。" : "已刷新清单，请核对最新内容并重新选择。");
  }

  function init() {
    $("batch-open").addEventListener("click", startSelection);
    $("batch-cancel").addEventListener("click", () => { if (!canNavigate()) return; reset(); render(); });
    $("batch-select-all").addEventListener("click", selectAll);
    $("batch-delete").addEventListener("click", askDelete);
    $("batch-confirm-delete").addEventListener("click", confirmDelete);
    $("batch-confirm-cancel").addEventListener("click", closeConfirm);
    $("batch-confirm-refresh").addEventListener("click", refreshSelection);
    $("batch-confirm-dialog").addEventListener("cancel", (event) => {
      if (batch.deleting) event.preventDefault();
      else batch.pending = null;
    });
  }

  return { init, reset, canNavigate, renderToolbar, decorate };
})();
