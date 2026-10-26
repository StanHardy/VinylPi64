"""Device-side Pixoo controls, automatic GIF caching, and selection persistence."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from vinylpi.config.config_loader import CONFIG_DEFAULTS, load_config
from vinylpi.integrations.divoom_api import PixooClient
from vinylpi.web.services import pixoo


class PixooAutomationsTests(unittest.TestCase):
    def test_default_disabled_and_no_legacy_ha_callbacks(self):
        self.assertFalse(CONFIG_DEFAULTS["pixoo_automations"]["turn_on_on_start"])
        self.assertEqual(CONFIG_DEFAULTS["pixoo_automations"]["off_action"], "none")
        self.assertNotIn("stop_webhook_id", CONFIG_DEFAULTS["homeassistant"])
        self.assertNotIn("wake_webhook_id", CONFIG_DEFAULTS["homeassistant"])

    def test_obsolete_ha_settings_are_removed_on_config_load(self):
        with tempfile.TemporaryDirectory() as folder:
            location = Path(folder) / "config.json"
            location.write_text(json.dumps({"homeassistant": {"use_ha": True,
                "base_url": "http://ha", "webhook_id": "rgb",
                "stop_webhook_id": "deprecated", "wake_webhook_id": "deprecated"}}))
            cfg = load_config(location)
            self.assertEqual(cfg["homeassistant"]["webhook_id"], "rgb")
            self.assertNotIn("stop_webhook_id", cfg["homeassistant"])
            self.assertNotIn("wake_webhook_id", cfg["homeassistant"])

    def test_legacy_stop_gif_migrates_to_off_action(self):
        with tempfile.TemporaryDirectory() as folder:
            location = Path(folder) / "config.json"
            location.write_text(json.dumps({"pixoo_automations": {
                "turn_on_on_vinyl_start": True,
                "play_gif_on_stop": True, "stop_gif_file_id": "4321",
            }, "divoom": {"animation_mode": "native_text", "native_text_font": 3}}))
            loaded = load_config(location)
            self.assertEqual(loaded["pixoo_automations"]["off_action"], "community_gif")
            self.assertTrue(loaded["pixoo_automations"]["turn_on_on_start"])
            self.assertNotIn("turn_on_on_vinyl_start", loaded["pixoo_automations"])
            self.assertEqual(loaded["pixoo_automations"]["stop_gif_file_id"], "4321")
            self.assertNotIn("play_gif_on_stop", loaded["pixoo_automations"])
            self.assertEqual(loaded["divoom"]["animation_mode"], "stream")
            self.assertNotIn("native_text_font", loaded["divoom"])

    def test_explicit_off_action_overrides_legacy_switch(self):
        with tempfile.TemporaryDirectory() as folder:
            location = Path(folder) / "config.json"
            location.write_text(json.dumps({"pixoo_automations": {
                "off_action": "brightness_off", "play_gif_on_stop": True,
            }}))
            loaded = load_config(location)
            self.assertEqual(loaded["pixoo_automations"]["off_action"], "brightness_off")
            self.assertNotIn("play_gif_on_stop", loaded["pixoo_automations"])

    @patch("vinylpi.integrations.divoom_api.read_config")
    def test_fast_client_does_not_probe_or_discover(self, config):
        config.return_value = {"divoom": {"ip": "192.0.2.5", "timeout": 1}, "debug": {"logs": False}}
        with patch("vinylpi.integrations.divoom_api._probe_ip") as probe, patch("vinylpi.integrations.divoom_api.discover_pixoo_ip") as discover:
            client = PixooClient(auto_discover=False)
            self.assertEqual(client.base_url, "http://192.0.2.5/post")
            probe.assert_not_called()
            discover.assert_not_called()

    def test_device_scoped_cache_persists_and_refreshes(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = {"divoom": {"device_id": 7, "device_mac": "aa:bb"}}
            with patch.object(pixoo, "_CACHE_PATH", Path(folder) / "gifs.json"), \
                 patch("vinylpi.web.services.pixoo.read_config", return_value=cfg), \
                 patch("vinylpi.web.services.pixoo.PixooClient") as client:
                client.return_value.get_liked_gifs.return_value = [{"file_id": "123", "file_name": "Animated"}]
                first, cached = pixoo.get_liked_gifs()
                self.assertFalse(cached)
                self.assertEqual(first[0]["file_id"], "123")
                second, cached = pixoo.get_liked_gifs()
                self.assertTrue(cached)
                self.assertEqual(second, first)
                client.return_value.get_liked_gifs.assert_called_once()
                client.assert_called_once_with(auto_discover=False)
                newer, cached = pixoo.get_liked_gifs(refresh=True)
                self.assertFalse(cached)
                self.assertEqual(client.return_value.get_liked_gifs.call_count, 2)

    def test_missing_pixoo_identity_does_not_contact_divoom(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(pixoo, "_CACHE_PATH", Path(folder) / "gifs.json"), \
                 patch("vinylpi.web.services.pixoo.read_config", return_value={"divoom": {"device_id": 0, "device_mac": ""}}), \
                 patch("vinylpi.web.services.pixoo.PixooClient") as client:
                gifs, cached = pixoo.get_liked_gifs(refresh=True)
                self.assertEqual(gifs, [])
                self.assertFalse(cached)
                client.assert_not_called()

    @patch("vinylpi.web.services.pixoo.write_config")
    def test_selected_gif_is_persisted_to_active_config(self, write_config):
        pixoo.set_selected_stop_gif(" 0123 ")
        write_config.assert_called_once_with({"pixoo_automations": {"stop_gif_file_id": "0123"}})


if __name__ == "__main__":
    unittest.main()
