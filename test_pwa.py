import json
import unittest
from pathlib import Path

from app import app

ROOT = Path(__file__).resolve().parent


class PwaRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_manifest_and_worker_are_root_scoped(self):
        manifest = self.client.get('/manifest.webmanifest')
        self.assertEqual(manifest.status_code, 200)
        data = json.loads(manifest.data)
        self.assertEqual(data['name'], 'GreenYield')
        self.assertEqual(data['display'], 'standalone')
        self.assertEqual(data['scope'], '/')
        worker = self.client.get('/service-worker.js')
        self.assertEqual(worker.status_code, 200)
        self.assertEqual(worker.headers.get('Service-Worker-Allowed'), '/')

    def test_health_and_offline_report_shell(self):
        self.assertEqual(self.client.get('/health').json, {'ready': True})
        shell = self.client.get('/offline-report.html')
        self.assertIn(b'offline-report.js', shell.data)
        shell.close()
        report_shell = self.client.get('/report/offline/test-id')
        self.assertIn(b'offline-controls', report_shell.data)
        report_shell.close()

    def test_precache_entries_exist(self):
        source = (ROOT / 'static' / 'service-worker.js').read_text(encoding='utf-8')
        import re
        paths = re.findall(r"'(/(?:static/[^']+|offline-report\.html|manifest\.webmanifest))'", source)
        for path in paths:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, f'precache path missing: {path}')
            response.close()


if __name__ == '__main__':
    unittest.main()
