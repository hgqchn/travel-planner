from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

import ai_service as ai
import server


class FillItemTests(unittest.TestCase):
    def test_specialty_cuisine_and_tags_flow_through_prompt_and_result(self):
        output = self.output()
        output['foods'][0].update(name='红烧肉', category='特色菜肴', cuisine='本帮菜', tags=['咸甜', '适合分享'])
        job = self.run_job(self.request(item={'name': '红烧肉', 'category': '特色菜肴', 'tags': []}), output)
        self.assertEqual(job['status'], 'ready', job['error'])
        self.assertEqual(job['result']['foods'][0]['cuisine'], '本帮菜')
        self.assertEqual(job['result']['foods'][0]['tags'], ['咸甜', '适合分享'])
        sent = self.service._post.call_args.args[0]
        self.assertIn('place_taxonomy', json.loads(sent['input']))
        self.assertIn('cuisine', sent['text']['format']['schema']['properties']['foods']['items']['required'])

    def test_malformed_tags_fail_instead_of_becoming_unvalidated_text(self):
        output = self.output()
        output['foods'][0]['tags'] = '早餐,夜宵'
        job = self.run_job(self.request(), output)
        self.assertEqual(job['status'], 'failed')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.context = {"city": {"id": "shanghai", "name": "上海"}, "existing": {}}
        self.import_items = Mock()
        self.service = ai.AIService(Path(self.temp.name) / "test.db", api_key="fake-key",
                                   get_context=lambda city_id: self.context,
                                   normalize_item=server.validate_payload, import_items=self.import_items,
                                   cooldown_seconds=0)
        self.service._post = Mock()

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def request(self, kind="food", **changes):
        value = {"city_id": "shanghai", "kinds": [kind], "purpose": "fill_item",
                 "item": {"name": "生煎" if kind == "food" else "外滩"}, "model": "custom-model"}
        value.update(changes)
        return value

    def output(self, kind="food"):
        record = {field: "" for field in ai.FIELD_LIMITS[kind]}
        record.update(name="生煎" if kind == "food" else "外滩", description="简短介绍")
        return {"schema_version": 1, "summary": "补充基本信息", "notices": [],
                **{plural: [record] if key == kind else [] for key, plural in ai.KINDS.items()}}

    def run_job(self, request, output):
        self.service._post.return_value = {
            "status": "completed", "output": [{"type": "message", "status": "completed", "content": [
                {"type": "output_text", "text": json.dumps(output)}]}]}
        job = self.service.create_job("Alice", "device", request)
        deadline = time.monotonic() + 3
        while job["status"] in ("queued", "running") and time.monotonic() < deadline:
            time.sleep(.01)
            job = self.service.get_job("Alice", "device", job["id"])
        return job

    def test_food_fill_is_one_draft_and_cannot_be_batch_imported(self):
        job = self.run_job(self.request(), self.output())
        self.assertEqual(job["status"], "ready", job["error"])
        self.assertEqual(job["result"]["foods"][0]["name"], "生煎")
        payload = self.service._post.call_args.args[0]
        self.assertEqual(payload["model"], "custom-model")
        prompt = json.loads(payload["input"])
        self.assertEqual(prompt["request"]["item"]["name"], "生煎")
        self.assertNotIn("existing", prompt)
        schema = payload["text"]["format"]["schema"]["properties"]
        self.assertEqual(schema["foods"]["maxItems"], 1)
        self.assertEqual(schema["attractions"]["maxItems"], 0)
        with self.assertRaises(ai.AIError):
            self.service.import_job("Alice", "device", job["id"], {"items": [
                {"kind": "food", "data": job["result"]["foods"][0]}]})
        self.import_items.assert_not_called()

    def test_attraction_fill_keeps_reference_information_and_uses_catalog(self):
        request = self.request("attraction", item={"name": "外滩", "district": "黄浦区"})
        job = self.run_job(request, self.output("attraction"))
        self.assertEqual(job["status"], "ready", job["error"])
        prompt = json.loads(self.service._post.call_args.args[0]["input"])
        self.assertEqual(prompt["request"]["item"]["district"], "黄浦区")
        self.assertIn("scenic_catalog", prompt)
        self.assertEqual(len(job["result"]["attractions"]), 1)

    def test_rejects_missing_name_metadata_and_wrong_kinds_before_generation(self):
        for change in ({"item": {}}, {"item": {"name": "生煎", "user_id": "other"}},
                       {"kinds": ["food", "attraction"]}, {"purpose": "other"},
                       {"kinds": ["itinerary"], "start_date": "2026-10-01"}):
            with self.subTest(change=change), self.assertRaises(ai.AIError):
                self.service.create_job("Alice", "device", self.request(**change))
        self.service._post.assert_not_called()

    def test_catalog_name_correction_updates_draft_and_grade_without_changing_request(self):
        self.context = {"city": {"id": "beijing", "name": "北京"}, "existing": {}}
        request = self.request("attraction", city_id="beijing", item={"name": "八达岭长城"})
        for model_name in ("八达岭长城", "八达岭—慕田峪长城旅游区"):
            output = self.output("attraction")
            output["attractions"][0]["name"] = model_name
            job = self.run_job(request, output)
            self.assertEqual(job["status"], "ready", job["error"])
            self.assertEqual(job["request"]["item"]["name"], "八达岭长城")
            self.assertEqual(job["result"]["attractions"][0]["name"], "八达岭—慕田峪长城旅游区")
            self.assertEqual(job["result"]["attractions"][0]["scenic_rating"], "5A")
            self.assertEqual(job["result"]["name_correction"]["from"], "八达岭长城")
            prompt = json.loads(self.service._post.call_args.args[0]["input"])
            self.assertEqual(prompt["request"]["item"]["name"], "八达岭—慕田峪长城旅游区")
            self.assertEqual(prompt["name_correction"]["from"], "八达岭长城")
        self.import_items.assert_not_called()

    def test_name_correction_does_not_allow_model_to_substitute_another_attraction(self):
        self.context = {"city": {"id": "beijing", "name": "北京"}, "existing": {}}
        output = self.output("attraction")
        output["attractions"][0]["name"] = "颐和园"
        job = self.run_job(self.request("attraction", city_id="beijing", item={"name": "八达岭长城"}), output)
        self.assertEqual(job["status"], "failed")

    def test_combined_name_drafts_apply_in_other_cities(self):
        for city_id, city_name, original, target in (
            ("suzhou", "苏州", "拙政园", "苏州市苏州园林（拙政园－留园－虎丘）"),
            ("chengdu", "成都", "都江堰", "成都市青城山－都江堰旅游景区"),
            ("ningbo", "宁波", "天一阁", "宁波市天一阁•月湖景区"),
        ):
            with self.subTest(city=city_name, original=original):
                self.context = {"city": {"id": city_id, "name": city_name}, "existing": {}}
                output = self.output("attraction")
                output["attractions"][0]["name"] = original
                job = self.run_job(self.request("attraction", city_id=city_id, item={"name": original}), output)
                self.assertEqual(job["status"], "ready", job["error"])
                self.assertEqual(job["request"]["item"]["name"], original)
                self.assertEqual(job["result"]["attractions"][0]["name"], target)
                self.assertEqual(job["result"]["attractions"][0]["scenic_rating"], "5A")
                prompt = json.loads(self.service._post.call_args.args[0]["input"])
                self.assertEqual(prompt["name_correction"]["from"], original)
                self.assertEqual(prompt["request"]["item"]["name"], target)
        self.import_items.assert_not_called()

    def test_rejects_model_changing_name_or_returning_multiple_records(self):
        for change in ("name", "count"):
            output = self.output()
            if change == "name":
                output["foods"][0]["name"] = "其他美食"
            else:
                output["foods"].append(dict(output["foods"][0]))
            job = self.run_job(self.request(), output)
            self.assertEqual(job["status"], "failed")
            self.assertIsNone(job["result"])
        self.import_items.assert_not_called()


if __name__ == "__main__":
    unittest.main()
