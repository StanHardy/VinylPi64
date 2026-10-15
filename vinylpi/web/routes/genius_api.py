from __future__ import annotations

import json
from contextlib import closing
import sqlite3
import threading
import unicodedata
from pathlib import Path
from flask import Blueprint, jsonify, request, current_app
from vinylpi.core.genius_scraper import get_lyrics
from vinylpi.paths import DATA_DIR

genius_bp = Blueprint("genius_api", __name__)
_cache_lock = threading.Lock()


def _cache_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _cache_path() -> Path:
    return DATA_DIR / "lyrics_cache.sqlite3"


def _connection():
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    try:
        connection.execute("""CREATE TABLE IF NOT EXISTS lyrics_cache (
            artist TEXT NOT NULL, title TEXT NOT NULL, payload TEXT NOT NULL,
            cached_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (artist, title))""")
    except BaseException:
        connection.close()
        raise
    return connection


@genius_bp.get("/api/lyrics")
def api_lyrics():
    artist = request.args.get("artist", "").strip()
    title = request.args.get("title", "").strip()
    if not artist or not title:
        return jsonify({"ok": False, "error": "missing_params"}), 400

    key = (_cache_key(artist), _cache_key(title))
    try:
        with _cache_lock, closing(_connection()) as db:
            row = db.execute("SELECT payload FROM lyrics_cache WHERE artist=? AND title=?", key).fetchone()
        if row:
            # Keep the original API payload independent of the cache implementation.
            return jsonify(json.loads(row[0]))
    except (sqlite3.Error, OSError, ValueError) as exc:
        current_app.logger.warning("Lyrics cache read failed: %s", exc)

    result = get_lyrics(artist, title)
    if result.get("ok") and result.get("lyrics"):
        try:
            with _cache_lock, closing(_connection()) as db, db:
                db.execute("INSERT OR REPLACE INTO lyrics_cache (artist,title,payload) VALUES (?,?,?)",
                           (*key, json.dumps(result, ensure_ascii=False)))
        except (sqlite3.Error, OSError) as exc:
            current_app.logger.warning("Lyrics cache write failed: %s", exc)
    return jsonify(result)
