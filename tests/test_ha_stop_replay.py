from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from vinylpi.integrations.pixoo_automations import on_playback_started, on_recognition_stopped, on_vinyl_started
from vinylpi.web.services import source


class PixooDirectActionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"pixoo_automations": {"turn_on_on_start": True,
                                          "off_action": "community_gif",
                                          "stop_gif_file_id": "4321"},
                    "divoom": {"ip": "192.0.2.25", "timeout": 1},
                    "homeassistant": {"use_ha": False}}

    @patch("vinylpi.integrations.pixoo_automations._client")
    @patch("vinylpi.integrations.pixoo_automations.read_config")
    def test_start_is_independent_of_ha(self, config, client):
        config.return_value = self.cfg
        self.assertTrue(on_vinyl_started(reason="auto_wake"))
        client.return_value.set_brightness.assert_called_once_with(100)

    @patch("vinylpi.integrations.pixoo_automations._client")
    @patch("vinylpi.integrations.pixoo_automations.read_config")
    def test_spotify_start_restores_brightness(self, config, client):
        config.return_value = self.cfg
        self.assertTrue(on_playback_started(source="spotify"))
        client.return_value.set_brightness.assert_called_once_with(100)

    @patch("vinylpi.integrations.pixoo_automations._client")
    @patch("vinylpi.integrations.pixoo_automations.read_config")
    def test_stop_plays_selected_gif(self, config, client):
        config.return_value = self.cfg
        self.assertTrue(on_recognition_stopped(source="spotify", reason="auto_sleep"))
        client.return_value.play_remote_gif.assert_called_once_with("4321")

    @patch("vinylpi.integrations.pixoo_automations._client")
    @patch("vinylpi.integrations.pixoo_automations.read_config")
    def test_turn_off_sets_brightness_to_zero(self, config, client):
        config.return_value = self.cfg
        self.cfg["pixoo_automations"]["off_action"] = "brightness_off"
        self.assertTrue(on_recognition_stopped(source="vinyl", reason="auto_sleep"))
        client.return_value.set_brightness.assert_called_once_with(0)
        client.return_value.play_remote_gif.assert_not_called()

    @patch("vinylpi.integrations.pixoo_automations._client")
    @patch("vinylpi.integrations.pixoo_automations.read_config")
    def test_disabled_action_does_not_contact_pixoo(self, config, client):
        config.return_value = self.cfg
        self.cfg["pixoo_automations"]["off_action"] = "none"
        self.assertFalse(on_recognition_stopped(source="spotify", reason="manual"))
        client.assert_not_called()

    @patch("vinylpi.integrations.pixoo_automations._client")
    @patch("vinylpi.integrations.pixoo_automations.read_config")
    def test_missing_gif_does_not_turn_off_brightness(self, config, client):
        config.return_value = self.cfg
        self.cfg["pixoo_automations"]["off_action"] = "community_gif"
        self.cfg["pixoo_automations"]["stop_gif_file_id"] = ""
        self.assertFalse(on_recognition_stopped(source="vinyl", reason="auto_sleep"))
        client.assert_not_called()

    @patch("vinylpi.integrations.pixoo_automations._client")
    @patch("vinylpi.integrations.pixoo_automations.read_config")
    def test_disabled_and_missing_selection_do_not_send(self, config, client):
        config.return_value = self.cfg
        self.cfg["pixoo_automations"]["turn_on_on_start"] = False
        self.cfg["pixoo_automations"]["stop_gif_file_id"] = ""
        self.assertFalse(on_vinyl_started())
        self.assertFalse(on_recognition_stopped(source="vinyl", reason="manual"))
        client.assert_not_called()

    @patch("vinylpi.integrations.pixoo_automations._client", side_effect=OSError("offline"))
    @patch("vinylpi.integrations.pixoo_automations.read_config")
    def test_offline_does_not_break_source_switch(self, config, client):
        config.return_value = self.cfg
        self.assertFalse(on_vinyl_started())
        self.assertFalse(on_recognition_stopped(source="vinyl", reason="manual"))


class SpotifyStartAutomationTests(unittest.TestCase):
    @patch("vinylpi.web.services.spotify.on_playback_started")
    @patch("vinylpi.web.services.spotify.subprocess.Popen")
    def test_only_new_worker_triggers_turn_on(self, popen, on_started):
        from vinylpi.web.services import spotify
        previous = spotify._spotify_proc
        spotify._spotify_proc = None
        self.addCleanup(setattr, spotify, "_spotify_proc", previous)
        popen.return_value.poll.return_value = None
        self.assertTrue(spotify.start(silence_output=True))
        self.assertFalse(spotify.start(silence_output=True))
        on_started.assert_called_once_with(source="spotify")
        popen.assert_called_once()


