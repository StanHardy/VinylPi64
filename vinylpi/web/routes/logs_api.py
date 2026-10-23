"""Bounded, read-only diagnostics API (no journalctl or shell access)."""
from flask import Blueprint, jsonify, request

from vinylpi.logging_service import read_events, summary

logs_bp = Blueprint("logs_api", __name__)


def _bounded_int(value: str | None, default: int, maximum: int) -> int:
    try:
        return min(max(int(value), 0), maximum)
    except (TypeError, ValueError):
        return default


@logs_bp.get("/api/logs")
def api_logs():
    after = _bounded_int(request.args.get("after"), 0, 2**63 - 1)
    limit = _bounded_int(request.args.get("limit"), 150, 300)
    level = request.args.get("level", "issues").lower()
    if level not in {"issues", "errors", "warnings", "trace", "all"}:
        return jsonify({"ok": False, "error": "invalid level"}), 400
    entries = read_events(after=after, limit=max(limit, 1), level=level,
                          search=request.args.get("search", ""))
    return jsonify({"ok": True, "entries": entries})


@logs_bp.get("/api/logs/summary")
def api_logs_summary():
    since = _bounded_int(request.args.get("since"), 0, 2**63 - 1)
    return jsonify({"ok": True, **summary(since=since)})
