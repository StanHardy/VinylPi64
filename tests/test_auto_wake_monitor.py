from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from vinylpi.core.audio_wake import AudioWakeEvent
from vinylpi.web.services import auto_wake_monitor


class AutoWakeMonitorTests(unittest.TestCase):
    def setUp(self):
        auto_wake_monitor._stop_event = threading.Event()
        auto_wake_monitor._pause_event.clear()
        auto_wake_monitor._monitoring_event.clear()

    @patch("vinylpi.web.services.source.set_mode")
    @patch("vinylpi.web.services.source.get_mode", return_value="off")
    @patch("vinylpi.web.services.auto_wake_monitor._wait_for_wake")
    @patch("vinylpi.web.services.auto_wake_monitor.read_config")
    def test_off_mode_audio_event_switches_dashboard_to_vinyl(
        self,
        read_config,
        wait_for_wake,
        _get_mode,
        set_mode,
    ):
        read_config.return_value = {
            "behavior": {
                "vinyl_auto_wake_enabled": True,
                "vinyl_auto_wake_threshold_dbfs": -70,
                "vinyl_auto_wake_confirm_seconds": 2,
            }
        }
        wait_for_wake.return_value = AudioWakeEvent(level_dbfs=-65.2, threshold_dbfs=-70.0)

        def stop_after_switch(*_args, **_kwargs):
            auto_wake_monitor._stop_event.set()

        set_mode.side_effect = stop_after_switch
        auto_wake_monitor._run()

        set_mode.assert_called_once_with("vinyl", _from_auto_wake=True)

    @patch("vinylpi.web.services.auto_wake_monitor.send_auto_wake")
    @patch("vinylpi.web.services.source.set_mode", return_value={"mode": "vinyl"})
    @patch("vinylpi.web.services.source.get_mode", return_value="off")
    @patch("vinylpi.web.services.auto_wake_monitor._wait_for_wake")
    @patch("vinylpi.web.services.auto_wake_monitor.read_config")
    def test_successful_auto_wake_sends_once_to_ha(
        self, read_config, wait_for_wake, _get_mode, set_mode, send_auto_wake,
    ):
        read_config.return_value = {"behavior": {"vinyl_auto_wake_enabled": True}}
        wait_for_wake.return_value = AudioWakeEvent(level_dbfs=-65.2, threshold_dbfs=-70.0)
        send_auto_wake.side_effect = auto_wake_monitor._stop_event.set
        auto_wake_monitor._run()
        set_mode.assert_called_once_with("vinyl", _from_auto_wake=True)
        send_auto_wake.assert_called_once_with()

    @patch("vinylpi.web.services.auto_wake_monitor.send_auto_wake")
    @patch("vinylpi.web.services.source.set_mode", return_value={"mode": "off"})
    @patch("vinylpi.web.services.source.get_mode", return_value="off")
    @patch("vinylpi.web.services.auto_wake_monitor._wait_for_wake")
    @patch("vinylpi.web.services.auto_wake_monitor.read_config")
    def test_failed_switch_does_not_send_ha(
        self, read_config, wait_for_wake, _get_mode, set_mode, send_auto_wake,
    ):
        read_config.return_value = {"behavior": {"vinyl_auto_wake_enabled": True}}
        wait_for_wake.return_value = AudioWakeEvent(level_dbfs=-65.2, threshold_dbfs=-70.0)

        def end_loop(*args, **kwargs):
            auto_wake_monitor._stop_event.set()
            return {"mode": "off"}

        set_mode.side_effect = end_loop
        auto_wake_monitor._run()
        send_auto_wake.assert_not_called()

    @patch("vinylpi.web.services.source.get_mode", return_value="off")
    @patch("vinylpi.web.services.auto_wake_monitor._wait_for_wake")
    @patch("vinylpi.web.services.auto_wake_monitor.read_config")
    def test_disabled_auto_wake_does_not_open_audio_monitor(
        self,
        read_config,
        wait_for_wake,
        _get_mode,
    ):
        read_config.return_value = {
            "behavior": {"vinyl_auto_wake_enabled": False}
        }

        # Stop after the first idle wait rather than allowing the service loop
        # to run forever in the unit test.
        original_wait = auto_wake_monitor._stop_event.wait

        def stop_wait(timeout=None):
            auto_wake_monitor._stop_event.set()
            return original_wait(0)

        with patch.object(auto_wake_monitor._stop_event, "wait", side_effect=stop_wait):
            auto_wake_monitor._run()

        wait_for_wake.assert_not_called()


if __name__ == "__main__":
    unittest.main()
