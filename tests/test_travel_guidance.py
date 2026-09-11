"""Imported guidance follows saved content, survives job cleanup and exports."""
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server
import travel_guidance
from test_replan_integration import LocalPlanService
from test_itinerary_export import content, xml_parts
from itinerary_export import build_docx, build_xlsx, SHEET


class GuidanceService(LocalPlanService):
    def _generate(self, request, context):
        response, mode = super()._generate(request, context)
        output = response['output'][0]['content'][0]
        value = json.loads(output['text'])
        value['summary'] = '本计划为建议性安排，出行前请确认开放信息。'
        value['notices'] = ['热门场馆请提前预约。', '准备室内备选方案。', '=1+1 <原文> & 保留']
        output['text'] = json.dumps(value)
        return response, mode


class TravelGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'trip.db'
        self.seed = Path(self.temp.name) / 'empty.json'
        self.seed.write_text('{}')
        server.init_database(self.path, 'test-code', self.seed)
        self.user = '提示测试'
        server.claim_identity(self.path, {'user_id': self.user})
        self.service = GuidanceService(self.path, api_key='mock-only', cooldown_seconds=0,
            get_context=lambda city: server.ai_context(self.path, city),
            normalize_item=server.validate_payload,
            import_items=lambda city, user, items, job: server.import_ai_items(self.path, city, user, items, job))

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def ready(self, city='shanghai', mode='append', start='2026-10-01', days=2):
        request = dict(city_id=city, kinds=['itinerary'], planning_mode=mode, start_date=start, days=days, people=1)
        if mode == 'replace_day':
            request['target_date'] = start
        job = self.service.create_job(self.user, 'device', request)
        for _ in range(300):
            job = self.service.get_job(self.user, 'device', job['id'])
            if job['status'] not in ('queued', 'running'):
                self.assertEqual(job['status'], 'ready', job)
                return job
            time.sleep(.01)
        self.fail('Mock generation timed out')

    def apply(self, job):
        return self.service.import_job(self.user, 'device', job['id'], {
            'confirm_replace': True, 'items': [{'kind': 'itinerary', 'data': item} for item in job['result']['itineraries']]})

    def guidance(self, city='shanghai'):
        return server.snapshot(self.path, city)['travel_guidance']

    def test_import_never_creates_guidance_even_if_provider_returns_notices(self):
        job = self.ready()
        self.assertEqual(job['result']['notices'], [])
        self.apply(job)
        self.assertEqual(self.guidance(), [])
        self.apply(job)
        server.init_database(self.path, 'test-code', self.seed)
        self.assertEqual(self.guidance(), [])

    def test_replanning_preserves_explicitly_saved_tips(self):
        self.apply(self.ready()); self.edit(notices=['已确认提示'])
        before = self.guidance()
        self.apply(self.ready(mode='replace_day', days=1))
        self.apply(self.ready(mode='replace_all', start='2026-10-04', days=1))
        self.assertEqual(self.guidance(), before)

    def test_failure_rolls_back_tips_and_all_items(self):
        job = self.ready()
        before = server.snapshot(self.path, 'shanghai')
        with patch('server.ensure_item_capacity', side_effect=server.ApiError(409, 'capacity')):
            with self.assertRaises(server.ApiError):
                self.apply(job)
        after = server.snapshot(self.path, 'shanghai')
        for key in ('items', 'travel_guidance', 'revision'):
            self.assertEqual(before[key], after[key])

    def test_old_import_receipts_recover_once_but_unimported_drafts_do_not(self):
        job = self.ready(); self.apply(job)
        self.ready(city='beijing')
        with server.connect_db(self.path) as db:
            db.execute('DELETE FROM travel_guidance')
            db.execute("DELETE FROM meta WHERE key='travel_guidance_v1'")
        server.init_database(self.path, 'test-code', self.seed)
        self.assertEqual(self.guidance(), [])
        self.assertEqual(self.guidance('beijing'), [])
        before = self.guidance()
        server.init_database(self.path, 'test-code', self.seed)
        self.assertEqual(before, self.guidance())

    def test_duplicate_only_import_does_not_create_guidance(self):
        self.apply(self.ready())
        before = server.snapshot(self.path, 'shanghai')
        result = self.apply(self.ready())
        self.assertEqual(result['created'], 0)
        after = server.snapshot(self.path, 'shanghai')
        self.assertEqual(after['revision'], before['revision'])
        self.assertEqual(after['travel_guidance'], [])
        self.assertEqual(after['items'], before['items'])

    def edit(self, summary='自己写的提示', notices=None, version=None, delete=False):
        current = server.guidance_state(self.path, 'shanghai')
        return server.change_guidance(self.path, {'city_id': 'shanghai',
            'version': version or current['version'], 'summary': summary,
            'notices': ['记得带相机'] if notices is None else notices}, self.user, delete=delete)

    def test_manual_edit_survives_ai_import_restart_and_updates_export(self):
        self.apply(self.ready())
        before = server.snapshot(self.path, 'shanghai')
        result = self.edit(notices=[' 带相机 ', '带相机', ''])
        self.assertGreater(result['revision'], before['revision'])
        self.assertEqual(self.guidance()[0]['notices'], ['带相机'])
        self.assertEqual(server.snapshot(self.path, 'shanghai')['items'], before['items'])
        self.apply(self.ready(mode='replace_all', start='2026-10-04', days=1))
        self.assertEqual(self.guidance()[0]['version'], result['guidance']['version'])
        server.init_database(self.path, 'test-code', self.seed)
        self.assertEqual(self.guidance()[0]['summary'], '')
        self.assertIn('带相机', content(build_docx(server.itinerary_export_snapshot(self.path, 'shanghai'))))

    def test_delete_stays_deleted_through_new_plans_and_can_be_added_again(self):
        first = self.ready(); self.apply(first)
        before = server.snapshot(self.path, 'shanghai')['items']
        self.edit(delete=True)
        self.assertEqual(self.guidance(), [])
        self.assertEqual(server.snapshot(self.path, 'shanghai')['items'], before)
        self.apply(first)
        self.apply(self.ready())
        server.init_database(self.path, 'test-code', self.seed)
        self.assertEqual(self.guidance(), [])
        self.assertEqual(server.itinerary_export_snapshot(self.path, 'shanghai')['travel_guidance'], [])
        self.edit(notices=['重新添加的出行建议'])
        self.assertEqual(self.guidance()[0]['notices'], ['重新添加的出行建议'])

    def test_conflict_validation_and_city_isolation(self):
        self.apply(self.ready())
        old = server.guidance_state(self.path, 'shanghai')['version']
        self.edit()
        for deleting in (False, True):
            with self.assertRaises(server.ApiError) as error:
                self.edit(version=old, delete=deleting)
            self.assertEqual(error.exception.status, 409)
        for summary, notices in [('x'*1001, []), ('', ['x']*13), ('', ['x'*501]), ('', [2]), ('', [])]:
            with self.assertRaises(server.ApiError) as error:
                self.edit(summary=summary, notices=notices)
            self.assertEqual(error.exception.status, 400)
        self.assertEqual(self.guidance('beijing'), [])

    def test_ai_import_does_not_invalidate_guidance_editor(self):
        self.apply(self.ready())
        old = server.guidance_state(self.path, 'shanghai')['version']
        self.apply(self.ready())
        self.edit(version=old)
        self.assertEqual(self.guidance()[0]['notices'], ['记得带相机'])

    def test_exports_include_scoped_tips_and_keep_formula_like_text_literal(self):
        shanghai = self.ready(); self.apply(shanghai)
        beijing = self.ready(city='beijing'); self.apply(beijing)
        self.edit(notices=['外滩步行建议', '=1+1 <原文> & 保留'])
        current=server.guidance_state(self.path,'beijing')
        server.change_guidance(self.path, {'city_id':'beijing','version':current['version'],'notices':['北京专属提示']}, self.user)
        data = server.itinerary_export_snapshot(self.path, 'shanghai')
        for build in (build_docx, build_xlsx):
            values = content(build(data))
            self.assertIn('出行提示', values)
            self.assertNotIn(shanghai['result']['summary'], values)
            for notice in self.guidance()[0]['notices']:
                self.assertIn(notice, values)
            self.assertNotIn('北京专属提示', values)
            self.assertNotIn(self.user, values)
            self.assertIn('北京专属提示', content(build(server.itinerary_export_snapshot(self.path, None))))
        parts = xml_parts(build_xlsx(data))
        ns = {'s': SHEET}
        self.assertEqual([sheet.attrib['name'] for sheet in parts['xl/workbook.xml'].find('s:sheets', ns)],
                         ['行程明细', '导出说明', '出行提示'])
        self.assertEqual(parts['xl/worksheets/sheet3.xml'].findall('.//s:f', ns), [])


