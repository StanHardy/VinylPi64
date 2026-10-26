from __future__ import annotations

import unittest
from unittest.mock import patch

try:
    from vinylpi.core import audio_capture
except ModuleNotFoundError as exc:
    if exc.name == "sounddevice":
        audio_capture = None
    else:
        raise

try:
    from flask import Flask
except ModuleNotFoundError:
    Flask = None

if Flask is not None and audio_capture is not None:
    from vinylpi.web.routes.config_api import config_bp


@unittest.skipIf(audio_capture is None, "sounddevice is not installed")
class AudioDeviceDiscoveryTests(unittest.TestCase):
    def setUp(self):
        audio_capture.invalidate_audio_device()
        self.addCleanup(audio_capture.invalidate_audio_device)


    @patch("vinylpi.core.audio_capture._default_input_index", return_value=1)
    @patch("vinylpi.core.audio_capture.sd.query_hostapis")
    @patch("vinylpi.core.audio_capture.sd.query_devices")
    def test_list_audio_input_devices_hides_alsa_aliases_and_marks_default(
        self,
        query_devices,
        query_hostapis,
        _default_input,
    ):
        query_devices.return_value = [
            {
                "name": "sysdefault",
                "max_input_channels": 2,
                "max_output_channels": 2,
                "hostapi": 0,
            },
            {
                "name": "USB AUDIO CODEC: Audio (hw:0,0)",
                "max_input_channels": 1,
                "max_output_channels": 2,
                "hostapi": 0,
            },
            {
                "name": "spdif",
                "max_input_channels": 2,
                "max_output_channels": 2,
                "hostapi": 0,
            },
            {
                "name": "default",
                "max_input_channels": 2,
                "max_output_channels": 2,
                "hostapi": 0,
            },
            {
                "name": "Microphone",
                "max_input_channels": 2,
                "max_output_channels": 0,
                "hostapi": 1,
            },
        ]
        query_hostapis.return_value = [{"name": "ALSA"}, {"name": "PulseAudio"}]

        devices = audio_capture.list_audio_input_devices()

        self.assertEqual(
            [item["name"] for item in devices],
            ["USB AUDIO CODEC: Audio (hw:0,0)", "Microphone"],
        )
        self.assertEqual(devices[0]["display_name"], "USB AUDIO CODEC")
        self.assertTrue(devices[0]["is_default"])
        self.assertFalse(devices[0]["is_virtual"])
        self.assertEqual(devices[0]["hostapi"], "ALSA")

    @patch("vinylpi.core.audio_capture._default_input_index", return_value=0)
    @patch("vinylpi.core.audio_capture.sd.query_hostapis", return_value=[{"name": "ALSA"}])
    @patch("vinylpi.core.audio_capture.sd.query_devices")
    def test_virtual_inputs_remain_available_as_fallback_when_no_hardware_is_exposed(
        self,
        query_devices,
        _query_hostapis,
        _default_input,
    ):
        query_devices.return_value = [
            {"name": "default", "max_input_channels": 2, "hostapi": 0},
            {"name": "pulse", "max_input_channels": 2, "hostapi": 0},
        ]

        devices = audio_capture.list_audio_input_devices()

        self.assertEqual([item["name"] for item in devices], ["default", "pulse"])
        self.assertTrue(all(item["is_virtual"] for item in devices))

    @patch("vinylpi.core.audio_capture._query_audio_input_devices")
    @patch("vinylpi.core.audio_capture.read_config")
    def test_selected_full_name_is_preferred(self, read_config, list_devices):
        read_config.return_value = {
            "audio": {"device_name_contains": "Microphone"},
            "debug": {"logs": False},
        }
        list_devices.return_value = [
            {"index": 2, "name": "USB Audio Interface", "is_default": True, "is_virtual": False},
            {"index": 5, "name": "Microphone", "is_default": False, "is_virtual": False},
        ]

        self.assertEqual(audio_capture.auto_detect_usb_device(), 5)

    @patch("vinylpi.core.audio_capture._query_audio_input_devices")
    @patch("vinylpi.core.audio_capture.read_config")
    def test_legacy_substring_still_matches(self, read_config, list_devices):
        read_config.return_value = {
            "audio": {"device_name_contains": "USB Audio"},
            "debug": {"logs": False},
        }
        list_devices.return_value = [
            {"index": 3, "name": "USB Audio Interface", "is_default": False, "is_virtual": False},
        ]

        self.assertEqual(audio_capture.auto_detect_usb_device(), 3)

    @patch("vinylpi.core.audio_capture._query_audio_input_devices")
    @patch("vinylpi.core.audio_capture.read_config")
    def test_empty_selection_uses_default_then_first(self, read_config, list_devices):
        read_config.return_value = {
            "audio": {"device_name_contains": ""},
            "debug": {"logs": False},
        }
        list_devices.return_value = [
            {"index": 1, "name": "Input A", "is_default": False, "is_virtual": False},
            {"index": 4, "name": "Input B", "is_default": True, "is_virtual": False},
        ]
        self.assertEqual(audio_capture.auto_detect_usb_device(), 4)

        audio_capture.invalidate_audio_device()  # Simulate a new worker start.
        list_devices.return_value = [
            {"index": 1, "name": "Input A", "is_default": False, "is_virtual": False},
            {"index": 4, "name": "Input B", "is_default": False, "is_virtual": False},
        ]
        self.assertEqual(audio_capture.auto_detect_usb_device(), 1)

    @patch("vinylpi.core.audio_capture._query_audio_input_devices")
    @patch("vinylpi.core.audio_capture.read_config")
    def test_device_resolved_once_until_selection_changes(self, read_config, query):
        read_config.return_value = {"audio": {"device_name_contains": "USB"}}
        query.return_value = [
            {"index": 0, "name": "USB Interface", "is_default": True, "is_virtual": False},
            {"index": 1, "name": "Other", "is_default": False, "is_virtual": False},
        ]
        for _ in range(20):
            self.assertEqual(audio_capture.auto_detect_usb_device(), 0)
        query.assert_called_once()
        read_config.return_value = {"audio": {"device_name_contains": "Other"}}
        self.assertEqual(audio_capture.auto_detect_usb_device(), 1)
        self.assertEqual(query.call_count, 2)

    @patch("vinylpi.core.audio_capture.sf.write")
    @patch("vinylpi.core.audio_capture.sd.rec", side_effect=RuntimeError("device unplugged"))
    @patch("vinylpi.core.audio_capture._query_audio_input_devices")
    @patch("vinylpi.core.audio_capture.read_config")
    def test_recording_failure_invalidates_cached_device(self, read_config, query, _record, _write):
        read_config.return_value = {
            "audio": {"device_name_contains": "", "sample_rate": 44100,
                      "sample_seconds": 3, "channels": 1},
            "debug": {"logs": False, "wav_path": ""},
        }
        query.return_value = [{"index": 0, "name": "USB", "is_default": True, "is_virtual": False}]
        self.assertEqual(audio_capture.auto_detect_usb_device(), 0)
        self.assertIsNone(audio_capture.record_sample())
        self.assertEqual(audio_capture.auto_detect_usb_device(), 0)
        self.assertEqual(query.call_count, 2)  # Retry discovery after device failure.

    @patch("vinylpi.core.audio_capture.sf.write")
    @patch("vinylpi.core.audio_capture.sd.wait")
    @patch("vinylpi.core.audio_capture.sd.rec")
    @patch("vinylpi.core.audio_capture.auto_detect_usb_device", return_value=7)
    @patch("vinylpi.core.audio_capture.read_config")
    def test_record_sample_uses_resolved_device(
        self,
        read_config,
        _resolve_device,
        record,
        _wait,
        _sf_write,
    ):
        read_config.return_value = {
            "audio": {"sample_rate": 44100, "sample_seconds": 3, "channels": 1},
            "debug": {"logs": False, "wav_path": ""},
        }
        record.return_value = object()

        audio_capture.record_sample()

        self.assertEqual(record.call_args.kwargs["device"], 7)
        self.assertEqual(record.call_args.kwargs["samplerate"], 44100)
        self.assertEqual(record.call_args.kwargs["channels"], 1)



@unittest.skipIf(Flask is None or audio_capture is None, "Flask/sounddevice is not installed")
class AudioDeviceApiTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.config.update(TESTING=True)
        app.register_blueprint(config_bp)
        self.client = app.test_client()

    @patch("vinylpi.web.routes.config_api.list_audio_input_devices")
    def test_audio_devices_endpoint_returns_capture_devices(self, list_devices):
        list_devices.return_value = [
            {
                "index": 2,
                "name": "USB Audio Interface",
                "max_input_channels": 1,
                "hostapi": "ALSA",
                "is_default": True,
            }
        ]

        response = self.client.get("/api/audio-devices")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["devices"][0]["name"], "USB Audio Interface")
        self.assertTrue(payload["devices"][0]["is_default"])

    @patch("vinylpi.web.routes.config_api.list_audio_input_devices", side_effect=RuntimeError("PortAudio unavailable"))
    def test_audio_devices_endpoint_reports_scan_failure(self, _list_devices):
        response = self.client.get("/api/audio-devices")

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["devices"], [])


if __name__ == "__main__":
    unittest.main()
