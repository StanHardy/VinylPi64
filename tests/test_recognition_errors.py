"""Recognition failure behavior: transient outages vs. unexpected exceptions."""
from __future__ import annotations

import unittest
from unittest.mock import patch, sentinel

from aiohttp import ClientConnectionError

from vinylpi.core import recognition


class RecognitionErrorTests(unittest.TestCase):
    @patch.object(recognition.logger, "exception")
    @patch.object(recognition.logger, "warning")
    @patch.object(recognition, "_recognize", side_effect=TimeoutError("Shazam request timed out"))
    def test_timeout_is_recoverable(self, recognize, warning, exception):
        self.assertIsNone(recognition.recognize_song(b"sample"))
        warning.assert_called_once()
        self.assertIn("timed out", warning.call_args.args[0])
        exception.assert_not_called()

    @patch.object(recognition.logger, "exception")
    @patch.object(recognition.logger, "warning")
    @patch.object(recognition, "_recognize", side_effect=ClientConnectionError("Connection lost"))
    def test_network_failure_is_recoverable(self, recognize, warning, exception):
        self.assertIsNone(recognition.recognize_song(b"sample"))
        warning.assert_called_once()
        self.assertIn("network", warning.call_args.args[0])
        exception.assert_not_called()

    @patch.object(recognition.logger, "exception")
    @patch.object(recognition.logger, "warning")
    @patch.object(recognition, "_recognize", side_effect=ValueError("Unexpected result"))
    def test_unexpected_failure_remains_diagnostic(self, recognize, warning, exception):
        self.assertIsNone(recognition.recognize_song(b"sample"))
        warning.assert_not_called()
        exception.assert_called_once()

    @patch.object(recognition.logger, "exception")
    @patch.object(recognition.logger, "warning")
    @patch.object(recognition, "_recognize", return_value=sentinel.track)
    def test_success_is_unaffected(self, recognize, warning, exception):
        self.assertIs(recognition.recognize_song(b"sample"), sentinel.track)
        warning.assert_not_called()
        exception.assert_not_called()


if __name__ == "__main__":
    unittest.main()
