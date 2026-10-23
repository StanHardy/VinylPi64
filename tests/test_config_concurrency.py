"""Concurrency regression tests for profile configuration writes."""
from __future__ import annotations

import json
import multiprocessing
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from vinylpi.config import runtime


def _process_write_profile(path: str, index: int, start, count: int) -> None:
    """Separate processes must coordinate on the same persistent lock file."""
    runtime.CONFIG_PATH = Path(path)
    runtime.clear_config_cache()
    if not start.wait(timeout=10):
        raise RuntimeError("Concurrent writers did not start")
    for iteration in range(count):
        runtime.write_config({"process_updates": {f"worker_{index}": iteration}})


class ConfigWriteConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "profiles" / "default" / "config.json"
        patcher = patch.object(runtime, "CONFIG_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        runtime.clear_config_cache()
        self.addCleanup(runtime.clear_config_cache)

    def test_parallel_thread_updates_are_not_lost(self) -> None:
        count = 16
        start = threading.Barrier(count)
        errors: list[Exception] = []
        original_load = runtime.load_config

        def slow_read(path):
            cfg = original_load(path)
            time.sleep(0.005)  # Force overlapping read/merge attempts without a lock.
            return cfg

        def writer(index: int) -> None:
            try:
                start.wait(timeout=10)
                runtime.write_config({"thread_updates": {f"worker_{index}": index}})
            except Exception as exc:
                errors.append(exc)

        with patch.object(runtime, "load_config", side_effect=slow_read):
            threads = [threading.Thread(target=writer, args=(index,)) for index in range(count)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
        self.assertFalse(errors)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        final = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(
            final["thread_updates"],
            {f"worker_{i}": i for i in range(count)},
        )
        self.assertEqual(list(self.path.parent.glob(".config.json.*.tmp")), [])

    def test_multiple_processes_preserve_all_updates(self) -> None:
        # Pi Zero worker and Flask web process may write the same profile.
        # Spawn also verifies the lock works between independent interpreters.
        ctx = multiprocessing.get_context("spawn")
        start = ctx.Event()
        workers = [
            ctx.Process(target=_process_write_profile, args=(str(self.path), i, start, 15))
            for i in range(5)
        ]
        for worker in workers:
            worker.start()
        start.set()
        for worker in workers:
            worker.join(timeout=15)
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
            self.assertEqual(worker.exitcode, 0)
        final = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(
            final["process_updates"],
            {f"worker_{i}": 14 for i in range(5)},
        )

    def test_failed_replace_keeps_old_config_and_removes_temp_file(self) -> None:
        runtime.write_config({"audio": {"channels": 2}})
        original = self.path.read_bytes()
        with patch.object(runtime.os, "replace", side_effect=OSError("injected failure")):
            with self.assertRaisesRegex(OSError, "injected failure"):
                runtime.write_config({"audio": {"channels": 1}})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob(".config.json.*.tmp")), [])

    def test_fallback_uses_partial_update(self) -> None:
        runtime.write_config({"pixoo_automations": {"stop_gif_file_id": "1234"}})
        runtime.set_fallback_image_path("assets/fallback/new.png")
        final = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(final["pixoo_automations"]["stop_gif_file_id"], "1234")
        self.assertEqual(final["fallback"]["image_path"], "assets/fallback/new.png")


if __name__ == "__main__":
    unittest.main()
