from __future__ import annotations

import hashlib
import http.cookiejar
import io
import json
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import metro_maps
import server
from test_server import RunningServer, TEST_ADMIN_PASSWORD, TEST_PROJECT_CODE


SAFE_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="1000" viewBox="0 0 1400 1000"><path d="M0 0 L1200 800" stroke="red"/><text x="10" y="20">Metro map</text></svg>'
NEW_SVG = SAFE_SVG.replace(b'stroke="red"', b'stroke="blue"')


def upstream(data=SAFE_SVG):
    return {"source_url": "https://commons.wikimedia.org/wiki/File:Test_Metro.svg",
            "source_name": "Wikimedia Commons", "author": "测试绘图者", "license": "CC BY-SA 4.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
            "source_updated_at": "2026-08-01T00:00:00Z", "remote_sha1": hashlib.sha1(data).hexdigest(),
            "download_url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Test_Metro.svg",
            "mime": "image/svg+xml"}


def fixture_store(root, *, cooldown=0):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    sources = [{"id": "beijing", "name": "北京", "aliases": ["北京市"],
                "commons_title": "File:Test Beijing Metro.svg", "official_url": "https://www.bjsubway.com/"},
               {"id": "shanghai", "name": "上海", "aliases": ["上海市"],
                "commons_title": "File:Test Shanghai Metro.svg"}]
    sources.extend({"id": f"test-city-{number}", "name": f"测试轨道城市{number}",
                    "commons_title": f"File:Test Metro {number}.svg"} for number in range(8))
    source_path = root / "sources.json"
    source_path.write_text(json.dumps({"version": 1, "sources": sources}), encoding="utf-8")
    bundle = root / "bundled"
    bundle.mkdir(exist_ok=True)
    version = hashlib.sha256(SAFE_SVG).hexdigest()
    metadata = dict(upstream(), id="beijing", name="北京", version=version, format="svg",
                    width=1400, height=1000, downloaded_at="2026-08-02T00:00:00Z",
                    checked_at="2026-08-02T00:00:00Z")
    (bundle / f"beijing-{version}.svg").write_bytes(SAFE_SVG)
    (bundle / "index.json").write_text(json.dumps({"version": 1, "maps": [metadata]}), encoding="utf-8")
    return metro_maps.MetroMapStore(root / "cache", bundled_dir=bundle,
                                    sources_path=source_path, cooldown_seconds=cooldown)


class MetroMapStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = fixture_store(self.temp.name)
        self.stores = [self.store]

    def tearDown(self):
        for store in self.stores:
            store.close()
        self.temp.cleanup()

    def wait_update(self, source="北京"):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            result = self.store.describe("city-id", source)
            if result["update"]["status"] not in ("queued", "running"):
                return result
            time.sleep(0.01)
        self.fail("轨道地图更新未结束")

    def test_get_uses_local_bundle_aliases_and_never_calls_network(self):
        with patch.object(metro_maps, "source_metadata", side_effect=AssertionError("GET called network")), \
                patch.object(metro_maps, "fetch_bytes", side_effect=AssertionError("GET downloaded image")):
            for alias in ("北京", "北京市", " 北 京 市 ", "beijing"):
                state = self.store.describe("project-local-city-id", alias)
                self.assertTrue(state["supported"])
                self.assertTrue(state["available"])
                self.assertEqual(state["map"]["id"], "beijing")
                self.assertEqual(state["city_id"], "project-local-city-id")
            self.assertFalse(self.store.describe("unknown", "不存在的城市")["supported"])
        self.assertNotIn("download_url", state["map"])
        self.assertNotIn("remote_sha1", state["map"])
        self.assertEqual(state["map"]["width"], 1400)
        self.assertEqual(state["map"]["license"], "CC BY-SA 4.0")

    def test_successful_update_publishes_actual_image_hash_and_keeps_old_version(self):
        old = self.store.describe("beijing", "北京")["map"]
        with patch.object(metro_maps, "source_metadata", return_value=upstream(NEW_SVG)), \
                patch.object(metro_maps, "fetch_bytes", return_value=NEW_SVG) as fetch:
            result = self.store.update_now("beijing")
        self.assertEqual(result["version"], hashlib.sha256(NEW_SVG).hexdigest())
        self.assertNotEqual(result["version"], old["version"])
        self.assertEqual((result["width"], result["height"]), (1400, 1000))
        self.assertEqual(self.store.asset_path("beijing", old["version"], "svg").read_bytes(), SAFE_SVG)
        self.assertEqual(self.store.asset_path("beijing", result["version"], "svg").read_bytes(), NEW_SVG)
        self.assertEqual(self.store.describe("beijing", "北京")["map"]["version"], result["version"])
        fetch.assert_called_once()
        self.assertFalse(list(self.store.cache_dir.glob("*.tmp")))

    def test_unchanged_upstream_checks_metadata_without_redownloading_image(self):
        old = self.store.describe("beijing", "北京")["map"]
        with patch.object(metro_maps, "source_metadata", return_value=upstream()), \
                patch.object(metro_maps, "fetch_bytes", side_effect=AssertionError("unchanged image downloaded")), \
                patch.object(metro_maps, "now", return_value="2026-09-10T01:02:03Z"):
            self.store.update_now("beijing")
        result = self.store.describe("beijing", "北京")["map"]
        self.assertEqual(result["version"], old["version"])
        self.assertEqual(result["downloaded_at"], old["downloaded_at"])
        self.assertEqual(result["checked_at"], "2026-09-10T01:02:03Z")

    def test_background_failure_keeps_old_image_and_hides_internal_exception(self):
        old = self.store.describe("beijing", "北京")["map"]
        with patch.object(metro_maps, "source_metadata", side_effect=OSError("private filesystem /secret/test")):
            self.store.request_update("beijing", "北京")
            state = self.wait_update()
        self.assertEqual(state["update"]["status"], "failed")
        self.assertNotIn("secret", state["update"]["error"])
        self.assertTrue(state["update"]["error"])
        self.assertEqual(state["map"], old)

    def test_duplicate_city_alias_requests_share_one_background_download_and_cooldown(self):
        self.store.cooldown_seconds = 300
        entered, release = threading.Event(), threading.Event()
        def blocked_metadata(source):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test worker timed out")
            return upstream(NEW_SVG)
        with patch.object(metro_maps, "source_metadata", side_effect=blocked_metadata) as metadata, \
                patch.object(metro_maps, "fetch_bytes", return_value=NEW_SVG):
            try:
                first = self.store.request_update("main-city", "北京")
                self.assertIn(first["update"]["status"], ("queued", "running"))
                self.assertTrue(entered.wait(1))
                second = self.store.request_update("child-city", "北京市")
                self.assertIn(second["update"]["status"], ("queued", "running"))
                self.assertEqual(second["city_id"], "child-city")
                self.assertEqual(metadata.call_count, 1)
            finally:
                release.set()
            self.assertEqual(self.wait_update()["update"]["status"], "ready")
        with self.assertRaises(metro_maps.MetroMapError) as limited:
            self.store.request_update("third-city", "北京")
        self.assertEqual(limited.exception.status, 429)

    def test_update_queue_and_worker_concurrency_are_bounded(self):
        release, both_entered = threading.Event(), threading.Event()
        counts = {"active": 0, "maximum": 0}
        guard = threading.Lock()
        def blocked_metadata(source):
            with guard:
                counts["active"] += 1
                counts["maximum"] = max(counts["maximum"], counts["active"])
                if counts["active"] == 2:
                    both_entered.set()
            release.wait(3)
            with guard:
                counts["active"] -= 1
            return upstream(NEW_SVG)
        names = ["北京", "上海", *[f"测试轨道城市{number}" for number in range(7)]]
        with patch.object(metro_maps, "source_metadata", side_effect=blocked_metadata), \
                patch.object(metro_maps, "fetch_bytes", return_value=NEW_SVG):
            try:
                for number, name in enumerate(names[:8]):
                    self.store.request_update(str(number), name)
                self.assertTrue(both_entered.wait(1))
                with self.assertRaises(metro_maps.MetroMapError) as limited:
                    self.store.request_update("overflow", names[8])
                self.assertEqual(limited.exception.status, 429)
            finally:
                release.set()
            for name in names[:8]:
                self.assertEqual(self.wait_update(name)["update"]["status"], "ready")
        self.assertEqual(counts["maximum"], 2)

    def test_manifest_failure_keeps_old_map_and_restarting_reads_last_good_state(self):
        old = self.store.describe("beijing", "北京")["map"]
        with patch.object(metro_maps, "source_metadata", return_value=upstream(NEW_SVG)), \
                patch.object(metro_maps, "fetch_bytes", return_value=NEW_SVG), \
                patch.object(metro_maps, "atomic_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.update_now("beijing")
        self.assertEqual(self.store.describe("beijing", "北京")["map"], old)
        restarted = metro_maps.MetroMapStore(self.store.cache_dir, bundled_dir=self.store.bundled_dir,
                                              sources_path=Path(self.temp.name) / "sources.json")
        self.stores.append(restarted)
        self.assertEqual(restarted.describe("beijing", "北京")["map"], old)
        self.assertEqual(restarted.describe("beijing", "北京")["update"]["status"], "idle")

    def test_corrupt_or_missing_cached_asset_falls_back_to_bundled_map(self):
        old = self.store.describe("beijing", "北京")["map"]
        with patch.object(metro_maps, "source_metadata", return_value=upstream(NEW_SVG)), \
                patch.object(metro_maps, "fetch_bytes", return_value=NEW_SVG):
            newer = self.store.update_now("beijing")
        self.store.asset_path("beijing", newer["version"], "svg").unlink()
        self.assertEqual(self.store.describe("beijing", "北京")["map"], old)
        (self.store.cache_dir / "beijing.json").write_text("{interrupted", encoding="utf-8")
        self.assertEqual(self.store.describe("beijing", "北京")["map"], old)

    def test_asset_path_rejects_traversal_wrong_hash_extension_and_external_symlink(self):
        version = hashlib.sha256(SAFE_SVG).hexdigest()
        for source, candidate, ext in (("../beijing", version, "svg"), ("unknown", version, "svg"),
                                       ("beijing", "../../trip", "db"), ("beijing", "A" * 64, "svg"),
                                       ("beijing", version, "html"), ("beijing", "0" * 64, "svg")):
            with self.subTest(source=source, version=candidate, extension=ext):
                self.assertIsNone(self.store.asset_path(source, candidate, ext))
        self.store.cache_dir.mkdir()
        secret = Path(self.temp.name) / "secret.svg"
        secret.write_bytes(SAFE_SVG)
        (self.store.cache_dir / f"beijing-{'0' * 64}.svg").symlink_to(secret)
        self.assertIsNone(self.store.asset_path("beijing", "0" * 64, "svg"))


class MetroImageSecurityTests(unittest.TestCase):
    def test_svg_local_defs_quoted_urls_and_metadata_remain_usable(self):
        for value in ("url(#clip)", "url(&apos;#clip&apos;)", "url(&quot;#clip&quot;)", "url( #clip )"):
            svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1400 1000">'
                   '<defs><clipPath id="clip"><path d="M0 0 L10 10"/></clipPath></defs>'
                   f'<path d="M0 0 L10 10" clip-path="{value}"/></svg>').encode()
            with self.subTest(value=value):
                self.assertEqual(metro_maps.validate_image(svg, "image/svg+xml"), ("svg", 1400, 1000))
        with_style = SAFE_SVG.replace(b"<path", b"<style>.line { clip-path: url('#clip'); }</style><path", 1)
        self.assertEqual(metro_maps.validate_image(with_style, "image/svg+xml")[0], "svg")

    def test_active_external_and_malformed_svg_content_is_rejected(self):
        fragments = [b'<script>alert(1)</script>', b'<foreignObject/>', b'<g onclick="alert(1)"/>',
                     b'<image href="https://tracker.example/pixel.png"/>', b'<use href="//localhost/secret.svg#x"/>',
                     b'<path style="fill:url(https://tracker.example/a.svg#paint)"/>',
                     b'<style>@import "https://tracker.example/a.css";</style>', b'<animate attributeName="href"/>']
        for fragment in fragments:
            data = SAFE_SVG.replace(b"</svg>", fragment + b"</svg>")
            with self.subTest(fragment=fragment), self.assertRaises(metro_maps.MetroMapError):
                metro_maps.validate_image(data, "image/svg+xml")
        for data in (b"<html>error</html>", b"<svg", b'<!DOCTYPE svg [<!ENTITY x "hello">]>' + SAFE_SVG):
            with self.subTest(data=data), self.assertRaises(metro_maps.MetroMapError):
                metro_maps.validate_image(data, "image/svg+xml")

    def test_image_type_size_dimensions_and_hd_raster_limit_are_verified(self):
        self.assertEqual(metro_maps.validate_image(SAFE_SVG, "image/svg+xml"), ("svg", 1400, 1000))
        for data, mime in ((SAFE_SVG, "image/png"), (b"", "image/svg+xml"),
                           (SAFE_SVG.replace(b"1400 1000", b"0 1000"), "image/svg+xml"),
                           (SAFE_SVG.replace(b"1400 1000", b"999999 1000"), "image/svg+xml")):
            with self.subTest(mime=mime), self.assertRaises(metro_maps.MetroMapError):
                metro_maps.validate_image(data, mime)
        png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 120, 100) + b"\x08\x02\0\0\0" + b"\0" * 4 + b"\0" * 4 + b"IEND" + b"\0" * 4
        with self.assertRaises(metro_maps.MetroMapError):
            metro_maps.validate_image(png, "image/png")

    def test_remote_allowlist_disallows_ssrf_and_redirects(self):
        valid = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Map.svg"
        self.assertEqual(metro_maps.validate_remote_url(valid, image=True), valid)
        for url in ("http://upload.wikimedia.org/wikipedia/commons/a.svg",
                    "https://upload.wikimedia.org.evil.example/wikipedia/commons/a.svg",
                    "https://upload.wikimedia.org@127.0.0.1/wikipedia/commons/a.svg",
                    "https://user:pass@upload.wikimedia.org/wikipedia/commons/a.svg",
                    "https://upload.wikimedia.org:444/wikipedia/commons/a.svg",
                    "https://upload.wikimedia.org/private/a.svg", "file:///etc/passwd",
                    "https://127.0.0.1/wikipedia/commons/a.svg", valid + "#fragment"):
            with self.subTest(url=url), self.assertRaises(metro_maps.MetroMapError):
                metro_maps.validate_remote_url(url, image=True)
        with self.assertRaises(metro_maps.MetroMapError):
            metro_maps.validate_remote_url("https://commons.wikimedia.org/wiki/File:Map.svg", image=False)
        with self.assertRaises(metro_maps.MetroMapError):
            metro_maps._NoRedirect().redirect_request(None, None, 302, "Moved", {}, "http://127.0.0.1/private")

    def test_metadata_enforces_redistribution_license_and_plain_text_attribution(self):
        metadata = {"Artist": {"value": '<a href="https://author.example">绘图者甲</a><br>与绘图者乙'},
                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                    "LicenseUrl": {"value": "//creativecommons.org/licenses/by-sa/4.0/"}}
        info = {"url": upstream()["download_url"], "size": len(SAFE_SVG), "timestamp": "2026-09-01T00:00:00Z",
                "sha1": "a" * 40, "mime": "image/svg+xml", "extmetadata": metadata}
        response = {"query": {"pages": {"123": {"title": "File:Test Metro.svg", "imageinfo": [info]}}}}
        source = {"commons_title": "File:Test Metro.svg"}
        with patch.object(metro_maps, "fetch_bytes", return_value=json.dumps(response).encode()):
            result = metro_maps.source_metadata(source)
        self.assertEqual(result["author"], "绘图者甲 与绘图者乙")
        self.assertEqual(result["license_url"], "https://creativecommons.org/licenses/by-sa/4.0/")
        self.assertNotIn("<", result["author"])
        for license_name in ("Copyrighted", "CC BY-NC-SA 4.0", "", "CC BY-SA 4.0 bogus"):
            metadata["LicenseShortName"]["value"] = license_name
            with self.subTest(license=license_name), \
                    patch.object(metro_maps, "fetch_bytes", return_value=json.dumps(response).encode()), \
                    self.assertRaises(metro_maps.MetroMapError):
                metro_maps.source_metadata(source)

    def test_fetch_limits_header_and_stream_size_without_real_network(self):
        class Response(io.BytesIO):
            def __init__(self, body, length=None):
                super().__init__(body)
                self.headers = {} if length is None else {"Content-Length": str(length)}
        url = upstream()["download_url"]
        for body, length in ((b"small", 100), (b"x" * 21, None)):
            with self.subTest(length=length), patch.object(urllib.request, "build_opener") as builder:
                builder.return_value.open.return_value = Response(body, length)
                with self.assertRaises(metro_maps.MetroMapError):
                    metro_maps.fetch_bytes(url, image=True, limit=20)


