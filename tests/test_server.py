from __future__ import annotations

import http.cookiejar
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import server  # noqa: E402


TEST_PROJECT_CODE = "同行项目口令-2026"
TEST_ADMIN_PASSWORD = "test1234"


class RunningServer:
    def __init__(self, project_code: str = TEST_PROJECT_CODE, public_origin: str = "") -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.httpd = server.create_server(
            "127.0.0.1",
            0,
            public_dir=PROJECT_ROOT / "public",
            data_dir=Path(self.temporary_directory.name),
            project_code=project_code,
            admin_password=TEST_ADMIN_PASSWORD,
            public_origin=public_origin,
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)
        self.temporary_directory.cleanup()


def opener_with_cookies() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def json_request(
    opener: urllib.request.OpenerDirector,
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
) -> tuple[int, dict, urllib.response.addinfourl]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Origin": base_url}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base_url + path, data=body, method=method, headers=headers)
    response = opener.open(request, timeout=3)
    return response.status, json.loads(response.read().decode("utf-8")), response


def unlock_project(
    opener: urllib.request.OpenerDirector,
    running: RunningServer,
    project_code: str = TEST_PROJECT_CODE,
) -> dict:
    _, data, _ = json_request(
        opener,
        running.base_url,
        "/api/project-session",
        method="POST",
        payload={"project_code": project_code},
    )
    return data


