"use strict";

// Separate controllers keep dialogs and drafts intact when the shared snapshot refreshes.
window.TripUI = (() => {
  const $ = (id) => document.getElementById(id);
  const ai = { config: null, job: null, cityId: "", cityName: "", timer: null, busy: false, importing: false, restoring: false, epoch: 0, pendingRequest: null, previewId: null, rows: [], formMode: "append", longRange: 0, replacementConflict: false };
  const map = { scale: 1, x: 0, y: 0, width: 0, height: 0, pointers: new Map(), previous: null, opener: null, tapStart: null, lastTap: null, suppressDoubleClickUntil: 0, record: null, cityId: "", failed: false };
  const metro = { cityId: "", epoch: 0, record: null, loading: false, submitting: false, error: "", timer: null, checkedAt: 0, view: null };
  let citySignature = "";
  let recent = [];
  try { recent = JSON.parse(localStorage.getItem(window.TripProject.storageKey("trip-recent-cities")) || "[]"); } catch { /* Storage is optional. */ }
  if (!Array.isArray(recent)) recent = [];

  function initialCity() { return typeof recent[0] === "string" && recent[0].length <= 100 ? recent[0] : "shanghai"; }

  function savedTask() {
    try {
      const pointer = JSON.parse(localStorage.getItem(window.TripProject.storageKey("trip-ai-current-task")) || "null");
      const sameProject = pointer?.projectId === state.projectId || (state.projectId === "main" && pointer?.projectId === undefined);
      return sameProject && pointer?.userId === state.userId && typeof pointer.jobId === "string" && /^[0-9a-f]{32}$/.test(pointer.jobId) ? pointer : null;
    } catch { return null; }
  }

  function rememberTask() {
    if (!ai.job?.id || !state.userId) return;
    try { localStorage.setItem(window.TripProject.storageKey("trip-ai-current-task"), JSON.stringify({ jobId: ai.job.id, userId: state.userId, projectId: state.projectId })); } catch { /* Optional recovery; no key or generated content is stored. */ }
    try {
      const all = taskHistory().filter((item) => item.jobId !== ai.job.id);
      all.unshift({ jobId: ai.job.id, userId: state.userId, cityName: ai.job.city_name || ai.cityName, status: ai.job.status });
      localStorage.setItem(taskHistoryKey(), JSON.stringify(all));
    } catch { /* Tasks continue when optional browser storage is unavailable. */ }
    renderTaskHistory();
  }

  function taskHistoryKey() { return window.TripProject.storageKey(`trip-ai-history:${state.userId}`); }
  function taskHistory() {
    try {
      const items = JSON.parse(localStorage.getItem(taskHistoryKey()) || "[]");
      return Array.isArray(items) ? items.filter((item) => item?.userId === state.userId && /^[0-9a-f]{32}$/.test(item.jobId)) : [];
    } catch { return []; }
  }
  function taskSwitchBlocked() { return Boolean(ai.pendingRequest) || ai.importing || ai.restoring || (ai.busy && !["queued", "running"].includes(ai.job?.status)); }
  function renderTaskHistory() {
    const select = $("ai-task-select");
    const labels = { queued: "排队中", running: "生成中", ready: "可查看", failed: "未完成", imported: "已导入" };
    select.replaceChildren();
    const blank = element("option", "", "新任务"); blank.value = ""; select.append(blank);
    for (const task of taskHistory()) {
      const option = element("option", "", `${task.cityName || "旅行计划"} · ${labels[task.status] || "查看任务"} · ${task.jobId.slice(-6)}`);
      option.value = task.jobId; select.append(option);
    }
    select.value = ai.job?.id || "";
    select.disabled = taskSwitchBlocked();
    $("ai-new-task").disabled = taskSwitchBlocked();
  }
  async function switchTask(jobId = "") {
    if (taskSwitchBlocked() || jobId === ai.job?.id) { renderTaskHistory(); return; }
    if (ai.job?.status === "ready" && !window.confirm("切换任务会放弃当前预览中尚未导入的手工编辑。原始生成结果仍可从任务记录打开，继续吗？")) { renderTaskHistory(); return; }
    if (ai.job) rememberTask();
    const oldJob = ai.job;
    const epoch = ++ai.epoch;
    clearTimeout(ai.timer);
    if (!jobId) {
      clearAiDraft();
      ai.formMode = "append"; ai.longRange = 0;
      setAiBusy(false);
      updateModeControls();
      return;
    }
    ai.restoring = true;
    updateModeControls();
    try {
      const { data } = await requestJson(`/api/ai/jobs/${encodeURIComponent(jobId)}`);
      if (epoch !== ai.epoch) return;
      ai.restoring = false;
      ai.job = unwrapJob(data);
      ai.previewId = null; ai.rows = []; ai.replacementConflict = false;
      $("ai-preview").hidden = true;
      $("ai-error").textContent = "";
      restoreRequestForm(ai.job.request || {});
      updateAiJob();
    } catch (error) {
      if (epoch !== ai.epoch) return;
      ai.restoring = false;
      ai.job = oldJob;
      if (oldJob) updateAiJob(); else setAiBusy(false);
      handleError(error, "ai-error");
    }
  }

  function forgetTask(jobId) {
    try { if (savedTask()?.jobId === jobId) localStorage.removeItem(window.TripProject.storageKey("trip-ai-current-task")); } catch { /* Storage is optional. */ }
  }

  async function restoreTask() {
    const pointer = savedTask();
    if (!pointer || ai.job || ai.pendingRequest || ai.restoring) return;
    const epoch = ai.epoch;
    ai.restoring = true;
    setAiBusy(true);
    $("ai-status").textContent = "正在恢复上次的旅行灵感…";
    try {
      const { data } = await requestJson(`/api/ai/jobs/${encodeURIComponent(pointer.jobId)}`);
      if (epoch !== ai.epoch || state.userId !== pointer.userId || ai.job) return;
      ai.job = unwrapJob(data);
      ai.restoring = false;
      restoreRequestForm(ai.job.request || {});
      $("ai-error").textContent = "";
      updateAiJob();
    } catch (error) {
      if (epoch !== ai.epoch || state.userId !== pointer.userId) return;
      setAiBusy(false);
      if ([403, 404].includes(error.status)) {
        forgetTask(pointer.jobId);
        $("ai-status").textContent = "上次任务已过期或不属于当前设备，可以开始新的计划。";
      } else {
        $("ai-status").textContent = "暂时无法恢复上次任务。关闭并重新打开助手可重试，任务记录仍然保留。";
        handleError(error, "ai-error");
      }
    } finally { if (epoch === ai.epoch) { ai.restoring = false; setAiBusy(["queued", "running"].includes(ai.job?.status)); } }
  }

  function authorized() {
    if (!state.projectUnlocked) { showProjectGate(); return false; }
    if (!state.userId) { openIdentity(false); return false; }
    return true;
  }

  function handleError(error, target) {
    if (error.data?.code === "PROJECT_LOCKED") showProjectGate("项目访问已过期，请重新输入口令。", true);
    else if (error.status === 401) { $(target).textContent = "请重新选择用户 ID 后再试。"; openIdentity(true); }
    else $(target).textContent = error.message || "操作没有完成，请稍后重试。";
  }

  function renderCity() {
    syncMetroContext();
    $("city-current-name").textContent = state.cities.find((city) => city.id === state.cityId)?.name || "选择城市";
    const signature = JSON.stringify(state.cities);
    if (signature !== citySignature) {
      citySignature = signature;
      // Preserve an active city search, including its focus and text selection.
      if ($("city-dialog").open) renderCityResults();
    }
  }

  function renderCacheNotice() {
    const omitted = state.cacheInfo?.omitted?.[state.tab];
    const visible = ["attraction", "food"].includes(state.tab) && Number.isFinite(omitted) && omitted > 0;
    $("cache-notice").hidden = !visible;
    $("cache-notice").textContent = visible ? `共享资料较多，当前项目已按容量上限载入，另有 ${omitted} 条仍保存在全局缓存中。` : "";
  }

  async function switchCity(cityId) {
    if (!window.TripBatch.canNavigate()) return;
    if (cityId === state.cityId && state.revision !== null) return;
    state.cityId = cityId;
    state.snapshotRequestId += 1;
    state.snapshotEtag = null;
    state.revision = null;
    state.items = { itinerary: [], attraction: [], transit: [], food: [] };
    state.cacheInfo = null;
    state.travelGuidance = [];
    state.activity = [];
    if (cityId) {
      recent = [cityId, ...recent.filter((id) => id !== cityId)].slice(0, 6);
      try { localStorage.setItem(window.TripProject.storageKey("trip-recent-cities"), JSON.stringify(recent)); } catch { /* Optional preference. */ }
    }
    render();
    if (cityId) await fetchSnapshot(true);
  }

  function normalizeSearch(value) { return String(value || "").normalize("NFKC").toLowerCase().replace(/[\s'’·-]/g, ""); }

  function cityButton(city) {
    const button = element("button", `city-choice${city.id === state.cityId ? " is-current" : ""}`);
    button.type = "button";
    button.append(element("span", "", city.name));
    if (city.id === state.cityId) {
      button.append(element("small", "", "当前"));
      button.setAttribute("aria-current", "true");
    }
    button.addEventListener("click", async () => {
      $("city-dialog").close();
      await switchCity(city.id);
    });
    return button;
  }

  function cityGroup(title, cities) {
    const group = element("section", "city-group");
    group.append(element("h3", "", title));
    const choices = element("div", "city-choice-list");
    choices.append(...cities.map(cityButton));
    group.append(choices);
    return group;
  }

  function renderCityResults() {
    const query = normalizeSearch($("city-search").value);
    const results = state.cities.filter((city) => !query || [city.name, city.province, city.pinyin, city.initials, ...(city.aliases || [])].some((value) => normalizeSearch(value).includes(query)));
    const content = [];
    if (!query) {
      const recentCities = recent.map((id) => state.cities.find((city) => city.id === id)).filter(Boolean);
      if (recentCities.length) content.push(cityGroup("最近访问", recentCities));
    }
    const groups = new Map();
    for (const city of results) {
      const province = city.province || "自定义城市";
      if (!groups.has(province)) groups.set(province, []);
      groups.get(province).push(city);
    }
    for (const [province, cities] of groups) content.push(cityGroup(province, cities));
    if (!results.length) content.push(element("p", "city-empty", "没有匹配的城市。可在下方手动添加，和同行者一起完善它。"));
    $("city-result-count").textContent = query ? `找到 ${results.length} 个城市` : `${results.length} 个城市，和你一起出发`;
    $("city-results").replaceChildren(...content);
  }

  async function addCity(event) {
    event.preventDefault();
    if (!authorized()) return;
    const name = $("city-add-input").value.trim();
    if (!name) return;
    const submit = event.submitter || $("city-add-form").querySelector("button");
    submit.disabled = true;
    $("city-add-error").textContent = "";
    try {
      const { data } = await requestJson("/api/cities", { method: "POST", body: JSON.stringify({ name }) });
      if (!state.cities.some((city) => city.id === data.city.id)) state.cities.push(data.city);
      $("city-add-input").value = "";
      $("city-dialog").close();
      await switchCity(data.city.id);
      showToast(`已添加 ${data.city.name}，开始安排旅行吧`);
    } catch (error) {
      if (error.status === 409 && error.data?.city) {
        const city = error.data.city;
        if (!state.cities.some((entry) => entry.id === city.id)) state.cities.push(city);
        $("city-dialog").close();
        await switchCity(city.id);
        showToast(`${city.name} 已在项目中，已为你切换`);
      } else handleError(error, "city-add-error");
    } finally { submit.disabled = false; }
  }

  function cityMapUrl(city) {
    return window.TripLinks.amapCity(city);
  }

  function syncMetroContext() {
    if (metro.cityId !== state.cityId) {
      clearTimeout(metro.timer);
      metro.epoch += 1;
      metro.cityId = state.cityId;
      metro.record = null; metro.view = null; metro.error = "";
      metro.loading = false; metro.submitting = false; metro.checkedAt = 0; metro.timer = null;
      if ($("metro-dialog").open) $("metro-dialog").close();
    }
    if (state.tab !== "transit") { clearTimeout(metro.timer); metro.timer = null; }
  }

  function metroPending() { return metro.submitting || ["queued", "running"].includes(metro.record?.update?.status); }
  function metroDate(value) {
    if (!value) return "暂无记录";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "暂无记录" : date.toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
  }

  function scheduleMetroPoll() {
    clearTimeout(metro.timer);
    metro.timer = null;
    if (state.tab !== "transit" || state.cityId !== metro.cityId || !metroPending() || metro.error) return;
    metro.timer = setTimeout(() => { metro.timer = null; loadMetro(); }, 2000);
  }

  async function loadMetro() {
    if (!metro.cityId || metro.loading || metro.submitting) return;
    const epoch = metro.epoch, cityId = metro.cityId;
    metro.loading = true; metro.error = "";
    renderMetroDetails();
    try {
      const { data } = await requestJson(`/api/metro-map?city_id=${encodeURIComponent(cityId)}`);
      if (epoch !== metro.epoch || cityId !== state.cityId) return;
      metro.record = data;
      metro.checkedAt = Date.now();
    } catch (error) {
      if (epoch !== metro.epoch || cityId !== state.cityId) return;
      metro.error = error.message || "线路图状态加载失败，请重试。";
    } finally {
      if (epoch === metro.epoch && cityId === state.cityId) {
        metro.loading = false;
        renderMetroDetails();
        scheduleMetroPoll();
      }
    }
  }

  async function updateMetro() {
    if (!authorized() || !metro.record?.supported || metroPending() || metro.loading) return;
    const epoch = metro.epoch, cityId = metro.cityId;
    clearTimeout(metro.timer);
    metro.submitting = true; metro.error = "";
    renderMetroDetails();
    try {
      const { data } = await requestJson("/api/metro-map/update", { method: "POST", body: JSON.stringify({ city_id: cityId }) });
      if (epoch !== metro.epoch || cityId !== state.cityId) return;
      metro.record = data;
      metro.checkedAt = Date.now();
    } catch (error) {
      if (epoch !== metro.epoch || cityId !== state.cityId) return;
      metro.error = error.message || "更新请求未完成。可重新加载状态，确认后再试。";
      if (error.data?.code === "PROJECT_LOCKED") showProjectGate("项目访问已过期，请重新输入口令。", true);
      else if (error.status === 401) openIdentity(true);
    } finally {
      if (epoch === metro.epoch && cityId === state.cityId) {
        metro.submitting = false;
        renderMetroDetails();
        scheduleMetroPoll();
      }
    }
  }

  function renderMetroDetails() {
    const view = metro.view;
    if (!view || view.city.id !== state.cityId || state.tab !== "transit" || dom.cards.firstElementChild !== view.panel) return;
    const record = metro.record, asset = record?.available ? record.map : null;
    const pending = metroPending();
    view.badge.hidden = !asset;
    view.badge.textContent = asset?.format?.toLowerCase() === "svg" ? "高清矢量图" : "高清线路图";
    view.preview.hidden = !asset;
    view.help.hidden = !asset;
    view.credit.hidden = !asset;
    view.empty.hidden = Boolean(asset);
    if (asset) {
      if (view.img.getAttribute("src") !== asset.image_url) {
        view.img.hidden = false;
        view.imageMessage.hidden = false;
        view.imageMessage.textContent = "正在加载线路图预览…";
        view.img.src = asset.image_url;
      }
      view.img.alt = `${view.city.name}轨道交通线路图，点击在页面内放大`;
      view.credit.replaceChildren();
      const attribution = element("p", "map-credit", `作者：${asset.author || "详见来源页"}。`);
      const sourceLinks = element("div", "metro-source-links");
      if (asset.source_url) sourceLinks.append(externalAnchor(`${asset.source_name || "Wikimedia Commons"} · 图片来源 ↗`, asset.source_url));
      if (asset.license_url) sourceLinks.append(externalAnchor(asset.license || "查看使用许可", asset.license_url));
      else if (asset.license) sourceLinks.append(element("span", "muted", asset.license));
      if (asset.official_url) sourceLinks.append(externalAnchor(`${view.city.name}轨道交通官方入口 ↗`, asset.official_url));
      view.credit.append(attribution, sourceLinks,
        element("p", "metro-file-dates", `源文件修订：${metroDate(asset.source_updated_at)} · 最后检查：${metroDate(asset.checked_at)} · 本地下载：${metroDate(asset.downloaded_at)}`),
        element("p", "muted", "线路图由 Wikimedia Commons 社区维护。源文件修订时间不代表运营更新日期，出行前请核对官方公告。"));
      if ($("metro-dialog").open && map.cityId === metro.cityId && map.record?.image_url !== asset.image_url) {
        $("metro-help").textContent = "线路图已更新，关闭后重新打开可查看新图。当前图仍可缩放。";
      }
    } else {
      view.emptyText.textContent = !record ? metro.loading ? "正在查询这个城市的线路图…" : "暂时无法载入线路图信息，上方城市地图仍可使用。"
        : record.supported ? "已收录这个城市的线路图，点击下方按钮下载高清原图。"
          : "尚未收录该城市线路图。可通过上方城市地图查询公交、地铁和实时路线。";
    }
    view.update.hidden = !record?.supported;
    view.update.disabled = pending || metro.loading;
    view.update.textContent = pending ? "正在检查并下载…" : asset ? "检查并更新线路图" : "下载高清线路图";
    view.retry.hidden = !metro.error;
    view.retry.disabled = metro.loading || metro.submitting;
    view.retry.textContent = metro.loading ? "正在重新加载…" : "重新加载状态";
    const complete = record?.update?.status === "ready" ? "线路图检查完成，已保存图源的当前版本。 " : "";
    view.status.textContent = metro.error || record?.update?.error || (pending ? "正在从来源站检查更新，已有线路图可继续使用。" : complete + (record?.message || ""));
    view.status.classList.toggle("is-error", Boolean(metro.error || record?.update?.status === "failed"));
    view.status.hidden = !view.status.textContent;
  }

  function renderTransport() {
    syncMetroContext();
    const city = state.cities.find((entry) => entry.id === state.cityId);
    const signature = city ? JSON.stringify([city.id, city.name, city.map_query, city.center]) : "";
    dom.cards.setAttribute("aria-busy", "false");
    if (!city) { dom.cards.replaceChildren(); return; }
    if (!metro.view || dom.cards.firstElementChild !== metro.view.panel || dom.cards.firstElementChild?.dataset.transportCity !== signature) {
      const panel = element("section", "transport-panel");
      panel.dataset.transportCity = signature;
      const links = element("div", "map-actions");
      links.append(appExternalAnchor(`高德 · ${city.name}地图 ↗`, cityMapUrl(city)),
        externalAnchor(`百度 · ${city.name}地图 ↗`, `https://api.map.baidu.com/place/search?query=${encodeURIComponent(city.map_query || city.name)}&region=${encodeURIComponent(city.name)}&output=html&src=webapp.travel.planner`));
      const heading = element("div", "metro-preview-heading");
      const badge = element("span", "tag is-accent");
      heading.append(element("h3", "", `${city.name}轨道交通线路图`), badge);
      const preview = element("button", "metro-preview");
      preview.type = "button";
      preview.setAttribute("aria-label", `在页面内放大${city.name}轨道交通线路图`);
      const img = element("img", "metro-image");
      img.loading = "lazy";
      const imageMessage = element("span", "metro-preview-message");
      img.addEventListener("load", () => { imageMessage.hidden = true; });
      img.addEventListener("error", () => { img.hidden = true; imageMessage.hidden = false; imageMessage.textContent = "预览加载失败，点击重试加载高清图。"; });
      preview.append(img, imageMessage, element("span", "metro-open-label", "⊕ 点击放大 · 支持拖动查看"));
      preview.addEventListener("click", () => { if (metro.record?.available && metro.cityId === city.id) openMap(preview, metro.record.map, city); });
      const help = element("p", "muted", "点击图内查看高清站名，支持滚轮、双指、双击和按钮缩放。");
      const credit = element("div", "metro-credit");
      const empty = element("div", "transport-empty");
      const emptyText = element("p");
      empty.append(element("span", "", "⇆"), element("h3", "", `探索${city.name}的公共交通`), emptyText);
      const actions = element("div", "metro-update-actions");
      const update = element("button", "secondary-button metro-update-button");
      update.type = "button";
      update.addEventListener("click", updateMetro);
      const retry = element("button", "secondary-button");
      retry.type = "button";
      retry.addEventListener("click", loadMetro);
      actions.append(update, retry);
      const status = element("p", "metro-update-status");
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      panel.append(links, heading, preview, empty, help, actions, status, credit);
      metro.view = { city, panel, badge, preview, img, imageMessage, help, credit, empty, emptyText, update, retry, status };
      dom.cards.replaceChildren(panel);
      renderMetroDetails();
    }
    // Snapshot refreshes must not replace the image or reset an open zoom dialog.
    if (!metro.error && (!metro.record || Date.now() - metro.checkedAt >= 30000)) loadMetro();
    else if (metroPending() && !metro.timer) scheduleMetroPoll();
  }

  function clampMap() {
    const stage = $("metro-stage");
    const maxX = Math.max(0, (map.width * map.scale - stage.clientWidth) / 2);
    const maxY = Math.max(0, (map.height * map.scale - stage.clientHeight) / 2);
    map.x = Math.max(-maxX, Math.min(maxX, map.x));
    map.y = Math.max(-maxY, Math.min(maxY, map.y));
  }

  function paintMap() {
    clampMap();
    const img = $("metro-full-image");
    // Resize the SVG itself so browsers repaint vector labels at the visible scale.
    img.style.width = `${map.width * map.scale}px`;
    img.style.height = `${map.height * map.scale}px`;
    img.style.transform = `translate(calc(-50% + ${map.x}px), calc(-50% + ${map.y}px))`;
    $("metro-scale").textContent = `${Math.round(map.scale * 100)}%`;
    $("metro-minus").disabled = map.scale <= 1;
    $("metro-plus").disabled = map.scale >= 32;
  }

  function fitMap() {
    const stage = $("metro-stage");
    const img = $("metro-full-image");
    const ratio = (map.record?.width && map.record?.height) ? map.record.width / map.record.height : (img.naturalWidth || 3) / (img.naturalHeight || 2);
    map.width = Math.min(stage.clientWidth, stage.clientHeight * ratio);
    map.height = map.width / ratio;
    map.scale = 1;
    map.x = map.y = 0;
    paintMap();
  }

  function zoomMap(factor, x = 0, y = 0) {
    const before = map.scale;
    map.scale = Math.max(1, Math.min(32, before * factor));
    const ratio = map.scale / before;
    map.x = x - (x - map.x) * ratio;
    map.y = y - (y - map.y) * ratio;
    paintMap();
  }

  function pointerPosition(event) {
    const rect = $("metro-stage").getBoundingClientRect();
    return { x: event.clientX - rect.left - rect.width / 2, y: event.clientY - rect.top - rect.height / 2 };
  }

  function gesture() {
    const points = [...map.pointers.values()];
    if (!points.length) return null;
    if (points.length === 1) return { ...points[0], distance: 0 };
    return { x: (points[0].x + points[1].x) / 2, y: (points[0].y + points[1].y) / 2, distance: Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y) };
  }

  function openMap(opener, record, city) {
    if (!record?.image_url) return;
    map.opener = opener;
    map.record = { ...record };
    map.cityId = city.id;
    $("metro-title").textContent = `${city.name}轨道交通线路图`;
    $("metro-help").textContent = "滚轮或双指缩放 · 拖动查看 · 双击放大";
    $("metro-dialog").showModal();
    const img = $("metro-full-image");
    img.alt = `${city.name}轨道交通高清线路图`;
    if (img.getAttribute("src") !== record.image_url || map.failed) {
      map.failed = false;
      img.hidden = true;
      $("metro-loading").hidden = false;
      $("metro-loading").textContent = "正在加载高清线路图…";
      img.src = record.image_url;
    }
    requestAnimationFrame(() => { fitMap(); $("metro-stage").focus(); });
  }

  function setupMap() {
    const stage = $("metro-stage");
    $("metro-close").addEventListener("click", () => $("metro-dialog").close());
    $("metro-dialog").addEventListener("close", () => {
      map.pointers.clear(); map.previous = null; map.tapStart = null; map.lastTap = null;
      map.opener?.focus({ preventScroll: true });
    });
    $("metro-full-image").addEventListener("load", () => { map.failed = false; $("metro-full-image").hidden = false; $("metro-loading").hidden = true; fitMap(); });
    $("metro-full-image").addEventListener("error", () => { map.failed = true; $("metro-full-image").hidden = true; $("metro-loading").hidden = false; $("metro-loading").textContent = "高清图加载失败，请关闭后重新打开重试。"; });
    $("metro-plus").addEventListener("click", () => zoomMap(1.5));
    $("metro-minus").addEventListener("click", () => zoomMap(1 / 1.5));
    $("metro-reset").addEventListener("click", fitMap);
    stage.addEventListener("wheel", (event) => { event.preventDefault(); const p = pointerPosition(event); zoomMap(Math.exp(-Math.max(-100, Math.min(100, event.deltaY)) * .008), p.x, p.y); }, { passive: false });
    stage.addEventListener("dblclick", (event) => {
      if (Date.now() < map.suppressDoubleClickUntil) return;
      const p = pointerPosition(event); zoomMap(map.scale >= 32 ? 1 / 32 : 2, p.x, p.y);
    });
    stage.addEventListener("pointerdown", (event) => {
      if (event.pointerType === "mouse" && event.button !== 0) return;
      stage.setPointerCapture(event.pointerId);
      map.pointers.set(event.pointerId, pointerPosition(event));
      map.tapStart = event.pointerType === "touch" && map.pointers.size === 1 ? { ...pointerPosition(event), id: event.pointerId, at: Date.now() } : null;
      if (map.pointers.size > 1) map.lastTap = null;
      map.previous = gesture();
      stage.classList.add("is-dragging");
    });
    stage.addEventListener("pointermove", (event) => {
      if (!map.pointers.has(event.pointerId)) return;
      map.pointers.set(event.pointerId, pointerPosition(event));
      if (map.tapStart && Math.hypot(pointerPosition(event).x - map.tapStart.x, pointerPosition(event).y - map.tapStart.y) > 10) map.tapStart = null;
      const next = gesture();
      if (next && map.previous) {
        if (next.distance && map.previous.distance) zoomMap(next.distance / map.previous.distance, map.previous.x, map.previous.y);
        map.x += next.x - map.previous.x;
        map.y += next.y - map.previous.y;
        paintMap();
      }
      map.previous = next;
    });
    for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) stage.addEventListener(type, (event) => {
      if (type === "pointerup" && map.tapStart?.id === event.pointerId && Date.now() - map.tapStart.at < 300) {
        const tap = { ...pointerPosition(event), at: Date.now() };
        if (map.lastTap && tap.at - map.lastTap.at < 350 && Math.hypot(tap.x - map.lastTap.x, tap.y - map.lastTap.y) < 30) {
          zoomMap(map.scale >= 32 ? 1 / 32 : 2, tap.x, tap.y);
          map.suppressDoubleClickUntil = Date.now() + 500;
          map.lastTap = null;
        } else map.lastTap = tap;
      }
      if (map.tapStart?.id === event.pointerId) map.tapStart = null;
      map.pointers.delete(event.pointerId); map.previous = gesture();
      if (!map.pointers.size) stage.classList.remove("is-dragging");
    });
    stage.addEventListener("keydown", (event) => {
      const moves = { ArrowLeft: [60, 0], ArrowRight: [-60, 0], ArrowUp: [0, 60], ArrowDown: [0, -60] };
      if (["+", "=", "-", "0", ...Object.keys(moves)].includes(event.key)) event.preventDefault();
      if (["+", "="].includes(event.key)) zoomMap(1.5);
      else if (event.key === "-") zoomMap(1 / 1.5);
      else if (event.key === "0") fitMap();
      else if (moves[event.key]) { map.x += moves[event.key][0]; map.y += moves[event.key][1]; paintMap(); }
    });
    new ResizeObserver(() => { if ($("metro-dialog").open) fitMap(); }).observe(stage);
  }

  function localDate() { const now = new Date(); return new Date(now - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10); }
  function unwrapJob(data) { return data?.job || data; }
  function busyJob() { return ai.busy || ai.importing || ai.restoring || ["queued", "running"].includes(ai.job?.status); }
  function formInput(name) { return $("ai-form").elements.namedItem(name); }
  function planningMode(request = ai.job?.request) { return ["replace_all", "replace_day"].includes(request?.planning_mode) ? request.planning_mode : "append"; }
  function requestDateRange(request) {
    const start = new Date(`${request.start_date}T00:00:00Z`);
    if (Number.isNaN(start.getTime())) return null;
    if (start.toISOString().slice(0, 10) !== request.start_date) return null;
    const days = Number(request.days);
    if (!Number.isSafeInteger(days) || days < 1) return null;
    const end = new Date(start.getTime() + (days - 1) * 86400000);
    if (Number.isNaN(end.getTime()) || end.getUTCFullYear() > 9999) return null;
    return { start: start.toISOString().slice(0, 10), end: end.toISOString().slice(0, 10), days };
  }

  function replacementLabel(request) {
    return planningMode(request) === "replace_day" ? `${request.target_date || request.start_date} 行程` : "全部行程";
  }

  function updateModeControls() {
    renderTaskHistory();
    const mode = ai.formMode;
    const replan = mode !== "append";
    const day = mode === "replace_day";
    const locked = busyJob() || Boolean(ai.pendingRequest);
    $("ai-form").querySelectorAll("input, textarea, select, button").forEach((input) => { input.disabled = locked; });
    formInput("planning_mode").value = mode;
    $("ai-target-date-field").hidden = !day;
    formInput("target_date").required = day;
    formInput("target_date").disabled = locked || !day;
    formInput("start_date").disabled = locked || day;
    formInput("days").disabled = locked || day;
    if (day) {
      formInput("start_date").value = formInput("target_date").value;
      formInput("days").value = "1";
    }
    $("ai-form").querySelectorAll('[name="kinds"]').forEach((input) => {
      input.disabled = locked || replan;
      if (replan) input.checked = input.value === "itinerary";
    });
    document.querySelectorAll('[data-ai-template="food"]').forEach((button) => { button.disabled = locked || replan; });
    $("ai-replan-scope").hidden = !replan;
    const scope = day ? `${formInput("target_date").value || "所选日期"} 当天行程` : "全部行程（包括新日期范围之外的旧行程）";
    $("ai-replan-scope").textContent = `${ai.cityName || "当前城市"}的${scope}将被新方案替换。确认应用前原行程保留，地点、美食与其他城市的行程保留。`;
    $("ai-replan-range-note").hidden = mode !== "replace_all" || !ai.longRange;
    $("ai-replan-range-note").textContent = ai.longRange ? `已有行程跨度为 ${ai.longRange} 天，将按所选日期和天数重新规划。` : "";
    $("ai-model-input").disabled = locked || !ai.config?.enabled;
    $("ai-generate").disabled = !ai.config?.enabled || busyJob() || !formInput("model").value.trim();
    if (!busyJob()) $("ai-generate").textContent = ai.pendingRequest ? "重试本次请求" : replan ? "✧ 生成新的行程方案" : "✧ 生成旅行灵感";
    $("ai-target-change").hidden = ai.cityId === state.cityId || locked;
    updateDateRequired();
  }

  function restoreRequestForm(request) {
    if (request.model) formInput("model").value = request.model;
    ai.formMode = planningMode(request);
    ai.longRange = 0;
    for (const name of ["start_date", "target_date", "days", "people", "budget", "pace", "preferences", "requirements"]) {
      const input = formInput(name);
      if (input && request[name] !== undefined && request[name] !== null) input.value = request[name];
    }
    if (ai.formMode === "replace_day") formInput("target_date").value = request.target_date || request.start_date;
    if (Array.isArray(request.kinds)) $("ai-form").querySelectorAll('[name="kinds"]').forEach((input) => { input.checked = request.kinds.includes(input.value); });
    updateModeControls();
  }

  function clearAiDraft() {
    if (ai.job?.id) forgetTask(ai.job.id);
    clearTimeout(ai.timer);
    ai.job = null; ai.previewId = null; ai.rows = []; ai.replacementConflict = false;
    ai.cityId = state.cityId;
    ai.cityName = state.cities.find((city) => city.id === state.cityId)?.name || "";
    $("ai-preview").hidden = true;
    $("ai-preview-items").replaceChildren();
    $("ai-status").textContent = $("ai-error").textContent = "";
    $("ai-open").classList.remove("has-result");
    $("ai-city-name").textContent = ai.cityName;
  }

  function choosePlanningMode(mode, targetDate = "", forceCurrentCity = false) {
    if (busyJob() || ai.pendingRequest || (savedTask() && !ai.job)) {
      formInput("planning_mode").value = ai.formMode;
      $("ai-status").textContent = ai.pendingRequest ? "请先重试本次请求，确认已有任务的状态后再新建计划。" : "请先完成或恢复当前任务，再重新规划行程。";
      return false;
    }
    const sameScope = ai.formMode === mode && (!forceCurrentCity || ai.cityId === state.cityId) && (mode !== "replace_day" || !targetDate || targetDate === formInput("target_date").value);
    if (sameScope && ai.job?.status !== "imported") { updateModeControls(); return true; }
    if (ai.job?.status === "ready" && !window.confirm("当前 AI 预览尚未应用。切换规划方式会清除这份预览，原行程保留，继续吗？")) {
      formInput("planning_mode").value = ai.formMode;
      return false;
    }
    clearAiDraft();
    ai.formMode = mode;
    ai.longRange = 0;
    if (mode === "replace_day") formInput("target_date").value = targetDate || formInput("target_date").value || localDate();
    if (mode === "replace_all") {
      const dates = (state.items.itinerary || []).filter((item) => item.city_id === state.cityId && /^\d{4}-\d{2}-\d{2}$/.test(item.date)).map((item) => item.date).sort();
      if (dates.length) {
        const span = Math.round((new Date(`${dates.at(-1)}T00:00:00Z`) - new Date(`${dates[0]}T00:00:00Z`)) / 86400000) + 1;
        ai.longRange = 0;
        formInput("start_date").value = dates[0];
        formInput("days").value = String(span);
      } else {
        formInput("start_date").value = localDate();
        formInput("days").value = "3";
      }
    }
    if (mode === "append") {
      $("ai-form").querySelectorAll('[name="kinds"]').forEach((input) => { input.checked = true; });
      if (!formInput("start_date").value) formInput("start_date").value = localDate();
      if (!formInput("days").value) formInput("days").value = "3";
    }
    updateModeControls();
    return true;
  }

  async function openAi(replan = null) {
    if (!authorized()) return;
    if (!ai.job && !ai.pendingRequest) {
      ai.cityId = state.cityId;
      ai.cityName = state.cities.find((city) => city.id === state.cityId)?.name || "";
    }
    $("ai-city-name").textContent = ai.cityName;
    $("ai-target-change").hidden = ai.cityId === state.cityId || busyJob();
    $("ai-target-change").textContent = `为${state.cities.find((city) => city.id === state.cityId)?.name || "当前城市"}新建计划`;
    if (!$("ai-dialog").open) $("ai-dialog").showModal();
    if (!$("ai-start-date").value && !ai.longRange) $("ai-start-date").value = localDate();
    $("ai-generate").disabled = true;
    const epoch = ai.epoch;
    try {
      const { data } = await requestJson("/api/ai/config");
      if (epoch !== ai.epoch) return;
      ai.config = data;
      $("ai-model").textContent = ai.job?.model ? ` · 本次结果：${ai.job.model}` : "";
      if (!formInput("model").value && data.model) formInput("model").value = data.model;
      if (!data.enabled) $("ai-error").textContent = "AI 服务尚未配置，请联系项目管理员。你仍可手动添加旅行内容。";
      $("ai-generate").disabled = !data.enabled || busyJob();
    } catch (error) { if (epoch === ai.epoch) handleError(error, "ai-error"); }
    if (epoch !== ai.epoch) return;
    await restoreTask();
    if (epoch !== ai.epoch) return;
    if (replan) choosePlanningMode(replan.planning_mode, replan.target_date, true);
    updateModeControls();
    if (["queued", "running"].includes(ai.job?.status)) schedulePoll(0);
  }

  function openReplan(mode, date = "") { return openAi({ planning_mode: mode, target_date: date }); }

  function useTemplate(template) {
    if (busyJob() || ai.pendingRequest || (template === "food" && ai.formMode !== "append")) return;
    const form = $("ai-form");
    const set = (name, value) => { form.elements.namedItem(name).value = value; };
    form.querySelectorAll('[name="kinds"]').forEach((input) => { input.checked = template !== "food" || input.value === "food"; });
    if (template === "classic") { set("days", 3); set("pace", "balanced"); set("preferences", "经典地标、当地美食、城市漫步"); }
    if (template === "family") { set("days", 3); set("pace", "relaxed"); set("preferences", "亲子友好、公园、博物馆"); set("requirements", "带孩子出行，减少往返和长距离步行，安排充足休息。"); }
    if (template === "food") { set("preferences", "当地特色、街头小吃、本地人日常饮食"); set("requirements", "介绍值得尝试的本地美食、推荐区域和点单提醒。"); }
    if (template === "extra") { set("preferences", "小众体验、街区漫步、文化空间"); set("requirements", "给我一些经典路线之外的新灵感。请填写需要避开的已去地点。"); }
    updateModeControls();
  }

  function updateDateRequired() {
    const kinds = [...$("ai-form").querySelectorAll('[name="kinds"]')];
    const hasItinerary = kinds.some((input) => input.value === "itinerary" && input.checked);
    $("ai-start-date").required = hasItinerary;
    $("ai-content-only-note").hidden = hasItinerary || !kinds.some((input) => input.checked);
  }

  function setAiBusy(value) {
    ai.busy = value;
    updateModeControls();
  }

  async function generateAi(event) {
    event.preventDefault();
    if (!authorized() || busyJob()) return;
    $("ai-error").textContent = "";
    if (savedTask() && !ai.job && !ai.pendingRequest) { $("ai-error").textContent = "请关闭并重新打开助手，先恢复已有任务后再生成。"; return; }
    // Retain the same request ID after an uncertain network response so retry is safe.
    if (!ai.pendingRequest) {
      const form = new FormData($("ai-form"));
      const mode = ai.formMode;
      const kinds = mode === "append" ? form.getAll("kinds") : ["itinerary"];
      if (!kinds.length) { $("ai-error").textContent = "请至少选择地点、美食或行程中的一项。"; return; }
      const startDate = mode === "replace_day" ? formInput("target_date").value : formInput("start_date").value;
      const days = mode === "replace_day" ? 1 : Number(formInput("days").value);
      if (kinds.includes("itinerary") && (!/^\d{4}-\d{2}-\d{2}$/.test(startDate) || !requestDateRange({ start_date: startDate, days }))) {
        $("ai-error").textContent = "请选择有效的出发日期，并填写正整数旅行天数。";
        return;
      }
      if (ai.job?.status === "ready" && !window.confirm("当前 AI 预览尚未应用。重新生成会替换这份预览，原行程保留，继续吗？")) return;
      ai.pendingRequest = {
        model: String(form.get("model") || "").trim(),
        city_id: ai.cityId, kinds, planning_mode: mode, start_date: startDate || "", days,
        people: Number(form.get("people")), budget: form.get("budget") ? Number(form.get("budget")) : "",
        pace: form.get("pace"), preferences: form.get("preferences") || "", requirements: form.get("requirements") || "",
        request_id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      };
      if (mode === "replace_day") ai.pendingRequest.target_date = startDate;
    }
    $("ai-city-name").textContent = ai.cityName;
    $("ai-preview").hidden = true;
    $("ai-status").textContent = "正在提交旅行需求…";
    setAiBusy(true);
    const epoch = ai.epoch;
    try {
      const { data } = await requestJson("/api/ai/jobs", { method: "POST", body: JSON.stringify(ai.pendingRequest) });
      if (epoch !== ai.epoch) return;
      ai.pendingRequest = null;
      ai.job = unwrapJob(data);
      ai.replacementConflict = false;
      rememberTask();
      ai.previewId = null;
      updateAiJob();
    } catch (error) {
      if (epoch !== ai.epoch) return;
      if (error.status && error.status < 500) ai.pendingRequest = null;
      $("ai-status").textContent = ai.pendingRequest ? "连接中断，点击重试会查询同一次生成，不会重复提交。" : "";
      if (ai.job?.status === "ready" && ai.rows.length) $("ai-preview").hidden = false;
      setAiBusy(false);
      handleError(error, "ai-error");
    }
  }

  function schedulePoll(delay = 2000) { clearTimeout(ai.timer); ai.timer = setTimeout(pollAi, delay); }

  async function pollAi() {
    if (!ai.job || !state.projectUnlocked || !state.userId) return;
    const id = ai.job.id;
    const epoch = ai.epoch;
    try {
      const { data } = await requestJson(`/api/ai/jobs/${encodeURIComponent(id)}`);
      if (epoch !== ai.epoch || ai.job?.id !== id) return;
      ai.job = unwrapJob(data);
      $("ai-error").textContent = "";
      updateAiJob();
    } catch (error) {
      if (epoch !== ai.epoch || ai.job?.id !== id) return;
      handleError(error, "ai-error");
      if ([403, 404].includes(error.status)) { forgetTask(id); ai.job.status = "failed"; setAiBusy(false); $("ai-status").textContent = "任务已不可用，请重新生成。"; }
      else if (error.status !== 401 && state.projectUnlocked && state.userId) schedulePoll(6000);
    }
  }

  function updateAiJob() {
    const job = ai.job;
    rememberTask();
    $("ai-model").textContent = job.model ? ` · 本次结果：${job.model}` : "";
    ai.cityId = job.city_id;
    ai.cityName = job.city_name || ai.cityName;
    $("ai-city-name").textContent = ai.cityName;
    const replacing = planningMode(job.request) !== "append";
    const labels = { queued: "正在排队，轮到后会自动开始…", running: "正在思考路线、挑选地点与美食，通常需要一两分钟。可以关闭窗口继续浏览。", ready: replacing ? "新的行程方案已准备好。编辑和勾选后，确认替换原行程。" : "灵感已准备好。编辑和勾选后，加入你的共享清单。", failed: "本次生成未完成，已有旅行内容不受影响。", imported: replacing ? "已应用新的行程方案。" : "已加入共享清单，和同行者一起继续完善吧。" };
    $("ai-status").textContent = labels[job.status] || "正在校验生成内容…";
    if (["queued", "running"].includes(job.status)) {
      setAiBusy(true);
      $("ai-target-change").hidden = true;
      $("ai-generate").textContent = "正在生成…";
      schedulePoll();
    } else {
      setAiBusy(false);
      $("ai-target-change").hidden = ai.cityId === state.cityId;
      $("ai-target-change").textContent = `为${state.cities.find((city) => city.id === state.cityId)?.name || "当前城市"}新建计划`;
      if (job.status === "failed") $("ai-error").textContent = typeof job.error === "string" ? job.error : job.error?.message || "AI 服务暂时不可用，请稍后重试。";
      if (["ready", "imported"].includes(job.status) && ai.previewId !== job.id) renderAiPreview();
      if (job.status === "imported") disableImportedPreview();
      $("ai-open").classList.toggle("has-result", job.status === "ready");
    }
  }

  function renderAiPreview() {
    const job = ai.job;
    const result = job.result || {};
    const replan = planningMode(job.request) !== "append";
    const range = replan ? requestDateRange(job.request) : null;
    ai.previewId = job.id;
    ai.rows = [];
    $("ai-notices").replaceChildren(...(result.notices || []).map((notice) => element("li", "", notice)));
    $("ai-notices").hidden = !(result.notices || []).length;
    const citywideGuidance = planningMode(job.request) !== "replace_day" && (result.itineraries || []).length > 0;
    $("ai-travel-guidance").hidden = !citywideGuidance || !(result.notices || []).length;
    $("ai-preview-scope").hidden = !replan;
    const oldCount = Number.isInteger(job.replacement?.count) ? `现有 ${job.replacement.count} 项行程` : "现有行程";
    $("ai-preview-scope").textContent = `${ai.cityName} · ${replacementLabel(job.request)}：确认后将用所选方案替换${oldCount}。${planningMode(job.request) === "replace_all" ? "包括新日期范围之外的全部旧行程。" : "其他日期保留。"}新方案的每一天至少选择一项；确认应用前原行程保留。`;
    const groups = [];
    for (const [kind, plural] of [["attraction", "attractions"], ["food", "foods"], ["itinerary", "itineraries"]]) {
      const records = result[plural] || [];
      if (!records.length) continue;
      const group = element("section", "ai-result-group");
      group.append(element("h3", "", `${CATEGORY[kind].title} · ${records.length}`));
      for (const record of records) {
        const index = ai.rows.length;
        const card = element("article", "ai-result-card");
        const heading = element("div", "ai-result-top");
        const choice = element("label", "ai-item-choice");
        const checkbox = element("input");
        checkbox.type = "checkbox";
        checkbox.checked = replan || !record.duplicate;
        checkbox.setAttribute("aria-label", `导入${itemName(kind, record)}`);
        choice.append(checkbox, element("strong", "", itemName(kind, record)));
        heading.append(choice);
        if (record.duplicate) heading.append(element("span", "tag", "可能已存在"));
        const rating = kind === "attraction" ? element("div", "ai-scenic-rating") : null;
        if (rating) {
          const badge = scenicRatingBadge(record);
          rating.hidden = !badge;
          if (badge) rating.append(badge);
        }
        const shortText = kind === "itinerary" ? `${record.date} ${record.start_time || ""} · ${record.location || "地点待定"}` : record.description;
        const description = element("p", "ai-result-description", shortText);
        const details = element("details", "ai-edit-details");
        details.append(element("summary", "", "查看 / 编辑详情"));
        const fields = element("div", "ai-edit-fields");
        for (const definition of FIELDS[kind]) {
          const field = makeField(definition, record[definition.key], { cityId: ai.cityId });
          const input = field.querySelector("input,textarea,select");
          input.id = `ai-${index}-${definition.key}`;
          input.name = `${index}:${definition.key}`;
          field.htmlFor = input.id;
          if (definition.tagsKind || definition.attractionNames) field.querySelector("label").htmlFor = input.id;
          input.required = checkbox.checked && Boolean(definition.required);
          if (replan && definition.key === "date" && range) { input.min = range.start; input.max = range.end; }
          fields.append(field);
        }
        details.append(fields);
        card.append(heading);
        if (rating) card.append(rating);
        card.append(description, details);
        const row = { kind, checkbox, fields, card, details, record };
        checkbox.addEventListener("change", () => {
          for (const definition of FIELDS[kind]) fields.querySelector(`[name="${index}:${definition.key}"]`).required = checkbox.checked && Boolean(definition.required);
          updateSelection();
        });
        const updateEditedPreview = () => {
          const key = kind === "itinerary" ? "title" : "name";
          const value = (field) => fields.querySelector(`[name="${index}:${field}"]`).value;
          const title = value(key) || "未命名";
          choice.querySelector("strong").textContent = title;
          checkbox.setAttribute("aria-label", `导入${title}`);
          description.textContent = kind === "itinerary" ? `${value("date")} ${value("start_time")} · ${value("location") || "地点待定"}` : value("description");
          if (rating) {
            const unchanged = value("name").trim() === (record.name || "").trim() && value("scenic_rating") === (record.scenic_rating || "");
            const edited = { ...record, scenic_rating: value("scenic_rating"), scenic_rating_info: unchanged ? record.scenic_rating_info : { status: "unverified" } };
            const badge = scenicRatingBadge(edited);
            rating.replaceChildren(...(badge ? [badge] : []));
            rating.hidden = !badge;
          }
          updateSelection();
        };
        fields.addEventListener("input", updateEditedPreview);
        fields.addEventListener("change", updateEditedPreview);
        ai.rows.push(row);
        group.append(card);
      }
      groups.push(group);
    }
    $("ai-preview-items").replaceChildren(...groups);
    $("ai-preview").hidden = false;
    $("ai-select-all").disabled = false;
    $("ai-import-form").querySelectorAll("button,input,textarea,select").forEach((input) => { input.disabled = false; });
    updateSelection();
  }

  function updateSelection() {
    const count = ai.rows.filter((row) => row.checkbox.checked).length;
    const replan = planningMode() !== "append";
    const coverageError = replan ? replacementCoverageError(ai.rows.filter((row) => row.checkbox.checked).map((row) => ({ kind: row.kind, data: { date: row.fields.querySelector('[name$=":date"]')?.value || "" } }))) : "";
    $("ai-selection-count").textContent = `已选 ${count} 项 · ${ai.cityName}${replan ? ` · ${replacementLabel(ai.job.request)}` : ""}${coverageError ? `。${coverageError}` : ""}`;
    $("ai-attraction-sync-note").hidden = !ai.rows.some(row => row.checkbox.checked && row.kind === "itinerary");
    $("ai-import").textContent = ai.replacementConflict ? "行程已变化，请重新生成" : replan ? `确认替换${replacementLabel(ai.job.request)}` : `将 ${count} 项加入${ai.cityName}清单`;
    $("ai-import").disabled = count === 0 || Boolean(coverageError) || ai.replacementConflict || ai.importing || ai.job?.status !== "ready";
    ai.rows.forEach((row) => row.card.classList.toggle("is-selected", row.checkbox.checked));
  }

  function replacementCoverageError(items) {
    const range = requestDateRange(ai.job.request);
    if (!range) return "本次任务的日期无效，请重新生成。";
    if (items.some((item) => item.kind !== "itinerary" || item.data.date < range.start || item.data.date > range.end)) return `行程日期必须在本次规划范围内：${range.start}${range.days > 1 ? ` 至 ${range.end}` : ""}。`;
    const selected = new Set(items.map((item) => item.data.date));
    if (selected.size === range.days) return "";
    let firstMissing = range.start;
    for (const value of [...selected].sort()) {
      if (value !== firstMissing) break;
      firstMissing = new Date(new Date(`${value}T00:00:00Z`).getTime() + 86400000).toISOString().slice(0, 10);
    }
    return `请为 ${firstMissing} 等未覆盖日期至少选择一项行程。`;
  }

  function disableImportedPreview() {
    $("ai-import-form").querySelectorAll("input,textarea,select,button").forEach((input) => { input.disabled = input.id !== "ai-result-close"; });
    $("ai-select-all").disabled = true;
    $("ai-import").textContent = "已导入";
  }

  function showAiImportError(message) {
    $("ai-error").textContent = message;
    $("ai-import-error-message").textContent = message;
    const dialog = $("ai-import-error-dialog");
    if (!dialog.open) dialog.showModal();
  }

  async function importAi(event) {
    event.preventDefault();
    if (!authorized() || ai.job?.status !== "ready" || ai.importing || busyJob() || ai.pendingRequest || ai.replacementConflict) return;
    $("ai-error").textContent = "";
    const selected = ai.rows.filter((row) => row.checkbox.checked);
    if (!selected.length) { showAiImportError("请至少选择一项内容后再导入。"); return; }
    const items = [];
    for (const row of selected) {
      const data = {};
      for (const definition of FIELDS[row.kind]) {
        const input = row.fields.querySelector(`[name$=":${definition.key}"]`);
        if (!input.checkValidity()) {
          row.details.open = true;
          input.focus();
          showAiImportError(`请检查“${definition.label || definition.key}”：${input.validationMessage || "请填写有效内容。"}`);
          return;
        }
        if (definition.attractionNames) {
          const error = attractionNamesError(input.value);
          if (error) { row.details.open = true; input.focus(); showAiImportError(error); return; }
        }
        data[definition.key] = definition.attractionNames ? parseAttractionNames(input.value) : definition.tagsKind ? window.TripTaxonomy.parseTags(input.value) : input.value.trim();
      }
      items.push({ kind: row.kind, data });
    }
    const replan = planningMode() !== "append";
    if (replan) {
      const coverageError = replacementCoverageError(items);
      if (coverageError) { showAiImportError(coverageError); return; }
    }
    const payload = { city_id: ai.job.city_id, items };
    if (replan) payload.confirm_replace = true;
    const body = JSON.stringify(payload);
    if (new TextEncoder().encode(body).length > 8 * 1024 * 1024) { showAiImportError("所选内容过多，请减少条目或缩短详细说明后再导入。"); return; }
    if (replan) {
      const oldCount = Number.isInteger(ai.job.replacement?.count) ? `${ai.job.replacement.count} 项旧行程` : "原行程";
      const scope = `${ai.cityName}的${replacementLabel(ai.job.request)}`;
      if (!window.confirm(`确认替换${scope}？\n\n将删除${oldCount}，应用所选的 ${items.length} 项新行程。${planningMode() === "replace_all" ? "包括新日期范围之外的全部旧行程。" : "其他日期不变。"}\n地点、美食及其他城市不受影响。此操作无法在页面撤销。`)) return;
    }
    $("ai-import").disabled = true;
    $("ai-import").textContent = replan ? "正在替换行程…" : "正在加入共享清单…";
    ai.importing = true;
    setAiBusy(true);
    $("ai-select-all").disabled = true;
    $("ai-target-change").disabled = true;
    $("ai-import-form").querySelectorAll("input,textarea,select").forEach((input) => { input.disabled = true; });
    const epoch = ai.epoch;
    const jobId = ai.job.id;
    try {
      const { data } = await requestJson(`/api/ai/jobs/${encodeURIComponent(jobId)}/import`, { method: "POST", body });
      if (epoch !== ai.epoch || ai.job?.id !== jobId) return;
      ai.job.status = "imported";
      disableImportedPreview();
      const created = Array.isArray(data.created) ? data.created.length : Number(data.created || 0);
      const skipped = Array.isArray(data.skipped) ? data.skipped.length : Number(data.skipped || 0);
      const deleted = Array.isArray(data.deleted) ? data.deleted.length : Number(data.deleted || 0);
      const autoAdded = Number.isInteger(data.auto_added_attractions) && data.auto_added_attractions > 0 ? data.auto_added_attractions : 0;
      $("ai-status").textContent = replan ? `已替换${ai.cityName}的${replacementLabel(ai.job.request)}：移除 ${deleted} 项旧行程，应用 ${created} 项新行程。` : `已向${ai.cityName}添加 ${created} 项，跳过 ${skipped} 项重复内容。`;
      if (autoAdded) $("ai-status").textContent += `已自动补充 ${autoAdded} 个行程地点到清单。`;
      $("ai-open").classList.remove("has-result");
      await fetchSnapshot(true, true);
      showToast(replan ? `已应用${ai.cityName}的新行程方案` : `已向${ai.cityName}添加 ${created} 项旅行灵感`);
    } catch (error) { if (epoch === ai.epoch && ai.job?.id === jobId) {
      $("ai-import-form").querySelectorAll("input,textarea,select").forEach((input) => { input.disabled = false; });
      $("ai-select-all").disabled = false;
      if (replan && error.status === 409) {
        ai.replacementConflict = true;
        showAiImportError("原行程已发生变化，本次未替换任何内容。预览已保留，请重新生成后再确认应用。");
      } else {
        handleError(error, "ai-error");
        const message = error.data?.code === "PROJECT_LOCKED" ? "项目访问已过期，请重新输入口令后再导入。"
          : error.status === 401 ? "用户身份已过期，请重新选择用户 ID 后再导入。"
          : error.name === "TypeError" && !error.status ? "网络连接异常，暂时无法确认导入结果。请检查网络后重试。"
          : error.message || "暂时无法导入，请稍后重试。";
        showAiImportError(message);
      }
    } } finally {
      if (epoch === ai.epoch && ai.job?.id === jobId) {
        ai.importing = false;
        setAiBusy(false);
        $("ai-target-change").disabled = false;
        if (ai.job.status === "ready") updateSelection();
      }
    }
  }

  function reset() {
    window.TripBatch.reset();
    clearTimeout(metro.timer);
    metro.epoch += 1;
    metro.cityId = ""; metro.record = null; metro.view = null; metro.error = "";
    metro.loading = false; metro.submitting = false; metro.checkedAt = 0; metro.timer = null;
    clearTimeout(ai.timer);
    ai.epoch += 1;
    $("ai-model-input").value = "";
    $("ai-model").textContent = "";
    ai.job = null; ai.pendingRequest = null; ai.previewId = null; ai.rows = []; ai.config = null;
    ai.importing = false;
    ai.restoring = false;
    ai.formMode = "append"; ai.longRange = 0; ai.replacementConflict = false;
    $("ai-form").reset();
    setAiBusy(false);
    $("ai-target-change").disabled = false;
    $("ai-select-all").disabled = false;
    $("ai-preview").hidden = true;
    $("ai-preview-items").replaceChildren();
    $("ai-status").textContent = $("ai-error").textContent = "";
    $("ai-open").classList.remove("has-result");
    for (const id of ["city-dialog", "metro-dialog", "ai-dialog", "ai-import-error-dialog"]) if ($(id).open) $(id).close();
  }

  function init() {
    $("city-select").addEventListener("click", () => {
      if (!authorized()) return;
      $("city-search").value = "";
      $("city-add-error").textContent = "";
      renderCityResults();
      $("city-dialog").showModal();
      $("city-search").focus();
    });
    $("city-close").addEventListener("click", () => $("city-dialog").close());
    $("city-search").addEventListener("input", renderCityResults);
    $("city-search").addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") { event.preventDefault(); $("city-results").querySelector("button")?.focus(); }
      if (event.key === "Enter") { event.preventDefault(); const buttons = $("city-results").querySelectorAll("button"); if (buttons.length === 1) buttons[0].click(); }
    });
    $("city-add-form").addEventListener("submit", addCity);
    setupMap();
    $("ai-open").addEventListener("click", () => openAi());
    $("ai-replan-all").addEventListener("click", () => openReplan("replace_all"));
    for (const id of ["ai-close", "ai-result-close"]) $(id).addEventListener("click", () => $("ai-dialog").close());
    $("ai-form").addEventListener("submit", generateAi);
    $("ai-new-task").addEventListener("click", () => switchTask());
    $("ai-task-select").addEventListener("change", () => switchTask($("ai-task-select").value));
    $("ai-model-input").addEventListener("input", updateModeControls);
    $("ai-target-change").addEventListener("click", () => {
      choosePlanningMode("append", "", true);
    });
    $("ai-planning-mode").addEventListener("change", () => choosePlanningMode(formInput("planning_mode").value));
    $("ai-target-date").addEventListener("change", () => {
      if (busyJob() || ai.pendingRequest) { formInput("target_date").value = ai.pendingRequest?.target_date || ai.job?.request?.target_date || ""; updateModeControls(); return; }
      if (ai.job?.status === "ready" && formInput("target_date").value !== ai.job.request.target_date) {
        if (!window.confirm("当前 AI 预览尚未应用。更改日期会清除这份预览，原行程保留，继续吗？")) { formInput("target_date").value = ai.job.request.target_date; updateModeControls(); return; }
        clearAiDraft();
      }
      updateModeControls();
    });
    $("ai-form").querySelectorAll('[name="kinds"]').forEach((input) => input.addEventListener("change", updateDateRequired));
    document.querySelectorAll("[data-ai-template]").forEach((button) => button.addEventListener("click", () => useTemplate(button.dataset.aiTemplate)));
    $("ai-select-all").addEventListener("click", () => {
      ai.rows.forEach((row) => { row.checkbox.checked = planningMode() !== "append" || !row.record.duplicate; row.checkbox.dispatchEvent(new Event("change")); });
    });
    // Browser constraint validation can otherwise try to focus inside a collapsed details.
    $("ai-import-form").noValidate = true;
    $("ai-import-form").addEventListener("submit", importAi);
  }

  return { init, reset, initialCity, switchCity, renderCity, renderTransport, cityMapUrl, renderCacheNotice, openReplan };
})();