class SourceTransitionTests(unittest.TestCase):
    @patch("vinylpi.web.services.auto_wake_monitor.resume")
    @patch("vinylpi.web.services.source.get_status", return_value={"mode": "off"})
    @patch("vinylpi.web.services.source.set_runtime_profile")
    @patch("vinylpi.web.services.source.show_fallback_image")
    @patch("vinylpi.web.services.source.recognizer.stop")
    @patch("vinylpi.web.services.source.spotify.stop")
    @patch("vinylpi.web.services.source.on_recognition_stopped")
    @patch("vinylpi.web.services.source.get_runtime_profile", return_value={"storage_key": "user"})
    @patch("vinylpi.web.services.source.get_active_storage_key", return_value="user")
    @patch("vinylpi.web.services.source.get_mode", return_value="vinyl")
    def test_manual_off_plays_gif_after_fallback(
        self, get_mode, viewer, profile, send, spotify_stop, vinyl_stop,
        fallback, set_profile, status, resume,
    ):
        events = []
        fallback.side_effect = lambda: events.append("fallback")
        send.side_effect = lambda **kw: events.append("pixoo")
        source.set_mode("off")
        self.assertEqual(events, ["fallback", "pixoo"])
        send.assert_called_once_with(source="vinyl", reason="manual")

    @patch("vinylpi.web.services.auto_wake_monitor.resume")
    @patch("vinylpi.web.services.source.get_status", return_value={"mode": "off"})
    @patch("vinylpi.web.services.source.set_runtime_profile")
    @patch("vinylpi.web.services.source.show_fallback_image")
    @patch("vinylpi.web.services.source.recognizer.stop")
    @patch("vinylpi.web.services.source.spotify.stop")
    @patch("vinylpi.web.services.source.on_recognition_stopped")
    @patch("vinylpi.web.services.source.get_runtime_profile", return_value={"storage_key": "user"})
    @patch("vinylpi.web.services.source.get_active_storage_key", return_value="user")
    @patch("vinylpi.web.services.source.get_mode", return_value="off")
    def test_repeated_off_does_not_replay(self, get_mode, viewer, profile, send, spotify_stop, vinyl_stop,
                                       fallback, set_profile, status, resume):
        source.set_mode("off")
        fallback.assert_called_once()
        send.assert_not_called()


class AutomaticStopTests(unittest.TestCase):
    @unittest.skipUnless(__import__("importlib").util.find_spec("sounddevice"), "PortAudio/sounddevice unavailable")
    @patch("vinylpi.core.runner.on_recognition_stopped")
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
    def test_vinyl_audio_standby_plays_after_fallback(
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
        send.side_effect = lambda **kw: events.append("pixoo")
        main_loop()
        self.assertEqual(events, ["fallback", "pixoo"])
        send.assert_called_once_with(source="vinyl", reason="audio_standby")

    @unittest.skipUnless(__import__("importlib").util.find_spec("sounddevice"), "PortAudio/sounddevice unavailable")
    @patch("vinylpi.core.runner.on_recognition_stopped")
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

    @patch("vinylpi.spotify_worker.on_recognition_stopped")
    @patch("vinylpi.spotify_worker.show_fallback_image")
    @patch("vinylpi.spotify_worker._auto_sleep_settings", return_value=(True, 1))
    @patch("vinylpi.spotify_worker._backfill_missing_genres")
    @patch("vinylpi.spotify_worker.get_last_source_status", return_value={})
    @patch("vinylpi.spotify_worker.get_active_db_path", return_value=Path("/tmp/mock-spotify.db"))
    @patch("vinylpi.spotify_worker.SpotifyClient")
    @patch("vinylpi.spotify_worker.start_display_refresh_watcher")
    @patch("vinylpi.spotify_worker.load_dotenv")
    @patch("vinylpi.spotify_worker.read_config", return_value={"debug": {"logs": False}})
    def test_spotify_idle_auto_sleep_plays_gif(self, read_config, load_env, watcher, client, db_path,
                                      last_status, backfill, auto_settings, fallback, send):
        client.return_value.get_currently_playing.return_value = None
        from vinylpi.spotify_worker import main
        main()
        fallback.assert_called_once()
        send.assert_called_once_with(source="spotify", reason="auto_sleep")


if __name__ == "__main__":
    unittest.main()
