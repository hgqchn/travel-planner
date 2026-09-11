"""Offline province matching, separate from the curated default city list."""

from __future__ import annotations

import json
import unicodedata
from functools import lru_cache
from pathlib import Path


REGIONS_PATH = Path(__file__).resolve().with_name("china_province_cities.json")


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _short_name(name: str) -> str:
    # Do not strip 区: names such as 朝阳区 are not the city 朝阳.
    for suffix in ("自治州", "地区", "市", "县", "旗", "盟"):
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[:-len(suffix)]
    return name


@lru_cache(maxsize=1)
def load_regions() -> tuple[dict, ...]:
    return tuple(json.loads(REGIONS_PATH.read_text(encoding="utf-8"))["provinces"])


@lru_cache(maxsize=1)
def _region_names() -> dict[str, tuple[dict, ...]]:
    names: dict[str, dict[str, dict]] = {}
    for province in load_regions():
        prefixes = {province["name"], province["short_name"], *province.get("aliases", [])}
        for city in province["cities"]:
            record = {
                "code": city["code"],
                "name": city["name"],
                "short_name": _short_name(city["name"]),
                "province": province["short_name"],
                "province_name": province["name"],
                "province_code": province["code"],
                "kind": city["kind"],
            }
            aliases = {city["name"], record["short_name"], *city.get("aliases", [])}
            qualified = {prefix + alias for prefix in prefixes for alias in aliases}
            for alias in aliases | qualified:
                names.setdefault(_normalize(alias), {})[record["code"]] = record
    return {name: tuple(records.values()) for name, records in names.items()}


def find_region(name: str) -> dict | None:
    """Exact names/aliases only; ambiguous districts and unknown names stay custom."""
    candidates = _region_names().get(_normalize(name), ())
    if not candidates:
        return None
    # A city shorthand takes priority over a same-named county (e.g. 朝阳).
    priority = {"municipality": 0, "prefecture": 0, "city": 1, "county": 2}
    best = min(priority[entry["kind"]] for entry in candidates)
    matches = [entry for entry in candidates if priority[entry["kind"]] == best]
    return matches[0] if len(matches) == 1 else None
