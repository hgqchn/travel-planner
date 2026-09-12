"use strict";

// Shared step definitions; only the isolated tutorial frame installs the in-memory API.
window.TripUsageTour = (() => {
  const steps = [
    ['#city-select', '从真实主页开始', '这是一个尚未规划的上海示例项目。页面使用应用本身的布局；下一步先打开城市选择。', '打开城市选择'],
    ['#city-results .city-choice', '城市列表已打开', '点击上海后会关闭城市列表，回到该城市的主页。', '选择上海'],
    ['#ai-open', '已选择上海', '现在点击主页蓝色的 AI 入口，打开整体规划表单。', '打开 AI 旅行助手'],
    ['#ai-start-date', '先填写旅行参数', '已打开真实的 AI 表单。接下来填写 10 月 1 日出发、2 天、2 人及城市漫步偏好。', '填写示例参数'],
    ['#ai-start-date', '参数已填好，尚未生成', '出发日期、天数和人数已更新。下一步点击“生成旅行灵感”，才会出现可编辑的草稿。', '生成旅行灵感'],
    ['#ai-preview .ai-result-card', '已生成草稿，尚未加入行程', '示例结果列出了游玩点、美食和两天行程。与真实页面一样，可以先核对、编辑和勾选，再点击“确认并保存草稿”。', '确认并保存草稿'],
    ['#ai-next-step', '草稿已保存', '保存成功后，助手才出现“前往行程”入口。接下来返回行程页，查看刚导入的日期与地点。', '前往行程'],
    ['.daily-day-picker', '导入后出现日期切换', '两天的日期来自刚才保存的 AI 草稿，所以无需再新增一天。每次查看一天；手动规划时才需要先填“新增日期”。', '打开确定当天行程'],
    ['.daily-visit-row', '当天行程设置已打开', '这里显示刚导入的豫园和外滩。接下来点击豫园的“编辑”，演示如何调整停留时长。', '编辑豫园'],
    ['#edit-form [name="duration_minutes"]', '编辑豫园的停留时长', '当前为 AI 推荐的 120 分钟。下一步改为 90 分钟并点击“保存修改”，返回当天行程。', '改为 90 分钟并保存'],
    ['#daily-slot-morning .itinerary-card', '修改已显示在行程卡片上', '豫园的停留时长已变为 90 分钟。接下来点击时段导航的“下午”，查看外滩。电脑端导航在右侧，移动端在上方横向排列。', '跳转到下午'],
    ['#daily-slot-afternoon', '已跳转到下午的外滩', '只改变了浏览位置，没有新增地点。接下来点击当天标题旁的“规划路线”。', '打开规划路线'],
    ['.trip-map-stop', '路线窗口已打开，地点尚未确认', '同名地点可能对应不同入口。先搜索起点豫园，再从搜索结果中确认具体地点。地图底图和交通耗时为离线示例。', '搜索豫园'],
    ['.trip-map-search-results button', '豫园搜索结果已出现', '搜索不会直接选定地点。下一步点击这条搜索结果，起点才会显示“已确认”。', '确认豫园地点'],
    ['.trip-map-stop:nth-child(2)', '起点已确认，再搜索终点', '豫园已确认；外滩仍未确认。接下来搜索外滩的具体位置。', '搜索外滩'],
    ['.trip-map-stop:nth-child(2) .trip-map-search-results button', '外滩搜索结果已出现', '核对名称与地址后选择结果，完成本段两端的地点确认。', '确认外滩地点'],
    ['.trip-mode-options', '两端地点已确认', '当前默认交通方式为公交地铁。接下来选择“步行”，只改变本段的交通方式，还没有规划路线。', '选择步行'],
    ['.trip-segment .primary-button', '已选择步行，路线待规划', '交通方式已切换。点击“规划本段路线”后，才会出现耗时、轨迹和保存结果。', '规划本段路线'],
    ['.trip-segment-status', '本段路线已规划并自动保存', '现在显示示例步行耗时和路线说明；地图也出现已规划轨迹。关闭路线窗口后，行程页会显示保存的当天路线。', '返回行程页'],
    ['.daily-route-preview', '行程页已出现当天路线', '地图来自刚才保存的路线。本例第一天只有两个地点，共一段；更多地点需要逐段规划。接下来从页面工具栏打开导出。', '打开导出行程'],
    ['#export-dialog .export-options', '导出选项在这里', '点击“导出行程”后才出现整体行程图、Word 和 Excel 选项。引导到此完成；回到实际项目后，可以用同样的步骤规划自己的旅程。', '完成引导'],
  ];
  const tutorial = { steps };
  if (document.documentElement.dataset.usageDemo !== 'true') return tutorial;

  // Never forward requests or share browser storage with the user's project.
  for (const key of ['localStorage', 'sessionStorage']) {
    const values = new Map();
    Object.defineProperty(window, key, { value: { getItem: k => values.get(k) ?? null,
      setItem: (k, v) => values.set(k, String(v)), removeItem: k => values.delete(k), clear: () => values.clear() } });
  }
  const cities = [{ id: 'shanghai', name: '上海', province: '上海市', pinyin: 'shanghai', initials: 'sh' }];
  const visit = (id, title, date, block, minutes, position) => ({ id, city_id: 'shanghai', title, location: title,
    date, time_block: block, duration_minutes: minutes, duration_source: 'ai_estimate', position, version: 1,
    priority: 'preferred', visit_kind: 'attraction', attraction_names: [title], opening_start: '', opening_end: '',
    opening_source: 'unknown', notes: '', category: '游览', is_backup: false, block_locked: false });
  let items = { itinerary: [], attraction: [], food: [], transit: [] }, revision = 1, job;
  const settings = { start_time: '09:00', end_time: '22:00', leg_modes: {}, start_anchor: null, end_anchor: null };
  let routes = [];
  const date = '2026-10-01';
  const plan = () => ({ city_id: 'shanghai', date, version: String(revision), settings,
    visits: items.itinerary.filter(v => v.date === date), routes, blocks: [] });
  const snapshot = () => ({ revision, cities, project: { name: '上海漫步 · 引导示例' }, items,
    project_itinerary: { cities: items.itinerary.length ? [{ city_id: "shanghai", city_name: "上海", dates: [date, "2026-10-02"], start_date: date, end_date: "2026-10-02", count: items.itinerary.length, version: String(revision) }] : [], conflicts: [] }, daily_plans: items.itinerary.length ? [
      { city_id: 'shanghai', date, ...(routes.length ? { route_preview: { version: String(revision), planned: 1,
        total: 1, minutes: 25, stops: plan().visits.map((v, i) => ({ number: i + 1, name: v.title })) } } : {}) },
      { city_id: 'shanghai', date: '2026-10-02' }] : [] });
  function api(path, body, method) {
    if (path === '/api/snapshot') return snapshot();
    if (path === '/api/metro-map') return { supported: false };
    if (path === '/api/ai/config') return { enabled: true, model: 'deepseek-v4-flash-vision-exp' };
    if (path === '/api/maps/config') return { enabled: true };
    if (path === '/api/ai/jobs' && method === 'POST') {
      const itinerary = [visit('demo-yuyuan', '豫园', date, 'morning', 120, 1),
        visit('demo-bund', '外滩', date, 'afternoon', 120, 2),
        visit('demo-museum', '上海博物馆', '2026-10-02', 'morning', 120, 1)];
      job = { id: '0123456789abcdef0123456789abcdef', status: 'ready', city_id: 'shanghai', city_name: '上海',
        request: body, result: { itineraries: itinerary, attractions: itinerary.map((v, i) => ({ id: `place-${i}`, name: v.title,
          city_id: 'shanghai', description: '城市漫步示例游玩点', duration: '约 2 小时', tags: [], category: '其他' })),
          foods: [{ name: '生煎', city_id: 'shanghai', category: '小吃', tags: [], description: '示例本地美食' }] } };
      return { job };
    }
    if (path.endsWith('/import') && method === 'POST') {
      body.items.forEach(({ kind, data }, i) => items[kind].push({ ...data, id: `import-${i}`, city_id: 'shanghai',
        position: kind === 'itinerary' ? items.itinerary.filter(v => v.date === data.date).length + 1 : i + 1,
        version: 1, updated_by: '示例用户', updated_at: '2026-09-12T08:00:00Z', is_backup: false, block_locked: false }));
      revision++; return { created: body.items.length, skipped: 0 };
    }
    if (path.startsWith('/api/items/itinerary/') && method === 'PUT') {
      const v = items.itinerary.find(v => v.id === path.split('/').at(-1));
      Object.assign(v, body); revision++; return { item: v };
    }
    if (path === '/api/day-plan') {
      if (method === 'PUT') {
        if (body.settings) Object.assign(settings, body.settings);
        for (const update of body.updates || []) Object.assign(items.itinerary.find(v => v.id === update.id), update.changes);
        revision++;
      }
      return plan();
    }
    if (path === '/api/maps/search') {
      const bund = body.keywords.includes('外滩');
      return { places: [{ id: bund ? 'poi-bund' : 'poi-yuyuan', name: bund ? '外滩' : '豫园',
        address: bund ? '上海市黄浦区中山东一路' : '上海市黄浦区福佑路', location: bund ? '121.4906,31.2415' : '121.4921,31.2272' }] };
    }
    if (path === '/api/day-plan/route' && method === 'POST') {
      const route = { from_ref: body.from_ref, to_ref: body.to_ref, mode: 'walking', status: 'ready', saved_at: 1790816400,
        departure: body.departure, source: 'reference', result: { duration: 1500, distance: 1800,
          parts: [[[121.4921,31.2272],[121.4895,31.232],[121.4906,31.2415]]], instructions: ['示例：步行约 25 分钟到外滩。'] } };
      routes = [route]; revision++; return route;
    }
    throw new Error(`引导未提供此操作的示例：${method} ${path}`);
  }
  window.fetch = async (input, options = {}) => {
    const url = new URL(String(input), document.baseURI), method = options.method || 'GET';
    try { return new Response(JSON.stringify(api(url.pathname, options.body ? JSON.parse(options.body) : {}, method)),
      { headers: { 'Content-Type': 'application/json' } }); }
    catch (error) { return new Response(JSON.stringify({ error: error.message }), { status: 400 }); }
  };

  // Offline map adapter: the application's map UI and route renderer stay unchanged.
  class Overlay { constructor(options) { this.options = options; } on() {} }
  class DemoMap {
    constructor(canvas) { this.canvas = canvas; this.overlays = []; this.draw(); }
    on() {} destroy() {} setFitView() {} setZoomAndCenter() {} setStatus() {} setDefaultCursor() {}
    add(overlays) { this.overlays.push(...(Array.isArray(overlays) ? overlays : [overlays])); this.draw(); }
    clearMap() { this.overlays = []; this.draw(); }
    remove() {}
    draw() {
      this.canvas.replaceChildren();
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('viewBox', '0 0 600 320'); svg.setAttribute('width', '100%'); svg.setAttribute('height', '100%');
      const project = ([x,y]) => [100 + (x - 121.486) * 45000, 285 - (y - 31.225) * 15000];
      svg.innerHTML = '<rect width="600" height="320" fill="#edf2ed"/><path d="M460 0 Q340 110 480 320" fill="none" stroke="#bddfed" stroke-width="95"/><path d="M40 70H370 M40 150H330 M40 230H360 M130 30V300 M245 30V300" fill="none" stroke="white" stroke-width="10"/><text x="18" y="24" fill="#526c8a" font-size="13">离线示例地图 · 非实时导航</text>';
      for (const { options } of this.overlays) {
        if (options.path) {
          const line = document.createElementNS(svg.namespaceURI, 'polyline');
          line.setAttribute('points', options.path.map(p => project(p).join(',')).join(' '));
          line.setAttribute('fill', 'none'); line.setAttribute('stroke', '#3975cc'); line.setAttribute('stroke-width', '6'); svg.append(line);
        } else if (options.position) {
          const [x,y] = project(options.position), pin = document.createElementNS(svg.namespaceURI, 'text');
          pin.setAttribute('x', x); pin.setAttribute('y', y); pin.setAttribute('fill', '#245bb4'); pin.textContent = `● ${options.title || ''}`; svg.append(pin);
        }
      }
      this.canvas.append(svg);
    }
  }
  window.AMap = { Map: DemoMap, Marker: Overlay, Polyline: Overlay };
  const q = selector => document.querySelector(selector);
  const click = selector => { const node = q(selector); if (!node || node.disabled) throw new Error(`示例控件不可用：${selector}`); node.click(); };
  const fill = (selector, value) => { const node = q(selector); node.value = value; node.dispatchEvent(new Event('input', { bubbles: true })); node.dispatchEvent(new Event('change', { bubbles: true })); };
  async function settle() { for (let i = 0; i < 4; i++) await new Promise(resolve => setTimeout(resolve, 0)); }
  const actions = [
    () => click('#city-select'), () => click('#city-results .city-choice'),
    () => window.TripUI.openPlanning('full'),
    () => { fill('#ai-start-date', date); fill('#ai-form [name="days"]', '2'); fill('#ai-form [name="people"]', '2'); fill('#ai-form [name="preferences"]', '城市漫步'); },
    () => click('#ai-generate'), () => click('#ai-import'), () => click('#ai-next-step-open'),
    () => click('[data-tour-target="daily-confirm"]'), () => click('.daily-visit-row button'),
    () => { fill('#edit-form [name="duration_minutes"]', '90'); click('#save-button'); },
    () => click('.daily-slot-link[aria-controls="daily-slot-afternoon"]'),
    () => click('[data-tour-target="daily-route"]'), () => click('.trip-map-stop .trip-map-search button'),
    () => click('.trip-map-search-results button'), () => click('.trip-map-stop:nth-child(2) .trip-map-search button'),
    () => click('.trip-map-stop:nth-child(2) .trip-map-search-results button'),
    () => { const button = [...document.querySelectorAll('.trip-mode-button')].find(n => n.textContent === '步行'); button.click(); },
    () => click('.trip-segment .primary-button'), () => click('[data-map-close]'), () => click('#export-open'),
  ];
  tutorial.advance = async index => { await actions[index](); await settle(); };
  tutorial.boot = async () => {
    state.projectUnlocked = true; state.userId = '示例用户'; state.cityId = 'shanghai';
    document.body.classList.remove('is-booting', 'project-locked');
    await fetchSnapshot(true);
    dom.identityLabel.textContent = '示例用户';
    tutorial.ready = true;
  };
  return tutorial;
})();
