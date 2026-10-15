from __future__ import annotations

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
                self.assertFalse(first['cached'])
                self.assertTrue(second['cached'])
                self.assertEqual(second['lyrics_html'], payload['lyrics_html'])
                scrape.assert_called_once()

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
                client.get('/api/lyrics?artist=A&title=B')
                client.get('/api/lyrics?artist=A&title=B')
                self.assertEqual(scrape.call_count, 2)


if __name__ == '__main__':
    unittest.main()
