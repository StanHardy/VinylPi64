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


def on_playback_started(*, source: str, reason: str = "manual") -> bool:
    settings, divoom = _settings()
    enabled = settings.get("turn_on_on_start")
    if enabled is None:  # Pre-migration config compatibility.
        enabled = settings.get("turn_on_on_vinyl_start", False)
    if not enabled:
        return False
    try:
        _client(divoom).set_brightness(100)
    except Exception as exc:
        logger.warning("[Pixoo automation] Could not turn on Pixoo (%s/%s): %s", source, reason, exc)
        return False
    return True


def on_vinyl_started(*, reason: str = "manual") -> bool:
    """Compatibility entry point for the Vinyl recognizer."""
    return on_playback_started(source="vinyl", reason=reason)


def on_recognition_stopped(*, source: str, reason: str) -> bool:
    settings, divoom = _settings()
    action = settings.get("off_action", "none")
    # Compatibility when called with a pre-migration config dictionary.
    if "off_action" not in settings and settings.get("play_gif_on_stop"):
        action = "community_gif"
    if action == "none":
        return False
    file_id = str(settings.get("stop_gif_file_id") or "").strip()
    if action == "community_gif" and not file_id:
        logger.warning("[Pixoo automation] No community GIF is selected")
        return False
    if action not in ("community_gif", "brightness_off"):
        logger.warning("[Pixoo automation] Invalid turn-off action: %s", action)
        return False
    try:
        pixoo = _client(divoom)
        if action == "community_gif":
            pixoo.play_remote_gif(file_id)
        else:
            pixoo.set_brightness(0)
    except Exception as exc:
        logger.warning("[Pixoo automation] Could not run turn-off action (%s/%s): %s", source, reason, exc)
        return False
    return True
