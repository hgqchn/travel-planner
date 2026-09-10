from __future__ import annotations

import http.cookiejar
import json
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from test_server import RunningServer, TEST_ADMIN_PASSWORD, TEST_PROJECT_CODE


class ProjectApiTests(unittest.TestCase):
    def setUp(self):
        self.running = RunningServer()
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.request("/api/admin/session", "POST", {"password": TEST_ADMIN_PASSWORD})
        status, created, _ = self.request("/api/admin/projects", "POST", {
            "name": "第二个旅行项目", "project_code": "second-project-code",
        })
        self.assertEqual(status, 201)
        self.project = created["project"]
        self.project_id = self.project["id"]

    def tearDown(self):
        self.running.close()

    def request(self, path, method="GET", payload=None, *, project=None, opener=None):
        headers = {"Origin": self.running.base_url}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if project is not None:
            headers["X-Trip-Project"] = project
        request = urllib.request.Request(self.running.base_url + path, data=body,
                                         method=method, headers=headers)
        response = (opener or self.opener).open(request, timeout=3)
        return response.status, json.loads(response.read().decode("utf-8")), response

    def assert_http_error(self, status, path, method="GET", payload=None, **kwargs):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(path, method, payload, **kwargs)
        self.assertEqual(error.exception.code, status)
        return json.loads(error.exception.read().decode("utf-8"))

    def unlock(self, project=None, opener=None):
        code = "second-project-code" if project == self.project_id else TEST_PROJECT_CODE
        self.request("/api/project-session", "POST", {"project_code": code},
                     project=project, opener=opener)

    def claim(self, user_id, project=None):
        self.request("/api/session", "POST", {"user_id": user_id}, project=project)

    def test_creation_and_listing_require_admin_and_share_only_admin_cookie(self):
        anon = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        for method, payload in (("GET", None), ("POST", {"name": "无权项目", "project_code": "code"})):
            self.assert_http_error(401, "/api/admin/projects", method, payload, opener=anon)
        self.unlock(opener=anon)
        self.assert_http_error(401, "/api/admin/projects", "POST",
                               {"name": "仍无权项目", "project_code": "code"}, opener=anon)
        _, listing, _ = self.request("/api/admin/projects")
        self.assertEqual(listing["current_project_id"], "main")
        self.assertEqual({entry["id"] for entry in listing["projects"]}, {"main", self.project_id})
        self.assertRegex(self.project_id, r"^[a-f0-9]{16}$")
        self.assertEqual(self.project["url"], f"/?project={self.project_id}")
        self.assertEqual(self.project["name"], "第二个旅行项目")
        self.assertNotIn("code", json.dumps(listing).lower())
        # The administrator session spans the deployment; no project login is needed here.
        _, child_listing, _ = self.request("/api/admin/projects", project=self.project_id)
        self.assertEqual(child_listing["current_project_id"], self.project_id)
        self.assertEqual(child_listing["projects"], listing["projects"])

    def test_project_codes_and_user_sessions_are_isolated_in_one_browser(self):
        self.unlock()
        self.claim("主项目旅客")
        self.assert_http_error(401, "/api/snapshot", project=self.project_id)
        self.assert_http_error(403, "/api/project-session", "POST",
                               {"project_code": TEST_PROJECT_CODE}, project=self.project_id)
        self.unlock(self.project_id)
        error = self.assert_http_error(401, "/api/session", project=self.project_id)
        self.assertEqual(error["code"], "USER_REQUIRED")
        self.claim("第二项目旅客", self.project_id)
        _, main_session, _ = self.request("/api/session")
        _, child_session, _ = self.request("/api/session", project=self.project_id)
        self.assertEqual(main_session["user_id"], "主项目旅客")
        self.assertEqual(child_session["user_id"], "第二项目旅客")
        _, main_users, _ = self.request("/api/users")
        _, child_users, _ = self.request("/api/users", project=self.project_id)
        self.assertEqual([user["user_id"] for user in main_users["users"]], ["主项目旅客"])
        self.assertEqual([user["user_id"] for user in child_users["users"]], ["第二项目旅客"])
        cookie_names = {cookie.name for cookie in self.cookies}
        self.assertTrue({"trip_project", "trip_session", f"trip_project_{self.project_id}",
                         f"trip_session_{self.project_id}"}.issubset(cookie_names))
        self.request("/api/project-session", "DELETE", {}, project=self.project_id)
        self.assert_http_error(401, "/api/snapshot", project=self.project_id)
        _, main_session, _ = self.request("/api/session")
        self.assertEqual(main_session["user_id"], "主项目旅客")
        self.assertEqual(self.request("/api/snapshot")[0], 200)

    def test_child_cookie_cannot_unlock_or_claim_identity_in_main(self):
        self.unlock(self.project_id)
        self.claim("第二项目旅客", self.project_id)
        self.assert_http_error(401, "/api/snapshot")
        self.assert_http_error(401, "/api/session", "POST", {"user_id": "越界旅客"})
        self.assert_http_error(403, "/api/project-session", "POST",
                               {"project_code": "second-project-code"})
        self.assertEqual(self.request("/api/snapshot", project=self.project_id)[0], 200)

    def test_same_city_same_name_items_have_independent_versions_and_deletion(self):
        self.unlock()
        self.claim("主项目旅客")
        self.unlock(self.project_id)
        self.claim("第二项目旅客", self.project_id)
        _, main_created, _ = self.request("/api/items/attraction", "POST", {
            "city_id": "beijing", "name": "同名公园", "description": "主项目的备注",
        })
        _, child_created, _ = self.request("/api/items/attraction", "POST", {
            "city_id": "beijing", "name": "同名公园", "description": "第二项目的备注",
        }, project=self.project_id)
        main_item, child_item = main_created["item"], child_created["item"]
        self.assertNotEqual(main_item["id"], child_item["id"])
        self.request(f"/api/items/attraction/{main_item['id']}", "PUT", {
            "name": "同名公园", "description": "主项目修改", "version": main_item["version"],
        })
        self.assert_http_error(404, f"/api/items/attraction/{main_item['id']}", "DELETE",
                               {"version": 2}, project=self.project_id)
        self.request("/api/items/attraction/batch-delete", "POST", {
            "city_id": "beijing", "items": [{"id": main_item["id"], "version": 2}],
        })
        _, main_snapshot, _ = self.request("/api/snapshot?city_id=beijing")
        _, child_snapshot, _ = self.request("/api/snapshot?city_id=beijing", project=self.project_id)
        self.assertNotIn(main_item["id"], {item["id"] for item in main_snapshot["items"]["attraction"]})
        self.assertNotIn(child_item["id"], {item["id"] for item in main_snapshot["items"]["attraction"]})
        remaining = next(item for item in child_snapshot["items"]["attraction"] if item["id"] == child_item["id"])
        self.assertEqual(remaining["version"], 1)
        self.assertEqual(remaining["description"], "第二项目的备注")
        self.assertEqual(remaining["updated_by"], "第二项目旅客")

    def test_invalid_or_unregistered_project_id_never_falls_back_to_main(self):
        self.unlock()
        self.claim("主项目旅客")
        for project_id in ("../trip.db", "/tmp/trip.db", "main/../trip.db", "MAIN",
                           "0" * 16, "f" * 17, "%2e%2e%2ftrip.db"):
            with self.subTest(project_id=project_id):
                self.assert_http_error(404, "/api/snapshot", project=project_id)
                self.assert_http_error(404, "/api/items/food", "POST", {
                    "name": "不能写入", "city_id": "beijing",
                }, project=project_id)
        _, main_snapshot, _ = self.request("/api/snapshot?city_id=beijing")
        self.assertNotIn("不能写入", {item["name"] for item in main_snapshot["items"]["food"]})


if __name__ == "__main__":
    unittest.main()