class GuidanceMigrationTests(unittest.TestCase):
    def test_legacy_day_and_food_tips_are_excluded_and_only_city_advice_remains(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.executescript('''CREATE TABLE cities(id TEXT PRIMARY KEY,name TEXT,position INTEGER);
            CREATE TABLE items(id TEXT PRIMARY KEY,city_id TEXT,kind TEXT,payload TEXT);
            CREATE TABLE ai_jobs(id TEXT PRIMARY KEY,request_json TEXT);
            CREATE TABLE travel_guidance(id TEXT PRIMARY KEY,city_id TEXT,summary TEXT,notices TEXT,created_at TEXT);
            CREATE TABLE travel_guidance_items(guidance_id TEXT,item_id TEXT,PRIMARY KEY(guidance_id,item_id));
            INSERT INTO cities VALUES('shanghai','上海',1);
            INSERT INTO items VALUES('i','shanghai','itinerary','{"date":"2026-10-01"}');
            INSERT INTO items VALUES('f','shanghai','food','{"name":"小笼包"}');''')
        for index, (job_id, mode, kinds, item) in enumerate([
                ('city', 'replace_all', ['itinerary'], 'i'),
                ('day', 'replace_day', ['itinerary'], 'i'),
                ('food', 'append', ['food'], 'f')]):
            db.execute('INSERT INTO ai_jobs VALUES(?,?)', (job_id, json.dumps({'planning_mode': mode, 'kinds': kinds})))
            db.execute('INSERT INTO travel_guidance VALUES(?,?,?,?,?)', (job_id, 'shanghai', '不应显示的说明', json.dumps([job_id+'建议']), str(index)))
            db.execute('INSERT INTO travel_guidance_items VALUES(?,?)', (job_id, item))
        travel_guidance.ensure_schema(db)
        groups = travel_guidance.read(db)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['notices'], ['city建议'])
        self.assertEqual(groups[0]['summary'], '')
        self.assertEqual(db.execute('SELECT COUNT(*) FROM travel_guidance').fetchone()[0], 3)
        travel_guidance.ensure_schema(db)
        self.assertEqual(travel_guidance.read(db), groups)
        db.execute("UPDATE travel_guidance SET notices='[]' WHERE id='city'")
        self.assertEqual(travel_guidance.read(db), [], 'summary alone is not a travel tip')


if __name__ == '__main__':
    unittest.main()
