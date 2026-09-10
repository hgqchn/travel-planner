from __future__ import annotations

import json
import time
import unittest
import urllib.error
from functools import partial
from pathlib import Path
from unittest.mock import Mock

import ai_service as ai
import server
from test_server import RunningServer, json_request, opener_with_cookies, unlock_project


class FillEditorIntegrationTests(unittest.TestCase):
    def test_authenticated_fill_returns_draft_then_normal_save_creates_item(self):
        running = RunningServer()
        self.addCleanup(running.close)
        db_path = Path(running.temporary_directory.name) / "trip.db"
        service = ai.AIService(db_path, api_key="offline-fake-key",
                              get_context=partial(server.ai_context, db_path),
                              normalize_item=server.validate_payload,
                              import_items=partial(server.import_ai_items, db_path), cooldown_seconds=0)
        running.httpd.ai_service = service
        name = "测试补充美食"
        record = {key: "" for key in ai.FIELD_LIMITS["food"]}
        record.update(name=name, description="测试风味简介", category="本地小吃")
        output = {"schema_version": 1, "summary": "已补充", "notices": [],
                  "attractions": [], "foods": [record], "itineraries": []}
        service._post = Mock(return_value={"status": "completed", "output": [{"type": "message", "status": "completed",
                             "content": [{"type": "output_text", "text": json.dumps(output)}]}]})
        opener = opener_with_cookies()
        request = {"city_id": "shanghai", "kinds": ["food"], "purpose": "fill_item", "item": {"name": name}}
        with self.assertRaises(urllib.error.HTTPError) as denied:
            json_request(opener, running.base_url, "/api/ai/jobs", method="POST", payload=request)
        self.assertEqual(denied.exception.code, 401)
        unlock_project(opener, running)
        json_request(opener, running.base_url, "/api/session", method="POST", payload={"user_id": "补充测试"})
        _, before, _ = json_request(opener, running.base_url, "/api/snapshot?city_id=shanghai")
        status, job, _ = json_request(opener, running.base_url, "/api/ai/jobs", method="POST", payload=request)
        self.assertEqual(status, 202)
        deadline = time.monotonic() + 3
        while job["status"] in ("queued", "running") and time.monotonic() < deadline:
            time.sleep(.01)
            _, job, _ = json_request(opener, running.base_url, "/api/ai/jobs/" + job["id"])
        self.assertEqual(job["status"], "ready", job["error"])
        _, draft_snapshot, _ = json_request(opener, running.base_url, "/api/snapshot?city_id=shanghai")
        self.assertEqual(len(before["items"]["food"]), len(draft_snapshot["items"]["food"]))
        self.assertFalse(any(item["name"] == name for item in draft_snapshot["items"]["food"]))
        payload = {key: job["result"]["foods"][0][key] for key in ai.FIELD_LIMITS["food"]}
        payload["city_id"] = "shanghai"
        json_request(opener, running.base_url, "/api/items/food", method="POST", payload=payload)
        _, after, _ = json_request(opener, running.base_url, "/api/snapshot?city_id=shanghai")
        self.assertEqual(len(after["items"]["food"]), len(before["items"]["food"]) + 1)
        self.assertEqual(next(item for item in after["items"]["food"] if item["name"] == name)["description"], "测试风味简介")


if __name__ == "__main__":
    unittest.main()
