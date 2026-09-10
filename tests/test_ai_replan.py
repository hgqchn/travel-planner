from __future__ import annotations

import copy
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ai_service as ai
import server


def itinerary(day, title="漫步外滩"):
    return {"date": day, "start_time": "10:00", "title": title, "category": "观光",
            "location": "外滩", "notes": "预留步行与休息时间。"}


def output(items):
    return {"schema_version": 1, "summary": "重新安排路线", "notices": [],
            "attractions": [], "foods": [], "itineraries": items}


class FakeReplanService(ai.AIService):
    def _generate(self, request, context):
        start = date.fromisoformat(request["start_date"])
        items = [itinerary((start + timedelta(days=offset)).isoformat())
                 for offset in range(request["days"])]
        return {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(output(items))}]}]}, "json_schema"


class AIReplanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "app.db"
        self.services = []
        self.imports = []
        self.context = {
            "city": {"id": "shanghai", "name": "上海"},
            "existing": {"attraction": [], "food": [], "itinerary": [
                itinerary("2026-10-01"), itinerary("2026-10-02", "保留的博物馆安排")]},
            "itinerary_snapshot": [{"id": "a" * 12, "version": 2, "date": "2026-10-01"},
                                   {"id": "b" * 12, "version": 1, "date": "2026-10-02"}],
        }

    def tearDown(self):
        for service in self.services:
            service.close()
        self.temp.cleanup()

    def service(self, cls=FakeReplanService):
        def import_items(city, user, items, job_id):
            self.imports.append(copy.deepcopy(items))
            return {"imported": len(items), "skipped": 0}

        service = cls(self.db_path, api_key="mock-only-secret", cooldown_seconds=0,
                      get_context=lambda _: copy.deepcopy(self.context),
                      normalize_item=server.validate_payload, import_items=import_items)
        self.services.append(service)
        return service

    def request(self, **changes):
        request = {"city_id": "shanghai", "kinds": ["itinerary"], "planning_mode": "replace_all",
                   "start_date": "2026-10-01", "days": 2, "people": 1}
        request.update(changes)
        return request

    def ready(self, service, **changes):
        job = service.create_job("user", "device", self.request(**changes))
        end = time.monotonic() + 3
        while time.monotonic() < end:
            job = service.get_job("user", "device", job["id"])
            if job["status"] not in {"queued", "running"}:
                self.assertEqual(job["status"], "ready", job["error"])
                return job
            time.sleep(0.01)
        self.fail("Mock AI task did not finish")

    def import_data(self, job):
        return {"confirm_replace": True,
                "items": [{"kind": "itinerary", "data": item} for item in job["result"]["itineraries"]]}

    def test_request_modes_require_itinerary_and_exact_single_date(self):
        service = self.service()
        invalid = [
            {"planning_mode": "remove"}, {"planning_mode": None},
            {"kinds": ["food"]}, {"kinds": ["itinerary", "food"]},
            {"planning_mode": "replace_day"},
            {"planning_mode": "replace_day", "target_date": "2026-02-30", "days": 1},
            {"planning_mode": "replace_day", "target_date": "2026-10-01"},
            {"planning_mode": "replace_day", "target_date": "2026-10-02", "days": 1},
            {"target_date": "2026-10-01"}, {"days": 0}, {"days": 8},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ai.AIError) as error:
                service.create_job("user", "device", self.request(**changes))
            self.assertEqual(error.exception.status, 400)
        request = service._validate_request(self.request(planning_mode="replace_day", days=1,
                                                         target_date="2026-10-01"))
        self.assertEqual(request["target_date"], request["start_date"])
        self.assertEqual(request["people"], 1)
        self.assertEqual(service._validate_request({"city_id": "shanghai", "kinds": ["food"]})["planning_mode"], "append")

    def test_snapshot_is_required_only_for_replanning_and_validated(self):
        service = self.service()
        self.context.pop("itinerary_snapshot")
        with self.assertRaises(ai.AIError) as error:
            service.create_job("user", "device", self.request())
        self.assertEqual(error.exception.status, 409)
        self.ready(service, planning_mode="append")
        bad_snapshots = [None, {}, [{"id": "bad", "version": 1, "date": "2026-10-01"}],
                         [{"id": "a" * 12, "version": True, "date": "2026-10-01"}],
                         [{"id": "a" * 12, "version": 1, "date": "2026-02-30"}],
                         [{"id": "a" * 12, "version": 1, "date": "2026-10-01"}] * 2]
        for snapshot in bad_snapshots:
            self.context["itinerary_snapshot"] = snapshot
            with self.subTest(snapshot=snapshot), self.assertRaises(ai.AIError):
                service._context("shanghai")

    def test_day_scope_is_persisted_and_public_does_not_expose_item_ids(self):
        service = self.service()
        job = self.ready(service, planning_mode="replace_day", target_date="2026-10-01", days=1)
        self.assertEqual(job["replacement"], {"planning_mode": "replace_day", "target_date": "2026-10-01", "count": 1})
        with sqlite3.connect(self.db_path) as db:
            context = json.loads(db.execute("SELECT context_json FROM ai_jobs WHERE id=?", (job["id"],)).fetchone()[0])
        self.assertEqual(context["replacement"]["items"], [{"id": "a" * 12, "version": 2}])
        self.assertEqual(context["itinerary_snapshot"], self.context["itinerary_snapshot"])
        self.assertNotIn("a" * 12, json.dumps(job))
        self.assertNotIn("b" * 12, json.dumps(job))
        service.close()
        self.context["itinerary_snapshot"] = []
        restarted = self.service()
        restored = restarted.get_job("user", "device", job["id"])
        self.assertEqual(restored["replacement"], job["replacement"])
        self.assertEqual(restored["status"], "ready")

    def test_full_scope_retains_untruncated_snapshot_including_dates_outside_new_range(self):
        service = self.service()
        self.context["itinerary_snapshot"] = [{"id": f"{number:012x}", "version": 1, "date": "2026-10-20"}
                                               for number in range(150)]
        self.context["existing"]["itinerary"] = [itinerary("2026-10-20", str(number)) for number in range(150)]
        job = self.ready(service)
        with sqlite3.connect(self.db_path) as db:
            context = json.loads(db.execute("SELECT context_json FROM ai_jobs WHERE id=?", (job["id"],)).fetchone()[0])
        self.assertEqual(len(context["replacement"]["items"]), 150)
        self.assertEqual(len(context["itinerary_snapshot"]), 150)
        self.assertEqual(job["replacement"]["count"], 150)
        self.assertEqual(job["request"]["target_date"], "")

    def test_replaced_existing_item_is_not_duplicate_but_new_internal_duplicate_is(self):
        service = self.service()
        context = service._context("shanghai")
        raw = output([itinerary("2026-10-01"), itinerary("2026-10-01")])
        request = service._validate_request(self.request(planning_mode="replace_day", target_date="2026-10-01", days=1))
        planned = service._validate_result(raw, request, context)["itineraries"]
        self.assertFalse(planned[0]["duplicate"])
        self.assertTrue(planned[1]["duplicate"])
        request["planning_mode"] = "append"
        self.assertTrue(service._validate_result(raw, request, context)["itineraries"][0]["duplicate"])

    def test_generation_requires_every_day_and_rejects_other_dates_or_kinds(self):
        service = self.service()
        context = service._context("shanghai")
        request = service._validate_request(self.request())
        cases = [output([]), output([itinerary("2026-10-01")]), output([itinerary("2026-10-03")])]
        wrong_kind = output([itinerary("2026-10-01"), itinerary("2026-10-02")])
        wrong_kind["foods"] = [{"name": "生煎", "category": "小吃", "description": "", "where_to_try": "", "tip": ""}]
        cases.append(wrong_kind)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ai.AIError):
                service._validate_result(value, request, context)
        complete = output([itinerary("2026-10-01"), itinerary("2026-10-02")])
        self.assertEqual(len(service._validate_result(complete, request, context)["itineraries"]), 2)

    def test_import_requires_explicit_confirmation_and_complete_edited_dates(self):
        service = self.service()
        job = self.ready(service)
        valid = self.import_data(job)
        for confirm in (False, None, "true", 1):
            with self.subTest(confirm=confirm), self.assertRaises(ai.AIError) as error:
                service.import_job("user", "device", job["id"], dict(valid, confirm_replace=confirm))
            self.assertEqual(error.exception.status, 400)
        invalid = [dict(valid, items=valid["items"][:1]), dict(valid, items=[])]
        shifted = copy.deepcopy(valid)
        shifted["items"][1]["data"]["date"] = "2026-10-03"
        invalid.append(shifted)
        wrong_kind = copy.deepcopy(valid)
        wrong_kind["items"][0]["kind"] = "food"
        invalid.append(wrong_kind)
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ai.AIError):
                service.import_job("user", "device", job["id"], data)
        self.assertEqual(self.imports, [])
        result = service.import_job("user", "device", job["id"], valid)
        self.assertEqual(result["imported"], 2)
        self.assertEqual(len(self.imports), 1)
        self.assertEqual(service.import_job("user", "device", job["id"], {}), result)
        self.assertEqual(len(self.imports), 1)

    def test_provider_receives_business_context_with_distinct_replaced_and_retained_days(self):
        service = self.service(cls=ai.AIService)
        service._post = Mock(return_value={})
        request = service._validate_request(self.request(planning_mode="replace_day", target_date="2026-10-01", days=1))
        context = service._context("shanghai")
        context["replacement"] = {"planning_mode": "replace_day", "target_date": "2026-10-01",
                                  "items": [{"id": "a" * 12, "version": 2}]}
        service._generate(request, context)
        payload = service._post.call_args.args[0]
        parsed = json.loads(payload["input"])
        self.assertEqual(parsed["city"], {"name": "上海"})
        self.assertNotIn("city_id", parsed["request"])
        self.assertEqual(parsed["existing"]["itinerary"][0]["date"], "2026-10-02")
        self.assertEqual(parsed["replacement"]["previous_itinerary"][0]["date"], "2026-10-01")
        self.assertEqual(parsed["replacement"]["previous_itinerary"][0]["notes"], "预留步行与休息时间。")
        for secret in ("a" * 12, "b" * 12, "version", "itinerary_snapshot", "mock-only-secret"):
            self.assertNotIn(secret, payload["input"])
        self.assertIn("replace_day", payload["instructions"])
        self.assertEqual(payload["model"], ai.DEFAULT_MODEL)

    def test_single_day_reference_survives_business_context_limit(self):
        service = self.service()
        self.context["existing"]["itinerary"] = [itinerary("2026-10-02", f"旧安排 {number}") for number in range(150)]
        self.context["existing"]["itinerary"].append(itinerary("2026-10-01", "选中日期的保留参考"))
        request = service._validate_request(self.request(planning_mode="replace_day", target_date="2026-10-01", days=1))
        packed = service._context("shanghai", planning_request=request)
        self.assertEqual(packed["existing"]["itinerary"][0]["title"], "选中日期的保留参考")
        self.assertLessEqual(len(packed["existing"]["itinerary"]), 100)
        self.assertEqual(packed["itinerary_snapshot"], self.context["itinerary_snapshot"])


if __name__ == "__main__":
    unittest.main()
