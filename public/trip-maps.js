"use strict";

const TripMapPlan = (() => {
  const MODES = ['transit', 'walking', 'driving', 'bicycling'];
  function minutes(value) {
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(value || '')) return null;
    const [h, m] = value.split(':').map(Number); return h * 60 + m;
  }
  function clock(value) {
    if (!Number.isFinite(value)) return '待核算';
    const n = Math.ceil(value), day = Math.floor(n / 1440), t = n % 1440;
    return `${day ? `次日${day > 1 ? `+${day - 1}` : ''} ` : ''}${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`;
  }
  function pair(from, to) { return JSON.stringify([from, to]); }
  function nodes(plan, order = null) {
    const active = plan.visits.filter(v => !v.is_backup).sort((a, b) => a.position - b.position);
    if (order && (order.length !== active.length || new Set(order).size !== active.length || order.some(id => !active.some(v => v.id === id))))
      throw new Error('候选方案中的访问引用不完整，请重新核算。');
    const visits = (order ? order.map(id => active.find(v => v.id === id)) : active).map(v => ({
      id: v.id, name: v.location || v.title, query: v.location || v.title, time: '',
      stay: Number.isInteger(v.duration_minutes) && v.duration_minutes > 0 ? v.duration_minutes : null,
      block: v.time_block || '', kind: v.visit_kind, poi: v.poi || null, anchor: false,
    }));
    const anchor = (id, poi, label) => ({ id, poi, anchor: true, name: poi.name || label, query: '', stay: 0, time: '', block: '', kind: 'anchor' });
    if (plan.settings.start_anchor) visits.unshift(anchor('@start', plan.settings.start_anchor, '出发地点'));
    if (plan.settings.end_anchor) visits.push(anchor('@end', plan.settings.end_anchor, '返回地点'));
    return visits;
  }
  function segments(stops, selected = {}, previous = []) {
    const routeStops = stops.filter(stop => !(stop.kind === 'rest' && !stop.poi));
    return routeStops.slice(0, -1).map((stop, i) => {
      const from_ref = stop.id, to_ref = routeStops[i + 1].id, id = pair(from_ref, to_ref);
      const mode = MODES.includes(selected[id]) ? selected[id] : 'transit';
      const old = previous.find(leg => leg.id === id && leg.mode === mode);
      return old ? { ...old } : { id, from_ref, to_ref, mode, result: null, status: 'idle', error: '', departure: null, source: 'reference' };
    });
  }
  function bufferFor(seconds, settings) {
    return Math.max(settings.buffer_minutes ?? 10, Math.ceil(Math.ceil(seconds / 60) * (settings.buffer_ratio ?? 0.2)));
  }
  function referenceDeparture(stops, legs, index, plan) {
    let ready = minutes(plan.settings.start_time) ?? 540, known = true;
    for (let i = 0; i <= index; i++) {
      const stop = stops[i];
      if (stop.stay === null || stop.kind === 'legacy') {
        if (i === index && stop.kind === 'legacy') return null;
        known = false;
      }
      if (i && !(stop.kind === 'rest' && !stop.poi)) {
        const result = legs.find(leg => leg.to_ref === stop.id)?.result;
        if (!result) known = false;
        else if (known) ready += Math.ceil(result.duration / 60) + bufferFor(result.duration, plan.settings);
      }
      const block = (plan.blocks || []).find(b => b.id === stop.block);
      const bound = minutes(stop.time) ?? minutes(block?.start) ?? (minutes(plan.settings.start_time) ?? 540);
      ready = (known ? Math.max(ready, bound) : bound) + (stop.stay ?? 0);
    }
    // Local estimates never account for all protected blocks/anchors/constraints.
    return { minutes: ready, provisional: true, context: known ? 'estimated-prefix' : 'missing-prefix' };
  }
  return { minutes, clock, pair, nodes, segments, bufferFor, referenceDeparture };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = TripMapPlan;
if (typeof window !== 'undefined') window.TripMaps = (() => {
  let dialog, view, map, sdkPromise, controller, generation = 0, busy = false, enabled = false;
  let plan = null, stops = [], legs = [], day = '', signature = '', activeScope = '', observedRevision = null;
  let saving = false, checking = false, conflicted = false, selectedLeg = -1;
  let routeLines = [], showWholeRoute = true, overview = null;
  let picking = null, pickMarker = null;
  const drafts = new Map();
  const modes = { transit: '公交 / 地铁', walking: '步行', bicycling: '骑行', driving: '驾车' };
  const colors = ['#2458bd', '#bd4b18', '#12815f', '#8750bd', '#bd3765'];
  const scope = () => JSON.stringify([state.projectId || window.TripProject.id, state.cityId, state.userId, state.projectUnlocked]);
  const fingerprint = () => JSON.stringify(state.items.itinerary.filter(x => x.date === day && x.city_id === state.cityId));
  const name = i => stops[i].poi?.name || stops[i].name;
  const endpoints = leg => [stops.find(s => s.id === leg.from_ref), stops.find(s => s.id === leg.to_ref)];
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  function button(label, action, cls = 'secondary-button') { const n = el('button', cls, label); n.type = 'button'; n.addEventListener('click', action); return n; }
  function nav(label, action, cls) { const n = button(label, action, cls); n.setAttribute('data-map-nav', ''); return n; }
  const message = text => { view.status.textContent = text; };
  const valid = token => generation === token && dialog.open && activeScope === scope() && !conflicted;
  async function dayRequest(method = 'GET', suffix = '', body = {}) {
    const url = `/api/day-plan${suffix}${method === 'GET' ? `?city_id=${encodeURIComponent(state.cityId)}&date=${encodeURIComponent(day)}` : ''}`;
    return (await requestJson(url, { method, signal: controller.signal,
      ...(method === 'GET' ? {} : { body: JSON.stringify({ city_id: state.cityId, date: day, version: plan.version, ...body }) }) })).data;
  }
  async function mapRequest(action, body) {
    return (await requestJson(`/api/maps/${action}`, { method: 'POST', signal: controller.signal,
      body: JSON.stringify({ city_id: state.cityId, ...body }) })).data;
  }
  function setBusy(value) {
    busy = value;
    dialog.querySelectorAll('button:not([data-map-close]):not([data-map-nav]), input').forEach(n => { n.disabled = busy || !enabled || conflicted; });
    view.reload.disabled = busy; view.reload.hidden = !conflicted;
  }
  function clearLeg(leg, status = 'idle') { leg.result = null; leg.status = status; leg.error = ''; leg.departure = null; leg.source = 'reference'; }
  function refDeparture(i) {
    const leg = legs[i];
    if (leg.source === 'evaluation' && leg.result) return leg.departure;
    return TripMapPlan.referenceDeparture(stops, legs, stops.findIndex(s => s.id === leg.from_ref), plan);
  }
  function reconcile() {
    legs.forEach((leg, i) => {
      if (!leg.result || leg.mode !== 'transit' || leg.source === 'evaluation') return;
      const next = refDeparture(i);
      if (!next || leg.departure?.minutes !== next.minutes || leg.departure?.provisional !== next.provisional || leg.departure?.context !== next.context) clearLeg(leg, 'stale');
    });
  }
  function adopt(next, preserve = false) {
    cancelPick();
    if (!next || typeof next.version !== 'string' || !Array.isArray(next.visits) || !next.settings) throw new Error('每日计划返回格式无效。');
    const oldStops = stops, oldDate = plan?.date, selectedId = legs[selectedLeg]?.id;
    plan = next; stops = TripMapPlan.nodes(plan);
    const savedLegs = (plan.routes || []).map(l => ({ ...l, id: TripMapPlan.pair(l.from_ref, l.to_ref) }));
    legs = TripMapPlan.segments(stops, plan.settings.leg_modes || {}, savedLegs);
    legs.forEach(leg => {
      if (savedLegs.some(l => l.id === leg.id)) return;
      const changed = [leg.from_ref, leg.to_ref].some(id => JSON.stringify(oldStops.find(s => s.id === id)?.poi) !== JSON.stringify(stops.find(s => s.id === id)?.poi));
      if (changed || oldDate !== next.date) clearLeg(leg);
      else leg.source = 'reference';
    });
    selectedLeg = Math.max(0, legs.findIndex(l => l.id === selectedId));
    reconcile(); renderStops(); render();
  }
  function markConflict(text = '当天计划已被修改。当前输入保留，请载入最新日计划后再保存或规划。') {
    cancelPick();
    conflicted = true; generation++; controller?.abort(); legs.forEach(l => clearLeg(l, 'stale'));
    busy = false; saving = false; checking = false; render(); message(text);
  }
  async function afterOwnSave(token) {
    await fetchSnapshot(true, true); if (!valid(token)) return false;
    const revision = state.revision, nextSignature = fingerprint();
    const latest = await dayRequest(); if (!valid(token)) return false;
    if (latest.version !== plan.version) { markConflict('你的修改已保存，但当天计划随后又有新修改。请载入最新版继续。'); return false; }
    signature = nextSignature; observedRevision = revision; return true;
  }
  async function savePatch(body, onSaved = null) {
    if (busy || conflicted || !enabled) return false;
    const token = generation; saving = true; setBusy(true); let written = false;
    try {
      const next = await dayRequest('PUT', '', body); if (!valid(token)) return false;
      written = true;
      adopt(next, true);
      if (!await afterOwnSave(token)) return false;
      message('已保存到共享日计划。');
    } catch (error) {
      if (valid(token)) {
        if (error.status === 409) markConflict();
        else if (written) markConflict('修改已保存，但同步核对未完成。请载入最新日计划后继续。');
        else message(error.message || '保存失败，输入仍保留，可重试。');
      }
      return false;
    } finally { if (valid(token)) { saving = false; setBusy(false); } }
    if (onSaved && valid(token)) await onSaved();
    return true;
  }
  async function reloadPlan() {
    if (busy) return;
    generation++; controller?.abort(); controller = new AbortController(); conflicted = false; checking = false;
    const token = generation; setBusy(true);
    try {
      const next = await dayRequest(); if (!valid(token)) return;
      drafts.clear(); adopt(next); signature = fingerprint(); observedRevision = state.revision;
      message('已载入最新共享日计划。');
    } catch (error) { if (valid(token)) markConflict(error.message); }
    finally { if (valid(token)) setBusy(false); }
  }
  function focusLeg(i) {
    if (!legs[i]) return;
    cancelPick();
    showWholeRoute = false; selectedLeg = i; renderStops(); render();
    view.segments.children[0]?.focus({ preventScroll: true });
  }
  function focusStop(i) {
    cancelPick();
    const stop = stops[i]; if (!stop) return;
    const outgoing = legs.findIndex(l => l.from_ref === stop.id);
    const incoming = legs.findIndex(l => l.to_ref === stop.id);
    if (outgoing >= 0 || incoming >= 0) focusLeg(outgoing >= 0 ? outgoing : incoming);
    else { selectedLeg = -1; renderStops([stop]); render(); }
  }
  function init() {
    if (dialog) return;
    dialog = el('dialog', 'trip-map-dialog'); dialog.setAttribute('aria-labelledby', 'trip-map-title');
    dialog.innerHTML = `<header class="trip-map-heading"><div><p class="eyebrow">每日出行 · 分段规划</p><h2 id="trip-map-title">规划路线</h2></div><button type="button" class="icon-button" data-map-close aria-label="关闭地图">×</button></header>
      <section class="trip-map-main" aria-label="当天路线地图"><div class="trip-map-canvas" aria-label="高德地图"></div><div class="trip-map-picker" role="status" hidden></div></section>
      <section class="trip-map-overview" aria-label="今天整体行程"><div class="trip-overview-heading"><h3>当天行程</h3><button type="button" class="secondary-button" data-map-nav data-fit>查看全程地图</button></div><p class="trip-map-summary"></p><div class="trip-map-timeline" aria-label="左右滑动选择行程或路段"></div></section>
      <p class="trip-map-status" role="status" aria-live="polite"></p><button type="button" class="secondary-button" data-map-nav data-reload hidden>载入最新日计划</button>
      <section class="trip-map-editor" aria-label="当前路段规划"><div class="trip-map-stops"></div><div class="trip-map-segments"></div></section>`;
    document.body.append(dialog);
    const q = selector => dialog.querySelector(selector);
    view = { picker: q('.trip-map-picker'), status: q('.trip-map-status'), list: q('.trip-map-stops'), segments: q('.trip-map-segments'), summary: q('.trip-map-summary'),
      timeline: q('.trip-map-timeline'), canvas: q('.trip-map-canvas'), overview: q('.trip-map-overview'),
      reload: q('[data-reload]') };
    q('[data-map-close]').addEventListener('click', () => dialog.close());
    q('[data-fit]').addEventListener('click', () => { showWholeRoute = true; drawMap(); view.canvas.scrollIntoView({ behavior: 'smooth', block: 'center' }); });
    view.reload.addEventListener('click', reloadPlan);
    dialog.addEventListener('close', () => {
      cancelPick();
      generation++; controller?.abort(); map?.destroy(); map = null; plan = null; stops = []; legs = []; routeLines = [];
      busy = enabled = saving = checking = conflicted = false;
      drafts.clear();
      [view.canvas, view.list, view.timeline, view.segments, view.summary].forEach(n => n.replaceChildren());
      if (state.projectUnlocked) fetchSnapshot(true, true).catch(() => {});
    });
  }
  function loadSDK(config) {
    if (window.AMap) return Promise.resolve();
    if (sdkPromise) return sdkPromise;
    window._AMapSecurityConfig = { serviceHost: location.origin + config.service_host };
    sdkPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      const fail = () => { clearTimeout(timer); script.remove(); sdkPromise = null; reject(new Error("高德地图加载失败，请检查网络、JS Key 和域名白名单，关闭后重试。")); };
      const timer = setTimeout(fail, 20000);
      script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(config.js_key)}`;
      script.onload = () => { if (!window.AMap) return fail(); clearTimeout(timer); resolve(); };
      script.onerror = fail;
      document.head.append(script);
    });
    return sdkPromise;
  }
  function drawMap(showAll = showWholeRoute) {
    if (!map) return;
    map.clearMap(); routeLines = []; const points = [];
    stops.forEach((stop, i) => {
      if (!stop.poi || stop.kind === 'legacy') return;
      const marker = new AMap.Marker({ position: stop.poi.location.split(',').map(Number), content: el('span', 'trip-map-pin', stop.id === '@start' ? '起' : stop.id === '@end' ? '终' : String(i + 1)), title: name(i) });
      marker.on('click', () => picking ? pickLocation(stop.poi.location.split(',').map(Number), stop.poi) : focusStop(i)); points.push(marker);
    });
    legs.forEach((leg, index) => ((showAll || index === selectedLeg) ? leg.result?.parts || [] : []).forEach(path => {
      const line = new AMap.Polyline({ path, strokeColor: colors[index % colors.length], strokeWeight: selectedLeg === index ? 8 : 6, strokeOpacity: 0.85, showDir: true, cursor: 'pointer', bubble: false });
      line.on('click', event => picking ? pickMapEvent(event) : focusLeg(index)); routeLines.push({ line, index });
    }));
    const overlays = [...points, ...routeLines.map(x => x.line)]; map.add(overlays); if (overlays.length) map.setFitView(!showAll && routeLines.length ? routeLines.map(x => x.line) : overlays, true, [45, 45, 45, 45], 16);
    if (pickMarker) map.add([pickMarker]);
  }
  function cancelPick() {
    if (pickMarker && map) map.remove(pickMarker);
    pickMarker = null; picking = null;
    if (view?.picker) { view.picker.replaceChildren(); view.picker.hidden = true; }
  }
  function renderPicker() {
    view.picker.replaceChildren(); view.picker.hidden = !picking;
    if (!picking) return;
    const target = picking;
    view.picker.append(el('strong', '', `选择${target.label}：${target.stop.name}`),
      el('p', '', target.poi ? `已选位置：${target.poi.location}。可继续点击地图调整，确认后同步到该行程的相邻路段。` : '请在地图上点击具体入口或道路位置，也可点击已有地点标记。确认后才保存。'));
    if (target.poi) view.picker.append(button(`确认${target.label}位置`, async () => {
      if (picking !== target || !valid(target.token)) return;
      const stop = target.stop, poi = target.poi;
      await savePatch(stop.anchor ? { settings: { [stop.id === '@start' ? 'start_anchor' : 'end_anchor']: poi } } : { updates: [{ id: stop.id, changes: { poi } }] });
    }, 'primary-button'));
    view.picker.append(nav('取消选点', cancelPick));
    setBusy(busy);
  }
  function startPick(stop, label) {
    if (busy || conflicted || !enabled || !map) return;
    cancelPick(); picking = { stop, label, token: generation, poi: null }; renderPicker();
    if (stop.poi) map.setZoomAndCenter(16, stop.poi.location.split(',').map(Number));
    view.canvas.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  function pickMapEvent(event) {
    if (event?.lnglat) pickLocation([event.lnglat.getLng(), event.lnglat.getLat()]);
  }
  function pickLocation(coords, existing = null) {
    if (!picking || busy || !valid(picking.token)) return;
    if (coords.length !== 2 || !coords.every(Number.isFinite) || Math.abs(coords[0]) > 180 || Math.abs(coords[1]) > 90) return;
    const location = coords.map(n => n.toFixed(6)).join(',');
    picking.poi = existing ? { ...existing, location } : { id: '', name: `${picking.stop.name.slice(0, 105)}（地图选点）`, address: `地图选点 · ${location}`, location };
    if (pickMarker) map.remove(pickMarker);
    pickMarker = new AMap.Marker({ position: coords, content: el('span', 'trip-map-pin trip-map-pick-pin', '选'), title: `待确认${picking.label}` });
    map.add([pickMarker]); renderPicker();
  }
  function legText(leg) {
    if (leg.status === 'loading') return '正在规划…';
    if (leg.error || ['error', 'unavailable'].includes(leg.status)) return '规划失败 · 可重试';
    if (leg.status === 'stale') return '已过期 · 待重新规划';
    if (!leg.result) return '待规划';
    return `约 ${Math.ceil(leg.result.duration / 60)} 分钟${leg.result.distance == null ? '' : ` · ${(leg.result.distance / 1000).toFixed(1)} 公里`}`;
  }
  function blockName(stop) { return (plan.blocks || []).find(b => b.id === stop.block)?.label || stop.block || '待安排时段'; }
  function render() {
    if (!plan) { setBusy(busy); return; }
    const timelineScroll = view.timeline.scrollLeft;
    const done = legs.filter(l => l.result).length, total = legs.reduce((n, l) => n + (l.result ? Math.ceil(l.result.duration / 60) : 0), 0);
    view.summary.textContent = `${done}/${legs.length} 段已查询${done ? ` · 已查询交通约 ${total} 分钟` : ' · 交通耗时待查询'}${done < legs.length ? '（未包含待规划路段）' : ''}`;
    view.timeline.replaceChildren(); view.segments.replaceChildren();
    stops.forEach((stop, i) => {
      const label = stop.id === '@start' ? '出发' : stop.id === '@end' ? '返回' : `${i + 1}`;
      const point = nav(`${label}. ${name(i)}`, () => focusStop(i), 'trip-overview-stop');
      if (!stop.anchor) point.append(el('small', '', blockName(stop)));
      point.setAttribute('aria-pressed', String(!!legs[selectedLeg] && endpoints(legs[selectedLeg]).includes(stop)));
      if (stop.kind === 'legacy') point.append(el('small', 'trip-map-warning', '多地点行程待拆分'));
      view.timeline.append(point);
      const leg = legs.find(l => l.from_ref === stop.id); if (!leg) return;
      const segmentIndex = legs.indexOf(leg), toIndex = stops.findIndex(s => s.id === leg.to_ref);
      const link = nav(`第 ${segmentIndex + 1} 段 · ${modes[leg.mode] || leg.mode} → ${legText(leg)}`, () => focusLeg(segmentIndex), 'trip-overview-leg');
      link.style.setProperty('--leg-color', colors[segmentIndex % colors.length]); link.setAttribute('aria-pressed', String(selectedLeg === segmentIndex)); view.timeline.append(link);
      if (segmentIndex !== selectedLeg) return;
      const panel = el('article', `trip-segment${selectedLeg === segmentIndex ? ' is-selected' : ''}`); panel.id = `trip-segment-${segmentIndex}`; panel.tabIndex = -1;
      panel.style.setProperty('--leg-color', colors[segmentIndex % colors.length]); panel.append(el('h4', '', `第 ${segmentIndex + 1} 段 · ${name(i)} → ${name(toIndex)}`));
      const group = el('div', 'trip-mode-options'); group.setAttribute('role', 'group'); group.setAttribute('aria-label', `第 ${segmentIndex + 1} 段交通方式`);
      Object.entries(modes).forEach(([mode, label]) => {
        const choice = button(label, async () => {
          if (leg.mode === mode) return;
          await savePatch({ settings: { leg_modes: { ...plan.settings.leg_modes, [leg.id]: mode } } });
        }, 'trip-mode-button'); choice.setAttribute('aria-pressed', String(leg.mode === mode)); group.append(choice);
      });
      panel.append(group);
      const dep = refDeparture(segmentIndex);
      panel.append(el('p', 'trip-map-note', dep ? `${dep.provisional ? '参考' : '核算'}出发：${TripMapPlan.clock(dep.minutes)}${dep.provisional ? '（仅供查路参考）' : ''}` : '请先在每日计划拆分多地点行程。'));
      const actions = el('div', 'trip-map-actions'); actions.append(button(leg.result ? '重新规划本段' : '规划本段路线', () => calculate(segmentIndex), 'primary-button'));
      for (const endpoint of [i, toIndex]) if (!stops[endpoint].poi) actions.append(nav(`确认地点 ${endpoint + 1}`, () => focusStop(endpoint)));
      panel.append(actions, el('p', leg.error ? 'trip-map-warning' : 'trip-segment-status', leg.error || legText(leg)));
      if (leg.result) {
        if (leg.saved_at) panel.append(el('p', 'trip-map-note', `路线已保存 · ${new Date(leg.saved_at * 1000).toLocaleString()} 查询；出行前可重新规划更新耗时。`));
        if (leg.source !== 'evaluation' || leg.departure?.provisional) panel.append(el('p', 'trip-map-note', '单段参考，不代表完整日程可行。'));
        const details = el('details'); details.append(el('summary', '', '查看路线与换乘说明'));
        (leg.result.instructions || []).forEach(text => details.append(el('p', '', text))); panel.append(details);
        if (leg.result.incomplete) panel.append(el('p', 'trip-map-warning', '部分轨迹缺失，地图仅展示已返回路段。'));
      }

      view.segments.append(panel);
    });
    if (!legs.length) view.segments.append(el('p', 'trip-map-note', '至少添加两个具体地点后即可规划路段。'));
    view.timeline.scrollLeft = timelineScroll;
    const active = view.timeline.querySelectorAll('.trip-overview-leg')[selectedLeg];
    if (active?.getBoundingClientRect && view.timeline.clientWidth) {
      const left = active.getBoundingClientRect().left - view.timeline.getBoundingClientRect().left + view.timeline.scrollLeft;
      if (left < view.timeline.scrollLeft) view.timeline.scrollLeft = left;
      else if (left + active.offsetWidth > view.timeline.scrollLeft + view.timeline.clientWidth)
        view.timeline.scrollLeft = left + active.offsetWidth - view.timeline.clientWidth;
    }
    drawMap(); setBusy(busy);
  }
  function renderStops(selected = null) {
    view.list.replaceChildren();
    const visible = selected || (legs[selectedLeg] ? endpoints(legs[selectedLeg]) : stops.slice(0, 1));
    visible.forEach((stop, endpointIndex) => {
      const index = stops.indexOf(stop);
      const card = el('article', 'trip-map-stop'); card.setAttribute('data-stop-id', stop.id); card.append(el('small', 'trip-endpoint-label', visible.length > 1 ? (endpointIndex === 0 ? '起点' : '终点') : '行程地点'), el('strong', '', stop.name));
      if (stop.kind === 'legacy') { card.append(el('p', 'trip-map-warning', '此行程含多个地点，请返回每日计划拆分后再规划路线。')); view.list.append(card); return; }
      const draft = drafts.get(stop.id) || {}; drafts.set(stop.id, draft);
      const row = el('div', 'trip-map-search'), input = el('input'), results = el('div', 'trip-map-search-results');
      input.value = draft.query ?? stop.query; stop.query = input.value; input.maxLength = 100; input.setAttribute('aria-label', `搜索 ${stop.name} 的具体地点`);
      input.addEventListener('input', () => { draft.query = stop.query = input.value; });
      const search = button('搜索地点', async () => {
        if (busy || conflicted) return;
        const token = generation; setBusy(true); results.replaceChildren();
        try {
          const data = await mapRequest('search', { keywords: stop.query }); if (!valid(token)) return;
          for (const poi of data.places) results.append(button(`${poi.name} · ${poi.address || '暂无详细地址'}`, () => savePatch(stop.anchor ? { settings: { [stop.id === '@start' ? 'start_anchor' : 'end_anchor']: poi } } : { updates: [{ id: stop.id, changes: { poi } }] })));
          message(data.places.length ? '选择正确地点后会保存到共享日计划。' : '未找到地点，请修改关键词。');
        } catch (error) { if (valid(token)) message(error.message); }
        finally { if (valid(token)) setBusy(false); }
      });
      input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); search.click(); } }); row.append(input, search); card.append(row, results);
      const endpointLabel = visible.length > 1 ? (endpointIndex === 0 ? '起点' : '终点') : '地点';
      card.append(button(`地图选择${endpointLabel}`, () => startPick(stop, endpointLabel), 'secondary-button trip-map-pick-button'));
      if (stop.poi) card.append(nav(`已确认：${stop.poi.name} · ${stop.poi.address}`, () => { map?.setZoomAndCenter(16, stop.poi.location.split(',').map(Number)); view.canvas.scrollIntoView({ behavior: 'smooth', block: 'center' }); }, 'secondary-button trip-map-selected'));
      view.list.append(card);
    });
  }
  async function queryLeg(index, token) {
    const leg = legs[index], [from, to] = endpoints(leg);
    if ([from, to].some(s => s.kind === 'legacy')) throw new Error('请先在每日计划拆分多地点行程。');
    if (!from.poi || !to.poi) throw new Error('请先确认本段两端的具体地点。');
    const departure = TripMapPlan.referenceDeparture(stops, legs, stops.indexOf(from), plan);
    if (!departure) throw new Error('请先在每日计划拆分多地点行程。');
    const previousDuration = leg.result?.duration;
    clearLeg(leg); leg.status = 'loading'; reconcile(); render();
    try {
      const saved = await dayRequest('POST', '/route', { from_ref: leg.from_ref, to_ref: leg.to_ref, departure });
      if (!valid(token)) return false;
      Object.assign(leg, saved);
      if (previousDuration !== leg.result.duration) legs.slice(index + 1).forEach(l => { if (l.mode === 'transit') clearLeg(l, 'stale'); });
      plan.evaluation = null;
      plan.routes = [...(plan.routes || []).filter(l => l.from_ref !== leg.from_ref || l.to_ref !== leg.to_ref), saved];
      reconcile(); render(); return true;
    } catch (error) { if (valid(token)) { leg.status = 'error'; leg.error = error.message; render(); } throw error; }
  }
  async function calculate(index) {
    if (busy || !enabled || conflicted) return;
    const token = generation; reconcile(); setBusy(true);
    try {
      message(`正在规划第 ${index + 1} 段…`);
      await queryLeg(index, token);
      if (valid(token)) message('本段路线已自动保存，重新打开仍可查看。点击其他行程或路段继续规划。');
    } catch (error) { if (valid(token)) { if (error.status === 409) markConflict(); else message(error.message); } }
    finally { if (valid(token)) setBusy(false); }
  }
  async function open(date) {
    if (!requireIdentity()) return;
    init(); if (dialog.open) return;
    showWholeRoute = true; day = date; generation++; controller = new AbortController(); const token = generation;
    activeScope = scope(); signature = fingerprint(); observedRevision = state.revision; enabled = false; conflicted = false; selectedLeg = -1;
    dialog.querySelector('h2').textContent = `${date} · 规划路线`; dialog.showModal(); setBusy(true); message('正在加载共享日计划…');
    try {
      const [next, configResponse] = await Promise.all([dayRequest(), requestJson('/api/maps/config', { signal: controller.signal })]);
      if (!valid(token)) return; adopt(next);
      const config = configResponse.data;
      if (!config.enabled) { message('尚未完成高德配置，请按 AMAP_SETUP.md 配置后重启。'); return; }
      await loadSDK(config); if (!valid(token)) return;
      map = new AMap.Map(view.canvas, { viewMode: '2D', zoom: 5, center: [104.1, 35.8] }); map.on('click', pickMapEvent); enabled = true; render();
      message('点击行程或路段切换，选择起终点和交通方式，再规划本段路线。');
    } catch (error) { if (valid(token)) message(error.message); }
    finally { if (valid(token)) setBusy(false); }
  }
  async function sync() {
    if (overview && (overview.scope !== scope() || state.tab !== 'itinerary' || !overview.canvas.isConnected)) mountOverview();
    if (!dialog?.open) return;
    if (activeScope !== scope()) { dialog.close(); return; }
    if (!plan || saving || checking || conflicted || (observedRevision === state.revision && signature === fingerprint())) return;
    const token = generation, version = plan.version, revision = state.revision, nextSignature = fingerprint(); checking = true;
    try {
      const latest = await dayRequest(); if (!valid(token) || saving || plan.version !== version) return;
      if (latest.version !== version) markConflict(); else { observedRevision = revision; signature = nextSignature; }
    } catch (error) { if (valid(token) && !saving && plan.version === version) markConflict('无法核对共享日计划版本，请载入最新版后继续。'); }
    finally { if (generation === token) checking = false; }
  }
  async function mountOverview(canvas, date, note, version) {
    // Snapshot renders replace the surrounding cards, but an unchanged map keeps its DOM and SDK instance.
    if (canvas && overview && overview.scope === scope() && overview.date === date && overview.version === version) {
      if (canvas !== overview.canvas) canvas.replaceWith(overview.canvas);
      overview.note = note;
      if (overview.error) note.textContent = overview.error;
      return;
    }
    overview?.controller.abort(); overview?.map?.destroy(); overview = null;
    if (!canvas) return;
    const current = { canvas, date, version, note, scope: scope(), controller: new AbortController(), map: null };
    overview = current;
    const validOverview = () => overview === current && current.scope === scope() && canvas.isConnected;
    try {
      const [response, configuration] = await Promise.all([
        requestJson('/api/day-plan?city_id=' + encodeURIComponent(state.cityId) + '&date=' + encodeURIComponent(date), { signal: current.controller.signal }),
        requestJson('/api/maps/config', { signal: current.controller.signal })
      ]);
      if (!validOverview()) return;
      if (!configuration.data.enabled) throw new Error('高德地图尚未配置');
      await loadSDK(configuration.data);
      if (!validOverview()) return;
      const overviewMap = current.map = new AMap.Map(canvas, { viewMode: '2D', zoom: 5, center: [104.1, 35.8], dragEnable: false, zoomEnable: false, scrollWheel: false, doubleClickZoom: false, keyboardEnable: false, touchZoom: false });
      const overlays = [];
      TripMapPlan.nodes(response.data).forEach((stop, index) => {
        if (!stop.poi || stop.kind === 'legacy') return;
        overlays.push(new AMap.Marker({ position: stop.poi.location.split(',').map(Number), content: el('span', 'trip-map-pin', stop.id === '@start' ? '起' : stop.id === '@end' ? '终' : String(index + 1)), title: stop.poi.name || stop.name }));
      });
      (response.data.routes || []).forEach((leg, index) => (leg.result?.parts || []).forEach(path => overlays.push(new AMap.Polyline({ path, strokeColor: colors[index % colors.length], strokeWeight: 6, strokeOpacity: 0.85, showDir: true }))));
      overviewMap.add(overlays);
      if (overlays.length) overviewMap.setFitView(overlays, true, [45, 35, 45, 35], 16);
      canvas.setAttribute('aria-busy', 'false');
    } catch (error) {
      if (validOverview()) { canvas.setAttribute('aria-busy', 'false'); current.note.textContent = current.error = '地图暂时无法加载：' + error.message + '。可点击下方按钮查看或重新规划。'; }
    }
  }
  return { open, sync, mountOverview };
})();
