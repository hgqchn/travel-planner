"""AI replacement jobs against real temporary project databases; no network calls."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import ai_service as ai
import place_cache
import server
from project_store import ProjectStore


class LocalPlanService(ai.AIService):
    """Exercise production parsing, persistence and import with a local provider."""

    def _generate(self, request, context):
        start = date.fromisoformat(request["start_date"])
        value = {"schema_version": 1, "summary": "新的旅行安排", "notices": [],
                 "attractions": [], "foods": [], "itineraries": []}
        for offset in range(request["days"]):
            day = (start + timedelta(days=offset)).isoformat()
            value["itineraries"].append({
                "date": day, "start_time": "10:00", "title": "新行程 " + day,
                "category": "观光", "location": "城市公园", "notes": "预留休息时间。"})
        return {"status": "completed", "output": [{
            "type": "message", "status": "completed", "content": [{
                "type": "output_text", "text": json.dumps(value, ensure_ascii=False)}]}]}, "json_schema"


class ReplanIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "trip.db"
        seed = Path(self.temp.name) / "empty.json"
        seed.write_text("{}")
        server.init_database(self.path, "test-project", seed)
        self.user = "测试旅客"
        self.device = "test-device"
        server.claim_identity(self.path, {"user_id": self.user})
        self.old = [self.add(day="2026-10-0" + str(number), title="原行程 " + str(number))
                    for number in (1, 2, 3)]
        self.other_city = self.add(city="shanghai", title="其他城市的安排")
        for kind in ("attraction", "food", "transit"):
            payload = {"city_id": "beijing", "name": "保留的" + kind}
            if kind == "transit":
                payload["color"] = "#123456"
            server.create_item(self.path, kind, payload, self.user)
        self.services = []
        self.service = self.make_service(self.path)

    def tearDown(self):
        for service in self.services:
            service.close()
        self.temp.cleanup()

    def make_service(self, path):
        service = LocalPlanService(
            path, api_key="fake-local-test-key", model=ai.DEFAULT_MODEL,
            get_context=lambda city_id: server.ai_context(path, city_id),
            normalize_item=server.validate_payload,
            import_items=lambda city_id, user_id, items, job_id:
                server.import_ai_items(path, city_id, user_id, items, job_id),
            cooldown_seconds=0)
        self.services.append(service)
        return service

    def add(self, *, path=None, city="beijing", day="2026-10-01", title="新增行程"):
        return server.create_item(path or self.path, "itinerary", {
            "city_id": city, "date": day, "start_time": "09:00", "title": title,
            "notes": "原备注"}, self.user)["item"]

    def request(self, **overrides):
        value = {"city_id": "beijing", "kinds": ["itinerary"], "planning_mode": "replace_all",
                 "target_date": "", "start_date": "2026-10-01", "days": 2, "people": 1}
        value.update(overrides)
        return value

    def ready(self, service=None, **overrides):
        service = service or self.service
        job = service.create_job(self.user, self.device, self.request(**overrides))
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            job = service.get_job(self.user, self.device, job["id"])
            if job["status"] not in ("queued", "running"):
                self.assertEqual(job["status"], "ready", job["error"])
                return job
            time.sleep(0.01)
        self.fail("Local mock AI job did not complete")

    def ready_day(self, **overrides):
        return self.ready(planning_mode="replace_day", target_date="2026-10-01", days=1, **overrides)

    def import_payload(self, job):
        return {"city_id": job["city_id"], "confirm_replace": True,
                "items": [{"kind": "itinerary", "data": copy.deepcopy(item)}
                          for item in job["result"]["itineraries"]]}

    def import_job(self, job, payload=None, service=None):
        return (service or self.service).import_job(
            self.user, self.device, job["id"], self.import_payload(job) if payload is None else payload)

    def business_state(self, path=None):
        """Exclude task progress; compare every affected business table and receipt."""
        with server.connect_db(path or self.path) as db:
            result = {}
            for table in ("items", "activity", "meta", "cache_outbox", "ai_import_batches"):
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    result[table] = sorted(tuple(row) for row in db.execute("SELECT * FROM " + table))
                else:
                    result[table] = []
            return result

    def assert_rejected_without_write(self, job, status, payload=None):
        before = self.business_state()
        with self.assertRaises((ai.AIError, server.ApiError)) as failure:
            self.import_job(job, payload)
        self.assertEqual(failure.exception.status, status)
        self.assertEqual(self.business_state(), before)
        self.assertEqual(self.service.get_job(self.user, self.device, job["id"])["status"], "ready")
        return failure.exception

    def update(self, item, **changes):
        payload = {key: item.get(key, "") for key in
                   ("date", "start_time", "title", "category", "location", "notes", "link")}
        payload.update(changes, version=item["version"])
        return server.update_item(self.path, "itinerary", item["id"], payload, self.user)["item"]

    def test_replace_all_replaces_entire_city_and_keeps_places_other_city_and_cache(self):
        before = server.snapshot(self.path)
        cache_before = place_cache.load_city(self.path, "北京")
        job = self.ready()
        # The proposal covers two new days, but full replacement removes all three old days.
        self.assertEqual(job["replacement"], {"planning_mode": "replace_all", "target_date": "", "count": 3})
        self.assertNotIn(self.old[0]["id"], json.dumps(job))
        self.assertEqual(server.snapshot(self.path)["items"], before["items"])
        result = self.import_job(job)
        self.assertEqual((result["created"], result["deleted"], result["planning_mode"]), (2, 3, "replace_all"))
        after = server.snapshot(self.path)
        plans = [item for item in after["items"]["itinerary"] if item["city_id"] == "beijing"]
        self.assertEqual({item["date"] for item in plans}, {"2026-10-01", "2026-10-02"})
        self.assertFalse({item["id"] for item in plans} & {item["id"] for item in self.old})
        self.assertIn(self.other_city, after["items"]["itinerary"])
        for kind in ("attraction", "food", "transit"):
            self.assertEqual(after["items"][kind], before["items"][kind])
        self.assertEqual(place_cache.load_city(self.path, "北京"), cache_before)
        activities = [entry for entry in after["activity"] if entry["revision"] > before["revision"]]
        self.assertEqual([entry["action"] for entry in sorted(activities, key=lambda item: item["revision"])],
                         ["delete", "delete", "delete", "create", "create"])

    def test_replace_day_preserves_other_days_and_accepts_an_unrelated_edit(self):
        job = self.ready_day()
        self.assertEqual(job["replacement"]["count"], 1)
        unrelated = self.update(self.old[1], notes="其他日期刚刚修改")
        result = self.import_job(job)
        self.assertEqual((result["created"], result["deleted"]), (1, 1))
        plans = server.snapshot(self.path, "beijing")["items"]["itinerary"]
        self.assertIn(unrelated, plans)
        self.assertIn(self.old[2], plans)
        self.assertNotIn(self.old[0]["id"], {item["id"] for item in plans})

    def test_empty_city_can_receive_a_complete_replacement(self):
        city = server.create_city(self.path, {"name": "新的出行城市"}, self.user)["city"]
        job = self.ready(city_id=city["id"])
        self.assertEqual(job["replacement"]["count"], 0)
        before = server.snapshot(self.path, "beijing")["items"]
        result = self.import_job(job)
        self.assertEqual((result["created"], result["deleted"]), (2, 0))
        self.assertEqual(len(server.snapshot(self.path, city["id"])["items"]["itinerary"]), 2)
        self.assertEqual(server.snapshot(self.path, "beijing")["items"], before)

    def test_replacement_does_not_skip_a_generated_item_matching_the_old_plan(self):
        job = self.ready_day()
        payload = self.import_payload(job)
        payload["items"][0]["data"].update(
            title=self.old[0]["title"], start_time=self.old[0]["start_time"])
        result = self.import_job(job, payload)
        self.assertEqual((result["created"], result["skipped"], result["deleted"]), (1, 0, 1))
        self.assertNotEqual(result["item_ids"], [self.old[0]["id"]])

    def test_scope_addition_edit_and_deletion_each_abort_replacement(self):
        for change in ("add", "edit", "delete"):
            with self.subTest(change=change):
                job = self.ready()
                if change == "add":
                    self.add(day="2026-10-07", title="生成之后新添加")
                elif change == "edit":
                    self.old[0] = self.update(self.old[0], notes="生成之后新修改")
                else:
                    server.delete_item(self.path, "itinerary", self.old[0]["id"],
                                       {"version": self.old[0]["version"]}, self.user)
                error = self.assert_rejected_without_write(job, 409)
                self.assertEqual(error.extra["code"], "ITINERARY_CHANGED")

    def test_moving_an_item_into_or_out_of_the_target_day_is_a_conflict(self):
        job = self.ready_day()
        self.old[1] = self.update(self.old[1], date="2026-10-01")
        self.assert_rejected_without_write(job, 409)
        job = self.ready_day()
        self.old[0] = self.update(self.old[0], date="2026-10-02")
        self.assert_rejected_without_write(job, 409)

    def test_confirmation_is_explicit_and_rejected_submissions_do_not_write(self):
        job = self.ready()
        for confirm in (None, False, "true", 1):
            with self.subTest(confirm=confirm):
                payload = self.import_payload(job)
                if confirm is None:
                    del payload["confirm_replace"]
                else:
                    payload["confirm_replace"] = confirm
                self.assert_rejected_without_write(job, 400, payload)

    def test_other_user_device_or_city_cannot_apply_the_proposal(self):
        job = self.ready()
        before = self.business_state()
        for user, device in (("另一旅客", self.device), (self.user, "another-device")):
            with self.subTest(user=user, device=device), self.assertRaises(ai.AIError) as failure:
                self.service.import_job(user, device, job["id"], self.import_payload(job))
            self.assertEqual(failure.exception.status, 404)
        payload = self.import_payload(job)
        payload["city_id"] = "shanghai"
        self.assert_rejected_without_write(job, 400, payload)
        self.assertEqual(self.business_state(), before)

    def test_incomplete_dates_out_of_range_and_other_kinds_cannot_replace(self):
        job = self.ready()
        incomplete = self.import_payload(job)
        incomplete["items"].pop()
        self.assert_rejected_without_write(job, 400, incomplete)
        out_of_range = self.import_payload(job)
        out_of_range["items"][0]["data"]["date"] = "2026-10-09"
        self.assert_rejected_without_write(job, 400, out_of_range)
        mixed = self.import_payload(job)
        mixed["items"].append({"kind": "food", "data": {"name": "不能替换为美食"}})
        self.assert_rejected_without_write(job, 400, mixed)

    def test_invalid_replan_requests_do_not_create_jobs_or_modify_data(self):
        invalid = [
            {"planning_mode": "unknown"},
            {"kinds": ["itinerary", "food"]},
            {"start_date": "2026-02-30"},
            {"planning_mode": "replace_day", "target_date": ""},
            {"planning_mode": "replace_day", "target_date": "2026-10-01", "days": 2},
            {"planning_mode": "replace_day", "target_date": "2026-10-02", "days": 1},
        ]
        before = self.business_state()
        for overrides in invalid:
            with self.subTest(overrides=overrides), self.assertRaises(ai.AIError):
                self.service.create_job(self.user, self.device, self.request(**overrides))
        self.assertEqual(self.business_state(), before)
        with server.connect_db(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0], 0)

    def test_deleted_user_or_renamed_city_cannot_replace(self):
        job = self.ready()
        server.admin_change(self.path, "DELETE", "users", {"user_id": self.user})
        self.assert_rejected_without_write(job, 401)
        server.claim_identity(self.path, {"user_id": self.user})
        server.admin_change(self.path, "PUT", "cities", {"id": "beijing", "name": "改名后的旅行城市"})
        self.assert_rejected_without_write(job, 409)

    def test_deleted_empty_city_cannot_receive_an_old_replacement(self):
        city = server.create_city(self.path, {"name": "临时测试城市"}, self.user)["city"]
        job = self.ready(city_id=city["id"])
        server.admin_change(self.path, "DELETE", "cities", {"id": city["id"]})
        self.assert_rejected_without_write(job, 404)

    def test_transaction_rechecks_city_after_service_context_validation(self):
        job = self.ready()
        callback = self.service._import_items
        after_rename = []

        def rename_then_import(*args):
            server.admin_change(self.path, "PUT", "cities", {"id": "beijing", "name": "导入瞬间改名"})
            after_rename.append(self.business_state())
            return callback(*args)

        with patch.object(self.service, "_import_items", side_effect=rename_then_import):
            with self.assertRaises(server.ApiError) as failure:
                self.import_job(job)
        self.assertEqual(failure.exception.status, 409)
        self.assertEqual(self.business_state(), after_rename[0])

    def test_a_second_saved_proposal_cannot_overwrite_the_first_accepted_one(self):
        whole_trip = self.ready()
        one_day = self.ready_day()
        self.import_job(one_day)
        self.assert_rejected_without_write(whole_trip, 409)

    def test_replacement_insert_failure_rolls_back_old_items_activity_and_receipt(self):
        job = self.ready()
        original = server.ensure_item_capacity
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise server.ApiError(507, "test capacity failure after first insertion")
            return original(*args, **kwargs)

        with patch.object(server, "ensure_item_capacity", side_effect=fail_second):
            self.assert_rejected_without_write(job, 507)
        self.assertEqual(calls, 2)
        self.assertEqual(self.import_job(job)["deleted"], 3)

    def test_receipt_retry_after_commit_does_not_delete_later_added_items(self):
        job = self.ready()
        result = self.import_job(job)
        self.add(day="2026-10-01", title="导入成功后才添加的安排")
        before = self.business_state()
        self.assertEqual(self.import_job(job), result)
        with server.connect_db(self.path) as db:
            db.execute("UPDATE ai_jobs SET status='ready',import_json=NULL WHERE id=?", (job["id"],))
        self.assertEqual(self.import_job(job), result)
        self.assertEqual(self.business_state(), before)

    def test_other_project_is_unchanged_and_cannot_import_the_job(self):
        store = ProjectStore(self.path, server.init_database)
        child = store.create("隔离项目", "child-test-code")
        child_path = store.resolve(child["id"])
        server.claim_identity(child_path, {"user_id": self.user})
        self.add(path=child_path, title="其他项目保留的安排")
        child_service = self.make_service(child_path)
        job = self.ready()
        before = self.business_state(child_path)
        with self.assertRaises(ai.AIError) as error:
            self.import_job(job, service=child_service)
        self.assertEqual(error.exception.status, 404)
        self.import_job(job)
        self.assertEqual(self.business_state(child_path), before)


if __name__ == "__main__":
    unittest.main()
