const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture() {
  let network=0;
  const storage={getItem:()=> 'real project data',setItem:()=>{throw Error('actual storage must not be written');}};
  const window={localStorage:storage,sessionStorage:storage,fetch:()=>{network++;throw Error('real network');}};
  const env={window,document:{documentElement:{dataset:{usageDemo:'true'}},baseURI:'https://trip.test/'},Response,URL,Map,setTimeout};
  vm.runInNewContext(fs.readFileSync('public/usage-tour-demo.js','utf8'),env);
  return {window,network:()=>network,storage,async api(path,body,method=body?'POST':'GET'){
    const response=await window.fetch(path,{method,...(body?{body:JSON.stringify(body)}:{})});return {status:response.status,data:await response.json()};
  }};
}
test('demo generation is preset and does not create dates until import',async()=>{
  const f=fixture();assert.equal((await f.api('/api/snapshot')).data.daily_plans.length,0);
  const {data:{job}}=await f.api('/api/ai/jobs',{start_date:'2026-10-01',days:2});
  assert.equal(job.status,'ready');assert.equal(job.result.itineraries.length,3);
  assert.equal((await f.api('/api/snapshot')).data.items.itinerary.length,0);
  await f.api(`/api/ai/jobs/${job.id}/import`,{items:job.result.itineraries.map(data=>({kind:'itinerary',data}))});
  const saved=(await f.api('/api/snapshot')).data;
  assert.equal(saved.daily_plans.length,2);assert.equal(saved.items.itinerary[0].position,1);
  assert.equal(saved.project_itinerary.cities[0].count,3);assert.equal(f.network(),0);
});
test('demo point confirmation and route result are separate preset operations',async()=>{
  const f=fixture();const {data:{job}}=await f.api('/api/ai/jobs',{});
  await f.api('/api/ai/jobs/example/import',{items:job.result.itineraries.map(data=>({kind:'itinerary',data}))});
  const visits=(await f.api('/api/day-plan')).data.visits;
  const poi=(await f.api('/api/maps/search',{keywords:'豫园'})).data.places[0];
  assert.equal((await f.api('/api/day-plan')).data.visits[0].poi,undefined);
  await f.api('/api/day-plan',{updates:[{id:visits[0].id,changes:{poi}}]},'PUT');
  assert.equal((await f.api('/api/day-plan')).data.visits[0].poi.name,'豫园');
  assert.equal((await f.api('/api/day-plan')).data.routes.length,0);
  await f.api('/api/day-plan/route',{from_ref:visits[0].id,to_ref:visits[1].id});
  assert.equal((await f.api('/api/day-plan')).data.routes[0].result.duration,1500);
  assert.equal((await f.api('/api/snapshot')).data.daily_plans[0].route_preview.planned,1);
  assert.equal(f.network(),0);
});
test('unknown operations fail closed and never forward even to an external origin',async()=>{
  const f=fixture();assert.equal((await f.api('/api/delete-project',{id:'real'})).status,400);
  assert.equal((await f.api('https://example.com/unhandled')).status,400);assert.equal(f.network(),0);
});
test('demo storage is isolated and starts fresh for every frame',()=>{
  const f=fixture();f.window.localStorage.setItem('draft','demo data');
  assert.equal(f.window.localStorage.getItem('draft'),'demo data');assert.equal(f.storage.getItem('draft'),'real project data');
  assert.equal(fixture().window.localStorage.getItem('draft'),null);
});
