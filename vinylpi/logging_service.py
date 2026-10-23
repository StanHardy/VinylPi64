"""Small, process-safe log store shared by the web, Vinyl and Spotify workers.

Legacy print() calls remain visible in journald, while their useful messages are
recorded in an independent, bounded SQLite database. No journalctl/sudo access is
needed and debug messages may be disabled without losing warnings/errors.
"""
from __future__ import annotations

import builtins
import logging
import os
import re
import sqlite3
import sys
import threading
import time
import traceback
from pathlib import Path

from vinylpi.paths import DATA_DIR

LOG_PATH = DATA_DIR / "logs.db"
MAX_EVENTS = 10000
_LEVELS = {"TRACE", "INFO", "WARNING", "ERROR"}
_local = threading.local()
_original_print = builtins.print
_installed = False
_install_lock = threading.Lock()
_source = "App"
_recent_issues: dict[tuple[str, str, str], float] = {}
_recent_lock = threading.Lock()

_REQUEST_LINE = re.compile(r'\"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) /[^\"]* HTTP/\d', re.I)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_SECRET = re.compile(
    r'(?i)(\b(?:access_token|refresh_token|client_secret|api_key|authorization|password|token)\b\s*[:=]\s*)'
    r'([^\s&,;]+)'
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+")
_INFO_NORMAL = re.compile(
    r'no song detected|no track recognized|no duration found|not confirmed yet|'
    r'failed iterations|consecutive failures|fallback|failed to match|'
    r'no cover image found in shazam response', re.I
)
_ERROR = re.compile(
    r'\b(?:error|exception|traceback|critical|fatal)\b|'
    r'\b(?:request|recording|authentication|authorization) failed\b|'
    r'\b(?:failed to|failure to) (?:connect|record|write|load|send|read|fetch|start)', re.I
)
_WARNING = re.compile(
    r'\bwarning\b|could not|cannot |can.t |unable to |not reachable|'
    r'not available|unavailable|no recording possible|no audio input|'
    r'no appropriate audio|timed? out|connection refused|permission denied|'
    r'\bmissing\b|\bskipped\b', re.I
)
_TRACE = re.compile(
    r'^\s*(?:recording |using audio input:|pickup input level:|'
    r'vinyl input level:|starting shazam|starting recognition|'
    r'no song detected|same song|discogs:|shazam returned|'
    r'config reloaded|auto-detected|detected:|cover url:|album:|genre:|'
    r'\s*trying |sample |recognition loop)', re.I
)


def classify(message: str) -> str:
    if _INFO_NORMAL.search(message) and not _ERROR.search(message):
        return "TRACE"
    if _ERROR.search(message):
        return "ERROR"
    if _WARNING.search(message):
        return "WARNING"
    if _TRACE.search(message):
        return "TRACE"
    return "INFO"


def _connection() -> sqlite3.Connection:
    path = Path(LOG_PATH)
    conn = getattr(_local, "connection", None)
    existing_path = getattr(_local, "path", None)
    pid = os.getpid()
    if conn is not None and existing_path == str(path) and getattr(_local, "pid", None) == pid:
        return conn
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=2, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=2000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS events ("
                 "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL, "
                 "level TEXT NOT NULL, source TEXT NOT NULL, message TEXT NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_level_id ON events(level, id)")
    _local.connection, _local.path, _local.pid = conn, str(path), pid
    return conn


def append(message: str, *, level: str | None = None, source: str | None = None) -> None:
    """Best-effort logging: never break music playback if logging is unavailable."""
    cleaned = _ANSI.sub("", str(message)).strip(" \r\n\t")
    if not cleaned or _REQUEST_LINE.search(cleaned):
        return
    cleaned = _BEARER.sub("Bearer [redacted]", cleaned)
    cleaned = _SECRET.sub(r"\1[redacted]", cleaned)[:3000]
    severity = (level or classify(cleaned)).upper()
    if severity not in _LEVELS:
        severity = "INFO"
    try:
        if severity in ("WARNING", "ERROR"):
            now = time.monotonic()
            key = ((source or _source), severity, cleaned)
            with _recent_lock:
                if now - _recent_issues.get(key, -1000.0) < 60:
                    return
                _recent_issues[key] = now
                if len(_recent_issues) > 500:
                    _recent_issues.clear()
                    _recent_issues[key] = now
        conn = _connection()
        conn.execute(
            "INSERT INTO events(timestamp, level, source, message) VALUES (?, ?, ?, ?)",
            (time.time(), severity, (source or _source)[:50], cleaned),
        )
        count = getattr(_local, "count", 0) + 1
        _local.count = count
        if count % 100 == 0:
            conn.execute(
                "DELETE FROM events WHERE id <= "
                "(SELECT COALESCE(MAX(id), 0) - ? FROM events)", (MAX_EVENTS,)
            )
    except (OSError, sqlite3.Error):
        pass


def trace(message: str, *, source: str | None = None) -> None:
    append(message, level="TRACE", source=source)


def info(message: str, *, source: str | None = None) -> None:
    append(message, level="INFO", source=source)


def warning(message: str, *, source: str | None = None) -> None:
    append(message, level="WARNING", source=source)


def error(message: str, *, source: str | None = None) -> None:
    append(message, level="ERROR", source=source)


def capture_exception(exc: BaseException, *, source: str | None = None) -> None:
    message = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    append(message, level="ERROR", source=source)


def _capturing_print(*args, **kwargs):
    _original_print(*args, **kwargs)
    output = kwargs.get("file")
    if output is not None and output not in (sys.stdout, sys.stderr):
        return
    sep = kwargs.get("sep", " ")
    try:
        message = (" " if sep is None else str(sep)).join(str(a) for a in args)
    except Exception:
        return
    # Avoid capturing transient, carriage-return-only progress messages.
    if "\r" in str(kwargs.get("end", "\n")):
        return
    for line in message.splitlines():
        append(line)


class _LibraryWarningsHandler(logging.Handler):
    """Capture warnings from libraries without enabling noisy HTTP access logs."""

    def __init__(self, source: str):
        super().__init__(level=logging.WARNING)
        self.source = source

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("werkzeug"):
            return
        try:
            message = self.format(record)
            if record.exc_info:
                message += "\n" + "".join(traceback.format_exception(*record.exc_info))
            append(message, level="ERROR" if record.levelno >= logging.ERROR else "WARNING",
                   source=self.source)
        except Exception:
            pass


def install_print_capture(source: str) -> None:
    """Call from process entrypoints, not module import or Flask app factory."""
    global _installed, _source
    with _install_lock:
        _source = source
        if _installed:
            return
        builtins.print = _capturing_print
        logging.getLogger().addHandler(_LibraryWarningsHandler(source))
        _installed = True
        original_hook = sys.excepthook

        def exception_hook(exc_type, exc_value, exc_tb):
            capture_exception(exc_value, source=source)
            original_hook(exc_type, exc_value, exc_tb)

        sys.excepthook = exception_hook
        original_thread_hook = threading.excepthook

        def thread_hook(args):
            capture_exception(args.exc_value, source=source)
            original_thread_hook(args)

        threading.excepthook = thread_hook


def read_events(*, after: int = 0, limit: int = 200, level: str = "issues", search: str = "") -> list[dict]:
    limit = max(1, min(int(limit), 300))
    after = max(0, int(after))
    where = []
    params: list = []
    if after:
        where.append("id > ?")
        params.append(after)
    levels = {
        "issues": ("WARNING", "ERROR"),
        "errors": ("ERROR",),
        "warnings": ("WARNING",),
        "trace": ("TRACE",),
        "all": None,
    }.get(level, ("WARNING", "ERROR"))
    if levels:
        where.append("level IN (" + ",".join("?" for _ in levels) + ")")
        params.extend(levels)
    search = search.strip()[:100]
    if search:
        where.append("instr(lower(message), lower(?)) > 0")
        params.append(search)
    sql = "SELECT id, timestamp, level, source, message FROM events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id " + ("ASC" if after else "DESC") + " LIMIT ?"
    params.append(limit)
    try:
        rows = _connection().execute(sql, params).fetchall()
    except (sqlite3.Error, OSError):
        return []
    if not after:
        rows.reverse()
    return [dict(id=row[0], timestamp=row[1], level=row[2], source=row[3], message=row[4]) for row in rows]


def summary(since: int = 0) -> dict:
    try:
        conn = _connection()
        latest = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
        rows = conn.execute(
            "SELECT level, COUNT(*) FROM events WHERE id > ? "
            "AND level IN ('WARNING', 'ERROR') GROUP BY level", (max(0, int(since)),),
        ).fetchall()
    except (sqlite3.Error, OSError):
        return {"latest_id": 0, "unread": 0, "warnings": 0, "errors": 0}
    counts = dict(rows)
    warns, errors = counts.get("WARNING", 0), counts.get("ERROR", 0)
    return {"latest_id": latest, "unread": warns + errors, "warnings": warns, "errors": errors}
