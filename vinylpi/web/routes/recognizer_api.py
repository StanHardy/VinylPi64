from flask import Blueprint, jsonify
from vinylpi.web.services.recognizer import is_running, start, stop
from vinylpi.web.services.config import read_config
from vinylpi.core.display import show_fallback_image
from vinylpi.integrations.pixoo_automations import on_recognition_stopped

recognizer_bp = Blueprint("recognizer_api", __name__)

@recognizer_bp.get("/api/recognizer/status")
def api_recognizer_status():
    return jsonify({"running": is_running()})

@recognizer_bp.post("/api/recognizer/start")
def api_recognizer_start():
    cfg = read_config()
    debug_log = (cfg.get("debug") or {}).get("logs", False)
    started = start(silence_output=not debug_log)
    return jsonify({"ok": True, "started": started, "running": is_running()})

@recognizer_bp.post("/api/recognizer/stop")
def api_recognizer_stop():
    stopped = stop()
    if stopped:
        show_fallback_image()
        on_recognition_stopped(source="vinyl", reason="manual")
    return jsonify({"ok": True, "stopped": stopped, "running": is_running()})
