import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import server
from project_itinerary import overview


class ProjectItineraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'trip.db'
        seed = Path(self.temp.name) / 'empty.json'
        seed.write_text('{}')
        server.init_database(self.path, 'test-code', seed)
        server.claim_identity(self.path, {'user_id': 'tester'})

    def add(self, city, date='2026-10-01', time='09:00'):
        return server.create_item(self.path, 'itinerary', {
            'city_id': city, 'date': date, 'start_time': time, 'title': '集合',
        }, 'tester')['item']

    def plan(self, city='shanghai'):
        return server.snapshot(self.path, city)['project_itinerary']

    def test_catalog_and_cached_places_do_not_count_as_planned_cities(self):
        self.assertEqual(self.plan(), {'cities': [], 'conflicts': []})
        self.add('beijing', '2026-10-03')
        self.add('shanghai', '2026-10-01')
        self.add('shanghai', '2026-10-02')
        plan = self.plan()
        self.assertEqual([c['city_id'] for c in plan['cities']], ['shanghai', 'beijing'])
        self.assertEqual(plan['cities'][0]['count'], 2)
        self.assertEqual(plan['cities'][0]['dates'], ['2026-10-01', '2026-10-02'])
        self.assertEqual(plan['conflicts'], [])
        self.assertEqual(plan, self.plan('beijing'))
        self.assertEqual(len(server.snapshot(self.path, 'shanghai')['items']['itinerary']), 2)

    def test_cross_city_same_day_is_advisory_and_missing_times_are_visible(self):
        first = self.add('shanghai')
        self.add('shanghai', time='10:00')
        self.assertEqual(self.plan()['conflicts'], [])
        second = self.add('beijing', time='')
        conflict = self.plan()['conflicts'][0]
        self.assertEqual(conflict['date'], '2026-10-01')
        self.assertEqual(len(conflict['items']), 3)
        self.assertEqual(conflict['items'][-1]['start_time'], '')
        # Advisory generation leaves saved dates, versions, and revisions intact.
        before = server.snapshot(self.path)
        self.plan()
        after = server.snapshot(self.path)
        self.assertEqual(before['items'], after['items'])
        self.assertEqual(before['revision'], after['revision'])
        self.assertEqual(first['date'], second['date'])

    def test_edit_and_delete_remove_conflicts_and_last_city(self):
        self.add('shanghai')
        second = self.add('beijing')
        self.assertEqual(len(self.plan()['conflicts']), 1)
        updated = server.update_item(self.path, 'itinerary', second['id'], {
            **second, 'date': '2026-10-02',
        }, 'tester')['item']
        self.assertEqual(self.plan()['conflicts'], [])
        server.delete_item(self.path, 'itinerary', second['id'], {'version': updated['version']}, 'tester')
        self.assertEqual([c['city_id'] for c in self.plan()['cities']], ['shanghai'])

    def test_other_project_has_no_itinerary_from_this_project(self):
        self.add('shanghai')
        self.add('beijing')
        other = Path(self.temp.name) / 'other.db'
        server.init_database(other, 'other-code', Path(self.temp.name) / 'empty.json')
        self.assertEqual(server.snapshot(other, 'shanghai')['project_itinerary'], {'cities': [], 'conflicts': []})

    def test_summary_is_stable_regardless_of_database_row_order(self):
        rows = [{'id': city, 'version': 1, 'city_id': city, 'payload': json.dumps({
            'date': '2026-10-01', 'title': '<script>test</script>', 'start_time': '09:00',
        })} for city in ['beijing', 'shanghai']]
        names = {'beijing': '北京', 'shanghai': '上海'}
        self.assertEqual(overview(rows, names), overview(list(reversed(rows)), names))

    def city_version(self, city='shanghai'):
        return next(c['version'] for c in self.plan()['cities'] if c['city_id'] == city)

    def change(self, start='2026-10-05', city='shanghai', version=None, delete=False, **extra):
        return server.change_city_itinerary(self.path, {
            'city_id': city, 'version': version or self.city_version(city),
            'start_date': start, 'confirm_delete': delete, **extra,
        }, 'tester', delete=delete)

    def test_shift_preserves_gaps_time_content_ids_and_links(self):
        first = self.add('shanghai', '2026-10-01', '09:00')
        self.add('shanghai', '2026-10-01', '14:00')
        self.add('shanghai', '2026-10-03', '')
        self.add('beijing', '2026-10-05')
        before = server.snapshot(self.path)
        result = self.change()
        self.assertEqual(result['offset_days'], 4)
        self.assertEqual(result['changed'], 3)
        after = server.snapshot(self.path)
        old = {item['id']: item for item in before['items']['itinerary']}
        new = {item['id']: item for item in after['items']['itinerary']}
        self.assertEqual(old.keys(), new.keys())
        for key, item in new.items():
            original = old[key]
            if item['city_id'] == 'beijing':
                self.assertEqual(item, original)
            else:
                self.assertEqual(item['version'], original['version'] + 1)
                for field in ('start_time', 'title', 'notes', 'location', 'linked_attractions'):
                    self.assertEqual(item[field], original[field])
        self.assertEqual(new[first['id']]['date'], '2026-10-05')
        self.assertEqual(next(c['dates'] for c in self.plan()['cities'] if c['city_id']=='beijing'), ['2026-10-05'])
        self.assertEqual(next(c['dates'] for c in self.plan()['cities'] if c['city_id']=='shanghai'), ['2026-10-05', '2026-10-07'])
        self.assertEqual(len(self.plan()['conflicts']), 1, 'cross-city conflicts are advisory')
        self.assertEqual(before['items']['attraction'], after['items']['attraction'])
        self.assertEqual(len([a for a in after['activity'] if a['action']=='update']), 3)

    def test_shift_supports_year_boundaries_leap_days_and_noop(self):
        self.add('shanghai', '2026-12-31')
        self.add('shanghai', '2027-01-02')
        before = server.snapshot(self.path)
        self.assertEqual(self.change('2026-12-31')['changed'], 0)
        self.assertEqual(before['revision'], server.snapshot(self.path)['revision'])
        self.change('2028-02-28')
        self.assertEqual(self.plan()['cities'][0]['dates'], ['2028-02-28', '2028-03-01'])

    def test_invalid_dates_and_overflow_roll_back_all_changes(self):
        self.add('shanghai', '2026-10-01')
        self.add('shanghai', '2026-10-03')
        before = server.snapshot(self.path)
        for date in ('2026-02-30', '2026-1-1', '', None, '0000-01-01', '9999-12-31'):
            with self.assertRaises(server.ApiError) as error:
                self.change(date)
            self.assertEqual(error.exception.status, 400)
            after = server.snapshot(self.path)
            self.assertEqual(after['items'], before['items'])
            self.assertEqual(after['revision'], before['revision'])

    def test_delete_keeps_places_food_city_catalog_and_other_cities(self):
        self.add('shanghai')
        self.add('shanghai', '2026-10-02')
        self.add('beijing')
        server.create_item(self.path, 'attraction', {'city_id':'shanghai', 'name':'外滩'}, 'tester')
        server.create_item(self.path, 'food', {'city_id':'shanghai', 'name':'生煎'}, 'tester')
        before = server.snapshot(self.path)
        self.assertEqual(self.change(delete=True)['changed'], 2)
        after = server.snapshot(self.path)
        self.assertEqual([c['city_id'] for c in self.plan()['cities']], ['beijing'])
        self.assertEqual(self.plan()['conflicts'], [])
        for kind in ('attraction', 'food', 'transit'):
            self.assertEqual(after['items'][kind], before['items'][kind])
        self.assertEqual(after['cities'], before['cities'])
        self.assertEqual(after['items']['itinerary'], [i for i in before['items']['itinerary'] if i['city_id']=='beijing'])
        self.add('shanghai')
        self.assertEqual(len(self.plan()['cities']), 2)

    def test_stale_editor_cannot_shift_or_delete_and_other_city_edits_are_allowed(self):
        self.add('shanghai')
        version = self.city_version()
        self.add('beijing')
        self.change(version=version)  # unrelated city does not invalidate editor
        stale = self.city_version()
        self.add('shanghai', '2026-10-09')
        before = server.snapshot(self.path)
        for delete in (False, True):
            with self.assertRaises(server.ApiError) as error:
                self.change(version=stale, delete=delete)
            self.assertEqual(error.exception.status, 409)
        after = server.snapshot(self.path)
        self.assertEqual(before['items'], after['items'])
        self.assertEqual(before['revision'], after['revision'])

    def test_delete_requires_explicit_confirmation_and_last_city_can_be_removed(self):
        self.add('shanghai')
        with self.assertRaises(server.ApiError) as error:
            self.change(delete=True, confirm_delete=False)
        self.assertEqual(error.exception.status, 400)
        version = self.city_version()
        self.change(delete=True)
        self.assertEqual(self.plan(), {'cities': [], 'conflicts': []})
        with self.assertRaises(server.ApiError) as error:
            self.change(version=version, delete=True)
        self.assertEqual(error.exception.status, 409)

    def test_partial_database_failure_rolls_back_shift_and_delete(self):
        self.add('shanghai')
        self.add('shanghai', '2026-10-03')
        before = server.snapshot(self.path)
        original = server.next_revision
        for delete in (False, True):
            calls = []
            def failing(db):
                calls.append(1)
                if len(calls) == 2:
                    raise RuntimeError('simulated write failure')
                return original(db)
            with patch('server.next_revision', side_effect=failing), self.assertRaises(RuntimeError):
                self.change(delete=delete)
            after = server.snapshot(self.path)
            for key in ('items', 'activity', 'revision', 'project_itinerary'):
                self.assertEqual(before[key], after[key])

    def test_linked_attraction_dates_follow_city_shift_and_clear_after_removal(self):
        server.create_item(self.path, 'itinerary', {'city_id':'shanghai', 'date':'2026-10-01',
            'title':'游览', 'attraction_names':['外滩']}, 'tester')
        attraction = server.snapshot(self.path, 'shanghai')['items']['attraction'][0]
        self.assertEqual(attraction['itinerary_refs'][0]['date'], '2026-10-01')
        self.change('2026-10-05')
        shifted = server.snapshot(self.path, 'shanghai')['items']['attraction'][0]
        self.assertEqual(shifted['id'], attraction['id'])
        self.assertEqual(shifted['itinerary_refs'][0]['date'], '2026-10-05')
        self.change(delete=True)
        remaining = server.snapshot(self.path, 'shanghai')['items']['attraction'][0]
        self.assertEqual(remaining['id'], attraction['id'])
        self.assertEqual(remaining['itinerary_refs'], [])
