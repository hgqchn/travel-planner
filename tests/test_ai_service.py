from __future__ import annotations

import copy
import io
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import ai_service as ai  # noqa: E402
import server  # noqa: E402


def suggestion():
    return {"schema_version": 1, "summary": "沿着黄浦江慢慢走。", "notices": ["出行前核实开放信息。"],
            "attractions": [{"name": "外滩", "district": "黄浦区", "category": "城市风光",
                             "description": "沿江漫步。", "duration": "1小时", "transport": "地铁后步行"}],
            "foods": [{"name": "生煎", "category": "本地小吃", "description": "尝试本地风味。",
                       "where_to_try": "市区老字号", "tip": "小心烫口。"}],
            "itineraries": [{"date": "2026-10-01", "start_time": "10:00", "title": "漫步外滩",
                             "category": "观光", "location": "外滩", "notes": "预留步行与休息时间。"}]}


def provider_response(value=None):
    return {"status": "completed", "output": [
        {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "private reasoning"}]},
        {"type": "message", "status": "completed", "content": [
            {"type": "output_text", "text": json.dumps(suggestion() if value is None else value)}]}],
        "usage": {"input_tokens": 100, "output_tokens": 200, "total_tokens": 300,
                  "unexpected_field": "not public"}}


class FakeService(ai.AIService):
    def _generate(self, request, context):
        value = suggestion()
        for kind, plural in ai.KINDS.items():
            if kind not in request["kinds"]:
                value[plural] = []
        return provider_response(value), "json_schema"


class AIServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "app.db"
        self.services = []
        self.receipts = {}
        self.imported_items = []
        self.context = {"city": {"id": "shanghai", "name": "上海"},
                        "existing": {"attraction": [{"data": {"name": "外滩"}}],
                                     "food": [], "itinerary": []}}

    def tearDown(self):
        for service in self.services:
            service.close()
        self.temp.cleanup()

    def make_service(self, cls=FakeService, **kwargs):
        def import_items(city_id, user_id, items, job_id):
            if job_id not in self.receipts:
                self.imported_items.extend(items)
                self.receipts[job_id] = {"imported": len(items), "skipped": 0}
            return self.receipts[job_id]
        options = {"api_key": "test-provider-secret", "model": ai.DEFAULT_MODEL,
                   "get_context": lambda city_id: copy.deepcopy(self.context),
                   "normalize_item": server.validate_payload, "import_items": import_items,
                   "cooldown_seconds": 0}
        options.update(kwargs)
        service = cls(self.db_path, **options)
        self.services.append(service)
        return service

    def request(self, **overrides):
        value = {"city_id": "shanghai", "kinds": list(ai.KINDS), "days": 3,
                 "start_date": "2026-10-01", "people": 2, "budget": "人均1000元",
                 "preferences": ["小吃", "漫步"], "requirements": "尽量轻松"}
        value.update(overrides)
        return value

    def wait_job(self, service, job, user="测试用户", device="device-a"):
        end = time.monotonic() + 4
        while time.monotonic() < end:
            job = service.get_job(user, device, job["id"])
            if job["status"] not in {"queued", "running"}:
                return job
            time.sleep(0.01)
        self.fail("AI job did not finish")

    def ready(self, service, **overrides):
        job = service.create_job("测试用户", "device-a", self.request(**overrides))
        job = self.wait_job(service, job)
        self.assertEqual(job["status"], "ready", job["error"])
        return job

    def test_selected_model_is_persisted_sent_and_part_of_idempotency(self):
        service = self.make_service(cls=ai.AIService)
        service._post = Mock(return_value=provider_response())
        job = self.ready(service, model="custom-model-id", request_id="model-choice")
        self.assertEqual(job["model"], "custom-model-id")
        self.assertEqual(job["request"]["model"], "custom-model-id")
        self.assertEqual(service._post.call_args.args[0]["model"], "custom-model-id")
        self.assertEqual(service.model, ai.DEFAULT_MODEL)
        same = service.create_job("测试用户", "device-a", self.request(model="custom-model-id", request_id="model-choice"))
        self.assertEqual(same["id"], job["id"])
        with self.assertRaises(ai.AIError) as caught:
            service.create_job("测试用户", "device-a", self.request(model=ai.DEFAULT_MODEL, request_id="model-choice"))
        self.assertEqual(caught.exception.status, 409)
        with self.assertRaises(ai.AIError):
            service.create_job("测试用户", "device-a", self.request(model="invalid model id"))
        self.assertEqual(service._post.call_count, 1)

    def test_generation_validation_duplicates_and_usage(self):
        service = self.make_service()
        job = self.ready(service)
        self.assertTrue(job["result"]["attractions"][0]["duplicate"])
        self.assertFalse(job["result"]["foods"][0]["duplicate"])
        self.assertIn("uri.amap.com/search?", job["result"]["attractions"][0]["navigation_link"])
        from urllib.parse import parse_qs, urlparse
        navigation = parse_qs(urlparse(job["result"]["attractions"][0]["navigation_link"]).query)
        self.assertEqual(navigation["callnative"], ["1"])
        self.assertEqual(navigation["view"], ["list"])
        self.assertEqual(navigation["src"], ["travelplanner"])
        self.assertEqual(job["result"]["foods"][0]["link"], "")
        self.assertEqual(job["usage"], {"input_tokens": 100, "output_tokens": 200, "total_tokens": 300})
        encoded = json.dumps(job)
        self.assertNotIn("test-provider-secret", encoded)
        self.assertNotIn("private reasoning", encoded)
        self.assertNotIn("session_hash", encoded)

    def test_owner_and_device_are_both_required(self):
        service = self.make_service()
        job = self.ready(service)
        for user, device in (("其他用户", "device-a"), ("测试用户", "device-b")):
            with self.assertRaises(ai.AIError) as error:
                service.get_job(user, device, job["id"])
            self.assertEqual(error.exception.status, 404)
            with self.assertRaises(ai.AIError):
                service.import_job(user, device, job["id"], {"items": []})

    def test_request_id_prevents_duplicate_paid_generations(self):
        service = self.make_service()
        job = self.ready(service, request_id="button-click-1")
        repeat = service.create_job("测试用户", "device-a", self.request(request_id="button-click-1"))
        self.assertEqual(repeat["id"], job["id"])
        with self.assertRaises(ai.AIError) as error:
            service.create_job("测试用户", "device-a", self.request(request_id="button-click-1", days=2))
        self.assertEqual(error.exception.status, 409)

    def test_import_edited_items_is_city_bound_and_idempotent(self):
        service = self.make_service()
        job = self.ready(service)
        food = dict(job["result"]["foods"][0], name="修改过的生煎推荐")
        items = [{"kind": "food", "data": food}]
        with self.assertRaises(ai.AIError):
            service.import_job("测试用户", "device-a", job["id"], {"items": items, "city_id": "beijing"})
        result = service.import_job("测试用户", "device-a", job["id"], {"items": items})
        repeat = service.import_job("测试用户", "device-a", job["id"], {"items": items})
        self.assertEqual(result, repeat)
        self.assertEqual(len(self.imported_items), 1)
        self.assertEqual(self.imported_items[0]["data"]["name"], "修改过的生煎推荐")
        self.assertNotIn("duplicate", self.imported_items[0]["data"])
        self.assertEqual(service.get_job("测试用户", "device-a", job["id"])["status"], "imported")
        # Simulate death after the application callback commits but before this
        # module saves the receipt. A transactional callback makes retry safe.
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE ai_jobs SET status='ready',import_json=NULL WHERE id=?", (job["id"],))
        again = service.import_job("测试用户", "device-a", job["id"], {"items": items})
        self.assertEqual(again, result)
        self.assertEqual(len(self.imported_items), 1)

    def test_import_rejects_other_kinds_dates_and_unknown_metadata(self):
        service = self.make_service()
        job = self.ready(service, kinds=["food"])
        with self.assertRaises(ai.AIError):
            service.import_job("测试用户", "device-a", job["id"],
                               {"items": [{"kind": "attraction", "data": suggestion()["attractions"][0]}]})
        for field in ("city_id", "author", "created_at", "id"):
            with self.assertRaises(ai.AIError):
                service.import_job("测试用户", "device-a", job["id"], {"items": [
                    {"kind": "food", "data": dict(suggestion()["foods"][0], **{field: "injected"})}]})
        dated = self.ready(service)
        bad = dict(suggestion()["itineraries"][0], date="2026-10-10")
        with self.assertRaises(ai.AIError):
            service.import_job("测试用户", "device-a", dated["id"],
                               {"items": [{"kind": "itinerary", "data": bad}]})
        self.assertEqual(self.imported_items, [])

    def test_import_rejects_substantial_city_rename_with_same_database_id(self):
        service = self.make_service()
        job = self.ready(service, kinds=["food"])
        self.context["city"]["name"] = "北京"
        items = [{"kind": "food", "data": job["result"]["foods"][0]}]
        with self.assertRaises(ai.AIError) as error:
            service.import_job("测试用户", "device-a", job["id"], {"items": items})
        self.assertEqual(error.exception.status, 409)
        self.assertIn("重新生成", error.exception.message)
        self.assertEqual(self.imported_items, [])
        self.assertEqual(self.receipts, {})
        self.assertEqual(service.get_job("测试用户", "device-a", job["id"])["status"], "ready")

    def test_import_allows_explicit_city_alias_after_rename(self):
        service = self.make_service()
        job = self.ready(service, kinds=["food"])
        self.context["city"]["name"] = "上海市"
        items = [{"kind": "food", "data": job["result"]["foods"][0]}]
        result = service.import_job("测试用户", "device-a", job["id"], {"items": items})
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["city_id"], "shanghai")
        # An already committed receipt remains retrievable after a later rename;
        # retry performs no additional write and must not charge or import again.
        self.context["city"]["name"] = "北京"
        self.assertEqual(service.import_job("测试用户", "device-a", job["id"], {"items": items}), result)
        self.assertEqual(len(self.imported_items), 1)

    def test_custom_city_rename_is_not_fuzzily_matched(self):
        service = self.make_service()
        self.context["city"]["name"] = "旅行小镇"
        job = self.ready(service, kinds=["food"])
        items = [{"kind": "food", "data": job["result"]["foods"][0]}]
        self.context["city"]["name"] = "旅行小镇市"
        with self.assertRaises(ai.AIError) as error:
            service.import_job("测试用户", "device-a", job["id"], {"items": items})
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.imported_items, [])
        self.context["city"]["name"] = " 旅行小镇 "
        self.assertEqual(service.import_job("测试用户", "device-a", job["id"], {"items": items})["imported"], 1)

    def test_invalid_model_structure_has_no_partial_import(self):
        service = self.make_service()
        examples = []
        wrong = suggestion()
        wrong["foods"][0]["author"] = "admin"
        examples.append(wrong)
        wrong = suggestion()
        wrong["itineraries"][0]["date"] = "2026-10-20"
        examples.append(wrong)
        wrong = suggestion()
        wrong["foods"][0]["description"] = 123
        examples.append(wrong)
        wrong = suggestion()
        wrong["foods"] *= 13
        examples.append(wrong)
        wrong = suggestion()
        wrong["schema_version"] = True
        examples.append(wrong)
        for value in examples:
            with self.subTest(value=value):
                with self.assertRaises((ai.AIError, server.ApiError)):
                    service._validate_result(value, self.request(), self.context)
        self.assertEqual(self.imported_items, [])

    def test_request_validation_prevents_unbounded_input(self):
        service = self.make_service()
        for values in ({"days": 8}, {"days": True}, {"people": 0}, {"kinds": ["transit"]},
                       {"kinds": ["food", "food"]}, {"start_date": "2026-02-30"},
                       {"start_date": ""}, {"requirements": "a" * 2001}, {"budget": float("nan")}):
            with self.subTest(values=values), self.assertRaises(ai.AIError):
                service.create_job("测试用户", "device-a", self.request(**values))
        job = self.ready(service, kinds=["food"], start_date="", budget=None)
        self.assertEqual(job["result"]["itineraries"], [])
        self.assertEqual(job["request"]["budget"], "")

    def test_restart_keeps_ready_jobs_and_fails_unfinished_jobs(self):
        service = self.make_service()
        ready = self.ready(service)
        pending = self.ready(service, request_id="pending")
        service.close()
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE ai_jobs SET status='running' WHERE id=?", (pending["id"],))
        replacement = self.make_service()
        self.assertEqual(replacement.get_job("测试用户", "device-a", ready["id"])["status"], "ready")
        old = replacement.get_job("测试用户", "device-a", pending["id"])
        self.assertEqual(old["status"], "failed")
        self.assertIn("重启", old["error"])

    def test_daily_generation_count_is_unlimited_for_same_user_and_device(self):
        service = self.make_service()
        jobs = [self.ready(service) for _ in range(25)]
        self.assertEqual(len({job["id"] for job in jobs}), 25)
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0], 25)
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                        "AND name IN ('ai_daily_usage','ai_device_usage')").fetchall(), [])

    def test_legacy_daily_counts_do_not_restrict_jobs_after_restart(self):
        service = self.make_service()
        self.ready(service)
        service.close()
        owner, session_hash = ai.AIService._owner("测试用户", "device-a")
        quota_owners = ("device:" + session_hash, "user:" + ai.hashlib.sha256(owner.encode()).hexdigest())
        with sqlite3.connect(self.db_path) as db:
            db.executescript("""
                CREATE TABLE ai_daily_usage (day_key TEXT PRIMARY KEY, total INTEGER NOT NULL);
                CREATE TABLE ai_device_usage (
                    day_key TEXT NOT NULL, owner TEXT NOT NULL, total INTEGER NOT NULL,
                    PRIMARY KEY(day_key, owner)
                );
            """)
            db.execute("INSERT INTO ai_daily_usage VALUES (?,1000)", (ai._day_key(),))
            db.executemany("INSERT INTO ai_device_usage VALUES (?,?,1000)",
                           [(ai._day_key(), quota_owner) for quota_owner in quota_owners])
        replacement = self.make_service()
        for user, device in (("测试用户", "device-a"), ("其他用户", "device-a"),
                             ("测试用户", "device-b"), ("第三用户", "device-c")):
            job = replacement.create_job(user, device, self.request())
            self.assertEqual(self.wait_job(replacement, job, user, device)["status"], "ready")
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute("SELECT total FROM ai_daily_usage").fetchall(), [(1000,)])
            self.assertEqual(db.execute("SELECT total FROM ai_device_usage").fetchall(), [(1000,), (1000,)])

    def test_cooldown_and_missing_key(self):
        disabled = self.make_service(api_key="")
        with self.assertRaises(ai.AIError) as error:
            disabled.create_job("测试用户", "device-a", self.request())
        self.assertEqual(error.exception.status, 503)
        disabled.close()
        service = self.make_service(cooldown_seconds=45)
        self.ready(service)
        with self.assertRaises(ai.AIError) as error:
            service.create_job("测试用户", "device-a", self.request())
        self.assertEqual(error.exception.status, 429)
        self.assertGreater(error.exception.extra["retry_after"], 0)

    def test_bounded_queue_and_active_job_limit(self):
        started = threading.Event()
        release = threading.Event()

        class SlowService(FakeService):
            def _generate(self, request, context):
                started.set()
                release.wait(3)
                return super()._generate(request, context)

        service = self.make_service(cls=SlowService, queue_size=1)
        try:
            first = service.create_job("测试用户", "device-a", self.request())
            self.assertTrue(started.wait(1))
            with self.assertRaises(ai.AIError):
                service.create_job("其他用户", "device-a", self.request())
            service.create_job("其他用户", "device-b", self.request())
            with self.assertRaises(ai.AIError) as error:
                service.create_job("第三用户", "device-c", self.request())
            self.assertIn("队列已满", error.exception.message)
            with self.assertRaises(ai.AIError):
                service.import_job("测试用户", "device-a", first["id"], {"items": []})
        finally:
            release.set()
        self.assertEqual(self.wait_job(service, first)["status"], "ready")

    def test_background_error_does_not_disclose_key_or_prompt(self):
        service = self.make_service()
        service._generate = Mock(side_effect=ValueError("test-provider-secret user-private-prompt"))
        job = service.create_job("测试用户", "device-a", self.request())
        job = self.wait_job(service, job)
        self.assertEqual(job["status"], "failed")
        self.assertNotIn("test-provider-secret", json.dumps(job))
        self.assertNotIn("user-private-prompt", json.dumps(job))

    def test_extract_rejects_empty_truncated_invalid_json_and_wrong_status(self):
        examples = [({"status": "completed", "output": []}, "没有返回内容"),
                    ({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}, "截断"),
                    ({"status": "failed", "error": {"message": "secret"}}, "未完成"),
                    ({"status": "completed", "output": [{"type": "message", "content": [
                        {"type": "output_text", "text": "```json\n{}\n```"}]}]}, "JSON")]
        for response, expected in examples:
            with self.subTest(expected=expected), self.assertRaises(ai.AIError) as error:
                ai.AIService._extract_response(response)
            self.assertIn(expected, error.exception.message)

    def test_http_error_messages_are_safe_and_actionable(self):
        service = self.make_service(cls=ai.AIService)
        cases = [(401, "invalid key test-provider-secret", "API Key"),
                 (402, "balance", "余额不足"), (429, "limit", "频繁"),
                 (404, "model missing", "模型不可用"), (503, "unavailable", "暂时不可用")]
        for code, body, expected in cases:
            service._opener = Mock()
            service._opener.open.side_effect = urllib.error.HTTPError(
                "https://api.deepseek.com/responses", code, "test", {}, io.BytesIO(body.encode()))
            with self.subTest(code=code), self.assertRaises(ai.AIError) as error:
                service._post({})
            self.assertIn(expected, error.exception.message)
            self.assertNotIn("test-provider-secret", error.exception.message)
        service._opener.open.side_effect = TimeoutError()
        with self.assertRaises(ai.AIError) as error:
            service._post({})
        self.assertEqual(error.exception.status, 504)

    def test_fallback_only_for_explicit_schema_rejection_and_preserves_model(self):
        service = self.make_service(cls=ai.AIService)
        calls = []

        def post(payload, **kwargs):
            calls.append(copy.deepcopy(payload))
            if len(calls) == 1:
                raise ai._SchemaUnsupported()
            return provider_response()

        service._post = post
        _, mode = service._generate(self.request(), self.context)
        self.assertEqual(mode, "json_object")
        self.assertEqual([value["model"] for value in calls], [ai.DEFAULT_MODEL, ai.DEFAULT_MODEL])
        self.assertEqual(calls[0]["text"]["format"]["type"], "json_schema")
        self.assertEqual(calls[1]["text"]["format"]["type"], "json_object")
        service._post = Mock(side_effect=ai.AIError(504, "超时"))
        with self.assertRaises(ai.AIError):
            service._generate(self.request(), self.context)
        self.assertEqual(service._post.call_count, 1)

    def test_http_schema_fallback_classification_and_bounded_reader(self):
        service = self.make_service(cls=ai.AIService)
        for body, can_fallback in (("json_schema is not supported", True),
                                   ("invalid JSON Schema", False), ("model not supported", False)):
            service._opener = Mock()
            service._opener.open.side_effect = urllib.error.HTTPError(
                "https://api.deepseek.com/responses", 400, "test", {}, io.BytesIO(body.encode()))
            with self.subTest(body=body), self.assertRaises(ai._SchemaUnsupported if can_fallback else ai.AIError):
                service._post({})
        service._opener.open.side_effect = None
        service._opener.open.return_value = io.BytesIO(json.dumps(provider_response()).encode())
        self.assertEqual(service._post({})["status"], "completed")
        service._opener.open.return_value = io.BytesIO(b" " * (ai.MAX_RESPONSE_BYTES + 1))
        with self.assertRaises(ai.AIError) as error:
            service._post({})
        self.assertIn("过大", error.exception.message)
        service._timeout_seconds = -1
        service._opener.open.return_value = io.BytesIO(b" ")
        with self.assertRaises(ai.AIError) as error:
            service._post({})
        self.assertEqual(error.exception.status, 504)


if __name__ == "__main__":
    unittest.main()
