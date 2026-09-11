"""Reproducible name-only aliases; never infer scenic ratings or membership."""
from __future__ import annotations

import re
import unicodedata


SUFFIXES = ('生态文化旅游区', '文化生态旅游区', '生态休闲旅游区', '文化旅游景区',
            '生态旅游景区', '旅游度假景区', '风景名胜区', '风景旅游区', '生态旅游区',
            '文化旅游区', '旅游度假区', '休闲旅游区', '旅游景区', '风景区', '旅游区', '景区')
GENERIC = {'中国', '中华', '国家', '森林', '国家森林', '国家湿地', '湿地', '旅游',
           '生态', '文化', '古城', '古镇', '公园', '博物馆', '纪念馆', '风景', '红色',
           '历史文化', '城市', '森林公园', '湿地公园', '遗址公园'}


def clean(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value or '')).strip('*＊')


def identity(row):
    return tuple(row.get(key) or '' for key in ('name', 'province', 'city'))


class AliasNames:
    def __init__(self, regions):
        self.regions = regions
        self.geographic_names = {clean(n) for p in regions for n in
                                 [p['name'], p.get('short_name', ''), *[c['name'] for c in p['cities']]]}

    def valid(self, name):
        return (2 <= len(name) <= 100 and name not in GENERIC and name not in self.geographic_names
                and not re.search(r'曾用名[:：]|\d{4}年|https?://|景区名称|变更|^\d+$', name)
                and name.count('(') == name.count(')'))

    def prefixes(self, row):
        province = next((p for p in self.regions if clean(p['name']) == clean(row.get('province'))), None)
        values = {clean(row.get('province')), clean(row.get('city'))}
        for value in list(values):
            if value.endswith(('省', '市')):
                values.add(value[:-1])
        if province:
            values.add(province.get('short_name', ''))
            cities = province['cities']
            # County prefixes must belong to this record's city. Unknown city
            # is not permission to strip every county name in the province.
            city = next((c for c in cities if clean(c['name']).removesuffix('市') ==
                         clean(row.get('city')).removesuffix('市')), None)
            if not city and province['name'].endswith('市'):
                allowed = cities
            elif city and city['kind'] == 'prefecture':
                allowed = [c for c in cities if c['code'].startswith(city['code'][:4])]
            else:
                allowed = [city] if city else []
            values.update(c['name'] for c in allowed)
        values.update(row.get('city_aliases', []))
        return sorted({clean(v) for v in values if v}, key=lambda v: (-len(v), v))

    def variants(self, row, name=None):
        original = clean(row['name'] if name is None else name)
        if not self.valid(original):
            return set()
        names = {original}
        current = original
        prefixes = self.prefixes(row)
        for _ in range(6):
            prefix = next((p for p in prefixes if current.startswith(p) and self.valid(current[len(p):])), None)
            if not prefix:
                break
            current = current[len(prefix):]
            names.add(current)
        for value in list(names):
            for suffix in SUFFIXES:
                if value.endswith(suffix):
                    if self.valid(value[:-len(suffix)]):
                        names.add(value[:-len(suffix)])
                    break
        # No arbitrary bracket removal, comma splitting or fuzzy substrings:
        # these may denote a restricted component rather than the whole area.
        return names


def apply_aliases(document, records):
    """Apply only exact file/name/province/city targets, preserving source data."""
    additions = {identity(row): row['aliases'] for row in records}
    items = []
    for row in document.get('items', []):
        extra = additions.get(identity(row), [])
        if not isinstance(extra, list) or not all(isinstance(alias, str) for alias in extra):
            raise ValueError('景区别名补充必须是文字列表。')
        items.append(dict(row, aliases=list(dict.fromkeys([*row.get('aliases', []), *extra]))))
    return dict(document, items=items)
