import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import place_cache
import server
from project_store import ProjectStore


class SharedProjectPlacesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'trip.db'
        self.seed = Path(self.temp.name) / 'empty.json'
        self.seed.write_text('{}')
        server.init_database(self.path, 'first-code', self.seed)
        server.claim_identity(self.path, {'user_id': 'first-user'})
        self.store = ProjectStore(self.path, server.init_database)

    def tearDown(self):
        self.temp.cleanup()

    def new_project(self):
        project = self.store.create('下一次旅行', 'second-code')
        path = self.store.resolve(project['id'])
        server.claim_identity(path, {'user_id': 'second-user'})
        return path

    def add(self, kind='food', name='项目共享美食'):
        return server.create_item(self.path, kind, {'name': name, 'city_id': 'beijing'}, 'first-user')['item']

    def test_cached_places_are_independent_copies_and_itineraries_are_private(self):
        food = self.add()
        self.add('attraction', '公园')
        server.create_item(self.path, 'itinerary', {'city_id': 'beijing', 'date': '2026-10-01', 'title': '我的安排'}, 'first-user')
        child = self.new_project()
        result = server.snapshot(child, 'beijing')
        self.assertEqual([x['name'] for x in result['items']['food']], ['项目共享美食'])
        self.assertEqual(len(result['items']['attraction']), 1)
        self.assertEqual(result['items']['itinerary'], [])
        self.assertNotEqual(result['items']['food'][0]['id'], food['id'])
        self.assertEqual(result['items']['food'][0]['created_by'], '城市资料库')
        server.update_item(self.path, 'food', food['id'], {'name': food['name'], 'description': '更新后的介绍', 'version': food['version']}, 'first-user')
        self.assertEqual(server.snapshot(child, 'beijing')['items']['food'][0]['description'], '')
        newest = self.new_project()
        self.assertEqual(server.snapshot(newest, 'beijing')['items']['food'][0]['description'], '更新后的介绍')

    def test_batch_delete_stays_deleted_after_refresh_restart_but_next_project_inherits(self):
        food = self.add()
        server.batch_delete_items(self.path, 'food', {'city_id': 'beijing', 'items': [{'id': food['id'], 'version': food['version']}]}, 'first-user')
        server.init_database(self.path, 'ignored', self.seed)
        self.assertEqual(server.snapshot(self.path, 'beijing')['items']['food'], [])
        child = self.new_project()
        copy = server.snapshot(child, 'beijing')['items']['food'][0]
        server.delete_item(child, 'food', copy['id'], {'version': copy['version']}, 'second-user')
        self.assertEqual(server.snapshot(child, 'beijing')['items']['food'], [])
        self.assertEqual(len(place_cache.load_city(child, '北京市')), 1)

    def test_ai_import_is_cached_and_failed_import_is_not(self):
        result = server.import_ai_items(self.path, 'beijing', 'first-user', [{'kind': 'attraction', 'data': {'name': 'AI景点'}}], 'good-job')
        self.assertEqual(result['created'], 1)
        capacity = server.ensure_item_capacity
        count = 0
        def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise server.ApiError(507, 'quota')
            return capacity(*args, **kwargs)
        with patch.object(server, 'ensure_item_capacity', side_effect=fail_second):
            with self.assertRaises(server.ApiError):
                server.import_ai_items(self.path, 'beijing', 'first-user', [{'kind': 'food', 'data': {'name': '不应缓存一'}}, {'kind': 'food', 'data': {'name': '不应缓存二'}}], 'bad-job')
        self.assertEqual([x['payload']['name'] for x in place_cache.load_city(self.path, 'beijing')], [])
        self.assertEqual([x['payload']['name'] for x in place_cache.load_city(self.path, '北京')], ['AI景点'])

    def test_cache_failure_preserves_committed_write_and_can_retry(self):
        with patch.object(place_cache, 'flush', side_effect=sqlite3.OperationalError('cache busy')):
            food = self.add()
        with server.connect_db(self.path) as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM items WHERE id=?', (food['id'],)).fetchone())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cache_outbox').fetchone()[0], 1)
        place_cache.flush(self.path)
        self.assertEqual(place_cache.load_city(self.path, '北京')[0]['payload']['name'], food['name'])

    def test_curated_migration_archives_removed_data_and_preserves_places_in_cache(self):
        with server.connect_db(self.path) as db:
            db.execute("DELETE FROM place_cache_state WHERE key='backfilled_v1'")
            db.execute("DELETE FROM meta WHERE key='curated_cities_v2'")
            db.execute("INSERT INTO cities VALUES('hangzhou','杭州',100,'old','old')")
            db.execute("INSERT INTO items VALUES('old-place','hangzhou','attraction',1,?,1,'old','old','first-user','first-user')", (json.dumps({'name':'西湖'}),))
            db.execute("INSERT INTO items VALUES('old-plan','hangzhou','itinerary',1,?,1,'old','old','first-user','first-user')", (json.dumps({'title':'旧行程','date':'2026-10-01'}),))
        server.init_database(self.path, 'ignored', self.seed)
        self.assertEqual(len(server.snapshot(self.path)['cities']), 14)
        with server.connect_db(self.path) as db:
            archived = json.loads(db.execute("SELECT data FROM archived_cities WHERE id='hangzhou'").fetchone()[0])
            self.assertEqual(len(archived['items']), 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM items WHERE city_id='hangzhou'").fetchone()[0], 0)
        self.assertEqual(place_cache.load_city(self.path, '杭州')[0]['payload']['name'], '西湖')
        city = server.create_city(self.path, {'name':'杭州'}, 'first-user')['city']
        self.assertEqual(server.snapshot(self.path, city['id'])['items']['attraction'][0]['name'], '西湖')
        server.init_database(self.path, 'ignored', self.seed)
        self.assertIn('杭州', [x['name'] for x in server.snapshot(self.path)['cities']])

    def test_hydration_quota_keeps_city_accessible_and_reports_remaining_cache(self):
        self.add('food','第一道')
        self.add('food','第二道')
        child = self.new_project()
        with patch.object(server, 'MAX_ITEMS_PER_KIND', 1):
            result = server.snapshot(child, 'beijing')
        self.assertEqual(len(result['items']['food']), 1)
        self.assertEqual(result['cache_info']['omitted']['food'], 1)
        with server.connect_db(child) as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM city_cache_imports WHERE city_id='beijing'").fetchone())
        self.assertEqual(len(place_cache.load_city(child, '北京')), 2)
        self.assertEqual(len(server.snapshot(child, 'beijing')['items']['food']), 1)


if __name__ == '__main__':
    unittest.main()