class MetroMapApiTests(unittest.TestCase):
    def setUp(self):
        self.running = RunningServer()
        self.running.httpd.metro_store.close()
        self.running.httpd.metro_store = fixture_store(Path(self.running.temporary_directory.name) / "metro-fixture")
        self.opener = self.new_browser()

    def tearDown(self):
        self.running.close()

    @staticmethod
    def new_browser():
        return urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path, method="GET", payload=None, *, project=None, opener=None,
                origin=None, raw=False):
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
            body = response.read()
            return response.status, body if raw else json.loads(body.decode("utf-8")), response.headers

    def assert_http_error(self, status, path, method="GET", payload=None, **kwargs):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(path, method, payload, **kwargs)
        self.assertEqual(error.exception.code, status)
        return json.loads(error.exception.read().decode("utf-8"))

    def unlock(self, project=None):
        self.request("/api/project-session", "POST", {
            "project_code": TEST_PROJECT_CODE if project in (None, "main") else "child-metro-code",
        }, project=project)

    def claim(self, project=None):
        self.request("/api/session", "POST", {"user_id": "轨道地图测试旅客"}, project=project)

    def admin(self):
        self.request("/api/admin/session", "POST", {"password": TEST_ADMIN_PASSWORD})

    def create_project(self):
        self.admin()
        return self.request("/api/admin/projects", "POST", {
            "name": "轨道地图第二项目", "project_code": "child-metro-code",
        })[1]["project"]

    def test_metadata_requires_project_access_and_real_city(self):
        self.assert_http_error(401, "/api/metro-map?city_id=beijing")
        self.unlock()
        status, state, _ = self.request("/api/metro-map?city_id=beijing")
        self.assertEqual(status, 200)
        self.assertEqual(state["city_id"], "beijing")
        self.assertEqual(state["city_name"].removesuffix("市"), "北京")
        self.assertTrue(state["supported"])
        self.assert_http_error(404, "/api/metro-map?city_id=does-not-exist")
        self.assert_http_error(404, "/api/metro-map?city_id=..%2Ftrip.db")

    def test_update_requires_selected_user_origin_and_rate_limit(self):
        path, payload = "/api/metro-map/update", {"city_id": "beijing"}
        self.assert_http_error(401, path, "POST", payload)
        self.unlock()
        self.assert_http_error(401, path, "POST", payload)
        self.claim()
        self.assert_http_error(403, path, "POST", payload, origin="https://unrelated.example")
        with patch.object(self.running.httpd.rate_limiter, "allow", return_value=False):
            self.assert_http_error(429, path, "POST", payload)

    def test_metadata_is_project_scoped_and_deleted_project_cannot_query(self):
        child = self.create_project()
        self.unlock()
        self.assert_http_error(401, "/api/metro-map?city_id=beijing", project=child["id"])
        self.unlock(child["id"])
        self.claim(child["id"])
        _, main, _ = self.request("/api/metro-map?city_id=beijing")
        _, other, _ = self.request("/api/metro-map?city_id=beijing", project=child["id"])
        self.assertEqual(main["supported"], other["supported"])
        self.assertEqual(main["map"], other["map"])
        self.request("/api/admin/cities", "PUT", {"id": "beijing", "name": "测试轨道城市0"},
                     project=child["id"])
        renamed = self.request("/api/metro-map?city_id=beijing", project=child["id"])[1]
        self.assertEqual(renamed["city_name"], "测试轨道城市0")
        self.assertTrue(renamed["supported"])
        self.assertFalse(renamed["available"])
        self.assertEqual(self.request("/api/metro-map?city_id=beijing")[1]["map"], main["map"])
        self.request(f"/api/admin/projects/{child['id']}", "DELETE",
                     {"confirm_name": child["name"]})
        self.assert_http_error(404, "/api/metro-map?city_id=beijing", project=child["id"])
        self.assert_http_error(404, "/api/metro-map/update", "POST", {"city_id": "beijing"},
                               project=child["id"])

    def test_unsupported_city_returns_clear_state_without_map(self):
        self.unlock()
        self.claim()
        city = self.request("/api/cities", "POST", {"name": "未开通轨道的测试城市"})[1]["city"]
        state = self.request(f"/api/metro-map?city_id={city['id']}")[1]
        self.assertFalse(state["supported"])
        self.assertFalse(state["available"])
        self.assertIsNone(state["map"])
        self.assertTrue(state["message"])

    def test_update_uses_project_city_name_and_rejects_client_download_target(self):
        self.unlock()
        self.claim()
        state = self.running.httpd.metro_store.describe("beijing", "北京")
        with patch.object(self.running.httpd.metro_store, "request_update", return_value=state) as update:
            self.assert_http_error(400, "/api/metro-map/update", "POST", {
                "city_id": "beijing", "city_name": "上海", "source_id": "shanghai",
                "source_url": "http://127.0.0.1/private", "download_url": "file:///etc/passwd",
            })
            update.assert_not_called()
            status, result, _ = self.request("/api/metro-map/update", "POST", {"city_id": "beijing"})
        self.assertEqual(status, 202)
        self.assertEqual(result["city_id"], "beijing")
        self.assertEqual(update.call_count, 1)
        self.assertEqual(update.call_args.args[0], "beijing")
        self.assertEqual(update.call_args.args[1].removesuffix("市"), "北京")
        with patch.object(self.running.httpd.metro_store, "request_update") as update:
            self.assert_http_error(404, "/api/metro-map/update", "POST", {"city_id": "missing-city"})
            update.assert_not_called()

    def test_map_image_is_public_sandboxed_and_survives_deleted_main_project(self):
        state = self.running.httpd.metro_store.describe("beijing", "北京")
        path = state["map"]["image_url"]
        status, data, headers = self.request(path, raw=True)
        self.assertEqual(status, 200)
        self.assertEqual(data, SAFE_SVG)
        self.assertTrue(headers["Content-Type"].startswith("image/svg+xml"))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        policies = "; ".join(headers.get_all("Content-Security-Policy", []))
        self.assertIn("sandbox", policies)
        self.assertIn("default-src 'none'", policies)
        self.admin()
        main = self.request("/api/admin/projects")[1]["projects"][0]
        self.request("/api/admin/projects/main", "DELETE", {"confirm_name": main["name"]})
        self.assertEqual(self.request(path, raw=True)[1], SAFE_SVG)
        self.assertEqual(self.request("/api/health")[1]["status"], "ok")

    def test_http_background_update_polls_new_version_and_old_link_stays_valid(self):
        self.unlock()
        self.claim()
        old_url = self.request("/api/metro-map?city_id=beijing")[1]["map"]["image_url"]
        with patch.object(metro_maps, "source_metadata", return_value=upstream(NEW_SVG)) as metadata, \
                patch.object(metro_maps, "fetch_bytes", return_value=NEW_SVG) as fetch:
            status, initial, _ = self.request("/api/metro-map/update", "POST", {"city_id": "beijing"})
            self.assertEqual(status, 202)
            self.assertIn(initial["update"]["status"], ("queued", "running", "ready"))
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                final = self.request("/api/metro-map?city_id=beijing")[1]
                if final["update"]["status"] not in ("queued", "running"):
                    break
                time.sleep(0.01)
            self.assertEqual(final["update"]["status"], "ready")
            self.assertEqual(final["map"]["version"], hashlib.sha256(NEW_SVG).hexdigest())
            metadata.assert_called_once()
            fetch.assert_called_once()
        self.assertEqual(self.request(final["map"]["image_url"], raw=True)[1], NEW_SVG)
        self.assertEqual(self.request(old_url, raw=True)[1], SAFE_SVG)

    def test_static_svg_navigation_is_sandboxed_and_versioned_images_revalidate(self):
        _, _, static_headers = self.request("/assets/shanghai-metro.svg", raw=True)
        policies = "; ".join(static_headers.get_all("Content-Security-Policy", []))
        self.assertIn("sandbox", policies)
        self.assertIn("default-src 'none'", policies)
        state = self.running.httpd.metro_store.describe("beijing", "北京")
        path = state["map"]["image_url"]
        _, _, headers = self.request(path, raw=True)
        self.assertIn("immutable", headers["Cache-Control"])
        request = urllib.request.Request(self.running.base_url + path, headers={"If-None-Match": headers["ETag"]})
        with self.assertRaises(urllib.error.HTTPError) as not_modified:
            self.opener.open(request, timeout=3)
        self.assertEqual(not_modified.exception.code, 304)
        self.assertEqual(not_modified.exception.read(), b"")

    def test_public_image_route_rejects_invalid_versions_paths_and_extension(self):
        version = hashlib.sha256(SAFE_SVG).hexdigest()
        for path in (f"/api/metro-maps/image/unknown/{version}.svg",
                     f"/api/metro-maps/image/beijing/{'0' * 64}.svg",
                     f"/api/metro-maps/image/beijing/{version}.html",
                     "/api/metro-maps/image/beijing/..%2F..%2Ftrip.db",
                     f"/api/metro-maps/image/%2e%2e/{version}.svg"):
            with self.subTest(path=path):
                self.assert_http_error(404, path)


if __name__ == "__main__":
    unittest.main()
