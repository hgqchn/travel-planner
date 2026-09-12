"use strict";

window.TripDaily = (() => {
  const blocks = [
    ["morning", "上午", "09:00", "11:00"], ["midday", "午间", "11:00", "13:00"],
    ["lunch_rest", "午后休息", "13:00", "14:00"], ["afternoon", "下午", "14:00", "16:00"],
    ["flexible", "机动", "16:00", "17:00"], ["evening", "傍晚", "17:00", "20:00"],
    ["night", "夜间", "20:00", "22:00"],
  ];
  const options = [{ value: "", label: "待安排" }, ...blocks.map(([value, name, a, b]) => ({ value, label: `${name} ${a}–${b}` }))];
  const label = id => options.find(x => x.value === id)?.label || "待安排";
  const scope = () => JSON.stringify([state.projectId, state.cityId, state.userId, state.projectUnlocked]);
  let dialog, content, status, current, activeScope, generation = 0, busy = false, dragged = null;
  let dayMutation = false, dateDraft = null;
  const selectedDates = new Map();
  const e = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  const button = (text, fn, cls = "secondary-button") => { const n = e("button", cls, text); n.type = "button"; n.addEventListener("click", fn); return n; };
  const api = async (path, body, method = "POST") => (await requestJson(path, { method, body: JSON.stringify(body) })).data;
  const identity = () => ({ city_id: current.city_id, date: current.date, version: current.version });
  function field(parent, text, type, value, min, max) {
    const wrapper = e("label", "field"), input = e("input"); wrapper.append(e("span", "", text));
    input.type = type; input.value = value ?? ""; if (min !== undefined) input.min = min; if (max !== undefined) input.max = max;
    wrapper.append(input); parent.append(wrapper); return input;
  }
  function setBusy(value) { busy = value; content?.querySelectorAll("button,input,select,textarea").forEach(n => n.disabled = value); }
  function valid(token) { return token === generation && dialog?.open && activeScope === scope(); }
  async function work(action) {
    if (busy) return;
    const token = generation; setBusy(true); status.textContent = "正在处理…";
    try { await action(token); } catch (err) {
      if (valid(token)) {
        status.textContent = err.message;
        if (err.status === 409) status.append(button("载入最新计划（会替换当前草稿）", () => open(current.date, true)));
      }
    } finally { if (valid(token)) setBusy(false); }
  }
  async function save(changes, token) {
    const result = await api("/api/day-plan", { ...identity(), ...changes }, "PUT");
    if (!valid(token)) return;
    current = result; await fetchSnapshot(true, true);
    if (valid(token)) { settingsView(); status.textContent = "每日计划已同步，可按新设置重新规划路线。"; }
  }
  function init() {
    if (dialog) return;
    dialog = e("dialog", "daily-dialog"); dialog.setAttribute("aria-labelledby", "daily-title");
    const head = e("header", "daily-heading"); head.append(e("h2", "", "每日计划")); head.querySelector("h2").id = "daily-title";
    head.append(button("关闭", () => dialog.close())); status = e("div", "daily-status"); status.setAttribute("role", "status");
    content = e("div", "daily-content"); dialog.append(head, status, content); document.body.append(dialog);
    dialog.addEventListener("close", () => { generation++; busy = false; current = null; });
  }
  async function open(date, reload = false, block = null) {
    if (!requireIdentity()) return;
    init(); generation++; const token = generation; activeScope = scope();
    if (!dialog.open) dialog.showModal();
    setBusy(false); content.replaceChildren(); status.textContent = "正在读取当天安排…";
    try {
      const result = (await requestJson(`/api/day-plan?city_id=${encodeURIComponent(state.cityId)}&date=${encodeURIComponent(date)}`)).data;
      if (!valid(token)) return;
      current = result; settingsView(); status.textContent = reload ? "已载入最新版本。" : "先选择地点，再比较路线。保存会同步给同行者。";
      if (block !== null) poolView(block);
      return true;
    } catch (err) { if (valid(token)) status.textContent = err.message; }
  }
  function anchor(parent, key, title, draft) {
    const box = e("section", "daily-anchor"); box.append(e("h4", "", title)); parent.append(box);
    const query = field(box, "搜索住处、车站或具体入口", "text", draft[key]?.name || "");
    const selected = e("p", "", draft[key] ? `已选：${draft[key].name} · ${draft[key].address}` : "尚未设置，可补充首末接驳地点");
    const results = e("div", "daily-choices"); box.append(selected, results);
    const setSelected = poi => {
      draft[key] = poi ? structuredClone(poi) : null;
      query.value = poi?.name || "";
      selected.textContent = poi ? `已选：${poi.name} · ${poi.address}` : "尚未设置，可补充首末接驳地点";
      results.replaceChildren();
    };
    box.append(button("搜索", () => work(async token => {
      const data = await api("/api/maps/search", { city_id: current.city_id, keywords: query.value });
      if (!valid(token)) return; results.replaceChildren();
      for (const poi of data.places) results.append(button(`${poi.name} · ${poi.address}`, () => {
        setSelected(poi); status.textContent = "地点已选，请保存每日设置。";
      }));
      status.textContent = data.places.length ? "请选择正确地点或入口。" : "未找到地点，请换一个名称。";
    })), button("清除", () => { setSelected(null); }));
    return setSelected;
  }
  function settingsView() {
    content.replaceChildren(); dialog.querySelector("h2").textContent = `${current.date} · 确定当天行程`;
    const nav = e("div", "daily-actions");
    nav.append(button("从地点清单添加", () => poolView()), button("AI 微调当天安排", () => aiView("daily_adjust")));
    if (current.visits.some(v => v.duration_minutes == null)) nav.append(button("AI 补全停留时长", () => aiView("daily_duration")));
    if (current.visits.some(v => !v.opening_start && !['legacy','rest','transit'].includes(v.visit_kind))) nav.append(button("AI 补全开放时间", () => aiView("daily_hours")));
    content.append(nav);
    const draft = structuredClone(current.settings);
    const preferences = e("details", "daily-preferences daily-settings-card");
    const summary = e("summary", "daily-settings-summary"), summaryText = e("span", "daily-settings-copy");
    const summaryTitle = e("span", "daily-settings-title");
    summaryTitle.append(e("strong", "", "出发地与返回地"), e("span", "daily-settings-optional", "可选设置"));
    summaryText.append(summaryTitle, e("span", "daily-settings-description", "设置当天出发和返回的住处或车站。"));
    const toggle = e("span", "daily-settings-toggle", "展开设置");
    summary.append(summaryText, toggle); preferences.append(summary); content.append(preferences);
    preferences.addEventListener("toggle", () => { toggle.textContent = preferences.open ? "收起设置" : "展开设置"; });
    preferences.append(e("h3", "", "出发与返回"));
    const anchors = e("div", "daily-grid"); preferences.append(anchors);
    anchor(anchors, "start_anchor", "当天出发地点", draft);
    const setReturnAnchor = anchor(anchors, "end_anchor", "当天返回地点", draft);
    preferences.append(button("起终点使用同一地点", () => {
      if (!draft.start_anchor) { status.textContent = "请先搜索并选择当天出发地点。"; return; }
      setReturnAnchor(draft.start_anchor);
      status.textContent = "返回地点已设为出发地点，点击保存每日设置后生效。";
    }));
    preferences.append(button("保存每日设置", () => work(token => save({ settings: { start_anchor: draft.start_anchor, end_anchor: draft.end_anchor } }, token)), "primary-button"));
    content.append(e("h3", "", "当天行程与顺序"));
    const help = e("div", "daily-action-help");
    for (const [title, description] of [
      ["转为备选", "暂时移出当天路线，保留内容与原时段；之后可点击“添加到行程中”恢复。"],
      ["锁定时段", "固定所属时段，手动编辑、跨时段拖动和 AI 微调都不能更换时段。其他信息仍可编辑；换时段前请先解除锁定。"],
    ]) {
      const line = e("p"); line.append(e("strong", "", `${title}：`), e("span", "", description)); help.append(line);
    }
    content.append(help);
    if (!current.visits.length) content.append(e("p", "daily-hint", "当天还没有地点。可从地点清单添加，也可保留为空白日。"));
    for (const visit of current.visits) {
      const row = e("div", "daily-visit-row"); row.append(e("span", "", `${visit.position}. ${visit.title} · ${label(visit.time_block)} · ${visit.duration_minutes == null ? "待补充建议时长" : `${visit.duration_source === "ai_estimate" ? "AI 推荐 " : ""}${visit.duration_minutes} 分钟`}${visit.is_backup ? " · 备选" : ""}`));
      row.append(button("编辑", () => { dialog.close(); openEditor("itinerary", visit); }));
      row.append(button(visit.is_backup ? "添加到行程中" : "转为备选", () => work(token => {
        if (!visit.is_backup && (visit.priority === "must")) throw new Error("必去地点请先在编辑中调整约束。");
        return save({ updates: [{ id: visit.id, changes: { is_backup: !visit.is_backup } }] }, token);
      })));
      row.append(button(visit.block_locked ? "解除时段锁定" : "锁定时段", () => work(token => {
        if (!visit.block_locked && !visit.time_block) throw new Error("请先编辑该地点，选择所属时段。");
        return save({ updates: [{ id: visit.id, changes: { block_locked: !visit.block_locked } }] }, token);
      })));
      if (visit.visit_kind === "legacy") row.append(button("预览拆分地点", () => {
        status.textContent = `将拆分为：${visit.attraction_names.join(" → ")}。原备注保留，各地点时长需重新填写。`;
        status.append(button("确认拆分", () => work(token => save({ split: visit.id }, token))));
      }));
      content.append(row);
    }
    content.append(button("下一步：规划路线", () => { const date = current.date; dialog.close(); window.TripMaps?.open(date); }, "primary-button"));
  }
  function poolView(block = "") {
    content.replaceChildren(); content.append(button("返回当天行程", settingsView), e("h3", "", "从地点清单选择"));
    const list = e("div", "daily-choices"), picked = new Map(); content.append(list);
    const scheduled = new Set(current.visits.flatMap(v => v.attraction_names || []));
    for (const place of state.items.attraction.filter(p => p.city_id === current.city_id && !scheduled.has(p.name))) {
      const row = e("label", "daily-pool-row"), check = e("input"); check.type = "checkbox";
      check.addEventListener("change", () => check.checked ? picked.set(place.id, place) : picked.delete(place.id));
      row.append(check, e("span", "", `${place.name}${place.duration ? ` · ${place.duration}` : ""}${place.in_itinerary ? " · 已在其他行程安排" : ""}`)); list.append(row);
    }
    content.append(button("添加所选地点", () => work(token => {
      if (!picked.size) throw new Error("请先选择地点。");
      return save({ additions: [...picked.values()].map(p => ({ source_place_id: p.id, title: p.name, location: p.name, attraction_names: [p.name], visit_kind: "attraction", time_block: block })) }, token);
    }), "primary-button"), button("添加行程", () => {
      const date = current.date; dialog.close(); openEditor("itinerary"); dom.editForm.elements.namedItem("date").value = date;
      dom.editForm.elements.namedItem("time_block").value = block;
    }));
  }
  const jobKey = () => window.TripProject.storageKey(`daily-ai:${state.userId}:${current.date}`);
  async function aiView(purpose, visitId = null) {
    content.replaceChildren(); content.append(button("返回当天行程", settingsView));
    const title = { daily_select: "AI 帮我选地点", daily_choose: "AI 比较路线方案", daily_adjust: "AI 调整现有安排", daily_duration: "AI 补全停留时长", daily_hours: "AI 补全开放时间" }[purpose];
    const intro = purpose === "daily_hours" ? `${visitId ? `为「${current.visits.find(v => v.id === visitId)?.title || "所选行程"}」` : "为当天未填写时间的行程"}补充开放与关闭时间。AI 可能不准确，请先预览，应用后仍需核实。` : "建议先预览，再应用。地点确认与路线核算由程序完成。";
    content.append(e("h3", "", title), e("p", "", intro));
    const preferences = e("textarea"); preferences.rows = 3; preferences.maxLength = 2000;
    if (purpose === "daily_duration") preferences.value = "为尚未填写停留时长的活动推荐合理时长，结合地点特点和游览节奏。保留所有已填写的时长，不改变地点、时段或顺序。";
    if (purpose === "daily_hours") preferences.value = "补充尚未填写的地点开放和关闭时间，仅作 AI 参考，可能不准确。未知、闭馆或跨夜时留空并说明，不修改已填写时间。";
    preferences.placeholder = purpose === "daily_adjust" ? "例如：下午轻松一点，晚上不出门" : "例如：喜欢历史建筑，保留必去地点，不要太赶";
    preferences.setAttribute("aria-label", "规划需求"); content.append(preferences);
    const model = field(content, "模型 ID（留空使用当前配置）", "text", "");
    const preview = e("div", "daily-ai-preview");
    content.append(button("生成建议", () => work(async token => {
      let candidates;
      if (purpose === "daily_choose") {
        status.textContent = "正在比较路线与时间预算…";
        const evaluated = await api("/api/day-plan/evaluate", { ...identity(), optimize: true });
        if (!valid(token)) return; evaluationView(evaluated); candidates = evaluated.candidates.map(c => c.candidate_ref);
      }
      const body = { purpose, city_id: current.city_id, start_date: current.date, day_version: current.version,
        requirements: preferences.value, ...(visitId ? { visit_id: visitId } : {}), preserve_selection: false, ...(candidates ? { candidate_refs: candidates } : {}) };
      if (model.value.trim()) body.model = model.value.trim();
      const result = await api("/api/ai/jobs", body);
      if (!valid(token)) return; const job = result.job || result;
      try { localStorage.setItem(jobKey(), job.id); } catch {}
      await poll(job.id, preview, token);
    }), "primary-button"), button("恢复上次建议", () => work(async token => {
      const id = localStorage.getItem(jobKey()); if (!id) throw new Error("当天没有保存的 AI 任务。"); await poll(id, preview, token);
    })), preview);
  }
  async function poll(id, preview, token) {
    while (valid(token)) {
      const raw = (await requestJson(`/api/ai/jobs/${encodeURIComponent(id)}`)).data;
      if (!valid(token)) return;
      const job = raw.job || raw;
      if (["queued", "running"].includes(job.status)) {
        status.textContent = job.status === "queued" ? "AI 任务排队中…可关闭窗口，稍后恢复。" : "AI 正在生成结构化建议…";
        await new Promise(resolve => setTimeout(resolve, 1500)); continue;
      }
      if (job.status === "failed") throw new Error(job.error || "AI 生成失败。");
      if (!job.result || job.result.schema_version !== 2) throw new Error("该任务不属于新版每日规划。");
      const result = job.result; preview.replaceChildren();
      const labels = result.reference_labels || {};
      for (const p of result.hours || []) preview.append(e("p", "", `${labels[p.place_ref] || p.place_ref} · ${p.opening_start ? `${p.opening_start}–${p.opening_end}` : "时间未知"}。${p.note}`));
      for (const p of result.durations || []) preview.append(e("p", "", `${labels[p.place_ref] || p.place_ref} · AI 推荐 ${p.duration_minutes} 分钟。${p.reason}`));
      for (const p of result.selected_places || []) preview.append(e("p", "", `${labels[p.place_ref] || p.place_ref} · ${p.duration_minutes_estimate} 分钟 · ${p.preferred_blocks.map(label).join(" / ")}。${p.reason}`));
      for (const ref of result.moved_to_backup || []) preview.append(e("p", "daily-issue", `转为备选：${labels[ref] || ref}`));
      for (const p of result.new_place_suggestions || []) preview.append(e("p", "", `新推荐：${p.name}（具体地点与停留时长待确认）· ${p.reason}`));
      for (const q of result.questions || []) preview.append(e("p", "daily-issue", q.message));
      if (result.stage === "choose_plan") {
        preview.append(e("h4", "", result.recommended_candidate_ref ? `AI 推荐方案 ${result.recommended_candidate_ref.slice(1)}` : "暂时没有可推荐的方案"));
        if (result.recommended_order?.length) preview.append(e("p", "", result.recommended_order.join(" → ")));
      }
      for (const reason of result.reasons || []) preview.append(e("p", "", reason.text));
      if (result.explanation) preview.append(e("p", "", result.explanation));
      const opNames = { set_pace: "调整节奏", set_night: "夜游开关", move_to_block: "移动时段", set_duration: "停留分钟", set_backup: "设为备选", set_order: "调整顺序" };
      for (const op of result.operations || []) preview.append(e("p", "", `${labels[op.place_ref] || "当天"}：${opNames[op.op]} → ${op.op === "move_to_block" ? label(op.value) : op.order.length ? op.order.map(x => labels[x] || x).join(" → ") : op.value}`));
      const canApply = result.stage === "recommend_hours" ? result.hours.length > 0 : result.stage === "recommend_durations" ? result.durations.length > 0 : result.stage === "choose_plan" ? result.recommended_candidate_ref !== null : result.stage === "adjust_plan" ? result.operations.length > 0 : (result.selected_places.length + result.new_place_suggestions.length) > 0;
      status.textContent = job.request.day_version === current.version ? "建议已生成，请核对后应用。" : "生成后当天计划已变化，请重新生成；旧建议仅供参考。";
      if (canApply && job.request.day_version === current.version) preview.append(button("应用这份建议", () => work(async next => {
        const plan = await api("/api/day-plan/ai-apply", { ...identity(), job_id: id });
        if (!valid(next)) return; current = plan; await fetchSnapshot(true, true);
        if (valid(next)) { settingsView(); status.textContent = result.stage === "recommend_hours" ? "开放时间已补充；停留、优先级和顺序已保留。" : "建议已应用；请确认具体地点，再核算全天路线。"; }
      }), "primary-button"));
      return;
    }
  }
  async function openHours(date, id) {
    if (await open(date)) aiView("daily_hours", id);
  }
  async function restoreBackup(date, visit) {
    if (!requireIdentity() || dayMutation) return;
    const active = scope(); setDayMutation(true);
    try {
      const plan = (await requestJson(`/api/day-plan?city_id=${encodeURIComponent(state.cityId)}&date=${date}`)).data;
      if (active !== scope()) return;
      const latest = plan.visits.find(v => v.id === visit.id);
      if (!latest || latest.version !== visit.version) throw new Error("该行程已变化，请刷新后重试。");
      if (!latest.is_backup) { await fetchSnapshot(true, true); return; }
      const changes = { is_backup:false };
      await api('/api/day-plan', {city_id:plan.city_id,date,version:plan.version,updates:[{id:visit.id,changes}]}, 'PUT');
      if (active === scope()) { await fetchSnapshot(true, true); showToast("已添加到当天行程，请规划相邻路线。"); }
    } catch (error) { if (active === scope()) showToast(error.message); }
    finally { setDayMutation(false); }
  }
  const groupId = visit => visit.is_backup ? "backup" : visit.time_block || "";
  const groupRank = id => id === "backup" ? blocks.length + 1 : id === "" ? -1 : blocks.findIndex(b => b[0] === id);
  async function move(date, from, to, block) {
    if (!requireIdentity()) return;
    const active = scope();
    const expected = JSON.stringify(state.items.itinerary.filter(v => v.city_id === state.cityId && v.date === date).map(v => [v.id,v.version]).sort());
    try {
      const plan = (await requestJson(`/api/day-plan?city_id=${encodeURIComponent(state.cityId)}&date=${date}`)).data;
      if (active !== scope()) return;
      if (JSON.stringify(plan.visits.map(v => [v.id,v.version]).sort()) !== expected) throw new Error("当天安排已变化，请刷新后再调整顺序。");
      const order = plan.visits.map(v => v.id), i = order.indexOf(from), j = order.indexOf(to);
      if (i < 0 || (to != null && j < 0)) throw new Error("活动已变化，请刷新。");
      const source = plan.visits[i], target = plan.visits[j];
      const destination = block ?? groupId(target);
      if (destination !== groupId(source) && (source.block_locked)) throw new Error("请先在“确定当天行程”中解除时段锁定，再移动到其他时段。");
      if (destination === "backup" && (source.priority === "must")) throw new Error("必去地点不能直接转为备选。");
      const changes = destination === "backup" ? { is_backup: true } : { time_block: destination, is_backup: false };
      const updates = destination !== groupId(source) ? [{ id: from, changes }] : [];
      order.splice(i, 1); order.splice(to == null ? order.length : j, 0, from);
      const byId = new Map(plan.visits.map(v => [v.id, v.id === from && updates.length ? { ...v, ...changes } : v]));
      order.sort((a, b) => groupRank(groupId(byId.get(a))) - groupRank(groupId(byId.get(b))));
      await api("/api/day-plan", { city_id: plan.city_id, date, version: plan.version, order, updates }, "PUT");
      if (active === scope()) { await fetchSnapshot(true, true); showToast("顺序已保存，请重新核算受影响路线。"); }
    } catch (err) { showToast(err.message); }
  }
  function setDayMutation(value) {
    dayMutation = value;
    dom.cards.querySelectorAll?.('[data-day-action]').forEach(node => node.disabled = value);
  }
  function suggestedDate(dates) {
    const next = dates.length ? new Date(`${dates[dates.length - 1]}T12:00:00`) : new Date();
    if (dates.length && dates[dates.length - 1] !== "9999-12-31") next.setDate(next.getDate() + 1);
    return `${String(next.getFullYear()).padStart(4, "0")}-${String(next.getMonth() + 1).padStart(2, "0")}-${String(next.getDate()).padStart(2, "0")}`;
  }
  async function createDay(date) {
    if (dayMutation || !requireIdentity() || !date) return;
    if (window.TripBatch && !window.TripBatch.canNavigate()) return;
    const active = scope(), cityId = state.cityId;
    setDayMutation(true);
    try {
      await api('/api/day-plan', { city_id: cityId, date });
      if (active !== scope()) return;
      dateDraft = null;
      selectedDates.set(active, date);
      await fetchSnapshot(true, true);
      if (active === scope()) showToast(`已新增 ${date}，可以添加地点或保留为空白日。`);
    } catch (err) {
      if (active === scope()) showToast(err.message);
    } finally { setDayMutation(false); }
  }
  async function deleteDay(day, daily, count) {
    if (dayMutation || !requireIdentity()) return;
    if (window.TripBatch && !window.TripBatch.canNavigate()) return;
    if (!daily?.version) { showToast('请刷新行程后再删除这一天。'); return; }
    const active = scope(), cityId = state.cityId;
    const cityName = state.cities?.find(city => city.id === cityId)?.name || '当前城市';
    if (!window.confirm(`删除 ${cityName} · ${day} 这一天？\n\n将删除当天 ${count} 项安排（含备选）、每日设置和路线核算结果。\n地点、美食清单及其他日期保留。此操作无法直接恢复。`)) return;
    setDayMutation(true);
    try {
      await api('/api/day-plan', { city_id: cityId, date: day, version: daily.version }, 'DELETE');
      if (active !== scope()) return;
      await fetchSnapshot(true, true);
      if (active === scope()) showToast(`已删除 ${day} 的每日计划。`);
    } catch (err) {
      if (active === scope()) {
        if ([404, 409].includes(err.status)) await fetchSnapshot(true, true);
        if (active === scope()) showToast(err.message);
      }
    } finally { setDayMutation(false); }
  }
  function render(items) {
    let overview = null;
    if (dialog?.open && activeScope !== scope()) dialog.close();
    const dates = [...new Set([...items.map(v => v.date), ...(state.dailyPlans || []).filter(p => p.city_id === state.cityId).map(p => p.date)])].sort();
    const previousDate = selectedDates.get(scope());
    const selectedDate = dates.includes(previousDate) ? previousDate : (dates.find(date => date >= previousDate) || (previousDate ? dates.at(-1) : dates[0]));
    if (selectedDate) selectedDates.set(scope(), selectedDate); else selectedDates.delete(scope());
    const wrapper = e("div", "daily-plans"), add = e("form", "daily-actions daily-day-add");
    const workflow = e("section", "daily-workflow");
    workflow.append(e("p", "daily-workflow-steps", "生成或收集 → 确认草稿 → 按天安排 → 规划路线"));
    if (!items.length) {
      const choices = e("div", "daily-workflow-choices");
      for (const [flow, title, detail] of [["full", "AI 帮我规划", "一起生成地点、美食和行程草稿，确认后再核对路线。"],
        ["collect", "先收集地点和美食", "AI 只补充清单；再新增日期，从清单选择当天地点。"]]) {
        const choice = button("", () => window.TripUI?.openPlanning(flow), "daily-workflow-choice");
        choice.append(e("strong", "", title), e("span", "", detail)); choices.append(choice);
      }
      workflow.append(choices);
    }
    wrapper.append(workflow);
    if (!dateDraft || dateDraft.scope !== scope() || !dateDraft.touched) dateDraft = { scope: scope(), value: suggestedDate(dates), touched: false };
    const date = field(add, '新增日期', 'date', dateDraft.value, '0001-01-01', '9999-12-31');
    date.required = true; date.dataset.dayAction = ''; date.disabled = dayMutation;
    date.addEventListener('input', () => { dateDraft = { scope: scope(), value: date.value, touched: true }; });
    const create = e('button', 'primary-button', '＋ 新增一天'); create.type = 'submit';
    create.dataset.dayAction = ''; create.disabled = dayMutation;
    add.append(create);
    add.addEventListener('submit', event => { event.preventDefault(); return createDay(date.value); });
    wrapper.append(add);
    if (!dates.length) wrapper.append(e('p', 'daily-hint', '自己安排：选择日期并新增一天，再从地点清单添加。使用 AI 整体规划时，不必提前创建日期。'));
    if (dates.length) {
      const picker = e('nav', 'daily-day-picker'); picker.setAttribute('aria-label', '按天查看行程');
      const index = dates.indexOf(selectedDate), field = e('label', 'daily-day-choice'), select = e('select', 'daily-day-select');
      field.append(e('span', '', `查看行程 · 共 ${dates.length} 天`), select);
      select.setAttribute('aria-label', '选择行程日期');
      dates.forEach((day, i) => {
        const count = items.filter(v => v.date === day && !v.is_backup).length;
        const option = e('option', '', `第 ${i + 1} 天 · ${formatItineraryDate(day)} · ${count ? `${count} 项行程` : '暂未安排'}`);
        option.value = day; select.append(option);
      });
      select.value = selectedDate;
      const choose = date => {
        if (!dates.includes(date) || (window.TripBatch && !window.TripBatch.canNavigate())) return;
        dragged = null; selectedDates.set(scope(), date); render(items);
        dom.cards.querySelector?.('.daily-day-select')?.focus?.({preventScroll:true});
        dom.cards.querySelector?.('.daily-day-picker')?.scrollIntoView?.({block:'start', behavior:'smooth'});
      };
      select.addEventListener('change', () => choose(select.value));
      const previous = button('← 上一天', () => choose(dates[index - 1]), 'secondary-button daily-day-step'); previous.disabled = index === 0;
      const next = button('下一天 →', () => choose(dates[index + 1]), 'secondary-button daily-day-step'); next.disabled = index === dates.length - 1;
      picker.append(previous, field, next); wrapper.append(picker);
    }
    for (const day of selectedDate ? [selectedDate] : []) {
      const visits = items.filter(v => v.date === day).sort((a,b) => a.position-b.position || a.id.localeCompare(b.id));
      const section = e("section", "itinerary-day"), heading = e("div", "itinerary-day-heading");
      heading.append(e("h3", "", formatItineraryDate(day)), button("确定当天行程", () => open(day)),
        button("规划路线", () => window.TripMaps?.open(day)));
      heading.children[1].setAttribute("data-tour-target", "daily-confirm");
      heading.children[2].setAttribute("data-tour-target", "daily-route");
      const dayMore = e("details", "daily-day-more"); dayMore.append(e("summary", "", "更多"),
        button("AI 重排这一天", () => window.TripUI?.openReplan("replace_day", day)));
      heading.append(dayMore);
      section.append(heading, e("p", "daily-hint", "同一时段可安排多个地点，也可留空休息。拖动地点可调整时段和顺序。"));
      const daily = (state.dailyPlans || []).find(p => p.city_id === state.cityId && p.date === day);
      const remove = button('删除这一天', () => deleteDay(day, daily, visits.length), 'daily-delete-day');
      remove.setAttribute('aria-label', `删除 ${day} 这一天`);
      remove.dataset.dayAction = ''; remove.disabled = dayMutation;
      dayMore.append(remove);
      const summary = daily?.evaluation;
      if (summary) section.append(e("p", "daily-hint", summary.route_status === "stale" ? "路线核算已过期，请重新核算。" : `交通约 ${summary.travel_minutes} 分钟 · 缓冲 ${summary.buffer_minutes} 分钟 · ${summary.slack_minutes == null ? "余量待核算" : `机动余量 ${summary.slack_minutes} 分钟`}`));
      const route = daily?.route_preview;
      if (route) {
        const card = e('section','daily-route-preview'), header = e('div','daily-route-heading');
        header.append(e('h4','','当天路线'), e('span','daily-route-count',`已保存 ${route.planned}/${route.total} 段 · 交通约 ${route.minutes} 分钟`));
        const shell=e('div','daily-route-map-shell'), canvas=e('div','daily-route-map');
        canvas.setAttribute('aria-label', `${day} 高德地图与已保存的全程路线`); canvas.setAttribute('aria-busy','true');
        const mapButton=button('查看全程 · 重新规划',()=>window.TripMaps?.open(day),'daily-route-map-button');
        mapButton.setAttribute('aria-label', `查看或调整 ${day} 的已保存路线`);
        const note=e('p','daily-hint',`点击地图可进入路线规划。${route.planned<route.total || route.incomplete ? '部分路段尚未规划或轨迹不完整。' : '已显示全部已保存路线，耗时为规划时参考。'}`);
        shell.append(canvas,mapButton); overview={canvas,date:day,note,version:route.version};
        const places=e('div','daily-route-places');
        route.stops.forEach(stop=>places.append(e('span','daily-route-place',`${stop.number}. ${stop.name}`)));
        card.append(header,shell,places,note,button('查看 / 调整路线',()=>window.TripMaps?.open(day)));
        section.append(card);
      }
      const layout=e('div','daily-schedule-layout'), slots=e('div','daily-schedule-slots');
      const navigation=e('nav','daily-slot-nav'); navigation.setAttribute('aria-label','当天时段快速跳转');
      navigation.append(e('h4','','时段导航'));
      const links=e('div','daily-slot-links'); navigation.append(links);
      layout.append(slots,navigation);
      const groups = [...(visits.some(v => groupId(v) === "") ? [["", "待安排", "", ""]] : []), ...blocks,
        ...(visits.some(v => v.is_backup) ? [["backup", "备选行程", "", ""]] : [])];
      for (const [id, name, start, end] of groups) {
        const members = visits.filter(v => groupId(v) === id), group = e("section", "daily-slot"), header = e("div", "daily-slot-heading");
        group.dataset.blockId = id;
        group.id = 'daily-slot-' + (id || 'unscheduled');
        group.setAttribute('tabindex','-1');
        const jump=button('',()=>{
          group.scrollIntoView({behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',block:'start'});
          group.focus({preventScroll:true});
        },'daily-slot-link');
        jump.setAttribute('aria-label',`跳转到${name}，${members.length}项行程`);
        jump.setAttribute('aria-controls',group.id);
        jump.append(e('span','daily-slot-link-title',name),e('span','daily-slot-link-time',start ? `${start}–${end}` : '当天安排'),e('span','daily-slot-link-count',`${members.length} 项`));
        links.append(jump);
        const title = e("h4", "daily-block", start ? `${name} ${start}–${end}` : name);
        header.append(title);
        if (id !== "backup") {
          const add = button("＋ 添加地点", () => open(day, false, id), "daily-slot-add");
          add.setAttribute("aria-label", `向${name}添加地点`); header.append(add);
        }
        group.append(header);
        group.addEventListener("dragover", event => { if (dragged?.day === day) event.preventDefault(); });
        group.addEventListener("drop", event => {
          event.preventDefault(); if (dragged?.day === day) move(day, dragged.id, null, id); dragged = null;
        });
        if (!members.length) group.append(e("p", "daily-slot-empty", "暂未安排 · 可留空休息或拖入地点"));
        members.forEach((visit, index) => {
          const card = itineraryCard(visit, { showTimeBlock: false }); card.draggable = true; card.dataset.visitId = visit.id;
          card.addEventListener("dragstart", event => { dragged = { id: visit.id, day }; event.dataTransfer?.setData("text/plain", visit.id); });
          card.addEventListener("dragover", event => { if (dragged?.day === day) event.preventDefault(); });
          card.addEventListener("drop", event => { event.preventDefault(); event.stopPropagation(); if (dragged?.day === day && dragged.id !== visit.id) move(day, dragged.id, visit.id, id); dragged = null; });
          card.addEventListener("dragend", () => { dragged = null; });
          const actions = e("div", "daily-card-actions");
          if (visit.is_backup) { const restore = button("添加到行程中", () => restoreBackup(day, visit), "secondary-button"); restore.dataset.dayAction = ''; restore.disabled = dayMutation; actions.append(restore); }
          if (!visit.opening_start && !['legacy','rest','transit'].includes(visit.visit_kind)) actions.append(button("AI 补充开放时间", () => openHours(day, visit.id), "daily-order-button"));
          if (index) actions.append(button("↑ 上移", () => move(day, visit.id, members[index-1].id, id), "daily-order-button"));
          if (index < members.length-1) actions.append(button("↓ 下移", () => move(day, visit.id, members[index+1].id, id), "daily-order-button"));
          if (actions.children.length) card.append(actions);
          group.append(card);
        });
        slots.append(group);
      }
      section.append(layout);
      wrapper.append(section);
    }
    dom.cards.replaceChildren(wrapper);
    window.TripMaps?.mountOverview?.(overview?.canvas,overview?.date,overview?.note,overview?.version);
  }
  return { blocks, options, label, render, open, openHours, selectedDate: () => selectedDates.get(scope()) || '' };
})();
