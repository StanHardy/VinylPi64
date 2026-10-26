"""Behavioral tests for Pixoo-hosted animation playback."""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from vinylpi.core import display, pixoo_animation
from vinylpi.integrations.divoom_api import PixooClient, PixooError


class GifRenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patch_dir = patch.object(pixoo_animation, "GIF_DIR", Path(self.tmp.name))
        self.patch_dir.start()
        self.addCleanup(self.patch_dir.stop)

    def test_animation_loops_and_uses_existing_renderer(self):
        from vinylpi.config.config_loader import CONFIG_DEFAULTS
        cfg = dict(CONFIG_DEFAULTS["image"])
        cfg.update(show_cover=False, show_artist=False, show_album=False, show_title=True)
        resources = display._prepare_scroll_resources(
            Image.new("RGB", (64, 64), "navy"),
            "", "A very long example title for testing the Pixoo marquee",
            img_cfg=cfg,
        )
        self.assertGreater(resources["lines"][0]["width"], 64)
        token = pixoo_animation.write_animation(
            resources, 20, display._render_scroll_frame, threading.Event()
        )
        path = pixoo_animation.animation_file(token)
        self.assertTrue(path.is_file())
        with Image.open(path) as gif:
            self.assertEqual(gif.size, (64, 64))
            self.assertEqual(gif.info.get("loop"), 0)
            self.assertGreater(gif.n_frames, 1)
            self.assertLessEqual(gif.n_frames, pixoo_animation.MAX_FRAMES)
            self.assertGreater(gif.info.get("duration", 0), 0)
            first = gif.convert("RGB").copy()
            gif.seek(gif.n_frames // 2)
            middle = gif.convert("RGB").copy()
            self.assertNotEqual(first.tobytes(), middle.tobytes())

    def test_generation_can_be_cancelled_without_exposing_partial_file(self):
        cancelled = threading.Event()
        cancelled.set()
        resources = {
            "canvas_size": 64,
            "lines": [{"width": 100}],
            "base_canvas": Image.new("RGB", (64, 64)),
            "text_color": (255, 255, 255),
        }
        render = Mock()
        self.assertIsNone(pixoo_animation.write_animation(resources, 20, render, cancelled))
        render.assert_not_called()
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_long_text_keeps_full_scroll_period_without_excess_frames(self):
        cancelled = threading.Event()
        resources = {
            "canvas_size": 64,
            "lines": [{"width": 2100}],
            "base_canvas": Image.new("RGB", (64, 64)),
            "text_color": (255, 255, 255),
        }
        ticks = []
        def render(_resources, tick):
            ticks.append(tick)
            return Image.new("RGB", (64, 64), "red")
        token = pixoo_animation.write_animation(resources, 20, render, cancelled)
        self.assertIsNotNone(token)
        self.assertLessEqual(len(ticks), pixoo_animation.MAX_FRAMES)
        self.assertGreater(ticks[-1], 2000)

    def test_animation_url_uses_lan_route_and_rejects_invalid_token(self):
        sock = Mock()
        sock.__enter__ = Mock(return_value=sock)
        sock.__exit__ = Mock(return_value=None)
        sock.getsockname.return_value = ("192.168.4.10", 42000)
        with patch.object(pixoo_animation.socket, "socket", return_value=sock), \
             patch.dict(os.environ, {"VINYLPI_PIXOO_GIF_BASE_URL": ""}):
            url = pixoo_animation.build_animation_url("192.168.4.42", "a" * 32)
        self.assertEqual(url, "http://192.168.4.10:8080/api/pixoo/animation/" + "a" * 32 + ".gif")
        sock.connect.assert_called_once_with(("192.168.4.42", 80))
        with self.assertRaises(ValueError):
            pixoo_animation.build_animation_url("192.168.4.42", "../private")


class PixooGifCommandTests(unittest.TestCase):
    def test_pixoo_receives_only_one_playback_command(self):
        pixoo = object.__new__(PixooClient)
        pixoo._post = Mock(return_value={})
        pixoo.play_gif_url("http://192.168.1.10:8080/a.gif")
        pixoo._post.assert_called_once_with({
            "Command": "Device/PlayTFGif", "FileType": 2,
            "FileName": "http://192.168.1.10:8080/a.gif",
        })

    def test_url_validation(self):
        pixoo = object.__new__(PixooClient)
        pixoo._post = Mock()
        for url in ("https://example.com/a.gif", "file:///tmp/a.gif", "http://x/" + "b" * 256):
            with self.assertRaises(PixooError):
                pixoo.play_gif_url(url)
        pixoo._post.assert_not_called()


class DisplayTransportTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "divoom": {"animation_mode": "gif_url"},
            "debug": {"logs": False, "pixoo_frame_path": "", "preview_path": ""},
            "image": {"marquee_speed": 20, "sleep_seconds": .01},
        }
        self.image = Image.new("RGB", (64, 64))
        self.resources = {"canvas_size": 64, "lines": [{"width": 100}]}
        self.pixoo = Mock(ip="192.168.4.42")
        self.cancelled = threading.Event()

    def _run(self, animation_token, response=None):
        if response is None:
            response = Mock(headers={"Content-Type": "image/gif"})
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=None)
        with patch.object(display, "read_config", return_value=self.cfg), \
             patch.object(display, "_get_pixoo", return_value=self.pixoo), \
             patch.object(display, "_prepare_scroll_resources", return_value=self.resources), \
             patch.object(display, "_render_scroll_frame", return_value=self.image), \
             patch.object(pixoo_animation, "write_animation", return_value=animation_token), \
             patch.object(pixoo_animation, "build_animation_url", return_value="http://192.168.4.10:8080/a.gif"), \
             patch.object(display.requests, "get", return_value=response):
            self.cancelled.set() if animation_token is None else None
            display._scroll_loop(self.image, "artist", "title", stop_event=self.cancelled)

    def test_cancelled_generation_does_not_control_pixoo(self):
        self._run(None)
        self.pixoo.play_gif_url.assert_not_called()
        self.pixoo.send_frame.assert_not_called()

    def test_successful_gif_uses_no_live_frame_stream(self):
        self.pixoo.play_gif_url.side_effect = lambda url: self.cancelled.set()
        self._run("a" * 32)
        self.pixoo.play_gif_url.assert_called_once_with("http://192.168.4.10:8080/a.gif")
        self.pixoo.send_frame.assert_not_called()

    def test_offline_web_server_falls_back_to_live_frames(self):
        response = Mock(headers={"Content-Type": "text/html"})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        self.resources["lines"] = [{"width": 100}]
        self.pixoo.send_frame.side_effect = lambda frame: self.cancelled.set()
        self._run("a" * 32, response=response)
        self.pixoo.send_frame.assert_called_once()
        self.pixoo.play_gif_url.assert_not_called()


class GifEndpointTests(unittest.TestCase):
    def test_pixoo_can_fetch_generated_gif_without_browser_session(self):
        try:
            from flask import Flask
            from vinylpi.web.routes.pixoo_api import pixoo_bp
        except ImportError as exc:
            self.skipTest(f"Flask dependencies unavailable: {exc}")

        with tempfile.TemporaryDirectory() as tmp, patch.object(pixoo_animation, "GIF_DIR", Path(tmp)):
            token = "a" * 32
            (Path(tmp) / f"{token}.gif").write_bytes(b"GIF89a")
            app = Flask(__name__)
            app.register_blueprint(pixoo_bp)
            client = app.test_client()

            response = client.get(f"/api/pixoo/animation/{token}.gif")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "image/gif")
            self.assertEqual(response.data, b"GIF89a")
            self.assertEqual(client.get("/api/pixoo/animation/invalid.gif").status_code, 404)
            self.assertEqual(client.get("/api/pixoo/animation/" + "b" * 32 + ".gif").status_code, 404)


if __name__ == "__main__":
    unittest.main()
