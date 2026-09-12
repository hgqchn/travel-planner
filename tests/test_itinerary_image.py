import copy
import json
import unittest
from unittest.mock import Mock

from amap_service import AMapError, AMapService
from itinerary_image import build_image_data, build_image_bundle, build_office_map, build_office_maps, mercator
from route_image import route_map, png_pixels


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
        self.assertIsNone(build_office_map(self.data, self.amap))

    def test_office_map_combines_days_and_preserves_basemap(self):
        other = copy.deepcopy(self.plan)
        other.update(date='2026-10-02', city_name='北京')
        other['visits'][0]['poi'] = dict(name='北京公园南门', location='116.4,39.9')
        other['routes'][0]['result']['parts'] = [[[116.3,39.8],[116.4,39.9]]]
        self.data['daily_plans'].append(other)
        png, legend = build_office_map(self.data, self.amap)
        self.amap.static_map.assert_called_once()
        self.assertIn('2026-10-01 · 上海', legend)
        self.assertIn('2026-10-02 · 北京', legend)
        self.assertIn('北京公园南门', '\n'.join(legend))
        self.assertNotIn('秘密转弯说明', '\n'.join(legend))
        before = png_pixels(self.amap.static_map.return_value[0])
        after = png_pixels(png)
        self.assertEqual(before[:3000], after[:3000])
        self.assertNotEqual(before, after)

    def test_palette_png_used_by_static_maps(self):
        import struct
        import zlib
        def chunk(kind, body):
            return struct.pack('!I', len(body))+kind+body+struct.pack('!I', zlib.crc32(kind+body)&0xffffffff)
        png = (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('!2I5B',1000,600,8,3,0,0,0))
               +chunk(b'PLTE', bytes([10,20,30]))+chunk(b'IDAT',zlib.compress(bytes(1001*600)))+chunk(b'IEND',b''))
        self.assertEqual(png_pixels(png), bytes([10,20,30])*(1000*600))
        with self.assertRaises(ValueError):
            png_pixels(b'not a png')

    def test_png_bundle_has_one_file_per_date_including_unlocated_days(self):
        second = copy.deepcopy(self.plan)
        second.update(date='2026-10-02', settings={}, routes=[], visits=[dict(id='x', title='第二天未确认地点')])
        self.data['daily_plans'].append(second)
        result = build_image_bundle(self.data, self.amap)
        self.assertTrue(result['filename'].endswith('.zip'))
        self.assertEqual([d['filename'] for d in result['daily_images']], ['2026-10-01-当天行程.png','2026-10-02-当天行程.png'])
        self.assertEqual(len(result['daily_images'][0]['days']), 1)
        self.assertIsNone(result['daily_images'][1]['map'])
        self.assertEqual(result['daily_images'][1]['days'][0]['stops'], ['第二天未确认地点'])

    def test_office_overview_then_daily_maps_are_scoped_and_ordered(self):
        import io
        import zipfile
        from xml.etree import ElementTree as ET
        from itinerary_export import build_docx, build_xlsx
        import daily_planner
        second = copy.deepcopy(self.plan)
        second['date'] = '2026-10-02'
        second['visits'][0]['poi']['name'] = '第二天公园北门'
        self.data['daily_plans'].insert(0, second)
        for plan in self.data['daily_plans']:
            plan['settings'] = {**daily_planner.defaults(), **plan['settings']}
        self.data['items'] = [dict(date=d, city_name='上海', title='当天游览') for d in ['2026-10-01','2026-10-02']]
        self.data.update(build_office_maps(self.data, self.amap))
        self.assertEqual(self.amap.static_map.call_count, 3)
        self.assertNotIn('第二天公园北门', '\n'.join(self.data['daily_route_maps']['2026-10-01'][1]))
        self.assertIn('第二天公园北门', '\n'.join(self.data['daily_route_maps']['2026-10-02'][1]))
        for builder, xml_path in [(build_docx, 'word/document.xml'), (build_xlsx, 'xl/worksheets/sheet3.xml')]:
            with zipfile.ZipFile(io.BytesIO(builder(self.data))) as archive:
                images = [n for n in archive.namelist() if n.endswith('.png')]
                self.assertEqual(len(images), 3)
                value = ''.join(ET.fromstring(archive.read(xml_path)).itertext())
                self.assertLess(value.index('所有天整体行程图'), value.index('2026-10-01 当天行程图'))
                self.assertLess(value.index('2026-10-01 当天行程图'), value.index('2026-10-02 当天行程图'))
                self.assertNotIn('无街道底图', value)
                if builder == build_xlsx:
                    root = ET.fromstring(archive.read('xl/workbook.xml'))
                    self.assertEqual(root.find('{*}sheets')[0].attrib['name'], '整体行程图')

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
