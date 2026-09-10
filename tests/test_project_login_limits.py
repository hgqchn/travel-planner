"""Project entry and exit remain available after repeated code submissions."""
import unittest
import urllib.error

from test_server import RunningServer, json_request, opener_with_cookies, TEST_PROJECT_CODE


class ProjectLoginTests(unittest.TestCase):
    def test_repeated_wrong_codes_do_not_block_correct_code_or_logout(self):
        running = RunningServer()
        try:
            opener = opener_with_cookies()
            for _ in range(12):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    json_request(opener, running.base_url, '/api/project-session',
                                 method='POST', payload={'project_code': 'incorrect'})
                self.assertEqual(error.exception.code, 403)
                error.exception.close()
            for _ in range(12):
                status, data, _ = json_request(
                    opener, running.base_url, '/api/project-session', method='POST',
                    payload={'project_code': TEST_PROJECT_CODE})
                self.assertEqual(status, 200)
                self.assertTrue(data['unlocked'])
                status, data, _ = json_request(
                    opener, running.base_url, '/api/project-session',
                    method='DELETE', payload={})
                self.assertEqual(status, 200)
                self.assertFalse(data['unlocked'])
        finally:
            running.close()
