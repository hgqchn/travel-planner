from __future__ import annotations

import json
import tempfile
import time
import unittest
from functools import partial
from pathlib import Path
from unittest.mock import patch

import server
from ai_service import AIError, AIService


class ImmediateAIService(AIService):
    """Exercise the real persisted job/import flow without provider requests."""

    def _generate(self, request, context):
        result = {
            "schema_version": 1,
            "summary": "测试城市建议",
            "notices": [],
            "attractions": [{"name": "测试公园", "district": "", "category": "",
                             "description": "", "duration": "", "transport": ""}],
            "foods": [],
            "itineraries": [],
        }
        return {"status": "completed", "output": [
            {"type": "message", "status": "completed", "content": [
                {"type": "output_text", "text": json.dumps(result)}]}]}, "json_schema"


class ImportTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "trip.db"
        server.init_database(self.db, "test-project")
        server.claim_identity(self.db, {"user_id": "test-author"})
        self.city = server.create_city(self.db, {"name": "导入测试城"}, "test-author")["city"]["id"]

    def tearDown(self):
        self.temp.cleanup()

    def entry(self, name):
        return {"kind": "attraction", "data": {"name": name}}

    def test_duplicate_and_idempotency_preserve_author_and_city(self):
        entries = [self.entry("公园"), self.entry("公 园"), {"kind": "food", "data": {"name": "特色面"}}]
        first = server.import_ai_items(self.db, self.city, "test-author", entries, "job-one")
        again = server.import_ai_items(self.db, self.city, "test-author", entries, "job-one")
        self.assertEqual(first, again)
        self.assertEqual((first["created"], first["skipped"]), (2, 1))
        snap = server.snapshot(self.db, self.city)
        items = snap["items"]["attraction"] + snap["items"]["food"]
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item["city_id"] == self.city and item["created_by"] == "test-author" for item in items))

    def test_mid_transaction_quota_failure_rolls_back_batch(self):
        original = server.ensure_item_capacity
        calls = []
        def capacity(db, city, kind, amount=1):
            calls.append(kind)
            if len(calls) == 2:
                raise server.ApiError(507, "quota")
            return original(db, city, kind, amount)
        before = server.snapshot(self.db, self.city)["revision"]
        with patch.object(server, "ensure_item_capacity", side_effect=capacity):
            with self.assertRaises(server.ApiError):
                server.import_ai_items(self.db, self.city, "test-author", [self.entry("一号园"), self.entry("二号园")], "job-two")
        snap = server.snapshot(self.db, self.city)
        self.assertEqual(snap["items"]["attraction"], [])
        self.assertEqual(snap["revision"], before)
        with server.connect_db(self.db) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM ai_import_batches WHERE job_id='job-two'").fetchone())

    def test_invalid_entry_does_not_write_valid_prefix(self):
        with self.assertRaises(server.ApiError):
            server.import_ai_items(self.db, self.city, "test-author", [self.entry("一号园"), {"kind": "itinerary", "data": {"date": "2026-02-30", "title": "无效日期"}}], "job-three")
        self.assertEqual(server.snapshot(self.db, self.city)["items"]["attraction"], [])

    def test_import_rechecks_changed_city_identity_inside_transaction(self):
        with server.connect_db(self.db) as db:
            db.execute("CREATE TABLE ai_jobs (id TEXT PRIMARY KEY, city_name TEXT NOT NULL)")
            db.execute("INSERT INTO ai_jobs VALUES (?, ?)", ("changed-city-job", "导入测试城"))
        server.admin_change(self.db, "PUT", "cities", {"id": self.city, "name": "另一座测试城"})
        before = server.snapshot(self.db, self.city)
        with self.assertRaises(server.ApiError) as changed:
            server.import_ai_items(self.db, self.city, "test-author", [self.entry("旧城市公园")], "changed-city-job")
        self.assertEqual(changed.exception.status, 409)
        after = server.snapshot(self.db, self.city)
        self.assertEqual(after["items"], before["items"])
        self.assertEqual(after["activity"], before["activity"])
        self.assertEqual(after["revision"], before["revision"])
        with server.connect_db(self.db) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM ai_import_batches WHERE job_id=?", ("changed-city-job",)).fetchone())

    def test_import_allows_city_alias_and_returns_existing_receipt_after_rename(self):
        with server.connect_db(self.db) as db:
            db.execute("CREATE TABLE ai_jobs (id TEXT PRIMARY KEY, city_name TEXT NOT NULL)")
            db.execute("INSERT INTO ai_jobs VALUES (?, ?)", ("alias-city-job", "北京"))
        server.admin_change(self.db, "PUT", "cities", {"id": "beijing", "name": "北京市"})
        entries = [self.entry("北京测试公园")]
        first = server.import_ai_items(self.db, "beijing", "test-author", entries, "alias-city-job")
        self.assertEqual(first["created"], 1)
        server.admin_change(self.db, "PUT", "cities", {"id": "beijing", "name": "重新命名的测试城"})
        repeated = server.import_ai_items(self.db, "beijing", "test-author", entries, "alias-city-job")
        self.assertEqual(repeated, first)
        self.assertEqual(len(server.snapshot(self.db, "beijing")["items"]["attraction"]), 1)

    def test_user_rename_without_ai_tables_still_succeeds(self):
        server.admin_change(self.db, "PUT", "users", {"user_id": "TEST-AUTHOR", "new_user_id": "renamed-author"})
        self.assertEqual([user["user_id"] for user in server.list_users(self.db)], ["renamed-author"])
        with server.connect_db(self.db) as db:
            for table in ("ai_jobs", "ai_import_batches"):
                self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone())

    def test_user_rename_preserves_ready_jobs_and_import_receipts(self):
        service = ImmediateAIService(
            self.db, api_key="fake-test-key",
            get_context=partial(server.ai_context, self.db),
            normalize_item=server.validate_payload,
            import_items=partial(server.import_ai_items, self.db),
            cooldown_seconds=0,
        )
        try:
            job = service.create_job("test-author", "same-device", {"city_id": self.city, "kinds": ["attraction"], "days": 1})
            deadline = time.monotonic() + 3
            while job["status"] in ("queued", "running") and time.monotonic() < deadline:
                time.sleep(0.01)
                job = service.get_job("test-author", "same-device", job["id"])
            self.assertEqual(job["status"], "ready", job["error"])
            with server.connect_db(self.db) as db:
                original_device = db.execute("SELECT session_hash FROM ai_jobs WHERE id=?", (job["id"],)).fetchone()[0]
            server.admin_change(self.db, "PUT", "users", {"user_id": "TEST-AUTHOR", "new_user_id": "renamed-author"})
            renamed_job = service.get_job("renamed-author", "same-device", job["id"])
            self.assertEqual(renamed_job["status"], "ready")
            self.assertEqual(renamed_job["result"], job["result"])
            with self.assertRaises(AIError):
                service.get_job("test-author", "same-device", job["id"])
            entries = [{"kind": "attraction", "data": renamed_job["result"]["attractions"][0]}]
            first = service.import_job("renamed-author", "same-device", job["id"], {"items": entries})
            self.assertEqual(first["created"], 1)
            # Rename again after the import receipt exists, then emulate a crash
            # between the callback commit and the service's own status update.
            server.admin_change(self.db, "PUT", "users", {"user_id": "renamed-author", "new_user_id": "final-author"})
            with server.connect_db(self.db) as db:
                db.execute("UPDATE ai_jobs SET status='ready', import_json=NULL WHERE id=?", (job["id"],))
                saved = db.execute("SELECT user_id,session_hash FROM ai_jobs WHERE id=?", (job["id"],)).fetchone()
                self.assertEqual((saved["user_id"], saved["session_hash"]), ("final-author", original_device))
                self.assertEqual(db.execute("SELECT user_id FROM ai_import_batches WHERE job_id=?", (job["id"],)).fetchone()[0], "final-author")
            repeated = service.import_job("final-author", "same-device", job["id"], {"items": entries})
            self.assertEqual(repeated, first)
            items = server.snapshot(self.db, self.city)["items"]["attraction"]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["created_by"], "final-author")
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
