from __future__ import annotations

import json
from pathlib import Path

from vinylpi.config.runtime import read_config, write_config
from vinylpi.integrations.divoom_api import PixooClient, PixooError
from vinylpi.paths import DATA_DIR

_CACHE_PATH = DATA_DIR / "pixoo_liked_gifs.json"


def _device_key(cfg: dict) -> str:
    d = cfg.get("divoom") or {}
    return f"{d.get('device_id') or ''}|{str(d.get('device_mac') or '').lower()}"


def _cached_likes() -> list[dict] | None:
    try:
        payload = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        if payload.get("device_key") != _device_key(read_config()):
            return None
        gifs = payload.get("gifs")
        return gifs if isinstance(gifs, list) else None
    except (OSError, ValueError, AttributeError):
        return None


def _save_likes(gifs: list[dict]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"device_key": _device_key(read_config()), "gifs": gifs}), encoding="utf-8")
    tmp.replace(_CACHE_PATH)


def get_client():
    try:
        return PixooClient()
    except PixooError:
        return None


def get_status():
    client = get_client()
    if not client:
        return {"ok": False, "online": False, "error": "Pixoo not reachable"}
    conf = client.get_all_conf()
    return {
        "ok": True, "online": True,
        "brightness": conf.get("Brightness"),
        "channel": conf.get("SelectIndex"),
        "device_name": conf.get("DeviceName") or "Pixoo",
        "raw": conf,
    }


def set_brightness(value: int):
    client = get_client()
    if not client:
        raise PixooError("Pixoo not reachable")
    client.set_brightness(int(value))


def reboot():
    PixooClient().reboot()


def set_channel(channel: int):
    client = get_client()
    if not client:
        raise PixooError("Pixoo not reachable")
    client.set_channel(int(channel))


def get_liked_gifs(page: int = 1, *, refresh: bool = False) -> tuple[list[dict], bool]:
    """Return cached likes first; refresh only on demand or an empty cache."""
    cached = _cached_likes()
    if cached is not None and not refresh:
        return cached, True
    try:
        # Divoom likes are a cloud operation: do not probe the LAN or run
        # IP discovery simply to populate a settings dropdown.
        gifs = PixooClient(auto_discover=False).get_liked_gifs(page=page)
        _save_likes(gifs)
        return gifs, False
    except Exception:
        if cached is not None:
            return cached, True
        raise


def get_selected_stop_gif() -> str:
    return str((read_config().get("pixoo_automations") or {}).get("stop_gif_file_id") or "")


def set_selected_stop_gif(file_id: str) -> None:
    write_config({"pixoo_automations": {"stop_gif_file_id": str(file_id).strip()}})


def play_remote_gif(file_id: str):
    client = get_client()
    if not client:
        raise PixooError("Pixoo not reachable")
    client.play_remote_gif(file_id)


def discover_cloud_device():
    return PixooClient(auto_discover=False).discover_cloud_device()
