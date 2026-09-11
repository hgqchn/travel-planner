import urllib.error
import unittest
from test_server import RunningServer, opener_with_cookies, json_request, TEST_PROJECT_CODE


class DailyApiTests(unittest.TestCase):
    def setUp(self):
        self.running=RunningServer();self.client=opener_with_cookies()
    def tearDown(self):self.running.close()
    def req(self,path,method='GET',payload=None):
        return json_request(self.client,self.running.base_url,path,method=method,payload=payload)[1]
    def login(self):
        self.req('/api/project-session','POST',{'project_code':TEST_PROJECT_CODE})
        self.req('/api/session','POST',{'user_id':'日计划测试'})
    def test_authenticated_daily_crud_and_conflict(self):
        path='/api/day-plan?city_id=shanghai&date=2030-01-01'
        with self.assertRaises(urllib.error.HTTPError):self.req(path)
        self.login();plan=self.req(path)
        payload={k:plan[k] for k in ('city_id','date','version')}
        new=self.req('/api/day-plan','PUT',{**payload,'additions':[{'title':'人民公园','duration_minutes':90,'time_block':'morning'}]})
        self.assertEqual(new['visits'][0]['fixed_start'],'')
        self.assertEqual(self.req(path)['version'],new['version'])
        with self.assertRaises(urllib.error.HTTPError) as err:self.req('/api/day-plan','PUT',{**payload,'order':[]})
        self.assertEqual(err.exception.code,409)
        result=self.req('/api/day-plan/evaluate','POST',{k:new[k] for k in ('city_id','date','version')})
        self.assertEqual(result['candidates'][0]['evaluation']['time_fit'],'unknown')
        state=self.req('/api/snapshot?city_id=shanghai')
        self.assertTrue(any(p['date']=='2030-01-01' for p in state['daily_plans']))
    def test_client_cannot_inject_evaluation_or_move_across_days(self):
        self.login();plan=self.req('/api/day-plan?city_id=shanghai&date=2030-01-01')
        base={k:plan[k] for k in ('city_id','date','version')}
        for bad in ({'evaluation':{'route_status':'checked'}},{'additions':[{'title':'跨日','date':'2030-01-02'}]}):
            with self.assertRaises(urllib.error.HTTPError) as err:self.req('/api/day-plan','PUT',{**base,**bad})
            self.assertEqual(err.exception.code,400)

    def test_create_delete_require_project_and_identity_and_sync_empty_days(self):
        base = {'city_id':'shanghai','date':'2030-02-01'}
        for method in ('POST','DELETE'):
            with self.assertRaises(urllib.error.HTTPError) as err:
                self.req('/api/day-plan',method,base)
            self.assertEqual(err.exception.code,401)
        self.req('/api/project-session','POST',{'project_code':TEST_PROJECT_CODE})
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.req('/api/day-plan','POST',base)
        self.assertEqual(err.exception.code,401)
        self.req('/api/session','POST',{'user_id':'日计划测试'})
        plan = self.req('/api/day-plan','POST',base)
        self.assertEqual(plan['visits'],[])
        self.assertIn(base['date'],[p['date'] for p in self.req('/api/snapshot')['daily_plans']])
        child = self.running.httpd.project_store.create('另一项目','other-code')
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.req('/api/day-plan?project='+child['id'],'DELETE',{**base,'version':plan['version']})
        self.assertEqual(err.exception.code,401)
        self.req('/api/day-plan','DELETE',{**base,'version':plan['version']})
        self.assertNotIn(base['date'],[p['date'] for p in self.req('/api/snapshot')['daily_plans']])
