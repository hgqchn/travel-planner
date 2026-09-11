#!/usr/bin/env python3
"""Build separate aliases for every bundled 4A/5A row using stdlib only.

Pass downloaded simplified Wikipedia list HTML to extract single-target links.
Ratings, source dates and application databases are never changed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scenic_aliases import AliasNames, clean
from scenic_catalog import ScenicCatalog, _key, _city, _province
from dev.build_wikipedia_4a import WikiTableParser


class LinkTables(WikiTableParser):
    def __init__(self):
        super().__init__()
        self.active_link = None

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        attrs = dict(attrs)
        if tag in ('td', 'th') and self.cell is not None:
            self.cell['links'] = []
        if tag == 'a' and self.cell is not None and not self.skip_sup:
            url = attrs.get('href', '')
            if url.startswith('/wiki/') and ':' not in unquote(url[len('/wiki/'):]) and 'redlink' not in url:
                self.active_link = {'name': attrs.get('title', ''), 'url': 'https://zh.wikipedia.org' + url, 'text': ''}
                self.cell['links'].append(self.active_link)

    def handle_data(self, data):
        if self.active_link is not None and not self.skip_sup:
            self.active_link['text'] += data
        super().handle_data(data)

    def handle_endtag(self, tag):
        if tag in ('a', 'td', 'th'):
            self.active_link = None
        super().handle_endtag(tag)


def expand(rows):
    carry = {}
    for cells in rows:
        row = {i: value for i, (value, remaining) in carry.items()}
        following = {i: (value, remaining - 1) for i, (value, remaining) in carry.items() if remaining > 1}
        col = 0
        for cell in cells:
            while col in row:
                col += 1
            for offset in range(cell['colspan']):
                row[col + offset] = cell
                if cell['rowspan'] > 1:
                    following[col + offset] = (cell, cell['rowspan'] - 1)
            col += cell['colspan']
        yield [row[i] for i in sorted(row)]
        carry = following


def wiki_rows(path, grade, regions):
    text = path.read_text(encoding='utf-8')
    revision = re.search(r'"wgRevisionId":(\d+)', text)
    if not revision:
        raise ValueError('Wikipedia source has no revision id')
    url = f'https://zh.wikipedia.org/w/index.php?title={quote("国家" + grade + "级旅游景区")}&oldid={revision[1]}&variant=zh-cn'
    sections = []
    if grade == '4A':
        for region in regions:
            marker = '<h3 id="' + region['name'] + '"'
            if marker not in text:
                continue
            start = text.index(marker)
            stop = re.search(r'<div class="mw-heading mw-heading[23]">', text[start:])
            sections.append((region['name'], text[start:start + stop.start()] if stop else text[start:]))
    else:
        sections.append(('', text))
    result = []
    for province, section in sections:
        parser = LinkTables(); parser.feed(section)
        for table in parser.tables:
            rows = list(expand(table))
            if not rows:
                continue
            headers = [cell['text'] for cell in rows[0]]
            if grade == '4A' and headers[:2] == ['名称', '所在地']:
                result.extend((province, row[1]['text'], row[0]) for row in rows[1:] if len(row) >= 2)
            elif grade == '5A' and '景点名称' in headers and '省级行政区' in headers:
                for row in rows[1:]:
                    if len(row) == len(headers):
                        result.append((row[0]['text'], row[2]['text'] or row[3]['text'], row[headers.index('景点名称')]))
    if len(result) < (4000 if grade == '4A' else 300):
        raise ValueError(f'Unexpected {grade} table coverage: {len(result)}')
    return result, {'source_url': url, 'revision': int(revision[1]), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                    'row_count': len(result), 'purpose': '名称及词条链接对照；不读取或更改评级',
                    'attribution': '维基百科贡献者', 'license': 'CC BY-SA 4.0'}


def build(args):
    directory = ROOT / 'resources/scenic'
    regions = json.loads((ROOT / 'china_province_cities.json').read_text())['provinces']
    names = AliasNames(regions)
    paths = sorted(directory.glob('*-official.json')) + [directory / '4a-wikipedia.json', directory / 'rating-corrections.json']
    documents = {p.name: json.loads(p.read_text()) for p in paths}
    records, index, flat, locations = {}, defaultdict(list), [], {}
    for region in regions:
        prefectures = {c['code'][:4]: c['name'] for c in region['cities'] if c['kind'] == 'prefecture'}
        for city in region['cities']:
            parent = region['name'] if region['name'].endswith('市') else prefectures.get(city['code'][:4], city['name'])
            locations[(_province(region['name']), _city(city['name']))] = _city(parent)
    for filename, doc in documents.items():
        records[filename] = []
        for row in doc['items']:
            aliases = names.variants(row)
            for alias in row.get('aliases', []):
                aliases.update(names.variants(row, alias))
            aliases.discard(row['name'])
            entry = {key: row.get(key) or '' for key in ('name', 'province', 'city')}
            entry.update(aliases=sorted(aliases), references=[])
            records[filename].append(entry)
            if filename == 'rating-corrections.json':
                continue
            idx = len(flat)
            flat.append((filename, row, entry))
            for key in {_key(n) for n in [row['name'], *row.get('aliases', []), *aliases]}:
                index[(_province(row['province']), key)].append(idx)
    matched = Counter(); skipped = Counter(); sources = []
    for path, grade in ((args.wiki4a, '4A'), (args.wiki5a, '5A')):
        rows, source = wiki_rows(path, grade, regions); sources.append(source)
        for province, city, cell in rows:
            linked = {link['url']: link for link in cell['links']}
            if len(linked) != 1:
                skipped['zero_or_multiple_links'] += 1; continue
            link = next(iter(linked.values()))
            label, title = clean(cell['text']), clean(link['name'])
            if label != clean(link['text']):
                skipped['link_covers_only_part_of_name'] += 1; continue
            # A disambiguated title may be useful as-is, but deleting its
            # qualifier could broaden a component (e.g. a museum exhibition).
            if not names.valid(label) or not names.valid(title):
                skipped['invalid_name'] += 1; continue
            keys = names.variants({'name': label, 'province': province, 'city': city})
            # A table can link a resort to its town, parent landscape, owner,
            # or a single museum. A hyperlink alone does not prove an alias.
            # Accept only matching name cores; unrelated old/common names
            # require an explicit curated source below.
            title_base = re.sub(r'\([^()]+\)$', '', title)
            title_keys = names.variants({'name': title_base, 'province': province, 'city': city})
            equivalent_title = bool({_key(n) for n in keys} & {_key(n) for n in title_keys})
            if not equivalent_title:
                skipped['article_is_not_equivalent_name'] += 1
            ids = set(i for key in keys for i in index.get((_province(province), _key(key)), []))
            parent = locations.get((_province(province), _city(city)), _city(city))
            ids = {i for i in ids if _city(flat[i][1].get('city') or '') in {_city(city), parent}
                   or (not flat[i][1].get('city') and province in {'北京市', '天津市', '上海市', '重庆市'})}
            groups = {_key(flat[i][1]['name']) for i in ids}
            if len(groups) != 1:
                skipped['missing_or_ambiguous_target'] += 1; continue
            for i in ids:
                filename, row, entry = flat[i]
                # Multiple links/components in the canonical name are never
                # collapsed into a lone Wikipedia link title.
                if re.search(r'[—－·•、]|[及和].+[园山湖寺馆]', row['name']):
                    skipped['combined_target'] += 1; continue
                for candidate in sorted({label, title} if equivalent_title else {label}):
                    if candidate == row['name'] or candidate in entry['aliases']:
                        continue
                    entry['aliases'].append(candidate)
                    entry['references'].append({'alias': candidate, 'source_url': source['source_url'],
                                                'article_url': link['url'], 'source_type': 'wikipedia'})
                    matched[grade] += 1
    curated = json.loads((directory / 'alias-curated.json').read_text())
    for group in curated['items']:
        targets = [(row, entry) for filename, row, entry in flat
                   if _province(row['province']) == _province(group['province']) and row['name'] in group['names']]
        if not targets:
            raise ValueError(f'Curated alias target not found: {group["names"]}')
        for row, entry in targets:
            for alias in group['aliases']:
                if alias != row['name'] and alias not in entry['aliases']:
                    entry['aliases'].append(alias)
                    entry['references'].append({'alias': alias, 'source_url': group['source_url'], 'source_type': 'official'})
    # Derive the same spellings for a sourced short name (e.g. 沈阳故宫).
    for entries in records.values():
        for entry in entries:
            for alias in list(entry['aliases']):
                entry['aliases'].extend(names.variants(entry, alias))
            entry['aliases'] = sorted(set(entry['aliases']) - {entry['name']})
            if not entry['references']:
                del entry['references']
    # A new alias must not make an already resolved official name ambiguous.
    baseline = ScenicCatalog([doc for filename, doc in documents.items() if filename != 'rating-corrections.json'])
    conflicts = []
    for filename, original, entry in flat:
        if filename == '4a-wikipedia.json':
            continue
        city = {'name': original.get('city') or original['province'], 'province': original['province']}
        kept = []
        for alias in entry['aliases']:
            previous = [baseline.enrich({'name': variant}, city)['scenic_rating_info']
                        for variant in sorted(names.variants(original, alias))]
            prior = next((p for p in previous if p['status'] == 'catalog'
                          and _key(p['name']) != _key(original['name'])), None)
            if alias not in original.get('aliases', []) and prior:
                conflicts.append({'name': original['name'], 'alias': alias, 'existing_target': prior['name']})
            else:
                kept.append(alias)
        entry['aliases'] = kept
        if 'references' in entry:
            entry['references'] = [r for r in entry['references'] if r['alias'] in kept]
    # Withdrawals must recognize every new spelling of their old canonical
    # name. Only intersect within the same province/city; do not use fuzziness.
    for original, correction in zip(documents['rating-corrections.json']['items'], records['rating-corrections.json']):
        keys = {_key(n) for n in [original['name'], *original.get('aliases', []), *correction['aliases']]}
        for filename, row, entry in flat:
            if _province(row['province']) != _province(original['province']) or _city(row.get('city') or '') != _city(original['city']):
                continue
            if keys.intersection(_key(n) for n in [row['name'], *row.get('aliases', [])]):
                correction['aliases'] = sorted(set(correction['aliases']) | set(entry['aliases']))
    counts = {filename: {'records': len(entries), 'with_aliases': sum(bool(r['aliases']) for r in entries),
                         'aliases': sum(len(r['aliases']) for r in entries)} for filename, entries in records.items()}
    result = {'version': 1, 'generated_at': args.date,
              'coverage_note': '逐条处理全部已收录4A/5A名录及取消降级记录。aliases为空表示未产生可用简称，不代表现实中无别名。机械简称来自原名录名称和明确行政前缀，references记录额外来源。组合景区成员不自动视为整个景区别名；不保证穷尽地方俗称。',
              'sources': sources,
              'input_sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                               for path in [*paths, directory / 'alias-curated.json', ROOT / 'china_province_cities.json']},
              'summary': counts, 'wiki_added': dict(matched), 'wiki_skipped': dict(skipped),
              'conflicting_aliases_skipped': conflicts, 'files': records}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'summary': counts, 'wiki_added': dict(matched), 'wiki_skipped': dict(skipped)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wiki4a', type=Path, required=True)
    parser.add_argument('--wiki5a', type=Path, required=True)
    parser.add_argument('--date', required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'resources/scenic/aliases.json')
    build(parser.parse_args())
