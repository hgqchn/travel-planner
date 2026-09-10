from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import ai_service as ai
import place_cache
import server
from place_taxonomy import CATEGORIES, TAXONOMY, migrate_project, normalize_place


class PlaceTaxonomyTests(unittest.TestCase):
    def test_legacy_classification_uses_place_or_food_not_a_broad_label(self):
        cases = [
            ('attraction', '颐和园', '皇家园林', '园林公园'),
            ('attraction', '天坛公园', '皇家祭坛', '历史古迹'),
            ('attraction', '故宫博物院', '历史文化', '历史古迹'),
            ('attraction', '龙华寺', '历史文化', '寺庙宗教'),
            ('attraction', '上海天文馆', '亲子', '博物展馆'),
            ('attraction', '上海动物园', '亲子', '动植物园'),
            ('attraction', '上海佘山国家森林公园', '自然休闲', '自然风光'),
            ('food', '小馄饨', '早餐', '面点饼类'),
            ('food', '粢饭团', '早餐', '米饭粥类'),
            ('food', '黄鱼面', '面点/河鲜面', '面条米粉'),
            ('food', '鲜肉月饼', '点心', '糕点甜品'),
            ('food', '哈达饼', '传统点心', '糕点甜品'),
        ]
        for kind, name, old, expected in cases:
            with self.subTest(name=name):
                item = normalize_place(kind, {'name': name, 'category': old}, legacy=True)
                self.assertEqual(item['category'], expected)
                self.assertIsInstance(item['tags'], list)
        self.assertEqual(normalize_place('attraction', {'name': '待确认', 'category': '亲子'}, legacy=True)['category'], '')

    def test_cuisine_preserves_explicit_old_cuisine_without_inventing_one(self):
        item = normalize_place('food', {'name': '四喜烤麸', 'category': '本帮冷菜'}, legacy=True)
        self.assertEqual((item['category'], item['cuisine']), ('特色菜肴', '本帮菜'))
        self.assertIn('本帮菜', item['tags'])
        unknown = normalize_place('food', {'name': '特色炒菜', 'category': '特色菜肴'}, legacy=True)
        self.assertEqual(unknown['cuisine'], '')

    def test_manual_choices_are_preserved_and_unknown_categories_rejected(self):
        item = server.validate_payload('attraction', {'name': '历史公园', 'category': '历史古迹', 'tags': ['皇家', '皇家', ' Ｃｉｔｙｗａｌｋ ', 'citywalk']})
        self.assertEqual(item['category'], '历史古迹')
        self.assertEqual(item['tags'], ['皇家', 'Citywalk'])
        for tags in ('皇家', [1], ['a' * 25], ['a'] * 31):
            with self.subTest(tags=tags), self.assertRaises(server.ApiError):
                server.validate_payload('food', {'name': '测试', 'tags': tags})
        with self.assertRaises(server.ApiError):
            server.validate_payload('attraction', {'name': '测试公园', 'category': 'AI随意生成的新类别'})

    def test_migration_preserves_originals_versions_and_runs_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trip.db'
            server.init_database(path, 'test')
            with server.connect_db(path) as db:
                db.execute("DELETE FROM meta WHERE key='place_taxonomy_v1'")
                row = db.execute("SELECT id,version FROM items WHERE kind='attraction' LIMIT 1").fetchone()
                original = json.dumps({'name': '天坛公园', 'category': '皇家祭坛'}, ensure_ascii=False)
                db.execute('UPDATE items SET payload=? WHERE id=?', (original, row['id']))
                revision = db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
                self.assertEqual(migrate_project(db, '2026-09-10T00:00:00Z'), 1)
                updated = db.execute('SELECT payload,version FROM items WHERE id=?', (row['id'],)).fetchone()
                self.assertEqual(json.loads(updated['payload'])['category'], '历史古迹')
                self.assertEqual(json.loads(updated['payload'])['tags'], ['皇家', '历史文化'])
                self.assertEqual(updated['version'], row['version'] + 1)
                self.assertEqual(db.execute('SELECT payload FROM place_taxonomy_history WHERE item_id=?', (row['id'],)).fetchone()[0], original)
                self.assertEqual(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0], revision + 1)
                self.assertEqual(migrate_project(db, 'later'), 0)

    def test_tags_cuisine_snapshot_and_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trip.db'
            server.init_database(path, 'test')
            value = server.validate_payload('food', {'name': '测试菜肴', 'category': '特色菜肴', 'cuisine': '川菜', 'tags': ['麻辣', '适合分享']})
            with server.connect_db(path) as db:
                place_cache.enqueue(db, '上海', 'food', value, '2026-09-10T00:00:00Z')
            places = place_cache.load_city(path, '上海')
            saved = next(entry['payload'] for entry in places if entry['payload']['name'] == '测试菜肴')
            self.assertEqual(saved['tags'], value['tags'])
            self.assertEqual(saved['cuisine'], '川菜')
            self.assertEqual(server.snapshot(path)['taxonomy'], TAXONOMY)
            for entries in server.snapshot(path)['items'].values():
                for item in entries:
                    if item['kind'] in CATEGORIES:
                        self.assertIn(item['category'], ['', *CATEGORIES[item['kind']]])
                        self.assertIsInstance(item['tags'], list)

    def test_ai_schema_and_instructions_include_fixed_categories_tags_and_cuisine(self):
        for kind, plural in [('attraction', 'attractions'), ('food', 'foods')]:
            schema = ai.OUTPUT_SCHEMA['properties'][plural]['items']
            self.assertEqual(schema['properties']['category']['enum'], ['', *CATEGORIES[kind]])
            self.assertIn('tags', schema['required'])
            self.assertEqual(schema['properties']['tags']['type'], 'array')
        self.assertIn('cuisine', ai.OUTPUT_SCHEMA['properties']['foods']['items']['required'])
        self.assertIn('特色菜肴必须尽可能补充具体 cuisine', ai.SYSTEM_PROMPT)


if __name__ == '__main__':
    unittest.main()
