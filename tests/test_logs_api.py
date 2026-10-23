"""Exercise the standalone Flask diagnostics blueprint."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from flask import Flask
except ModuleNotFoundError:
    Flask = None

if Flask is not None:
    from vinylpi import logging_service as logs
    from vinylpi.web.routes.logs_api import logs_bp


@unittest.skipIf(Flask is None, 'Flask is not installed')
class LogsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(logs, 'LOG_PATH', Path(self.temp.name) / 'logs.db')
        patcher.start()
        self.addCleanup(patcher.stop)
        app = Flask(__name__)
        app.register_blueprint(logs_bp)
        self.client = app.test_client()

    def test_read_and_summary(self):
        logs.trace('Recording sample', source='Vinyl')
        logs.warning('Missing Pixoo', source='Pixoo')
        logs.error('Spotify API error 500', source='Spotify')
        events = self.client.get('/api/logs').get_json()
        self.assertEqual([r['level'] for r in events['entries']], ['WARNING', 'ERROR'])
        all_events = self.client.get('/api/logs?level=all').get_json()
        self.assertEqual(len(all_events['entries']), 3)
        s = self.client.get('/api/logs/summary?since=0').get_json()
        self.assertEqual(s['unread'], 2)
        after = all_events['entries'][1]['id']
        self.assertEqual(self.client.get(f'/api/logs/summary?since={after}').get_json()['unread'], 1)

    def test_reject_unknown_filter(self):
        self.assertEqual(self.client.get('/api/logs?level=foo').status_code, 400)


if __name__ == '__main__':
    unittest.main()
