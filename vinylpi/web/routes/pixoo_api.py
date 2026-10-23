from flask import Blueprint, jsonify, request
import ipaddress

from vinylpi.integrations.pixoo_discovery import discover_pixoo_devices, _device_info
from vinylpi.integrations.divoom_api import PixooError
from vinylpi.web.services import pixoo
from vinylpi.web.services.config import read_config, write_config
from vinylpi.config.config_loader import CONFIG_DEFAULTS

pixoo_bp = Blueprint("pixoo_api", __name__)

@pixoo_bp.get("/api/pixoo/status")
def api_pixoo_status():
    try:
        return jsonify(pixoo.get_status())
    except PixooError as e:
        return jsonify({"ok": False, "online": False, "error": str(e)}), 500

@pixoo_bp.post("/api/pixoo/brightness")
def api_pixoo_brightness():
    data = request.get_json() or {}
    value = data.get("brightness")
    if value is None:
        return jsonify({"ok": False, "error": "missing brightness"}), 400
    try:
        pixoo.set_brightness(int(value))
        return jsonify({"ok": True})
    except PixooError as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@pixoo_bp.post("/api/pixoo/reboot")
def api_pixoo_reboot():
    try:
        pixoo.reboot()
        return jsonify({"ok": True})
    except PixooError as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@pixoo_bp.post("/api/pixoo/channel")
def api_pixoo_channel():
    data = request.get_json() or {}
    ch = data.get("channel")
    if ch is None:
        return jsonify({"ok": False, "error": "missing channel"}), 400
    try:
        pixoo.set_channel(int(ch))
        return jsonify({"ok": True})
    except PixooError as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@pixoo_bp.get("/api/pixoo/liked-gifs")
def api_pixoo_liked_gifs():
    try:
        refresh = request.args.get("refresh", "").lower() in {"1", "true", "yes"}
        gifs, cached = pixoo.get_liked_gifs(page=1, refresh=refresh)
        return jsonify({"ok": True, "gifs": gifs, "cached": cached,
                        "selected_file_id": pixoo.get_selected_stop_gif()})
    except PixooError as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@pixoo_bp.post("/api/pixoo/selected-gif")
def api_selected_stop_gif():
    data = request.get_json(silent=True) or {}
    file_id = data.get("file_id")
    if not isinstance(file_id, str):
        return jsonify({"ok": False, "error": "file_id must be a string"}), 400
    pixoo.set_selected_stop_gif(file_id)
    return jsonify({"ok": True, "selected_file_id": file_id.strip()})

@pixoo_bp.post("/api/pixoo/play-remote")
def api_pixoo_play_remote():
    data = request.get_json() or {}
    file_id = data.get("file_id")
    if not file_id:
        return jsonify({"ok": False, "error": "missing file_id"}), 400
    try:
        pixoo.play_remote_gif(file_id)
        return jsonify({"ok": True})
    except PixooError as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@pixoo_bp.post("/api/pixoo/discover-and-save")
def api_pixoo_discover_and_save():
    try:
        dev = pixoo.discover_cloud_device()
    except PixooError as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    try:
        cfg = read_config()
    except Exception:
        cfg = CONFIG_DEFAULTS

    divoom_cfg = cfg.get("divoom") or {}
    if dev.get("device_private_ip"):
        divoom_cfg["ip"] = dev["device_private_ip"]
    if dev.get("device_id") is not None:
        divoom_cfg["device_id"] = dev["device_id"]
    if dev.get("device_mac"):
        divoom_cfg["device_mac"] = dev["device_mac"]

    cfg["divoom"] = divoom_cfg
    write_config(cfg)

    return jsonify({"ok": True, "device": dev, "divoom": divoom_cfg})


@pixoo_bp.post("/api/pixoo/scan")
def api_pixoo_scan():
    """An explicitly requested local network scan; does not change settings."""
    data = request.get_json(silent=True) or {}
    try:
        prefix = data.get("subnet_prefix")
        devices = discover_pixoo_devices(manual=True, subnet_prefix=prefix)
    except (ValueError, TypeError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "devices": devices})


@pixoo_bp.post("/api/pixoo/select")
def api_pixoo_select():
    """Save a user-selected Pixoo IP to the current profile immediately."""
    data = request.get_json(silent=True) or {}
    ip = data.get("ip")
    if not isinstance(ip, str):
        return jsonify({"ok": False, "error": "ip must be a string"}), 400
    ip = ip.strip()
    if ip:
        try:
            address = ipaddress.ip_address(ip)
            if address.version != 4 or not (address.is_private or address.is_link_local):
                raise ValueError("Not a private IPv4 address")
        except ValueError:
            return jsonify({"ok": False, "error": "Invalid local Pixoo IPv4 address"}), 400
        timeout = max(0.1, min(5.0, float((read_config().get("divoom") or {}).get("timeout", 2.0))))
        if _device_info(ip, timeout) is None:
            return jsonify({"ok": False, "error": "The selected IP did not respond as a Pixoo"}), 422

    write_config({"divoom": {"ip": ip}})
    return jsonify({"ok": True, "ip": ip})
