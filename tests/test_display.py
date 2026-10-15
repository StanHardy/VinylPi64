from __future__ import annotations

import unittest
from pathlib import Path

from PIL import Image, ImageChops

from vinylpi.core.display import _overlay_side_letter


class SideFlipImageTests(unittest.TestCase):
    def setUp(self):
        self.template_path = Path("assets/fallback/turn/turn_record.png")

    def test_target_side_letter_is_inserted_only_in_badge_slot(self):
        template = Image.open(self.template_path).convert("RGB")

        side_b = _overlay_side_letter(template.copy(), "B")
        side_d = _overlay_side_letter(template.copy(), "D")

        difference = ImageChops.difference(side_b, side_d)
        bbox = difference.getbbox()
        self.assertIsNotNone(bbox)
        self.assertGreaterEqual(bbox[0], 38)
        self.assertGreaterEqual(bbox[1], 54)
        self.assertLessEqual(bbox[2], 43)
        self.assertLessEqual(bbox[3], 59)

    def test_multi_disc_side_f_is_rendered(self):
        template = Image.open(self.template_path).convert("RGB")
        side_f = _overlay_side_letter(template.copy(), "F")

        slot = side_f.crop((38, 54, 43, 59))
        self.assertIsNotNone(slot.getbbox())
        self.assertNotEqual(slot.tobytes(), template.crop((38, 54, 43, 59)).tobytes())

class LayoutConstraintTests(unittest.TestCase):
    def test_classic_layout_keeps_existing_63_pixel_composition(self):
        from vinylpi.core.display_layout import layout_metrics, normalize_image_config

        cfg = normalize_image_config({
            "show_cover": True,
            "show_artist": True,
            "show_title": True,
            "show_album": False,
            "top_margin": 1,
            "cover_size": 46,
            "margin_image_text": 3,
            "line_spacing_margin": 3,
            "font_size": 5,
        })

        self.assertEqual(layout_metrics(cfg)["used_height"], 63)
        self.assertTrue(layout_metrics(cfg)["fits"])
        self.assertEqual(cfg["cover_size"], 46)

    def test_large_text_automatically_reduces_cover_to_fit(self):
        from vinylpi.core.display_layout import layout_metrics, normalize_image_config

        cfg = normalize_image_config({
            "show_cover": True,
            "show_artist": True,
            "show_title": True,
            "show_album": True,
            "top_margin": 8,
            "cover_size": 60,
            "margin_image_text": 8,
            "line_spacing_margin": 8,
            "font_size": 12,
        })

        self.assertLess(cfg["cover_size"], 60)
        self.assertLessEqual(layout_metrics(cfg)["used_height"], 64)

    def test_cover_only_can_use_full_64_pixels(self):
        from vinylpi.core.display_layout import layout_metrics, normalize_image_config

        cfg = normalize_image_config({
            "show_cover": True,
            "show_artist": False,
            "show_title": False,
            "show_album": False,
            "top_margin": 0,
            "cover_size": 64,
            "font_size": 5,
        })

        self.assertEqual(cfg["cover_size"], 64)
        self.assertEqual(layout_metrics(cfg)["used_height"], 64)

    def test_empty_layout_recovers_to_title_only(self):
        from vinylpi.core.display_layout import normalize_image_config

        cfg = normalize_image_config({
            "show_cover": False,
            "show_artist": False,
            "show_title": False,
            "show_album": False,
        })

        self.assertTrue(cfg["show_title"])

    def test_renderer_adds_album_line_when_enabled(self):
        from vinylpi.core.display import _prepare_scroll_resources
        from vinylpi.config.config_loader import CONFIG_DEFAULTS

        cfg = dict(CONFIG_DEFAULTS["image"])
        cfg.update({
            "show_cover": False,
            "show_artist": True,
            "show_title": True,
            "show_album": True,
            "top_margin": 4,
            "font_size": 5,
            "line_spacing_margin": 2,
        })
        cover = Image.new("RGB", (64, 64), (80, 40, 120))

        resources = _prepare_scroll_resources(
            cover,
            "Artist",
            "Title",
            "Album",
            img_cfg=cfg,
        )

        self.assertEqual([line["key"] for line in resources["lines"]], ["artist", "title", "album"])
        last = resources["lines"][-1]
        self.assertLessEqual(last["y"] + resources["glyph_height"], 64)


class PixooUnchangedFrameTests(unittest.TestCase):
    def test_static_track_sends_only_one_frame_until_stopped(self):
        import threading
        import time
        from unittest.mock import Mock, patch
        from vinylpi.core import display

        stopped = threading.Event()
        pixoo = Mock()
        pixoo.send_frame = Mock()
        resources = {'canvas_size': 64, 'lines': [{'width': 20}]}
        config = {'debug': {'logs': False, 'pixoo_frame_path': '', 'preview_path': ''},
                  'image': {'marquee_speed': 20, 'sleep_seconds': 0.01}}
        with patch.object(display, '_scroll_stop_event', stopped), \
             patch.object(display, 'read_config', return_value=config), \
             patch.object(display, '_get_pixoo', return_value=pixoo), \
             patch.object(display, '_prepare_scroll_resources', return_value=resources), \
             patch.object(display, '_render_scroll_frame', return_value=Image.new('RGB', (64, 64))):
            thread = threading.Thread(
                target=display._scroll_loop,
                args=(Image.new('RGB', (64, 64)), 'Artist', 'Title'), daemon=True,
            )
            try:
                thread.start()
                deadline = time.monotonic() + 1
                while not pixoo.send_frame.called and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertTrue(pixoo.send_frame.called)
                time.sleep(0.1)
                self.assertEqual(pixoo.send_frame.call_count, 1)
            finally:
                stopped.set()
                thread.join(timeout=2)
            self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
