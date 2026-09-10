"""Shared fixed categories and bounded tags, including conservative legacy mapping."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

TAXONOMY = json.loads(Path(__file__).with_name("place_taxonomy.json").read_text(encoding="utf-8"))
CATEGORIES = TAXONOMY["categories"]
MAX_TAGS = TAXONOMY["max_tags"]
MAX_TAG_LENGTH = TAXONOMY["max_tag_length"]


def normalize_tags(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_TAGS:
        raise ValueError(f"标签须为列表，最多 {MAX_TAGS} 个。")
    result, seen = [], set()
    for tag in value:
        if not isinstance(tag, str):
            raise ValueError("每个标签必须是文字。")
        tag = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", tag)).strip()
        if not tag:
            continue
        if len(tag) > MAX_TAG_LENGTH or any(ord(c) < 32 for c in tag):
            raise ValueError(f"每个标签最多 {MAX_TAG_LENGTH} 个字符，不可包含控制字符。")
        if tag.casefold() not in seen:
            result.append(tag)
            seen.add(tag.casefold())
    return result


# Names resolve broad old labels such as 亲子 / 早餐 before label-based aliases.
NAME_RULES = {
    "attraction": [
        ("博物展馆", r"博物馆|美术馆|科技馆|天文馆|纪念馆|艺术宫|展览馆"),
        ("动植物园", r"动物园|植物园|水族馆|海洋馆|海洋公园"),
        ("主题乐园", r"迪士尼|欢乐谷|环球影城|游乐园|水上乐园|主题乐园"),
        ("寺庙宗教", r"清真寺|教堂|道观|静安寺|龙华寺|寺$|寺[（(]"),
        ("历史古迹", r"故宫|长城|天坛|地坛|祭坛|遗址|兵马俑|城墙|故居"),
        ("自然风光", r"国家森林公园|地质公园"),
        ("园林公园", r"颐和园|圆明园|豫园|古猗园|拙政园|留园|公园"),
        ("古镇街区", r"古镇|古村|街区|新天地|田子坊|老街|胡同"),
        ("城市观景", r"外滩|滨江|东方明珠|观景台|观景塔|上海中心"),
        ("购物市集", r"步行街|购物|商场|商城|集市|夜市"),
        ("休闲运动", r"温泉|滑雪|露营|运动场|体育馆"),
        ("演艺文娱", r"剧院|剧场|音乐厅|演艺"),
        ("自然风光", r"草原|沙漠|湿地|保护区|森林|瀑布|火山|湖$|山$|海岛"),
    ],
    "food": [
        ("饮品", r"奶茶|酥油茶|豆汁|咖啡|果汁|豆浆|酸梅汤"),
        ("糕点甜品", r"月饼|条头糕|海棠糕|酒酿圆子|梨膏糖|驴打滚|艾窝窝|青团|糖葫芦|冰淇淋|奶酪|双皮奶|哈达饼"),
        ("火锅涮煮", r"火锅|涮羊肉|串串香|麻辣烫"),
        ("烧烤烤肉", r"烧烤|烤肉|烤羊|烤全羊|烤鸭|羊肉串"),
        ("面点饼类", r"小笼|生煎|饺|馄饨|烧麦|烧卖|包子|汤包|馒头|烧饼|葱油饼|煎饼|蟹壳黄|油条|对夹"),
        ("面条米粉", r"拌面|拉面|炸酱面|刀削面|牛肉面|米线|米粉|酸辣粉|河鲜面|面条|面$"),
        ("米饭粥类", r"饭团|粢饭|煲仔饭|炒饭|盖饭|粥"),
        ("汤羹炖品", r"腌笃鲜|羊杂汤|羊肉汤|煲汤|肉羹"),
        ("特色菜肴", r"红烧肉|油爆河虾|八宝鸭|烤麸|鳝糊|草头圈子|熏鱼|手把肉|锅包肉"),
    ],
}

LEGACY_TAGS = {
    "皇家园林": ["皇家"], "皇家祭坛": ["皇家", "历史文化"],
    "历史建筑": ["历史文化"], "历史遗迹": ["历史文化"], "历史展馆": ["历史文化"],
    "博物馆": [], "公园": [], "城市公园": [], "园林": [], "古镇": [], "城市地标": [],
    "美术馆": ["艺术"], "艺术展览": ["艺术"], "天文科普": ["科普", "天文"],
    "自然科学": ["科普"], "亲子娱乐": ["亲子"], "Citywalk": ["城市漫步"],
    "滨江漫步": ["滨水", "城市漫步"], "滨水景观": ["滨水"],
    "草原自然景观": ["草原"], "湖泊湿地": ["湖泊", "湿地"], "沙漠湖泊": ["沙漠", "湖泊"],
    "京味早餐": ["京味", "早餐"], "街头早点": ["早餐"],
    "本帮热菜": ["本帮菜"], "本帮冷菜": ["本帮菜"], "本帮宴席菜": ["本帮菜"],
    "本帮河鲜": ["本帮菜", "河鲜"], "本帮面点": ["本帮菜"], "本帮炸物": ["本帮菜"],
    "京菜": ["京菜"], "京味甜点": ["京味"], "蒙古族风味": ["蒙古族风味"],
}
LABEL_RULES = {
    "attraction": [
        ("博物展馆", r"博物|展馆|展览|美术|科普|自然科学"),
        ("动植物园", r"动物|植物|水族"),
        ("寺庙宗教", r"寺庙|寺院|宗教|教堂|道观"),
        ("园林公园", r"园林|公园"),
        ("历史古迹", r"遗迹|遗址|古迹|祭坛|宫殿|历史建筑|历史文化|^历史$"),
        ("主题乐园", r"乐园|游乐"),
        ("购物市集", r"商业街|购物|集市|夜市"),
        ("城市观景", r"滨江|滨水|观景|地标|城市风光"),
        ("古镇街区", r"古镇|古村|街区|风貌|石库门|Citywalk|城市漫步|街巷"),
        ("自然风光", r"草原|沙漠|湖泊|湿地|地质|自然保护|山川|自然风光"),
        ("休闲运动", r"温泉|运动|露营|滑雪"),
        ("演艺文娱", r"演艺|演出|剧场"),
    ],
    "food": [
        ("饮品", r"饮品|饮料"), ("火锅涮煮", r"火锅|涮煮"),
        ("烧烤烤肉", r"烧烤|烤肉"), ("面条米粉", r"面食|河鲜面|米粉|米线"),
        ("糕点甜品", r"甜点|甜品|糕点|糯米点心"),
        ("面点饼类", r"蒸点|汤包|煎点|面点|汤点"),
        ("米饭粥类", r"米饭|粥类"), ("汤羹炖品", r"汤类|汤菜|汤食|汤羹"),
        ("特色菜肴", r"热菜|冷菜|宴席菜|河鲜|京菜|本帮菜|特色菜"),
        ("风味小吃", r"小吃|炸物"),
    ],
}


def classify_legacy(kind: str, value: dict) -> str:
    category = value.get("category", "").strip()
    if category in CATEGORIES[kind] or not category:
        return category
    for rules, source in ((NAME_RULES[kind], value.get("name", "")), (LABEL_RULES[kind], category)):
        for target, pattern in rules:
            if re.search(pattern, source, re.I):
                return target
    return ""


def normalize_place(kind: str, value: dict, *, legacy: bool = False) -> dict:
    if kind not in CATEGORIES:
        return dict(value)
    result = dict(value)
    category = result.get("category", "").strip()
    tags = normalize_tags(result.get("tags", []))
    if legacy:
        result["category"] = classify_legacy(kind, result)
        # Preserve old descriptive labels as tags, without repeating the new category.
        if category and category not in CATEGORIES[kind]:
            old_tags = re.split(r"\s*[/／、,，;；]\s*", category)
            old_tags = [label for tag in old_tags if tag and len(tag) <= MAX_TAG_LENGTH
                        for label in LEGACY_TAGS.get(tag, [tag])]
            tags = normalize_tags((tags + [tag for tag in old_tags if tag != result["category"]])[:MAX_TAGS])
    elif category not in CATEGORIES[kind] and category:
        # Recognized old labels remain compatible with older clients; arbitrary new ones fail.
        mapped = classify_legacy(kind, {"category": category})
        if not mapped:
            raise ValueError("请选择预设的景点或美食分类。")
        return normalize_place(kind, result, legacy=True)
    else:
        result["category"] = category
    result["tags"] = tags
    if kind == "food":
        result.setdefault("cuisine", "")
        if legacy and not result['cuisine']:
            if '本帮' in category:
                result['cuisine'] = '本帮菜'
            elif category == '京菜':
                result['cuisine'] = '京菜'
    return result


def migrate_project(db, timestamp: str) -> int:
    """Once per project, preserve original payloads and increment item/revision versions."""
    if db.execute("SELECT 1 FROM meta WHERE key='place_taxonomy_v1'").fetchone():
        return 0
    db.execute("CREATE TABLE IF NOT EXISTS place_taxonomy_history (item_id TEXT PRIMARY KEY, payload TEXT NOT NULL, version INTEGER NOT NULL, migrated_at TEXT NOT NULL)")
    changed = 0
    for row in db.execute("SELECT id,kind,payload,version FROM items WHERE kind IN ('attraction','food')").fetchall():
        original = json.loads(row["payload"])
        updated = normalize_place(row["kind"], original, legacy=True)
        if updated == original:
            continue
        db.execute("INSERT OR IGNORE INTO place_taxonomy_history VALUES(?,?,?,?)", (row["id"], row["payload"], row["version"], timestamp))
        db.execute("UPDATE items SET payload=?,version=version+1,updated_at=?,updated_by='system' WHERE id=?", (json.dumps(updated, ensure_ascii=False), timestamp, row["id"]))
        changed += 1
    if changed:
        db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
    db.execute("INSERT INTO meta(key,value) VALUES('place_taxonomy_v1',1)")
    return changed
