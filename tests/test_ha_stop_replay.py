from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

from vinylpi.integrations.home_assistant import send_recognition_stopped
from vinylpi.web.services import source


class StopWebhookTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "homeassistant": {
                "use_ha": True,
                "base_url": "http://homeassistant:8123/",
                "webhook_id": "rgb-webhook",
                "replay_on_stop_enabled": True,
                "stop_webhook_id": "stop-only-webhook",
            }
        }

    @patch("vinylpi.integrations.home_assistant._session.post")
    @patch("vinylpi.integrations.home_assistant.read_config")
    def test_stop_uses_dedicated_webhook_not_rgb(self, read_config, post):
        read_config.return_value = self.cfg
        self.assertTrue(send_recognition_stopped(source="spotify", reason="auto_sleep"))
        post.assert_called_once_with(
            "http://homeassistant:8123/api/webhook/stop-only-webhook",
            json={"event": "recognition_stopped", "source": "spotify", "reason": "auto_sleep"},
            timeout=2,
        )

    @patch("vinylpi.integrations.home_assistant._session.post")
    @patch("vinylpi.integrations.home_assistant.read_config")
    def test_disabled_does_not_send(self, read_config, post):
        self.cfg["homeassistant"]["replay_on_stop_enabled"] = False
        read_config.return_value = self.cfg
        self.assertFalse(send_recognition_stopped(source="vinyl", reason="manual"))
        post.assert_not_called()

    @patch("vinylpi.integrations.home_assistant._session.post")
    @patch("vinylpi.integrations.home_assistant.read_config")
    def test_missing_webhook_does_not_send(self, read_config, post):
        self.cfg["homeassistant"]["stop_webhook_id"] = ""
        read_config.return_value = self.cfg
        self.assertFalse(send_recognition_stopped(source="vinyl", reason="manual"))
        post.assert_not_called()

    @patch("vinylpi.integrations.home_assistant._session.post", side_effect=requests.Timeout("offline"))
    @patch("vinylpi.integrations.home_assistant.read_config")
    def test_offline_ha_never_crashes_stop(self, read_config, post):
        read_config.return_value = self.cfg
        self.assertFalse(send_recognition_stopped(source="vinyl", reason="manual"))
        post.assert_called_once()


class SourceTransitionTests(unittest.TestCase):
    @patch("vinylpi.web.services.auto_wake_monitor.resume")
    @patch("vinylpi.web.services.source.get_status", return_value={"mode": "off"})
    @patch("vinylpi.web.services.source.set_runtime_profile")
    @patch("vinylpi.web.services.source.show_fallback_image")
    @patch("vinylpi.web.services.source.recognizer.stop")
    @patch("vinylpi.web.services.source.spotify.stop")
    @patch("vinylpi.web.services.source.send_recognition_stopped")
    @patch("vinylpi.web.services.source.get_runtime_profile", return_value={"storage_key": "user"})
    @patch("vinylpi.web.services.source.get_active_storage_key", return_value="user")
    @patch("vinylpi.web.services.source.get_mode", return_value="vinyl")
    def test_manual_off_emits_after_fallback(
        self, get_mode, viewer, profile, send, spotify_stop, vinyl_stop,
        fallback, set_profile, status, resume,
    ):
        events = []
        fallback.side_effect = lambda: events.append("fallback")
        send.side_effect = lambda **kw: events.append("webhook")
        source.set_mode("off")
        self.assertEqual(events, ["fallback", "webhook"])
        send.assert_called_once_with(source="vinyl", reason="manual")

    @patch("vinylpi.web.services.auto_wake_monitor.resume")
    @patch("vinylpi.web.services.source.get_status", return_value={"mode": "off"})
    @patch("vinylpi.web.services.source.set_runtime_profile")
    @patch("vinylpi.web.services.source.show_fallback_image")
    @patch("vinylpi.web.services.source.recognizer.stop")
    @patch("vinylpi.web.services.source.spotify.stop")
    @patch("vinylpi.web.services.source.send_recognition_stopped")
    @patch("vinylpi.web.services.source.get_runtime_profile", return_value={"storage_key": "user"})
    @patch("vinylpi.web.services.source.get_active_storage_key", return_value="user")
    @patch("vinylpi.web.services.source.get_mode", return_value="off")
    def test_repeated_off_does_not_emit(self, get_mode, viewer, profile, send, spotify_stop, vinyl_stop,
                                       fallback, set_profile, status, resume):
        source.set_mode("off")
        fallback.assert_called_once()
        send.assert_not_called()


