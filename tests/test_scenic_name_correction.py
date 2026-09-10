from __future__ import annotations

import unittest
from unittest.mock import patch

import scenic_catalog as scenic


class NameCorrectionTests(unittest.TestCase):
    @staticmethod
    def official_record(name, **overrides):
        return dict({'name': name, 'rating': '5A', 'province': '北京市',
                     'city': '北京市', 'aliases': [],
                     'source_url': 'https://example.com/catalog',
                     'source_type': 'official', 'as_of': '2026-09-10'}, **overrides)

    def test_requested_combined_names_resolve_only_in_beijing(self):
        for name in ('八达岭长城', '慕田峪长城'):
            result = scenic.canonical_name_correction(name, '北京市')
            self.assertEqual(result['from'], name)
            self.assertEqual(result['to'], '八达岭—慕田峪长城旅游区')
            self.assertIn('合并景区', result['reason'])
            self.assertIsNone(scenic.canonical_name_correction(name, '上海'))
        self.assertIsNone(scenic.canonical_name_correction('八达岭长城纪念品店', '北京'))
        self.assertIsNone(scenic.canonical_name_correction('八达岭—慕田峪长城旅游区', '北京'))
        # Stored component records never acquire the combined area's grade merely by being read.
        self.assertEqual(scenic.enrich_attraction({'name': '八达岭长城'}, '北京')['scenic_rating'], '')

    def test_exact_official_aliases_use_full_names_but_ambiguity_is_not_guessed(self):
        record = {'name': '北京市示例旅游景区', 'rating': '5A', 'province': '北京市',
                  'aliases': ['示例公园'], 'source_url': 'https://example.com/catalog',
                  'source_type': 'official', 'as_of': '2026-09-10'}
        catalog = scenic.ScenicCatalog([{'items': [record]}])
        with patch.object(scenic, 'get_catalog', return_value=catalog):
            self.assertEqual(scenic.canonical_name_correction('示例公园', '北京')['to'], record['name'])
            self.assertIsNone(scenic.canonical_name_correction('示例公园东门', '北京'))
        catalog = scenic.ScenicCatalog([{'items': [record, dict(record, name='第二座旅游景区')]}])
        with patch.object(scenic, 'get_catalog', return_value=catalog):
            self.assertIsNone(scenic.canonical_name_correction('示例公园', '北京'))

    def test_existing_catalog_combined_names_across_cities(self):
        cases = [
            ('大连', ('老虎滩极地馆',), '大连市老虎滩海洋公园—老虎滩极地馆'),
            ('苏州', ('拙政园', '留园', '虎丘'), '苏州市苏州园林（拙政园－留园－虎丘）'),
            ('宁德', ('白水洋', '鸳鸯溪'), '宁德市（白水洋•鸳鸯溪）旅游景区'),
            ('宁波', ('天一阁', '月湖'), '宁波市天一阁•月湖景区'),
            ('成都', ('青城山', '都江堰'), '成都市青城山－都江堰旅游景区'),
        ]
        for city, names, target in cases:
            for name in names:
                with self.subTest(city=city, name=name):
                    correction = scenic.canonical_name_correction(name, city)
                    self.assertIsNotNone(correction)
                    self.assertEqual(correction['from'], name)
                    self.assertEqual(correction['to'], target)
                    self.assertIn('合并景区', correction['reason'])
                    self.assertIsNone(scenic.canonical_name_correction(name, '未知城市'))
                    self.assertIsNone(scenic.canonical_name_correction(name + '纪念品店', city))
                    self.assertIsNone(scenic.canonical_name_correction(name + '东门', city))

    def test_new_catalog_records_work_without_named_exceptions(self):
        for separator in ('—', '-', '－', '·', '•', '・', '、'):
            target = '北京市云岚山' + separator + '映月湖旅游景区'
            catalog = scenic.ScenicCatalog([{'items': [self.official_record(target)]}])
            with self.subTest(separator=separator), patch.object(scenic, 'get_catalog', return_value=catalog):
                for name in ('云岚山', '映月湖'):
                    self.assertEqual(scenic.canonical_name_correction(name, '北京')['to'], target)
                    self.assertEqual(catalog.enrich({'name': name}, '北京')['scenic_rating'], '')

    def test_parenthesized_members_and_shared_long_descriptor(self):
        cases = [
            ('北京市云岚园林（映月湖－流霞阁）', ('映月湖', '流霞阁')),
            ('北京市（映月湖、流霞阁）旅游景区', ('映月湖', '流霞阁')),
            ('北京市锦绣岭—望海关长城旅游区', ('锦绣岭长城', '望海关长城')),
        ]
        for target, names in cases:
            catalog = scenic.ScenicCatalog([{'items': [self.official_record(target)]}])
            with self.subTest(target=target), patch.object(scenic, 'get_catalog', return_value=catalog):
                for name in names:
                    self.assertEqual(scenic.canonical_name_correction(name, '北京')['to'], target)

    def test_locations_people_brands_dates_and_ji_are_not_member_lists(self):
        cases = [
            ('北京市示例园林（甲县、乙县）', ('甲县', '乙县', '示例园林')),
            ('北京市示例园林（东城区）', ('东城区', '示例园林')),
            ('北京市库尔班·吐鲁木纪念馆', ('库尔班', '库尔班纪念馆', '吐鲁木纪念馆')),
            ('北京市九·一八历史博物馆', ('九', '九博物馆', '一八历史博物馆')),
            ('北京市建业·华谊兄弟电影小镇', ('建业', '华谊兄弟电影小镇')),
            ('北京市措木及日景区', ('措木', '日')),
            ('北京市云岚山及映月湖旅游景区', ('云岚山', '映月湖')),
            # Without a clear full name for both members, keep the user's name.
            ('北京市南湖·开滦旅游景区', ('南湖', '开滦')),
        ]
        for target, names in cases:
            catalog = scenic.ScenicCatalog([{'items': [self.official_record(target)]}])
            with patch.object(scenic, 'get_catalog', return_value=catalog):
                for name in names:
                    with self.subTest(target=target, name=name):
                        self.assertIsNone(scenic.canonical_name_correction(name, '北京'))

    def test_wikipedia_names_do_not_become_official_corrections(self):
        record = self.official_record('北京市云岚山—映月湖旅游景区',
                                      aliases=['山水公园'], source_type='wikipedia')
        catalog = scenic.ScenicCatalog([{'items': [record]}])
        with patch.object(scenic, 'get_catalog', return_value=catalog):
            for name in ('山水公园', '云岚山', '映月湖'):
                with self.subTest(name=name):
                    self.assertIsNone(scenic.canonical_name_correction(name, '北京'))

    def test_unique_component_in_more_than_one_combined_area_is_ambiguous(self):
        records = [self.official_record('北京市云岚山—映月湖旅游景区'),
                   self.official_record('北京市流霞阁—映月湖旅游景区')]
        catalog = scenic.ScenicCatalog([{'items': records}])
        with patch.object(scenic, 'get_catalog', return_value=catalog):
            self.assertIsNone(scenic.canonical_name_correction('映月湖', '北京'))

    def test_exact_official_name_takes_priority_over_combined_component(self):
        independent = self.official_record('北京市映月湖旅游景区', rating='4A')
        combined = self.official_record('北京市云岚山—映月湖旅游景区')
        catalog = scenic.ScenicCatalog([{'items': [combined, independent]}])
        with patch.object(scenic, 'get_catalog', return_value=catalog):
            self.assertEqual(scenic.canonical_name_correction('映月湖', '北京')['to'], independent['name'])
            self.assertIsNone(scenic.canonical_name_correction(independent['name'], '北京'))

    def test_ambiguous_exact_names_cannot_fall_back_to_a_combined_area(self):
        records = [self.official_record('北京市第一座公园', aliases=['映月湖']),
                   self.official_record('北京市第二座公园', aliases=['映月湖']),
                   self.official_record('北京市云岚山—映月湖旅游景区')]
        catalog = scenic.ScenicCatalog([{'items': records}])
        with patch.object(scenic, 'get_catalog', return_value=catalog):
            self.assertIsNone(scenic.canonical_name_correction('映月湖', '北京'))

    def test_withdrawal_of_component_or_combined_area_cannot_be_bypassed(self):
        target = '北京市云岚山—映月湖旅游景区'
        for withdrawn in ('映月湖', target):
            correction = self.official_record(withdrawn, action='removed', effective_date='2026-09-11')
            catalog = scenic.ScenicCatalog([{'items': [self.official_record(target)]}],
                                           corrections=[correction])
            with self.subTest(withdrawn=withdrawn), patch.object(scenic, 'get_catalog', return_value=catalog):
                self.assertIsNone(scenic.canonical_name_correction('映月湖', '北京'))

    def test_unrelated_component_descriptors_are_not_shared(self):
        cases = [
            ('剑川石宝山·沙溪古镇景区', '云南省', '大理州', '剑川石宝山古镇', '剑川石宝山'),
            ('萤火虫水洞•地下大峡谷旅游区', '山东省', '临沂市', '萤火虫水洞峡谷', '萤火虫水洞'),
        ]
        for target, province, city, fabricated, member in cases:
            record = self.official_record(target, province=province, city=city)
            catalog = scenic.ScenicCatalog([{'items': [record]}])
            with self.subTest(target=target), patch.object(scenic, 'get_catalog', return_value=catalog):
                self.assertIsNone(scenic.canonical_name_correction(fabricated, city))
                self.assertEqual(scenic.canonical_name_correction(member, city)['to'], target)

    def test_official_alias_cannot_hide_withdrawal_recorded_under_full_name(self):
        target = '北京市云岚山旅游景区'
        record = self.official_record(target, aliases=['示例园'])
        withdrawal = self.official_record(target, action='removed', effective_date='2026-09-11')
        catalog = scenic.ScenicCatalog([{'items': [record]}], corrections=[withdrawal])
        with patch.object(scenic, 'get_catalog', return_value=catalog):
            self.assertIsNone(scenic.canonical_name_correction('示例园', '北京'))


if __name__ == '__main__':
    unittest.main()
