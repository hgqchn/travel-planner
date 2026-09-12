import copy
import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
import daily_planner as p
import daily_plan_store as store
import daily_ai

POI={'id':'poi','name':'入口','address':'地址','location':'121.1,31.1'}


class PlannerTests(unittest.TestCase):
    def settings(self):
        return {**p.defaults(),'start_anchor':POI,'end_anchor':POI}

    def visit(self, **changes):
        return {'id':'visit1','time_block':'morning','duration_minutes':75,'poi':POI,**changes}

    def route(self, payload):
        return {'duration':1200,'distance':500,'parts':[],'instructions':[],'incomplete':True}

    def test_cycling_preference_reaches_daily_evaluation(self):
        settings = p.normalize_settings({**self.settings(), 'leg_modes':{p.edge_key('@start','visit1'):'bicycling'}})
        requests = []
        def route(payload):
            requests.append(payload)
            return self.route(payload)
        p.evaluate([self.visit()], settings, '2026-09-12', route)
        self.assertEqual(requests[0]['mode'], 'bicycling')

    def test_budget_counts_first_last_travel_and_rest_once(self):
        ev=p.evaluate([self.visit()],self.settings(),'2026-09-12',self.route)
        self.assertEqual(ev['travel_minutes'],40)
        self.assertEqual(ev['buffer_minutes'],20)
        self.assertEqual(ev['meal_minutes'],120)
        self.assertEqual(ev['rest_minutes'],30)
        self.assertEqual(ev['slack_minutes'],660-40-20-75-120-30)
        self.assertEqual(ev['route_status'],'checked')
        self.assertEqual(ev['constraint_status'],'needs_verification')

    def test_unknown_never_zero_or_complete(self):
        ev=p.evaluate([self.visit(poi=None)],self.settings(),'2026-09-12',self.route)
        self.assertIsNone(ev['slack_minutes'])
        self.assertIsNone(ev['rows'][0]['begin'])
        self.assertEqual(ev['time_fit'],'unknown')

    def test_legacy_start_is_not_appointment(self):
        v=p.normalize_visit({'start_time':'09:00'})
        self.assertEqual(v['fixed_start'],'')
        self.assertEqual(v['time_block'],'morning')
        self.assertIsNone(v['duration_minutes'])

    def test_deprecated_appointments_do_not_constrain_plans(self):
        ev=p.evaluate([self.visit(fixed_start='09:10')],self.settings(),'2026-09-12',self.route)
        self.assertNotEqual(ev['constraint_status'],'conflict')
        self.assertEqual(ev['rows'][0]['late'],0)
        v=p.normalize_visit({'fixed_start':'09:10','entry_start':'10:00','entry_end':'10:15'})
        self.assertTrue(all(v[k]=='' for k in ('fixed_start','entry_start','entry_end')))

    def test_ai_hours_are_unverified_and_do_not_create_hard_conflicts(self):
        visit=self.visit(opening_start='18:00',opening_end='19:00',opening_source='ai_estimate',duration_minutes=120)
        result=p.evaluate([visit],self.settings(),'2026-09-12',self.route)
        self.assertEqual(result['constraint_status'],'needs_verification')
        self.assertTrue(any(i['code']=='opening_unverified' for i in result['issues']))
        self.assertEqual(result['rows'][0]['begin'],570)
        self.assertEqual(p.normalize_visit({**visit,'duration_source':'user'})['duration_source'],'user')

    def test_closed_night_and_opening_window(self):
        ev=p.evaluate([self.visit(time_block='night',opening_start='09:00',opening_end='10:00')],self.settings(),'2026-09-12',self.route)
        self.assertEqual(ev['constraint_status'],'conflict')

    def test_night_is_available_with_legacy_settings_without_changing_saved_context(self):
        settings=self.settings()
        original=copy.deepcopy(settings)
        ev=p.evaluate([self.visit(time_block='night',duration_minutes=30)],settings,'2026-09-12',self.route)
        self.assertEqual(settings,original)
        self.assertGreaterEqual(ev['rows'][0]['begin'],1200)
        self.assertFalse(any('未开启夜游' in issue['message'] for issue in ev['issues']))
        self.assertNotEqual(ev['time_fit'],'overflow')

    def test_long_activity_is_not_split_or_overlap_rest(self):
        ev=p.evaluate([self.visit(duration_minutes=180)],self.settings(),'2026-09-12',self.route)
        self.assertEqual(ev['visit_minutes'],180)
        self.assertEqual(len(ev['rows']),2)
        self.assertEqual(ev['rows'][0]['begin'],810)

    def test_after_lunch_transit_queried_again(self):
        seen=[]
        settings={**self.settings(),'start_time':'11:50'}
        def route(payload):
            seen.append(payload['time'])
            return self.route(payload)
        p.evaluate([self.visit(time_block='afternoon')],settings,'2026-09-12',route)
        self.assertIn('13:30',seen)

    def test_direction_and_rounding(self):
        self.assertNotEqual(p.edge_key('a','b'),p.edge_key('b','a'))
        self.assertEqual(p.block_for('13:00'),'lunch_rest')
        self.assertEqual(p.block_for('22:00'),'')
        with self.assertRaises(ValueError): p.normalize_visit({'duration_minutes':True})

    def test_recommended_place_duration_uses_explicit_units_and_range_upper_bound(self):
        for text, expected in [('约 2–3 小时',180),('1.5小时',90),('30-60分钟',60),('1小时30分钟',90),('建议90分钟左右',90)]:
            self.assertEqual(p.recommended_minutes(text),expected)
        for text in ['半天','全天','依体力而定','交通30分钟，游览2小时','0分钟','3000分钟','90']:
            self.assertIsNone(p.recommended_minutes(text))

    def test_optimization_keeps_slots_together_and_reorders_within_a_slot(self):
        visits=[self.visit(id='far',poi={**POI,'location':'121.9,31.1'}),
                self.visit(id='afternoon',time_block='afternoon'), self.visit(id='near')]
        orders=p.candidate_orders(visits,self.settings(),True)
        self.assertEqual([v['id'] for v in orders[0]],['far','near','afternoon'])
        self.assertEqual([v['id'] for v in orders[1]],['near','far','afternoon'])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'trip.db'
        seed=Path(self.tmp.name)/'empty.json';seed.write_text('{}')
        server.init_database(self.path,'test-project',seed)
        self.user='测试者';server.claim_identity(self.path,{'user_id':self.user})
        self.city='shanghai';self.day='2026-09-12'

    def tearDown(self): self.tmp.cleanup()
    def read(self): return store.get(server,self.path,self.city,self.day)
    def save(self,plan,**change):
        return store.save(server,self.path,{'city_id':self.city,'date':self.day,'version':plan['version'],**change},self.user)
    def add(self,**change):
        return self.save(self.read(),additions=[{'title':'公园','duration_minutes':60,**change}])

    def test_saved_routes_survive_reopen_and_only_relevant_edits_invalidate(self):
        from types import SimpleNamespace
        plan=self.save(self.read(),additions=[{'title':str(i),'duration_minutes':60,'poi':{**POI,'location':f'121.{i+1},31.1'}} for i in range(3)])
        a,b,c=[v['id'] for v in plan['visits']]
        provider=SimpleNamespace(route=lambda payload,city:dict(duration=600,distance=1000,parts=[[[121.1,31.1],[121.2,31.1]]],instructions=['步行至入口']))
        def query(a,b):
            current=self.read()
            return store.save_route(server,self.path,dict(city_id=self.city,date=self.day,version=current['version'],from_ref=a,to_ref=b,
                departure=dict(minutes=600,provisional=True,context='estimated-prefix')),self.user,provider)
        query(a,b)
        self.assertEqual(len(self.read()['routes']),1)
        with server.connect_db(self.path) as db: store.ensure_schema(db)
        self.assertEqual(self.read()['routes'][0]['result']['duration'],600)
        self.save(self.read(),updates=[{'id':a,'changes':{'notes':'保留路线'}}])
        self.assertEqual(len(self.read()['routes']),1)
        self.save(self.read(),updates=[{'id':c,'changes':{'poi':{**POI,'location':'122,31'}}}])
        self.assertEqual(len(self.read()['routes']),1)
        query(b,c); self.assertEqual(len(self.read()['routes']),2)
        query(a,b); self.assertEqual(len(self.read()['routes']),2)
        provider.route=lambda payload,city:dict(duration=1200,distance=1000,parts=[],instructions=[])
        query(a,b); self.assertEqual(len(self.read()['routes']),1)
        self.save(self.read(),updates=[{'id':b,'changes':{'poi':{**POI,'location':'123,31'}}}])
        self.assertEqual(self.read()['routes'],[])

    def test_route_conflict_during_provider_call_cannot_save_old_geometry(self):
        from types import SimpleNamespace
        plan=self.save(self.read(),additions=[{'title':str(i),'duration_minutes':60,'poi':POI} for i in range(2)])
        a,b=[v['id'] for v in plan['visits']]
        def changed(payload,city):
            self.save(self.read(),updates=[{'id':a,'changes':{'duration_minutes':90}}])
            return dict(duration=600,parts=[])
        with self.assertRaises(server.ApiError) as error:
            store.save_route(server,self.path,dict(city_id=self.city,date=self.day,version=plan['version'],from_ref=a,to_ref=b,
                departure=dict(minutes=600)),self.user,SimpleNamespace(route=changed))
        self.assertEqual(error.exception.status,409)
        self.assertEqual(self.read()['routes'],[])

    def test_evaluated_and_applied_route_geometry_survives_summary_expiry(self):
        from types import SimpleNamespace
        plan=self.save(self.read(),additions=[{'title':str(i),'duration_minutes':30,'time_block':'morning','poi':POI} for i in range(2)])
        provider=SimpleNamespace(route=lambda payload,city:dict(duration=600,distance=100,parts=[[[121.1,31.1],[121.2,31.2]]],instructions=[]))
        data=store.evaluate(server,self.path,{k:plan[k] for k in ('city_id','date','version')},self.user,provider)
        self.assertEqual(len(self.read()['routes']),1)
        applied=store.apply(server,self.path,{**{k:plan[k] for k in ('city_id','date','version')},'candidate_ref':data['candidates'][0]['candidate_ref']},self.user)
        self.assertEqual(len(applied['routes']),1)
        with server.connect_db(self.path) as db:
            db.execute('UPDATE day_evaluations SET evaluated_at=evaluated_at-1000')
        restored=self.read()
        self.assertEqual(restored['evaluation']['route_status'],'stale')
        self.assertEqual(len(restored['routes']),1)

    def test_order_conflicts_on_insert_and_settings(self):
        initial=self.read();now=self.add()
        with self.assertRaises(server.ApiError) as error:self.save(initial,order=[])
        self.assertEqual(error.exception.status,409)
        modified=self.save(now,settings={'night_enabled':True,'end_time':'22:00'})
        self.assertNotEqual(now['version'],modified['version'])
        with self.assertRaises(server.ApiError): self.save(now,order=[v['id'] for v in now['visits']])

    def test_split_retains_notes_and_single_stable_reference(self):
        plan=self.add(attraction_names=['人民公园','世纪公园'],notes='保留备注',visit_kind='legacy')
        ref=plan['visits'][0]['id']
        split=self.save(plan,split=ref)
        self.assertEqual(split['visits'][0]['id'],ref)
        self.assertEqual(len(split['visits']),2)
        self.assertTrue(all(v['duration_minutes'] is None and v['notes']=='保留备注' for v in split['visits']))

    def test_mode_bound_to_directed_pair(self):
        plan=self.save(self.read(),additions=[{'title':'A'},{'title':'B'}])
        ids=[v['id'] for v in plan['visits']]
        plan=self.save(plan,settings={'leg_modes':{p.edge_key(*ids):'walking'}})
        self.assertEqual(plan['settings']['leg_modes'][p.edge_key(*ids)],'walking')
        plan=self.save(plan,order=list(reversed(ids)))
        self.assertEqual(plan['settings']['leg_modes'],{})

    def test_place_selection_inherits_recommendation_but_user_duration_wins(self):
        place=server.create_item(self.path,'attraction',{'city_id':self.city,'name':'时长测试园','duration':'1.5–2小时'},self.user)
        raw={'source_place_id':place['item']['id'],'title':'时长测试园','attraction_names':['时长测试园']}
        plan=self.save(self.read(),additions=[raw])
        self.assertEqual(plan['visits'][0]['duration_minutes'],120)
        self.assertEqual(plan['visits'][0]['duration_source'],'supplied_data')
        plan=self.save(plan,additions=[{**raw,'duration_minutes':75,'duration_source':'user'}])
        self.assertEqual(plan['visits'][1]['duration_minutes'],75)
        self.assertEqual(plan['visits'][1]['duration_source'],'user')

    def test_shared_order_groups_slots_without_adding_empty_slot_items(self):
        plan=self.save(self.read(),additions=[{'title':'A','time_block':'morning'},
            {'title':'B','time_block':'afternoon'}, {'title':'C','time_block':'morning'}])
        self.assertEqual([v['title'] for v in plan['visits']],['A','C','B'])
        moved=self.save(plan,updates=[{'id':plan['visits'][0]['id'],'changes':{'time_block':'evening'}}])
        self.assertEqual([v['title'] for v in moved['visits']],['C','B','A'])
        self.assertEqual(len(self.read()['visits']),3)

    def test_receipt_is_atomic_and_does_not_duplicate(self):
        plan=self.read();data={'city_id':self.city,'date':self.day,'version':plan['version'],'additions':[{'title':'A'}]}
        one=store.save(server,self.path,data,self.user,receipt='test')
        two=store.save(server,self.path,data,self.user,receipt='test')
        self.assertEqual(one,two)
        self.assertEqual(len(self.read()['visits']),1)

    def test_evaluation_detects_edit_during_route(self):
        plan=self.add(poi=POI)
        plan=self.save(plan,settings={'start_anchor':POI,'end_anchor':POI})
        outer=self
        class Map:
            changed=False
            def route(self,payload,city):
                if not self.changed:
                    self.changed=True;outer.save(outer.read(),settings={'slack_target':90})
                return {'duration':60}
        with self.assertRaises(server.ApiError) as error:
            store.evaluate(server,self.path,{**{k:plan[k] for k in ('city_id','date','version')},'optimize':False},self.user,Map())
        self.assertEqual(error.exception.status,409)

    def test_migration_is_idempotent(self):
        plan=self.add(start_time='16:00')
        with server.connect_db(self.path) as db:store.ensure_schema(db)
        self.assertEqual(self.read()['version'],plan['version'])

    def test_ai_cannot_drop_must_or_forge_ref(self):
        plan=self.add(priority='must')
        context=daily_ai.prepare(server,self.path,{'city_id':self.city,'start_date':self.day,'day_version':plan['version'],'purpose':'daily_select'},self.user)
        value={'schema_version':2,'stage':'select_places','selected_places':[],'new_place_suggestions':[],'questions':[]}
        with self.assertRaises(ValueError): daily_ai.validate(value,'daily_select',context)


if __name__=='__main__':unittest.main()
