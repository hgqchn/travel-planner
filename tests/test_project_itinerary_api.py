import json
import unittest
import urllib.error
import urllib.request

from test_server import RunningServer, opener_with_cookies, json_request, unlock_project


class ProjectItineraryApiTests(unittest.TestCase):
    def setUp(self):
        self.running = RunningServer()
        self.opener = opener_with_cookies()

    def tearDown(self):
        self.running.close()

    def request(self, path='/api/project-itinerary', method='PUT', payload=None):
        return json_request(self.opener, self.running.base_url, path, method=method, payload=payload)[1]

    def login(self):
        unlock_project(self.opener, self.running)
        self.request('/api/session', 'POST', {'user_id': '日期测试'})

    def snapshot(self):
        return self.request('/api/snapshot?city_id=shanghai', 'GET')

    def add(self, date='2026-10-01', city='shanghai'):
        return self.request('/api/items/itinerary', 'POST', {'city_id':city, 'date':date, 'start_time':'09:00', 'title':'测试集合'})

    def payload(self):
        city = next(c for c in self.snapshot()['project_itinerary']['cities'] if c['city_id']=='shanghai')
        return {'city_id':'shanghai', 'version':city['version'], 'start_date':'2026-10-07', 'confirm_delete':True}

    def test_requires_project_access_and_identity_for_both_actions(self):
        for unlocked in (False, True):
            if unlocked:
                unlock_project(self.opener, self.running)
            for method in ('PUT','DELETE'):
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    self.request(method=method, payload={'city_id':'shanghai','version':'fake'})
                self.assertEqual(raised.exception.code, 401)

    def test_shift_delete_conflict_and_retained_city_catalog_over_http(self):
        self.login(); self.add(); self.add('2026-10-03'); self.add('2026-10-07', 'beijing')
        before = self.snapshot(); payload = self.payload()
        result = self.request(payload=payload)
        self.assertEqual(result['offset_days'], 6)
        after = self.snapshot()
        self.assertEqual(sorted(i['date'] for i in after['items']['itinerary']), ['2026-10-07','2026-10-09'])
        self.assertEqual(len(after['project_itinerary']['conflicts']), 1)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(method='DELETE', payload=payload)
        self.assertEqual(raised.exception.code, 409)
        self.request(method='DELETE', payload=self.payload())
        deleted = self.snapshot()
        self.assertEqual(deleted['items']['itinerary'], [])
        self.assertEqual(deleted['cities'], before['cities'])
        self.assertEqual(deleted['items']['attraction'], before['items']['attraction'])
        self.assertEqual(deleted['items']['food'], before['items']['food'])
        self.assertEqual([c['city_id'] for c in deleted['project_itinerary']['cities']], ['beijing'])

    def test_wrong_origin_and_other_project_cannot_use_current_credentials(self):
        self.login(); self.add()
        payload = json.dumps(self.payload()).encode()
        other = self.running.httpd.project_store.create('另一个项目', 'test-only')
        before = self.snapshot()
        for method in ('PUT', 'DELETE'):
            for headers in ({'Origin':'https://untrusted.invalid'},
                            {'Origin':self.running.base_url, 'X-Trip-Project':other['id']}):
                request = urllib.request.Request(self.running.base_url+'/api/project-itinerary', data=payload,
                    method=method, headers={'Content-Type':'application/json', **headers})
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    self.opener.open(request)
                self.assertIn(raised.exception.code, (401, 403))
        self.assertEqual(self.snapshot()['items'], before['items'])
