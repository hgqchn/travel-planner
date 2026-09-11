import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server
import daily_plan_store as store


class DayLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'trip.db'
        server.init_database(self.path, 'test-code', empty_project=True)
        self.user = 'day-tester'
        server.claim_identity(self.path, {'user_id': self.user})
        self.base = {'city_id': 'shanghai', 'date': '2030-01-01'}

    def tearDown(self):
        self.temp.cleanup()

    def read(self):
        return store.get(server, self.path, **self.base)

    def create(self):
        return store.change_day(server, self.path, self.base, self.user)

    def delete(self, plan):
        return store.change_day(server, self.path, {**self.base, 'version': plan['version']}, self.user, delete=True)

    def save(self, plan, **changes):
        return store.save(server, self.path, {**self.base, 'version': plan['version'], **changes}, self.user)

    def test_empty_day_is_persistent_and_duplicate_create_preserves_settings(self):
        plan = self.create()
        self.assertEqual(plan['visits'], [])
        plan = self.save(plan, settings={'slack_target': 90})
        with self.assertRaises(server.ApiError) as err:
            self.create()
        self.assertEqual(err.exception.status, 409)
        server.init_database(self.path, 'ignored', empty_project=True)
        self.assertEqual(self.read()['settings']['slack_target'], 90)
        self.assertEqual([p['date'] for p in server.snapshot(self.path)['daily_plans']], ['2030-01-01'])

    def test_locked_time_block_requires_separate_unlock_through_both_save_paths(self):
        plan = self.save(self.create(), additions=[{'title':'公园', 'time_block':'morning', 'block_locked':True}])
        for endpoint in ('item', 'day'):
            with self.subTest(endpoint=endpoint):
                plan = self.read()
                visit = plan['visits'][0]
                def update(changes):
                    current = self.read()
                    if endpoint == 'day':
                        return self.save(current, updates=[{'id':visit['id'], 'changes':changes}])
                    item = current['visits'][0]
                    return server.update_item(self.path, 'itinerary', visit['id'], {**item, **changes}, self.user)
                for changes in ({'time_block':'afternoon'}, {'time_block':''},
                                {'time_block':'afternoon', 'block_locked':False}):
                    before = self.read()
                    with self.assertRaises(server.ApiError) as err:
                        update(changes)
                    self.assertEqual(err.exception.status, 400)
                    self.assertIn('解除时段锁定', str(err.exception))
                    self.assertEqual(self.read(), before)
                update({'notes':'保留备注', 'duration_minutes':95})
                self.assertTrue(self.read()['visits'][0]['block_locked'])
                self.assertEqual(self.read()['visits'][0]['duration_minutes'], 95)
                update({'block_locked':False})
                update({'time_block':'afternoon'})
                self.assertEqual(self.read()['visits'][0]['time_block'], 'afternoon')
                update({'time_block':'morning', 'block_locked':True})

    def test_existing_itinerary_also_counts_as_a_day(self):
        server.create_item(self.path, 'itinerary', {**self.base, 'title': '已有行程'}, self.user)
        with self.assertRaises(server.ApiError) as err:
            self.create()
        self.assertEqual(err.exception.status, 409)
        self.delete(self.read())
        self.assertEqual(self.read()['visits'], [])

    def test_delete_removes_visits_settings_and_evaluation_but_keeps_places_and_other_days(self):
        plan = self.save(self.create(), additions=[{'title':'人民公园', 'attraction_names':['人民公园']},
                                                {'title':'备选活动', 'is_backup':True}])
        server.create_item(self.path, 'food', {'city_id':'shanghai', 'name':'小笼包'}, self.user)
        store.change_day(server, self.path, {**self.base, 'date':'2030-01-02'}, self.user)
        store.change_day(server, self.path, {**self.base, 'city_id':'beijing'}, self.user)
        with server.connect_db(self.path) as db:
            db.execute('INSERT INTO day_evaluations VALUES(?,?,?,?,?)', ('shanghai','2030-01-01',plan['version'],0,'{}'))
        before = server.snapshot(self.path)
        result = self.delete(plan)
        self.assertEqual(result['deleted_count'], 2)
        after = server.snapshot(self.path)
        self.assertEqual({(p['city_id'],p['date']) for p in after['daily_plans']},
                         {('shanghai','2030-01-02'),('beijing','2030-01-01')})
        for kind in ('attraction','food'):
            self.assertEqual([p['id'] for p in before['items'][kind]], [p['id'] for p in after['items'][kind]])
        with server.connect_db(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM day_evaluations').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM itinerary_attractions').fetchone()[0], 0)
        self.assertEqual(after['activity'][0]['action'], 'delete')
        self.assertEqual(after['activity'][0]['user_id'], self.user)

    def test_concurrent_edit_blocks_delete_and_failed_delete_rolls_back(self):
        old = self.create()
        fresh = self.save(old, additions=[{'title':'新安排'}])
        with self.assertRaises(server.ApiError) as err:
            self.delete(old)
        self.assertEqual(err.exception.status, 409)
        with patch.object(server, 'next_revision', side_effect=RuntimeError('simulated write failure')):
            with self.assertRaises(RuntimeError):
                self.delete(fresh)
        self.assertEqual(self.read(), fresh)

    def test_delete_and_recreate_invalidates_old_empty_day_drafts(self):
        old = self.create()
        self.delete(old)
        recreated = self.create()
        self.assertNotEqual(old['version'], recreated['version'])
        for action in (lambda: self.delete(old), lambda: self.save(old, settings={'slack_target':90})):
            with self.assertRaises(server.ApiError) as err:
                action()
            self.assertEqual(err.exception.status, 409)
        self.assertEqual(self.read()['version'], recreated['version'])

    def test_invalid_requests_and_missing_day_never_write(self):
        for data in ([], {}, {**self.base, 'date':'2030-02-30'}, {**self.base, 'settings':{}}):
            with self.assertRaises(server.ApiError) as err:
                store.change_day(server, self.path, data, self.user)
            self.assertEqual(err.exception.status, 400)
        with self.assertRaises(server.ApiError) as err:
            self.delete(self.read())
        self.assertEqual(err.exception.status, 404)
        self.assertEqual(server.snapshot(self.path)['daily_plans'], [])
