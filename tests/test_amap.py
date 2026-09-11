import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

from test_server import RunningServer, opener_with_cookies, json_request, unlock_project
from amap_service import AMapError, AMapService, coordinate, route_result


class AdapterTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, {'AMAP_JS_KEY': 'public-js', 'AMAP_SECURITY_JS_CODE': 'private-security',
                                     'AMAP_WEB_SERVICE_KEY': 'private-web'}):
            self.service = AMapService()

    def test_config_exposes_only_public_key(self):
        config = self.service.config('1234567890abcdef')
        self.assertTrue(config['enabled'])
        self.assertEqual(config['service_host'], '/_AMapService/1234567890abcdef')
        self.assertNotIn('private', json.dumps(config))

    def test_cycling_v4_response_and_error_are_handled_independently(self):
        body = {'errcode':0,'data':{'paths':[{'duration':720,'distance':2500,
                'steps':[{'instruction':'向东骑行','polyline':'121,31;121.1,31.1'}]}]}}
        with patch.object(self.service, 'fetch', return_value=(json.dumps(body).encode(), 'application/json')) as fetch:
            result = self.service.route({'mode':'bicycling','origin':'121,31','destination':'121.1,31.1'},'上海')
        self.assertEqual(fetch.call_args.args[1],'/v4/direction/bicycling')
        self.assertEqual(result['duration'],720)
        self.assertEqual(len(result['parts']),1)
        self.assertEqual(result['instructions'],['向东骑行'])
        for bad in ({'errcode':10001,'errmsg':'private-web'}, {'status':'1'}, {'errcode':0,'data':{'paths':[]}}):
            with patch.object(self.service, 'fetch', return_value=(json.dumps(bad).encode(), 'application/json')):
                with self.assertRaises(AMapError) as err:
                    self.service.route({'mode':'bicycling','origin':'121,31','destination':'121.1,31.1'},'上海')
                self.assertNotIn('private-web',str(err.exception))

    def test_invalid_coordinates_never_call_provider(self):
        for bad in ('nan,3', '181,3', '121,91', None, ['121', '31'], 'https://evil.test', '121,31&key=x'):
            with self.subTest(bad=bad), self.assertRaises(AMapError):
                coordinate(bad)

    def test_search_limits_city_and_normalizes_empty_fields(self):
        with patch.object(self.service, 'api', return_value={'pois': [
            {'name': '分店', 'id': 'p', 'location': '121,31', 'address': [], 'cityname': '上海'},
            {'name': '坏坐标', 'location': 'nan,31'}]}) as api:
            result = self.service.search({'keywords': '店'}, '上海')
        self.assertEqual(len(result['places']), 1)
        self.assertEqual(result['places'][0]['address'], '上海')
        self.assertEqual(api.call_args.args[1]['citylimit'], 'true')

    def test_missing_duration_and_empty_route_are_not_zero_minutes(self):
        for data in ({}, {'route': {'paths': [{'duration': []}]}}, {'route': {'paths': [{'duration': 'NaN'}]}}):
            with self.assertRaises(AMapError):
                route_result(data, 'walking')

    def test_transit_uses_segment_geometry_not_a_straight_line(self):
        data = {'route': {'transits': [{'duration': '900', 'distance': '2200', 'segments': [
            {'walking': {'steps': [{'instruction': '步行', 'polyline': '121,31;121.001,31'}]},
             'bus': {'buslines': [{'name': '地铁 1 号线', 'polyline': '121.001,31;121.01,31.01'}]}},
            {'railway': {'name': '火车'}}]}]}}
        result = route_result(data, 'transit')
        self.assertEqual(len(result['parts']), 2)
        self.assertTrue(result['incomplete'])
        self.assertEqual(result['duration'], 900)
        self.assertIn('乘坐 地铁 1 号线', result['instructions'])

    def test_transit_validates_departure_and_passes_both_cities(self):
        payload = {'mode': 'transit', 'origin': '121,31', 'destination': '121.1,31.1', 'date': '2026-09-12', 'time': '22:30'}
        with patch.object(self.service, 'api', return_value={'route': {'transits': [{'duration': '600'}]}}) as api:
            self.service.route(payload, '上海')
        self.assertEqual(api.call_args.args[1]['cityd'], '上海')
        self.assertEqual(api.call_args.args[1]['time'], '22:30')
        with self.assertRaises(AMapError):
            self.service.route({**payload, 'date': '2026-02-30'}, '上海')

    def test_proxy_cannot_become_open_proxy_or_override_keys(self):
        for path in ('//evil.test/', '/v3/place/text', '/v4/maps/../x', '/v4/maps%3F'):
            with self.assertRaises(AMapError):
                self.service.proxy(path, '')
        with self.assertRaises(AMapError):
            self.service.proxy('/v4/maps', 'callback=alert(1)')
        with patch.object(self.service, 'fetch', return_value=(b'{}', 'application/json')) as fetch:
            self.service.proxy('/v4/maps', 'key=evil&jscode=evil&callback=AMap.cb')
        self.assertEqual(fetch.call_args.args[2]['key'], 'public-js')
        self.assertEqual(fetch.call_args.args[2]['jscode'], 'private-security')

    def test_provider_error_does_not_echo_private_key(self):
        with patch.object(self.service, 'fetch', return_value=(b'{"status":"0","infocode":"10005","info":"private-web"}', 'application/json')):
            with self.assertRaises(AMapError) as caught:
                self.service.api('/v3/place/text', {})
        self.assertNotIn('private', str(caught.exception))
        self.assertIn('IP', str(caught.exception))

    def test_budget_counts_failed_attempts(self):
        self.service.daily_limit = 1
        with patch.object(self.service.opener, 'open', side_effect=urllib.error.URLError('private-web')):
            with self.assertRaises(AMapError) as first:
                self.service.fetch('restapi.amap.com', '/v4/maps', {})
            with self.assertRaises(AMapError) as second:
                self.service.fetch('restapi.amap.com', '/v4/maps', {})
        self.assertEqual(first.exception.status, 502)
        self.assertEqual(second.exception.status, 429)
        self.assertNotIn('private', str(first.exception))


