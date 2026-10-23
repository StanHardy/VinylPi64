"""Diagnostics store should remain usable without journalctl or systemd."""
from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from vinylpi import logging_service as logs


class LogsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(logs, 'LOG_PATH', Path(self.tmp.name) / 'logs.db')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_severity_and_ignoring_access_lines(self):
        self.assertEqual(logs.classify('No song detected (#30 in a row).'), 'TRACE')
        self.assertEqual(logs.classify('Recording 3s sample ...'), 'TRACE')
        self.assertEqual(logs.classify('Spotify API error: request failed'), 'ERROR')
        self.assertEqual(logs.classify('Could not write status'), 'WARNING')
        logs.append('127.0.0.1 - - "GET /api/status HTTP/1.1" 200 -')
        self.assertEqual(logs.read_events(level='all'), [])

    def test_filter_summary_search_and_order(self):
        logs.append('Recording sample', source='Vinyl')
        logs.append('Sample ready', level='INFO', source='Vinyl')
        logs.append('Broken display connection', level='WARNING', source='Pixoo')
        logs.append('Spotify API error: rejected', source='Spotify')
        all_rows = logs.read_events(level='all')
        self.assertEqual([r['level'] for r in all_rows], ['TRACE', 'INFO', 'WARNING', 'ERROR'])
        issue_rows = logs.read_events(level='issues')
        self.assertEqual([r['level'] for r in issue_rows], ['WARNING', 'ERROR'])
        self.assertEqual(len(logs.read_events(level='all', search='display')), 1)
        self.assertEqual(len(logs.read_events(after=all_rows[1]['id'], level='all')), 2)
        self.assertEqual(logs.summary(), {
            'latest_id': all_rows[-1]['id'], 'unread': 2, 'warnings': 1, 'errors': 1,
        })
        self.assertEqual(logs.summary(since=all_rows[-2]['id'])['unread'], 1)

    def test_clear_is_persistent_but_history_is_preserved(self):
        logs.append('old warning - 1', level='WARNING', source='Test')
        logs.append('old trace - 2', level='TRACE', source='Test')
        saved = logs.read_events(level='all')
        cutoff = logs.clear_view()
        self.assertEqual(cutoff, saved[-1]['id'])
        self.assertEqual(logs.cleared_through(), cutoff)
        self.assertEqual(logs.read_events(level='all', include_cleared=False), [])
        self.assertEqual(logs.read_events(level='all'), saved)
        self.assertEqual(logs.summary()['unread'], 0)
        self.assertEqual(logs.clear_view(), cutoff)  # Repeated clear is harmless.
        logs.append('new warning - 3', level='WARNING', source='Test')
        self.assertEqual([e['message'] for e in logs.read_events(level='all', include_cleared=False)],
                         ['new warning - 3'])
        self.assertEqual(logs.summary()['unread'], 1)
        # State survives SQLite connection recreation (e.g. web-app restart).
        old_conn = getattr(logs._local, 'connection', None)
        if old_conn is not None:
            old_conn.close()
        logs._local.connection = None
        self.assertEqual(logs.cleared_through(), cutoff)
        self.assertEqual(len(logs.read_events(level='all', include_cleared=False)), 1)

    def test_clear_and_new_messages_are_thread_safe(self):
        logs.append('existing event', level='INFO')
        with ThreadPoolExecutor(max_workers=6) as pool:
            cutoffs = list(pool.map(lambda _: logs.clear_view(), range(6)))
        self.assertEqual(len(set(cutoffs)), 1)
        logs.append('message after clear', level='INFO')
        self.assertEqual([e['message'] for e in logs.read_events(level='all', include_cleared=False)],
                         ['message after clear'])

    def test_duplicate_error_is_throttled_but_normal_trace_is_not(self):
        logs.append('Unique API error: code z9', source='Spotify')
        logs.append('Unique API error: code z9', source='Spotify')
        logs.append('No song detected (#7 in a row).', source='Vinyl')
        logs.append('No song detected (#7 in a row).', source='Vinyl')
        self.assertEqual(len(logs.read_events(level='issues')), 1)
        self.assertEqual(len(logs.read_events(level='all')), 3)

    def test_redaction_and_concurrent_writers(self):
        def write(i):
            logs.append(f'worker-{i} token=PRIVATEKEY and Bearer HEADERSECRET', level='INFO')
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write, range(32)))
        rows = logs.read_events(level='all')
        self.assertEqual(len(rows), 32)
        for row in rows:
            self.assertNotIn('PRIVATEKEY', row['message'])
            self.assertNotIn('HEADERSECRET', row['message'])
            self.assertIn('[redacted]', row['message'])


if __name__ == '__main__':
    unittest.main()
