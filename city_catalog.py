"""Bundled city search metadata, deliberately independent of user content."""

from __future__ import annotations

import json
import hashlib
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from china_regions import REGIONS_PATH, find_region


CATALOG_PATH = Path(__file__).resolve().with_name("city_catalog.json")
CATALOG_MIGRATION_KEY = "city_catalog_v1"
CATALOG_EXPANSION_KEY = "city_catalog_v3"
# Do not restore cities people deliberately removed from the former defaults.
LEGACY_CITY_IDS = frozenset({
    "shanghai", "beijing", "shenzhen", "guangzhou", "xian", "luoyang", "changsha",
    "chongqing", "chengdu", "hohhot", "hulunbuir", "arxan", "manzhouli", "ordos",
})


def normalize_city_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


@lru_cache(maxsize=1)
def load_catalog() -> tuple[dict[str, Any], ...]:
    """Load a small trusted local file; no network lookup or coordinates needed."""
    document = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return tuple(document["cities"])


@lru_cache(maxsize=1)
def catalog_names() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for city in load_catalog():
        for name in (city["name"], *city["aliases"]):
            normalized = normalize_city_name(name)
            if normalized in result and result[normalized]["id"] != city["id"]:
                raise ValueError(f"城市目录包含不明确别名：{name}")
            result[normalized] = city
    return result


def find_catalog_city(name: str) -> dict[str, Any] | None:
    """Match only full names and explicit aliases, never fuzzy pinyin matches."""
    return catalog_names().get(normalize_city_name(name))


def find_city_region(name: str) -> dict[str, Any] | None:
    match = find_catalog_city(name)
    return find_region(match["name"] if match else name)


@lru_cache(maxsize=1)
def city_metadata_version() -> str:
    """Invalidate polling caches when bundled geographic metadata changes."""
    return hashlib.sha256(CATALOG_PATH.read_bytes() + REGIONS_PATH.read_bytes()).hexdigest()[:16]


def city_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    """Enrich a row without replacing its ID, user-visible name or ordering."""
    city = dict(row)
    match = find_catalog_city(city["name"])
    region = find_city_region(city["name"])
    aliases = list(match["aliases"]) if match else []
    if region:
        aliases = list(dict.fromkeys([*aliases, region["name"], region["short_name"], region["province_name"]]))
    map_query = city["name"]
    if region:
        map_query = region["name"] if region["name"] == region["province_name"] else region["province_name"] + region["name"]
    city.update({
        "catalog_id": match["id"] if match else None,
        "canonical_name": match["name"] if match else region["short_name"] if region else city["name"],
        "province": region["province"] if region else match["province"] if match else "自定义",
        "province_name": region["province_name"] if region else match["province"] if match else "",
        "pinyin": match["pinyin"] if match else "",
        "initials": match["initials"] if match else "",
        "aliases": aliases,
        # Official URI keyword search works for custom cities and avoids
        # inventing or mixing GCJ-02/WGS-84 center coordinates.
        "map_query": match["map_query"] if match else map_query,
    })
    return city
