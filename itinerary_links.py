"""Project-local itinerary/attraction links with conservative place extraction."""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Callable

from scenic_catalog import canonical_name_correction, enrich_attraction


MAX_ATTRACTION_NAMES = 12
MAX_ATTRACTION_NAME_LENGTH = 60
_SPLIT = re.compile(r"[、,，;；\n]+|\s*(?:→|->|⇒|➡|\s+\+\s+)\s*")
_ROUTE = re.compile(r".+(?:到|至|→|->|⇒|➡|[—–]).+")
_NON_VISIT = re.compile(r"用餐|餐饮|早餐|午餐|晚餐|住宿|入住|退房|交通|接送|换乘|返程|出发|路线|路口")
_NON_SIGHT = re.compile(r"路口|交叉口|交汇处|街道|路段|路线|线路|沿线|地铁|公交|车站|机场|客运站|酒店|宾馆|民宿|餐厅|饭店|咖啡店|停车场|高速|国道|省道|\d+号|https?://|(?:路|街|大道|大街|弄|巷)$")
_GENERIC = {"景点", "景区", "公园", "城市公园", "博物馆", "观景台", "市区", "市中心", "周边", "沿途", "待定", "自由活动", "散步", "游览", "休息", "酒店", "住所"}
_SIGHT = re.compile(r"(?:博物馆|纪念馆|美术馆|科技馆|天文馆|展览馆|公园|乐园|景区|风景区|旅游区|古镇|古村|遗址|故居|长城|城墙|寺|庙|宫|园|山|湖|滩|瀑布|峡谷|草原|湿地|海岛|陵|塔|故里|阁|堰|泉|岭|峪)$|(?:park|museum|palace)$", re.I)
_VISIT_PREFIX = re.compile(r"^(?:(?:参观|游览|游玩|探访|打卡|漫步|游逛|前往|抵达|到达|登上)\s*)+")
_ACTIVITY_SUFFIX = re.compile(r"(?:日落|夜景|晨游|夜游|观光|游览|游玩|参观|散步|漫步|拍照|打卡|看日落|赏夜景|半日游|一日游|观景平台|观景台|入口|东门|西门|南门|北门|正门|集合|游览区|周边)$")


def name_key(value: str) -> str:
    return re.sub(r"[\s·・•，,。()（）\[\]【】\-—]", "", unicodedata.normalize("NFKC", value)).casefold()


def _name_variants(name: str) -> list[str]:
    result = [name]
    for suffix in ('旅游景区', '风景名胜区', '风景区', '旅游区', '景区'):
        if name.endswith(suffix) and len(name) > len(suffix) + 1:
            result.append(name[:-len(suffix)])
            break
    return result


