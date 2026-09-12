"""Public project entry and deployment credential settings regression checks."""
import json
import unittest
import http.cookiejar
import urllib.request
from pathlib import Path
import test_projects
import server


class ProjectHubTests(unittest.TestCase):
    setUp = test_projects.ProjectApiTests.setUp
    tearDown = test_projects.ProjectApiTests.tearDown
    request = test_projects.ProjectApiTests.request
    unlock = test_projects.ProjectApiTests.unlock
    assert_http_error = test_projects.ProjectApiTests.assert_http_error
    def test_public_creation_and_passwordless_projects(self):
        anon = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        _, listing, _ = self.request('/api/projects', opener=anon)
        self.assertTrue(all(set(p) == {'id', 'name', 'url', 'password_required'} for p in listing['projects']))
        self.assertTrue(listing['projects'][0]['password_required'])
        for name in ('无密码一', '无密码二'):
            _, result, _ = self.request('/api/projects', 'POST', {'name': name}, opener=anon)
            pid = result['project_id']
            self.assertTrue(result['unlocked'])
            self.request('/api/project-session', 'POST', {}, project=pid, opener=anon)
            self.request('/api/session', 'POST', {'user_id': '好友'}, project=pid, opener=anon)
            self.request('/api/snapshot', project=pid, opener=anon)
        _, result, _ = self.request('/api/projects', 'POST', {'name': '带密码', 'project_code': 'private'}, opener=anon)
        self.assert_http_error(403, '/api/project-session', 'POST', {}, project=result['project_id'])
        self.assert_http_error(401, '/api/admin/api-settings', opener=anon)
        self.assert_http_error(401, '/api/admin/api-settings', 'PUT', {'DEEPSEEK_API_KEY': 'fake'}, opener=anon)

    def test_remove_password_revokes_old_access(self):
        self.unlock()
        self.request('/api/admin/project', 'PUT', {'name': '开放项目', 'remove_password': True})
        _, state, _ = self.request('/api/project-session')
        self.assertFalse(state['unlocked'])
        self.request('/api/project-session', 'POST', {})
        _, state, _ = self.request('/api/projects')
        self.assertFalse(state['projects'][0]['password_required'])

    def test_keys_are_private_persistent_and_can_be_cleared(self):
        values = {'DEEPSEEK_API_KEY': 'test-deepseek', 'AMAP_JS_KEY': 'test-js',
                  'AMAP_SECURITY_JS_CODE': 'test-security', 'AMAP_WEB_SERVICE_KEY': 'test-web'}
        _, result, _ = self.request('/api/admin/api-settings', 'PUT', values)
        self.assertTrue(all(result['configured'].values()))
        self.assertNotIn('test-', json.dumps(result))
        httpd = self.running.httpd
        self.assertEqual(httpd.project_ai('main')._api_key, 'test-deepseek')
        self.assertEqual(httpd.project_ai(self.project_id)._api_key, 'test-deepseek')
        self.request('/api/admin/api-settings', 'PUT', {'DEEPSEEK_API_KEY': 'test-new'})
        self.assertEqual(httpd.ai_service._api_key, 'test-new')
        self.assertEqual(httpd.amap_service.web_key, 'test-web')
        config = httpd.db_path.parent / 'api-settings.json'
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        restarted = server.create_server('127.0.0.1', 0, public_dir=Path(server.__file__).parent / 'public',
                                         data_dir=httpd.db_path.parent, project_code='', ai_api_key='env-old')
        try:
            self.assertEqual(restarted._ai_api_key, 'test-new')
            self.assertEqual(restarted.amap_service.web_key, 'test-web')
        finally:
            restarted.server_close()
        self.request('/api/admin/api-settings', 'PUT', {'DEEPSEEK_API_KEY': ''})
        self.assertFalse(httpd.ai_service.enabled)
        self.assert_http_error(400, '/api/admin/api-settings', 'PUT', {'AMAP_JS_KEY': 'bad\nkey'})
