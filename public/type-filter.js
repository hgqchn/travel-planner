"use strict";

window.TripTypeFilter = (() => {
  const $ = (id) => document.getElementById(id);
  let scope = "", selected = null, signature = "", tagSignature = "", itinerarySignature = "", initialized = false;
  let itineraryStatus = "all", scenicRating = "all", ratingSignature = "";
  let selectedTags = new Set(), mode = "any", search = "";
  const supported = () => ["attraction", "food"].includes(state.tab);
  const category = (item) => typeof item.category === "string" ? item.category.trim() : "";
  const tags = item => window.TripTaxonomy?.effectiveTags(item) || item.tags || [];
  const tagKey = tag => tag.toLocaleLowerCase();

  function syncScope() {
    const next = JSON.stringify([state.projectId, state.cityId, state.tab, state.userId, state.projectUnlocked]);
    if (scope !== next) {
      scope = next; selected = null; signature = ""; tagSignature = ""; itinerarySignature = ""; itineraryStatus = "all";
      selectedTags = new Set(); mode = "any"; search = "";
      scenicRating = "all"; ratingSignature = "";
      if ($("tag-filter-search")) $("tag-filter-search").value = "";
    }
  }

  function selection() { syncScope(); return selected; }
  function key() { syncScope(); return JSON.stringify([selected, [...selectedTags].sort(), mode, itineraryStatus, scenicRating]); }
  function active() { syncScope(); return selected !== null || selectedTags.size > 0 || itineraryStatus !== "all" || scenicRating !== "all"; }
  const scheduled = item => item.in_itinerary === true || (Array.isArray(item.itinerary_refs) && item.itinerary_refs.length > 0);
  const matchesItinerary = item => state.tab !== "attraction" || itineraryStatus === "all" || scheduled(item) === (itineraryStatus === "scheduled");
  const matchesRating = item => state.tab !== "attraction" || scenicRating === "all" || item.scenic_rating === scenicRating;

  function filter(items) {
    syncScope();
    if (!supported()) return items;
    return items.filter(item => {
      if (selected !== null && category(item) !== selected) return false;
      if (!matchesItinerary(item)) return false;
      if (!matchesRating(item)) return false;
      if (!selectedTags.size) return true;
      const values = new Set(tags(item).map(tagKey));
      return mode === "all" ? [...selectedTags].every(tag => values.has(tag)) : [...selectedTags].some(tag => values.has(tag));
    });
  }

  function choose(value) {
    if (!window.TripBatch.canNavigate()) return;
    selected = value;
    render();
    const key = JSON.stringify(value);
    for (const button of $("type-filter-options").querySelectorAll("button")) {
      if (button.dataset.filterKey === key) { button.focus({ preventScroll: true }); break; }
    }
  }

  function renderBar() {
    syncScope();
    const visible = supported() && state.projectUnlocked && Boolean(state.userId);
    $("type-filter").hidden = !visible;
    if (!visible) return;
    if (!initialized && $("tag-filter-search")) {
      initialized = true;
      $("tag-filter-search").addEventListener("input", () => { search = $("tag-filter-search").value.trim().toLocaleLowerCase(); renderBar(); });
      $("filter-reset").addEventListener("click", reset);
    }
    const items = (state.items[state.tab] || []).filter((item) => item.city_id === state.cityId);
    const counts = new Map();
    for (const item of items) counts.set(category(item), (counts.get(category(item)) || 0) + 1);
    // Keep the selected type visible when a collaborator moves its last item,
    // so the page clearly reports zero matches instead of changing the filter.
    if (selected !== null && !counts.has(selected)) counts.set(selected, 0);
    const groups = [...counts].sort(([a], [b]) => !a ? 1 : !b ? -1 : a.localeCompare(b, "zh-CN"));
    const next = JSON.stringify([scope, selected, items.length, groups]);
    if (signature !== next) {
      signature = next;
      const options = [[null, items.length], ...groups].map(([value, count]) => {
        const label = value === null ? "全部" : value || "未分类";
        const button = element("button", "type-filter-chip", `${label} ${count}`);
        button.type = "button";
        button.dataset.filterKey = JSON.stringify(value);
        button.setAttribute("aria-label", `筛选类型：${label}，${count}项`);
        button.setAttribute("aria-pressed", String(selected === value));
        button.addEventListener("click", () => choose(value));
        return button;
      });
      $("type-filter-options").replaceChildren(...options);
    }
    const shown = filter(items).length;
    $("type-filter-count").textContent = `显示 ${shown} / ${items.length} 项${state.tab === "food" ? "美食" : "地点"}`;
    renderTags(items);
    renderItineraryStatus(items);
    renderScenicRating(items);
  }

  function renderScenicRating(items) {
    const wrapper = $("scenic-rating-filter");
    if (!wrapper) return;
    wrapper.hidden = state.tab !== "attraction";
    if (wrapper.hidden) return;
    const options = [["all", "全部", items.length], ...["4A", "5A"].map(rating =>
      [rating, rating, items.filter(item => item.scenic_rating === rating).length])];
    const next = JSON.stringify([scope, scenicRating, options]);
    if (ratingSignature === next) return;
    ratingSignature = next;
    $("scenic-rating-filter-options").replaceChildren(...options.map(([value, label, count]) => {
      const button = element("button", "type-filter-chip", `${label} ${count}`);
      button.type = "button";
      button.dataset.scenicRating = value;
      button.setAttribute("aria-label", `筛选景区等级：${label}，${count}项`);
      button.setAttribute("aria-pressed", String(scenicRating === value));
      button.addEventListener("click", () => {
        if (!window.TripBatch.canNavigate()) return;
        scenicRating = value; render();
        for (const option of $("scenic-rating-filter-options").querySelectorAll("button")) if (option.dataset.scenicRating === value) option.focus({ preventScroll: true });
      });
      return button;
    }));
  }

  function renderItineraryStatus(items) {
    const wrapper = $("itinerary-filter");
    if (!wrapper) return;
    wrapper.hidden = state.tab !== "attraction";
    if (wrapper.hidden) return;
    const included = items.filter(scheduled).length;
    const next = JSON.stringify([scope, itineraryStatus, items.length, included]);
    if (itinerarySignature === next) return;
    itinerarySignature = next;
    $("itinerary-filter-options").replaceChildren(...[["all", "全部", items.length], ["scheduled", "已排行程", included], ["unscheduled", "未排行程", items.length - included]].map(([value, label, count]) => {
      const button = element("button", "type-filter-chip", `${label} ${count}`);
      button.type = "button";
      button.dataset.itineraryStatus = value;
      button.setAttribute("aria-label", `筛选行程状态：${label}，${count}项`);
      button.setAttribute("aria-pressed", String(itineraryStatus === value));
      button.addEventListener("click", () => {
        if (!window.TripBatch.canNavigate()) return;
        itineraryStatus = value; render();
        for (const option of $("itinerary-filter-options").querySelectorAll("button")) if (option.dataset.itineraryStatus === value) option.focus({ preventScroll: true });
      });
      return button;
    }));
  }

  function reset() {
    if (!window.TripBatch.canNavigate()) return;
    selected = null; selectedTags.clear(); mode = "any"; search = ""; itineraryStatus = "all";
    scenicRating = "all";
    $("tag-filter-search").value = "";
    render();
  }

  function renderTags(items) {
    if (!$("tag-filter-options")) return;
    $("tag-filter-hint").textContent = state.tab === "food" ? "可多选，包含菜系" : "可多选";
    const counts = new Map(), labels = new Map();
    for (const item of items) {
      for (const tag of tags(item)) if (!labels.has(tagKey(tag))) labels.set(tagKey(tag), tag);
      if (selected !== null && category(item) !== selected) continue;
      if (!matchesItinerary(item)) continue;
      if (!matchesRating(item)) continue;
      for (const tag of new Set(tags(item).map(tagKey))) counts.set(tag, (counts.get(tag) || 0) + 1);
    }
    for (const tag of selectedTags) if (!counts.has(tag)) counts.set(tag, 0);
    const groups = [...counts].filter(([tag]) => selectedTags.has(tag) || tag.includes(search))
      .sort(([a], [b]) => Number(selectedTags.has(b)) - Number(selectedTags.has(a)) || (labels.get(a) || a).localeCompare(labels.get(b) || b, "zh-CN"));
    const next = JSON.stringify([scope, selected, [...selectedTags], mode, groups, [...labels], search, itineraryStatus, scenicRating]);
    if (next !== tagSignature) {
      $("tag-filter-options").replaceChildren(...groups.map(([tag, count]) => {
        const button = element("button", "type-filter-chip", `${labels.get(tag) || tag} ${count}`);
        button.type = "button";
        button.dataset.tagKey = tag;
        button.setAttribute("aria-pressed", String(selectedTags.has(tag)));
        button.addEventListener("click", () => {
          if (!window.TripBatch.canNavigate()) return;
          if (selectedTags.has(tag)) selectedTags.delete(tag); else selectedTags.add(tag);
          render();
          for (const option of $("tag-filter-options").querySelectorAll("button")) if (option.dataset.tagKey === tag) option.focus({ preventScroll: true });
        });
        return button;
      }));
      $("tag-filter-mode").replaceChildren(...[["any", "符合任一标签"], ["all", "符合全部标签"]].map(([value, label]) => {
        const button = element("button", "tag-mode-button", label);
        button.type = "button";
        button.dataset.mode = value;
        button.setAttribute("aria-pressed", String(mode === value));
        button.addEventListener("click", () => {
          if (!window.TripBatch.canNavigate()) return;
          mode = value; render();
          for (const option of $("tag-filter-mode").querySelectorAll("button")) if (option.dataset.mode === value) option.focus({ preventScroll: true });
        });
        return button;
      }));
      tagSignature = next;
    }
    $("filter-reset").disabled = !active() && !search;
    $("tag-filter-empty").hidden = groups.length > 0;
    $("tag-filter-empty").textContent = search ? "没有匹配的标签，试试其他关键词。" : "暂无标签，可在编辑时添加或使用 AI 补充。";
  }

  function emptyState() {
    if (!supported() || !active()) return null;
    const wrapper = element("div", "empty-state");
    const copy = element("div");
    copy.append(element("strong", "", selectedTags.size || itineraryStatus !== "all" || scenicRating !== "all" ? "没有符合当前筛选条件的内容" : `“${selected || "未分类"}”暂无内容`));
    const button = element("button", "link-button", "清空筛选，查看全部");
    button.type = "button";
    button.addEventListener("click", reset);
    copy.append(button);
    wrapper.append(copy);
    return wrapper;
  }

  return { render: renderBar, filter, selection, key, active, emptyState };
})();
