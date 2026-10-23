from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import werkzeug  # noqa: F401
except ModuleNotFoundError:
    werkzeug = None

if werkzeug is not None:
    from vinylpi.web.services.uploads import _safe_child_path, _safe_upload_path, delete_fallback_image, build_font_preview


@unittest.skipIf(werkzeug is None, "Werkzeug is not installed")
class UploadPathTests(unittest.TestCase):
    def test_safe_child_path_allows_spaces_but_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            expected = directory / "Pixel Millennium.ttf"
            expected.touch()

            self.assertEqual(_safe_child_path(directory, expected.name), expected.resolve())
            self.assertIsNone(_safe_child_path(directory, "../Pixel Millennium.ttf"))
            self.assertIsNone(_safe_child_path(directory, "subdir/font.ttf"))

    def test_upload_paths_restrict_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "uploads"
            normal, turn = base / "normal", base / "turn"
            normal.mkdir(parents=True)
            turn.mkdir(parents=True)
            with patch("vinylpi.web.services.uploads.UPLOAD_DIR", base), \
                 patch("vinylpi.web.services.uploads.FALLBACK_NORMAL_DIR", normal), \
                 patch("vinylpi.web.services.uploads.FALLBACK_TURN_DIR", turn):
                self.assertEqual(_safe_upload_path("turn/image.png"), (turn / "image.png").resolve())
                self.assertEqual(_safe_upload_path("normal/image.png"), (normal / "image.png").resolve())
                for unsafe in ("../../secret.txt", "turn/../secret.txt", "wrong/image.png",
                               "turn/subfolder/file.png", "/etc/passwd", "normal\\..\\secret.txt"):
                    with self.subTest(unsafe=unsafe):
                        self.assertIsNone(_safe_upload_path(unsafe))

    def test_delete_rejects_escape_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "uploads"
            base.mkdir()
            outside = Path(tmp) / "important.json"
            outside.write_text("keep", encoding="utf-8")
            with patch("vinylpi.web.services.uploads.UPLOAD_DIR", base):
                self.assertFalse(delete_fallback_image("../important.json"))
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_symlink_cannot_escape_upload_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "uploads"
            base.mkdir()
            outside = Path(tmp) / "private.png"
            outside.write_bytes(b"private")
            try:
                (base / "alias.png").symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks not available")
            with patch("vinylpi.web.services.uploads.UPLOAD_DIR", base):
                self.assertIsNone(_safe_upload_path("alias.png"))
                self.assertFalse(delete_fallback_image("alias.png"))
            self.assertTrue(outside.exists())

    def test_font_preview_supports_existing_font_filename_with_spaces(self):
        source = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "Pixel5.ttf"
        self.assertTrue(source.is_file())

        with tempfile.TemporaryDirectory() as tmp:
            fonts_dir = Path(tmp)
            target = fonts_dir / "Pixel Millennium.ttf"
            shutil.copyfile(source, target)

            with patch("vinylpi.web.services.uploads.FONTS_DIR", fonts_dir):
                preview = build_font_preview(target.name)

            self.assertIsNotNone(preview)
            self.assertTrue(preview.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()