def normalize_attraction_names(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_ATTRACTION_NAMES:
        raise ValueError("关联景点须为列表，最多 12 个。")
    result, seen = [], set()
    for raw in value:
        if not isinstance(raw, str):
            raise ValueError("关联景点名称必须是文字。")
        name = unicodedata.normalize("NFKC", raw).strip()
        if len(name) > MAX_ATTRACTION_NAME_LENGTH or any(ord(c) < 32 for c in name):
            raise ValueError("每个关联景点名称最多 60 字，不可包含控制字符。")
        if name and name_key(name) not in seen:
            result.append(name)
            seen.add(name_key(name))
    return result


def ensure_schema(db) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS itinerary_attractions (
        itinerary_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        attraction_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        matched_name TEXT NOT NULL,
        position INTEGER NOT NULL,
        PRIMARY KEY (itinerary_id, attraction_id)
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS itinerary_attractions_by_attraction ON itinerary_attractions(attraction_id)")


def _canonical_key(name: str, city: str) -> str:
    correction = canonical_name_correction(name, city)
    return name_key(_name_variants(correction['to'] if correction else name)[-1])


def _text_mentions(text: str, name: str) -> bool:
    """Accept a named visit, not a shop, route or arbitrary substring mention."""
    text, name = name_key(text), name_key(name)
    if not name or name not in text:
        return False
    start = text.find(name)
    before, after = text[:start], text[start + len(name):]
    if before and not re.fullmatch(r"(?:上午|下午|晚上|早上|傍晚|夜间|第一站|第二站|参观|游览|游玩|探访|打卡|漫步|游逛|前往|抵达|到达|登上|登|逛|游|在|到)+", before):
        return False
    while after:
        match = _ACTIVITY_SUFFIX.search(after)
        if not match:
            return False
        after = after[:match.start()]
    return True


def _pieces(text: str) -> list[str]:
    return [part.strip(" \t。:：") for part in _SPLIT.split(text) if part.strip()]


def candidate_names(payload: dict, attractions: list[dict], city: str, previous: list[dict] = ()) -> list[str]:
    explicit = normalize_attraction_names(payload.get('attraction_names', []))
    location = payload.get('location', '')
    route = bool(_ROUTE.search(location)) and enrich_attraction({'name': location}, city)['scenic_rating_info']['status'] != 'catalog'
    pieces = explicit or ([] if route else _pieces(location))
    category = payload.get('category', '')
    if not explicit and _NON_VISIT.search(category):
        return []
    known = [name for item in attractions for name in _name_variants(item['name'])] + [item['matched_name'] for item in previous]
    result = []
    for part in pieces:
        if name_key(part) in {name_key(name) for name in known}:
            result.append(part)
            continue
        # A known sight can be used with a viewing platform or visit suffix.
        matches = [name for name in known if _text_mentions(part, name)]
        if matches:
            result.extend(matches)
            continue
        name = _VISIT_PREFIX.sub('', part).strip()
        if _ROUTE.search(name) and enrich_attraction({'name': name}, city)['scenic_rating_info']['status'] != 'catalog':
            continue
        if not name or len(name) > MAX_ATTRACTION_NAME_LENGTH or name in _GENERIC or _NON_SIGHT.search(name):
            continue
        if explicit or _SIGHT.search(name) or enrich_attraction({'name': name}, city)['scenic_rating_info']['status'] == 'catalog':
            result.append(name)
    # Titles can identify a visit when location is empty or more descriptive.
    if not explicit:
        for part in _pieces(payload.get('title', '')):
            matches = [name for name in known if _text_mentions(part, name)]
            result.extend(matches)
            if not location.strip() and not matches:
                name = _VISIT_PREFIX.sub('', part).strip()
                while _ACTIVITY_SUFFIX.search(name):
                    name = _ACTIVITY_SUFFIX.sub('', name)
                if (1 < len(name) <= MAX_ATTRACTION_NAME_LENGTH and name not in _GENERIC
                        and not _NON_SIGHT.search(name) and not _ROUTE.search(name) and _SIGHT.search(name)):
                    result.append(name)
    # Inference is bounded even for punctuation-heavy legacy entries.
    return normalize_attraction_names(list(dict.fromkeys(result))[:MAX_ATTRACTION_NAMES])


def sync_itinerary(db, row, city: str, create_attraction: Callable, *, allow_create: bool = True) -> int:
    """Called inside the caller's write transaction; creation shares its limits."""
    payload = json.loads(row['payload'])
    attractions = [dict(json.loads(record['payload']), id=record['id']) for record in
                   db.execute("SELECT id,payload FROM items WHERE city_id=? AND kind='attraction' ORDER BY position,id", (row['city_id'],))]
    previous = [dict(record) for record in db.execute(
        "SELECT l.* FROM itinerary_attractions l JOIN items a ON a.id=l.attraction_id WHERE l.itinerary_id=? AND a.city_id=? AND a.kind='attraction'",
        (row['id'], row['city_id']))]
    names = candidate_names(payload, attractions, city, previous)
    selected, added = [], 0
    for name in names:
        key = name_key(name)
        previous_ids = {link['attraction_id'] for link in previous if name_key(link['matched_name']) == key}
        candidates = [item for item in attractions if item['id'] in previous_ids]
        if not candidates:
            candidates = [item for item in attractions if name_key(item['name']) == key]
        if not candidates:
            canonical = _canonical_key(name, city)
            candidates = [item for item in attractions if _canonical_key(item['name'], city) == canonical]
        if len(candidates) > 1:
            # An ambiguous duplicate stays unlinked, never assigned arbitrarily.
            continue
        if candidates:
            attraction = candidates[0]
        elif allow_create:
            attraction = create_attraction(name)
            attractions.append(attraction)
            added += 1
        else:
            continue
        if attraction['id'] not in {item[0] for item in selected}:
            selected.append((attraction['id'], name))
    db.execute("DELETE FROM itinerary_attractions WHERE itinerary_id=?", (row['id'],))
    for position, (attraction_id, matched_name) in enumerate(selected):
        db.execute("INSERT INTO itinerary_attractions VALUES(?,?,?,?)", (row['id'], attraction_id, matched_name, position))
    return added


def annotate_items(grouped: dict, links: list[dict]) -> None:
    itineraries = {item['id']: item for item in grouped['itinerary']}
    attractions = {item['id']: item for item in grouped['attraction']}
    for item in itineraries.values():
        item.setdefault('attraction_names', [])
        item['linked_attractions'] = []
    for item in attractions.values():
        item.update(itinerary_refs=[], in_itinerary=False)
    for link in links:
        itinerary, attraction = itineraries.get(link['itinerary_id']), attractions.get(link['attraction_id'])
        if not itinerary or not attraction or itinerary['city_id'] != attraction['city_id']:
            continue
        itinerary['linked_attractions'].append({'id': attraction['id'], 'name': attraction['name'], 'matched_name': link['matched_name']})
        attraction['itinerary_refs'].append({key: itinerary.get(key, '') for key in ('id', 'date', 'start_time', 'title')})
        attraction['in_itinerary'] = True
    for item in attractions.values():
        item['itinerary_refs'].sort(key=lambda ref: (ref['date'], ref['start_time'], ref['id']))
