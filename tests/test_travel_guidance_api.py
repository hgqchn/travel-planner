"""HTTP permissions and optimistic updates for city guidance."""
import json
import unittest
import urllib.error
import urllib.request

from test_server import RunningServer, opener_with_cookies, json_request, unlock_project


class GuidanceApiTests(unittest.TestCase):
    def setUp(self):
        self.running = RunningServer()
        self.opener = opener_with_cookies()

    def tearDown(self):
        self.running.close()

    def request(self, path='/api/travel-guidance', method='GET', payload=None):
        return json_request(self.opener, self.running.base_url, path, method=method, payload=payload)[1]

    def error(self, status, method='GET', payload=None):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request('/api/travel-guidance?city_id=shanghai', method, payload)
        self.assertEqual(raised.exception.code, status)

    def login(self):
        unlock_project(self.opener, self.running)
        self.request('/api/session', 'POST', {'user_id': '测试同行者'})

    def test_project_and_identity_required_for_read_edit_and_delete(self):
        payload = {'city_id': 'shanghai', 'version': 'a'*24, 'summary': '提示', 'notices': []}
        for method in ('GET', 'PUT', 'DELETE'):
            self.error(401, method, None if method == 'GET' else payload)
        unlock_project(self.opener, self.running)
        for method in ('GET', 'PUT', 'DELETE'):
            self.error(401, method, None if method == 'GET' else payload)

    def test_end_to_end_edit_conflict_delete_and_city_scope(self):
        self.login()
        current = self.request('/api/travel-guidance?city_id=shanghai')['guidance']
        payload = {'city_id': 'shanghai', 'version': current['version'], 'summary': '一起带相机', 'notices': ['预约博物馆']}
        saved = self.request(method='PUT', payload=payload)['guidance']
        self.assertEqual(saved['summary'], '')
        self.assertEqual(saved['notices'], payload['notices'])
        self.assertTrue(saved['manual'])
        self.error(409, 'PUT', payload)
        self.error(409, 'DELETE', payload)
        self.assertEqual(self.request('/api/travel-guidance?city_id=beijing')['guidance']['summary'], '')
        snapshot = self.request('/api/snapshot?city_id=shanghai')
        self.assertEqual(len(snapshot['travel_guidance']), 1)
        self.request(method='DELETE', payload={**payload, 'version': saved['version']})
        after = self.request('/api/snapshot?city_id=shanghai')
        self.assertEqual(after['travel_guidance'], [])
        self.assertEqual(after['items'], snapshot['items'])
        self.assertGreater(after['revision'], snapshot['revision'])

    def test_wrong_origin_and_cross_project_credentials_cannot_write(self):
        self.login()
        group = self.request('/api/travel-guidance?city_id=shanghai')['guidance']
        payload = json.dumps({'city_id': 'shanghai', 'version': group['version'], 'summary': '提示', 'notices': []}).encode()
        project = self.running.httpd.project_store.create('隔离测试', 'test-only')
        for headers in ({'Origin': 'https://untrusted.invalid'},
                        {'Origin': self.running.base_url, 'X-Trip-Project': project['id']}):
            req = urllib.request.Request(self.running.base_url+'/api/travel-guidance', data=payload,
                method='PUT', headers={'Content-Type': 'application/json', **headers})
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.opener.open(req)
            self.assertIn(raised.exception.code, (401, 403))
        self.assertEqual(self.request('/api/snapshot?city_id=shanghai')['travel_guidance'], [])
