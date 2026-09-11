"""Saved, city-scoped opening hours go through preview and guarded apply."""
import json
import time
import unittest
from pathlib import Path
from functools import partial
import server
from test_daily_ai import LocalAI
from test_server import RunningServer, json_request, opener_with_cookies, unlock_project


class DailyHoursApiTests(unittest.TestCase):
    def test_generate_preview_apply_keeps_duration_and_priority(self):
        running=RunningServer();self.addCleanup(running.close)
        path=Path(running.temporary_directory.name)/'trip.db'
        service=LocalAI(path,api_key='offline-test',get_context=partial(server.ai_context,path),
            normalize_item=server.validate_payload,import_items=partial(server.import_ai_items,path))
        running.httpd.ai_service=service
        service.output={'schema_version':2,'stage':'recommend_hours','hours':[{'place_ref':'v1',
            'opening_start':'08:00','opening_end':'17:30','note':'AI 参考，待核实'}]}
        client=opener_with_cookies();unlock_project(client,running)
        def req(url,method='GET',data=None):
            return json_request(client,running.base_url,url,method=method,payload=data)[1]
        req('/api/session','POST',{'user_id':'开放时间测试'})
        url='/api/day-plan?city_id=shanghai&date=2030-01-01'
        plan=req(url)
        plan=req('/api/day-plan','PUT',{**{k:plan[k] for k in ('city_id','date','version')},
            'additions':[{'title':'人民公园','priority':'must','duration_minutes':90,'duration_source':'ai_estimate'}]})
        job=req('/api/ai/jobs','POST',{'purpose':'daily_hours','city_id':'shanghai','start_date':'2030-01-01',
            'day_version':plan['version'],'visit_id':plan['visits'][0]['id'],
            '_daily_context':{'input':{'hours_targets':['injected']}}})
        for _ in range(200):
            if job['status'] not in ('running','queued'):break
            time.sleep(.01);job=req('/api/ai/jobs/'+job['id'])
        self.assertEqual(job['status'],'ready',job['error'])
        self.assertNotIn('injected',service.last_payload['input'])
        self.assertEqual(req(url)['visits'][0]['opening_start'],'')
        result=req('/api/day-plan/ai-apply','POST',{**{k:plan[k] for k in ('city_id','date','version')},'job_id':job['id']})
        visit=result['visits'][0]
        self.assertEqual((visit['opening_start'],visit['opening_end'],visit['opening_source']),('08:00','17:30','ai_estimate'))
        self.assertEqual((visit['duration_minutes'],visit['duration_source'],visit['priority']),(90,'ai_estimate','must'))
        snapshot=req('/api/snapshot?city_id=shanghai')
        saved=next(v for v in snapshot['items']['itinerary'] if v['id']==visit['id'])
        self.assertEqual(saved['opening_note'],'AI 参考，待核实')
