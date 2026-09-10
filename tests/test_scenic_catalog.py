import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_service
import place_cache
import scenic_catalog
import server
from project_store import ProjectStore


def document(items, date='2025-03-11'):
    return {'source_name': '官方测试名录', 'source_url': 'https://sjfw.mct.gov.cn/',
            'as_of': date, 'items': items}


def fixture():
    return scenic_catalog.ScenicCatalog([document([
        {'name': '上海市核验园景区', 'province': '上海市', 'city': '上海市', 'rating': '5A'},
        {'name': '洛阳市示例山旅游景区', 'province': '河南省', 'city': '洛阳', 'rating': '4A'},
    ])])


class ScenicMatchingTests(unittest.TestCase):
    def test_withdrawal_suppresses_old_official_wiki_and_manually_stored_rating(self):
        item = {'name': '合肥示例公园', 'province': '安徽省', 'city': '合肥市', 'rating': '4A'}
        correction = dict(item, action='removed', effective_date='2026-09-07',
                          source_name='公告转述报道', source_url='https://www.cqn.com.cn/example')
        docs = [document([item, dict(item, source_type='wikipedia', as_of='2026-03')])]
        catalog = scenic_catalog.ScenicCatalog(docs, corrections=[correction])
        result = catalog.enrich({'name': '示例公园', 'scenic_rating': '4A'}, '合肥')
        self.assertEqual(result['scenic_rating'], '')
        self.assertEqual(result['scenic_rating_info']['status'], 'outdated')
        self.assertEqual(result['scenic_rating_info']['source_type'], 'reported_official')
        self.assertEqual(catalog.for_city('合肥'), [])
        self.assertNotEqual(catalog.fingerprint, scenic_catalog.ScenicCatalog(docs).fingerprint)
        self.assertEqual(catalog.enrich({'name': '示例公园', 'scenic_rating': '4A'}, '南京')['scenic_rating'], '4A')

    def test_later_official_reinstatement_supersedes_withdrawal_but_wiki_cannot(self):
        item = {'name': '合肥示例公园', 'province': '安徽省', 'city': '合肥市', 'rating': '5A', 'as_of': '2026-10-01'}
        correction = dict(item, action='downgraded', effective_date='2026-09-07',
                          source_url='https://www.cqn.com.cn/example')
        catalog = scenic_catalog.ScenicCatalog([document([item])], corrections=[correction])
        self.assertEqual(catalog.enrich({'name': '示例公园'}, '合肥')['scenic_rating'], '5A')
        catalog = scenic_catalog.ScenicCatalog([document([dict(item, source_type='wikipedia')])], corrections=[correction])
        self.assertEqual(catalog.enrich({'name': '示例公园'}, '合肥')['scenic_rating'], '')

    def test_wikipedia_fills_gaps_with_explicit_secondary_provenance(self):
        item = {'name': '南京示例公园', 'province': '江苏省', 'city': '南京市', 'rating': '4A',
                'source_type': 'wikipedia', 'source_url': 'https://zh.wikipedia.org/w/index.php?oldid=123'}
        catalog = scenic_catalog.ScenicCatalog([document([item])])
        result = catalog.enrich({'name': '示例公园'}, '南京')
        self.assertEqual(result['scenic_rating'], '4A')
        self.assertEqual(result['scenic_rating_info']['status'], 'reference')
        self.assertEqual(result['scenic_rating_info']['source_type'], 'wikipedia')
        self.assertEqual(catalog.enrich({'name': '示例公园'}, '合肥')['scenic_rating'], '')
        self.assertEqual(catalog.for_city('南京')[0]['source_type'], 'wikipedia')
        self.assertEqual(catalog.for_city('南京', 0), [])

    def test_wikipedia_never_downgrades_an_official_match_even_when_newer(self):
        official = {'name': '南京示例山旅游景区', 'province': '江苏省', 'city': '南京市', 'rating': '5A'}
        wiki = dict(official, name='示例山', rating='4A', source_type='wikipedia', as_of='2026-03')
        catalog = scenic_catalog.ScenicCatalog([document([official, wiki])])
        result = catalog.enrich({'name': '示例山'}, '南京市')
        self.assertEqual(result['scenic_rating'], '5A')
        self.assertEqual(result['scenic_rating_info']['source_type'], 'official')
        references = catalog.for_city('南京')
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0]['scenic_rating'], '5A')

    def test_wikipedia_cannot_hide_an_ambiguous_official_name(self):
        one = {'name': '上海公园景区', 'province': '上海', 'city': '上海', 'rating': '4A', 'aliases': ['公园']}
        two = dict(one, name='第二座上海公园景区')
        wiki = dict(one, source_type='wikipedia', name='公园', as_of='2026-03')
        catalog = scenic_catalog.ScenicCatalog([document([one, two, wiki])])
        self.assertEqual(catalog.enrich({'name': '公园'}, '上海')['scenic_rating_info']['status'], 'ambiguous')

    def test_loader_includes_separate_wikipedia_snapshot(self):
        item = {'name': '南京示例公园', 'province': '江苏省', 'city': '南京', 'rating': '4A'}
        with tempfile.TemporaryDirectory() as directory, patch.object(scenic_catalog, 'DATA_DIR', Path(directory)):
            doc = dict(document([item]), source_type='wikipedia')
            (Path(directory) / '4a-wikipedia.json').write_text(json.dumps(doc))
            (Path(directory) / '4a-official.json').write_text(json.dumps(document([])))
            scenic_catalog.get_catalog.cache_clear()
            try:
                result = scenic_catalog.get_catalog().enrich({'name': item['name']}, '南京')
                self.assertEqual(result['scenic_rating_info']['source_type'], 'wikipedia')
            finally:
                scenic_catalog.get_catalog.cache_clear()

    def test_city_alias_prefix_suffix_and_full_name(self):
        catalog = fixture()
        for name in ('核验园', '核验园景区', '上海市核验园景区'):
            result = catalog.enrich({'name': name, 'scenic_rating': '4A'}, '上海')
            self.assertEqual(result['scenic_rating'], '5A')
            self.assertEqual(result['scenic_rating_info']['status'], 'catalog')
        self.assertEqual(catalog.enrich({'name': '示例山'}, '洛阳市')['scenic_rating'], '4A')

    def test_never_matches_other_city_or_parent_substring(self):
        catalog = fixture()
        for name, city in [('核验园', '北京'), ('核验园咖啡馆', '上海'), ('核验园东门', '上海')]:
            result = catalog.enrich({'name': name}, city)
            self.assertEqual(result['scenic_rating'], '')
            self.assertEqual(result['scenic_rating_info']['status'], 'unknown')
        unknown = catalog.enrich({'name': '未收录的小公园', 'scenic_rating': '5A'}, '上海')
        self.assertEqual(unknown['scenic_rating_info']['status'], 'unverified')

    def test_unknown_city_never_treated_as_global_match(self):
        result = fixture().enrich({'name': '核验园', 'scenic_rating': '5A'}, None)
        self.assertEqual(result['scenic_rating_info']['status'], 'unverified')

    def test_same_alias_is_ambiguous_and_newer_exact_source_wins(self):
        base = {'name': '上海公园景区', 'province': '上海', 'city': '上海', 'rating': '4A', 'aliases': ['公园']}
        newer = dict(base, rating='5A')
        catalog = scenic_catalog.ScenicCatalog([document([base], '2020-01-01'), document([newer])])
        self.assertEqual(catalog.enrich({'name': '公园'}, '上海')['scenic_rating'], '5A')
        other = dict(base, name='另一个上海公园景区')
        catalog = scenic_catalog.ScenicCatalog([document([base, other])])
        result = catalog.enrich({'name': '公园'}, '上海')
        self.assertEqual(result['scenic_rating_info']['status'], 'ambiguous')
        self.assertEqual(result['scenic_rating'], '')

    def test_without_city_requires_explicit_city_in_official_name(self):
        item = {'name': '洛阳市示例山景区', 'province': '河南省', 'rating': '4A', 'aliases': ['示例山']}
        catalog = scenic_catalog.ScenicCatalog([document([item])])
        self.assertEqual(catalog.enrich({'name': '示例山'}, '洛阳')['scenic_rating'], '4A')
        self.assertEqual(catalog.enrich({'name': '示例山'}, '郑州')['scenic_rating'], '')

    def test_latest_snapshot_resolves_old_conflicts_and_missing_city(self):
        old = {'name': '上海公园景区', 'province': '上海市', 'city': '上海', 'rating': '4A'}
        docs = [document([old, dict(old, rating='5A')], '2020-01-01'),
                document([dict(old, city='', rating='5A')])]
        catalog = scenic_catalog.ScenicCatalog(docs)
        result = catalog.enrich({'name': '公园'}, '上海')
        self.assertEqual(result['scenic_rating'], '5A')
        self.assertEqual(result['scenic_rating_info']['status'], 'catalog')

    def test_explicit_county_city_alias_is_scoped_to_this_attraction(self):
        item = {'name': '示例景区', 'province': '内蒙古自治区', 'city': '呼伦贝尔市',
                'city_aliases': ['满洲里市'], 'rating': '5A'}
        catalog = scenic_catalog.ScenicCatalog([document([item])])
        self.assertEqual(catalog.enrich({'name': '示例景区'}, '满洲里')['scenic_rating'], '5A')
        self.assertEqual(catalog.enrich({'name': '示例景区'}, '阿尔山')['scenic_rating'], '')

    def test_unknown_source_date_remains_visible_and_cannot_resolve_conflict(self):
        entry = {'name': '上海公园景区', 'province': '上海市', 'city': '上海', 'rating': '4A', 'as_of': None}
        catalog = scenic_catalog.ScenicCatalog([document([entry])])
        self.assertEqual(catalog.enrich({'name': '公园'}, '上海')['scenic_rating_info']['as_of'], '')
        catalog = scenic_catalog.ScenicCatalog([document([entry, dict(entry, rating='5A', as_of='2025-01-01')])])
        self.assertEqual(catalog.enrich({'name': '公园'}, '上海')['scenic_rating_info']['status'], 'ambiguous')

    def test_snapshot_fingerprint_changes_with_rating(self):
        item = {'name': '上海公园', 'province': '上海', 'city': '上海', 'rating': '4A'}
        first = scenic_catalog.ScenicCatalog([document([item])])
        second = scenic_catalog.ScenicCatalog([document([dict(item, rating='5A')])])
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_rejects_invalid_rating_and_unsafe_source(self):
        for item in ({'name': '公园', 'rating': '9A'},
                     {'name': '公园', 'rating': '5A', 'source_url': 'javascript:alert(1)'}):
            with self.assertRaises(ValueError):
                scenic_catalog.ScenicCatalog([document([item])])
        with self.assertRaises(server.ApiError):
            server.validate_payload('attraction', {'name': '公园', 'scenic_rating': '3A'})
        self.assertEqual(server.validate_payload('attraction', {'name': '公园'})['scenic_rating'], '')


class ScenicIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'trip.db'
        seed = Path(self.temp.name) / 'empty.json'
        seed.write_text('{}')
        self.catalog = fixture()
        self.patches = [patch.object(module, name, return_value=self.catalog) for module, name in
                        [(scenic_catalog, 'get_catalog'), (server, 'get_scenic_catalog'), (ai_service, 'get_scenic_catalog')]]
        for mock in self.patches:
            mock.start()
        server.init_database(self.path, 'test-code', seed)
        server.claim_identity(self.path, {'user_id': 'rating-test'})

    def tearDown(self):
        for mock in reversed(self.patches):
            mock.stop()

    def test_wiki_rating_persists_and_a_later_correction_hides_it_without_rewriting(self):
        source = {'name': '合肥示例公园', 'province': '安徽省', 'city': '合肥市', 'rating': '4A',
                  'source_type': 'wikipedia'}
        docs = [document([source])]
        catalog = scenic_catalog.ScenicCatalog(docs)
        city = server.create_city(self.path, {'name': '合肥'}, 'rating-test')['city']
        with patch.object(scenic_catalog, 'get_catalog', return_value=catalog):
            item = server.create_item(self.path, 'attraction', {'name': '示例公园', 'city_id': city['id']}, 'rating-test')['item']
            self.assertEqual(item['scenic_rating'], '4A')
            self.assertEqual(item['scenic_rating_info']['status'], 'reference')
        correction = dict(source, action='removed', effective_date='2026-09-07', source_url='https://www.cqn.com.cn/example')
        corrected = scenic_catalog.ScenicCatalog(docs, corrections=[correction])
        with patch.object(scenic_catalog, 'get_catalog', return_value=corrected):
            result = server.snapshot(self.path, city['id'])['items']['attraction'][0]
        self.assertEqual(result['scenic_rating'], '')
        self.assertEqual(result['scenic_rating_info']['status'], 'outdated')
        with server.connect_db(self.path) as db:
            row = db.execute('SELECT version,payload FROM items WHERE id=?', (item['id'],)).fetchone()
            self.assertEqual(row['version'], 1)
            self.assertEqual(json.loads(row['payload'])['scenic_rating'], '4A')

    def test_create_update_and_shared_cache_recalculate_rating(self):
        item = server.create_item(self.path, 'attraction', {'name': '核验园', 'scenic_rating': '4A'}, 'rating-test')['item']
        self.assertEqual(item['scenic_rating'], '5A')
        self.assertEqual(item['scenic_rating_info']['status'], 'catalog')
        cached = place_cache.load_city(self.path, '上海')[0]['payload']
        self.assertEqual(cached['scenic_rating'], '5A')
        self.assertNotIn('scenic_rating_info', cached)
        store = ProjectStore(self.path, server.init_database)
        child = store.resolve(store.create('评级缓存测试', 'other-code')['id'])
        copy = server.snapshot(child, 'shanghai')['items']['attraction'][0]
        self.assertEqual(copy['scenic_rating'], '5A')
        updated = server.update_item(self.path, 'attraction', item['id'],
                                     {'name': '核验园附近小店', 'scenic_rating': '', 'version': 1}, 'rating-test')['item']
        self.assertEqual(updated['scenic_rating'], '')
        self.assertEqual(updated['scenic_rating_info']['status'], 'unknown')
        self.assertEqual(server.snapshot(child, 'shanghai')['items']['attraction'][0]['scenic_rating'], '5A')

    def test_existing_rows_enrich_without_rewriting_authors_versions_or_content(self):
        item = server.create_item(self.path, 'attraction', {'name': '核验园'}, 'rating-test')['item']
        with server.connect_db(self.path) as db:
            db.execute('UPDATE items SET payload=? WHERE id=?', (json.dumps({'name': '核验园', 'description': '旧内容'}), item['id']))
            before = dict(db.execute('SELECT * FROM items WHERE id=?', (item['id'],)).fetchone())
        result = server.snapshot(self.path, 'shanghai')['items']['attraction'][0]
        self.assertEqual(result['scenic_rating'], '5A')
        self.assertEqual(result['description'], '旧内容')
        with server.connect_db(self.path) as db:
            self.assertEqual(before, dict(db.execute('SELECT * FROM items WHERE id=?', (item['id'],)).fetchone()))

    def test_old_ai_import_without_rating_is_enriched(self):
        result = server.import_ai_items(self.path, 'shanghai', 'rating-test',
                                      [{'kind': 'attraction', 'data': {'name': '核验园'}}], 'test-old-job')
        self.assertEqual(result['created'], 1)
        result = server.snapshot(self.path, 'shanghai')['items']['attraction'][0]
        self.assertEqual(result['scenic_rating'], '5A')
        self.assertEqual(result['scenic_rating_info']['status'], 'catalog')

    def test_ai_prompt_schema_and_result_reconcile_ratings_without_network(self):
        service = ai_service.AIService(self.path, api_key='test-secret',
                                      get_context=lambda city: server.ai_context(self.path, city),
                                      normalize_item=server.validate_payload, import_items=lambda *args: {})
        self.addCleanup(service.close)
        request = service._validate_request({'city_id': 'shanghai', 'kinds': ['attraction'],
                                            'days': 1, 'start_date': '2026-10-01', 'people': 1})
        context = service._context('shanghai')
        with patch.object(service, '_post', return_value={}) as post:
            service._generate(request, context)
        prompt = json.loads(post.call_args.args[0]['input'])
        self.assertEqual(prompt['scenic_catalog'][0]['scenic_rating'], '5A')
        self.assertNotIn('test-secret', json.dumps(prompt))
        self.assertEqual(ai_service.OUTPUT_SCHEMA['properties']['attractions']['items']['properties']['scenic_rating']['enum'], ['', '4A', '5A'])
        value = {'schema_version': 1, 'summary': '景点推荐', 'notices': [], 'foods': [], 'itineraries': [],
                 'attractions': [dict.fromkeys(ai_service.FIELD_LIMITS['attraction'], '')]}
        value['attractions'][0].update(name='核验园', scenic_rating='4A')
        result = service._validate_result(value, request, context)['attractions'][0]
        self.assertEqual(result['scenic_rating'], '5A')
        self.assertEqual(result['scenic_rating_info']['status'], 'catalog')
        value['attractions'][0].update(name='名录里没有的地方', scenic_rating='5A')
        result = service._validate_result(value, request, context)['attractions'][0]
        self.assertEqual(result['scenic_rating_info']['status'], 'unverified')
        value['attractions'][0]['scenic_rating'] = '3A'
        with self.assertRaises(ai_service.AIError):
            service._validate_result(value, request, context)


if __name__ == '__main__':
    unittest.main()
