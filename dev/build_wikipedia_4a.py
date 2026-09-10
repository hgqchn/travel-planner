#!/usr/bin/env python3
"""Extract the Anhui and Jiangsu 4A tables from a pinned Wikipedia revision.

Uses Python standard library only. Output stays separate from official data.
The two province table rowspans are expanded rather than guessing city names.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
import hashlib
import json
import re
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
TITLE = '国家4A级旅游景区'
REVISION = 94049187
PROVINCES = ('安徽省', '江苏省')
ARTICLE_URL = 'https://zh.wikipedia.org/wiki/' + quote(TITLE)
LICENSE_URL = 'https://creativecommons.org/licenses/by-sa/4.0/deed.zh-hans'


def clean(text):
    return re.sub(r'\s+', ' ', text).strip()


class WikiTableParser(HTMLParser):
    """Read cell text and rowspans, ignoring footnote reference markers."""
    def __init__(self):
        super().__init__()
        self.tables = []
        self.table = None
        self.row = None
        self.cell = None
        self.table_depth = 0
        self.skip_sup = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'table':
            self.table_depth += 1
            if self.table_depth == 1:
                self.table = []
        if self.table_depth != 1:
            return
        if tag == 'tr':
            self.row = []
        if tag in ('td', 'th'):
            self.cell = {'text': [], 'rowspan': int(attrs.get('rowspan', 1)), 'colspan': int(attrs.get('colspan', 1))}
        if tag == 'sup':
            self.skip_sup += 1
        if tag == 'br' and self.cell is not None:
            self.cell['text'].append(' ')

    def handle_endtag(self, tag):
        if tag == 'sup' and self.skip_sup:
            self.skip_sup -= 1
        if self.table_depth == 1:
            if tag in ('td', 'th') and self.cell is not None:
                self.cell['text'] = clean(''.join(self.cell['text']))
                self.row.append(self.cell)
                self.cell = None
            if tag == 'tr' and self.row is not None:
                self.table.append(self.row)
                self.row = None
        if tag == 'table':
            if self.table_depth == 1:
                self.tables.append(self.table)
                self.table = None
            self.table_depth -= 1

    def handle_data(self, data):
        if self.cell is not None and not self.skip_sup:
            self.cell['text'].append(data)


def expand_rows(rows):
    result, carry = [], {}
    for raw in rows:
        row = {col: text for col, (text, remaining) in carry.items()}
        following = {col: (text, remaining - 1) for col, (text, remaining) in carry.items() if remaining > 1}
        col = 0
        for cell in raw:
            while col in row:
                col += 1
            for offset in range(cell['colspan']):
                row[col + offset] = cell['text']
                if cell['rowspan'] > 1:
                    following[col + offset] = (cell['text'], cell['rowspan'] - 1)
            col += cell['colspan']
        result.append([row.get(i, '') for i in range(max(row, default=-1) + 1)])
        carry = following
    if carry:
        raise ValueError('Table ended before its city rowspans were exhausted')
    return result


def extract_province(document, province, permalink):
    start = document.index('<h3 id="' + province + '"')
    next_heading = re.search(r'<div class="mw-heading mw-heading[23]">', document[start:])
    end = start + next_heading.start() if next_heading else len(document)
    section = document[start:end]
    parser = WikiTableParser()
    parser.feed(section)
    if len(parser.tables) != 1:
        raise ValueError(f'{province}: expected one main 4A table, got {len(parser.tables)}')
    rows = expand_rows(parser.tables[0])
    if rows[0][:2] != ['名称', '所在地']:
        raise ValueError(f'{province}: unexpected table columns: {rows[0]}')
    declared_match = re.search(r'(?:共有|合计)(\d+)家', section)
    if not declared_match:
        raise ValueError(f'{province}: missing declared table count')
    declared = int(declared_match.group(1))
    period_match = re.search(r'截[至止](\d{4})年(?:(\d{1,2})月)?', section)
    if not period_match:
        raise ValueError(f'{province}: source period missing')
    year, month = period_match.groups()
    source_period = year + (f'-{int(month):02d}' if month else '')
    as_of = source_period if month else None
    url = permalink + '#' + quote(province)
    items = []
    for i, row in enumerate(rows[1:], start=1):
        if len(row) == 2:
            row.append('')  # Some source rows omit the optional award-date cell.
        if len(row) != 3:
            raise ValueError(f'{province} row {i}: expected 3 cells after rowspan expansion: {row}')
        name, city, awarded_in = row
        if not name or not city or not city.endswith('市') or re.search(r'^\d+$|名称|所在地', name):
            raise ValueError(f'{province} row {i}: invalid attraction or city: {row}')
        item = {
            'name': name, 'province': province, 'city': city, 'rating': '4A',
            'source_url': url, 'source_name': '维基百科《国家4A级旅游景区》',
            'source_type': 'wikipedia', 'as_of': as_of, 'source_period': source_period,
            'as_of_precision': 'month' if month else 'year',
        }
        if awarded_in:
            item['awarded_in'] = awarded_in
        items.append(item)
    distinct = len({(r['city'], r['name']) for r in items})
    summary = {
        'province': province, 'source_url': url, 'as_of': as_of, 'source_period': source_period,
        'declared_count': declared, 'extracted_count': len(items), 'unique_count': distinct,
        'difference': len(items) - declared,
        'difference_note': '按正文表格实际行数提取，不根据段落宣称数量补造记录；差异保留待核验。' if len(items) != declared else '表格实际条数与段落宣称数量一致。',
        'city_counts': dict(Counter(r['city'] for r in items)),
    }
    return items, summary


def build(document, fetched_at):
    revision = int(re.search(r'"wgRevisionId":(\d+)', document).group(1))
    variant = re.search(r'"wgUserVariant":"([^"]+)"', document)
    if not variant or variant.group(1) != 'zh-cn':
        raise ValueError('The saved article must be downloaded using variant=zh-cn; do not relabel another language variant.')
    permalink = 'https://zh.wikipedia.org/w/index.php?' + urlencode({'title': TITLE, 'oldid': revision, 'variant': 'zh-cn'})
    last_modified = re.search(r'<li id="footer-info-lastmod"[^>]*>(.*?)</li>', document, re.S)
    modified_text = clean(re.sub(r'<[^>]+>', '', last_modified.group(1))) if last_modified else None
    if '署名-相同方式共享 4.0' not in document:
        raise ValueError('Expected Wikipedia CC BY-SA 4.0 license notice was not found; review the source terms')
    items, sections = [], []
    for province in PROVINCES:
        part, summary = extract_province(document, province, permalink)
        items.extend(part)
        sections.append(summary)
    result = {
        'source_name': '维基百科《国家4A级旅游景区》安徽省、江苏省章节',
        'source_type': 'wikipedia', 'source_url': ARTICLE_URL, 'permanent_url': permalink,
        'revision': revision, 'language_variant': 'zh-cn', 'revision_last_modified_text': modified_text,
        'retrieved_at': fetched_at, 'source_html_sha256': hashlib.sha256(document.encode('utf-8')).hexdigest(),
        'as_of': None,
        'coverage_note': '维基百科社区编写的补充名单，与官方名录分文件保存。仅提取本条目当前4A列表中的安徽、江苏两节，不读取末尾摘牌表或从旧全A级表补入已升5A的景区。维基声明日期和页面修订日期不同；安徽只明确到2025年，不虚构月份。等级以可核验的官方资料优先，维基可能存在遗漏、过时或景区范围差异。',
        'license': {
            'name': 'Creative Commons Attribution-ShareAlike 4.0 International', 'spdx': 'CC-BY-SA-4.0',
            'url': LICENSE_URL, 'attribution': '维基百科贡献者，《国家4A级旅游景区》，修订版本 ' + str(revision),
            'history_url': 'https://zh.wikipedia.org/w/index.php?' + urlencode({'title': TITLE, 'action': 'history'}),
            'changes': '提取两个省份的表格；展开城市跨行合并单元格；去除脚注编号；转换为JSON并增加来源、日期精度和数量核对元数据。',
        },
        'sections': sections, 'record_count': len(items), 'items': items,
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--html', type=Path, help='Read an existing saved article HTML instead of downloading.')
    parser.add_argument('--revision', type=int, default=REVISION)
    parser.add_argument('--output', type=Path, default=ROOT / 'resources/scenic/4a-wikipedia.json')
    parser.add_argument('--save-html', type=Path, help='Optional raw HTML cache path.')
    parser.add_argument('--retrieved-at', help='Original retrieval date or UTC timestamp when reusing a saved HTML snapshot.')
    args = parser.parse_args()
    fetched_at = args.retrieved_at or datetime.now(timezone.utc).isoformat(timespec='seconds')
    if args.html:
        document = args.html.read_text(encoding='utf-8')
    else:
        url = 'https://zh.wikipedia.org/w/index.php?' + urlencode({'title': TITLE, 'oldid': args.revision, 'variant': 'zh-cn'})
        request = Request(url, headers={'User-Agent': 'TravelPlannerResearch/1.0'})
        with urlopen(request, timeout=40) as response:
            body = response.read(5_000_001)
        if len(body) > 5_000_000:
            raise ValueError('Article exceeds the download size limit')
        document = body.decode('utf-8')
    result = build(document, fetched_at)
    if result['revision'] != args.revision:
        raise ValueError(f"Expected revision {args.revision}, received {result['revision']}")
    if args.save_html:
        args.save_html.parent.mkdir(parents=True, exist_ok=True)
        args.save_html.write_text(document, encoding='utf-8')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + '.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(args.output)
    print(json.dumps({'record_count': result['record_count'], 'revision': result['revision'], 'sections': result['sections']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