class AutomaticStopTests(unittest.TestCase):
    @patch("vinylpi.core.runner.send_recognition_stopped")
    @patch("vinylpi.core.runner.show_fallback_image")
    @patch("vinylpi.core.runner.flush_timed_listen_if_needed")
    @patch("vinylpi.core.runner.record_sample", return_value=b"audio")
    @patch("vinylpi.core.runner.measure_wav_level", return_value=SimpleNamespace(dbfs=-75, duration_seconds=4))
    @patch("vinylpi.core.runner.LowAudioSleepTracker")
    @patch("vinylpi.core.runner.PickupUsageTracker")
    @patch("vinylpi.core.runner.restore_last_vinyl_song")
    @patch("vinylpi.core.runner.start_display_refresh_watcher")
    @patch("vinylpi.core.runner.maybe_log_config_reload")
    @patch("vinylpi.core.runner.LoopConfig.from_config")
    @patch("vinylpi.core.runner.initialize_storage")
    @patch("vinylpi.core.runner.read_config", return_value={"debug": {"logs": False}})
    def test_vinyl_audio_standby_sends_after_fallback(
        self, read_config, storage, loop_cfg, reload, watcher, restore, pickup, audio,
        measure, record, flush, fallback, send,
    ):
        loop_cfg.return_value = SimpleNamespace(debug_log=False, delay=0, sample_seconds_for_failures=lambda n: 4)
        pickup.return_value.config.enabled = False
        audio.return_value.config = SimpleNamespace(enabled=True, threshold_dbfs=-61, confirm_seconds=30)
        audio.return_value.observe.return_value = True
        from vinylpi.core.runner import main_loop
        events = []
        fallback.side_effect = lambda: events.append("fallback")
        send.side_effect = lambda **kw: events.append("webhook")
        main_loop()
        self.assertEqual(events, ["fallback", "webhook"])
        send.assert_called_once_with(source="vinyl", reason="audio_standby")

    @patch("vinylpi.core.runner.send_recognition_stopped")
    @patch("vinylpi.core.runner.handle_no_result", return_value=True)
    @patch("vinylpi.core.runner.get_side_flip_prompt", return_value=None)
    @patch("vinylpi.core.runner.flush_timed_listen_if_needed")
    @patch("vinylpi.core.runner.should_hold_inferred_track", return_value=False)
    @patch("vinylpi.core.runner.infer_expected_next_track", return_value=None)
    @patch("vinylpi.core.runner.recognize_song", return_value=None)
    @patch("vinylpi.core.runner.record_sample", return_value=b"audio")
    @patch("vinylpi.core.runner.LowAudioSleepTracker")
    @patch("vinylpi.core.runner.PickupUsageTracker")
    @patch("vinylpi.core.runner.restore_last_vinyl_song")
    @patch("vinylpi.core.runner.start_display_refresh_watcher")
    @patch("vinylpi.core.runner.maybe_log_config_reload")
    @patch("vinylpi.core.runner.LoopConfig.from_config")
    @patch("vinylpi.core.runner.initialize_storage")
    @patch("vinylpi.core.runner.read_config", return_value={"debug": {"logs": False}})
    def test_vinyl_recognition_failures_auto_sleep(
        self, read_config, storage, loop_cfg, reload, watcher, restore, pickup, audio,
        record, recognize, infer, hold, flush, side_flip, no_result, send,
    ):
        loop_cfg.return_value = SimpleNamespace(debug_log=False, delay=0, sample_seconds_for_failures=lambda n: 4)
        pickup.return_value.config.enabled = False
        audio.return_value.config.enabled = False
        from vinylpi.core.runner import main_loop
        main_loop()
        send.assert_called_once_with(source="vinyl", reason="auto_sleep")

    @patch("vinylpi.spotify_worker.send_recognition_stopped")
    @patch("vinylpi.spotify_worker.show_fallback_image")
    @patch("vinylpi.spotify_worker._auto_sleep_settings", return_value=(True, 1))
    @patch("vinylpi.spotify_worker._backfill_missing_genres")
    @patch("vinylpi.spotify_worker.get_last_source_status", return_value={})
    @patch("vinylpi.spotify_worker.get_active_db_path", return_value=Path("/tmp/mock-spotify.db"))
    @patch("vinylpi.spotify_worker.SpotifyClient")
    @patch("vinylpi.spotify_worker.start_display_refresh_watcher")
    @patch("vinylpi.spotify_worker.load_dotenv")
    @patch("vinylpi.spotify_worker.read_config", return_value={"debug": {"logs": False}})
    def test_spotify_idle_auto_sleep(self, read_config, load_env, watcher, client, db_path,
                                      last_status, backfill, auto_settings, fallback, send):
        client.return_value.get_currently_playing.return_value = None
        from vinylpi.spotify_worker import main
        main()
        fallback.assert_called_once()
        send.assert_called_once_with(source="spotify", reason="auto_sleep")


if __name__ == "__main__":
    unittest.main()
