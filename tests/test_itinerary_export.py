from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import quote, unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import itinerary_export as export
import server
from test_server import RunningServer, TEST_ADMIN_PASSWORD, json_request, opener_with_cookies, unlock_project


def xml_parts(body):
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert archive.testzip() is None
        return {name: ET.fromstring(archive.read(name)) for name in archive.namelist() if not name.endswith('.png')}


def content(body):
    return ' '.join(node.text or '' for root in xml_parts(body).values() for node in root.iter())


class ExportFormatTests(unittest.TestCase):
    def setUp(self):
        self.data = {'project_name': '假期 <一起> & 旅行', 'scope_name': '上海',
                     'exported_at': '2026-09-10 19:00:00 UTC+08:00', 'items': [
                         {'date': '2026-10-01', 'start_time': '08:30', 'city_name': '上海',
                          'title': '=1+1', 'category': '景点', 'location': '外滩 & 南京路',
                          'notes': '第一行\n第二行\t集合\x01保留_x000A_😀',
                          'link': 'https://example.com/?a=1&b=2'},
                         {'date': '2026-10-02', 'city_name': '北京', 'title': '未定时间',
                          'notes': '@SUM(A1) <原样保存>', 'link': 'javascript:alert(1)'},
                     ]}

    def test_docx_preserves_text_breaks_and_only_http_hyperlinks(self):
        parts = xml_parts(export.build_docx(self.data))
        ns = {'w': export.WORD, 'r': export.PKG}
        doc = parts['word/document.xml']
        values = ''.join(doc.itertext())
        for value in ('假期 <一起> & 旅行', '=1+1', '外滩 & 南京路', '第一行', '第二行', '保留_x000A_😀', '时间待定'):
            self.assertIn(value, values)
        self.assertGreater(len(doc.findall('.//w:br', ns)), 0)
        self.assertGreater(len(doc.findall('.//w:tab', ns)), 0)
        links = [node.attrib['Target'] for node in parts['word/_rels/document.xml.rels'] if node.attrib['Type'].endswith('/hyperlink')]
        self.assertEqual(links, ['https://example.com/?a=1&b=2'])

    def test_saved_route_png_is_embedded_in_both_office_formats(self):
        import daily_planner
        self.data['daily_plans']=[dict(city_name='上海',date='2026-10-01',settings=daily_planner.defaults(),visits=[
            dict(id='a',title='起点公园',poi=dict(location='121.1,31.1')),
            dict(id='b',title='终点广场',poi=dict(location='121.2,31.2'))],routes=[dict(from_ref='a',to_ref='b',mode='walking',result=dict(
                duration=600,parts=[[[121.1,31.1],[121.15,31.12],[121.2,31.2]]],instructions=['沿河向北步行']))])]
        for builder in (export.build_docx,export.build_xlsx):
            body=builder(self.data); parts=xml_parts(body)
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                images=[name for name in archive.namelist() if name.endswith('.png')]
                self.assertEqual(len(images),1)
                self.assertTrue(archive.read(images[0]).startswith(b'\x89PNG'))
            self.assertIn('沿河向北步行',content(body))
            self.assertTrue(any(node.attrib.get('Type','').endswith('/image') for root in parts.values() for node in root.iter()))

    def test_excel_typed_dates_times_and_formula_like_text(self):
        parts = xml_parts(export.build_xlsx(self.data))
        ns = {'s': export.SHEET}
        sheet = parts['xl/worksheets/sheet1.xml']
        cells = {node.attrib['r']: node for node in sheet.findall('.//s:c', ns)}
        self.assertEqual(cells['D2'].attrib['t'], 'inlineStr')
        self.assertEqual(''.join(cells['D2'].itertext()), '=1+1')
        self.assertEqual(sheet.findall('.//s:f', ns), [])
        self.assertEqual(float(cells['A2'].find('s:v', ns).text), export.excel_date('2026-10-01'))
        self.assertAlmostEqual(float(cells['C2'].find('s:v', ns).text), 8.5 / 24)
        self.assertEqual(''.join(cells['C3'].itertext()), '')
        self.assertIn('_x005F_x000A_', ''.join(cells['G2'].itertext()))
        self.assertEqual(sheet.find('s:autoFilter', ns).attrib['ref'], 'A1:H3')
        self.assertEqual(sheet.find('.//s:pane', ns).attrib['state'], 'frozen')
        self.assertEqual(len(parts['xl/worksheets/_rels/sheet1.xml.rels']), 1)
        workbook = parts['xl/workbook.xml']
        self.assertEqual([node.attrib['name'] for node in workbook.find('s:sheets', ns)], ['行程明细', '导出说明'])

    def test_no_links_packages_and_filename_safety(self):
        for item in self.data['items']:
            item['link'] = ''
        for builder in (export.build_docx, export.build_xlsx):
            xml_parts(builder(self.data))
        self.data['project_name'] = '../名字\r\n"/\\:*?<>|'
        name = export.filename(self.data, 'xlsx')
        self.assertNotRegex(name, r'[<>:"/\\|?*\r\n]')
        self.assertTrue(name.endswith('.xlsx'))
        self.assertEqual(export.excel_date('1900-01-01'), 1)
        self.assertEqual(export.excel_date('1900-03-01'), 61)
        self.assertEqual(export.excel_date('0001-01-01'), '0001-01-01')


