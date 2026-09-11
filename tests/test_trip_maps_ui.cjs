const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const flush = () => new Promise(resolve => setImmediate(resolve));

function harness(options = {}) {
  class Node {
    constructor(tag = 'div') {
      this.tagName = tag; this.children = []; this.attrs = {}; this.events = {}; this.className = ''; this.value = '';
      this.style = { setProperty() {} }; this.open = false; this.disabled = false;
      this.classList = { toggle: (name, on) => { this.className = this.className.split(' ').filter(x => x !== name).concat(on ? [name] : []).join(' '); } };
    }
    set textContent(value) { this.text = String(value); this.children = []; }
    get textContent() { return (this.text || '') + this.children.map(n => n.textContent).join(''); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; this.text = ''; this.scrollLeft = 0; }
    setAttribute(name, value) { this.attrs[name] = value; if (name === 'class') this.className = value; if (name === 'value') this.value = value; }
    getAttribute(name) { return this.attrs[name] ?? null; }
    addEventListener(name, fn) { this.events[name] = fn; }
    showModal() { this.open = true; }
    close() { this.open = false; this.events.close?.(); }
    focus() { this.focused = true; }
    scrollIntoView() { this.scrolled = true; }
    matches(selector) {
      if (selector.startsWith('button:not(')) return this.tagName === 'button' && !('data-map-close' in this.attrs) && !('data-map-nav' in this.attrs);
      if (selector.startsWith('.')) return this.className.split(' ').includes(selector.slice(1));
      if (selector.startsWith('[')) return selector.slice(1, -1) in this.attrs;
      return this.tagName === selector;
    }
    querySelectorAll(selector) {
      const all = this.children.flatMap(n => [n, ...n.querySelectorAll('*')]);
      return selector === '*' ? all : all.filter(n => selector.split(',').some(s => n.matches(s.trim())));
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    set innerHTML(html) {
      this.children = []; const stack = [this];
      for (const token of html.match(/<[^>]+>|[^<]+/g)) {
        if (token.startsWith('</')) { stack.pop(); continue; }
        if (!token.startsWith('<')) { const text = new Node('#text'); text.textContent = token; stack.at(-1).append(text); continue; }
        const tag = token.match(/^<([\w-]+)/)[1], node = new Node(tag);
        for (const attr of token.slice(tag.length + 1, -1).matchAll(/([\w-]+)(?:="([^"]*)")?/g)) node.setAttribute(attr[1], attr[2] ?? '');
        stack.at(-1).append(node); if (!['input', 'br'].includes(tag)) stack.push(node);
      }
    }
  }
  const body = new Node('body'), requests = [], maps = [];
  class Overlay {
    constructor(options) { this.options = options; this.events = {}; }
    on(name, fn) { this.events[name] = fn; }
    setOptions(options) { Object.assign(this.options, options); }
  }
  class MapStub {
    constructor() { this.overlays = []; this.events = {}; maps.push(this); }
    on(name, fn) { this.events[name] = fn; }
    remove(node) { this.overlays = this.overlays.filter(n => n !== node); }
    clearMap() { this.overlays = []; }
    add(nodes) { this.overlays.push(...nodes); }
    setFitView(nodes) { this.fit = nodes; }
    setZoomAndCenter() {}
    destroy() { this.destroyed = true; }
  }
  const AMap = { Map: MapStub, Marker: Overlay, Polyline: Overlay };
  const state = { projectId: 'main', cityId: 'shanghai', userId: '测试', projectUnlocked: true,
    items: { itinerary: ['甲', '乙', '丙'].map((name, i) => ({ id: String(i), city_id: 'shanghai', date: '2026-09-12', title: name, location: name, position: i, start_time: '07:00', fixed_start: '', duration_minutes: 60, time_block: 'morning', visit_kind: 'attraction', poi: null })) } };
  state.revision = 1;
  const document = { city_id: 'shanghai', date: '2026-09-12', version: 'v1', visits: state.items.itinerary, settings: { start_time: '09:00', end_time:'20:00', buffer_minutes:10, buffer_ratio:.2, leg_modes:{}, start_anchor:null, end_anchor:null }, blocks: [{id:'morning',label:'上午',start:'09:00'}] };
  if (options.setup) options.setup(document);
  let failure = null, evaluations = null, candidateOrders = null, refreshes = 0;
  const clone = value => JSON.parse(JSON.stringify(value));
  let handler = async payload => ({ duration: payload.mode === 'walking' ? 1200 : 600, distance: 1000, parts: [[[121, 31], [121.1, 31.1]]], instructions: ['按路标步行'], incomplete: false });
  const env = { window: { AMap, TripProject: { id: 'main' } }, AMap, state, AbortController, Date, console, setTimeout, clearTimeout,
    location: { origin: 'http://localhost' }, document: { body, createElement: tag => new Node(tag) },
    requireIdentity: () => true, showToast() {},
    fetchSnapshot: async () => { refreshes++; state.revision++; state.items.itinerary = clone(document.visits); await env.window.TripMaps.sync(); },
    requestJson: async (url, options = {}) => {
      const payload = options.body ? JSON.parse(options.body) : null; requests.push({ url, payload, method: options.method || 'GET' });
      if (failure && url === '/api/day-plan' && options.method === 'PUT') throw failure;
      if (url.startsWith('/api/day-plan?')) return {data:clone(document)};
      if (url === '/api/day-plan' && options.method === 'PUT') {
        assert.equal(payload.version, document.version);
        for (const update of payload.updates || []) Object.assign(document.visits.find(v=>v.id===update.id),update.changes);
        Object.assign(document.settings,payload.settings || {});
        document.routes = [];
        document.version = 'v'+(Number(document.version.slice(1))+1); return {data:clone(document)};
      }
      if (url.endsWith('/evaluate')) {
        assert.equal(payload.version,document.version);
        const order = document.visits.filter(v=>!v.is_backup).sort((a,b)=>a.position-b.position).map(v=>v.id);
        const orders = candidateOrders || (payload.optimize ? [order,[...order].reverse()] : [order]);
        const candidates = orders.map((order,i)=>({candidate_ref:'c'+i,order,evaluation:{time_fit:'fits',route_status:'checked',constraint_status:'needs_verification',travel_minutes:20,buffer_minutes:20,slack_minutes:40,issues:[{code:'hours',message:'开放时间待核实'}], rows:[],legs:order.slice(0,-1).map((from_ref,j)=>({from_ref,to_ref:order[j+1],mode:document.settings.leg_modes[JSON.stringify([from_ref,order[j+1]])]||'transit',result:{duration:600,distance:1000,parts:[[[121,31],[121.1,31.1]]],instructions:['测试路线']},status:'checked',departure:{minutes:600+j*80,provisional:false}}))}}));
        evaluations = {version:document.version,candidates}; return {data:clone(evaluations)};
      }
      if (url.endsWith('/apply')) {
        assert.equal(payload.version,document.version);
        const candidate = evaluations.candidates.find(c=>c.candidate_ref===payload.candidate_ref);
        candidate.order.forEach((id,i)=>{document.visits.find(v=>v.id===id).position=i;});
        document.settings.order_mode='manual'; document.version='v'+(Number(document.version.slice(1))+1); return {data:clone(document)};
      }
      if (url.endsWith('config')) return { data: { enabled: true, max_stops: 12 } };
      if (url.endsWith('search')) return { data: { places: [{ name: payload.keywords, address: '上海', location: `121.${payload.keywords === '甲' ? 1 : payload.keywords === '乙' ? 2 : 3},31` }] } };
      if (url === '/api/day-plan/route') {
        const mode = document.settings.leg_modes[JSON.stringify([payload.from_ref,payload.to_ref])] || 'transit';
        const result = await handler({ ...payload, mode });
        const saved = {from_ref:payload.from_ref,to_ref:payload.to_ref,departure:payload.departure,mode,result,status:'ready',source:'reference',saved_at:Date.now()/1000};
        document.routes = [...(document.routes || []).filter(l=>l.from_ref!==saved.from_ref), saved];
        return {data:clone(saved)};
      }
      return { data: await handler(payload) };
    } };
  vm.createContext(env); vm.runInContext(fs.readFileSync('public/trip-maps.js', 'utf8'), env);
  const get = selector => body.querySelector(selector);
  const buttons = root => root.querySelectorAll('button');
  const click = async (root, text) => { const node = buttons(root).find(n => n.textContent === text); assert.ok(node, text); assert.equal(node.disabled, false); await node.events.click(); await flush(); };
  const panel = i => {
    const current = get('.trip-map-segments').children[0];
    if (current?.id !== `trip-segment-${i}`) get('.trip-map-timeline').querySelectorAll('.trip-overview-leg')[i]?.events.click();
    return get('.trip-map-segments').children[0];
  };
  const routes = () => requests.filter(r => r.url.endsWith('/route'));
  async function confirm(i) {
    get('.trip-map-timeline').querySelectorAll('.trip-overview-stop')[i].events.click();
    const card = () => get('.trip-map-stops').children.find(n => n.getAttribute('data-stop-id') === String(i));
    await click(card(), '搜索地点');
    await click(card(), `${['甲', '乙', '丙'][i]} · 上海`);
  }
  return { env, get, panel, maps, routes, click, confirm, requests, document, setFailure: error=>{failure=error;}, setOrders: orders=>{candidateOrders=orders;}, get refreshes(){return refreshes;}, setHandler: fn => { handler = fn; },
    open: () => env.window.TripMaps.open('2026-09-12'), summary: () => get('.trip-map-summary').textContent };
}


test('shared node loading ignores backups and old HH:MM, while each leg defaults to transit', async () => {
  const h = harness({setup:p=>{p.visits[2].is_backup=true; p.visits[0].duration_minutes=null;}}); await h.open();
  assert.equal(h.get('.trip-map-segments').children.length,1);
  assert.equal(h.panel(0).querySelectorAll('.trip-mode-button').find(n=>n.getAttribute('aria-pressed')==='true').textContent,'公交 / 地铁');
  assert.match(h.get('.trip-map-timeline').textContent,/上午 · 停留待确认/);
  assert.doesNotMatch(h.get('.trip-map-timeline').textContent,/07:00|09:00/);
});

test('route timeline retains horizontal position and saved geometry survives closing and reopening', async () => {
  const h=harness(); await h.open(); await h.confirm(0); await h.confirm(1);
  const timeline=h.get('.trip-map-timeline'); timeline.scrollLeft=340;
  await h.click(h.panel(0),'规划本段路线');
  assert.equal(timeline.scrollLeft,340);
  const count=h.routes().length;
  h.get('dialog').close(); await h.open();
  assert.equal(h.routes().length,count);
  assert.match(h.panel(0).textContent,/约 10 分钟/);
  assert.ok(h.maps.at(-1).overlays.some(n=>n.options.path));
  assert.match(h.panel(0).textContent,/路线已保存/);
});

test('map endpoints stage coordinates, cancel without saving, then save and query cycling', async () => {
  const h = harness(); await h.open();
  const point = (lng, lat) => h.maps[0].events.click({lnglat:{getLng:()=>lng,getLat:()=>lat}});
  await h.click(h.get('.trip-map-stops'), '地图选择起点');
  point(121.12345678, 31.23456789);
  assert.equal(h.document.visits[0].poi, null);
  assert.match(h.get('.trip-map-picker').textContent, /121.123457,31.234568/);
  await h.click(h.get('.trip-map-picker'), '取消选点');
  assert.equal(h.requests.filter(r=>r.method==='PUT').length, 0);
  for (const [label, lng] of [['起点',121.1],['终点',121.2]]) {
    await h.click(h.get('.trip-map-stops'), `地图选择${label}`);
    point(lng,31);
    await h.click(h.get('.trip-map-picker'), `确认${label}位置`);
  }
  assert.equal(h.document.visits[0].poi.location, '121.100000,31.000000');
  assert.equal(h.document.visits[1].poi.location, '121.200000,31.000000');
  await h.click(h.panel(0),'骑行'); await h.click(h.panel(0),'规划本段路线');
  assert.equal(h.document.routes[0].mode,'bicycling');
  assert.equal(h.routes().at(-1).payload.from_ref,'0');
  assert.equal(h.routes().at(-1).payload.to_ref,'1');
  assert.match(h.get('.trip-map-timeline').textContent,/约 10 分钟/);
  assert.ok(h.maps[0].overlays.some(n=>n.options.path));
  await h.click(h.get('.trip-map-stops'), '地图选择终点'); point(121.3,31);
  await h.click(h.get('.trip-map-picker'), '确认终点位置');
  assert.match(h.panel(0).textContent,/待规划/);
  assert.equal(h.routes().length,1);
});

test('map picking targets anchors and is cancelled on navigation and conflicts', async () => {
  const h=harness({setup:p=>{p.settings.start_anchor={name:'酒店',location:'121,31',address:''};}}); await h.open();
  const point=()=>h.maps[0].events.click({lnglat:{getLng:()=>121.4,getLat:()=>31}});
  await h.click(h.get('.trip-map-stops'),'地图选择起点'); point();
  await h.click(h.get('.trip-map-picker'),'确认起点位置');
  assert.equal(h.document.settings.start_anchor.location,'121.400000,31.000000');
  await h.click(h.get('.trip-map-stops'),'地图选择终点'); point();
  h.panel(1); assert.equal(h.get('.trip-map-picker').hidden,true);
  await h.click(h.get('.trip-map-stops'),'地图选择终点'); point();
  h.document.version='changed'; h.env.state.revision++;
  await h.env.window.TripMaps.sync();
  assert.equal(h.get('.trip-map-picker').hidden,true);
  point(); assert.equal(h.document.visits[1].poi,null);
});
test('POI confirmation and visit edits persist through PUT and own snapshot does not close dialog', async () => {
  const h=harness(); await h.open(); await h.confirm(0);
  assert.equal(h.document.visits[0].poi.name,'甲'); assert.equal(h.get('dialog').open,true); assert.equal(h.refreshes,1);
  const fields=h.get('.trip-map-stops').children[0].querySelectorAll('input'); fields[1].value='90';
  await h.click(h.get('.trip-map-stops').children[0],'保存停留时长');
  assert.equal(h.document.visits[0].duration_minutes,90); assert.equal(fields.length,2); assert.equal(h.document.visits[0].fixed_start,'');
  assert.equal(h.requests.filter(r=>r.method==='PUT').at(-1).payload.version,'v2');
  assert.equal(h.get('dialog').open,true);
  h.get('dialog').close(); await h.open(); assert.doesNotMatch(h.get('.trip-map-timeline').textContent,/预约/);
});
test('mode changes save a directed stable pair and independently query without third POI', async () => {
  const h=harness(); await h.open(); await h.confirm(0); await h.confirm(1);
  await h.click(h.panel(0),'步行');
  assert.equal(h.document.settings.leg_modes['["0","1"]'],'walking'); assert.equal(h.routes().length,0); await h.click(h.panel(0),'规划本段路线'); assert.equal(h.routes().length,1);
  assert.equal(h.document.routes[0].mode,'walking'); assert.match(h.summary(),/1\/2.*未包含/);
  assert.match(h.get('.trip-map-evaluation').textContent,/分段路线参考/);
});
test('independent result never replaces full server feasibility, even if all routes exist', async () => {
  const h=harness(); await h.open(); for(let i=0;i<3;i++) await h.confirm(i);
  await h.click(h.get('dialog'),'核算全天');
  assert.match(h.get('.trip-map-evaluation').textContent,/时长可容纳.*约束待核实/);
  assert.match(h.get('.trip-map-evaluation').textContent,/开放时间待核实/);
  await h.click(h.panel(0),'重新规划本段');
  assert.match(h.get('.trip-map-evaluation').textContent,/尚未核算完整日程/);
  assert.match(h.panel(0).textContent,/单段参考/);
});
test('optimization is preview-only; only applying chosen server candidate changes shared order', async () => {
  const h=harness(); await h.open();
  await h.click(h.get('dialog'),'优化顺序');
  await h.click(h.get('.trip-map-candidates'),'预览方案 2');
  assert.match(h.get('.trip-map-timeline').textContent,/1\. 丙/);
  assert.equal(h.requests.some(r=>r.url.endsWith('/apply')),false); assert.deepEqual(h.document.visits.map(v=>v.position),[0,1,2]);
  await h.click(h.get('dialog'),'应用此方案');
  assert.equal(h.requests.find(r=>r.url.endsWith('/apply')).payload.candidate_ref,'c1');
  assert.deepEqual(h.document.visits.map(v=>v.position),[2,1,0]); assert.equal(h.get('dialog').open,true);
});
test('return from preview never sends apply or writes a new order', async () => {
  const h=harness(); await h.open(); await h.click(h.get('dialog'),'优化顺序'); await h.click(h.get('.trip-map-candidates'),'预览方案 2');
  await h.click(h.get('dialog'),'返回已保存顺序');
  assert.match(h.get('.trip-map-timeline').textContent,/1\. 甲/); assert.equal(h.requests.some(r=>r.method==='PUT'||r.url.endsWith('/apply')),false);
});
test('409 preserves edit fields, disables stale actions and requires explicit reload', async () => {
  const h=harness(); await h.open();
  const card=h.get('.trip-map-stops').children[0], fields=card.querySelectorAll('input'); fields[1].value='100';
  h.setFailure(Object.assign(new Error('conflict'),{status:409}));
  await h.click(card,'保存停留时长');
  assert.equal(fields[1].value,'100'); assert.equal(h.get('[data-reload]').hidden,false);
  assert.equal(h.panel(0).querySelectorAll('.trip-mode-button')[0].disabled,true);
  h.setFailure(null); await h.click(h.get('dialog'),'载入最新日计划');
  assert.equal(h.get('.trip-map-stops').children[0].querySelectorAll('input')[1].value,60);
});
test('settings-only remote revision invalidates evaluation even with unchanged itinerary payload', async () => {
  const h=harness(); await h.open(); await h.click(h.get('dialog'),'核算全天');
  h.document.version='v2'; h.document.settings.buffer_minutes=30; h.env.state.revision++;
  await h.env.window.TripMaps.sync();
  assert.match(h.get('.trip-map-status').textContent,/已被修改/); assert.equal(h.get('[data-reload]').hidden,false);
  assert.equal(h.maps[0].overlays.filter(x=>x.options.path).length,0);
});
test('legacy activities block precise route queries instead of guessing location or 60 minutes', async () => {
  const h=harness({setup:p=>{p.visits[0].visit_kind='legacy'; p.visits[0].duration_minutes=null;}}); await h.open();
  assert.match(h.get('.trip-map-stops').children[0].textContent,/拆分/);
  await h.click(h.panel(0),'规划本段路线');
  assert.equal(h.routes().length,0); assert.match(h.get('.trip-map-status').textContent,/拆分/);
});
test('overview and polyline navigation still focus the referenced panel with server results', async () => {
  const h=harness(); await h.open(); await h.click(h.get('dialog'),'核算全天');
  h.get('.trip-map-timeline').querySelectorAll('.trip-overview-leg')[1].events.click(); assert.equal(h.panel(1).focused,true);
  assert.equal(h.maps[0].overlays.filter(x=>x.options.path).length,1); h.maps[0].overlays.filter(x=>x.options.path)[0].events.click(); assert.equal(h.panel(1).focused,true);
});
test('late independent results are discarded after closing the dialog', async () => {
  const h=harness(); await h.open(); await h.confirm(0); await h.confirm(1);
  let resolve; h.setHandler(()=>new Promise(yes=>{resolve=yes;}));
  const pending=h.click(h.panel(0),'规划本段路线'); await flush(); h.get('dialog').close();
  resolve({duration:100,distance:0,parts:[],instructions:[]}); await pending;
  assert.equal(h.get('.trip-map-segments').children.length,0); assert.equal(h.maps[0].destroyed,true);
});

test('unlocated rest remains visible while traffic links and mode saves use real endpoints', async () => {
  const h=harness({setup:p=>{p.visits[1].visit_kind='rest';}}); await h.open();
  assert.equal(h.get('.trip-map-segments').children.length,1);
  assert.match(h.panel(0).textContent,/甲 → 丙/);
  assert.match(h.get('.trip-map-timeline').textContent,/乙/);
  await h.click(h.panel(0),'步行');
  assert.equal(h.document.settings.leg_modes['["0","2"]'],'walking');
});
test('unrelated saves preserve unsaved stay drafts', async () => {
  const h=harness(); await h.open();
  const inputs=h.get('.trip-map-stops').children[1].querySelectorAll('input');
  inputs[1].value='75'; inputs[1].events.input();
  await h.click(h.panel(0),'步行');
  const retained=h.get('.trip-map-stops').children[1].querySelectorAll('input');
  assert.equal(retained[1].value,'75');
  assert.equal(h.document.visits[1].duration_minutes,60);
  await h.click(h.get('.trip-map-stops').children[1],'保存停留时长');
  assert.equal(h.document.visits[1].duration_minutes,75);
});

test('saving unchanged stay preserves AI provenance until duration changes', async () => {
  const h=harness({setup:p=>{p.visits[0].duration_source='ai_estimate';}}); await h.open();
  let card=h.get('.trip-map-stops').children[0];
  await h.click(card,'保存停留时长');
  assert.equal(h.document.visits[0].duration_source,'ai_estimate');
  card=h.get('.trip-map-stops').children[0];
  card.querySelectorAll('input')[1].value='85';
  await h.click(card,'保存停留时长');
  assert.equal(h.document.visits[0].duration_minutes,85);
  assert.equal(h.document.visits[0].duration_source,'user');
});

test('only selected endpoints and one route editor render; switching retains planned duration', async () => {
  const h=harness(); await h.open(); for(let i=0;i<3;i++) await h.confirm(i);
  await h.click(h.panel(0),'规划本段路线');
  assert.equal(h.get('.trip-map-segments').children.length,1);
  assert.deepEqual(h.get('.trip-map-stops').children.map(n=>n.getAttribute('data-stop-id')),['0','1']);
  assert.match(h.get('.trip-map-timeline').textContent,/约 10 分钟/);
  h.panel(1);
  assert.equal(h.get('.trip-map-segments').children.length,1);
  assert.deepEqual(h.get('.trip-map-stops').children.map(n=>n.getAttribute('data-stop-id')),['1','2']);
  assert.equal(h.routes().length,1);
  assert.match(h.get('.trip-map-timeline').textContent,/约 10 分钟/);
});

test('upstream planning invalidates downstream transit without automatically querying it', async () => {
  const h=harness(); await h.open(); for(let i=0;i<3;i++) await h.confirm(i);
  await h.click(h.panel(1),'规划本段路线');
  await h.click(h.panel(0),'规划本段路线');
  assert.equal(h.routes().length,2);
  assert.match(h.get('.trip-map-timeline').textContent,/已过期/);
});
