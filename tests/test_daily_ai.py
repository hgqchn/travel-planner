import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from functools import partial
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
import daily_ai
import daily_plan_store as store
import ai_service


class LocalAI(ai_service.AIService):
    output=None
    def _post(self,payload,**kwargs):
        self.last_payload=payload
        return {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(self.output,ensure_ascii=False)}]}]}


class DailyAITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'trip.db'
        seed=Path(self.tmp.name)/'seed.json';seed.write_text('{}')
        server.init_database(self.path,'test-code',seed)
        self.user='AI测试';self.device='local-device'
        server.claim_identity(self.path,{'user_id':self.user})
        self.day='2026-09-13';self.city='shanghai'
        self.service=LocalAI(self.path,api_key='test-only',model='test-model',get_context=partial(server.ai_context,self.path),normalize_item=server.validate_payload,import_items=partial(server.import_ai_items,self.path))

    def tearDown(self): self.service.close();self.tmp.cleanup()
    def plan(self):return store.get(server,self.path,self.city,self.day)
    def job(self,purpose,output,**extras):
        plan=self.plan()
        self.service.output=output
        data={'purpose':purpose,'city_id':self.city,'start_date':self.day,'day_version':plan['version'],**extras}
        data['_daily_context']=daily_ai.prepare(server,self.path,data,self.user)
        job=self.service.create_job(self.user,self.device,data)
        until=time.monotonic()+3
        while time.monotonic()<until:
            job=self.service.get_job(self.user,self.device,job['id'])
            if job['status'] not in {'queued','running'}:return job
            time.sleep(.01)
        self.fail('Mock AI timed out')
    def selection(self):
        return {'schema_version':2,'stage':'select_places','selected_places':[],
                'new_place_suggestions':[{'name':'人民公园','city':'上海','kind':'attraction','reason':'轻松游览'}],'questions':[]}
    def apply(self,job):
        plan=self.plan()
        return daily_ai.apply_job(server,self.service,self.path,{'job_id':job['id'],'city_id':self.city,'date':self.day,'version':job['request']['day_version']},self.user,self.device)

    def test_real_job_pipeline_select_apply_and_duplicate_receipt(self):
        job=self.job('daily_select',self.selection())
        self.assertEqual(job['status'],'ready',job['error'])
        self.assertEqual(self.service.last_payload['text']['format']['name'],'daily_select_v2')
        self.assertNotIn(self.user,self.service.last_payload['input'])
        before=self.plan()
        self.assertFalse(before['visits'])
        first=self.apply(job);second=self.apply(job)
        self.assertEqual(first,second)
        self.assertEqual(len(first['visits']),1)
        self.assertIsNone(first['visits'][0]['poi'])
        self.assertEqual(first['visits'][0]['fixed_start'],'')

    def test_adjust_ref_is_checked_and_scope_conflicts_preserve_user_changes(self):
        self.apply(self.job('daily_select',self.selection()))
        output={'schema_version':2,'stage':'adjust_plan','operations':[{'op':'set_duration','place_ref':'v1','value':'80','order':[]}],'explanation':'延长停留'}
        job=self.job('daily_adjust',output)
        self.assertEqual(job['status'],'ready',job['error'])
        plan=self.plan()
        store.save(server,self.path,{'city_id':self.city,'date':self.day,'version':plan['version'],'settings':{'slack_target':90}},self.user)
        with self.assertRaises(server.ApiError) as err:self.apply(job)
        self.assertEqual(err.exception.status,409)

    def test_duration_recommendations_fill_only_missing_values_and_preserve_order(self):
        plan=self.plan()
        plan=store.save(server,self.path,{'city_id':self.city,'date':self.day,'version':plan['version'],
            'additions':[{'title':'缺少时长','time_block':'morning'},{'title':'人工时长','duration_minutes':45,'duration_source':'user','time_block':'afternoon'}]},self.user)
        output={'schema_version':2,'stage':'recommend_durations','durations':[{'place_ref':'v1','duration_minutes':95,'reason':'按地点规模建议'}]}
        job=self.job('daily_duration',output)
        self.assertEqual(job['status'],'ready',job['error'])
        result=self.apply(job)
        self.assertEqual([v['id'] for v in result['visits']],[v['id'] for v in plan['visits']])
        self.assertEqual([v['duration_minutes'] for v in result['visits']],[95,45])
        self.assertEqual(result['visits'][0]['duration_source'],'ai_estimate')
        self.assertEqual(result['visits'][1]['duration_source'],'user')

    def test_duration_recommendation_cannot_overwrite_an_existing_value(self):
        plan=self.plan()
        store.save(server,self.path,{'city_id':self.city,'date':self.day,'version':plan['version'],
            'additions':[{'title':'缺少时长'},{'title':'人工时长','duration_minutes':45}]},self.user)
        output={'schema_version':2,'stage':'recommend_durations','durations':[{'place_ref':'v2','duration_minutes':90,'reason':'越界修改'}]}
        self.assertEqual(self.job('daily_duration',output)['status'],'failed')
        self.assertEqual(self.plan()['visits'][1]['duration_minutes'],45)

    def test_choose_unknown_candidate_rejected_and_null_accepted(self):
        plan=self.plan()
        class Map:
            def route(self,*args):raise RuntimeError('Should not be called with missing endpoints')
        evaluated=store.evaluate(server,self.path,{**{k:plan[k] for k in ('city_id','date','version')}},self.user,Map())
        refs=[c['candidate_ref'] for c in evaluated['candidates']]
        output={'schema_version':2,'stage':'choose_plan','recommended_candidate_ref':'c1','reasons':[],'adjustment_refs':[],'blocking_issue_refs':[]}
        bad=self.job('daily_choose',output,candidate_refs=refs)
        self.assertEqual(bad['status'],'failed')
        output['recommended_candidate_ref']=None
        good=self.job('daily_choose',output,candidate_refs=refs)
        self.assertEqual(good['status'],'ready',good['error'])

    def test_v2_city_draft_has_blocks_no_exact_time_and_v1_still_reads(self):
        context=self.service._context(self.city)
        request={'kinds':['itinerary'],'start_date':self.day,'days':1,'planning_mode':'append'}
        value={'schema_version':2,'summary':'','notices':[],'attractions':[],'foods':[],
               'itineraries':[{'date':self.day,'time_block':'morning','duration_minutes':90,'duration_source':'ai_estimate','title':'人民公园','category':'观光','location':'人民公园','notes':'','attraction_names':['人民公园']}]}
        checked=self.service._validate_result(value,request,context)
        self.assertEqual(checked['itineraries'][0]['duration_minutes'],90)
        self.assertEqual(checked['itineraries'][0]['start_time'],'')
        self.assertEqual(checked['itineraries'][0]['fixed_start'],'')
        v1=copy.deepcopy(value);v1['schema_version']=1
        item=v1['itineraries'][0]
        for k in ('time_block','duration_minutes','duration_source'):item.pop(k)
        item['start_time']='14:00'
        checked=self.service._validate_result(v1,request,context)
        self.assertEqual(checked['itineraries'][0]['time_block'],'afternoon')
        self.assertEqual(checked['itineraries'][0]['fixed_start'],'')

    def test_choose_preview_contains_authoritative_order_and_applies_it(self):
        plan=self.plan()
        poi={'id':'poi','name':'公园入口','address':'测试地址','location':'121.4,31.2'}
        plan=store.save(server,self.path,{'city_id':self.city,'date':self.day,'version':plan['version'],
            'settings':{'start_anchor':poi,'end_anchor':poi},
            'additions':[{'title':'人民公园','duration_minutes':60,'poi':poi}]},self.user)
        class Map:
            def route(self,*args):return {'duration':600}
        evaluated=store.evaluate(server,self.path,{k:plan[k] for k in ('city_id','date','version')},self.user,Map())
        output={'schema_version':2,'stage':'choose_plan','recommended_candidate_ref':'c1','reasons':[],
                'adjustment_refs':[],'blocking_issue_refs':[]}
        job=self.job('daily_choose',output,candidate_refs=[evaluated['candidates'][0]['candidate_ref']])
        self.assertEqual(job['status'],'ready',job['error'])
        self.assertEqual(job['result']['recommended_order'],['人民公园'])
        self.assertEqual(self.apply(job)['visits'][0]['id'],plan['visits'][0]['id'])

    def test_hours_preview_apply_preserves_other_fields_and_fills_only_target(self):
        plan=self.plan()
        plan=store.save(server,self.path,{**{k:plan[k] for k in ('city_id','date','version')},'additions':[
            {'title':'人民公园','duration_minutes':90,'duration_source':'ai_estimate','priority':'must'},
            {'title':'博物馆','opening_start':'09:00','opening_end':'17:00'}]},self.user)
        output={'schema_version':2,'stage':'recommend_hours','hours':[{'place_ref':'v1','opening_start':'06:00','opening_end':'18:00','note':'AI 参考，待核实'}]}
        job=self.job('daily_hours',output,visit_id=plan['visits'][0]['id'])
        self.assertEqual(job['status'],'ready',job['error'])
        self.assertEqual(self.plan()['visits'][0]['opening_start'],'')
        self.assertEqual(len(json.loads(self.service.last_payload['input'])['hours_targets']),1)
        result=self.apply(job);v=result['visits'][0]
        self.assertEqual((v['opening_start'],v['opening_end'],v['opening_source']),('06:00','18:00','ai_estimate'))
        self.assertEqual((v['duration_minutes'],v['duration_source'],v['priority']),(90,'ai_estimate','must'))
        self.assertEqual(result['visits'][1]['opening_end'],'17:00')
        self.assertEqual(self.apply(job),result)

    def test_hours_reject_wrong_refs_invalid_windows_and_stale_apply(self):
        plan=self.plan();plan=store.save(server,self.path,{**{k:plan[k] for k in ('city_id','date','version')},'additions':[{'title':'公园'}]},self.user)
        output={'schema_version':2,'stage':'recommend_hours','hours':[{'place_ref':'v1','opening_start':'','opening_end':'','note':'无法确定，待核实'}]}
        for patch in ({'place_ref':'p99'},{'opening_start':'20:00','opening_end':'06:00'},{'opening_start':'99:00','opening_end':'12:00'},{'opening_start':'09:00'},{'note':''}):
            bad=copy.deepcopy(output);bad['hours'][0].update(patch)
            self.assertEqual(self.job('daily_hours',bad)['status'],'failed')
        good=self.job('daily_hours',output);self.assertEqual(good['status'],'ready')
        store.save(server,self.path,{**{k:plan[k] for k in ('city_id','date','version')},'updates':[{'id':plan['visits'][0]['id'],'changes':{'title':'修改名称'}}]},self.user)
        with self.assertRaises(server.ApiError):self.apply(good)


if __name__=='__main__':unittest.main()