class ApiTests(unittest.TestCase):
    def admin(self, path, method="GET", payload=None):
        return json_request(self.opener, self.running.base_url, "/api/admin/" + path, method=method, payload=payload)[1]

    def test_admin_access_and_code_rotation(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.admin("state")
        self.assertEqual(error.exception.code, 401)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.admin("session", "POST", {"password": "wrong"})
        self.assertEqual(error.exception.code, 403)
        self.admin("session", "POST", {"password": TEST_ADMIN_PASSWORD})
        new_code = "游"
        result = self.admin("project", "PUT", {"name": "多城旅行", "project_code": new_code})
        self.assertEqual(result["project"]["name"], "多城旅行")
        with self.assertRaises(urllib.error.HTTPError) as error:
            json_request(self.opener, self.running.base_url, "/api/snapshot")
        self.assertEqual(error.exception.code, 401)
        with self.assertRaises(urllib.error.HTTPError):
            unlock_project(self.opener, self.running)
        server.init_database(self.running.httpd.db_path, TEST_PROJECT_CODE)
        unlock_project(self.opener, self.running, new_code)
        self.assertNotIn("code_hash", json.dumps(self.admin("state")))
        self.admin("session", "DELETE", {})
        self.assertFalse(self.admin("session")["authenticated"])

    def test_admin_users_cities_and_navigation(self):
        self.admin("session", "POST", {"password": TEST_ADMIN_PASSWORD})
        self.admin("users", "POST", {"user_id": "旅客甲"})
        self.claim("旅客甲")
        result = self.admin("cities", "POST", {"name": "测试杭州"})
        city = next(city for city in result["cities"] if city["name"] == "测试杭州")
        _, result, _ = json_request(self.opener, self.running.base_url, "/api/items/attraction", method="POST", payload={"name": "西湖", "city_id": city["id"], "navigation_link": "https://uri.amap.com/search?keyword=西湖"})
        item = result["item"]
        self.assertEqual(item["city_id"], city["id"])
        self.assertTrue(item["navigation_link"].startswith("https://"))
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.admin("cities", "DELETE", {"id": city["id"]})
        self.assertEqual(error.exception.code, 409)
        self.admin("cities", "PUT", {"id": city["id"], "name": "测试杭州市"})
        self.admin("users", "PUT", {"user_id": "旅客甲", "new_user_id": "旅客乙"})
        self.claim("旅客乙")
        with server.connect_db(self.running.httpd.db_path) as db:
            self.assertEqual(db.execute("SELECT created_by FROM items WHERE id=?", (item["id"],)).fetchone()[0], "旅客乙")
        self.admin("users", "DELETE", {"user_id": "旅客乙"})
        self.assertNotIn("旅客乙", [u["user_id"] for u in self.admin("state")["users"]])
        result = self.admin("cities", "POST", {"name": "测试苏州"})
        empty = next(city for city in result["cities"] if city["name"] == "测试苏州")
        self.admin("cities", "DELETE", {"id": empty["id"]})
        with self.assertRaises(server.ApiError):
            server.validate_payload("attraction", {"name": "坏链接", "navigation_link": "javascript:alert(1)"})

    def setUp(self) -> None:
        self.running = RunningServer()
        self.opener = opener_with_cookies()
        unlock_project(self.opener, self.running)

    def tearDown(self) -> None:
        self.running.close()

    def claim(self, user_id: str, opener: urllib.request.OpenerDirector | None = None) -> dict:
        _, data, _ = json_request(
            opener or self.opener,
            self.running.base_url,
            "/api/session",
            method="POST",
            payload={"user_id": user_id},
        )
        return data

    def test_seed_snapshot_and_health(self) -> None:
        status, health, _ = json_request(self.opener, self.running.base_url, "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")

        _, snapshot, response = json_request(self.opener, self.running.base_url, "/api/snapshot")
        self.assertGreaterEqual(len(snapshot["items"]["attraction"]), 10)
        self.assertGreaterEqual(len(snapshot["items"]["transit"]), 20)
        self.assertGreaterEqual(len(snapshot["items"]["food"]), 8)
        self.assertEqual(snapshot["items"]["itinerary"], [])
        self.assertEqual(snapshot["revision"], 0)
        self.assertEqual(response.headers["ETag"], f'"revision-0-scenic-{snapshot["scenic_catalog_version"]}-cities-{snapshot["city_metadata_version"]}"')

        conditional = urllib.request.Request(
            self.running.base_url + "/api/snapshot",
            headers={"If-None-Match": response.headers["ETag"]},
        )
        with self.assertRaises(urllib.error.HTTPError) as unchanged:
            self.opener.open(conditional, timeout=3)
        self.assertEqual(unchanged.exception.code, 304)

        # Updating bundled province data must refresh clients even without edits.
        with patch.object(server, 'city_metadata_version', return_value='new-province-data'):
            with self.opener.open(conditional, timeout=3) as updated:
                self.assertEqual(updated.status, 200)
                refreshed = json.load(updated)
                updated_etag = updated.headers['ETag']
        self.assertEqual(refreshed['revision'], snapshot['revision'])
        self.assertNotEqual(updated_etag, response.headers['ETag'])

    def test_users_can_switch_and_share_an_id_across_browsers(self) -> None:
        identity = self.claim("小鱼-01")
        self.assertEqual(identity["user_id"], "小鱼-01")
        _, session, _ = json_request(self.opener, self.running.base_url, "/api/session")
        self.assertEqual(session["user_id"], "小鱼-01")

        second_browser = opener_with_cookies()
        unlock_project(second_browser, self.running)
        second_identity = self.claim("小鱼-01", second_browser)
        self.assertEqual(second_identity["user_id"], "小鱼-01")

        self.claim("小明", self.opener)
        self.claim("小鱼-01", self.opener)
        _, first_session, _ = json_request(self.opener, self.running.base_url, "/api/session")
        _, second_session, _ = json_request(second_browser, self.running.base_url, "/api/session")
        self.assertEqual(first_session["user_id"], "小鱼-01")
        self.assertEqual(second_session["user_id"], "小鱼-01")

        _, users, _ = json_request(self.opener, self.running.base_url, "/api/users")
        self.assertEqual({entry["user_id"] for entry in users["users"]}, {"小鱼-01", "小明"})

    def test_create_update_conflict_and_delete(self) -> None:
        self.claim("planner_a")
        payload = {
            "name": "测试景点",
            "district": "徐汇区",
            "category": "城市观景",
            "description": "只作为自动测试。",
            "duration": "1 小时",
            "transport": "地铁测试站",
            "link": "https://example.com/guide",
        }
        status, created, _ = json_request(
            self.opener,
            self.running.base_url,
            "/api/items/attraction",
            method="POST",
            payload=payload,
        )
        self.assertEqual(status, 201)
        item = created["item"]
        self.assertEqual(item["updated_by"], "planner_a")
        self.assertEqual(item["version"], 1)

        changed = {**payload, "description": "第一次修改。", "version": 1}
        _, updated, _ = json_request(
            self.opener,
            self.running.base_url,
            f"/api/items/attraction/{item['id']}",
            method="PUT",
            payload=changed,
        )
        self.assertEqual(updated["item"]["version"], 2)

        with self.assertRaises(urllib.error.HTTPError) as raised:
            json_request(
                self.opener,
                self.running.base_url,
                f"/api/items/attraction/{item['id']}",
                method="PUT",
                payload={**payload, "version": 1},
            )
        self.assertEqual(raised.exception.code, 409)
        conflict = json.loads(raised.exception.read().decode("utf-8"))
        self.assertEqual(conflict["current"]["version"], 2)

        _, deleted, _ = json_request(
            self.opener,
            self.running.base_url,
            f"/api/items/attraction/{item['id']}",
            method="DELETE",
            payload={"version": 2},
        )
        self.assertEqual(deleted["deleted_id"], item["id"])

    def test_itinerary_crud_and_date_validation(self) -> None:
        self.claim("route-planner")
        payload = {
            "date": "2026-10-03",
            "start_time": "09:30",
            "title": "外滩晨间散步",
            "category": "景点",
            "location": "外滩观景平台",
            "notes": "九点半集合，之后去南京东路吃早餐。",
            "link": "https://example.com/itinerary",
        }
        status, created, _ = json_request(
            self.opener,
            self.running.base_url,
            "/api/items/itinerary",
            method="POST",
            payload=payload,
        )
        self.assertEqual(status, 201)
        item = created["item"]
        self.assertEqual(item["title"], payload["title"])
        self.assertEqual(item["updated_by"], "route-planner")

        _, updated, _ = json_request(
            self.opener,
            self.running.base_url,
            f"/api/items/itinerary/{item['id']}",
            method="PUT",
            payload={**payload, "start_time": "10:00", "version": 1},
        )
        self.assertEqual(updated["item"]["version"], 2)
        self.assertEqual(updated["item"]["start_time"], "10:00")

        for invalid in (
            {**payload, "date": "2026-02-30"},
            {**payload, "start_time": "25:00"},
        ):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                json_request(
                    self.opener,
                    self.running.base_url,
                    "/api/items/itinerary",
                    method="POST",
                    payload=invalid,
                )
            self.assertEqual(raised.exception.code, 400)

        _, deleted, _ = json_request(
            self.opener,
            self.running.base_url,
            f"/api/items/itinerary/{item['id']}",
            method="DELETE",
            payload={"version": 2},
        )
        self.assertEqual(deleted["deleted_id"], item["id"])

    def test_rejects_unsafe_links_and_cross_origin_writes(self) -> None:
        self.claim("safe-editor")
        bad = {
            "name": "危险链接",
            "district": "",
            "category": "",
            "description": "",
            "duration": "",
            "transport": "",
            "link": "javascript:alert(1)",
        }
        with self.assertRaises(urllib.error.HTTPError) as raised:
            json_request(
                self.opener,
                self.running.base_url,
                "/api/items/attraction",
                method="POST",
                payload=bad,
            )
        self.assertEqual(raised.exception.code, 400)

        request = urllib.request.Request(
            self.running.base_url + "/api/items/attraction",
            data=json.dumps({**bad, "link": ""}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
        )
        with self.assertRaises(urllib.error.HTTPError) as cross_origin:
            self.opener.open(request, timeout=3)
        self.assertEqual(cross_origin.exception.code, 403)


class ProjectGateTests(unittest.TestCase):
    def test_project_code_protects_users_content_and_writes(self) -> None:
        running = RunningServer()
        opener = opener_with_cookies()
        try:
            self.assertEqual(opener.open(running.base_url + "/", timeout=3).status, 200)
            self.assertEqual(opener.open(running.base_url + "/app.js", timeout=3).status, 200)
            _, status, _ = json_request(opener, running.base_url, "/api/project-session")
            self.assertFalse(status["unlocked"])

            for path in ("/api/users", "/api/session", "/api/snapshot"):
                with self.assertRaises(urllib.error.HTTPError) as locked:
                    json_request(opener, running.base_url, path)
                self.assertEqual(locked.exception.code, 401)
                error = json.loads(locked.exception.read().decode("utf-8"))
                self.assertEqual(error["code"], "PROJECT_LOCKED")

            with self.assertRaises(urllib.error.HTTPError) as raised:
                json_request(
                    opener,
                    running.base_url,
                    "/api/project-session",
                    method="POST",
                    payload={"project_code": "错误项目口令"},
                )
            self.assertEqual(raised.exception.code, 403)

            unlocked = unlock_project(opener, running)
            self.assertTrue(unlocked["unlocked"])
            _, status, _ = json_request(opener, running.base_url, "/api/project-session")
            self.assertTrue(status["unlocked"])
            _, users, _ = json_request(opener, running.base_url, "/api/users")
            self.assertEqual(users["users"], [])

            _, locked, _ = json_request(
                opener,
                running.base_url,
                "/api/project-session",
                method="DELETE",
                payload={},
            )
            self.assertFalse(locked["unlocked"])
            with self.assertRaises(urllib.error.HTTPError) as relocked:
                json_request(opener, running.base_url, "/api/snapshot")
            self.assertEqual(relocked.exception.code, 401)
        finally:
            running.close()


class DatabaseLifecycleTests(unittest.TestCase):
    def test_v1_database_migrates_users_sessions_items_and_itinerary_support(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "trip.db"
            legacy_token = "legacy-session-token-that-is-long-enough"
            payload = json.dumps(
                {
                    "name": "旧版景点",
                    "district": "黄浦区",
                    "category": "历史",
                    "description": "迁移测试",
                    "duration": "1 小时",
                    "transport": "步行",
                    "link": "",
                },
                ensure_ascii=False,
            )
            with sqlite3.connect(db_path) as db:
                db.executescript(
                    """
                    CREATE TABLE users (
                        user_id TEXT PRIMARY KEY COLLATE NOCASE,
                        token_hash TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL
                    );
                    CREATE TABLE items (
                        id TEXT PRIMARY KEY,
                        kind TEXT NOT NULL CHECK (kind IN ('attraction', 'transit', 'food')),
                        position INTEGER NOT NULL,
                        payload TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        created_by TEXT NOT NULL,
                        updated_by TEXT NOT NULL
                    );
                    PRAGMA user_version = 1;
                    """
                )
                db.execute(
                    "INSERT INTO users VALUES (?, ?, ?, ?)",
                    ("旧用户", server.token_hash(legacy_token), "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
                )
                db.execute(
                    "INSERT INTO items VALUES (?, 'attraction', 1, ?, 1, ?, ?, ?, ?)",
                    (
                        "abcdef123456",
                        payload,
                        "2026-01-01T00:00:00Z",
                        "2026-01-01T00:00:00Z",
                        "旧用户",
                        "旧用户",
                    ),
                )

            server.init_database(db_path, TEST_PROJECT_CODE)
            self.assertEqual(server.authenticate(db_path, legacy_token), "旧用户")
            with server.connect_db(db_path) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)
                self.assertEqual(db.execute("SELECT city_id FROM items WHERE id='abcdef123456'").fetchone()[0], "shanghai")
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                item_sql = db.execute(
                    "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = 'items'"
                ).fetchone()[0]
                self.assertIn("itinerary", item_sql)

            created = server.create_item(
                db_path,
                "itinerary",
                {
                    "date": "2026-10-03",
                    "start_time": "",
                    "title": "迁移后的行程",
                    "category": "",
                    "location": "",
                    "notes": "",
                    "link": "",
                },
                "旧用户",
            )
            self.assertEqual(created["item"]["title"], "迁移后的行程")

    def test_empty_plan_is_not_reseeded_after_first_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "trip.db"
            server.init_database(db_path, TEST_PROJECT_CODE)
            with server.connect_db(db_path) as db:
                db.execute("DELETE FROM items")
            server.init_database(db_path, TEST_PROJECT_CODE)
            with server.connect_db(db_path) as db:
                count = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            self.assertEqual(count, 0)

    def test_backup_requires_existing_source_and_passes_integrity_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            source = directory_path / "source.db"
            destination = directory_path / "backup.db"
            with self.assertRaises(FileNotFoundError):
                server.create_backup(source, destination)
            server.init_database(source, TEST_PROJECT_CODE)
            result = server.create_backup(source, destination)
            self.assertEqual(result, destination.resolve())
            with sqlite3.connect(destination) as backup:
                self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                server.create_backup(source, destination)

    def test_public_origin_validation_and_rate_limit(self) -> None:
        self.assertEqual(server.validate_project_code("12345678"), "12345678")
        for code in ("1", "游", "short", "纯中文", "!", "a" * 200):
            self.assertEqual(server.validate_project_code(code), code)
        with self.assertRaises(ValueError):
            server.validate_project_code("")
        with self.assertRaises(ValueError):
            server.validate_project_code("a" * 201)
        self.assertEqual(
            server.validate_public_origin("https://trip.example.com/"),
            "https://trip.example.com",
        )
        with self.assertRaises(ValueError):
            server.validate_public_origin("https://trip.example.com/path")
        limiter = server.SlidingWindowLimiter()
        self.assertTrue(limiter.allow("test", 2))
        self.assertTrue(limiter.allow("test", 2))
        self.assertFalse(limiter.allow("test", 2))


if __name__ == "__main__":
    unittest.main()
