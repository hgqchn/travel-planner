import json
import unittest

import scenic_catalog


class ScenicUpdatesTests(unittest.TestCase):
    def test_yuanmingyuan_short_name_is_verified_without_matching_nearby_places(self):
        catalog = scenic_catalog.get_catalog()
        for name in ['圆明园', '圆明园遗址公园', '圆明园遗址公园景区']:
            result = catalog.enrich({'name': name, 'scenic_rating': '5A'}, '北京')
            self.assertEqual(result['scenic_rating'], '5A')
            self.assertEqual(result['scenic_rating_info']['status'], 'catalog')
            self.assertEqual(result['scenic_rating_info']['name'], '圆明园遗址公园景区')
        for name, city in [('圆明园地铁站', '北京'), ('圆明园东门', '北京'), ('圆明园', '上海')]:
            self.assertEqual(catalog.enrich({'name': name}, city)['scenic_rating'], '')

    def test_every_official_addition_resolves_by_full_name_and_explicit_alias(self):
        document = json.loads((scenic_catalog.DATA_DIR / '4a-updates-official.json').read_text())
        self.assertEqual(len(document['items']), sum(source['count'] for source in document['coverage_sources']))
        catalog = scenic_catalog.get_catalog()
        for entry in document['items']:
            for city in [entry['city'], *entry.get('city_aliases', [])]:
                for name in [entry['name'], *entry.get('aliases', [])]:
                    with self.subTest(name=name, city=city):
                        result = catalog.enrich({'name': name}, {'name': city, 'province': entry['province']})
                        self.assertEqual(result['scenic_rating'], '4A')
                        self.assertEqual(result['scenic_rating_info']['status'], 'catalog')
                        self.assertEqual(result['scenic_rating_info']['as_of'], entry['as_of'])

    def test_new_withdrawals_clear_saved_rating_and_preserve_official_provenance(self):
        document = json.loads((scenic_catalog.DATA_DIR / 'rating-corrections.json').read_text())
        catalog = scenic_catalog.get_catalog()
        for entry in document['items']:
            for name in [entry['name'], *entry.get('aliases', [])]:
                with self.subTest(name=name):
                    result = catalog.enrich({'name': name, 'scenic_rating': '4A'}, entry['city'])
                    self.assertEqual(result['scenic_rating'], '')
                    self.assertEqual(result['scenic_rating_info']['status'], 'outdated')
                    self.assertEqual(result['scenic_rating_info']['source_type'], entry['source_type'])
        # A similarly named attraction in another city remains independently rated.
        self.assertEqual(catalog.enrich({'name': '梅州市客天下景区'}, '梅州')['scenic_rating'], '4A')

    def test_bundled_5a_verification_matches_snapshot_without_changing_source_date(self):
        document = json.loads((scenic_catalog.DATA_DIR / '5a-official.json').read_text())
        verification = document['verification_notes'][-1]
        self.assertEqual(verification['record_count'], len(document['items']))
        self.assertEqual(verification['unique_name_count'], len({r['name'] for r in document['items']}))
        self.assertLess(document['as_of'], document['verified_at'])
        self.assertEqual(scenic_catalog.get_catalog().enrich({'name': '故宫博物院'}, '北京')['scenic_rating'], '5A')


if __name__ == '__main__':
    unittest.main()
