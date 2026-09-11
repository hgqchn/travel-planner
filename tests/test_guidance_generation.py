"""Independent guidance uses final saved city plans and optimistic saves."""
import json
import tempfile
import time
import unittest
from functools import partial
from pathlib import Path

import ai_service
import daily_plan_store
import server
import travel_guidance
from test_daily_ai import LocalAI


class GuidanceGenerationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'trip.db'
        seed=Path(self.tmp.name)/'seed.json';seed.write_text('{}')
        server.init_database(self.path,'test-code',seed)
        self.user='生成提示测试';server.claim_identity(self.path,{'user_id':self.user})
        self.service=LocalAI(self.path,api_key='test-only',get_context=partial(server.ai_context,self.path),
            normalize_item=server.validate_payload,import_items=partial(server.import_ai_items,self.path))
        self.service.output={'schema_version':1,'notices':['外滩沿江步行，准备舒适鞋。']}

    def tearDown(self):
        self.service.close();self.tmp.cleanup()

    def add(self,city='shanghai',date='2030-10-01',title='外滩'):
        p=daily_plan_store.get(server,self.path,city,date)
        return daily_plan_store.save(
            server,self.path, {**{k:p[k] for k in ('city_id','date','version')},
            'additions':[{'title':title,'location':title,'time_block':'morning','duration_minutes':90}]},self.user)

    def request(self):
        data={'purpose':'travel_guidance','city_id':'shanghai','confirmed_complete':True}
        data['_guidance_context']=travel_guidance.prepare(server,self.path,data)
        job=self.service.create_job(self.user,'device',data)
        for _ in range(200):
            job=self.service.get_job(self.user,'device',job['id'])
            if job['status'] not in ('queued','running'): return job
            time.sleep(.01)
        self.fail('Mock AI timeout')

    def test_requires_confirmation_and_nonempty_saved_city(self):
        for data in ({'city_id':'shanghai'}, {'city_id':'shanghai','confirmed_complete':True}):
            with self.assertRaises(server.ApiError):travel_guidance.prepare(server,self.path,data)
        with self.assertRaises(ai_service.AIError):self.service._validate_request({'purpose':'travel_guidance','city_id':'shanghai'})

    def test_independent_generation_reads_saved_city_only_and_never_writes_automatically(self):
        self.add(); self.add(city='beijing',title='其他城市私有内容')
        job=self.request();self.assertEqual(job['status'],'ready',job['error'])
        prompt=json.loads(self.service.last_payload['input'])
        self.assertEqual(prompt['city'],'上海')
        visit=prompt['days'][0]['visits'][0]
        self.assertEqual((visit['title'],visit['duration_minutes'],visit['time_block']),('外滩',90,'morning'))
        self.assertNotIn('其他城市私有内容',self.service.last_payload['input'])
        self.assertNotIn(self.user,self.service.last_payload['input'])
        self.assertNotIn('plan_version',self.service.last_payload['input'])
        self.assertEqual(server.snapshot(self.path,'shanghai')['travel_guidance'],[])
        result=server.change_guidance(self.path,{'city_id':'shanghai','version':job['request']['guidance_version'],
            'plan_version':job['request']['plan_version'],'notices':job['result']['notices']},self.user)
        self.assertEqual(result['guidance']['notices'],job['result']['notices'])

    def test_changed_itinerary_rejects_generated_draft_without_overwriting_notes(self):
        self.add();job=self.request();self.add(title='新增地点')
        with self.assertRaises(server.ApiError) as error:
            server.change_guidance(self.path,{'city_id':'shanghai','version':job['request']['guidance_version'],
                'plan_version':job['request']['plan_version'],'notices':job['result']['notices']},self.user)
        self.assertEqual(error.exception.status,409)
        self.assertEqual(server.snapshot(self.path,'shanghai')['travel_guidance'],[])

    def test_manual_note_edit_during_generation_is_not_overwritten(self):
        self.add();job=self.request()
        server.change_guidance(self.path,{'city_id':'shanghai','version':job['request']['guidance_version'],'notices':['同行者刚修改的提示']},self.user)
        with self.assertRaises(server.ApiError) as error:
            server.change_guidance(self.path,{'city_id':'shanghai','version':job['request']['guidance_version'],
                'plan_version':job['request']['plan_version'],'notices':job['result']['notices']},self.user)
        self.assertEqual(error.exception.status,409)
        self.assertEqual(server.snapshot(self.path,'shanghai')['travel_guidance'][0]['notices'],['同行者刚修改的提示'])

    def test_bad_outputs_are_rejected(self):
        for value in ({'schema_version':1,'notices':[]},{'schema_version':1,'notices':['a']*13},
                      {'schema_version':True,'notices':['a']},{'schema_version':1,'notices':['x'*501]},
                      {'schema_version':1,'notices':['a'],'itineraries':[]}):
            with self.assertRaises(ValueError):travel_guidance.validate_generated(value)


class GuidanceGenerationApiTests(unittest.TestCase):
    def test_authenticated_confirmation_uses_server_snapshot_and_saves_only_on_confirmation(self):
        import urllib.error
        from test_server import RunningServer, json_request, opener_with_cookies, unlock_project
        running=RunningServer();self.addCleanup(running.close)
        path=Path(running.temporary_directory.name)/'trip.db'
        service=LocalAI(path,api_key='offline-fake',get_context=partial(server.ai_context,path),
            normalize_item=server.validate_payload,import_items=partial(server.import_ai_items,path))
        service.output={'schema_version':1,'notices':['按已保存路线安排步行休息。']}
        running.httpd.ai_service=service
        client=opener_with_cookies()
        def req(url,method='GET',body=None):
            return json_request(client,running.base_url,url,method=method,payload=body)[1]
        data={'purpose':'travel_guidance','city_id':'shanghai','confirmed_complete':True,
              '_guidance_context':{'input':{'secret':'client-injected-itinerary'}}}
        with self.assertRaises(urllib.error.HTTPError) as error:req('/api/ai/jobs','POST',data)
        self.assertEqual(error.exception.code,401)
        unlock_project(client,running);req('/api/session','POST',{'user_id':'提示API测试'})
        with self.assertRaises(urllib.error.HTTPError) as error:req('/api/ai/jobs','POST',{**data,'confirmed_complete':False})
        self.assertEqual(error.exception.code,400)
        plan=req('/api/day-plan?city_id=shanghai&date=2030-10-01')
        req('/api/day-plan','PUT',{**{k:plan[k] for k in ('city_id','date','version')},'additions':[{'title':'API saved place','duration_minutes':90}]})
        before=req('/api/snapshot?city_id=shanghai')
        job=req('/api/ai/jobs','POST',data)
        for _ in range(200):
            if job['status'] not in ('queued','running'):break
            time.sleep(.01);job=req('/api/ai/jobs/'+job['id'])
        self.assertEqual(job['status'],'ready',job['error'])
        self.assertNotIn('client-injected-itinerary',service.last_payload['input'])
        self.assertTrue(json.loads(service.last_payload['input'])['days'])
        self.assertEqual(req('/api/snapshot?city_id=shanghai')['travel_guidance'],before['travel_guidance'])
        saved=req('/api/travel-guidance','PUT',{'city_id':'shanghai','version':job['request']['guidance_version'],
            'plan_version':job['request']['plan_version'],'notices':job['result']['notices']})
        self.assertEqual(saved['guidance']['notices'],job['result']['notices'])
        self.assertEqual(req('/api/snapshot?city_id=shanghai')['items'],before['items'])
