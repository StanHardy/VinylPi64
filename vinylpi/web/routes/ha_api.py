from __future__ import annotations

import hmac
import os

from flask import Blueprint, abort, jsonify, request

from vinylpi.web.services import pixoo, recognizer
from vinylpi.core.display import show_fallback_image
from vinylpi.integrations.pixoo_automations import on_recognition_stopped

bp = Blueprint("ha_api", __name__, url_prefix="/api/ha")


def require_token() -> None:
    expected = os.getenv("VINYLPI_API_TOKEN", "").strip()
    supplied = request.headers.get("X-Api-Token", "")
    if not expected or not hmac.compare_digest(supplied, expected):
        abort(401)


@bp.post("/music_mode/on")
def music_mode_on():
    require_token()
    started = recognizer.start(silence_output=True)
    return jsonify({"ok": True, "started": started})


@bp.post("/music_mode/off")
def music_mode_off():
    require_token()
    stopped = recognizer.stop()
    if stopped:
        show_fallback_image()
        on_recognition_stopped(source="vinyl", reason="manual")
    return jsonify({"ok": True, "stopped": stopped})


@bp.post("/off")
def pixoo_off():
    require_token()
    pixoo.set_brightness(0)
    return jsonify({"ok": True})


@bp.post("/on")
def pixoo_on():
    require_token()
    pixoo.set_brightness(100)
    return jsonify({"ok": True})
