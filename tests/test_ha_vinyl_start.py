"""Start notification is centralized so manual and automatic paths never diverge."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from vinylpi.web.services import recognizer


class VinylStartDispatchTests(unittest.TestCase):
    def setUp(self):
        self.original_proc = recognizer._recognizer_proc
        recognizer._recognizer_proc = None

    def tearDown(self):
        recognizer._recognizer_proc = self.original_proc

    @patch("vinylpi.web.services.recognizer.on_vinyl_started")
    @patch("vinylpi.web.services.recognizer.subprocess.Popen")
    def test_manual_start_notifies_once(self, popen, send):
        popen.return_value.poll.return_value = None
        self.assertTrue(recognizer.start(silence_output=True))
        send.assert_called_once_with(reason="manual")
        self.assertFalse(recognizer.start(silence_output=True))
        send.assert_called_once_with(reason="manual")
        popen.assert_called_once()

    @patch("vinylpi.web.services.recognizer.on_vinyl_started")
    @patch("vinylpi.web.services.recognizer.subprocess.Popen")
    def test_auto_wake_start_notifies_once(self, popen, send):
        popen.return_value.poll.return_value = None
        self.assertTrue(recognizer.start(silence_output=False, start_origin="auto_wake"))
        send.assert_called_once_with(reason="auto_wake")
        self.assertEqual(popen.call_args.kwargs["env"]["VINYLPI_START_ORIGIN"], "auto_wake")

    @patch("vinylpi.web.services.recognizer.on_vinyl_started")
    @patch("vinylpi.web.services.recognizer.subprocess.Popen", side_effect=OSError("bad spawn"))
    def test_failed_spawn_does_not_notify(self, popen, send):
        with self.assertRaises(OSError):
            recognizer.start(silence_output=True)
        send.assert_not_called()

    @patch("vinylpi.web.services.recognizer.on_vinyl_started", return_value=False)
    @patch("vinylpi.web.services.recognizer.subprocess.Popen")
    def test_offline_pixoo_does_not_block_start(self, popen, send):
        popen.return_value.poll.return_value = None
        self.assertTrue(recognizer.start(silence_output=True))
        send.assert_called_once_with(reason="manual")


if __name__ == "__main__":
    unittest.main()
