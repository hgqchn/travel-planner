from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import server
from test_server import RunningServer, json_request, opener_with_cookies, unlock_project


class BulkDeleteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "trip.db"
        seed = Path(self.temp.name) / "seed.json"
        seed.write_text("{}", encoding="utf-8")
        server.init_database(self.db_path, "bulk-test-project", seed)
        server.claim_identity(self.db_path, {"user_id": "bulk-tester"})
        self.city_id = "shanghai"
        self.first = self.create("attraction", "甲公园")
        self.second = self.create("attraction", "乙公园")
        self.food = self.create("food", "特色面")

    def tearDown(self):
        self.temp.cleanup()

    def create(self, kind, name, city_id=None):
        return server.create_item(self.db_path, kind, {
            "city_id": city_id or self.city_id, "name": name,
        }, "bulk-tester")["item"]

    def body(self, *items):
        return {"city_id": self.city_id, "items": [
            {"id": item["id"], "version": item["version"]} for item in items
        ]}

    def assert_rejected_unchanged(self, body, status, kind="attraction"):
        before = server.snapshot(self.db_path)
        with self.assertRaises(server.ApiError) as raised:
            server.batch_delete_items(self.db_path, kind, body, "bulk-tester")
        self.assertEqual(raised.exception.status, status)
        after = server.snapshot(self.db_path)
        # Wall-clock response metadata can cross a second without a DB write.
        before.pop('server_time', None)
        after.pop('server_time', None)
        self.assertEqual(after, before)

    def test_selected_items_deleted_with_activity_and_revision(self):
        before = server.snapshot(self.db_path)
        result = server.batch_delete_items(
            self.db_path, "attraction", self.body(self.first, self.second), "bulk-tester"
        )
        self.assertEqual(result, {
            "revision": before["revision"] + 2,
            "deleted_ids": [self.first["id"], self.second["id"]],
            "deleted_count": 2,
            "city_id": self.city_id,
        })
        after = server.snapshot(self.db_path)
        self.assertEqual(after["items"]["attraction"], [])
        self.assertEqual(after["items"]["food"], before["items"]["food"])
        deletion_log = after["activity"][:2]
        self.assertEqual({row["item_id"] for row in deletion_log}, set(result["deleted_ids"]))
        self.assertTrue(all(row["action"] == "delete" and row["user_id"] == "bulk-tester"
                            and row["city_id"] == self.city_id for row in deletion_log))

    def test_food_batch_and_other_project_unchanged(self):
        other_path = Path(self.temp.name) / "other-project.db"
        with sqlite3.connect(self.db_path) as source, sqlite3.connect(other_path) as target:
            source.backup(target)
        other_before = server.snapshot(other_path)
        result = server.batch_delete_items(self.db_path, "food", self.body(self.food), "bulk-tester")
        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(server.snapshot(self.db_path)["items"]["food"], [])
        self.assertEqual(server.snapshot(other_path), other_before)

    def test_stale_version_after_valid_selection_deletes_nothing(self):
        body = self.body(self.first, self.second)
        server.update_item(self.db_path, "attraction", self.second["id"], {
            "name": "乙公园的新名称", "version": self.second["version"],
        }, "bulk-tester")
        self.assert_rejected_unchanged(body, 409)

    def test_missing_wrong_kind_or_wrong_city_deletes_nothing(self):
        other_city_item = self.create("attraction", "北京公园", "beijing")
        for other in ({"id": "0" * 12, "version": 1}, self.food, other_city_item):
            with self.subTest(other=other["id"]):
                self.assert_rejected_unchanged(self.body(self.first, other), 404)
        body = self.body(self.first)
        body["city_id"] = "missing-city"
        self.assert_rejected_unchanged(body, 404)

    def test_duplicate_selection_is_rejected(self):
        self.assert_rejected_unchanged(self.body(self.first, self.first), 400)

    def test_malformed_request_versions_ids_and_size_rejected(self):
        invalid = [None, [], {}, {"city_id": self.city_id, "items": []},
                   {"city_id": self.city_id, "items": [{}] * 251},
                   {"city_id": True, "items": [self.first]},
                   {"city_id": self.city_id, "items": [None]},
                   {"city_id": self.city_id, "items": "invalid"}]
        for version in (None, True, False, 0, -1, 1.0, "1"):
            invalid.append(self.body({**self.first, "version": version}))
        for item_id in (None, True, "bad-id", "f" * 13):
            invalid.append(self.body({**self.first, "id": item_id}))
        for body in invalid:
            with self.subTest(body=body):
                self.assert_rejected_unchanged(body, 400)
        for kind in ("itinerary", "transit", "unknown"):
            self.assert_rejected_unchanged(self.body(self.first), 400, kind)

    def test_mid_transaction_failure_rolls_back_items_log_and_revision(self):
        before = server.snapshot(self.db_path)
        original = server.next_revision
        calls = []

        def fail_second(db):
            calls.append(1)
            if len(calls) == 2:
                raise sqlite3.OperationalError("simulated write failure")
            return original(db)

        with patch.object(server, "next_revision", side_effect=fail_second):
            with self.assertRaises(sqlite3.OperationalError):
                server.batch_delete_items(self.db_path, "attraction",
                                          self.body(self.first, self.second), "bulk-tester")
        self.assertEqual(server.snapshot(self.db_path), before)


class BulkDeleteHttpTests(unittest.TestCase):
    def test_route_preserves_project_identity_origin_and_write_rate_guards(self):
        running = RunningServer()
        try:
            anonymous = opener_with_cookies()
            path = "/api/items/attraction/batch-delete"
            with self.assertRaises(urllib.error.HTTPError) as error:
                json_request(anonymous, running.base_url, path, method="POST", payload={})
            self.assertEqual(error.exception.code, 401)
            opener = opener_with_cookies()
            unlock_project(opener, running)
            with self.assertRaises(urllib.error.HTTPError) as error:
                json_request(opener, running.base_url, path, method="POST", payload={})
            self.assertEqual(error.exception.code, 401)
            json_request(opener, running.base_url, "/api/session", method="POST",
                         payload={"user_id": "bulk-http"})
            _, created, _ = json_request(opener, running.base_url, "/api/items/attraction",
                                        method="POST", payload={"name": "HTTP 公园"})
            item = created["item"]
            body = {"city_id": item["city_id"], "items": [{"id": item["id"], "version": 1}]}
            request = urllib.request.Request(running.base_url + path,
                                            data=json.dumps(body).encode(), method="POST",
                                            headers={"Content-Type": "application/json",
                                                     "Origin": "https://untrusted.example"})
            with self.assertRaises(urllib.error.HTTPError) as error:
                opener.open(request, timeout=3)
            self.assertEqual(error.exception.code, 403)
            with patch.object(running.httpd.rate_limiter, "allow", return_value=False):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    json_request(opener, running.base_url, path, method="POST", payload=body)
                self.assertEqual(error.exception.code, 429)
            status, deleted, _ = json_request(opener, running.base_url, path,
                                              method="POST", payload=body)
            self.assertEqual(status, 200)
            self.assertEqual(deleted["deleted_ids"], [item["id"]])
            self.assertEqual(deleted["deleted_count"], 1)
        finally:
            running.close()


if __name__ == "__main__":
    unittest.main()
