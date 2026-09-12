"use strict";

const CATEGORY = {
  itinerary: {
    title: "行程规划",
    kicker: "按天安排",
    description: "先确认想去的地点，再按天安排并核对路线。",
    addLabel: "添加行程",
    accent: "#315ca8",
  },
  attraction: {
    title: "地点",
    kicker: "一起挑选",
    description: "地点信息和攻略链接都可以共同修改。",
    addLabel: "添加地点",
    accent: "#f05b3f",
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
    { key: "time_block", label: "偏好时段", type: "select", options: (typeof window !== "undefined" ? window.TripDaily?.options : null) || [{value:"",label:"待安排"}] },
    { key: "duration_minutes", label: "建议停留（分钟）", type: "number", placeholder: "留空表示待确认" },
    { key: "priority", label: "优先级", type: "select", defaultValue: "preferred", options: [{value:"preferred",label:"想去"},{value:"must",label:"必去"},{value:"optional",label:"可选"}] },
    { key: "visit_kind", label: "行程类别", help: "区分游览、用餐、休息和交通，用于计算每日安排。多地点行程需拆分后才能逐段规划路线。", type: "select", defaultValue: "place", options: [{value:"place",label:"地点"},{value:"attraction",label:"游览景点"},{value:"meal",label:"用餐"},{value:"rest",label:"休息"},{value:"transit",label:"交通枢纽"},{value:"legacy",label:"多地点行程（需拆分）"}] },
    { key: "opening_start", label: "开放时间（可选）", type: "time", help: "不知道可先保存，再点行程卡片上的 AI 补充开放时间；AI 结果仅供参考。" },
    { key: "opening_end", label: "关闭时间（可选）", type: "time" },
    { key: "title", label: "安排名称", required: true, maxlength: 80, placeholder: "例如：外滩日落散步" },
    { key: "category", label: "类型", maxlength: 30, placeholder: "例如：地点 / 用餐 / 交通" },
    { key: "location", label: "地点", maxlength: 120, placeholder: "例如：外滩观景平台" },
    {
      key: "attraction_names", label: "关联地点", attractionNames: true, multiline: true, maxlength: 1000,
      placeholder: "例如：故宫博物院、景山公园",
      help: "用顿号、逗号或换行分隔，最多 12 个地点。留空时按地点自动识别游览地点；路口、普通街道、路线等不会自动收录。可点选已有地点，或明确填写历史街区等游览目的地；保存后缺少的地点会自动加入清单。",
    },
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
    { key: "name", label: "地点名称", required: true, maxlength: 60, placeholder: "例如：外滩风景区" },
    {
      key: "scenic_rating", label: "景区级别", type: "select",
      options: [{ value: "", label: "自动匹配 / 暂不填写" }, { value: "4A", label: "4A" }, { value: "5A", label: "5A" }],
      help: "可由 AI 补充或手动选择 4A、5A，保存后直接显示等级；留空时尝试自动匹配。",
    },
    { key: "district", label: "所在区县", maxlength: 30, placeholder: "例如：黄浦区" },
    { key: "category", label: "主分类", categoryKind: "attraction" },
    { key: "tags", label: "标签", tagsKind: "attraction", multiline: true, maxlength: 1000, placeholder: "例如：皇家，亲子，夜景" },
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
  food: [
    { key: "name", label: "美食名称", required: true, maxlength: 60, placeholder: "例如：生煎馒头" },
    { key: "category", label: "主分类", categoryKind: "food" },
    { key: "cuisine", label: "菜系", maxlength: 40, placeholder: "例如：本帮菜、川菜、粤菜", help: "特色菜肴请填写具体菜系，也可用 AI 补充；不确定时留空。" },
    { key: "tags", label: "标签", tagsKind: "food", multiline: true, maxlength: 1000, placeholder: "例如：早餐，夜宵，伴手礼" },
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
  projectId: window.TripProject.id,
  cityId: window.TripUI.initialCity(),
  cities: [],
  projectName: "旅游规划",
  tab: ["itinerary", "attraction", "food", "activity"].includes(location.hash.slice(1))
    ? location.hash.slice(1)
    : "itinerary",
  items: { itinerary: [], attraction: [], transit: [], food: [] },
  cacheInfo: null,
  travelGuidance: [],
  projectItinerary: null,
  activity: [],
  users: [],
  revision: null,
  snapshotEtag: null,
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
  editReturn: document.querySelector("#edit-return"),
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
    headers: window.TripProject.headers({
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    }),
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
  const requestedCity = state.cityId;
  if (!quiet) setSyncStatus("syncing", "正在同步");
  try {
    const headers = {};
    if (!force && state.snapshotEtag) headers["If-None-Match"] = state.snapshotEtag;
    const { response, data } = await requestJson(`/api/snapshot?city_id=${encodeURIComponent(requestedCity)}`, { headers });
    if (requestId !== state.snapshotRequestId || requestedCity !== state.cityId || !state.projectUnlocked || !state.userId) return;
    if (
      response.status !== 304 &&
      data &&
      (state.revision === null || data.revision >= state.revision)
    ) {
      const hadRevision = state.revision !== null;
      const changedRemotely = hadRevision && data.revision > state.revision;
      state.revision = data.revision;
      state.snapshotEtag = response.headers.get("ETag");
      state.cities = data.cities || [];
      state.projectName = data.project?.name || "旅游规划";
      window.TripTaxonomy.configure(data.taxonomy);
      if (!state.cities.some((city) => city.id === state.cityId)) {
        await window.TripUI.switchCity(state.cities[0]?.id || "");
        return;
      }
      state.items = {
        itinerary: [],
        attraction: [],
        transit: [],
        food: [],
        ...(data.items || {}),
      };
      state.activity = data.activity || [];
      state.cacheInfo = data.cache_info || null;
      state.travelGuidance = data.travel_guidance || [];
      state.projectItinerary = data.project_itinerary || null;
      state.dailyPlans = data.daily_plans || [];
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
    if (error.status === 404 && requestedCity) {
      state.cityId = "";
      state.snapshotEtag = null;
      state.revision = null;
      state.items = { itinerary: [], attraction: [], transit: [], food: [] };
      state.cacheInfo = null;
      state.activity = [];
      render();
      await fetchSnapshot(true, quiet);
      return;
    }
    if (error.data?.code === "PROJECT_LOCKED") {
      showProjectGate("项目访问已过期，请重新输入口令。", true);
      return;
    }
    state.previouslyOffline = true;
    setSyncStatus("offline", "暂时离线");
    if (!quiet) showToast(error.message || "暂时无法同步计划");
  }
}

function addTag(container, text, accent = false, variant = "") {
  if (!text) return;
  const tag = element("span", `tag${accent ? " is-accent" : ""}${variant ? ` ${variant}` : ""}`, text);
  if (variant === "is-category") tag.setAttribute("aria-label", `类别：${text}`);
  if (variant === "is-place-meta") tag.setAttribute("aria-label", `所在区县：${text}`);
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

function appendAttractionHighlights(container, value, links) {
  const text = typeof value === "string" ? value : "";
  const names = [...new Set(links.flatMap(link => [link.matched_name, link.name]).filter(name => typeof name === "string" && name.length))]
    .sort((a, b) => b.length - a.length);
  let cursor = 0;
  while (cursor < text.length) {
    let offset = text.length, match = "";
    for (const name of names) {
      const index = text.indexOf(name, cursor);
      if (index >= 0 && index < offset) { offset = index; match = name; }
    }
    if (!match) { container.append(document.createTextNode(text.slice(cursor))); break; }
    if (offset > cursor) container.append(document.createTextNode(text.slice(cursor, offset)));
    container.append(element("mark", "linked-attraction-highlight", match));
    cursor = offset + match.length;
  }
}

function itineraryCard(item, { showTimeBlock = true } = {}) {
  const card = element("article", "plan-card itinerary-card");
  card.style.setProperty("--card-accent", CATEGORY.itinerary.accent);
  const body = element("div", "card-body");
  const top = element("div", "card-topline");
  const heading = element("div");
  const tags = element("div", "tags");
  if (showTimeBlock) addTag(tags, window.TripDaily?.label(item.time_block) || "待安排", true);
  addTag(tags, item.duration_minutes == null ? "待补充建议时长" : `${item.duration_source === "ai_estimate" ? "AI 推荐" : item.duration_source === "user" ? "停留" : "建议"} ${item.duration_minutes} 分钟`);
  if (item.is_backup) addTag(tags, "备选");
  addTag(tags, `优先级：${({must:"必去",preferred:"想去",optional:"可选"})[item.priority] || "想去"}`);
  addTag(tags, item.category);
  const links = Array.isArray(item.linked_attractions) ? item.linked_attractions.filter(link => link && link.id && typeof link.name === "string") : [];
  const title = element("h3");
  appendAttractionHighlights(title, item.title, links);
  heading.append(tags, title);
  top.append(heading, editButton("itinerary", item));
  body.append(top);
  if (item.notes) body.append(element("p", "card-description", item.notes));
  const details = element("dl", "details");
  if (item.location) {
    const row = element("div", "detail-row"), location = element("dd");
    appendAttractionHighlights(location, item.location, links);
    row.append(element("dt", "", "地点"), location);
    details.append(row);
  }
  const hours = item.opening_start && item.opening_end ? `${item.opening_start}–${item.opening_end}` : "待补充";
  const hoursRow = element("div", "detail-row");
  hoursRow.append(element("dt", "", "开放时间"), element("dd", "", `${hours}${item.opening_note ? `（${item.opening_note}）` : ""}`));
  details.append(hoursRow);
  if (item.visit_kind === "legacy") {
    const legacyRow = element("div", "detail-row");
    legacyRow.append(element("dt", "", "行程状态"), element("dd", "", "包含多个地点，请在确定当天行程中拆分后规划路线。")); details.append(legacyRow);
  }
  body.append(details);
  if (links.length) {
    const related = element("div", "itinerary-attractions");
    related.append(element("span", "itinerary-attractions-label", "已关联地点"));
    for (const link of links) {
      const button = element("button", "linked-attraction-chip", link.name);
      button.type = "button";
      button.setAttribute("aria-label", `查看并编辑地点：${link.name}`);
      button.addEventListener("click", () => {
        const attraction = (state.items.attraction || []).find(candidate => candidate.id === link.id && candidate.city_id === item.city_id);
        if (attraction) openEditor("attraction", attraction);
        else showToast("地点已发生变化，请刷新后重试。");
      });
      related.append(button);
    }
    body.append(related);
  }
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
  addTag(tags, item.category, false, "is-category");
  addTag(tags, item.district, false, "is-place-meta");
  const ratingBadge = scenicRatingBadge(item);
  if (ratingBadge) tags.append(ratingBadge);
  const itineraryRefs = Array.isArray(item.itinerary_refs) ? item.itinerary_refs : [];
  const scheduled = item.in_itinerary === true || itineraryRefs.length > 0;
  tags.append(element("span", `tag itinerary-status ${scheduled ? "is-scheduled" : "is-unscheduled"}`, scheduled ? "已排行程" : "未排行程"));
  for (const tag of window.TripTaxonomy.effectiveTags(item)) addTag(tags, tag, false, "is-place-label");
  heading.append(tags, element("h3", "", item.name));
  top.append(heading, editButton("attraction", item));
  body.append(top);
  if (item.description) body.append(element("p", "card-description", item.description));
  const details = element("dl", "details");
  if (scheduled) {
    const dates = [...new Set(itineraryRefs.map(ref => ref.date).filter(date => typeof date === "string" && date))].sort();
    addDetail(details, "行程安排", `${itineraryRefs.length ? `${itineraryRefs.length} 次` : "已安排"}${dates.length ? ` · ${dates.join("、")}` : ""}`);
  }
  addDetail(details, "建议时长", item.duration);
  addDetail(details, "到达方式", item.transport);
  body.append(details);
  card.append(body);
  appendFooter(card, item, "打开攻略");
  const city = itemCity(item);
  const navigation = appExternalAnchor("高德导航 ↗", window.TripLinks.attractionUrl(item, city));
  const actions = element("span", "place-link-actions");
  actions.append(navigation);
  card.lastElementChild.append(actions);
  return card;
}

function scenicRatingState(item) {
  const rating = ["4A", "5A"].includes(item?.scenic_rating) ? item.scenic_rating : "";
  return { rating, label: rating ? `${rating} 景区` : "" };
}

function scenicRatingBadge(item) {
  const { rating, label } = scenicRatingState(item);
  if (!rating) return null;
  return element("span", "tag scenic-rating", label);
}

function externalAnchor(label, url) {
  const link = element("a", "map-link", label);
  link.href = validExternalUrl(url);
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

function appExternalAnchor(label, url) {
  const link = externalAnchor(label, url);
  if (window.TripLinks.isMobile(window.navigator)) link.target = "_self";
  return link;
}

function itemCity(item) {
  const cityId = item.city_id || state.cityId;
  return state.cities.find((city) => city.id === cityId) || { name: "" };
}

function amapUrl(city, keyword, options) {
  return window.TripLinks.amapSearch(city, keyword, options);
}

function foodCard(item) {
  const card = element("article", "plan-card");
  card.style.setProperty("--card-accent", CATEGORY.food.accent);
  const body = element("div", "card-body");
  const top = element("div", "card-topline");
  const heading = element("div");
  const tags = element("div", "tags");
  addTag(tags, item.category, false, "is-category");
  for (const tag of window.TripTaxonomy.effectiveTags(item)) addTag(tags, tag, false, "is-place-label");
  heading.append(tags, element("h3", "", item.name));
  top.append(heading, editButton("food", item));
  body.append(top);
  if (item.description) body.append(element("p", "card-description", item.description));
  const details = element("dl", "details");
  if (item.category === "特色菜肴") addDetail(details, "菜系", item.cuisine || "待补充，可在编辑中使用 AI 补充");
  addDetail(details, "推荐体验", item.where_to_try);
  addDetail(details, "点单提示", item.tip);
  body.append(details);
  card.append(body);
  appendFooter(card, item, "参考资料");
  const searchText = window.TripLinks.foodSearchText(itemCity(item), item);
  const actions = element("div", "food-search-actions");
  const links = element("div", "place-link-actions");
  const copy = element("button", "link-button", "复制搜索词");
  copy.type = "button";
  copy.setAttribute("aria-label", `复制美食搜索词：${searchText}`);
  copy.addEventListener("click", () => copyFoodSearch(searchText));
  links.append(copy);
  actions.append(links, element("p", "food-search-keyword", `搜索词：${searchText}`));
  card.append(actions);
  return card;
}

async function copyFoodSearch(value) {
  try {
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
    } else {
      // LAN HTTP previews may not expose the Clipboard API. Keep a synchronous
      // copy fallback inside the user's click, and leave visible selectable text.
      const input = element("textarea", "clipboard-helper");
      input.value = value;
      input.readOnly = true;
      input.setAttribute("aria-label", "待复制的美食搜索词");
      const focused = document.activeElement;
      document.body.append(input);
      try {
        input.select();
        input.setSelectionRange(0, input.value.length);
        if (!document.execCommand("copy")) throw new Error("Copy unavailable");
      } finally { input.remove(); focused?.focus(); }
    }
    showToast("搜索词已复制，可粘贴到美团等应用的搜索栏");
  } catch { showToast("未能自动复制，请长按卡片上的搜索词复制"); }
}

function renderItinerary(items) {
  if (window.TripDaily) return window.TripDaily.render(items);
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
      if (/^\d{4}-\d{2}-\d{2}$/.test(currentDate)) {
        const date = currentDate;
        const replan = element("button", "link-button ai-replan-day", "AI 重新规划这一天");
        replan.type = "button";
        replan.setAttribute("aria-label", `AI 重新规划 ${date} 这一天`);
        replan.addEventListener("click", () => window.TripUI.openReplan("replace_day", date));
        heading.append(replan);
        const maps = element("button", "link-button", "地图与路线");
        maps.type = "button";
        maps.addEventListener("click", () => window.TripMaps?.open(date));
        heading.append(maps);
      }
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
  const kindLabel = { itinerary: "行程", attraction: "地点", transit: "线路", food: "美食" };
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
  window.TripExport?.sync();
  window.TripEditorAI?.sync();
  window.TripTypeFilter?.render();
  window.TripBatch.renderToolbar();
  window.TripUI.renderCity();
  window.TripUI.renderCacheNotice();
  window.TripGuidance?.render();
  window.TripMaps?.sync();
  window.TripCities?.render();
  document.querySelector("#project-name").textContent = state.projectName;
  const focusedEditId = document.activeElement?.dataset?.editId;
  const focusedBatchId = document.activeElement?.dataset?.batchId;
  const meta = CATEGORY[state.tab];
  dom.sectionKicker.textContent = meta.kicker;
  dom.sectionTitle.textContent = meta.title;
  dom.sectionDescription.textContent = meta.description;
  const count = (kind) => state.items[kind].filter((item) => item.city_id === state.cityId).length;
  dom.tripStats.textContent = `${count("itinerary")} 项行程 · ${count("attraction")} 个地点 · ${count("food")} 种美食`;
  document.querySelector(".eyebrow").textContent = `共享旅行清单 · ${state.cities.find((city) => city.id === state.cityId)?.name || "选择城市"}`;
  dom.identityLabel.textContent = state.userId || "设置 ID";

  document.querySelectorAll(".nav-item").forEach((button) => {
    const active = button.dataset.tab === state.tab;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });

  const showingActivity = state.tab === "activity";
  document.getElementById("ai-replan-all").hidden = state.tab !== "itinerary" || !count("itinerary");
  dom.cards.hidden = showingActivity;
  dom.cards.classList.toggle("is-itinerary", state.tab === "itinerary");
  dom.activityPanel.hidden = !showingActivity;
  dom.addButton.hidden = showingActivity;
  if (showingActivity) {
    renderActivity();
    return;
  }

  dom.addButton.lastElementChild.textContent = meta.addLabel;
  dom.cards.setAttribute("aria-busy", "false");
  const cityItems = (state.items[state.tab] || []).filter((item) => item.city_id === state.cityId);
  const items = window.TripTypeFilter?.filter(cityItems) || cityItems;
  if (state.tab === "itinerary" && window.TripDaily) { renderItinerary(items); return; }
  if (!items.length) {
    dom.cards.replaceChildren(window.TripTypeFilter?.emptyState() || emptyState(state.tab));
    return;
  }
  if (state.tab === "itinerary") {
    renderItinerary(items);
    return;
  }
  const factories = { attraction: attractionCard, food: foodCard };
  dom.cards.replaceChildren(...items.map((item) => window.TripBatch.decorate(factories[state.tab](item), state.tab, item)));
  if (focusedEditId) {
    dom.cards.querySelector(`[data-edit-id="${focusedEditId}"]`)?.focus({ preventScroll: true });
  }
  if (focusedBatchId) {
    dom.cards.querySelector(`[data-batch-id="${CSS.escape(focusedBatchId)}"]`)?.focus({ preventScroll: true });
  }
}

function selectTab(tab) {
  if (!CATEGORY[tab] || !window.TripBatch.canNavigate()) return;
  state.tab = tab;
  history.replaceState(null, "", `#${tab}`);
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function parseAttractionNames(value) {
  const values = Array.isArray(value) ? value : typeof value === "string" ? value.split(/[、,，;；\r\n]+/) : [];
  return [...new Set(values.filter(name => typeof name === "string").map(name => name.trim()).filter(Boolean))];
}

function attractionNamesError(value) {
  const names = parseAttractionNames(value);
  if (names.length > 12) return "每项行程最多关联 12 个地点。";
  if (names.some(name => [...name].length > 60)) return "每个关联地点名称不能超过 60 个字。";
  if (names.some(name => /[\u0000-\u001f\u007f]/.test(name))) return "关联地点名称不能包含控制字符。";
  return "";
}

function enhanceAttractionNames(wrapper, input, cityId) {
  const suggestions = element("div", "attraction-name-suggestions");
  suggestions.setAttribute("role", "group");
  suggestions.setAttribute("aria-label", "点选已有地点");
  const names = [...new Set((state.items.attraction || []).filter(item => item.city_id === cityId).map(item => item.name).filter(name => typeof name === "string" && name.trim()))];
  const refresh = () => {
    input.setCustomValidity(attractionNamesError(input.value));
    const selected = new Set(parseAttractionNames(input.value));
    for (const button of suggestions.querySelectorAll("button")) button.setAttribute("aria-pressed", String(selected.has(button.dataset.attractionName)));
  };
  for (const name of names) {
    const button = element("button", "linked-attraction-chip", name);
    button.type = "button";
    button.dataset.attractionName = name;
    button.addEventListener("click", () => {
      if (input.disabled || state.saving) return;
      const selected = parseAttractionNames(input.value);
      input.value = (selected.includes(name) ? selected.filter(value => value !== name) : [...selected, name]).join("、");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    suggestions.append(button);
  }
  input.addEventListener("input", refresh);
  input.addEventListener("change", refresh);
  refresh();
  if (names.length) {
    wrapper.append(element("small", "", "点选已有地点（可多选）"), suggestions);
  }
}

function makeField(definition, value, context = {}) {
  const interactive = definition.tagsKind || definition.attractionNames;
  const label = element(interactive ? "div" : "label", "field");
  const title = element(interactive ? "label" : "span", "", `${definition.label}${definition.required ? " *" : ""}`);
  const options = definition.categoryKind ? window.TripTaxonomy.categoryOptions(definition.categoryKind) : definition.options;
  const isSelect = Array.isArray(options);
  const input = document.createElement(isSelect ? "select" : definition.multiline ? "textarea" : "input");
  input.name = definition.key;
  input.id = `field-${definition.key}`;
  if (definition.categoryKind) input.className = "place-category-select";
  const today = new Date();
  const localDate = new Date(today.getTime() - today.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 10);
  if (isSelect) {
    for (const choice of options) {
      if (definition.key === "visit_kind" && choice.value === "legacy" && value !== "legacy") continue;
      const option = element("option", "", choice.label);
      option.value = choice.value;
      input.append(option);
    }
    input.value = options.some((choice) => choice.value === value) ? value : definition.defaultValue ?? "";
  } else input.value = definition.attractionNames ? parseAttractionNames(value).join("、") : definition.tagsKind ? window.TripTaxonomy.formatTags(value) : value ?? definition.defaultValue ?? (definition.key === "date" ? localDate : "");
  input.required = Boolean(definition.required);
  if (definition.maxlength) input.maxLength = definition.maxlength;
  if (definition.placeholder) input.placeholder = definition.placeholder;
  if (definition.type && !isSelect) input.type = definition.type;
  if (definition.type === "url") input.inputMode = "url";
  label.htmlFor = input.id;
  label.append(title, input);
  if (definition.tagsKind) {
    title.htmlFor = input.id;
    window.TripTaxonomy.enhanceTags(label, input, definition.tagsKind);
  }
  if (definition.help) label.append(element("small", "", definition.help));
  if (definition.key === "time_block") {
    const hint = element("small", "", "时段已锁定。请先在“确定当天行程”中解除时段锁定，再修改时段；其他信息仍可编辑。");
    hint.id = "time-block-lock-hint"; hint.hidden = true; label.append(hint);
  }
  if (definition.attractionNames) {
    title.htmlFor = input.id;
    enhanceAttractionNames(label, input, context.cityId || state.currentEdit?.item?.city_id || state.cityId);
  }
  return label;
}

function syncEditorTimeLock() {
  if (state.currentEdit?.kind !== "itinerary") return;
  const input = dom.editFields.querySelector('[name="time_block"]');
  const hint = dom.editFields.querySelector('[id="time-block-lock-hint"]');
  if (!input) return;
  const locked = Boolean(state.currentEdit.item?.block_locked);
  input.disabled = locked;
  if (locked) input.value = state.currentEdit.item.time_block || "";
  if (hint) hint.hidden = !locked;
}

function requireIdentity() {
  if (state.userId) return true;
  openIdentity(false);
  return false;
}

function openEditor(kind, item = null) {
  if (!window.TripBatch.canNavigate()) return;
  if (!requireIdentity()) return;
  state.currentEdit = { kind, item: item ? { ...item } : null, conflict: null };
  state.editorDirty = false;
  const isNew = !item;
  dom.editKicker.textContent = isNew ? "新增到共享计划" : `由 ${item.updated_by === "system" ? "资料初始化" : item.updated_by} 最近更新`;
  dom.editTitle.textContent = `${isNew ? "添加" : "编辑"}${kind === "itinerary" ? "行程" : CATEGORY[kind].title}`;
  dom.saveButton.textContent = isNew ? "添加到计划" : "保存修改";
  dom.deleteButton.hidden = isNew;
  dom.editReturn.hidden = !["attraction", "food"].includes(kind);
  dom.editError.textContent = "";
  dom.editConflict.hidden = true;
  dom.conflictDetails.replaceChildren();
  dom.editFields.replaceChildren(
    ...FIELDS[kind].map((definition) => makeField(definition, !item && kind === "itinerary" && definition.key === "date" ? (window.TripDaily?.selectedDate?.() || undefined) : item?.[definition.key])),
  );
  syncEditorTimeLock();
  dom.editDialog.showModal();
  window.TripEditorAI?.open();
  requestAnimationFrame(() => dom.editFields.querySelector("input, textarea, select")?.focus());
}

function closeEditor(force = false) {
  if (state.saving && !force) {
    showToast("正在保存，请稍候");
    return;
  }
  if (!force && state.editorDirty && !window.confirm("放弃还没有保存的修改吗？")) return;
  window.TripEditorAI?.close();
  state.editorDirty = false;
  state.currentEdit = null;
  dom.editDialog.close();
}

function summarizeValue(value) {
  const text = (Array.isArray(value) ? value.join("、") : value) || "（空）";
  return text.length > 80 ? `${text.slice(0, 80)}…` : text;
}

function showConflict(remoteItem) {
  if (!state.currentEdit?.item) return;
  const baseline = state.currentEdit.item;
  state.currentEdit.conflict = { ...remoteItem };
  window.TripEditorAI?.invalidate("发现同时编辑，请先处理冲突，再补充基本信息。");
  const changedFields = FIELDS[state.currentEdit.kind].filter(
    (field) => JSON.stringify(baseline[field.key] || "") !== JSON.stringify(remoteItem[field.key] || ""),
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
  syncEditorTimeLock();
  state.editorDirty = false;
  window.TripEditorAI?.open();
}

function keepLocalConflict() {
  const conflict = state.currentEdit?.conflict;
  if (!conflict || !state.currentEdit) return;
  state.currentEdit.item = { ...conflict };
  state.currentEdit.conflict = null;
  dom.editConflict.hidden = true;
  dom.conflictDetails.replaceChildren();
  dom.editError.textContent = "已保留你的表单。再次点击保存将以这份内容覆盖最新版本。";
  syncEditorTimeLock();
  state.editorDirty = true;
  window.TripEditorAI?.open();
}

async function saveEditor(event) {
  event.preventDefault();
  if (!state.currentEdit || !requireIdentity()) return;
  const editContext = state.currentEdit;
  window.TripEditorAI?.invalidate("正在保存，当前 AI 结果不再填入。");
  const { kind, item } = editContext;
  const payload = Object.fromEntries(new FormData(dom.editForm).entries());
  if (kind === "attraction" || kind === "food") payload.tags = window.TripTaxonomy.parseTags(payload.tags);
  if (kind === "itinerary") {
    if (item?.block_locked) payload.time_block = item.time_block || "";
    const error = attractionNamesError(payload.attraction_names);
    if (error) { dom.editError.textContent = error; return; }
    payload.attraction_names = parseAttractionNames(payload.attraction_names);
    payload.duration_minutes = payload.duration_minutes === "" ? null : Number(payload.duration_minutes);
    payload.duration_source = payload.duration_minutes === null ? "unknown" : item?.duration_minutes === payload.duration_minutes ? (item.duration_source || "user") : "user";
    const sameHours = (item?.opening_start || "") === payload.opening_start && (item?.opening_end || "") === payload.opening_end;
    payload.opening_source = sameHours ? (item?.opening_source || (payload.opening_start ? "user" : "unknown")) : payload.opening_start ? "user" : "unknown";
    payload.opening_note = sameHours ? (item?.opening_note || "") : "";
    if (payload.attraction_names.length > 1) payload.visit_kind = "legacy";
  }
  payload.city_id = item?.city_id || state.cityId;
  if (item) payload.version = item.version;
  state.saving = true;
  window.TripEditorAI?.sync();
  dom.saveButton.disabled = true;
  dom.editClose.disabled = true;
  dom.editReturn.disabled = true;
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
        dom.editFields.querySelectorAll("input, textarea, select").forEach((field) => {
          field.setAttribute("aria-invalid", "true");
          field.setAttribute("aria-describedby", "edit-error");
        });
      }
    }
  } finally {
    state.saving = false;
    window.TripEditorAI?.sync();
    dom.saveButton.disabled = false;
    dom.editClose.disabled = false;
    dom.editReturn.disabled = false;
    dom.deleteButton.disabled = false;
  }
}

function askDelete() {
  if (!state.currentEdit?.item || state.saving) return;
  state.pendingDelete = {
    kind: state.currentEdit.kind,
    item: { ...state.currentEdit.item },
  };
  dom.confirmMessage.textContent = `删除“${itemName(state.pendingDelete.kind, state.pendingDelete.item)}”后，当前项目的同行者也会看不到这条内容。${["attraction", "food"].includes(state.pendingDelete.kind) ? "只从当前项目移除，不影响其他项目和全局缓存。" : ""}`;
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
  if (!window.TripBatch.canNavigate()) return;
  if (!state.projectUnlocked) {
    showProjectGate();
    return;
  }
  window.TripEditorAI?.invalidate("请确认用户身份后重新补充。");
  dom.identityError.textContent = "";
  dom.userIdInput.value = switching ? "" : state.userId;
  dom.identityCancel.hidden = !state.userId;
  dom.identityTitle.textContent = state.userId ? "切换用户 ID" : "选择你的用户 ID";
  renderUserChoices();
  if (!dom.identityDialog.open) dom.identityDialog.showModal();
  window.TripEditorAI?.sync();
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
  if (!window.TripBatch.canNavigate()) return;
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
    if (state.userId && state.userId !== data.user_id) window.TripUI.reset();
    state.userId = data.user_id;
    window.TripEditorAI?.sync();
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
  state.cacheInfo = null;
  state.travelGuidance = [];
  state.projectItinerary = null;
  state.activity = [];
  state.revision = null;
  state.snapshotEtag = null;
  state.snapshotRequestId += 1;
  window.TripUI.reset();
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
    if (field && "value" in field) {
      field.value = value;
      if (name === "tags") field.dispatchEvent(new Event("input", { bubbles: true }));
    }
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
  if (submit.disabled) return;
  submit.disabled = true;
  dom.projectError.textContent = "";
  try {
    const creating = document.querySelector("#project-mode").value === "create";
    const projectId = document.querySelector("#project-list").value;
    const payload = { project_code: dom.projectCodeInput.disabled ? "" : dom.projectCodeInput.value };
    if (creating) payload.name = document.querySelector("#project-create-name").value;
    const response = await fetch(creating ? "/api/projects" : "/api/project-session", {
      method: "POST", headers: { "Content-Type": "application/json", ...(creating ? {} : { "X-Trip-Project": projectId }) },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "暂时无法进入项目，请重试。");
    const selectedId = data.project_id || projectId;
    if (selectedId !== window.TripProject.id || !window.TripProject.valid) {
      sessionStorage.setItem("trip-enter-selected", selectedId);
      location.assign(window.TripProject.url(`/${location.hash}`, selectedId));
      return;
    }
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
  if (!window.TripBatch.canNavigate()) return;
  if (state.editorDirty && !window.confirm("退出项目会放弃尚未保存的修改，确定退出吗？")) return;
  dom.projectLockButton.disabled = true;
  try {
    await requestJson("/api/project-session", {
      method: "DELETE",
      body: JSON.stringify({}),
    });
    showProjectGate();
    await refreshProjectChoices();
  } catch (error) {
    dom.identityError.textContent = error.message;
  } finally {
    dom.projectLockButton.disabled = false;
  }
}

async function sharePlan() {
  const shareData = {
    title: "即刻出发",
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
          description: "读取当前共享项目中的行程、地点、公共交通和美食条目。",
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
          title: "添加当前城市地点",
          description: "以当前用户身份向共享计划添加一个地点，并刷新可见列表。",
          inputSchema: {
            type: "object",
            properties: {
              name: { type: "string", minLength: 1, maxLength: 60 },
              scenic_rating: { type: "string", enum: ["", "4A", "5A"] },
              district: { type: "string", maxLength: 30 },
              category: { type: "string", maxLength: 30 },
              tags: { type: "array", maxItems: 30, items: { type: "string", maxLength: 24 } },
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
            payload.tags = window.TripTaxonomy.parseTags(input.tags);
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
              time_block: { type: "string", enum: ["", "morning", "midday", "lunch_rest", "afternoon", "flexible", "evening", "night"] },
              duration_minutes: { type: "integer", minimum: 1, maximum: 1440 },
              title: { type: "string", minLength: 1, maxLength: 80 },
              category: { type: "string", maxLength: 30 },
              location: { type: "string", maxLength: 120 },
              attraction_names: { type: "array", maxItems: 12, items: { type: "string", minLength: 1, maxLength: 60 }, description: "本项行程游览的地点名称，留空自动识别；不包含路口、普通街道或交通路线。" },
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
            payload.attraction_names = parseAttractionNames(input.attraction_names);
            payload.duration_minutes = input.duration_minutes ?? null;
            payload.duration_source = "user";
            payload.priority = input.priority || "preferred";
            payload.visit_kind = input.visit_kind || "place";
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
dom.editForm.addEventListener("input", (event) => {
  if (event.target.closest("#editor-ai")) return;
  state.editorDirty = true;
  dom.editForm.querySelectorAll('[aria-invalid="true"]').forEach((field) => {
    field.removeAttribute("aria-invalid");
    field.removeAttribute("aria-describedby");
  });
});
dom.editClose.addEventListener("click", () => closeEditor());
dom.editReturn.addEventListener("click", () => closeEditor());
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
    if (!window.TripBatch.canNavigate()) { history.replaceState(null, "", `#${state.tab}`); return; }
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

let availableProjects = [];
function updateProjectChoice() {
  const creating = document.querySelector("#project-mode").value === "create";
  const selected = availableProjects.find((p) => p.id === document.querySelector("#project-list").value);
  const password = creating ? document.querySelector("#project-password-mode").value === "password" : Boolean(selected?.password_required);
  document.querySelector("#project-list-field").hidden = creating;
  document.querySelector("#project-list-refresh").hidden = creating;
  document.querySelector("#project-list").disabled = creating;
  document.querySelector("#project-create-name-field").hidden = !creating;
  document.querySelector("#project-create-name").disabled = !creating;
  document.querySelector("#project-create-name").required = creating;
  document.querySelector("#project-password-mode-field").hidden = !creating;
  document.querySelector("#project-code-field").hidden = !password;
  dom.projectCodeInput.disabled = !password;
  dom.projectCodeInput.required = password;
  dom.projectForm.querySelector('[type="submit"]').textContent = creating ? "创建并进入项目" : "进入项目";
  dom.projectForm.querySelector('[type="submit"]').disabled = !creating && !selected;
}
async function refreshProjectChoices() {
  dom.projectForm.querySelector('[type="submit"]').disabled = true;
  try {
    const response = await fetch("/api/projects");
    if (!response.ok) throw new Error("暂时无法加载项目列表，请点击刷新重试。");
    const data = await response.json();
    availableProjects = data.projects;
    const select = document.querySelector("#project-list");
    const previous = select.value || window.TripProject.id;
    select.replaceChildren(...availableProjects.map((project) => {
      const option = document.createElement("option");
      option.value = project.id;
      option.textContent = `${project.name} · ${project.password_required ? "需要密码" : "无密码"}`;
      return option;
    }));
    if (availableProjects.some((p) => p.id === previous)) select.value = previous;
    if (!availableProjects.length) document.querySelector("#project-mode").value = "create";
    updateProjectChoice();
  } catch (error) { dom.projectError.textContent = error.message; }
}
for (const id of ["project-mode", "project-list", "project-password-mode"]) {
  document.getElementById(id).addEventListener("change", () => {
    dom.projectCodeInput.value = "";
    dom.projectError.textContent = "";
    updateProjectChoice();
  });
}
document.querySelector("#project-list-refresh").onclick = refreshProjectChoices;
document.querySelector("#project-switch").onclick = async () => {
  if (!window.TripBatch.canNavigate()) return;
  if (state.editorDirty && !confirm("切换项目会放弃尚未保存的修改，确定继续吗？")) return;
  showProjectGate();
  await refreshProjectChoices();
};
async function start() {
  registerWebMcpTools();
  showProjectGate();
  await refreshProjectChoices();
  const selected = sessionStorage.getItem("trip-enter-selected");
  sessionStorage.removeItem("trip-enter-selected");
  if (selected === window.TripProject.id) {
    try {
      const { data } = await requestJson("/api/project-session");
      if (data.unlocked) await enterUnlockedProject();
    } catch (error) { dom.projectError.textContent = error.message; }
  }
}

window.TripProject.initLinks();
window.TripBatch.init();
window.TripUI.init();
window.TripEditorAI?.init();
window.TripExport?.init();
window.TripCities?.init();
window.TripCityEditor?.init();
start();
