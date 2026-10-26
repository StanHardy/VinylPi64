"""LAN discovery for Pixoo devices.

Manual scans enumerate a /24 subnet on demand. Automatic discovery uses the
configured scan range and is rate limited in each running process to avoid
repeated subnet scans when a device is offline.
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress
import threading
import time

import requests

from vinylpi.config.runtime import read_config

_DISCOVERY_COOLDOWN_SECONDS = 300.0
_discovery_lock = threading.Lock()
_last_auto_attempts: dict[tuple, float] = {}


def _device_info(ip: str, timeout: float) -> dict | None:
    """Probe a candidate once; avoid logging the successful per-IP probes."""
    try:
        response = requests.post(
            f"http://{ip}/post",
            json={"Command": "Channel/GetAllConf"},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        return None

    if not isinstance(data, dict) or not any(
        key in data for key in ("DeviceName", "Brightness", "SelectIndex", "error_code")
    ):
        return None
    # A non-zero Pixoo error is not proof of a usable device.
    if data.get("error_code", 0) not in (0, "0", None):
        return None
    name = str(data.get("DeviceName") or "Pixoo").strip()[:80]
    return {"ip": ip, "name": name or "Pixoo"}


def _probe_ip(ip: str, timeout: float) -> bool:
    """Backward-compatible low-level probe for an explicitly given IP."""
    return _device_info(ip, timeout) is not None


def _scan_parameters(cfg: dict, *, manual: bool) -> tuple[str, int, int, float]:
    divoom = cfg.get("divoom") or {}
    discovery = divoom.get("discovery") or {}
    prefix = str(discovery.get("subnet_prefix") or "").strip()
    # Only a single /24 may be scanned; reject malformed or remote addresses.
    try:
        network = ipaddress.ip_network(f"{prefix}0/24", strict=True)
    except ValueError as exc:
        raise ValueError("Invalid /24 subnet prefix (example: 192.168.2.)") from exc
    if not prefix.endswith(".") or str(network.network_address) != prefix + "0":
        raise ValueError("Invalid /24 subnet prefix (example: 192.168.2.)")
    if not (network.is_private or network.is_link_local):
        raise ValueError("Pixoo discovery is limited to private local networks")

    start = 1 if manual else max(1, min(254, int(discovery.get("ip_range_start", 1))))
    end = 254 if manual else max(1, min(254, int(discovery.get("ip_range_end", 254))))
    if start > end:
        start, end = end, start
    # Profiles that saved a device outside the old default .100-.199 range
    # must still be able to recover when DHCP gives that device a new IP.
    if not manual:
        saved_ip = str(divoom.get("ip") or "").strip()
        try:
            address = ipaddress.ip_address(saved_ip)
            if address in network and not start <= int(str(address).split(".")[-1]) <= end:
                start, end = 1, 254
        except ValueError:
            pass
    timeout = max(0.1, min(5.0, float(divoom.get("timeout", 2.0))))
    return prefix, start, end, timeout


def discover_pixoo_devices(*, manual: bool = False, subnet_prefix: str | None = None) -> list[dict]:
    """Enumerate LAN devices. A manual search ignores auto-discovery's switch.

    A manual search checks all usable addresses in the configured /24, even if
    the legacy automatic discovery range was limited to .100-.199.
    """
    cfg = read_config()
    discovery = (cfg.get("divoom") or {}).get("discovery") or {}
    if not manual and not discovery.get("enabled", False):
        return []

    if subnet_prefix is not None:
        if not manual:
            raise ValueError("Subnet override is only allowed for manual scans")
        cfg = {**cfg, "divoom": {**(cfg.get("divoom") or {}), "discovery": {
            **discovery, "subnet_prefix": str(subnet_prefix),
        }}}
    prefix, start, end, timeout = _scan_parameters(cfg, manual=manual)
    candidates = [f"{prefix}{host}" for host in range(start, end + 1)]
    if (cfg.get("debug") or {}).get("logs"):
        logger.info(f"Scanning for Pixoo in {prefix}{start}-{end} ({'manual' if manual else 'automatic'})")

    devices: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(24, len(candidates)), thread_name_prefix="pixoo-scan") as pool:
        futures = [pool.submit(_device_info, ip, timeout) for ip in candidates]
        for future in as_completed(futures):
            try:
                info = future.result()
                if info:
                    devices.append(info)
            except Exception:
                continue
    return sorted(devices, key=lambda item: ipaddress.ip_address(item["ip"]))


def discover_pixoo_ip() -> str | None:
    """Automatically find one device, at most once per 5 minutes/configuration.

    The lock protects concurrent web requests against launching parallel scans.
    """
    cfg = read_config()
    if not ((cfg.get("divoom") or {}).get("discovery") or {}).get("enabled", False):
        return None
    try:
        key = _scan_parameters(cfg, manual=False)
    except (ValueError, TypeError) as exc:
        logger.warning(f"invalid Pixoo discovery configuration: {exc}")
        return None
    # Includes scan settings in the key so correcting them enables a fresh scan.
    with _discovery_lock:
        now = time.monotonic()
        if now - _last_auto_attempts.get(key, float('-inf')) < _DISCOVERY_COOLDOWN_SECONDS:
            return None
        _last_auto_attempts[key] = now
        devices = discover_pixoo_devices()
    if not devices:
        logger.warning("Pixoo auto discovery found no device in the configured range.")
        return None
    if len(devices) > 1:
        logger.warning("Multiple Pixoo devices found. Select the correct one in Settings.")
        return None
    return devices[0]["ip"]
