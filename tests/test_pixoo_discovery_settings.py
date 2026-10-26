"""Regression tests for explicit Pixoo selection and lazy discovery."""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import requests

from vinylpi.integrations.divoom_api import PixooClient, PixooError
from vinylpi.integrations import pixoo_discovery


class PixooSelectionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "debug": {"logs": True},
            "divoom": {
                "ip": "192.168.2.41", "timeout": 0.5,
                "discovery": {"enabled": True, "subnet_prefix": "192.168.2.",
                              "ip_range_start": 100, "ip_range_end": 199},
            },
        }

    @patch("vinylpi.integrations.divoom_api.requests.post")
    @patch("vinylpi.integrations.divoom_api.discover_pixoo_ip")
    @patch("vinylpi.integrations.divoom_api.read_config")
    def test_saved_ip_skips_probe_and_scan(self, read, discover, post):
        read.return_value = self.cfg
        client = PixooClient()
        self.assertEqual(client.ip, "192.168.2.41")
        discover.assert_not_called()
        post.assert_not_called()

    @patch("vinylpi.integrations.divoom_api.write_config")
    @patch("vinylpi.integrations.divoom_api.discover_pixoo_ip", return_value="192.168.2.52")
    @patch("vinylpi.integrations.divoom_api.requests.post")
    @patch("vinylpi.integrations.divoom_api.read_config")
    def test_failed_connection_triggers_one_discovery_and_retry(self, read, post, discover, write):
        read.return_value = self.cfg
        good = Mock()
        good.raise_for_status.return_value = None
        good.json.return_value = {"Brightness": 20}
        post.side_effect = [requests.ConnectionError("no route"), good]
        client = PixooClient()
        self.assertEqual(client.get_all_conf(), {"Brightness": 20})
        self.assertEqual(client.ip, "192.168.2.52")
        self.assertEqual(post.call_count, 2)
        discover.assert_called_once()
        write.assert_called_once_with({"divoom": {"ip": "192.168.2.52"}})

    @patch("vinylpi.integrations.divoom_api.discover_pixoo_ip")
    @patch("vinylpi.integrations.divoom_api.requests.post")
    @patch("vinylpi.integrations.divoom_api.read_config")
    def test_http_failure_does_not_trigger_network_scan(self, read, post, discover):
        read.return_value = self.cfg
        post.return_value.raise_for_status.side_effect = requests.HTTPError("503")
        with self.assertRaises(PixooError):
            PixooClient().get_all_conf()
        discover.assert_not_called()

    @patch("vinylpi.integrations.divoom_api.discover_pixoo_ip")
    @patch("vinylpi.integrations.divoom_api.read_config")
    def test_cloud_client_never_discovers(self, read, discover):
        read.return_value = {**self.cfg, "divoom": {**self.cfg["divoom"], "ip": ""}}
        client = PixooClient(auto_discover=False)
        self.assertFalse(client.ip)
        discover.assert_not_called()

    @patch("vinylpi.integrations.pixoo_discovery.read_config")
    @patch("vinylpi.integrations.pixoo_discovery._device_info")
    def test_manual_scan_includes_addresses_outside_legacy_range(self, device_info, read):
        read.return_value = self.cfg
        device_info.side_effect = lambda ip, _timeout: (
            {"ip": ip, "name": "Pixoo"} if ip in {"192.168.2.41", "192.168.2.151"} else None
        )
        devices = pixoo_discovery.discover_pixoo_devices(manual=True)
        self.assertEqual([d["ip"] for d in devices], ["192.168.2.41", "192.168.2.151"])
        self.assertEqual(device_info.call_count, 254)

    @patch("vinylpi.integrations.pixoo_discovery.discover_pixoo_devices", return_value=[])
    @patch("vinylpi.integrations.pixoo_discovery.time.monotonic", side_effect=[100., 101.])
    @patch("vinylpi.integrations.pixoo_discovery.read_config")
    def test_auto_scan_is_throttled(self, read, monotonic, scan):
        read.return_value = self.cfg
        pixoo_discovery._last_auto_attempts.clear()
        self.assertIsNone(pixoo_discovery.discover_pixoo_ip())
        self.assertIsNone(pixoo_discovery.discover_pixoo_ip())
        scan.assert_called_once_with()

    @patch("vinylpi.integrations.pixoo_discovery.read_config")
    def test_legacy_range_expands_when_saved_ip_outside_range(self, read):
        read.return_value = self.cfg
        prefix, start, end, _ = pixoo_discovery._scan_parameters(self.cfg, manual=False)
        self.assertEqual((prefix, start, end), ("192.168.2.", 1, 254))

    @patch("vinylpi.integrations.pixoo_discovery.read_config")
    def test_public_subnet_rejected(self, read):
        read.return_value = self.cfg
        with self.assertRaises(ValueError):
            pixoo_discovery.discover_pixoo_devices(manual=True, subnet_prefix="8.8.8.")

    @patch("vinylpi.integrations.divoom_api.discover_pixoo_ip", return_value=None)
    @patch("vinylpi.integrations.divoom_api.read_config")
    def test_new_profile_without_pixoo_has_readable_error(self, read, discover):
        read.return_value = {**self.cfg, "divoom": {**self.cfg["divoom"], "ip": ""}}
        with self.assertRaisesRegex(PixooError, "Pixoo not found on the local network"):
            PixooClient()
        discover.assert_called_once()

    @patch("vinylpi.integrations.divoom_api.discover_pixoo_ip")
    @patch("vinylpi.integrations.divoom_api.read_config")
    def test_no_device_and_disabled_discovery_is_not_configured(self, read, discover):
        read.return_value = {**self.cfg, "divoom": {**self.cfg["divoom"],
            "ip": "", "discovery": {"enabled": False}}}
        with self.assertRaisesRegex(PixooError, "Pixoo not configured"):
            PixooClient()
        discover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
