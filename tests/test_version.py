"""Regression tests for the Git-based About page version indicator."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from vinylpi.version import _version_for_root


class VersionInfoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test User")
        (self.repo / "vinylpi").mkdir()
        (self.repo / "data").mkdir()
        (self.repo / "vinylpi" / "main.py").write_text("print('one')")
        (self.repo / "data" / "config.json").write_text('{"test": 1}')
        self._git("add", ".")
        self._git("commit", "-qm", "Initial version")
        self._git("tag", "v0.8")

    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True).stdout.strip()

    def test_exact_tag_is_release(self):
        info = _version_for_root(self.repo)
        self.assertEqual(info["display"], "v0.8")
        self.assertEqual(info["status"], "Release")
        self.assertIn("/tree/v0.8", info["tag_url"])

    def test_new_commit_is_development_not_stale_release(self):
        (self.repo / "vinylpi" / "main.py").write_text("print('two')")
        self._git("add", "vinylpi/main.py")
        self._git("commit", "-qm", "Feature")
        info = _version_for_root(self.repo)
        self.assertEqual(info["status"], "Development build")
        self.assertIn("v0.8-1-g", info["display"])
        self.assertIn("1 commit after v0.8", info["detail"])

    def test_uncommitted_code_is_development(self):
        (self.repo / "vinylpi" / "main.py").write_text("print('changed')")
        info = _version_for_root(self.repo)
        self.assertEqual(info["status"], "Development build")
        self.assertIn("Local source changes", info["detail"])

    def test_runtime_config_edits_do_not_mark_code_as_modified(self):
        (self.repo / "data" / "config.json").write_text('{"test": 2}')
        info = _version_for_root(self.repo)
        self.assertEqual(info["status"], "Release")

    def test_future_semver_tags_are_supported(self):
        (self.repo / "vinylpi" / "main.py").write_text("print('next release')")
        self._git("add", "vinylpi/main.py")
        self._git("commit", "-qm", "Next release")
        self._git("tag", "v0.9.0")
        info = _version_for_root(self.repo)
        self.assertEqual(info["status"], "Release")
        self.assertEqual(info["display"], "v0.9.0")

    def test_checkout_older_tag(self):
        (self.repo / "vinylpi" / "main.py").write_text("print('next')")
        self._git("add", "vinylpi/main.py")
        self._git("commit", "-qm", "Next release")
        self._git("tag", "v0.9.0")
        self.assertEqual(_version_for_root(self.repo)["display"], "v0.9.0")
        self._git("switch", "--detach", "v0.8")
        self.assertEqual(_version_for_root(self.repo)["display"], "v0.8")

    def test_outside_git_checkout_fails_gracefully(self):
        with tempfile.TemporaryDirectory() as folder:
            info = _version_for_root(Path(folder))
        self.assertEqual(info["status"], "Unknown build")
        self.assertEqual(info["display"], "Unavailable")


if __name__ == "__main__":
    unittest.main()
