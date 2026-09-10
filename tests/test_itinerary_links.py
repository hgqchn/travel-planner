"""Saved itinerary/place relationships against temporary databases, without AI/network."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server


class ItineraryLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "trip.db"
        self.seed = Path(self.temp.name) / "empty.json"
        self.seed.write_text("{}", encoding="utf-8")
        self.user = "测试旅客"
        self.init_project(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def init_project(self, path):
        server.init_database(path, "test-project", self.seed, empty_project=True)
        server.claim_identity(path, {"user_id": self.user})

    def attraction(self, name="青石公园", *, city="shanghai", path=None, **extra):
        return server.create_item(path or self.path, "attraction", {
            "city_id": city, "name": name, **extra,
        }, self.user)["item"]

    def plan_payload(self, **extra):
        return {
            "city_id": "shanghai", "date": "2026-10-01", "start_time": "09:00",
            "title": "游览青石公园", "category": "景点", "location": "青石公园",
            **extra,
        }

    def plan(self, *, path=None, **extra):
        return server.create_item(path or self.path, "itinerary", self.plan_payload(**extra), self.user)["item"]

    def snapshot(self, city="shanghai", *, path=None):
        return server.snapshot(path or self.path, city)["items"]

    def update(self, item, **extra):
        return server.update_item(self.path, item["kind"], item["id"], {
            **item, **extra,
        }, self.user)["item"]

    def only_link(self, plan):
        current = next(item for item in self.snapshot(plan["city_id"])["itinerary"] if item["id"] == plan["id"])
        self.assertEqual(len(current["linked_attractions"]), 1)
        return current["linked_attractions"][0]

    def test_existing_attraction_links_in_both_directions(self):
        attraction = self.attraction()
        plan = self.plan()
        current = self.snapshot()
        self.assertEqual(len(current["attraction"]), 1)
        self.assertEqual(self.only_link(plan), {
            "id": attraction["id"], "name": attraction["name"], "matched_name": attraction["name"],
        })
        self.assertTrue(current["attraction"][0]["in_itinerary"])
        self.assertEqual(current["attraction"][0]["itinerary_refs"], [{
            "id": plan["id"], "date": plan["date"], "start_time": plan["start_time"], "title": plan["title"],
        }])

    def test_unplanned_attraction_has_explicit_empty_relationship(self):
        self.attraction()
        item = self.snapshot()["attraction"][0]
        self.assertIs(item["in_itinerary"], False)
        self.assertEqual(item["itinerary_refs"], [])

    def test_unknown_sight_is_added_and_reused_for_later_plan(self):
        first = self.plan()
        second = self.plan(date="2026-10-02", title="再次游览青石公园")
        current = self.snapshot()
        self.assertEqual(len(current["attraction"]), 1)
        self.assertEqual(current["attraction"][0]["name"], "青石公园")
        self.assertEqual({ref["id"] for ref in current["attraction"][0]["itinerary_refs"]}, {first["id"], second["id"]})
        self.assertEqual(self.only_link(first)["id"], self.only_link(second)["id"])

    def test_multiple_explicit_places_are_deduplicated(self):
        existing = self.attraction()
        plan = self.plan(title="上午参观", location="", attraction_names=["青石公园", "白鹭博物馆", "青石公园"])
        current = self.snapshot()
        self.assertEqual({item["name"] for item in current["attraction"]}, {"青石公园", "白鹭博物馆"})
        linked = current["itinerary"][0]["linked_attractions"]
        self.assertEqual(len(linked), 2)
        self.assertIn(existing["id"], {item["id"] for item in linked})
        self.assertTrue(all(item["in_itinerary"] for item in current["attraction"]))

    def test_multiple_places_in_location_are_separate_attractions(self):
        self.plan(title="上午游览", location="青石公园、白鹭博物馆")
        current = self.snapshot()
        self.assertEqual({item["name"] for item in current["attraction"]}, {"青石公园", "白鹭博物馆"})
        self.assertEqual(len(current["itinerary"][0]["linked_attractions"]), 2)

    def test_title_mentions_existing_attraction_but_notes_do_not_link(self):
        park = self.attraction()
        museum = self.attraction("白鹭博物馆")
        plan = self.plan(title="青石公园晨游", location="", notes="下雨时可改去白鹭博物馆")
        self.assertEqual(self.only_link(plan)["id"], park["id"])
        by_id = {item["id"]: item for item in self.snapshot()["attraction"]}
        self.assertFalse(by_id[museum["id"]]["in_itinerary"])

    def test_road_route_and_service_locations_never_create_sights(self):
        names = (
            "南京西路", "人民路与中山路交叉口", "颐和园路", "外滩至豫园步行路线",
            "世纪大道", "红旗街道", "上海虹桥站", "青石酒店", "青石餐厅",
        )
        for index, name in enumerate(names):
            with self.subTest(name=name):
                self.plan(title="地点安排 " + str(index), location=name, category="景点")
        current = self.snapshot()
        self.assertEqual(current["attraction"], [])
        self.assertTrue(all(not item["linked_attractions"] for item in current["itinerary"]))

    def test_explicit_names_cannot_reclassify_roads_as_sights(self):
        self.plan(title="步行", location="", attraction_names=["南京西路", "世纪大道", "人民路路口"])
        self.assertEqual(self.snapshot()["attraction"], [])

    def test_routes_in_locations_or_explicit_names_are_not_single_sights(self):
        routes = (
            "人民广场到静安寺", "青石公园—白鹭博物馆", "南京西路到静安寺",
            "外滩至豫园", "青石公园→白鹭博物馆", "青石公园->白鹭博物馆",
        )
        for route in routes:
            for explicit in (False, True):
                with self.subTest(route=route, explicit=explicit):
                    self.plan(title="上午游览", location=route,
                              attraction_names=[route] if explicit else [])
        current = self.snapshot()
        self.assertEqual(current["attraction"], [])
        self.assertTrue(all(not item["linked_attractions"] for item in current["itinerary"]))

    def test_place_name_beginning_with_you_is_not_treated_as_visit_verb(self):
        plan = self.plan(title="游埠古镇", location="游埠古镇")
        current = self.snapshot()
        self.assertEqual([item["name"] for item in current["attraction"]], ["游埠古镇"])
        self.assertEqual(self.only_link(plan)["name"], "游埠古镇")

    def test_route_only_title_cannot_create_a_single_route_sight(self):
        for title in ("外滩至豫园", "人民广场到静安寺", "青石公园—白鹭博物馆"):
            with self.subTest(title=title):
                self.plan(title=title, location="")
        self.assertEqual(self.snapshot()["attraction"], [])

    def test_official_combined_name_with_dash_is_not_excluded_as_route(self):
        name = "八达岭—慕田峪长城旅游区"
        plan = self.plan(city_id="beijing", title="长城游览", location=name)
        current = self.snapshot("beijing")
        self.assertEqual([item["name"] for item in current["attraction"]], [name])
        self.assertEqual(self.only_link(plan)["name"], name)

    def test_create_and_update_return_same_relationships_as_snapshot(self):
        plan = self.plan()
        self.assertEqual(plan, self.snapshot()["itinerary"][0])
        updated = self.update(plan, date="2026-10-03", notes="新安排")
        self.assertEqual(updated, self.snapshot()["itinerary"][0])
        attraction = self.snapshot()["attraction"][0]
        changed = self.update(attraction, description="补充介绍")
        self.assertEqual(changed, self.snapshot()["attraction"][0])

    def test_dates_and_title_in_reverse_links_follow_itinerary_edit(self):
        plan = self.plan()
        self.update(plan, date="2026-10-03", start_time="14:30", title="下午游览青石公园")
        refs = self.snapshot()["attraction"][0]["itinerary_refs"]
        self.assertEqual(refs, [{"id": plan["id"], "date": "2026-10-03", "start_time": "14:30", "title": "下午游览青石公园"}])

    def test_changing_plan_unlinks_previous_sight_without_deleting_it(self):
        plan = self.plan(attraction_names=["青石公园"])
        self.update(plan, title="游览白鹭博物馆", location="白鹭博物馆", attraction_names=["白鹭博物馆"])
        by_name = {item["name"]: item for item in self.snapshot()["attraction"]}
        self.assertEqual(set(by_name), {"青石公园", "白鹭博物馆"})
        self.assertFalse(by_name["青石公园"]["in_itinerary"])
        self.assertTrue(by_name["白鹭博物馆"]["in_itinerary"])

    def test_deleting_plan_keeps_attraction_unplanned(self):
        plan = self.plan()
        server.delete_item(self.path, "itinerary", plan["id"], {"version": plan["version"]}, self.user)
        current = self.snapshot()
        self.assertEqual(current["itinerary"], [])
        self.assertEqual(len(current["attraction"]), 1)
        self.assertFalse(current["attraction"][0]["in_itinerary"])

    def test_deleting_attraction_does_not_resurrect_it_on_refresh(self):
        plan = self.plan()
        attraction = self.snapshot()["attraction"][0]
        server.delete_item(self.path, "attraction", attraction["id"], {"version": attraction["version"]}, self.user)
        for _ in range(2):
            current = self.snapshot()
            self.assertEqual(current["attraction"], [])
            self.assertEqual(current["itinerary"][0]["linked_attractions"], [])
        self.update(plan, notes="确定前往")
        self.assertEqual(len(self.snapshot()["attraction"]), 1)

    def test_renaming_linked_attraction_preserves_id_and_original_match(self):
        # A user enters the city before editing; hydrate its shared cache once.
        self.snapshot()
        attraction = self.attraction()
        plan = self.plan()
        self.update(attraction, name="青石文化公园")
        linked = self.only_link(plan)
        self.assertEqual(linked["id"], attraction["id"])
        self.assertEqual(linked["name"], "青石文化公园")
        self.assertEqual(linked["matched_name"], "青石公园")
        self.update(plan, notes="提前到达")
        self.assertEqual(self.only_link(plan)["id"], attraction["id"])
        self.assertEqual(len(self.snapshot()["attraction"]), 1)

    def test_official_combined_alias_matches_saved_canonical_attraction(self):
        canonical = "八达岭—慕田峪长城旅游区"
        attraction = self.attraction(canonical, city="beijing")
        plan = self.plan(city_id="beijing", title="游览八达岭长城", location="八达岭长城")
        self.assertEqual(self.only_link(plan)["id"], attraction["id"])
        self.assertEqual(len(self.snapshot("beijing")["attraction"]), 1)

    def test_short_location_name_reuses_saved_scenic_area(self):
        attraction = self.attraction("外滩风景区")
        plan = self.plan(title="游览外滩", location="外滩")
        self.assertEqual(self.only_link(plan), {
            "id": attraction["id"], "name": "外滩风景区", "matched_name": "外滩",
        })
        self.assertEqual(len(self.snapshot()["attraction"]), 1)

    def test_short_title_name_with_activities_reuses_saved_scenic_area(self):
        attraction = self.attraction("外滩风景区")
        plan = self.plan(title="外滩日落散步", location="")
        self.assertEqual(self.only_link(plan), {
            "id": attraction["id"], "name": "外滩风景区", "matched_name": "外滩",
        })
        self.assertEqual(len(self.snapshot()["attraction"]), 1)

    def test_same_name_in_another_city_is_not_linked(self):
        elsewhere = self.attraction(city="beijing")
        plan = self.plan()
        self.assertNotEqual(self.only_link(plan)["id"], elsewhere["id"])
        self.assertFalse(self.snapshot("beijing")["attraction"][0]["in_itinerary"])

    def test_projects_never_share_relationships(self):
        first = self.plan()
        other_path = Path(self.temp.name) / "other-project" / "trip.db"
        self.init_project(other_path)
        other = self.attraction(path=other_path)
        second = self.plan(path=other_path)
        item = self.snapshot(path=other_path)["attraction"][0]
        self.assertEqual(item["id"], other["id"])
        self.assertEqual([ref["id"] for ref in item["itinerary_refs"]], [second["id"]])
        self.assertEqual([ref["id"] for ref in self.snapshot()["attraction"][0]["itinerary_refs"]], [first["id"]])

    def test_import_uses_complete_attraction_even_when_plan_is_first(self):
        result = server.import_ai_items(self.path, "shanghai", self.user, [
            {"kind": "itinerary", "data": self.plan_payload()},
            {"kind": "attraction", "data": {"name": "青石公园", "description": "完整的景点介绍", "duration": "2 小时"}},
        ], "same-batch")
        current = self.snapshot()
        self.assertEqual((result["created"], len(result["item_ids"]), result["auto_added_attractions"]), (2, 2, 0))
        self.assertEqual(len(current["attraction"]), 1)
        self.assertEqual(current["attraction"][0]["description"], "完整的景点介绍")
        self.assertTrue(current["attraction"][0]["in_itinerary"])

    def test_import_receipt_separates_automatic_attractions_and_is_idempotent(self):
        payload = [{"kind": "itinerary", "data": self.plan_payload()}]
        result = server.import_ai_items(self.path, "shanghai", self.user, payload, "auto-batch")
        self.assertEqual((result["created"], len(result["item_ids"]), result["auto_added_attractions"]), (1, 1, 1))
        self.assertEqual(server.import_ai_items(self.path, "shanghai", self.user, payload, "auto-batch"), result)
        self.assertEqual(len(self.snapshot()["attraction"]), 1)
        self.assertEqual(len(self.snapshot()["itinerary"]), 1)

    def test_capacity_failure_rolls_back_plan_and_automatic_attractions(self):
        self.attraction("已有博物馆")
        before = server.snapshot(self.path, "shanghai")
        with patch.object(server, "MAX_ITEMS_PER_KIND", 1), self.assertRaises(server.ApiError) as error:
            self.plan()
        self.assertEqual(error.exception.status, 507)
        after = server.snapshot(self.path, "shanghai")
        self.assertEqual(after["items"], before["items"])
        self.assertEqual(after["revision"], before["revision"])

    def test_import_capacity_failure_rolls_back_all_selected_items(self):
        before = server.snapshot(self.path, "shanghai")
        with patch.object(server, "MAX_ITEMS_TOTAL", 1), self.assertRaises(server.ApiError):
            server.import_ai_items(self.path, "shanghai", self.user, [
                {"kind": "itinerary", "data": self.plan_payload()},
            ], "too-many")
        after = server.snapshot(self.path, "shanghai")
        self.assertEqual(after["items"], before["items"])
        self.assertEqual(after["revision"], before["revision"])

    def test_stale_itinerary_version_cannot_create_new_attraction(self):
        plan = self.plan()
        self.update(plan, notes="刚更新")
        before = server.snapshot(self.path, "shanghai")
        with self.assertRaises(server.ApiError) as error:
            self.update(plan, location="白鹭博物馆", attraction_names=["白鹭博物馆"])
        self.assertEqual(error.exception.status, 409)
        after = server.snapshot(self.path, "shanghai")
        self.assertEqual(after["items"], before["items"])
        self.assertEqual(after["revision"], before["revision"])

    def test_invalid_attraction_names_are_rejected_before_writes(self):
        invalid = ("青石公园", None, {}, [1], [None], ["长" * 61], ["公园" + str(n) for n in range(13)], ["青石\x00公园"])
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(server.ApiError) as error:
                self.plan(attraction_names=value)
            self.assertEqual(error.exception.status, 400)
        current = self.snapshot()
        self.assertEqual(current["itinerary"], [])
        self.assertEqual(current["attraction"], [])

    def test_empty_explicit_array_keeps_legacy_inference(self):
        plan = self.plan(attraction_names=[])
        self.assertEqual(self.only_link(plan)["name"], "青石公园")

    def test_legacy_backfill_runs_once_without_resurrecting_deleted_attraction(self):
        now = server.utc_now()
        with server.connect_db(self.path) as db:
            db.execute("DELETE FROM meta WHERE key='itinerary_links_v1'")
            db.execute("""INSERT INTO items(id,city_id,kind,position,payload,version,created_at,updated_at,created_by,updated_by)
                          VALUES(?,?,'itinerary',1,?,1,?,?,?,?)""",
                       ("abcdef123456", "shanghai", json.dumps(self.plan_payload(), ensure_ascii=False), now, now, self.user, self.user))
        server.init_database(self.path, "test-project", self.seed, empty_project=True)
        current = self.snapshot()
        self.assertEqual(len(current["attraction"]), 1)
        self.assertTrue(current["attraction"][0]["in_itinerary"])
        attraction = current["attraction"][0]
        server.delete_item(self.path, "attraction", attraction["id"], {"version": attraction["version"]}, self.user)
        server.init_database(self.path, "test-project", self.seed, empty_project=True)
        self.assertEqual(self.snapshot()["attraction"], [])


if __name__ == "__main__":
    unittest.main()