class ExportApiTests(unittest.TestCase):
    def setUp(self):
        self.running = RunningServer()
        self.opener = opener_with_cookies()
        unlock_project(self.opener, self.running)
        self.json('/api/session', 'POST', {'user_id': '导出测试'})
        # Keep all mutations in disposable test databases.
        with server.connect_db(self.running.httpd.db_path) as db:
            db.execute("DELETE FROM items WHERE kind='itinerary'")

    def tearDown(self):
        self.running.close()

    def json(self, path, method='GET', payload=None):
        return json_request(self.opener, self.running.base_url, path, method=method, payload=payload)[1]

    def add(self, city, title, time='', day='2026-10-01', project=None):
        path = '/api/items/itinerary' + (f'?project={project}' if project else '')
        return self.json(path, 'POST', {'city_id': city, 'date': day, 'start_time': time,
                                      'title': title, 'notes': '备注原文\n集合提示'})['item']

    def download(self, query='format=docx&scope=all', opener=None, headers=None):
        req = urllib.request.Request(self.running.base_url + '/api/itinerary/export?' + query, headers=headers or {})
        response = (opener or self.opener).open(req, timeout=5)
        return response, response.read()

    def test_download_types_names_order_and_read_only_snapshot(self):
        self.add('shanghai', '稍晚', '14:00')
        self.add('shanghai', '时间未定')
        self.add('shanghai', '最早', '08:00')
        self.add('beijing', '北京安排', '09:00')
        before = server.snapshot(self.running.httpd.db_path)
        for fmt in ('docx', 'xlsx'):
            response, body = self.download(f'format={fmt}&scope=city&city_id=shanghai')
            self.assertEqual(response.headers['Content-Type'], export.MIME_TYPES[fmt])
            self.assertEqual(int(response.headers['Content-Length']), len(body))
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertIn(f'filename="itinerary.{fmt}"', response.headers['Content-Disposition'])
            self.assertIn('上海-行程', unquote(response.headers['Content-Disposition']))
            value = content(body)
            self.assertLess(value.index('时间未定'), value.index('最早'))  # Shared visit order, independent of legacy HH:MM.
            self.assertLess(value.index('最早'), value.index('稍晚'))  # Unassigned slot first, then chronological slots.
            self.assertNotIn('北京安排', value)
            self.assertNotIn('导出测试', value)
            self.assertIn('北京安排', content(self.download(f'format={fmt}&scope=all')[1]))
        after = server.snapshot(self.running.httpd.db_path)
        for key in ('revision', 'items', 'activity'):
            self.assertEqual(before[key], after[key])

    def test_invalid_empty_and_locked_requests_return_json_errors(self):
        for query, status in [('format=pdf&scope=all', 400), ('format=docx&scope=wrong', 400),
                              ('format=xlsx', 400), ('format=docx&city_id=missing', 404),
                              ('format=xlsx&scope=city&city_id=', 400), ('format=xlsx&scope=all', 400)]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.download(query)
            self.assertEqual(error.exception.code, status)
            self.assertIn('error', json.loads(error.exception.read()))
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.download(opener=opener_with_cookies())
        self.assertEqual(error.exception.code, 401)
        self.assertEqual(json.loads(error.exception.read())['code'], 'PROJECT_LOCKED')

    def test_projects_are_isolated_for_both_header_and_query_selection(self):
        self.add('shanghai', '主项目专属安排')
        self.json('/api/admin/session', 'POST', {'password': TEST_ADMIN_PASSWORD})
        project = self.json('/api/admin/projects', 'POST', {'name': '第二项目', 'project_code': 'second'})['project']['id']
        for fmt in ('docx', 'xlsx'):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.download(f'format={fmt}&scope=all&project={project}')
            self.assertEqual(error.exception.code, 401)
        self.json(f'/api/project-session?project={project}', 'POST', {'project_code': 'second'})
        self.json(f'/api/session?project={project}', 'POST', {'user_id': '第二项目用户'})
        self.add('beijing', '第二项目专属安排', project=project)
        for fmt in ('docx', 'xlsx'):
            for query, headers in [(f'format={fmt}&scope=all&project={project}', {}),
                                   (f'format={fmt}&scope=all', {'X-Trip-Project': project})]:
                value = content(self.download(query, headers=headers)[1])
                self.assertIn('第二项目专属安排', value)
                self.assertNotIn('主项目专属安排', value)
            self.assertNotIn('第二项目专属安排', content(self.download(f'format={fmt}&scope=all')[1]))


if __name__ == '__main__':
    unittest.main()
