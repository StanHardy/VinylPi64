"""Best-effort asynchronous enrichment of missing Spotify genres.

Statistics requests only schedule work; network calls never run in the request
thread. Work is isolated by profile database and unsuccessful attempts are
persisted with a retry delay instead of being repeated on each tab switch.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from vinylpi.core.spotify_stats import (
    claim_spotify_songs_missing_genre,
    update_spotify_genre,
)
from vinylpi.paths import get_active_db_path

_RETRY_SECONDS = 24 * 60 * 60
_MIN_SCHEDULE_INTERVAL = 5 * 60
_lock = threading.Lock()
_running: set[str] = set()
_last_started: dict[str, float] = {}


def request_spotify_genre_backfill(
    *, db_path: Path | str | None = None, limit: int = 8,
) -> bool:
    """Schedule bounded background work without waiting for external APIs."""
    path = Path(db_path) if db_path is not None else get_active_db_path()
    key = str(path.resolve())
    now = time.monotonic()
    with _lock:
        if key in _running or now - _last_started.get(key, float('-inf')) < _MIN_SCHEDULE_INTERVAL:
            return False
        _running.add(key)
        _last_started[key] = now

    def run() -> None:
        try:
            _enrich_missing_genres(path, limit=limit)
        except Exception as exc:
            print(f"Spotify genre backfill skipped: {exc}")
        finally:
            with _lock:
                _running.discard(key)

    try:
        threading.Thread(target=run, name="spotify-genre-backfill", daemon=True).start()
    except Exception:
        with _lock:
            _running.discard(key)
            _last_started.pop(key, None)
        raise
    return True


def _enrich_missing_genres(db_path: Path, *, limit: int) -> None:
    # Import lazily: neither web startup nor statistics JSON depends on OAuth.
    from vinylpi.integrations.spotify_client import SpotifyClient

    client = SpotifyClient(profile_db_path=db_path)
    if not client.authorized:
        return
    rows = claim_spotify_songs_missing_genre(
        limit=max(1, min(int(limit), 30)),
        db_path=db_path,
        retry_after_seconds=_RETRY_SECONDS,
    )
    for row in rows:
        track_id = str(row.get("track_id") or "")
        try:
            genre = client.get_artist_genre(
                row.get("artist_id"),
                artist=str(row.get("artist") or ""),
                title=str(row.get("title") or ""),
            )
            if genre:
                update_spotify_genre(track_id, genre, db_path=db_path)
        except Exception as exc:
            print(f"Spotify genre lookup failed for {track_id}: {exc}")
