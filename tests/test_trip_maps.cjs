const test = require('node:test');
const assert = require('node:assert/strict');
const plan = require('../public/trip-maps.js');
const fixture = () => ({ date: '2026-09-12', version: 'v1', settings: {start_time: '09:00', buffer_minutes: 10, buffer_ratio: .2, leg_modes: {}}, blocks: [],
  visits: [ {id: 'a', position: 1, title: '甲', start_time: '08:00', fixed_start: '', duration_minutes: null, time_block: 'morning'},
    {id: 'b', position: 0, title: '乙', duration_minutes: 60, fixed_start: '11:00'}, {id: 'backup', position: 2, title: '备选', is_backup: true} ] });

test('nodes use stable visit IDs and shared positions, ignore backup and old start_time', () => {
  const stops = plan.nodes(fixture()); assert.deepEqual(stops.map(s => s.id), ['b', 'a']);
  assert.equal(stops[1].stay, null); assert.equal(stops[1].time, ''); assert.equal(stops[0].time, '');
});
test('legacy multi-place activity stays one blocked node without invented duration', () => {
  const p = fixture(); p.visits = [{id: 'legacy', position: 0, visit_kind: 'legacy', attraction_names: ['甲','乙'], start_time:'09:00'}];
  const stops = plan.nodes(p); assert.equal(stops.length, 1); assert.equal(stops[0].stay, null);
  assert.equal(plan.referenceDeparture(stops, [], 0, p), null);
});
test('anchors get stable reserved references and missing anchors are not fabricated', () => {
  const p = fixture(); p.settings.start_anchor = {name:'酒店',location:'121,31'}; p.settings.end_anchor = {name:'车站',location:'121.1,31.1'};
  assert.deepEqual(plan.nodes(p).map(s => s.id), ['@start','b','a','@end']);
  assert.equal(plan.nodes(p)[0].stay, 0); assert.equal(plan.nodes(fixture()).length, 2);
});
test('directed edge selections survive reorder while reversed/new edges default to transit', () => {
  const stops = ['a','b','c'].map(id => ({id})); const selections = {[plan.pair('a','b')]:'walking', [plan.pair('b','c')]:'driving'};
  assert.deepEqual(plan.segments(stops, selections).map(l => l.mode), ['walking','driving']);
  assert.deepEqual(plan.segments([stops[0],stops[1],{id:'d'},stops[2]], selections).map(l => l.mode), ['walking','transit','transit']);
  assert.equal(plan.segments([stops[1],stops[0]], selections)[0].mode, 'transit');
});
test('candidate reference validation rejects duplicated, missing and backup nodes', () => {
  for (const order of [['a'],['a','a'],['a','backup']]) assert.throws(() => plan.nodes(fixture(), order));
  assert.deepEqual(plan.nodes(fixture(), ['a','b']).map(s => s.id), ['a','b']);
});
test('unknown stays allow provisional route lookup using the block start', () => {
  const p = fixture(); const stops = plan.nodes(p, ['a','b']);
  assert.deepEqual(plan.referenceDeparture(stops, plan.segments(stops), 0, p), {minutes:540, provisional:true, context:'missing-prefix'});
  assert.deepEqual(plan.referenceDeparture(stops, plan.segments(stops), 1, p), {minutes:600, provisional:true, context:'missing-prefix'});
});
test('buffer uses maximum of fixed and proportion, not their sum', () => {
  assert.equal(plan.bufferFor(600, {buffer_minutes:10,buffer_ratio:.2}),10);
  assert.equal(plan.bufferFor(3601, {buffer_minutes:10,buffer_ratio:.2}),13);
  assert.equal(plan.clock(1450),'次日 00:10');
});

test('stationary rest contributes time without creating fictitious traffic nodes', () => {
  const p = fixture(); p.visits = ['a','rest','b','c'].map((id,position)=>({id,position,title:id,duration_minutes: id==='rest'?30:60,visit_kind:id==='rest'?'rest':'attraction'}));
  const stops = plan.nodes(p), legs = plan.segments(stops,{[plan.pair('a','b')]:'walking'});
  assert.deepEqual(legs.map(l=>[l.from_ref,l.to_ref,l.mode]),[['a','b','walking'],['b','c','transit']]);
  legs[0].result={duration:600};
  assert.equal(plan.referenceDeparture(stops,legs,2,p).minutes,710);
  p.visits[1].poi={name:'休息地点',location:'121,31'};
  assert.equal(plan.segments(plan.nodes(p)).length,3);
});
