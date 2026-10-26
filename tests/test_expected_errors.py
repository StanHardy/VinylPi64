"""Failure handling at external-system boundaries.

These tests exercise behavior (fallbacks/retries/classification), not log copy
or visual layout. No live services or hardware are required.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from vinylpi.config.config_loader import load_config, CONFIG_DEFAULTS
from vinylpi.core import status
from vinylpi.integrations.spotify_client import (
    SpotifyClient, SpotifyNotAuthorized, SpotifyRateLimited, SpotifyUnavailable,
)


class ConfigFailureTests(unittest.TestCase):
    def test_missing_profile_uses_defaults_without_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('vinylpi.config.config_loader.logger') as logger:
                config = load_config(Path(directory) / 'missing.json')
        self.assertEqual(config, CONFIG_DEFAULTS)
        logger.error.assert_not_called()

    def test_invalid_json_reports_failure_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text('{not JSON', encoding='utf-8')
            with patch('vinylpi.config.config_loader.logger') as logger:
                config = load_config(path)
        self.assertEqual(config, CONFIG_DEFAULTS)
        logger.error.assert_called_once()
        logger.exception.assert_not_called()

    def test_non_object_config_is_not_merged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps(['not', 'a', 'config']), encoding='utf-8')
            with patch('vinylpi.config.config_loader.logger') as logger:
                config = load_config(path)
        self.assertEqual(config, CONFIG_DEFAULTS)
        logger.error.assert_called_once()


class SpotifyFailureTests(unittest.TestCase):
    def make_client(self):
        # An authorized in-memory client; no profile database or OAuth request.
        client = object.__new__(SpotifyClient)
        client._access_token = 'test-token'
        client._access_token_expires_at = 0.0
        client.refresh_token = 'unused'
        client.timeout = 1.0
        return client

    @patch('vinylpi.integrations.spotify_client.requests.get', side_effect=requests.Timeout())
    def test_timeout_is_transient(self, get):
        with self.assertRaises(SpotifyUnavailable) as caught:
            self.make_client()._api_get('/me')
        self.assertIn('timed out', str(caught.exception))

    @patch('vinylpi.integrations.spotify_client.requests.get', side_effect=requests.ConnectionError())
    def test_connection_failure_is_transient(self, get):
        with self.assertRaises(SpotifyUnavailable):
            self.make_client()._api_get('/me')

    @patch('vinylpi.integrations.spotify_client.requests.get')
    def test_rate_limit_is_not_auth_failure(self, get):
        get.return_value.status_code = 429
        with self.assertRaises(SpotifyRateLimited):
            self.make_client().get_currently_playing()

    @patch('vinylpi.integrations.spotify_client.requests.get')
    def test_outage_is_transient(self, get):
        get.return_value.status_code = 503
        with self.assertRaises(SpotifyUnavailable):
            self.make_client().get_currently_playing()

    @patch('vinylpi.integrations.spotify_client.requests.get')
    def test_expired_session_requires_authentication(self, get):
        get.return_value.status_code = 401
        # The one allowed refresh attempt fails authorization too.
        with patch.object(SpotifyClient, 'refresh_access_token', side_effect=SpotifyNotAuthorized('Expired')):
            with self.assertRaises(SpotifyNotAuthorized):
                self.make_client().get_currently_playing()

    @patch('vinylpi.integrations.spotify_client.requests.get')
    def test_malformed_service_response_is_transient(self, get):
        get.return_value.status_code = 200
        get.return_value.json.side_effect = ValueError('invalid JSON')
        with self.assertRaises(SpotifyUnavailable):
            self.make_client().get_currently_playing()


class DatabaseFailureTests(unittest.TestCase):
    @patch.object(status, 'write_current_status', side_effect=sqlite3.OperationalError('database is locked'))
    @patch.object(status.logger, 'exception')
    @patch.object(status.logger, 'warning')
    def test_sqlite_lock_is_recoverable(self, warning, exception, write):
        status.write_status('Artist', 'Track')
        warning.assert_called_once()
        exception.assert_not_called()

    @patch.object(status, 'write_current_status', side_effect=RuntimeError('unexpected error'))
    @patch.object(status.logger, 'exception')
    @patch.object(status.logger, 'warning')
    def test_programming_failure_preserves_traceback(self, warning, exception, write):
        status.write_status('Artist', 'Track')
        warning.assert_not_called()
        exception.assert_called_once()


try:
    from flask import Flask
    from vinylpi.web.routes.pixoo_api import pixoo_bp
    from vinylpi.integrations.divoom_api import PixooError
except ModuleNotFoundError:
    Flask = None
    PixooError = RuntimeError


@unittest.skipIf(Flask is None, 'Flask unavailable')
class PixooApiFailureTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(pixoo_bp)
        app.testing = True
        self.client = app.test_client()

    @patch('vinylpi.web.routes.pixoo_api.pixoo.set_brightness', side_effect=PixooError('unreachable'))
    def test_unreachable_device_is_service_unavailable(self, brightness):
        response = self.client.post('/api/pixoo/brightness', json={'brightness': 0})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.get_json()['ok'])

    @patch('vinylpi.web.routes.pixoo_api.pixoo.discover_cloud_device', side_effect=PixooError('cloud timeout'))
    def test_cloud_outage_is_bad_gateway(self, discover):
        response = self.client.post('/api/pixoo/discover-and-save')
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.get_json()['ok'])


try:
    from vinylpi.core import audio_capture
except (ModuleNotFoundError, OSError):  # Optional PortAudio dependency for CI/dev hosts
    audio_capture = None


@unittest.skipIf(audio_capture is None, 'PortAudio unavailable')
class AudioFailureTests(unittest.TestCase):
    @patch('vinylpi.core.audio_capture.read_config')
    @patch('vinylpi.core.audio_capture.auto_detect_usb_device', return_value=0)
    @patch('vinylpi.core.audio_capture.invalidate_audio_device')
    @patch('vinylpi.core.audio_capture.sd.rec')
    def test_disconnected_device_does_not_log_traceback(self, rec, invalidate, device, config):
        config.return_value = {
            'audio': {'sample_rate': 44100, 'sample_seconds': 2, 'channels': 1},
            'debug': {'logs': False, 'wav_path': ''},
        }
        rec.side_effect = audio_capture.sd.PortAudioError('Device unavailable')
        with patch.object(audio_capture.logger, 'warning') as warning, \
             patch.object(audio_capture.logger, 'exception') as exception:
            self.assertIsNone(audio_capture.record_sample())
        invalidate.assert_called_once()
        warning.assert_called_once()
        exception.assert_not_called()


if __name__ == '__main__':
    unittest.main()
