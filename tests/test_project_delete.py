from __future__ import annotations

import http.cookiejar
import json
import sqlite3
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from functools import partial
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import backup_all
import place_cache
import server
from ai_service import AIService
from test_server import RunningServer, TEST_ADMIN_PASSWORD, TEST_PROJECT_CODE


class ProjectDeletionTests(unittest.TestCase):
    def setUp(self):
        self.running = RunningServer()
        self.opener = self.new_browser()
        self.request("/api/admin/session", "POST", {"password": TEST_ADMIN_PASSWORD})
        self.child = self.create_project("准备删除的旅行")
        self.child_path = self.running.httpd.project_store.resolve(self.child["id"])

    def tearDown(self):
        self.running.close()

    @staticmethod
    def new_browser():
        return urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path, method="GET", payload=None, *, project=None, opener=None,
                origin=None):
        headers = {"Origin": self.running.base_url if origin is None else origin}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if project is not None:
            headers["X-Trip-Project"] = project
        request = urllib.request.Request(self.running.base_url + path, data=body,
                                         method=method, headers=headers)
        with (opener or self.opener).open(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def assert_http_error(self, status, path, method="GET", payload=None, **kwargs):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(path, method, payload, **kwargs)
        self.assertEqual(error.exception.code, status)
        return json.loads(error.exception.read().decode("utf-8"))

    def create_project(self, name, **kwargs):
        status, result = self.request("/api/admin/projects", "POST",
                                      {"name": name, "project_code": "child-trip-code"}, **kwargs)
        self.assertEqual(status, 201)
        return result["project"]

    def delete_project(self, target, **kwargs):
        return self.request(f"/api/admin/projects/{target['id']}", "DELETE",
                            {"confirm_name": target["name"]}, **kwargs)

    def unlock_and_claim(self, project):
        code = TEST_PROJECT_CODE if project == "main" else "child-trip-code"
        self.request("/api/project-session", "POST", {"project_code": code}, project=project)
        self.request("/api/session", "POST", {"user_id": "删除测试旅客"}, project=project)

    def restart(self):
        data_dir = Path(self.running.temporary_directory.name)
        self.running.httpd.shutdown()
        self.running.httpd.server_close()
        self.running.thread.join(timeout=3)
        self.running.httpd = server.create_server(
            "127.0.0.1", 0, public_dir=PROJECT_ROOT / "public", data_dir=data_dir,
            project_code=TEST_PROJECT_CODE, admin_password=TEST_ADMIN_PASSWORD)
        self.running.thread = threading.Thread(target=self.running.httpd.serve_forever, daemon=True)
        self.running.thread.start()
        self.running.base_url = f"http://127.0.0.1:{self.running.httpd.server_port}"

    def test_admin_and_same_origin_required_and_rate_limit_does_not_delete(self):
        path = f"/api/admin/projects/{self.child['id']}"
        body = {"confirm_name": self.child["name"]}
        visitor = self.new_browser()
        self.assert_http_error(401, path, "DELETE", body, opener=visitor)
        self.request("/api/project-session", "POST", {"project_code": TEST_PROJECT_CODE}, opener=visitor)
        self.assert_http_error(401, path, "DELETE", body, opener=visitor)
        self.assert_http_error(403, path, "DELETE", body, origin="https://unrelated.example")
        with patch.object(self.running.httpd.rate_limiter, "allow", return_value=False) as limiter:
            self.assert_http_error(429, path, "DELETE", body)
        self.assertTrue(any(call.args[0].startswith("admin_write:") for call in limiter.call_args_list))
        self.assertIn(self.child["id"], {p["id"] for p in self.request("/api/admin/projects")[1]["projects"]})

    def test_current_name_confirmation_and_missing_or_repeated_deletion(self):
        path = f"/api/admin/projects/{self.child['id']}"
        self.assert_http_error(409, path, "DELETE", {"confirm_name": "错误名称"})
        new_name = "已经更名的旅行"
        self.request("/api/admin/project", "PUT", {"name": new_name}, project=self.child["id"])
        self.assert_http_error(409, path, "DELETE", {"confirm_name": self.child["name"]})
        self.assert_http_error(404, "/api/admin/projects/0000000000000000", "DELETE",
                               {"confirm_name": new_name})
        self.child["name"] = new_name
        status, result = self.delete_project(self.child)
        self.assertEqual(status, 200)
        self.assertEqual(result["deleted_id"], self.child["id"])
        self.assertEqual(result["next_project_id"], "main")
        self.assertEqual([p["id"] for p in result["projects"]], ["main"])
        self.assert_http_error(404, path, "DELETE", {"confirm_name": new_name})

    def test_deleted_current_project_rejects_data_but_keeps_global_admin_hub(self):
        project_id = self.child["id"]
        self.unlock_and_claim(project_id)
        self.delete_project(self.child, project=project_id)
        routes = [
            ("GET", "/api/snapshot?city_id=beijing", None),
            ("GET", "/api/users", None),
            ("GET", "/api/session", None),
            ("GET", "/api/project-session", None),
            ("GET", "/api/admin/state", None),
            ("GET", "/api/ai/config", None),
            ("GET", "/api/ai/jobs/old-job", None),
            ("POST", "/api/project-session", {"project_code": "child-trip-code"}),
            ("POST", "/api/session", {"user_id": "不能恢复项目"}),
            ("POST", "/api/cities", {"name": "杭州"}),
            ("POST", "/api/items/food", {"city_id": "beijing", "name": "不应写入"}),
            ("POST", "/api/ai/jobs", {"city_id": "beijing", "kinds": ["food"]}),
            ("DELETE", "/api/items/food/0123456789ab", {"version": 1}),
        ]
        for method, path, payload in routes:
            with self.subTest(method=method, path=path):
                self.assert_http_error(404, path, method, payload, project=project_id)
        self.assertTrue(self.request("/api/admin/session", project=project_id)[1]["authenticated"])
        self.assertEqual([p["id"] for p in self.request("/api/admin/projects", project=project_id)[1]["projects"]], ["main"])
        self.request("/api/admin/session", "DELETE", {}, project=project_id)
        self.assertFalse(self.request("/api/admin/session", project=project_id)[1]["authenticated"])
        self.request("/api/admin/session", "POST", {"password": TEST_ADMIN_PASSWORD}, project=project_id)
        self.assertTrue(self.request("/api/admin/session", project=project_id)[1]["authenticated"])

    def test_delete_preserves_other_project_places_cache_and_archived_backup(self):
        self.unlock_and_claim(self.child["id"])
        _, added = self.request("/api/items/food", "POST",
                                {"city_id": "beijing", "name": "已归档旅行的特色美食"},
                                project=self.child["id"])
        item = added["item"]
        main_path = self.running.httpd.db_path
        before_main = server.snapshot(main_path, "beijing")
        before_cache = place_cache.load_city(main_path, "北京")
        self.delete_project(self.child)
        self.assertEqual(server.snapshot(main_path, "beijing"), before_main)
        self.assertEqual(place_cache.load_city(main_path, "北京"), before_cache)
        self.assertTrue(self.child_path.is_file())
        with sqlite3.connect(self.child_path) as db:
            self.assertIsNotNone(db.execute("SELECT id FROM items WHERE id=?", (item["id"],)).fetchone())
        with self.assertRaises(KeyError):
            self.running.httpd.project_store.resolve(self.child["id"])
        newest = self.create_project("下一次旅行")
        self.unlock_and_claim(newest["id"])
        _, inherited = self.request("/api/snapshot?city_id=beijing", project=newest["id"])
        copy = next(place for place in inherited["items"]["food"] if place["name"] == item["name"])
        self.assertNotEqual(copy["id"], item["id"])
        self.assertEqual(copy["created_by"], "城市资料库")
        output = main_path.parent / "deletion-backup"
        backup_all.backup_all(main_path.parent, output)
        self.assertTrue((output / self.child_path.name).is_file())
        self.assertTrue((output / "place_cache.db").is_file())
        with sqlite3.connect(output / self.child_path.name) as db:
            self.assertIsNotNone(db.execute("SELECT id FROM items WHERE id=?", (item["id"],)).fetchone())

    def test_delete_main_and_last_project_survives_restart_and_allows_new_project(self):
        main = next(p for p in self.request("/api/admin/projects")[1]["projects"] if p["id"] == "main")
        _, result = self.delete_project(main)
        self.assertEqual(result["next_project_id"], self.child["id"])
        self.assertEqual([p["id"] for p in result["projects"]], [self.child["id"]])
        self.assert_http_error(404, "/api/snapshot", project="main")
        self.assertEqual(self.request("/api/health")[0], 200)
        self.restart()
        self.assertEqual([p["id"] for p in self.request("/api/admin/projects")[1]["projects"]], [self.child["id"]])
        _, result = self.delete_project(self.child, project=self.child["id"])
        self.assertEqual(result["projects"], [])
        self.assertIsNone(result["next_project_id"])
        self.assertEqual(self.request("/api/health", project=self.child["id"])[0], 200)
        self.restart()
        self.assertEqual(self.request("/api/admin/projects")[1]["projects"], [])
        self.assertEqual(self.request("/api/health")[0], 200)
        for deleted_id in ("main", self.child["id"]):
            self.assert_http_error(404, "/api/snapshot", project=deleted_id)
        new_project = self.create_project("从空列表新建", project=self.child["id"])
        self.unlock_and_claim(new_project["id"])
        self.assertEqual(self.request("/api/snapshot", project=new_project["id"])[0], 200)
        self.assertEqual([p["id"] for p in self.request("/api/admin/projects")[1]["projects"]], [new_project["id"]])
        self.assertTrue(self.running.httpd.db_path.is_file())

    def test_deleting_project_flushes_previously_failed_cache_write(self):
        self.unlock_and_claim(self.child["id"])
        with patch.object(place_cache, "flush", side_effect=sqlite3.OperationalError("cache busy")):
            _, result = self.request("/api/items/attraction", "POST",
                                     {"city_id": "beijing", "name": "等待写入缓存的景点"},
                                     project=self.child["id"])
        with sqlite3.connect(self.child_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM cache_outbox").fetchone()[0], 1)
        self.delete_project(self.child)
        cached_names = [entry["payload"]["name"]
                        for entry in place_cache.load_city(self.running.httpd.db_path, "北京")]
        self.assertIn(result["item"]["name"], cached_names)

    def test_cache_failure_during_deletion_keeps_project_accessible(self):
        self.unlock_and_claim(self.child["id"])
        with patch.object(place_cache, "flush", side_effect=sqlite3.OperationalError("cache busy")):
            self.assert_http_error(503, f"/api/admin/projects/{self.child['id']}", "DELETE",
                                   {"confirm_name": self.child["name"]})
        self.assertEqual(self.running.httpd.project_store.resolve(self.child["id"]), self.child_path)
        self.assertEqual(self.request("/api/snapshot", project=self.child["id"])[0], 200)
        self.assertIn(self.child["id"], {p["id"] for p in self.request("/api/admin/projects")[1]["projects"]})
        self.assertEqual(self.delete_project(self.child)[0], 200)

    def test_active_ai_job_blocks_deletion_without_cancel_or_external_call(self):
        # Persist task states without queueing work: no paid or external AI request.
        path = self.child_path
        service = AIService(path, api_key="test-only-unused-key",
                            get_context=partial(server.ai_context, path),
                            normalize_item=server.validate_payload,
                            import_items=partial(server.import_ai_items, path))
        self.running.httpd._ai_services[self.child["id"]] = service
        with sqlite3.connect(path) as db:
            db.execute("""INSERT INTO ai_jobs(
                id, user_id, session_hash, request_hash, city_id, city_name, model,
                status, request_json, context_json, created_at, updated_at, created_epoch, day_key
            ) VALUES ('pending-job', '测试用户', 'unused-device', 'request-hash', 'beijing', '北京',
                      'test-model', 'queued', '{}', '{}', 'now', 'now', ?, '2026-10-01')""", (time.time(),))
        with patch.object(service, "_post", side_effect=AssertionError("Deletion must not call AI")) as network:
            for state in ("queued", "running"):
                with self.subTest(state=state):
                    with sqlite3.connect(path) as db:
                        db.execute("UPDATE ai_jobs SET status=? WHERE id='pending-job'", (state,))
                    self.assert_http_error(409, f"/api/admin/projects/{self.child['id']}", "DELETE",
                                           {"confirm_name": self.child["name"]})
                    with sqlite3.connect(path) as db:
                        self.assertEqual(db.execute("SELECT status FROM ai_jobs WHERE id='pending-job'").fetchone()[0], state)
                    self.assertEqual(self.running.httpd.project_store.resolve(self.child["id"]), path)
            with sqlite3.connect(path) as db:
                db.execute("UPDATE ai_jobs SET status='ready' WHERE id='pending-job'")
            self.assertEqual(self.delete_project(self.child)[0], 200)
            network.assert_not_called()
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("SELECT status FROM ai_jobs WHERE id='pending-job'").fetchone()[0], "ready")


if __name__ == "__main__":
    unittest.main()