class MapApiTests(unittest.TestCase):
    def setUp(self):
        self.running = RunningServer()
        self.opener = opener_with_cookies()

    def tearDown(self):
        self.running.close()

    def request(self, path, method='GET', payload=None):
        return json_request(self.opener, self.running.base_url, path, method=method, payload=payload)[1]

    def login(self):
        unlock_project(self.opener, self.running)
        self.request('/api/session', 'POST', {'user_id': '地图测试'})

    def test_identity_and_project_required(self):
        for unlocked in (False, True):
            if unlocked:
                unlock_project(self.opener, self.running)
            for path, method, payload in [('/api/maps/config', 'GET', None),
                    ('/api/maps/search', 'POST', {'city_id': 'shanghai', 'keywords': '博物馆'}),
                    ('/_AMapService/main/v4/maps', 'GET', None)]:
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.request(path, method, payload)
                self.assertEqual(caught.exception.code, 401)

    def test_disabled_setup_and_invalid_city(self):
        self.login()
        self.running.httpd.amap_service.web_key = ''
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/maps/search', 'POST', {'city_id': 'shanghai', 'keywords': '博物馆'})
        self.assertEqual(caught.exception.code, 503)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/maps/search', 'POST', {'city_id': 'does-not-exist', 'keywords': '博物馆'})
        self.assertEqual(caught.exception.code, 404)

    def test_read_only_route_keeps_shared_itinerary_and_revision(self):
        self.login()
        before = self.request('/api/snapshot?city_id=shanghai')
        with patch.object(self.running.httpd.amap_service, 'route', return_value={'duration': 900}) as route:
            self.assertEqual(self.request('/api/maps/route', 'POST', {'city_id': 'shanghai'}), {'duration': 900})
            self.assertEqual(route.call_args.args[1], '上海')
        after = self.request('/api/snapshot?city_id=shanghai')
        self.assertEqual(before['revision'], after['revision'])
        self.assertEqual(before['items'], after['items'])

    def test_proxy_checks_project_in_path_not_spoofed_header(self):
        self.login()
        project = self.running.httpd.project_store.create('其他项目', 'test-map')
        req = urllib.request.Request(self.running.base_url + f'/_AMapService/{project["id"]}/v4/maps',
                                     headers={'X-Trip-Project': 'main'})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.opener.open(req)
        self.assertEqual(caught.exception.code, 401)

    def test_csp_only_relaxes_app_when_map_configured(self):
        service = self.running.httpd.amap_service
        service.js_key = 'public'; service.security_code = 'secret'
        with self.opener.open(self.running.base_url + '/') as response:
            self.assertIn('https://webapi.amap.com', response.headers['Content-Security-Policy'])
        with self.opener.open(self.running.base_url + '/admin.html') as response:
            self.assertNotIn('https://webapi.amap.com', response.headers['Content-Security-Policy'])
