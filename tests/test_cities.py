from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import city_catalog
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
        self.assertEqual(len(cities), 14)
        self.assertEqual(len(cities), len({city["id"] for city in cities}))
        self.assertEqual(len(cities), len({city["name"] for city in cities}))
        required = {
            "北京", "上海", "深圳", "广州", "西安", "洛阳", "长沙", "重庆", "成都",
            "呼和浩特", "呼伦贝尔", "阿尔山", "满洲里", "鄂尔多斯",
        }
        self.assertEqual(required, {city["name"] for city in cities})
        self.assertEqual(
            {city["name"] for city in cities if city["province"] == "内蒙古"},
            {"呼和浩特", "呼伦贝尔", "阿尔山", "满洲里", "鄂尔多斯"},
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
        self.assertEqual(custom["map_query"], "宿州")
        with self.assertRaises(server.ApiError) as duplicate:
            server.create_city(self.db_path, {"name": "深圳市"}, "city_tester")
        self.assertEqual(duplicate.exception.status, 409)
        self.assertEqual(duplicate.exception.extra["city"]["id"], "shenzhen")
        with self.assertRaises(server.ApiError) as duplicate_custom:
            server.create_city(self.db_path, {"name": "宿州"}, "city_tester")
        self.assertEqual(duplicate_custom.exception.status, 409)
        with self.assertRaises(server.ApiError):
            server.create_city(self.db_path, {"name": "新城市"}, "missing-user")

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
