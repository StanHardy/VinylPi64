from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class LyricsCacheTests(unittest.TestCase):
    def test_successful_lyrics_are_persistent_and_reused(self):
        try:
            from flask import Flask
            from vinylpi.web.routes import genius_api
        except ImportError as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as folder:
            app = Flask(__name__)
            app.register_blueprint(genius_api.genius_bp)
            payload = {"ok": True, "source": "genius", "lyrics": "Hello\nWorld", "lyrics_html": "Hello<br>World"}
            with patch.object(genius_api, "_cache_path", return_value=Path(folder) / "lyrics.sqlite3"), \
                 patch.object(genius_api, "get_lyrics", return_value=payload) as scrape:
                client = app.test_client()
                first = client.get('/api/lyrics?artist=The%20Band&title=One').json
                second = client.get('/api/lyrics?artist=the%20band&title=ONE').json
                # Cache misses and hits must preserve the public API contract.
                self.assertEqual(first, payload)
                self.assertEqual(second, payload)
                self.assertEqual(second['lyrics_html'], payload['lyrics_html'])
                scrape.assert_called_once_with('The Band', 'One')

    def test_cache_connections_are_closed_after_reads_and_writes(self):
        try:
            from flask import Flask
            from vinylpi.web.routes import genius_api
        except ImportError as exc:
            self.skipTest(str(exc))

        class TrackedConnection(sqlite3.Connection):
            def close(self):
                self.was_closed = True
                return super().close()

        original_connect = sqlite3.connect
        opened = []

        def tracked_connect(*args, **kwargs):
            conn = original_connect(*args, factory=TrackedConnection, **kwargs)
            opened.append(conn)
            return conn

        with tempfile.TemporaryDirectory() as folder:
            app = Flask(__name__)
            app.register_blueprint(genius_api.genius_bp)
            with patch.object(genius_api, "_cache_path", return_value=Path(folder) / "lyrics.sqlite3"), \
                 patch.object(genius_api, "get_lyrics", return_value={"ok": True, "lyrics": "Words"}), \
                 patch.object(genius_api.sqlite3, "connect", side_effect=tracked_connect):
                client = app.test_client()
                self.assertEqual(client.get('/api/lyrics?artist=A&title=B').json, {"ok": True, "lyrics": "Words"})
                self.assertEqual(client.get('/api/lyrics?artist=A&title=B').json, {"ok": True, "lyrics": "Words"})

        self.assertEqual(len(opened), 3)  # cache read + write, then cached read
        self.assertTrue(all(getattr(conn, "was_closed", False) for conn in opened))

    def test_missing_lyrics_not_persisted(self):
        try:
            from flask import Flask
            from vinylpi.web.routes import genius_api
        except ImportError as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as folder:
            app = Flask(__name__)
            app.register_blueprint(genius_api.genius_bp)
            with patch.object(genius_api, '_cache_path', return_value=Path(folder) / 'lyrics.sqlite3'), \
                 patch.object(genius_api, 'get_lyrics', return_value={"ok": False, "error": "not_found"}) as scrape:
                client = app.test_client()
                self.assertEqual(client.get('/api/lyrics?artist=A&title=B').json, {"ok": False, "error": "not_found"})
                self.assertEqual(client.get('/api/lyrics?artist=A&title=B').json, {"ok": False, "error": "not_found"})
                self.assertEqual(scrape.call_count, 2)


if __name__ == '__main__':
    unittest.main()
