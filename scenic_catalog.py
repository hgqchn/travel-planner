"""Offline scenic-rating snapshots; conservative, city-scoped matching.

Ratings describe the dated source, not a claim of real-time accreditation. Never
use substring/fuzzy matches: a shop or a sub-attraction is not its parent resort.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from city_catalog import find_catalog_city


DATA_DIR = Path(__file__).resolve().parent / "resources" / "scenic"
RATINGS = {"", "4A", "5A"}


def _key(value: str) -> str:
    return re.sub(r"[\s·・•，,。()（）\[\]【】\-—]", "", unicodedata.normalize("NFKC", value)).casefold()


def _city(value: str) -> str:
    match = find_catalog_city(value)
    return _key(match["name"] if match else value).removesuffix("市")


def _province(value: str) -> str:
    value = _key(value)
    for suffix in ("壮族自治区", "回族自治区", "维吾尔自治区", "自治区", "特别行政区", "省", "市"):
        if value.endswith(suffix):
            return value[:-len(suffix)]
    return value


def _variants(value: str) -> set[str]:
    value = _key(value)
    result = {value} if len(value) >= 2 else set()
    for suffix in ("旅游景区", "风景名胜区", "风景区", "景区"):
        if value.endswith(suffix) and len(value) - len(suffix) >= 2:
            result.add(value[:-len(suffix)])
    return result


_DESIGNATION = re.compile(r"(?:文化旅游区|旅游景区|风景名胜区|风景旅游区|旅游区|风景区|景区)$")
_MEMBER_SEPARATOR = re.compile(r"[—–\-·•・、]+")
_SIGHT_ENDING = re.compile(r"(?:园|寺|庙|祠|城|山|峰|湖|河|溪|岭|峪|瀑布|峡谷|洋|洞|岩|岛|洲|庄|村|镇|楼|宫|陵|塔|馆|滩|关|丘|林|草原|湿地|泉|阁|堰)$")
_SHARED_DESCRIPTORS = {"长城": ("岭", "峪", "关")}


def _combined_components(entry: Mapping[str, Any]) -> set[str]:
    """Extract explicitly enumerated members for editable draft names only.

    Keep punctuation until after parsing. In particular, a location annotation
    is not a list of member attractions, nor is an arbitrary substring a name.
    """
    prefixes = sorted({_key(entry["province"]), _province(entry["province"]),
                       _key(entry["city"]), _city(entry["city"])}, key=len, reverse=True)

    def strip_prefix(value: str) -> str:
        for _ in range(2):
            for prefix in prefixes:
                if prefix and value.startswith(prefix) and len(value) > len(prefix) + 1:
                    value = value[len(prefix):].removeprefix("市")
                    break
        return value

    result = set()
    for name in (entry["name"], *entry["aliases"]):
        value = re.sub(r"\s", "", unicodedata.normalize("NFKC", name)).casefold()
        value = _DESIGNATION.sub("", strip_prefix(value))
        if "(" in value or ")" in value:
            # Only a terminal parenthesized list of sights qualifies. Reject
            # administrative annotations and bare place names such as counties.
            bracket = re.fullmatch(r"[^()]*\(([^()]+)\)", value)
            if not bracket:
                continue
            value = bracket[1]
            if re.search(r"省|市|县|区|州|盟|地区", value):
                continue
        parts = _MEMBER_SEPARATOR.split(value)
        if len(parts) < 2 or any(not part for part in parts):
            continue
        parts = [_DESIGNATION.sub("", strip_prefix(part)) for part in parts]
        # Dots also occur in personal names, dates and brands. A separator
        # alone is insufficient: every member must have a scenic-place ending.
        if not all(_SIGHT_ENDING.search(part) for part in parts):
            continue
        members = {part for part in parts if len(part) >= 2
                   and not re.search(r"周围|周边|附近|及其|等", part)
                   and not re.search(r"(?:省|市|县|区|州|盟|地区)$", part)}
        result.update(members)
        # A岭—B峪长城 may share its descriptor. Keep the permissible endings
        # explicit: A山·B古镇 does not establish an A山古镇 alias.
        for descriptor, endings in _SHARED_DESCRIPTORS.items():
            if parts[-1].endswith(descriptor) and len(parts[-1]) > len(descriptor):
                result.update(part + descriptor for part in parts[:-1]
                              if part in members and part.endswith(endings))
    return {key for member in result for key in _variants(member)}


class ScenicCatalog:
    def __init__(self, documents: list[dict[str, Any]], *, corrections: list[dict[str, Any]] | None = None):
        self.entries: list[dict[str, Any]] = []
        self.index: dict[str, list[int]] = {}
        self.component_index: dict[str, list[int]] = {}
        self.fingerprint = hashlib.sha256(json.dumps([documents, corrections or []], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
        # Use the same strict city/name matcher for the small withdrawal list.
        # Its synthetic rating is internal only; preserve whether the decision
        # was read in an official announcement or in a report quoting it.
        self._corrections = None
        self._correction_source_types = {}
        if corrections:
            for item in corrections:
                if item.get('action') not in {'removed', 'downgraded'} or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', item.get('effective_date', '')):
                    raise ValueError('评级更正需要明确的处理类型与生效日期。')
                source_type = item.get('source_type', 'reported_official')
                if source_type not in {'official', 'reported_official'}:
                    raise ValueError('评级更正来源类别无效。')
                self._correction_source_types[(item.get('source_url', ''), item['effective_date'])] = source_type
            self._corrections = ScenicCatalog([{'items': [dict(item, rating='4A', source_type='official',
                                                              as_of=item['effective_date']) for item in corrections]}])
        for document in documents:
            for raw in document.get("items", []):
                if raw.get("rating") not in {"4A", "5A"} or not isinstance(raw.get("name"), str):
                    raise ValueError("景区名录的名称或级别无效。")
                entry = {key: raw.get(key, document.get(key, "")) or "" for key in
                         ("name", "rating", "province", "city", "source_name", "source_url", "as_of")}
                entry['source_type'] = raw.get('source_type', document.get('source_type', 'official'))
                if entry['source_type'] not in {'official', 'wikipedia'}:
                    raise ValueError('景区名录来源类别无效。')
                source = urlsplit(entry["source_url"])
                if source.scheme not in {"https", "http"} or not source.hostname or source.username or source.password:
                    raise ValueError("景区名录需要有效的公开来源链接。")
                aliases = raw.get("aliases", [])
                if not isinstance(aliases, list) or not all(isinstance(name, str) for name in aliases):
                    raise ValueError("景区别名必须是文字列表。")
                entry["aliases"] = aliases
                entry["city_aliases"] = raw.get("city_aliases", [])
                if not isinstance(entry["city_aliases"], list) or not all(isinstance(name, str) for name in entry["city_aliases"]):
                    raise ValueError("景区城市别名必须是文字列表。")
                index = len(self.entries)
                self.entries.append(entry)
                if entry['source_type'] == 'official':
                    for key in _combined_components(entry):
                        self.component_index.setdefault(key, []).append(index)
                prefixes = sorted({_key(entry["province"]), _province(entry["province"]),
                                   _key(entry["city"]), _city(entry["city"])}, key=len, reverse=True)
                names = {entry["name"], *aliases}
                # Strip known administrative prefixes, never arbitrary districts
                # or components of a combined scenic-area designation.
                for name in list(names):
                    normalized = _key(name)
                    for _ in range(2):
                        for prefix in prefixes:
                            if prefix and normalized.startswith(prefix) and len(normalized) > len(prefix) + 1:
                                normalized = normalized[len(prefix):]
                                if normalized.startswith("市"):
                                    normalized = normalized[1:]
                                break
                    names.add(normalized)
                for key in {key for name in names for key in _variants(name)}:
                    self.index.setdefault(key, []).append(index)

    @staticmethod
    def _in_city(entry: Mapping[str, Any], city: str | Mapping[str, Any] | None) -> bool:
        if not city:
            return False
        if isinstance(city, str):
            city = {"name": city}
        name = str(city.get("canonical_name") or city.get("name", ""))
        canonical = find_catalog_city(name)
        province = str(city.get("province") or (canonical["province"] if canonical else ""))
        if province and province != "自定义" and _province(province) != _province(entry["province"]):
            return False
        city_key = _city(name)
        if city_key and any(city_key == _city(alias) for alias in entry["city_aliases"]):
            return True
        if entry["city"]:
            return bool(city_key) and city_key == _city(entry["city"])
        # Some national lists provide only a province and full official name.
        # Such records qualify only when their full name spells out this city,
        # or the province is a direct-administered municipality.
        if city_key in {"北京", "上海", "天津", "重庆"}:
            return city_key == _province(entry["province"])
        full_name = _key(entry["name"])
        return bool(city_key) and any(full_name.startswith(prefix + city_key + "市")
                                      for prefix in ("", _key(entry["province"]), _province(entry["province"])))

    def enrich(self, payload: Mapping[str, Any], city: str | Mapping[str, Any] | None) -> dict[str, Any]:
        result = dict(payload)
        rating = payload.get("scenic_rating", "")
        rating = rating if isinstance(rating, str) and rating in RATINGS else ""
        ids = {index for key in _variants(str(payload.get("name", ""))) for index in self.index.get(key, [])}
        candidates = [self.entries[index] for index in sorted(ids) if self._in_city(self.entries[index], city)]
        # Wikipedia fills gaps. A newer article date must never downgrade a
        # matching official 5A record or erase an official-source ambiguity.
        official = [entry for entry in candidates if entry['source_type'] == 'official']
        if official:
            candidates = official
        # A newer source for the exact same official name supersedes its older
        # snapshot (e.g. a former 4A has since become 5A). Distinct names sharing
        # an alias remain ambiguous and are never guessed by rating priority.
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for entry in candidates:
            key = (_key(entry["name"]), _province(entry["province"]))
            groups.setdefault(key, []).append(entry)
        candidates = []
        for entries in groups.values():
            latest = max(entry['as_of'] for entry in entries)
            # Exact duplicate sources need only one citation. Conflicting
            # ratings on the latest date retain multiple candidates.
            ratings = {entry['rating']: entry for entry in entries if entry['as_of'] in {'', latest}}
            candidates.extend(ratings.values())
        info = {"status": "unverified" if rating else "unknown", "name": "", "source_name": "", "source_url": "", "as_of": "", "source_type": ""}
        if len(candidates) == 1:
            match = candidates[0]
            rating = match["rating"]
            info = {key: match[key] for key in ("name", "source_name", "source_url", "as_of", "source_type")}
            info["status"] = "catalog" if match['source_type'] == 'official' else "reference"
        elif candidates:
            info["status"] = "ambiguous"
        if self._corrections:
            correction = self._corrections.enrich({'name': str(payload.get('name', ''))}, city)['scenic_rating_info']
            if correction['status'] == 'catalog':
                reinstated = info['status'] == 'catalog' and info['as_of'] > correction['as_of']
                if not reinstated:
                    rating = ''
                    source_type = self._correction_source_types[(correction['source_url'], correction['as_of'])]
                    info = dict(correction, status='outdated', source_type=source_type)
        result.update(scenic_rating=rating, scenic_rating_info=info)
        return result

    def for_city(self, city: str | Mapping[str, Any], limit: int = 60) -> list[dict[str, str]]:
        if limit <= 0:
            return []
        entries = sorted((entry for entry in self.entries if self._in_city(entry, city)),
                         key=lambda entry: (entry['source_type'] != 'official', -int(entry["rating"][0]), entry["name"]))
        result, seen = [], set()
        for entry in entries:
            matched = self.enrich({'name': entry['name']}, city)
            info = matched['scenic_rating_info']
            if info['status'] not in {'catalog', 'reference'}:
                continue
            key = (_key(info['name']), matched['scenic_rating'])
            if key in seen:
                continue
            seen.add(key)
            result.append({'name': info['name'][:100], 'scenic_rating': matched['scenic_rating'],
                           'as_of': info['as_of'], 'source_type': info['source_type']})
            if len(result) >= limit:
                break
        return result


@lru_cache(maxsize=1)
def get_catalog() -> ScenicCatalog:
    from scenic_aliases import apply_aliases

    aliases_path = DATA_DIR / 'aliases.json'
    aliases = json.loads(aliases_path.read_text(encoding='utf-8'))['files'] if aliases_path.is_file() else {}
    paths = sorted(DATA_DIR.glob("*-official.json"))
    wikipedia = DATA_DIR / '4a-wikipedia.json'
    if wikipedia.is_file():
        paths.append(wikipedia)
    corrections_path = DATA_DIR / 'rating-corrections.json'
    corrections = apply_aliases(json.loads(corrections_path.read_text(encoding='utf-8')),
                                aliases.get(corrections_path.name, []))['items'] if corrections_path.is_file() else []
    return ScenicCatalog([apply_aliases(json.loads(path.read_text(encoding="utf-8")), aliases.get(path.name, []))
                          for path in paths], corrections=corrections)


def enrich_attraction(payload: Mapping[str, Any], city: str | Mapping[str, Any] | None) -> dict[str, Any]:
    return get_catalog().enrich(payload, city)


def canonical_name_correction(name: str, city: str | Mapping[str, Any]) -> dict[str, str] | None:
    """Resolve exact aliases or uniquely enumerated members of official names.

    This is an editable AI-draft name change, never implicit rating inheritance
    for stored component sights, nearby shops or substring matches.
    """
    catalog = get_catalog()
    info = catalog.enrich({"name": name}, city)["scenic_rating_info"]
    combined = False
    if info["status"] in {"unknown", "reference"}:
        ids = {index for key in _variants(name) for index in catalog.component_index.get(key, [])}
        candidates = {}
        for index in ids:
            entry = catalog.entries[index]
            if not catalog._in_city(entry, city):
                continue
            target_info = catalog.enrich({"name": entry["name"]}, city)["scenic_rating_info"]
            if target_info["status"] != "catalog":
                return None
            candidates[_key(target_info["name"])] = target_info
        if len(candidates) != 1:
            return None
        info = next(iter(candidates.values()))
        combined = True
    target = info["name"]
    if info["status"] != "catalog" or not target or target == name or len(target) > 60:
        return None
    if catalog.enrich({"name": target}, city)["scenic_rating_info"]["status"] != "catalog":
        return None
    reason = "已使用名录中的合并景区名称，介绍和交通仍以原景点为目标。" if combined else "已使用官方名录中的完整景点名称。"
    return {"from": name, "to": target, "reason": reason}


def rated_payload(kind: str, payload: dict[str, str], city: str | Mapping[str, Any]) -> dict[str, str]:
    if kind != "attraction":
        return payload
    return dict(payload, scenic_rating=enrich_attraction(payload, city)["scenic_rating"])
