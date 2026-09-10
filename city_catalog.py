"""Bundled city search metadata, deliberately independent of user content."""

from __future__ import annotations

import json
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping


CATALOG_PATH = Path(__file__).resolve().with_name("city_catalog.json")
CATALOG_MIGRATION_KEY = "city_catalog_v1"


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


def city_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    """Enrich a row without replacing its ID, user-visible name or ordering."""
    city = dict(row)
    match = find_catalog_city(city["name"])
    city.update({
        "catalog_id": match["id"] if match else None,
        "canonical_name": match["name"] if match else city["name"],
        "province": match["province"] if match else "自定义",
        "pinyin": match["pinyin"] if match else "",
        "initials": match["initials"] if match else "",
        "aliases": list(match["aliases"]) if match else [],
        # Official URI keyword search works for custom cities and avoids
        # inventing or mixing GCJ-02/WGS-84 center coordinates.
        "map_query": match["map_query"] if match else city["name"],
    })
    return city
