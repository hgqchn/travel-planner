import copy
import json
import unittest
from unittest.mock import Mock

from amap_service import AMapError, AMapService
from itinerary_image import build_image_data, mercator
from route_image import route_map


class ItineraryImageTests(unittest.TestCase):
    def setUp(self):
        self.plan = dict(date='2026-10-01', city_name='上海', settings={
            'start_anchor': {'name': '酒店正门', 'location': '121.4,31.2'},
            'end_anchor': {'name': '酒店正门', 'location': '121.4,31.2'},
            'leg_modes': {'["a","@end"]': 'driving'},
        }, visits=[dict(id='a', title='逛公园', location='公园', poi={'name': '公园东门', 'location': '121.5,31.3'}),
                   dict(id='b', title='备选餐厅', is_backup=True)], routes=[
            dict(from_ref='@start', to_ref='a', mode='walking', result=dict(duration=600,
                 parts=[[[121.4,31.2],[121.5,31.3]]], instructions=['秘密转弯说明']))])
        self.data = dict(project_name='假期', scope_name='全部城市', exported_at='2026-09-12', daily_plans=[self.plan])
        self.amap = Mock()
        self.amap.static_map.return_value = (route_map(self.plan)[0], 'image/png')

    def test_confirmed_endpoints_modes_pending_and_no_directions(self):
        before = copy.deepcopy(self.data)
        result = build_image_data(self.data, self.amap)
        self.assertEqual(result['days'][0]['segments'], [
            '1. 酒店正门 → 2. 公园东门 · 步行', '2. 公园东门 → 3. 酒店正门 · 驾车（待规划）'])
        self.assertEqual(result['days'][0]['backups'], ['备选餐厅'])
        self.assertNotIn('秘密转弯说明', json.dumps(result, ensure_ascii=False))
        self.assertEqual(self.data, before)
        self.assertTrue(result['filename'].endswith('.png'))
        self.assertEqual(len(result['map']['paths']), 1)
        self.assertEqual(len(result['map']['pins']), 3)

    def test_map_fits_all_cities_and_days_sorted(self):
        other = copy.deepcopy(self.plan)
        other.update(date='2026-10-02', city_name='北京', routes=[])
        other['visits'][0]['poi']['location'] = '116.4,39.9'
        self.data['daily_plans'].insert(0, other)
        result = build_image_data(self.data, self.amap)
        self.assertEqual([d['city'] for d in result['days']], ['上海', '北京'])
        spec = result['map']
        for pin in spec['pins']:
            x, y = mercator(pin['point'])
            self.assertLess(abs(x-spec['center'][0])*256*2**spec['zoom'], 450)
            self.assertLess(abs(y-spec['center'][1])*256*2**spec['zoom'], 250)
        self.amap.static_map.assert_called_once()

    def test_no_confirmed_locations_does_not_request_map(self):
        self.plan.update(settings={}, routes=[], visits=[dict(id='a', title='未确认地点')])
        with self.assertRaisesRegex(AMapError, '确认起点和终点'):
            build_image_data(self.data, self.amap)
        self.amap.static_map.assert_not_called()

    def test_static_map_validates_images_and_keeps_key_server_side(self):
        service = AMapService()
        service.web_key = 'private-test-key'
        service.fetch = Mock(return_value=self.amap.static_map.return_value)
        body, mime = service.static_map('121.4,31.2', 12)
        self.assertEqual(mime, 'image/png')
        self.assertTrue(body.startswith(b'\x89PNG'))
        self.assertEqual(service.fetch.call_args[0][2]['key'], 'private-test-key')
        service.fetch.return_value = (b'{"key":"private-test-key"}', 'application/json')
        with self.assertRaisesRegex(AMapError, '底图生成失败') as caught:
            service.static_map('121.4,31.2', 12)
        self.assertNotIn('private-test-key', str(caught.exception))
