"""Diagnostics store should remain usable without journalctl or systemd."""
from __future__ import annotations

import tempfile
import threading
import sqlite3
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

    def test_levels_are_explicit_and_access_lines_ignored(self):
        logs.append('127.0.0.1 - - "GET /api/status HTTP/1.1" 200 -')
        self.assertEqual(logs.read_events(level='all'), [])
        logs.append('Error appears in the text, not the level', level='DEBUG')
        logs.append('Successful operation', level='ERROR')
        self.assertEqual([row['level'] for row in logs.read_events(level='all')], ['DEBUG', 'ERROR'])
        with self.assertRaises(ValueError):
            logs.append('invalid severity', level='NOT_A_LEVEL')

    def test_handler_respects_explicit_severity(self):
        import logging
        record = logging.LogRecord('vinylpi.core.recognition', logging.WARNING, __file__, 1,
                                   'No song detected: debugging is unrelated to severity', (), None)
        logs.SQLiteHandler().emit(record)
        result = logs.read_events(level='issues')
        self.assertEqual(result[0]['level'], 'WARNING')
        self.assertEqual(result[0]['source'], 'Vinyl')

    def test_handler_suppresses_debug_when_disabled(self):
        import logging
        record = logging.LogRecord('vinylpi.core.audio_capture', logging.DEBUG, __file__, 1,
                                   'audio detail', (), None)
        with patch.object(logs, '_debug_enabled', return_value=False):
            logs.SQLiteHandler().emit(record)
        self.assertEqual(logs.read_events(level='all'), [])
        with patch.object(logs, '_debug_enabled', return_value=True):
            logs.SQLiteHandler().emit(record)
        self.assertEqual(logs.read_events(level='debug')[0]['message'], 'audio detail')

    def test_legacy_trace_is_read_as_debug(self):
        logs._connection().execute("INSERT INTO events(timestamp,level,source,message) VALUES (0,'TRACE','Vinyl','older event')")
        self.assertEqual(logs.read_events(level='debug')[0]['level'], 'DEBUG')

    def test_filter_summary_search_and_order(self):
        logs.append('Recording sample', level='DEBUG', source='Vinyl')
        logs.append('Sample ready', level='INFO', source='Vinyl')
        logs.append('Broken display connection', level='WARNING', source='Pixoo')
        logs.append('Spotify API error: rejected', level='ERROR', source='Spotify')
        all_rows = logs.read_events(level='all')
        self.assertEqual([r['level'] for r in all_rows], ['DEBUG', 'INFO', 'WARNING', 'ERROR'])
        issue_rows = logs.read_events(level='issues')
        self.assertEqual([r['level'] for r in issue_rows], ['WARNING', 'ERROR'])
        self.assertEqual(len(logs.read_events(level='all', search='display')), 1)
        self.assertEqual([r['level'] for r in logs.read_events(level='info')], ['INFO'])
        self.assertEqual([r['level'] for r in logs.read_events(level='debug')], ['DEBUG'])
        self.assertEqual(len(logs.read_events(after=all_rows[1]['id'], level='all')), 2)
        self.assertEqual(logs.summary(), {
            'latest_id': all_rows[-1]['id'], 'unread': 2, 'warnings': 1, 'errors': 1,
        })
        self.assertEqual(logs.summary(since=all_rows[-2]['id'])['unread'], 1)

    def test_clear_is_persistent_but_history_is_preserved(self):
        logs.append('old warning - 1', level='WARNING', source='Test')
        logs.append('old debug - 2', level='DEBUG', source='Test')
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

    def test_duplicate_issues_are_throttled_but_debug_is_not(self):
        logs.append('Unique API error: code z9', level='ERROR', source='Spotify')
        logs.append('Unique API error: code z9', level='ERROR', source='Spotify')
        logs.append('No song detected (#7 in a row).', level='DEBUG', source='Vinyl')
        logs.append('No song detected (#7 in a row).', level='DEBUG', source='Vinyl')
        self.assertEqual(len(logs.read_events(level='issues')), 1)
        self.assertEqual(len(logs.read_events(level='all')), 3)

    def test_unavailable_database_is_nonfatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            # sqlite3 cannot open a directory as a database file.
            with patch.object(logs, "LOG_PATH", Path(tmp)):
                logs.append("Recording failed", level="ERROR")

    def test_redaction_and_concurrent_writers(self):
        start = threading.Barrier(4)

        def write(i):
            if i < 4:
                start.wait(timeout=5)
            logs.append(f'worker-{i} token=PRIVATEKEY and Bearer HEADERSECRET', level='INFO')

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write, range(32)))
        rows = logs.read_events(level='all')
        self.assertEqual(len(rows), 32)
        self.assertEqual(
            {row['message'] for row in rows},
            {f'worker-{i} token=[redacted] and Bearer [redacted]' for i in range(32)},
        )

    def test_busy_insert_is_retried(self):
        conn = logs._connection()

        class LockedOnce:
            calls = 0

            def execute(self, sql, params=()):
                if sql.startswith('INSERT INTO events'):
                    self.calls += 1
                    if self.calls == 1:
                        raise sqlite3.OperationalError('database is locked')
                return conn.execute(sql, params)

        wrapper = LockedOnce()
        with patch.object(logs, '_connection', return_value=wrapper):
            logs.append('write retried', level='INFO')
        self.assertEqual(wrapper.calls, 2)
        self.assertEqual([row['message'] for row in logs.read_events(level='all')], ['write retried'])

    def test_failed_warning_write_does_not_hide_next_warning(self):
        message = 'Unique failed warning after broken database'
        with patch.object(logs, '_connection', side_effect=sqlite3.OperationalError('unable to open database')):
            logs.append(message, level='WARNING', source='Test')
        logs.append(message, level='WARNING', source='Test')
        self.assertEqual([row['message'] for row in logs.read_events(level='issues')], [message])


if __name__ == '__main__':
    unittest.main()
