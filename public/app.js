"use strict";

const CATEGORY = {
  itinerary: {
    title: "行程规划",
    kicker: "按天安排",
    description: "把每天的时间、地点和提醒排在一起。",
    addLabel: "添加行程",
    accent: "#315ca8",
  },
  attraction: {
    title: "景点",
    kicker: "一起挑选",
    description: "地点信息和攻略链接都可以共同修改。",
    addLabel: "添加景点",
    accent: "#f05b3f",
  },
  transit: {
    title: "公共交通",
    kicker: "轻松换乘",
    description: "查看轨道交通图，或打开地图查询实时路线。",
    addLabel: "添加线路",
    accent: "#087f73",
  },
  food: {
    title: "本地美食",
    kicker: "边走边吃",
    description: "把想吃的味道和推荐区域留在清单里。",
    addLabel: "添加美食",
    accent: "#d58d16",
  },
  activity: {
    title: "协作动态",
    kicker: "谁改了什么",
    description: "最近 30 次修改按时间排列。",
  },
};

const FIELDS = {
  itinerary: [
    { key: "date", label: "日期", required: true, maxlength: 10, type: "date" },
    { key: "start_time", label: "开始时间", maxlength: 5, type: "time" },
    { key: "title", label: "安排名称", required: true, maxlength: 80, placeholder: "例如：外滩日落散步" },
    { key: "category", label: "类型", maxlength: 30, placeholder: "例如：景点 / 用餐 / 交通" },
    { key: "location", label: "地点", maxlength: 120, placeholder: "例如：外滩观景平台" },
    {
      key: "notes",
      label: "备注",
      maxlength: 600,
      multiline: true,
      placeholder: "集合方式、预约信息、备选方案或需要携带的物品",
    },
    {
      key: "link",
      label: "相关链接",
      maxlength: 500,
      type: "url",
      placeholder: "https://…",
      help: "只支持完整的 http:// 或 https:// 地址",
    },
  ],
  attraction: [
    { key: "navigation_link", label: "导航链接", maxlength: 500, type: "url", placeholder: "留空时按名称打开高德地图" },
    { key: "name", label: "景点名称", required: true, maxlength: 60, placeholder: "例如：外滩风景区" },
    { key: "district", label: "所在区域", maxlength: 30, placeholder: "例如：黄浦区" },
    { key: "category", label: "类型", maxlength: 30, placeholder: "例如：城市漫步" },
    {
      key: "description",
      label: "简介",
      maxlength: 600,
      multiline: true,
      placeholder: "这里有什么、为什么值得去",
    },
    { key: "duration", label: "建议时长", maxlength: 40, placeholder: "例如：2–3 小时" },
    {
      key: "transport",
      label: "到达方式",
      maxlength: 160,
      placeholder: "例如：地铁 10/14 号线豫园站",
    },
    {
      key: "link",
      label: "小红书 / 攻略链接",
      maxlength: 500,
      type: "url",
      placeholder: "https://…",
      help: "只支持完整的 http:// 或 https:// 地址",
    },
  ],
  transit: [
    { key: "name", label: "线路名称", required: true, maxlength: 60, placeholder: "例如：2 号线" },
    { key: "color", label: "卡片识别色", required: true, type: "color", defaultValue: "#087f73" },
    {
      key: "route",
      label: "大致走向",
      maxlength: 180,
      multiline: true,
      placeholder: "起点、终点和沿途重要区域",
    },
    {
      key: "connections",
      label: "重点连接",
      maxlength: 220,
      multiline: true,
      placeholder: "机场、火车站、景点或重要换乘点",
    },
    {
      key: "service_note",
      label: "运营提醒",
      maxlength: 220,
      multiline: true,
      placeholder: "例如：末班车时间请在出发前核对",
    },
    {
      key: "link",
      label: "官方线路链接",
      maxlength: 500,
      type: "url",
      placeholder: "https://…",
    },
  ],
  food: [
    { key: "name", label: "美食名称", required: true, maxlength: 60, placeholder: "例如：生煎馒头" },
    { key: "category", label: "类别", maxlength: 30, placeholder: "例如：街头小吃" },
    {
      key: "description",
      label: "风味简介",
      maxlength: 500,
      multiline: true,
      placeholder: "口味、做法或特色",
    },
    {
      key: "where_to_try",
      label: "推荐时段 / 区域",
      maxlength: 120,
      placeholder: "例如：早餐；黄浦老城厢",
    },
    {
      key: "tip",
      label: "点单提示",
      maxlength: 220,
      multiline: true,
      placeholder: "适合分享、季节限定或其他提醒",
    },
    {
      key: "link",
      label: "参考链接",
      maxlength: 500,
      type: "url",
      placeholder: "https://…",
    },
  ],
};

const state = {
  cityId: "shanghai",
  cities: [],
  projectName: "旅游规划",
  tab: ["itinerary", "attraction", "transit", "food", "activity"].includes(location.hash.slice(1))
    ? location.hash.slice(1)
    : "itinerary",
  items: { itinerary: [], attraction: [], transit: [], food: [] },
  activity: [],
  users: [],
  revision: null,
  userId: "",
  projectUnlocked: false,
  currentEdit: null,
  lockedDraft: null,
  pendingDelete: null,
  editorDirty: false,
  saving: false,
  deleteInFlight: false,
  snapshotRequestId: 0,
  toastTimer: null,
  syncTimer: null,
  starting: false,
  previouslyOffline: false,
};

