"""Native Pixoo text transport and display transition tests."""
from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock, patch, call

from PIL import Image

from vinylpi.core import display
from vinylpi.config.config_loader import CONFIG_DEFAULTS
from vinylpi.integrations.divoom_api import PixooClient, PixooError


class NativeTextApiTests(unittest.TestCase):
    def setUp(self):
        self.client = object.__new__(PixooClient)
        self.client._post = Mock(return_value={})

    def test_send_native_text_payload(self):
        self.client.send_http_text(text_id=2, text="IN RAINBOWS", y=51,
                                   font=4, speed=55, color=(12, 200, 255))
        self.client._post.assert_called_once_with({
            "Command": "Draw/SendHttpText", "TextId": 2,
            "x": 0, "y": 51, "dir": 0, "font": 4, "TextWidth": 64,
            "TextString": "IN RAINBOWS", "speed": 55,
            "color": "#0CC8FF", "align": 2,
        })

    def test_reject_invalid_text_without_network_call(self):
        for kwargs in (
            {"text": ""}, {"text": "x" * 256}, {"font": 8}, {"y": 64},
            {"color": (-1, 0, 0)},
        ):
            payload = dict(text_id=1, text="song", y=48, font=0, color=(255, 255, 255))
            payload.update(kwargs)
            with self.assertRaises(PixooError):
                self.client.send_http_text(**payload)
        self.client._post.assert_not_called()

    def test_clear_native_text(self):
        self.client.clear_http_text()
        self.client._post.assert_called_once_with({"Command": "Draw/ClearHttpText"})


class NativeTextLayoutTests(unittest.TestCase):
    def test_cover_resizes_to_fit_native_font_rows(self):
        img_cfg = dict(CONFIG_DEFAULTS["image"])
        img_cfg.update(show_album=True, show_artist=True, show_title=True,
                       cover_size=46, font_path="assets/fonts/vinylpixel.ttf")
        cover = Image.new("RGB", (64, 64), "navy")
        resources = display._prepare_scroll_resources(
            cover, "RADIOHEAD", "WEIRD FISHES", "IN RAINBOWS", img_cfg=img_cfg
        )
        background, lines = display._prepare_native_text(cover, resources, font_height=10)
        self.assertEqual(background.size, (64, 64))
        self.assertEqual(len(lines), 3)
        self.assertEqual([entry["y"] for entry in lines], [
            lines[0]["y"], lines[0]["y"] + 13, lines[0]["y"] + 26,
        ])
        self.assertLessEqual(lines[-1]["y"] + 10, 64)
        # The embedded TTF is not loaded for native overlays.
        self.assertEqual(lines[1]["text"], "WEIRD FISHES")

    def test_cancelled_before_send_does_not_contact_pixoo(self):
        cover = Image.new("RGB", (64, 64), "black")
        cfg = dict(CONFIG_DEFAULTS["image"])
        resources = display._prepare_scroll_resources(cover, "A", "B", img_cfg=cfg)
        cancelled = threading.Event()
        cancelled.set()
        pixoo = Mock()
        self.assertFalse(display._start_native_text(pixoo, cover, resources,
                                                    {}, 20, cancelled))
        pixoo.send_frame.assert_not_called()
        pixoo.send_http_text.assert_not_called()


class NativeTextPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.prev_client = display._native_overlay_client
        display._native_overlay_client = None
        self.addCleanup(setattr, display, "_native_overlay_client", self.prev_client)
        self.cover = Image.new("RGB", (64, 64), "navy")
        cfg = dict(CONFIG_DEFAULTS["image"])
        self.resources = display._prepare_scroll_resources(
            self.cover, "RADIOHEAD", "WEIRD FISHES", img_cfg=cfg
        )
        self.config = {
            "divoom": {"animation_mode": "native_text", "native_text_font": 4,
                       "native_text_height": 9},
            "image": {"marquee_speed": 20, "sleep_seconds": .01},
            "debug": {"logs": False, "pixoo_frame_path": "", "preview_path": ""},
        }
        self.pixoo = Mock(ip="192.168.1.55")
        self.cancel = threading.Event()

    def test_native_playback_sends_one_background_and_three_overlays(self):
        # Backend owns the animation; this test must not enter the live frame loop.
        self.resources["lines"].append(dict(text="IN RAINBOWS", width=80, y=0))
        self.pixoo.send_http_text.side_effect = lambda **_: self.cancel.set() if self.pixoo.send_http_text.call_count == 3 else None
        with patch.object(display, "read_config", return_value=self.config), \
             patch.object(display, "_get_pixoo", return_value=self.pixoo), \
             patch.object(display, "_prepare_scroll_resources", return_value=self.resources):
            display._scroll_loop(self.cover, "A", "B", stop_event=self.cancel)
        self.pixoo.send_frame.assert_called_once()
        self.assertEqual(self.pixoo.send_http_text.call_count, 3)
        self.assertEqual([entry.kwargs["text_id"] for entry in self.pixoo.send_http_text.call_args_list], [1, 2, 3])
        self.assertEqual(self.pixoo.send_http_text.call_args.kwargs["font"], 4)
        self.pixoo.play_gif_url.assert_not_called()

    def test_firmware_error_falls_back_to_live_frames(self):
        self.pixoo.send_http_text.side_effect = PixooError("no native fonts")
        self.pixoo.send_frame.side_effect = lambda *_: self.cancel.set() if self.pixoo.send_frame.call_count > 1 else None
        with patch.object(display, "read_config", return_value=self.config), \
             patch.object(display, "_get_pixoo", return_value=self.pixoo), \
             patch.object(display, "_prepare_scroll_resources", return_value=self.resources):
            display._scroll_loop(self.cover, "A", "B", stop_event=self.cancel)
        self.assertGreaterEqual(self.pixoo.send_frame.call_count, 2)
        self.pixoo.clear_http_text.assert_called()
        self.pixoo.play_gif_url.assert_not_called()

    def test_stopping_native_mode_clears_overlays(self):
        display._native_overlay_client = self.pixoo
        with patch.object(display, "_scroll_thread", None):
            display.stop_scrolling_display()
        self.pixoo.clear_http_text.assert_called_once_with()
        self.assertIsNone(display._native_overlay_client)


if __name__ == "__main__":
    unittest.main()
