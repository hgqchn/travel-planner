from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import city_catalog
import china_regions
import server


class CityCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "trip.db"
        self.seed_path = Path(self.temp.name) / "seed.json"
        self.seed_path.write_text("{}", encoding="utf-8")
        server.init_database(self.db_path, "test-project-code", self.seed_path)
        server.claim_identity(self.db_path, {"user_id": "city_tester"})

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_catalog_is_bounded_complete_and_unambiguous(self) -> None:
        cities = city_catalog.load_catalog()
        self.assertEqual(len(cities), 162)
        self.assertEqual(len(cities), len({city["id"] for city in cities}))
        self.assertEqual(len(cities), len({city["name"] for city in cities}))
        required = {
            "北京", "上海", "深圳", "广州", "西安", "洛阳", "长沙", "重庆", "成都",
            "呼和浩特", "呼伦贝尔", "阿尔山", "满洲里", "鄂尔多斯",
        }
        self.assertTrue(required.issubset({city["name"] for city in cities}))
        self.assertEqual(
            {city["name"] for city in cities if city["province"] == "内蒙古"},
            {"呼和浩特", "呼伦贝尔", "阿尔山", "满洲里", "鄂尔多斯", "包头"},
        )
        for city in cities:
            self.assertRegex(city["pinyin"], r"^[a-z]+$")
            self.assertRegex(city["initials"], r"^[a-z]+$")
            self.assertTrue(city["province"])
            self.assertIn(city["name"], city["map_query"])
            self.assertEqual(city_catalog.find_catalog_city(city["name"])["id"], city["id"])
            for alias in city["aliases"]:
                self.assertEqual(city_catalog.find_catalog_city(alias)["id"], city["id"])
        # Similar phonetics and short fragments are not safe canonical matches.
        self.assertIsNone(city_catalog.find_catalog_city("宿州"))
        self.assertIsNone(city_catalog.find_catalog_city("苏"))
        self.assertIsNone(city_catalog.find_catalog_city("sz"))

    def test_all_mainland_capitals_and_representative_destinations_are_present(self):
        names = {city['name'] for city in city_catalog.load_catalog()}
        capitals = set('北京 天津 上海 重庆 石家庄 太原 沈阳 长春 哈尔滨 南京 杭州 合肥 福州 南昌 济南 郑州 武汉 长沙 广州 海口 成都 贵阳 昆明 西安 兰州 西宁 呼和浩特 南宁 拉萨 银川 乌鲁木齐'.split())
        destinations = set('苏州 无锡 宁波 佛山 东莞 泉州 烟台 黄山 桂林 三亚 大理 丽江 景洪 张家界 敦煌 喀什 香港 澳门 台北'.split())
        self.assertTrue((capitals | destinations).issubset(names))
        self.assertEqual(city_catalog.find_catalog_city('台州')['province'], '浙江')
        self.assertEqual(city_catalog.find_catalog_city('泰州')['province'], '江苏')

    def test_expansion_preserves_existing_data_aliases_and_deleted_old_defaults(self):
        item = server.create_item(self.db_path, 'itinerary', {'title': '原行程', 'date': '2026-10-01'}, 'city_tester')['item']
        with server.connect_db(self.db_path) as db:
            db.execute("DELETE FROM cities WHERE id NOT IN (%s)" % ','.join('?' for _ in city_catalog.LEGACY_CITY_IDS), tuple(city_catalog.LEGACY_CITY_IDS))
            db.execute("DELETE FROM cities WHERE id='beijing'")
            db.execute("DELETE FROM meta WHERE key=?", (city_catalog.CATALOG_EXPANSION_KEY,))
            db.execute("INSERT INTO cities VALUES('legacy-hangzhou','杭州市',200,'old','old')")
            db.execute("INSERT INTO cities VALUES('nanjing','自定义营地',201,'old','old')")
            original = dict(db.execute('SELECT * FROM items WHERE id=?', (item['id'],)).fetchone())
            settings = dict(db.execute('SELECT * FROM project_settings').fetchone())
            revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
        server.init_database(self.db_path, 'ignored', self.seed_path)
        state = server.snapshot(self.db_path)
        self.assertNotIn('北京', [city['name'] for city in state['cities']])
        self.assertEqual([city['id'] for city in state['cities'] if city['catalog_id']=='hangzhou'], ['legacy-hangzhou'])
        self.assertNotEqual(next(city['id'] for city in state['cities'] if city['name']=='南京'), 'nanjing')
        self.assertEqual(state['revision'], revision + 1)
        with server.connect_db(self.db_path) as db:
            self.assertEqual(dict(db.execute('SELECT * FROM items WHERE id=?', (item['id'],)).fetchone()), original)
            self.assertEqual(dict(db.execute('SELECT * FROM project_settings').fetchone()), settings)
        server.admin_change(self.db_path, 'DELETE', 'cities', {'id':'guilin'})
        before = server.snapshot(self.db_path)
        server.init_database(self.db_path, 'ignored-again', self.seed_path)
        after = server.snapshot(self.db_path)
        self.assertEqual(after['cities'], before['cities'])
        self.assertEqual(after['revision'], before['revision'])

    def test_fresh_database_has_catalog_and_zero_revision(self) -> None:
        state = server.snapshot(self.db_path)
        self.assertEqual(len(state["cities"]), len(city_catalog.load_catalog()))
        self.assertEqual(state["revision"], 0)
        self.assertEqual(state["cities"][0]["id"], "shanghai")
        self.assertEqual(state["cities"][0]["pinyin"], "shanghai")
        self.assertTrue(all(not items for items in state["items"].values()))

    def test_existing_database_upgrade_preserves_alias_identity_and_items(self) -> None:
        existing_item = server.create_item(
            self.db_path, "attraction", {"name": "原始内容"}, "city_tester"
        )["item"]
        with server.connect_db(self.db_path) as db:
            db.execute("DELETE FROM cities WHERE id <> 'shanghai'")
            db.execute("DELETE FROM meta WHERE key = ?", (city_catalog.CATALOG_MIGRATION_KEY,))
            db.execute("INSERT INTO cities VALUES ('legacy-shenzhen', '深圳市', 17, 'old', 'old')")
            # An old custom city may already use a bundled ID for a different name.
            db.execute("INSERT INTO cities VALUES ('beijing', '另一座城市', 18, 'old', 'old')")
            db.execute("UPDATE meta SET value = 41 WHERE key = 'revision'")
            original_item = dict(db.execute("SELECT * FROM items WHERE id = ?", (existing_item["id"],)).fetchone())
            original_user = dict(db.execute("SELECT * FROM users").fetchone())
            original_project = dict(db.execute("SELECT * FROM project_settings").fetchone())
        server.init_database(self.db_path, "ignored-existing-code", self.seed_path)
        state = server.snapshot(self.db_path)
        shenzhen = [city for city in state["cities"] if city["catalog_id"] == "shenzhen"]
        self.assertEqual(len(shenzhen), 1)
        self.assertEqual((shenzhen[0]["id"], shenzhen[0]["name"], shenzhen[0]["position"]), ("legacy-shenzhen", "深圳市", 17))
        self.assertEqual(state["revision"], 42)
        actual_beijing = next(city for city in state["cities"] if city["name"] == "北京")
        self.assertNotEqual(actual_beijing["id"], "beijing")
        with server.connect_db(self.db_path) as db:
            self.assertEqual(dict(db.execute("SELECT * FROM items WHERE id = ?", (existing_item["id"],)).fetchone()), original_item)
            self.assertEqual(dict(db.execute("SELECT * FROM users").fetchone()), original_user)
            self.assertEqual(dict(db.execute("SELECT * FROM project_settings").fetchone()), original_project)
            before = db.execute("SELECT COUNT(*) FROM cities").fetchone()[0]
        server.init_database(self.db_path, "ignored-again", self.seed_path)
        repeated = server.snapshot(self.db_path)
        self.assertEqual(len(repeated["cities"]), before)
        self.assertEqual(repeated["revision"], 42)

    def test_deleted_bundled_city_stays_deleted_until_explicitly_added(self) -> None:
        server.admin_change(self.db_path, "DELETE", "cities", {"id": "beijing"})
        server.init_database(self.db_path, "unused", self.seed_path)
        self.assertNotIn("北京", [city["name"] for city in server.snapshot(self.db_path)["cities"]])
        created = server.create_city(self.db_path, {"name": "北京市"}, "city_tester")
        self.assertEqual(created["city"]["name"], "北京")
        self.assertEqual(created["city"]["catalog_id"], "beijing")
        self.assertEqual(created["city"]["map_query"], "北京市")

    def test_custom_creation_and_canonical_duplicate_detection(self) -> None:
        custom = server.create_city(self.db_path, {"name": "  宿州  "}, "city_tester")["city"]
        self.assertEqual(custom["name"], "宿州")
        self.assertIsNone(custom["catalog_id"])
        self.assertEqual(custom["province"], "安徽")
        self.assertEqual(custom["map_query"], "安徽省宿州市")
        with self.assertRaises(server.ApiError) as duplicate:
            server.create_city(self.db_path, {"name": "深圳市"}, "city_tester")
        self.assertEqual(duplicate.exception.status, 409)
        self.assertEqual(duplicate.exception.extra["city"]["id"], "shenzhen")
        with self.assertRaises(server.ApiError) as duplicate_custom:
            server.create_city(self.db_path, {"name": "宿州"}, "city_tester")
        self.assertEqual(duplicate_custom.exception.status, 409)
        with self.assertRaises(server.ApiError):
            server.create_city(self.db_path, {"name": "新城市"}, "missing-user")

    def test_offline_regions_cover_every_province_with_unique_codes(self):
        provinces = china_regions.load_regions()
        self.assertEqual(len(provinces), 34)
        codes = [city['code'] for province in provinces for city in province['cities']]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertGreater(len(codes), 3000)
        for city in city_catalog.load_catalog():
            region = city_catalog.find_city_region(city['name'])
            self.assertIsNotNone(region, city['name'])
            self.assertEqual(region['province'], city['province'], city['name'])

    def test_custom_city_province_and_aliases(self):
        cases = {'赤峰':'内蒙古', '乌兰浩特市':'内蒙古', '安吉':'浙江',
                 '宿州市':'安徽', '济源':'河南', '石河子':'新疆', '基隆':'台湾'}
        for name, province in cases.items():
            with self.subTest(name=name):
                created = server.create_city(self.db_path, {'name':name}, 'city_tester')['city']
                self.assertEqual(created['province'], province)
                self.assertEqual(created['name'], name)
        for alias in ('赤峰市', '内蒙古赤峰', '内蒙古自治区赤峰市', '内蒙赤峰市'):
            with self.subTest(alias=alias), self.assertRaises(server.ApiError) as duplicate:
                server.create_city(self.db_path, {'name':alias}, 'city_tester')
            self.assertEqual(duplicate.exception.status, 409)
            self.assertEqual(duplicate.exception.extra['city']['name'], '赤峰')
        # Pinyin fragments and mismatched province prefixes must not be guessed.
        for name in ('cf', '赤', '河北省赤峰市', '未知露营地', '朝阳区'):
            self.assertIsNone(china_regions.find_region(name), name)
        self.assertEqual(china_regions.find_region('朝阳')['province'], '辽宁')
        self.assertEqual(china_regions.find_region('北京市朝阳区')['province'], '北京')
        unknown = server.create_city(self.db_path, {'name':'未知露营地'}, 'city_tester')['city']
        self.assertEqual(unknown['province'], '自定义')

    def test_existing_chifeng_gets_province_without_rewriting_user_data(self):
        with server.connect_db(self.db_path) as db:
            db.execute("INSERT INTO cities VALUES('legacy-chifeng','赤峰市',401,'old','old')")
        item = server.create_item(self.db_path, 'itinerary', {
            'city_id':'legacy-chifeng', 'title':'原有赤峰行程', 'date':'2026-10-01',
        }, 'city_tester')['item']
        with server.connect_db(self.db_path) as db:
            before_city = dict(db.execute("SELECT * FROM cities WHERE id='legacy-chifeng'").fetchone())
            before_item = dict(db.execute('SELECT * FROM items WHERE id=?', (item['id'],)).fetchone())
            revision = db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
        state = server.snapshot(self.db_path, 'legacy-chifeng')
        city = next(c for c in state['cities'] if c['id']=='legacy-chifeng')
        self.assertEqual(city['province'], '内蒙古')
        self.assertEqual(city['name'], '赤峰市')
        self.assertEqual(city['position'], 401)
        with server.connect_db(self.db_path) as db:
            self.assertEqual(dict(db.execute("SELECT * FROM cities WHERE id='legacy-chifeng'").fetchone()), before_city)
            self.assertEqual(dict(db.execute('SELECT * FROM items WHERE id=?', (item['id'],)).fetchone()), before_item)
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0], revision)

    def test_admin_creation_and_rename_recompute_province(self):
        state = server.admin_change(self.db_path, 'POST', 'cities', {'name':'赤峰'})
        city = next(c for c in state['cities'] if c['name']=='赤峰')
        self.assertEqual(city['province'], '内蒙古')
        state = server.admin_change(self.db_path, 'PUT', 'cities', {'id':city['id'], 'name':'宿州'})
        renamed = next(c for c in state['cities'] if c['id']==city['id'])
        self.assertEqual(renamed['province'], '安徽')
        other = server.create_city(self.db_path, {'name':'另外一座城'}, 'city_tester')['city']
        with self.assertRaises(server.ApiError) as duplicate:
            server.admin_change(self.db_path, 'PUT', 'cities', {'id':other['id'], 'name':'安徽省宿州市'})
        self.assertEqual(duplicate.exception.status, 409)

    def test_admin_rename_rejects_duplicate_alias(self) -> None:
        custom = server.create_city(self.db_path, {"name": "测试城市"}, "city_tester")["city"]
        with self.assertRaises(server.ApiError) as duplicate:
            server.admin_change(self.db_path, "PUT", "cities", {"id": custom["id"], "name": "北京市"})
        self.assertEqual(duplicate.exception.status, 409)
        # Changing a city's own presentation to a known alias remains allowed.
        state = server.admin_change(self.db_path, "PUT", "cities", {"id": "beijing", "name": "北京市"})
        city = next(city for city in state["cities"] if city["id"] == "beijing")
        self.assertEqual(city["name"], "北京市")
        self.assertEqual(city["catalog_id"], "beijing")

    def test_snapshot_filters_both_content_and_recent_activity(self) -> None:
        first = server.create_item(self.db_path, "attraction", {"name": "甲", "city_id": "beijing"}, "city_tester")["item"]
        server.create_item(self.db_path, "food", {"name": "乙", "city_id": "shanghai"}, "city_tester")
        filtered = server.snapshot(self.db_path, "beijing")
        self.assertEqual(filtered["city_id"], "beijing")
        self.assertEqual([entry["id"] for entry in filtered["items"]["attraction"]], [first["id"]])
        self.assertEqual(filtered["items"]["food"], [])
        self.assertEqual({entry["city_id"] for entry in filtered["activity"]}, {"beijing"})
        self.assertEqual(len(filtered["cities"]), len(city_catalog.load_catalog()))
        self.assertEqual(len(server.snapshot(self.db_path)["activity"]), 2)
        with self.assertRaises(server.ApiError) as missing:
            server.snapshot(self.db_path, "missing")
        self.assertEqual(missing.exception.status, 404)

    def test_item_quota_and_positions_are_per_city_and_total_is_bounded(self) -> None:
        with patch.object(server, "MAX_ITEMS_PER_KIND", 1):
            first = server.create_item(self.db_path, "food", {"name": "甲", "city_id": "beijing"}, "city_tester")["item"]
            second = server.create_item(self.db_path, "food", {"name": "乙", "city_id": "shanghai"}, "city_tester")["item"]
            self.assertEqual(first["position"], 1)
            self.assertEqual(second["position"], 1)
            with self.assertRaises(server.ApiError) as quota:
                server.create_item(self.db_path, "food", {"name": "丙", "city_id": "beijing"}, "city_tester")
            self.assertEqual(quota.exception.status, 507)
        with patch.object(server, "MAX_ITEMS_TOTAL", 2):
            with self.assertRaises(server.ApiError) as quota:
                server.create_item(self.db_path, "attraction", {"name": "丁", "city_id": "chengdu"}, "city_tester")
            self.assertEqual(quota.exception.status, 507)
        self.assertEqual(len(server.snapshot(self.db_path)["activity"]), 2)

    def test_city_limit_enforced_at_five_hundred(self) -> None:
        with server.connect_db(self.db_path) as db:
            current = db.execute("SELECT COUNT(*) FROM cities").fetchone()[0]
            db.executemany(
                "INSERT INTO cities VALUES (?, ?, ?, 'test', 'test')",
                [(f"custom-{index}", f"自定义城市{index}", index + 1000) for index in range(500 - current)],
            )
        with self.assertRaises(server.ApiError) as quota:
            server.create_city(self.db_path, {"name": "第501个城市"}, "city_tester")
        self.assertEqual(quota.exception.status, 507)
        self.assertEqual(len(server.snapshot(self.db_path)["cities"]), 500)


if __name__ == "__main__":
    unittest.main()