const dom = {
  cards: document.querySelector("#cards"),
  activityPanel: document.querySelector("#activity-panel"),
  activityList: document.querySelector("#activity-list"),
  sectionTitle: document.querySelector("#section-title"),
  sectionKicker: document.querySelector("#section-kicker"),
  sectionDescription: document.querySelector("#section-description"),
  addButton: document.querySelector("#add-button"),
  tripStats: document.querySelector("#trip-stats"),
  syncStatus: document.querySelector("#sync-status"),
  syncLabel: document.querySelector("#sync-label"),
  appShell: document.querySelector(".app-shell"),
  projectDialog: document.querySelector("#project-dialog"),
  projectForm: document.querySelector("#project-form"),
  projectCodeInput: document.querySelector("#project-code-input"),
  projectError: document.querySelector("#project-error"),
  identityButton: document.querySelector("#identity-button"),
  identityLabel: document.querySelector("#identity-label"),
  identityDialog: document.querySelector("#identity-dialog"),
  identityForm: document.querySelector("#identity-form"),
  identityTitle: document.querySelector("#identity-title"),
  identityError: document.querySelector("#identity-error"),
  identityCancel: document.querySelector("#identity-cancel"),
  userIdInput: document.querySelector("#user-id-input"),
  knownUsersSection: document.querySelector("#known-users-section"),
  knownUsersCount: document.querySelector("#known-users-count"),
  userChoiceList: document.querySelector("#user-choice-list"),
  identityDivider: document.querySelector("#identity-divider"),
  projectLockButton: document.querySelector("#project-lock-button"),
  editDialog: document.querySelector("#edit-dialog"),
  editForm: document.querySelector("#edit-form"),
  editFields: document.querySelector("#edit-fields"),
  editTitle: document.querySelector("#edit-title"),
  editKicker: document.querySelector("#edit-kicker"),
  editError: document.querySelector("#edit-error"),
  editConflict: document.querySelector("#edit-conflict"),
  conflictDetails: document.querySelector("#conflict-details"),
  conflictLoad: document.querySelector("#conflict-load"),
  conflictOverwrite: document.querySelector("#conflict-overwrite"),
  editClose: document.querySelector("#edit-close"),
  deleteButton: document.querySelector("#delete-button"),
  saveButton: document.querySelector("#save-button"),
  confirmDialog: document.querySelector("#confirm-dialog"),
  confirmMessage: document.querySelector("#confirm-message"),
  confirmCancel: document.querySelector("#confirm-cancel"),
  confirmDelete: document.querySelector("#confirm-delete"),
  shareButton: document.querySelector("#share-button"),
  toast: document.querySelector("#toast"),
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function validExternalUrl(value) {
  if (!value) return "";
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "刚刚";
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const absSeconds = Math.abs(seconds);
  const formatter = new Intl.RelativeTimeFormat("zh-CN", { numeric: "auto" });
  if (absSeconds < 60) return formatter.format(seconds, "second");
  if (absSeconds < 3600) return formatter.format(Math.round(seconds / 60), "minute");
  if (absSeconds < 86400) return formatter.format(Math.round(seconds / 3600), "hour");
  if (absSeconds < 604800) return formatter.format(Math.round(seconds / 86400), "day");
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric" }).format(date);
}

function showToast(message) {
  clearTimeout(state.toastTimer);
  dom.toast.textContent = message;
  dom.toast.hidden = false;
  state.toastTimer = setTimeout(() => {
    dom.toast.hidden = true;
  }, 2600);
}

function setSyncStatus(mode, text) {
  dom.syncStatus.classList.toggle("is-online", mode === "online");
  dom.syncStatus.classList.toggle("is-offline", mode === "offline");
  dom.syncLabel.textContent = text;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    cache: "no-store",
    credentials: "same-origin",
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  let data = null;
  if (response.status !== 204 && response.status !== 304) {
    data = await response.json().catch(() => ({}));
  }
  if (response.status === 304) return { response, data: null };
  if (!response.ok) {
    const error = new Error(data?.error || "请求没有成功，请稍后再试。");
    error.status = response.status;
    error.data = data || {};
    throw error;
  }
  return { response, data };
}

async function fetchSnapshot(force = false, quiet = false) {
  if (!state.projectUnlocked || !state.userId) return;
  const requestId = ++state.snapshotRequestId;
  if (!quiet) setSyncStatus("syncing", "正在同步");
  try {
    const headers = {};
    if (!force && state.revision !== null) headers["If-None-Match"] = `"revision-${state.revision}"`;
    const { response, data } = await requestJson("/api/snapshot", { headers });
    if (requestId !== state.snapshotRequestId || !state.projectUnlocked || !state.userId) return;
    if (
      response.status !== 304 &&
      data &&
      (state.revision === null || data.revision >= state.revision)
    ) {
      const hadRevision = state.revision !== null;
      const changedRemotely = hadRevision && data.revision > state.revision;
      state.revision = data.revision;
      state.cities = data.cities || [];
      state.projectName = data.project?.name || "旅游规划";
      if (!state.cities.some((city) => city.id === state.cityId)) state.cityId = state.cities[0]?.id || "";
      state.items = {
        itinerary: [],
        attraction: [],
        transit: [],
        food: [],
        ...(data.items || {}),
      };
      state.activity = data.activity;
      render();
      if (changedRemotely && quiet && !dom.editDialog.open) showToast("计划里有新的修改");
    }
    if (requestId !== state.snapshotRequestId) return;
    setSyncStatus("online", state.revision ? `已同步 · 版本 ${state.revision}` : "已同步");
    if (state.previouslyOffline) {
      state.previouslyOffline = false;
      showToast("已恢复连接并同步最新计划");
    }
  } catch (error) {
    if (requestId !== state.snapshotRequestId) return;
    if (error.data?.code === "PROJECT_LOCKED") {
      showProjectGate("项目访问已过期，请重新输入口令。", true);
      return;
    }
    state.previouslyOffline = true;
    setSyncStatus("offline", "暂时离线");
    if (!quiet) showToast(error.message || "暂时无法同步计划");
  }
}

function addTag(container, text, accent = false) {
  if (!text) return;
  const tag = element("span", `tag${accent ? " is-accent" : ""}`, text);
  container.append(tag);
}

function addDetail(list, label, value) {
  if (!value) return;
  const row = element("div", "detail-row");
  row.append(element("dt", "", label), element("dd", "", value));
  list.append(row);
}

function itemName(kind, item) {
  return kind === "itinerary" ? item.title : item.name;
}

function editButton(kind, item) {
  const button = element("button", "edit-card-button", "✎");
  button.type = "button";
  button.dataset.editId = item.id;
  button.setAttribute("aria-label", `编辑${itemName(kind, item)}`);
  button.addEventListener("click", () => openEditor(kind, item));
  return button;
}

function formatItineraryDate(value) {
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(date);
}

function itineraryCard(item) {
  const card = element("article", "plan-card itinerary-card");
  card.style.setProperty("--card-accent", CATEGORY.itinerary.accent);
  const body = element("div", "card-body");
  const top = element("div", "card-topline");
  const heading = element("div");
  const tags = element("div", "tags");
  addTag(tags, item.start_time || "时间待定", true);
  addTag(tags, item.category);
  heading.append(tags, element("h3", "", item.title));
  top.append(heading, editButton("itinerary", item));
  body.append(top);
  if (item.notes) body.append(element("p", "card-description", item.notes));
  const details = element("dl", "details");
  addDetail(details, "地点", item.location);
  body.append(details);
  card.append(body);
  appendFooter(card, item, "打开链接");
  return card;
}

function appendFooter(card, item, linkLabel) {
  const footer = element("footer", "card-footer");
  const byline =
    item.updated_by === "system"
      ? "初始化资料"
      : `${item.updated_by} · ${formatTime(item.updated_at)}`;
  footer.append(element("span", "", byline));
  const link = validExternalUrl(item.link);
  if (link) {
    const anchor = element("a", "guide-link", `${linkLabel} ↗`);
    anchor.href = link;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer nofollow";
    footer.append(anchor);
  } else {
    footer.append(element("span", "", "暂无外链"));
  }
  card.append(footer);
}

function attractionCard(item) {
  const card = element("article", "plan-card");
  card.style.setProperty("--card-accent", CATEGORY.attraction.accent);
  const body = element("div", "card-body");
  const top = element("div", "card-topline");
  const heading = element("div");
  const tags = element("div", "tags");
  addTag(tags, item.district, true);
  addTag(tags, item.category);
  heading.append(tags, element("h3", "", item.name));
  top.append(heading, editButton("attraction", item));
  body.append(top);
  if (item.description) body.append(element("p", "card-description", item.description));
  const details = element("dl", "details");
  addDetail(details, "建议时长", item.duration);
  addDetail(details, "到达方式", item.transport);
  body.append(details);
  card.append(body);
  appendFooter(card, item, "打开攻略");
  const city = state.cities.find((entry) => entry.id === item.city_id)?.name || "上海";
  const navigation = externalAnchor("导航 ↗", item.navigation_link || amapUrl(city, item.name));
  card.lastElementChild.append(navigation);
  return card;
}

function externalAnchor(label, url) {
  const link = element("a", "map-link", label);
  link.href = validExternalUrl(url);
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

function amapUrl(city, keyword = "地铁站") {
  return `https://uri.amap.com/search?keyword=${encodeURIComponent(keyword)}&city=${encodeURIComponent(city)}&view=map&src=travelplanner&callnative=0`;
}

function renderTransport() {
  const city = state.cities.find((entry) => entry.id === state.cityId);
  const panel = element("section", "transport-panel");
  if (!city) { dom.cards.replaceChildren(panel); return; }
  const links = element("div", "map-actions");
  links.append(externalAnchor("高德地图 ↗", amapUrl(city.name)), externalAnchor("百度地图 ↗", `https://api.map.baidu.com/place/search?query=${encodeURIComponent("地铁站")}&region=${encodeURIComponent(city.name)}&output=html&src=webapp.travel.planner`));
  panel.append(links);
  if (city.id === "shanghai" || /^上海市?$/.test(city.name)) {
    panel.append(element("h3", "", "上海轨道交通线路图"));
    const full = element("a", "");
    full.href = "/assets/shanghai-metro.png";
    full.target = "_blank";
    full.rel = "noopener noreferrer";
    const img = element("img", "metro-image");
    img.src = "/assets/shanghai-metro.png";
    img.alt = "上海轨道交通线路图，点击打开大图";
    img.loading = "lazy";
    full.append(img);
    panel.append(full, element("p", "", "点击图片查看大图。示意图仅供规划，运营调整请查询官方最新版。"));
    panel.append(externalAnchor("上海地铁官方最新版 ↗", "https://service.shmetro.com/awltcz/index.htm"));
    panel.append(element("p", "map-credit", "线路图：Yveltal，2025-12-27 版本；PNG 预览，未改图。"));
    panel.append(externalAnchor("图片来源", "https://commons.wikimedia.org/wiki/File:Shanghai_Metro_Linemap.svg"), document.createTextNode(" · "), externalAnchor("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/"));
  } else panel.append(element("p", "", "该城市暂未配置线路图，可通过上方地图查询公共交通。"));
  dom.cards.replaceChildren(panel);
}

function transitCard(item) {
  const card = element("article", "plan-card");
  card.style.setProperty("--card-accent", item.color || CATEGORY.transit.accent);
  const body = element("div", "card-body");
  const top = element("div", "card-topline");
  const heading = element("div", "transit-name");
  const dot = element("span", "line-dot");
  dot.style.setProperty("--line-color", item.color || CATEGORY.transit.accent);
  dot.setAttribute("aria-hidden", "true");
  heading.append(dot, element("h3", "", item.name));
  top.append(heading, editButton("transit", item));
  body.append(top);
  if (item.route) body.append(element("p", "card-description", item.route));
  const details = element("dl", "details");
  addDetail(details, "重点连接", item.connections);
  addDetail(details, "运营提醒", item.service_note);
  body.append(details);
  card.append(body);
  appendFooter(card, item, "线路资料");
  return card;
}

function foodCard(item) {
  const card = element("article", "plan-card");
  card.style.setProperty("--card-accent", CATEGORY.food.accent);
  const body = element("div", "card-body");
  const top = element("div", "card-topline");
  const heading = element("div");
  const tags = element("div", "tags");
  addTag(tags, item.category, true);
  heading.append(tags, element("h3", "", item.name));
  top.append(heading, editButton("food", item));
  body.append(top);
  if (item.description) body.append(element("p", "card-description", item.description));
  const details = element("dl", "details");
  addDetail(details, "推荐体验", item.where_to_try);
  addDetail(details, "点单提示", item.tip);
  body.append(details);
  card.append(body);
  appendFooter(card, item, "参考资料");
  return card;
}

function renderItinerary(items) {
  const ordered = [...items].sort((left, right) =>
    `${left.date}|${left.start_time || "99:99"}|${String(left.position).padStart(6, "0")}`.localeCompare(
      `${right.date}|${right.start_time || "99:99"}|${String(right.position).padStart(6, "0")}`,
    ),
  );
  const groups = [];
  let currentDate = "";
  let group = null;
  for (const item of ordered) {
    if (!group || item.date !== currentDate) {
      currentDate = item.date;
      group = element("section", "itinerary-day");
      const heading = element("div", "itinerary-day-heading");
      heading.append(
        element("span", "day-marker", String(groups.length + 1).padStart(2, "0")),
        element("h3", "", formatItineraryDate(currentDate)),
      );
      group.append(heading);
      groups.push(group);
    }
    group.append(itineraryCard(item));
  }
  dom.cards.replaceChildren(...groups);
}

function emptyState(kind) {
  const wrapper = element("div", "empty-state");
  const copy = element("div");
  copy.append(
    element("strong", "", `还没有${CATEGORY[kind].title}`),
    element("p", "", `点“${CATEGORY[kind].addLabel}”，把第一条计划放进来。`),
  );
  wrapper.append(copy);
  return wrapper;
}

function renderActivity() {
  dom.activityList.replaceChildren();
  if (!state.activity.some((entry) => entry.city_id === state.cityId)) {
    const item = element("li");
    const copy = element("div", "activity-copy");
    copy.append(element("p", "", "还没有修改记录。第一条编辑会出现在这里。"));
    item.append(element("span", "activity-avatar", "新"), copy);
    dom.activityList.append(item);
    return;
  }
  const actionLabel = { create: "添加了", update: "更新了", delete: "删除了" };
  const kindLabel = { itinerary: "行程", attraction: "景点", transit: "线路", food: "美食" };
  for (const activity of state.activity.filter((entry) => entry.city_id === state.cityId)) {
    const item = element("li");
    const avatar = element("span", "activity-avatar", activity.user_id.slice(0, 1).toUpperCase());
    const copy = element("div", "activity-copy");
    const summary = element("p");
    const user = element("strong", "", activity.user_id);
    summary.append(
      user,
      document.createTextNode(
        ` ${actionLabel[activity.action] || "修改了"}${kindLabel[activity.kind] || "内容"}“${activity.item_name}”`,
      ),
    );
    const time = element("time", "", formatTime(activity.happened_at));
    time.dateTime = activity.happened_at;
    copy.append(summary, time);
    item.append(avatar, copy);
    dom.activityList.append(item);
  }
}

function render() {
  const citySelect = document.querySelector("#city-select");
  citySelect.replaceChildren(...state.cities.map((city) => { const option = element("option", "", city.name); option.value = city.id; return option; }));
  citySelect.value = state.cityId;
  document.querySelector("#project-name").textContent = state.projectName;
  const focusedEditId = document.activeElement?.dataset?.editId;
  const meta = CATEGORY[state.tab];
  dom.sectionKicker.textContent = meta.kicker;
  dom.sectionTitle.textContent = meta.title;
  dom.sectionDescription.textContent = meta.description;
  const count = (kind) => state.items[kind].filter((item) => item.city_id === state.cityId).length;
  dom.tripStats.textContent = `${count("itinerary")} 项行程 · ${count("attraction")} 个景点 · ${count("food")} 种美食`;
  document.querySelector(".eyebrow").textContent = `共享旅行清单 · ${state.cities.find((city) => city.id === state.cityId)?.name || "选择城市"}`;
  dom.identityLabel.textContent = state.userId || "设置 ID";

  document.querySelectorAll(".nav-item").forEach((button) => {
    const active = button.dataset.tab === state.tab;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });

  const showingActivity = state.tab === "activity";
  dom.cards.hidden = showingActivity;
  dom.cards.classList.toggle("is-itinerary", state.tab === "itinerary");
  dom.activityPanel.hidden = !showingActivity;
  dom.addButton.hidden = showingActivity || state.tab === "transit";
  if (showingActivity) {
    renderActivity();
    return;
  }

  dom.addButton.lastElementChild.textContent = meta.addLabel;
  if (state.tab === "transit") { renderTransport(); return; }
  dom.cards.setAttribute("aria-busy", "false");
  const items = (state.items[state.tab] || []).filter((item) => item.city_id === state.cityId);
  if (!items.length) {
    dom.cards.replaceChildren(emptyState(state.tab));
    return;
  }
  if (state.tab === "itinerary") {
    renderItinerary(items);
    return;
  }
  const factories = { attraction: attractionCard, transit: transitCard, food: foodCard };
  dom.cards.replaceChildren(...items.map((item) => factories[state.tab](item)));
  if (focusedEditId) {
    dom.cards.querySelector(`[data-edit-id="${focusedEditId}"]`)?.focus({ preventScroll: true });
  }
}

function selectTab(tab) {
  if (!CATEGORY[tab]) return;
  state.tab = tab;
  history.replaceState(null, "", `#${tab}`);
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function makeField(definition, value) {
  const label = element("label", "field");
  const title = element("span", "", `${definition.label}${definition.required ? " *" : ""}`);
  const input = definition.multiline ? document.createElement("textarea") : document.createElement("input");
  input.name = definition.key;
  input.id = `field-${definition.key}`;
  const today = new Date();
  const localDate = new Date(today.getTime() - today.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 10);
  input.value = value ?? definition.defaultValue ?? (definition.key === "date" ? localDate : "");
  input.required = Boolean(definition.required);
  if (definition.maxlength) input.maxLength = definition.maxlength;
  if (definition.placeholder) input.placeholder = definition.placeholder;
  if (definition.type) input.type = definition.type;
  if (definition.type === "url") input.inputMode = "url";
  label.htmlFor = input.id;
  label.append(title, input);
  if (definition.help) label.append(element("small", "", definition.help));
  return label;
}

function requireIdentity() {
  if (state.userId) return true;
  openIdentity(false);
  return false;
}

function openEditor(kind, item = null) {
  if (!requireIdentity()) return;
  state.currentEdit = { kind, item: item ? { ...item } : null, conflict: null };
  state.editorDirty = false;
  const isNew = !item;
  dom.editKicker.textContent = isNew ? "新增到共享计划" : `由 ${item.updated_by === "system" ? "资料初始化" : item.updated_by} 最近更新`;
  dom.editTitle.textContent = `${isNew ? "添加" : "编辑"}${kind === "itinerary" ? "行程" : CATEGORY[kind].title}`;
  dom.saveButton.textContent = isNew ? "添加到计划" : "保存修改";
  dom.deleteButton.hidden = isNew;
  dom.editError.textContent = "";
  dom.editConflict.hidden = true;
  dom.conflictDetails.replaceChildren();
  dom.editFields.replaceChildren(
    ...FIELDS[kind].map((definition) => makeField(definition, item?.[definition.key])),
  );
  dom.editDialog.showModal();
  requestAnimationFrame(() => dom.editFields.querySelector("input, textarea")?.focus());
}

function closeEditor(force = false) {
  if (state.saving && !force) {
    showToast("正在保存，请稍候");
    return;
  }
  if (!force && state.editorDirty && !window.confirm("放弃还没有保存的修改吗？")) return;
  state.editorDirty = false;
  state.currentEdit = null;
  dom.editDialog.close();
}

function summarizeValue(value) {
  const text = value || "（空）";
  return text.length > 80 ? `${text.slice(0, 80)}…` : text;
}

function showConflict(remoteItem) {
  if (!state.currentEdit?.item) return;
  const baseline = state.currentEdit.item;
  state.currentEdit.conflict = { ...remoteItem };
  const changedFields = FIELDS[state.currentEdit.kind].filter(
    (field) => (baseline[field.key] || "") !== (remoteItem[field.key] || ""),
  );
  dom.conflictDetails.replaceChildren();
  if (!changedFields.length) {
    dom.conflictDetails.append(element("li", "", "对方保存了同一条内容的新版本。"));
  } else {
    for (const field of changedFields) {
      dom.conflictDetails.append(
        element("li", "", `${field.label}的最新内容：${summarizeValue(remoteItem[field.key])}`),
      );
    }
  }
  dom.editConflict.hidden = false;
  dom.editError.textContent = "请先选择载入最新内容，或明确保留自己的表单内容。";
  dom.editConflict.scrollIntoView({ behavior: "smooth", block: "center" });
}

function loadRemoteConflict() {
  const conflict = state.currentEdit?.conflict;
  if (!conflict || !state.currentEdit) return;
  state.currentEdit.item = { ...conflict };
  state.currentEdit.conflict = null;
  dom.editFields.replaceChildren(
    ...FIELDS[state.currentEdit.kind].map((definition) =>
      makeField(definition, conflict[definition.key]),
    ),
  );
  dom.editConflict.hidden = true;
  dom.conflictDetails.replaceChildren();
  dom.editError.textContent = "已载入最新内容，你可以继续编辑。";
  state.editorDirty = false;
}

function keepLocalConflict() {
  const conflict = state.currentEdit?.conflict;
  if (!conflict || !state.currentEdit) return;
  state.currentEdit.item = { ...conflict };
  state.currentEdit.conflict = null;
  dom.editConflict.hidden = true;
  dom.conflictDetails.replaceChildren();
  dom.editError.textContent = "已保留你的表单。再次点击保存将以这份内容覆盖最新版本。";
  state.editorDirty = true;
}

async function saveEditor(event) {
  event.preventDefault();
  if (!state.currentEdit || !requireIdentity()) return;
  const editContext = state.currentEdit;
  const { kind, item } = editContext;
  const payload = Object.fromEntries(new FormData(dom.editForm).entries());
  payload.city_id = item?.city_id || state.cityId;
  if (item) payload.version = item.version;
  state.saving = true;
  dom.saveButton.disabled = true;
  dom.editClose.disabled = true;
  dom.deleteButton.disabled = true;
  dom.editError.textContent = "";
  try {
    const url = item ? `/api/items/${kind}/${item.id}` : `/api/items/${kind}`;
    await requestJson(url, {
      method: item ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    state.editorDirty = false;
    closeEditor(true);
    await fetchSnapshot(true);
    showToast(item ? "修改已同步给大家" : "已添加到共享计划");
  } catch (error) {
    if (
      error.status === 409 &&
      error.data?.current &&
      item &&
      state.currentEdit === editContext
    ) {
      showConflict(error.data.current);
      await fetchSnapshot(true, true);
    } else if (error.data?.code === "PROJECT_LOCKED") {
      dom.editError.textContent = "项目访问已过期。请重新输入项目口令后再保存。";
      showProjectGate("项目访问已过期，请重新输入口令。", true);
    } else if (error.status === 401) {
      dom.editError.textContent = "身份已失效。表单内容仍保留，请重新选择 ID 后再保存。";
      state.userId = "";
      openIdentity(false);
    } else {
      dom.editError.textContent = `${error.message} 你的表单内容仍保留。`;
      if ([400, 413].includes(error.status)) {
        dom.editFields.querySelectorAll("input, textarea").forEach((field) => {
          field.setAttribute("aria-invalid", "true");
          field.setAttribute("aria-describedby", "edit-error");
        });
      }
    }
  } finally {
    state.saving = false;
    dom.saveButton.disabled = false;
    dom.editClose.disabled = false;
    dom.deleteButton.disabled = false;
  }
}

function askDelete() {
  if (!state.currentEdit?.item || state.saving) return;
  state.pendingDelete = {
    kind: state.currentEdit.kind,
    item: { ...state.currentEdit.item },
  };
  dom.confirmMessage.textContent = `删除“${itemName(state.pendingDelete.kind, state.pendingDelete.item)}”后，其他人也会看不到这条内容。`;
  dom.confirmDialog.showModal();
}

async function confirmDelete() {
  if (!state.pendingDelete || !requireIdentity()) return;
  state.deleteInFlight = true;
  dom.confirmDelete.disabled = true;
  dom.confirmCancel.disabled = true;
  const { kind, item } = state.pendingDelete;
  try {
    await requestJson(`/api/items/${kind}/${item.id}`, {
      method: "DELETE",
      body: JSON.stringify({ version: item.version }),
    });
    state.pendingDelete = null;
    dom.confirmDialog.close();
    state.editorDirty = false;
    closeEditor(true);
    await fetchSnapshot(true);
    showToast("内容已删除");
  } catch (error) {
    state.pendingDelete = null;
    dom.confirmDialog.close();
    if (error.data?.code === "PROJECT_LOCKED") {
      showProjectGate("项目访问已过期，请重新输入口令。", true);
    } else if (error.status === 409 && error.data?.current) {
      showConflict(error.data.current);
    } else {
      dom.editError.textContent = `${error.message} 请刷新后再试。`;
    }
    await fetchSnapshot(true, true);
  } finally {
    state.deleteInFlight = false;
    dom.confirmDelete.disabled = false;
    dom.confirmCancel.disabled = false;
  }
}

function renderUserChoices() {
  dom.userChoiceList.replaceChildren();
  dom.knownUsersSection.hidden = state.users.length === 0;
  dom.identityDivider.hidden = state.users.length === 0;
  dom.knownUsersCount.textContent = `${state.users.length} 人`;
  for (const user of state.users) {
    const button = element("button", "user-choice");
    button.type = "button";
    if (user.user_id === state.userId) button.classList.add("is-current");
    const avatar = element("span", "user-choice-avatar", user.user_id.slice(0, 1).toUpperCase());
    const copy = element("span", "user-choice-copy");
    copy.append(
      element("strong", "", user.user_id),
      element("small", "", user.user_id === state.userId ? "当前使用" : `最近使用于 ${formatTime(user.last_seen_at)}`),
    );
    button.append(avatar, copy, element("span", "user-choice-arrow", "›"));
    button.addEventListener("click", () => activateIdentity(user.user_id));
    dom.userChoiceList.append(button);
  }
}

async function loadUsers() {
  const { data } = await requestJson("/api/users");
  state.users = Array.isArray(data.users) ? data.users : [];
  renderUserChoices();
}

function openIdentity(switching) {
  if (!state.projectUnlocked) {
    showProjectGate();
    return;
  }
  dom.identityError.textContent = "";
  dom.userIdInput.value = switching ? "" : state.userId;
  dom.identityCancel.hidden = !state.userId;
  dom.identityTitle.textContent = state.userId ? "切换用户 ID" : "选择你的用户 ID";
  renderUserChoices();
  if (!dom.identityDialog.open) dom.identityDialog.showModal();
  const focusTarget = state.users.length ? dom.userChoiceList.querySelector("button") : dom.userIdInput;
  requestAnimationFrame(() => focusTarget?.focus());
}

function startSyncPolling() {
  clearInterval(state.syncTimer);
  state.syncTimer = setInterval(() => {
    if (!document.hidden) fetchSnapshot(false, true);
  }, 5000);
}

async function activateIdentity(userId) {
  const buttons = dom.identityDialog.querySelectorAll("button");
  buttons.forEach((button) => {
    button.disabled = true;
  });
  dom.identityError.textContent = "";
  try {
    const { data } = await requestJson("/api/session", {
      method: "POST",
      body: JSON.stringify({ user_id: userId }),
    });
    state.userId = data.user_id;
    dom.identityLabel.textContent = state.userId;
    await loadUsers();
    dom.identityDialog.close();
    await fetchSnapshot(true);
    restoreLockedDraft();
    startSyncPolling();
    showToast(`现在以 ${state.userId} 编辑`);
  } catch (error) {
    if (error.data?.code === "PROJECT_LOCKED") {
      showProjectGate("项目访问已过期，请重新输入口令。", true);
    } else {
      dom.identityError.textContent = error.message;
      dom.userIdInput.setAttribute("aria-invalid", "true");
      dom.userIdInput.setAttribute("aria-describedby", "identity-error");
    }
  } finally {
    buttons.forEach((button) => {
      button.disabled = false;
    });
  }
}

async function submitIdentity(event) {
  event.preventDefault();
  await activateIdentity(dom.userIdInput.value);
}

async function restoreIdentity() {
  state.userId = "";
  try {
    const { data } = await requestJson("/api/session");
    state.userId = data.user_id;
  } catch (error) {
    if (error.data?.code === "PROJECT_LOCKED") {
      showProjectGate("项目访问已过期，请重新输入口令。", true);
      return false;
    }
    if (error.status !== 401) showToast("暂时无法确认用户 ID");
  }
  dom.identityLabel.textContent = state.userId || "选择用户";
  return Boolean(state.userId);
}

function resetProjectState() {
  clearInterval(state.syncTimer);
  state.syncTimer = null;
  state.projectUnlocked = false;
  state.userId = "";
  state.users = [];
  state.cities = [];
  state.projectName = "旅游规划";
  state.items = { itinerary: [], attraction: [], transit: [], food: [] };
  state.activity = [];
  state.revision = null;
  state.snapshotRequestId += 1;
  render();
  dom.identityLabel.textContent = "选择用户";
  dom.tripStats.textContent = "项目尚未解锁";
  setSyncStatus("offline", "项目已锁定");
}

function captureEditorDraft() {
  if (!dom.editDialog.open || !state.currentEdit) return null;
  return {
    kind: state.currentEdit.kind,
    item: state.currentEdit.item ? { ...state.currentEdit.item } : null,
    values: Object.fromEntries(new FormData(dom.editForm).entries()),
  };
}

function restoreLockedDraft() {
  const draft = state.lockedDraft;
  if (!draft || !state.projectUnlocked || !state.userId) return;
  state.lockedDraft = null;
  openEditor(draft.kind, draft.item);
  for (const [name, value] of Object.entries(draft.values)) {
    const field = dom.editForm.elements.namedItem(name);
    if (field && "value" in field) field.value = value;
  }
  state.editorDirty = true;
  dom.editError.textContent = "项目已重新解锁，未保存的表单内容已恢复。";
}

function showProjectGate(message = "", preserveEditor = false) {
  document.body.classList.remove("is-booting");
  const draft = preserveEditor ? captureEditorDraft() : null;
  if (draft) state.lockedDraft = draft;
  else if (!preserveEditor) state.lockedDraft = null;
  resetProjectState();
  if (dom.confirmDialog.open) dom.confirmDialog.close();
  if (dom.editDialog.open) closeEditor(true);
  if (dom.identityDialog.open) dom.identityDialog.close();
  dom.projectError.textContent = message;
  dom.projectCodeInput.value = "";
  if (!dom.projectDialog.open) dom.projectDialog.showModal();
  requestAnimationFrame(() => dom.projectCodeInput.focus());
}

function setProjectGateChecking(checking) {
  const submit = dom.projectForm.querySelector('[type="submit"]');
  dom.projectCodeInput.disabled = checking;
  submit.disabled = checking;
  submit.textContent = checking ? "正在检查…" : "进入项目";
  if (!checking && dom.projectDialog.open) {
    requestAnimationFrame(() => dom.projectCodeInput.focus());
  }
}

async function enterUnlockedProject() {
  document.body.classList.remove("is-booting");
  state.projectUnlocked = true;
  if (dom.projectDialog.open) dom.projectDialog.close();
  setSyncStatus("syncing", "正在连接");
  try {
    await loadUsers();
    const hasIdentity = await restoreIdentity();
    if (!state.projectUnlocked) return;
    if (!hasIdentity) {
      openIdentity(false);
      return;
    }
    await fetchSnapshot(true);
    restoreLockedDraft();
    startSyncPolling();
  } catch (error) {
    if (error.data?.code === "PROJECT_LOCKED") {
      showProjectGate("项目访问已过期，请重新输入口令。", true);
    }
    else showProjectGate("暂时无法读取项目，请稍后重试。");
  }
}

async function submitProject(event) {
  event.preventDefault();
  const submit = dom.projectForm.querySelector('[type="submit"]');
  submit.disabled = true;
  dom.projectError.textContent = "";
  try {
    await requestJson("/api/project-session", {
      method: "POST",
      body: JSON.stringify({ project_code: dom.projectCodeInput.value }),
    });
    await enterUnlockedProject();
  } catch (error) {
    dom.projectError.textContent = error.message;
    dom.projectCodeInput.setAttribute("aria-invalid", "true");
    dom.projectCodeInput.setAttribute("aria-describedby", "project-error");
    dom.projectCodeInput.select();
  } finally {
    submit.disabled = false;
  }
}

async function lockProject() {
  if (state.editorDirty && !window.confirm("退出项目会放弃尚未保存的修改，确定退出吗？")) return;
  dom.projectLockButton.disabled = true;
  try {
    await requestJson("/api/project-session", {
      method: "DELETE",
      body: JSON.stringify({}),
    });
    showProjectGate();
  } catch (error) {
    dom.identityError.textContent = error.message;
  } finally {
    dom.projectLockButton.disabled = false;
  }
}

async function sharePlan() {
  const shareData = {
    title: "旅游规划",
    text: "一起编辑这份旅行清单",
    url: location.href.split("#")[0],
  };
  if (navigator.share) {
    try {
      await navigator.share(shareData);
      return;
    } catch (error) {
      if (error.name === "AbortError") return;
    }
  }
  try {
    await navigator.clipboard.writeText(shareData.url);
    showToast("链接已复制，可以发给同行伙伴了");
  } catch {
    const input = document.createElement("input");
    input.value = shareData.url;
    document.body.append(input);
    input.select();
    document.execCommand("copy");
    input.remove();
    showToast("链接已复制，可以发给同行伙伴了");
  }
}

function registerWebMcpTools() {
  const context = document.modelContext;
  if (!context?.registerTool) return;
  const lifecycle = new AbortController();
  const report = (error) => console.warn("WebMCP tool registration failed", error);
  try {
    void Promise.resolve(
      context.registerTool(
        {
          name: "get_trip_plan_snapshot",
          title: "读取旅行计划",
          description: "读取当前共享项目中的行程、景点、公共交通和美食条目。",
          inputSchema: { type: "object", properties: {}, additionalProperties: false },
          annotations: { readOnlyHint: true, untrustedContentHint: true },
          execute() {
            if (!state.projectUnlocked) throw new Error("请先在页面中输入项目口令。");
            return {
              revision: state.revision,
              itinerary: state.items.itinerary,
              attraction: state.items.attraction,
              transit: state.items.transit,
              food: state.items.food,
            };
          },
        },
        { signal: lifecycle.signal },
      ),
    ).catch(report);
    void Promise.resolve(
      context.registerTool(
        {
          name: "add_attraction_to_trip_plan",
          title: "添加当前城市景点",
          description: "以当前用户身份向共享计划添加一个景点，并刷新可见列表。",
          inputSchema: {
            type: "object",
            properties: {
              name: { type: "string", minLength: 1, maxLength: 60 },
              district: { type: "string", maxLength: 30 },
              category: { type: "string", maxLength: 30 },
              description: { type: "string", maxLength: 600 },
              duration: { type: "string", maxLength: 40 },
              transport: { type: "string", maxLength: 160 },
              link: { type: "string", maxLength: 500 },
            },
            required: ["name"],
            additionalProperties: false,
          },
          annotations: { readOnlyHint: false, untrustedContentHint: true },
          async execute(input) {
            if (!state.projectUnlocked) throw new Error("请先在页面中输入项目口令。");
            if (!state.userId) throw new Error("请先在页面中选择用户 ID。");
            const payload = { city_id: state.cityId };
            for (const field of FIELDS.attraction) payload[field.key] = input[field.key] || "";
            const { data } = await requestJson("/api/items/attraction", {
              method: "POST",
              body: JSON.stringify(payload),
            });
            await fetchSnapshot(true, true);
            return { revision: data.revision, id: data.item.id, name: data.item.name };
          },
        },
        { signal: lifecycle.signal },
      ),
    ).catch(report);
    void Promise.resolve(
      context.registerTool(
        {
          name: "add_itinerary_to_trip_plan",
          title: "添加当前城市行程",
          description: "以当前用户身份添加一项按日期安排的共享行程，并刷新可见列表。",
          inputSchema: {
            type: "object",
            properties: {
              date: { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$" },
              start_time: { type: "string", pattern: "^(?:[01]\\d|2[0-3]):[0-5]\\d$" },
              title: { type: "string", minLength: 1, maxLength: 80 },
              category: { type: "string", maxLength: 30 },
              location: { type: "string", maxLength: 120 },
              notes: { type: "string", maxLength: 600 },
              link: { type: "string", maxLength: 500 },
            },
            required: ["date", "title"],
            additionalProperties: false,
          },
          annotations: { readOnlyHint: false, untrustedContentHint: true },
          async execute(input) {
            if (!state.projectUnlocked) throw new Error("请先在页面中输入项目口令。");
            if (!state.userId) throw new Error("请先在页面中选择用户 ID。");
            const payload = { city_id: state.cityId };
            for (const field of FIELDS.itinerary) payload[field.key] = input[field.key] || "";
            const { data } = await requestJson("/api/items/itinerary", {
              method: "POST",
              body: JSON.stringify(payload),
            });
            await fetchSnapshot(true, true);
            return { revision: data.revision, id: data.item.id, title: data.item.title };
          },
        },
        { signal: lifecycle.signal },
      ),
    ).catch(report);
  } catch {
    report();
  }
  window.addEventListener("pagehide", (event) => {
    if (!event.persisted) lifecycle.abort();
  });
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => selectTab(button.dataset.tab));
});
document.querySelector("#city-select").addEventListener("change", (event) => { state.cityId = event.target.value; render(); });
dom.addButton.addEventListener("click", () => openEditor(state.tab));
dom.identityButton.addEventListener("click", () => openIdentity(true));
dom.projectForm.addEventListener("submit", submitProject);
dom.projectCodeInput.addEventListener("input", () => {
  dom.projectCodeInput.removeAttribute("aria-invalid");
  dom.projectCodeInput.removeAttribute("aria-describedby");
  dom.projectError.textContent = "";
});
dom.projectDialog.addEventListener("cancel", (event) => event.preventDefault());
dom.projectLockButton.addEventListener("click", lockProject);
dom.identityForm.addEventListener("submit", submitIdentity);
dom.userIdInput.addEventListener("input", () => {
  dom.userIdInput.removeAttribute("aria-invalid");
  dom.userIdInput.removeAttribute("aria-describedby");
  dom.identityError.textContent = "";
});
dom.identityCancel.addEventListener("click", () => dom.identityDialog.close());
dom.identityDialog.addEventListener("cancel", (event) => {
  if (!state.userId) event.preventDefault();
});
dom.editForm.addEventListener("submit", saveEditor);
dom.editForm.addEventListener("input", () => {
  state.editorDirty = true;
  dom.editForm.querySelectorAll('[aria-invalid="true"]').forEach((field) => {
    field.removeAttribute("aria-invalid");
    field.removeAttribute("aria-describedby");
  });
});
dom.editClose.addEventListener("click", () => closeEditor());
dom.editDialog.addEventListener("cancel", (event) => {
  if (state.saving) {
    event.preventDefault();
    showToast("正在保存，请稍候");
    return;
  }
  if (state.editorDirty) {
    event.preventDefault();
    closeEditor();
  }
});
dom.deleteButton.addEventListener("click", askDelete);
dom.conflictLoad.addEventListener("click", loadRemoteConflict);
dom.conflictOverwrite.addEventListener("click", keepLocalConflict);
dom.confirmCancel.addEventListener("click", () => {
  if (!state.deleteInFlight) {
    state.pendingDelete = null;
    dom.confirmDialog.close();
  }
});
dom.confirmDialog.addEventListener("cancel", (event) => {
  if (state.deleteInFlight) event.preventDefault();
  else state.pendingDelete = null;
});
dom.confirmDelete.addEventListener("click", confirmDelete);
dom.shareButton.addEventListener("click", sharePlan);
window.addEventListener("hashchange", () => {
  const tab = location.hash.slice(1);
  if (CATEGORY[tab]) {
    state.tab = tab;
    render();
  }
});
window.addEventListener("beforeunload", (event) => {
  if (!state.editorDirty) return;
  event.preventDefault();
  event.returnValue = "";
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) fetchSnapshot(false, true);
});

async function start() {
  registerWebMcpTools();
  showProjectGate();
  setProjectGateChecking(true);
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 8000);
  try {
    const { data } = await requestJson("/api/project-session", { signal: controller.signal });
    setProjectGateChecking(false);
    if (data.unlocked) await enterUnlockedProject();
  } catch {
    setProjectGateChecking(false);
    dom.projectError.textContent = "暂时无法连接项目，请稍后重试。";
  } finally {
    window.clearTimeout(timeout);
  }
}

start();
