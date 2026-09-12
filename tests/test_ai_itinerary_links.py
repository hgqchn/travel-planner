from __future__ import annotations

import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import ai_service as ai
import server


def itinerary(day="2026-10-01", names=None, title="游览外滩"):
    return {"date": day, "start_time": "10:00", "title": title, "category": "观光",
            "location": "外滩", "notes": "步行游览并预留休息时间。",
            **({"attraction_names": names} if names is not None else {})}


def output(*items):
    return {"schema_version": 1, "summary": "安排景点游览", "notices": [],
            "attractions": [], "foods": [], "itineraries": list(items)}


class AIItineraryLinksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.context = {"city": {"id": "shanghai", "name": "上海"}, "existing": {
            "attraction": [
                {"name": "外滩", "category": "城市地标", "tags": ["江景"], "description": "历史建筑与江景", "duration": "2 小时",
                 "in_itinerary": True, "itinerary_dates": ["2026-10-01"]},
                {"name": "上海博物馆", "category": "博物馆", "tags": ["历史文化"], "duration": "3 小时",
                 "in_itinerary": True, "itinerary_dates": ["2026-10-02"]},
                {"name": "豫园", "category": "园林公园", "in_itinerary": False, "itinerary_dates": []},
            ], "food": [], "itinerary": [itinerary(names=["外滩"]), itinerary("2026-10-02", ["上海博物馆"], "参观上海博物馆")],
        }, "itinerary_snapshot": [{"id": "a" * 12, "version": 1, "date": "2026-10-01"},
                                   {"id": "b" * 12, "version": 1, "date": "2026-10-02"}]}
        self.imported = []

        def import_items(city, user, items, job_id):
            self.imported.extend(copy.deepcopy(items))
            return {"created": len(items), "skipped": 0}

        self.service = ai.AIService(Path(self.temp.name) / "test.db", api_key="fake-local-key", cooldown_seconds=0,
                                   get_context=lambda city: copy.deepcopy(self.context),
                                   normalize_item=server.validate_payload, import_items=import_items)
        self.service._post = Mock()

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def request(self, **changes):
        value = {"city_id": "shanghai", "kinds": ["itinerary"], "start_date": "2026-10-01", "days": 2}
        value.update(changes)
        return self.service._validate_request(value)

    def prompt(self, request=None):
        request = request or self.request()
        context = self.service._context("shanghai", planning_request=request)
        self.service._generate(request, context)
        payload = self.service._post.call_args.args[0]
        return json.loads(payload["input"]), payload, context

    def ready(self, result, **changes):
        self.service._post.return_value = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(result, ensure_ascii=False)}]}]}
        job = self.service.create_job("Alice", "device", self.request(**changes))
        deadline = time.monotonic() + 3
        while job["status"] in ("queued", "running") and time.monotonic() < deadline:
            time.sleep(.01)
            job = self.service.get_job("Alice", "device", job["id"])
        self.assertEqual(job["status"], "ready", job["error"])
        return job

    def test_itinerary_only_schema_and_prompt_distinguish_real_visits_from_routes(self):
        prompt, payload, _ = self.prompt()
        schema = payload["text"]["format"]["schema"]["properties"]["itineraries"]["items"]
        self.assertIn("attraction_names", schema["required"])
        self.assertEqual(schema["properties"]["attraction_names"]["type"], "array")
        self.assertEqual(schema["properties"]["attraction_names"]["maxItems"], 1)
        self.assertEqual(schema["properties"]["attraction_names"]["items"]["maxLength"], 60)
        self.assertEqual(prompt["request"]["kinds"], ["itinerary"])
        for text in ("路口、普通街道、路线不是景点", "餐厅、酒店、车站", "纯交通", "真正参观、游览", "不限制在行程中游览已收录的景点", "不能强塞"):
            self.assertIn(text, payload["instructions"])

    def test_append_supplies_existing_place_details_and_scheduled_status(self):
        prompt, _, _ = self.prompt()
        attraction = prompt["existing"]["attraction"][0]
        self.assertEqual(attraction["category"], "城市地标")
        self.assertEqual(attraction["tags"], ["江景"])
        self.assertEqual(attraction["duration"], "2 小时")
        self.assertEqual(attraction["description"], "历史建筑与江景")
        self.assertEqual(attraction["itinerary_dates"], ["2026-10-01"])
        self.assertTrue(attraction["in_itinerary"])
        self.assertEqual(prompt["itinerary_links"], {"scheduled_attractions": ["外滩", "上海博物馆"], "unscheduled_attractions": ["豫园"]})
        self.assertEqual(prompt["existing"]["itinerary"][0]["attraction_names"], ["外滩"])

    def test_replace_day_releases_its_old_visits_but_preserves_other_dates(self):
        request = self.request(planning_mode="replace_day", target_date="2026-10-01", days=1)
        prompt, _, _ = self.prompt(request)
        self.assertEqual(prompt["itinerary_links"], {"scheduled_attractions": ["上海博物馆"], "unscheduled_attractions": ["外滩", "豫园"]})
        self.assertFalse(prompt["existing"]["attraction"][0]["in_itinerary"])
        self.assertEqual(prompt["existing"]["attraction"][0]["itinerary_dates"], [])
        self.assertEqual(prompt["replacement"]["previous_itinerary"][0]["attraction_names"], ["外滩"])
        self.assertEqual(prompt["existing"]["itinerary"][0]["attraction_names"], ["上海博物馆"])
        self.context["existing"]["itinerary"][1]["attraction_names"].append("外滩")
        prompt, _, _ = self.prompt(request)
        self.assertIn("外滩", prompt["itinerary_links"]["scheduled_attractions"])
        self.assertEqual(prompt["existing"]["attraction"][0]["itinerary_dates"], ["2026-10-02"])

    def test_replace_all_keeps_references_but_marks_all_saved_places_available(self):
        prompt, _, _ = self.prompt(self.request(planning_mode="replace_all"))
        self.assertEqual(prompt["existing"]["itinerary"], [])
        self.assertEqual(prompt["itinerary_links"]["scheduled_attractions"], [])
        self.assertEqual(prompt["itinerary_links"]["unscheduled_attractions"], ["外滩", "上海博物馆", "豫园"])
        self.assertTrue(all(not item["in_itinerary"] and not item["itinerary_dates"] for item in prompt["existing"]["attraction"]))
        self.assertEqual(prompt["replacement"]["previous_itinerary"][1]["attraction_names"], ["上海博物馆"])

    def test_new_and_legacy_provider_results_survive_validation_and_import(self):
        for names in ([" 外滩 ", "外滩", "豫园"], None, []):
            with self.subTest(names=names):
                job = self.ready(output(itinerary(names=names)))
                expected = ["外滩", "豫园"] if names else []
                item = job["result"]["itineraries"][0]
                self.assertEqual(item["attraction_names"], expected)
                if names is None:
                    item.pop("attraction_names")  # Old ready drafts/clients also import.
                self.service.import_job("Alice", "device", job["id"], {"items": [{"kind": "itinerary", "data": item}]})
                self.assertEqual(self.imported[-1]["data"]["attraction_names"], expected)
                self.assertEqual(self.imported[-1]["kind"], "itinerary")

    def test_planning_schema_limits_each_visit_in_both_provider_modes(self):
        for fallback in (False, True):
            with self.subTest(fallback=fallback):
                self.service._post.side_effect = [ai._SchemaUnsupported(), {}] if fallback else None
                _, payload, _ = self.prompt()
                if fallback:
                    schema = json.loads(payload['instructions'].split('JSON Schema：')[-1])
                else:
                    schema = payload['text']['format']['schema']
                self.assertEqual(schema['properties']['itineraries']['items']['properties']['attraction_names']['maxItems'], 1)
                self.assertIn('一天可以安排多项行程', payload['instructions'])
                self.assertIn('同一时间段（time_block）也可以安排多项行程', payload['instructions'])
                self.assertIn('单地点规则同时适用于 title、location、notes 和 attraction_names', payload['instructions'])
                self.assertEqual(payload['reasoning']['effort'], 'high')
        self.assertEqual(ai.OUTPUT_SCHEMA['properties']['itineraries']['items']['properties']['attraction_names']['maxItems'], 12)

    def test_planning_deduplicates_names_but_rejects_distinct_combined_visits(self):
        for names in ([], ['外滩'], ['外滩', ' 外滩 '], ['外滩', '豫园']):
            with self.subTest(names=names):
                item = itinerary(names=names)
                item.pop('start_time')
                item.update(time_block='morning', duration_minutes=90, duration_source='ai_estimate',
                            opening_start='', opening_end='')
                value = dict(output(item), schema_version=2)
                if names == ['外滩', '豫园']:
                    with self.assertRaisesRegex(ai.AIError, '每项行程只能安排一个具体游览地点'):
                        self.service._validate_result(value, self.request(), self.context)
                else:
                    job = self.ready(value)
                    self.assertEqual(job['result']['itineraries'][0]['attraction_names'], ['外滩'] if names else [])

    def test_planning_allows_multiple_separate_visits_in_same_time_block(self):
        items = []
        for name in ('外滩', '豫园'):
            item = itinerary(names=[name], title='游览' + name)
            item.pop('start_time')
            item.update(location=name, time_block='morning', duration_minutes=90,
                        duration_source='ai_estimate', opening_start='', opening_end='')
            items.append(item)
        job = self.ready(dict(output(*items), schema_version=2))
        self.assertEqual(len(job['result']['itineraries']), 2)

    def test_result_and_import_reject_malformed_attraction_name_arrays(self):
        request = self.request()
        context = self.service._context("shanghai", planning_request=request)
        job = self.ready(output(itinerary(names=[])))
        for value in ("外滩", None, {}, [2], [False], [""], [" "], ["景" * 61], [f"景点 {i}" for i in range(13)]):
            with self.subTest(value=value):
                record = dict(itinerary(), attraction_names=value)
                with self.assertRaises(ai.AIError) as error:
                    self.service._validate_result(output(record), request, context)
                self.assertEqual(error.exception.status, 502)
                with self.assertRaises(ai.AIError) as error:
                    self.service.import_job("Alice", "device", job["id"], {"items": [{"kind": "itinerary", "data": record}]})
                self.assertEqual(error.exception.status, 400)
        self.assertEqual(self.imported, [])

    def test_replanned_day_preserves_explicit_visit_links_through_confirmed_import(self):
        job = self.ready(output(itinerary(names=["外滩", "豫园"])), planning_mode="replace_day",
                         target_date="2026-10-01", days=1)
        item = job["result"]["itineraries"][0]
        self.assertFalse(item["duplicate"])
        with self.assertRaises(ai.AIError):
            self.service.import_job("Alice", "device", job["id"], {"items": [{"kind": "itinerary", "data": item}]})
        self.service.import_job("Alice", "device", job["id"], {"confirm_replace": True,
                                 "items": [{"kind": "itinerary", "data": item}]})
        self.assertEqual(self.imported[0]["data"]["attraction_names"], ["外滩", "豫园"])

    def test_array_bytes_count_toward_result_and_import_budgets(self):
        request = self.request()
        context = self.service._context("shanghai", planning_request=request)
        base = self.service._validate_result(output(itinerary(names=[])), request, context)
        linked = output(itinerary(names=[str(i) + "景" * 59 for i in range(10)]))
        baseline_bytes = len(ai._dumps(base).encode("utf-8"))
        with patch.object(ai, "MAX_RESULT_BYTES", baseline_bytes + 100), self.assertRaises(ai.AIError):
            self.service._validate_result(linked, request, context)
        with patch.object(ai, "MAX_IMPORT_BYTES", baseline_bytes + 500), self.assertRaises(ai.AIError):
            self.service._validate_result(linked, request, context)

    def test_context_allowlist_and_actual_provider_bytes_include_rich_fields_and_links(self):
        self.context["city"]["api_key"] = "secret-city-key"
        self.context["existing"]["attraction"][0].update(id="secret-id", user_id="secret-user", token="secret-token", navigation_link="secret-link")
        self.context["existing"]["attraction"][0]["description"] = "景" * 1000
        self.context["existing"]["attraction"][0]["tags"] = ["标签" * 30] * 50
        self.context["existing"]["itinerary"][0]["user_id"] = "secret-author"
        prompt, payload, _ = self.prompt()
        attraction = prompt["existing"]["attraction"][0]
        self.assertEqual(set(attraction), {"name", "category", "tags", "description", "duration", "in_itinerary", "itinerary_dates"})
        self.assertLessEqual(len(attraction["description"]), 600)
        self.assertTrue(all(len(tag) <= ai.MAX_TAG_LENGTH for tag in attraction["tags"]))
        for secret in ("secret-city-key", "secret-id", "secret-user", "secret-token", "secret-link", "secret-author"):
            self.assertNotIn(secret, payload["input"])
        self.context["existing"]["attraction"] = [dict(attraction, name=f"景点 {i}" + "景" * 50, description="景" * 600,
                                                           tags=[f"{j}" + "标签" * 11 for j in range(30)]) for i in range(100)]
        self.context["existing"]["itinerary"] = [dict(itinerary(names=["景" * 59 + str(j) for j in range(10)]), notes="景" * 600) for _ in range(100)]
        request = self.request(kinds=["attraction", "food", "itinerary"], requirements="需" * 2000, preferences="偏" * 800)
        _, payload, context = self.prompt(request)
        self.assertLessEqual(len(payload["input"].encode("utf-8")), ai.MAX_INPUT_BYTES)
        self.assertEqual(context["itinerary_snapshot"], self.context["itinerary_snapshot"])

    def test_retained_visits_after_context_row_limit_are_not_marked_unscheduled(self):
        self.context["existing"]["attraction"][0].update(in_itinerary=False, itinerary_dates=[])
        self.context["existing"]["itinerary"] = [itinerary(names=[], title=f"安排 {i}") for i in range(130)]
        self.context["existing"]["itinerary"].append(itinerary("2026-10-02", ["外滩"], "保留的外滩安排"))
        prompt, _, _ = self.prompt(self.request(planning_mode="replace_day", target_date="2026-10-01", days=1))
        self.assertIn("外滩", prompt["itinerary_links"]["scheduled_attractions"])
        self.assertNotIn("外滩", prompt["itinerary_links"]["unscheduled_attractions"])
        self.assertEqual(prompt["existing"]["attraction"][0]["itinerary_dates"], ["2026-10-02"])

    def test_real_server_relationships_reach_the_ai_context_without_generic_roads(self):
        db_path = Path(self.temp.name) / "project.db"
        seed = Path(self.temp.name) / "empty-seed.json"
        seed.write_text("{}", encoding="utf-8")
        server.init_database(db_path, "test-project", seed, empty_project=True)
        server.claim_identity(db_path, {"user_id": "旅行者"})
        for name in ("青石公园", "云山公园", "白鹭博物馆"):
            server.create_item(db_path, "attraction", {"city_id": "shanghai", "name": name, "category": "园林公园",
                               "tags": ["湖景"], "description": "已收录的游览地点", "duration": "2 小时"}, "旅行者")
        server.create_item(db_path, "attraction", {"city_id": "beijing", "name": "异地私有景点"}, "旅行者")
        for day, name in (("2026-10-01", "青石公园"), ("2026-10-02", "云山公园"), ("2026-10-02", "普通路口")):
            server.create_item(db_path, "itinerary", {"city_id": "shanghai", **itinerary(day, [name], "游览 " + name), "location": name}, "旅行者")
        self.service._get_context = lambda city: server.ai_context(db_path, city)
        prompt, payload, _ = self.prompt(self.request(planning_mode="replace_day", target_date="2026-10-01", days=1))
        self.assertEqual(prompt["itinerary_links"]["scheduled_attractions"], ["云山公园"])
        self.assertEqual(set(prompt["itinerary_links"]["unscheduled_attractions"]), {"青石公园", "白鹭博物馆"})
        by_name = {item["name"]: item for item in prompt["existing"]["attraction"]}
        self.assertEqual(by_name["云山公园"]["tags"], ["湖景"])
        self.assertEqual(by_name["云山公园"]["duration"], "2 小时")
        self.assertEqual(by_name["云山公园"]["itinerary_dates"], ["2026-10-02"])
        self.assertFalse(by_name["青石公园"]["in_itinerary"])
        road = next(item for item in prompt["existing"]["itinerary"] if item["location"] == "普通路口")
        self.assertEqual(road["attraction_names"], [])
        self.assertNotIn("异地私有景点", payload["input"])
        self.assertNotIn("旅行者", payload["input"])

    def test_import_allows_only_name_arrays_not_relationship_metadata(self):
        job = self.ready(output(itinerary(names=["外滩"])))
        for field in ("linked_attractions", "attraction_ids", "itinerary_refs", "in_itinerary", "attraction_name_ids"):
            item = dict(job["result"]["itineraries"][0], **{field: ["injected"]})
            with self.subTest(field=field), self.assertRaises(ai.AIError) as error:
                self.service.import_job("Alice", "device", job["id"], {"items": [{"kind": "itinerary", "data": item}]})
            self.assertEqual(error.exception.status, 400)
        self.assertEqual(self.imported, [])


if __name__ == "__main__":
    unittest.main()
