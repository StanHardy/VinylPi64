"""Local Pixoo actions for VinylPi source transitions (no HA round trip)."""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


from vinylpi.config.runtime import read_config
from vinylpi.integrations.divoom_api import PixooClient, PixooError


def _settings() -> tuple[dict, dict]:
    cfg = read_config()
    return cfg.get("pixoo_automations") or {}, cfg.get("divoom") or {}


def _client(divoom: dict) -> PixooClient:
    # A source transition must never launch a lengthy subnet discovery scan.
    # The configured IP is used directly and requests have a bounded timeout.
    if not str(divoom.get("ip") or "").strip():
        raise PixooError("Pixoo IP is not configured for local automations")
    return PixooClient(ip=str(divoom.get("ip") or "").strip(),
                       timeout=min(2.0, max(0.1, float(divoom.get("timeout", 2.0)))),
                       auto_discover=False)


def on_vinyl_started(*, reason: str = "manual") -> bool:
    settings, divoom = _settings()
    if not settings.get("turn_on_on_vinyl_start", False):
        return False
    try:
        _client(divoom).set_brightness(100)
    except Exception as exc:
        logger.warning(f"[Pixoo automation] Could not turn on Pixoo ({reason}): {exc}")
        return False
    return True


def on_recognition_stopped(*, source: str, reason: str) -> bool:
    settings, divoom = _settings()
    file_id = str(settings.get("stop_gif_file_id") or "").strip()
    if not settings.get("play_gif_on_stop", False) or not file_id:
        return False
    try:
        _client(divoom).play_remote_gif(file_id)
    except Exception as exc:
        logger.warning(f"[Pixoo automation] Could not play stop GIF ({source}/{reason}): {exc}")
        return False
    return True
