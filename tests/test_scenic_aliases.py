import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import scenic_catalog
from scenic_aliases import AliasNames, apply_aliases, identity
from dev.build_scenic_aliases import LinkTables


class ScenicAliasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = scenic_catalog.DATA_DIR
        cls.overlay = json.loads((cls.directory / 'aliases.json').read_text())
        cls.documents = {name: json.loads((cls.directory / name).read_text())
                         for name in cls.overlay['files']}
        cls.catalog = scenic_catalog.get_catalog()
        cls.baseline = scenic_catalog.ScenicCatalog(
            [doc for name, doc in cls.documents.items() if name != 'rating-corrections.json'],
            corrections=cls.documents['rating-corrections.json']['items'])

    def test_all_source_rows_covered_and_provenance_unchanged(self):
        expected_files = {p.name for p in self.directory.glob('*-official.json')}
        expected_files.update({'4a-wikipedia.json', 'rating-corrections.json'})
        self.assertEqual(set(self.overlay['files']), expected_files)
        for name, document in self.documents.items():
            rows = self.overlay['files'][name]
            self.assertEqual([identity(row) for row in rows], [identity(row) for row in document['items']])
            enriched = apply_aliases(document, rows)
            self.assertEqual(apply_aliases(enriched, rows), enriched)
            for old, new in zip(document['items'], enriched['items']):
                self.assertEqual({k: v for k, v in old.items() if k != 'aliases'},
                                 {k: v for k, v in new.items() if k != 'aliases'})
                self.assertTrue(set(old.get('aliases', [])) <= set(new['aliases']))
        self.assertNotEqual(self.baseline.fingerprint, self.catalog.fingerprint)

    def test_common_names_resolve_in_their_city(self):
        for alias, city, grade, name in [
            ('紫禁城', '北京', '5A', '故宫博物院'),
            ('天坛', '北京', '5A', '天坛公园'),
            ('东方明珠电视塔', '上海', '5A', '东方明珠广播电视塔'),
            ('总统府', '南京', '4A', '南京总统府（南京中国近代史遗址博物馆）'),
            ('盛京皇宫', '沈阳', '4A', '沈阳故宫'),
            ('湖南博物院', '长沙', '4A', '湖南省博物馆'),
            ('福州植物园', '福州', '4A', '福州国家森林公园景区（福州植物园）'),
            ('五里桥', '泉州', '4A', '安平桥（五里桥）景区'),
            ('黄鹤楼', '武汉', '5A', '武汉市黄鹤楼公园'),
            ('三星堆博物馆', '德阳', '4A', '四川广汉三星堆博物馆'),
            ('木格措', '甘孜州', '4A', '康定情歌(木格措)风景区'),
        ]:
            with self.subTest(alias=alias):
                result = self.catalog.enrich({'name': alias}, city)
                self.assertEqual(result['scenic_rating'], grade)
                self.assertEqual(result['scenic_rating_info']['name'], name)
        correction = scenic_catalog.canonical_name_correction('紫禁城', '北京')
        self.assertEqual(correction['to'], '故宫博物院')

    def test_existing_names_never_lose_ratings_or_become_ambiguous(self):
        for entry in self.baseline.entries:
            city = {'name': entry['city'] or entry['province'], 'province': entry['province']}
            before = self.baseline.enrich({'name': entry['name']}, city)
            if before['scenic_rating_info']['status'] not in {'catalog', 'reference', 'outdated'}:
                continue
            after = self.catalog.enrich({'name': entry['name']}, city)
            with self.subTest(name=entry['name'], city=city):
                self.assertEqual(before['scenic_rating'], after['scenic_rating'])
                self.assertNotIn(after['scenic_rating_info']['status'], {'unknown', 'ambiguous'})

    def test_new_aliases_honor_withdrawals(self):
        for row in self.overlay['files']['rating-corrections.json']:
            for alias in row['aliases']:
                with self.subTest(alias=alias):
                    result = self.catalog.enrich({'name': alias, 'scenic_rating': '4A'}, row['city'])
                    self.assertEqual(result['scenic_rating'], '')
                    self.assertEqual(result['scenic_rating_info']['status'], 'outdated')

    def test_nearby_businesses_parent_landscapes_and_other_cities_do_not_match(self):
        for name, city in [('紫禁城咖啡店', '北京'), ('天坛东门地铁站', '北京'),
                           ('黄鹤楼停车场', '武汉'), ('圆明园南门', '北京'),
                           ('故宫', '南京'), ('东方明珠', '北京'),
                           ('北运河', '北京'), ('武钢集团', '武汉'),
                           ('亚龙湾', '三亚'), ('北宋东京城遗址', '开封')]:
            with self.subTest(name=name, city=city):
                self.assertEqual(self.catalog.enrich({'name': name}, city)['scenic_rating'], '')

    def test_administrative_prefixes_and_suffixes_preserve_name_meaning(self):
        regions = json.loads((self.directory.parents[1] / 'china_province_cities.json').read_text())['provinces']
        names = AliasNames(regions)
        row = {'name': '四川省德阳市广汉市示例风景区', 'province': '四川省', 'city': '德阳市'}
        aliases = names.variants(row)
        self.assertIn('示例', aliases)
        self.assertNotIn('示例风', aliases)
        self.assertNotIn('博物馆', names.variants(dict(row, name='四川省博物馆')))
        self.assertNotIn('映月湖', names.variants(dict(row, name='云岚山－映月湖景区')))
        self.assertEqual(names.variants(dict(row, name='（曾用名：刺树丫口景区，2023年7月变更')), set())

    def test_overlay_only_applies_to_exact_scoped_target(self):
        row = dict(name='示例园', province='北京市', city='北京', rating='4A')
        doc = {'items': [row], 'source_url': 'https://example.gov.cn/scenic-list', 'source_type': 'official'}
        extra = [dict(row, province='天津市', aliases=['别称'])]
        self.assertEqual(apply_aliases(doc, extra)['items'][0]['aliases'], [])
        with tempfile.TemporaryDirectory() as tmp, patch.object(scenic_catalog, 'DATA_DIR', Path(tmp)):
            (Path(tmp) / '4a-official.json').write_text(json.dumps(doc))
            (Path(tmp) / 'aliases.json').write_text(json.dumps({'files': {
                '4a-official.json': [dict(row, aliases=['别称'])]}}))
            scenic_catalog.get_catalog.cache_clear()
            try:
                self.assertEqual(scenic_catalog.get_catalog().enrich({'name': '别称'}, '北京')['scenic_rating'], '4A')
            finally:
                scenic_catalog.get_catalog.cache_clear()

    def test_table_links_retain_whole_cell_and_link_text_separately(self):
        parser = LinkTables()
        parser.feed('<table><tr><td><a href="/wiki/山村" title="山村"><b>山村</b></a>博物馆<sup>1</sup></td></tr></table>')
        cell = parser.tables[0][0][0]
        self.assertEqual(cell['text'], '山村博物馆')
        self.assertEqual(cell['links'][0]['text'], '山村')


if __name__ == '__main__':
    unittest.main()
