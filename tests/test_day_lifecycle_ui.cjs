const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness({date='2030-01-01', confirm=true, request}={}) {
  const all = n => [n, ...n.children.flatMap(all)];
  class Node {
    constructor(tag) { Object.assign(this,{tag,children:[],dataset:{},events:{},attrs:{}}); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children=nodes; }
    setAttribute(k,v) { this.attrs[k]=v; }
    addEventListener(k,v) { this.events[k]=v; }
    querySelectorAll() { return all(this).filter(n=>'dayAction' in n.dataset); }
  }
  const cards=new Node('div'), requests=[], notices=[], confirmations=[];
  let refreshes=0;
  const plan={city_id:'shanghai',date,version:'visible-version',settings:{}};
  const env={window:{confirm:text=>{confirmations.push(text);return confirm;}},Date,
    state:{projectId:'main',cityId:'shanghai',userId:'tester',projectUnlocked:true,cities:[{id:'shanghai',name:'上海'}],
      items:{itinerary:[]},dailyPlans:[plan]},
    dom:{cards},document:{createElement:tag=>new Node(tag)},formatItineraryDate:d=>d,
    requireIdentity:()=>true,showToast:text=>notices.push(text),
    fetchSnapshot:async()=>{refreshes++;},
    requestJson:async(url,opts)=>{requests.push({url,method:opts.method,body:JSON.parse(opts.body)});return request?request():{data:{}};},
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../public/daily-plans.js'),'utf8'),env);
  const render=()=>env.window.TripDaily.render(env.state.items.itinerary);
  const nodes=()=>all(cards), input=()=>nodes().find(n=>n.type==='date');
  render();
  return {env,requests,notices,confirmations,render,input,nodes,refreshes:()=>refreshes,
    submit:()=>nodes().find(n=>n.tag==='form').events.submit({preventDefault(){}}),
    remove:()=>nodes().find(n=>n.textContent==='删除这一天').events.click()};
}

test('new day defaults to the next date across leap days and year boundaries',()=>{
  for(const [date,next] of [['2028-02-28','2028-02-29'],['2030-12-31','2031-01-01']]) {
    assert.equal(harness({date}).input().value,next);
  }
});
test('date draft survives synchronization and create saves the selected empty day',async()=>{
  const h=harness();h.input().value='2030-02-12';h.input().events.input();h.render();
  assert.equal(h.input().value,'2030-02-12');
  await h.submit();
  assert.deepEqual(h.requests,[{url:'/api/day-plan',method:'POST',body:{city_id:'shanghai',date:'2030-02-12'}}]);
  assert.equal(h.refreshes(),1);
});
test('initial snapshot updates the suggested date until the user edits it',()=>{
  const h=harness();h.env.state.dailyPlans=[];h.render();
  h.env.state.dailyPlans=[{city_id:'shanghai',date:'2031-05-01',version:'v',settings:{}}];h.render();
  assert.equal(h.input().value,'2031-05-02');
});

test('creating a day selects it when the refreshed snapshot arrives', async()=>{
  const h=harness();h.input().value='2030-02-12';h.input().events.input();
  h.env.fetchSnapshot=async()=>{h.env.state.dailyPlans.push({city_id:'shanghai',date:'2030-02-12',settings:{}});h.render();};
  await h.submit();
  assert.equal(h.env.window.TripDaily.selectedDate(),'2030-02-12');
  assert.equal(h.nodes().find(n=>n.className==='daily-day-select').value,'2030-02-12');
});
test('canceling deletion makes no request',async()=>{
  const h=harness({confirm:false});await h.remove();
  assert.equal(h.requests.length,0);assert.equal(h.refreshes(),0);
});
test('delete confirms scope and uses the visible version',async()=>{
  const h=harness();await h.remove();
  assert.match(h.confirmations[0],/上海 · 2030-01-01/);
  assert.match(h.confirmations[0],/0 项安排/);
  assert.match(h.confirmations[0],/游玩点、美食清单及其他日期保留/);
  assert.deepEqual(h.requests[0],{url:'/api/day-plan',method:'DELETE',body:{city_id:'shanghai',date:'2030-01-01',version:'visible-version'}});
  assert.equal(h.refreshes(),1);
});
test('conflict refreshes the view but never retries deletion automatically',async()=>{
  const h=harness({request:async()=>{throw Object.assign(new Error('当天安排已变化'),{status:409});}});
  await h.remove();assert.equal(h.requests.length,1);assert.equal(h.refreshes(),1);
  assert.match(h.notices[0],/已变化/);
  assert.equal(h.nodes().find(n=>n.textContent==='删除这一天').disabled,false);
});
test('in-flight request blocks duplicate submits and does not refresh another project',async()=>{
  let finish;const pending=new Promise(resolve=>finish=resolve);
  const h=harness({request:()=>pending});const first=h.submit();await h.submit();
  assert.equal(h.requests.length,1);h.env.state.projectId='another-project';finish({data:{}});await first;
  assert.equal(h.refreshes(),0);assert.deepEqual(h.notices,[]);
});
