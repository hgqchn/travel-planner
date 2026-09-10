from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import place_cache


class PlaceCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.first = self.make_project("trip.db")
        self.second = self.make_project("project-second.db")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_project(self, name: str) -> Path:
        path = self.root / name
        with sqlite3.connect(path) as db:
            db.executescript("""
                CREATE TABLE cities (id TEXT PRIMARY KEY, name TEXT);
                CREATE TABLE items (id TEXT PRIMARY KEY, city_id TEXT, kind TEXT, payload TEXT, updated_at TEXT);
                INSERT INTO cities VALUES ('shanghai', '上海');
                INSERT INTO cities VALUES ('beijing', '北京');
            """)
        return path

    def add_place(self, path: Path, city: str, kind: str, payload: dict, timestamp: str = "2026-09-10T00:00:00Z") -> None:
        with sqlite3.connect(path) as db:
            place_cache.enqueue(db, city, kind, payload, timestamp)

    def test_existing_project_backfill_excludes_private_metadata_and_itinerary(self) -> None:
        with sqlite3.connect(self.first) as db:
            db.executemany("INSERT INTO items VALUES (?, 'shanghai', ?, ?, '2026-09-10T00:00:00Z')", [
                ("a", "attraction", json.dumps({"name": "外滩", "description": "散步", "created_by": "私有作者", "city_id": "私有城市ID", "version": 4})),
                ("b", "food", json.dumps({"name": "生煎", "tip": "趁热吃", "updated_by": "私有作者"})),
                ("c", "itinerary", json.dumps({"title": "私人行程", "date": "2026-10-01"})),
            ])
        self.assertEqual(place_cache.prepare(self.first), 2)
        result = place_cache.load_city(self.first, "上海市")
        self.assertEqual({row["kind"] for row in result}, {"attraction", "food"})
        self.assertEqual({row["payload"]["name"] for row in result}, {"外滩", "生煎"})
        self.assertNotIn("私有", json.dumps(result, ensure_ascii=False))
        for row in result:
            self.assertEqual(set(row["payload"]), set(place_cache.CACHE_FIELDS[row["kind"]]))
        with sqlite3.connect(self.first) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM city_cache_imports").fetchone()[0], 2)
        self.assertEqual(place_cache.prepare(self.first), 0)

    def test_new_project_leaves_cities_available_for_first_hydration(self) -> None:
        place_cache.prepare(self.second, mark_existing_cities=False)
        # Subsequent ordinary preparation cannot change the initial decision.
        place_cache.prepare(self.second)
        with sqlite3.connect(self.second) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM city_cache_imports").fetchone()[0], 0)

    def test_rolled_back_project_write_does_not_publish_cache_event(self) -> None:
        place_cache.prepare(self.first)
        with self.assertRaises(RuntimeError):
            with sqlite3.connect(self.first) as db:
                place_cache.enqueue(db, "上海", "food", {"name": "失败的添加"}, "2026-09-10T00:00:00Z")
                raise RuntimeError("simulate failed project transaction")
        self.assertEqual(place_cache.flush(self.first), 0)
        self.assertEqual(place_cache.load_city(self.first, "上海"), [])

    def test_shared_cache_survives_project_deletion_and_returns_independent_copies(self) -> None:
        place_cache.prepare(self.first)
        place_cache.prepare(self.second, mark_existing_cities=False)
        self.add_place(self.first, "上海", "food", {"name": "生煎", "tip": "趁热吃"})
        place_cache.flush(self.first)
        first_copy = place_cache.load_city(self.second, "上海市")
        first_copy[0]["payload"]["tip"] = "项目内修改"
        with sqlite3.connect(self.first) as db:
            db.execute("DELETE FROM items")
            db.execute("DELETE FROM cities WHERE id = 'shanghai'")
        place_cache.prepare(self.first)
        second_copy = place_cache.load_city(self.second, "上海")
        self.assertEqual(second_copy[0]["payload"]["tip"], "趁热吃")

    def test_cache_failure_keeps_outbox_for_a_later_retry(self) -> None:
        place_cache.prepare(self.first)
        self.add_place(self.first, "上海", "food", {"name": "生煎"})
        with patch.object(place_cache, "cache_path", return_value=self.root):
            with self.assertRaises(sqlite3.OperationalError):
                place_cache.flush(self.first)
        with sqlite3.connect(self.first) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM cache_outbox").fetchone()[0], 1)
        self.assertEqual(place_cache.flush(self.first), 1)
        self.assertEqual(place_cache.flush(self.first), 0)
        self.assertEqual(len(place_cache.load_city(self.first, "上海")), 1)

    def test_replay_is_idempotent_and_does_not_overwrite_later_updates(self) -> None:
        place_cache.prepare(self.first)
        place_cache.prepare(self.second)
        self.add_place(self.first, "上海市", "food", {"name": "生 煎", "tip": "旧提示"})
        with sqlite3.connect(self.first) as db:
            event = db.execute("SELECT * FROM cache_outbox").fetchone()
        place_cache.flush(self.first)
        self.add_place(self.second, "上海", "food", {"name": "生煎", "tip": "新提示"}, "2026-09-10T00:01:00Z")
        place_cache.flush(self.second)
        with sqlite3.connect(self.first) as db:
            db.execute("INSERT INTO cache_outbox VALUES (?, ?, ?, ?, ?, ?, ?, ?)", event)
        self.assertEqual(place_cache.flush(self.first), 1)
        cached = place_cache.load_city(self.first, "上海")
        self.assertEqual(len(cached), 1)
        self.assertEqual(cached[0]["payload"]["tip"], "新提示")

    def test_cities_and_kinds_remain_separate_and_unknown_aliases_are_not_guessed(self) -> None:
        place_cache.prepare(self.first)
        self.add_place(self.first, "上海", "food", {"name": "老街"})
        self.add_place(self.first, "上海", "attraction", {"name": "老街"})
        self.add_place(self.first, "北京", "food", {"name": "老街"})
        self.add_place(self.first, "宿州市", "food", {"name": "老街"})
        self.assertEqual(len(place_cache.load_city(self.first, "上海市")), 2)
        self.assertEqual(len(place_cache.load_city(self.first, "北京市")), 1)
        self.assertEqual(place_cache.load_city(self.first, "宿州"), [])
        self.assertEqual(len(place_cache.load_city(self.first, "宿州市")), 1)

    def test_only_food_and_attractions_can_be_queued(self) -> None:
        place_cache.prepare(self.first)
        with sqlite3.connect(self.first) as db:
            self.assertFalse(place_cache.enqueue(db, "上海", "itinerary", {"title": "私人行程"}, "now"))
            self.assertFalse(place_cache.enqueue(db, "上海", "transit", {"name": "地铁"}, "now"))
            with self.assertRaises(ValueError):
                place_cache.enqueue(db, "上海", "food", {"name": ""}, "now")
            with self.assertRaises(ValueError):
                place_cache.enqueue(db, "上海", "food", {"name": "生煎", "tip": {"invalid": "value"}}, "now")
        self.assertEqual(place_cache.flush(self.first), 0)


if __name__ == "__main__":
    unittest.main()
