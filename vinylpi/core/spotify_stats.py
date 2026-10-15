from __future__ import annotations

import time
import threading
from pathlib import Path
from typing import Any

from vinylpi.core.database import get_connection, init_db
from vinylpi.paths import get_active_db_path


_SPOTIFY_INIT_LOCK = threading.Lock()
_SPOTIFY_INITIALIZED_PATHS: set[str] = set()


def init_spotify_stats(db_path: Path | str | None = None) -> None:
    """Initialize Spotify tables once per profile/process (not on every poll)."""
    path = Path(db_path) if db_path is not None else get_active_db_path()
    init_db(path)
    key = str(path.resolve())
    if key in _SPOTIFY_INITIALIZED_PATHS:
        return
    with _SPOTIFY_INIT_LOCK:
        if key in _SPOTIFY_INITIALIZED_PATHS:
            return
        _create_spotify_schema(path)
        _SPOTIFY_INITIALIZED_PATHS.add(key)


def _create_spotify_schema(path: Path) -> None:
    with get_connection(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS spotify_songs (
                track_id TEXT PRIMARY KEY,
                artist TEXT NOT NULL,
                title TEXT NOT NULL,
                artist_id TEXT,
                album TEXT,
                cover_url TEXT,
                genre TEXT,
                duration_ms INTEGER,
                play_count INTEGER NOT NULL DEFAULT 0,
                listened_seconds REAL NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
                updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
            );

            CREATE TABLE IF NOT EXISTS spotify_artist_totals (
                artist TEXT PRIMARY KEY,
                play_count INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
            );

            CREATE TABLE IF NOT EXISTS spotify_album_totals (
                album TEXT PRIMARY KEY,
                artist TEXT,
                cover_url TEXT,
                play_count INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
            );

            CREATE TABLE IF NOT EXISTS spotify_listening_totals (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                total_seconds REAL NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
            );

            INSERT OR IGNORE INTO spotify_listening_totals (id, total_seconds)
            VALUES (1, 0);

            CREATE TABLE IF NOT EXISTS spotify_genre_attempts (
                track_id TEXT PRIMARY KEY,
                last_attempt_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_spotify_songs_play_count
                ON spotify_songs(play_count DESC);
            CREATE INDEX IF NOT EXISTS idx_spotify_songs_artist
                ON spotify_songs(artist);
            CREATE INDEX IF NOT EXISTS idx_spotify_songs_album
                ON spotify_songs(album);
            """
        )


def record_spotify_play(
    *,
    track_id: str,
    artist: str,
    title: str,
    artist_id: str | None = None,
    album: str | None = None,
    cover_url: str | None = None,
    genre: str | None = None,
    duration_ms: int | None = None,
) -> None:
    if not track_id or not artist or not title:
        return
    init_spotify_stats()
    now = int(time.time())
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO spotify_songs (
                track_id, artist, title, artist_id, album, cover_url, genre,
                duration_ms, play_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(track_id) DO UPDATE SET
                play_count = spotify_songs.play_count + 1,
                artist = excluded.artist,
                title = excluded.title,
                artist_id = COALESCE(excluded.artist_id, spotify_songs.artist_id),
                album = COALESCE(excluded.album, spotify_songs.album),
                cover_url = COALESCE(excluded.cover_url, spotify_songs.cover_url),
                genre = COALESCE(excluded.genre, spotify_songs.genre),
                duration_ms = COALESCE(excluded.duration_ms, spotify_songs.duration_ms),
                updated_at = excluded.updated_at
            """,
            (
                track_id,
                artist,
                title,
                artist_id,
                album,
                cover_url,
                genre,
                duration_ms,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO spotify_artist_totals (artist, play_count, updated_at)
            VALUES (?, 1, ?)
            ON CONFLICT(artist) DO UPDATE SET
                play_count = spotify_artist_totals.play_count + 1,
                updated_at = excluded.updated_at
            """,
            (artist, now),
        )
        if album:
            conn.execute(
                """
                INSERT INTO spotify_album_totals (album, artist, cover_url, play_count, updated_at)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(album) DO UPDATE SET
                    play_count = spotify_album_totals.play_count + 1,
                    artist = COALESCE(excluded.artist, spotify_album_totals.artist),
                    cover_url = COALESCE(excluded.cover_url, spotify_album_totals.cover_url),
                    updated_at = excluded.updated_at
                """,
                (album, artist, cover_url, now),
            )


def add_spotify_listening_seconds(track_id: str, seconds: float) -> None:
    seconds = max(0.0, float(seconds))
    if not track_id or seconds <= 0:
        return
    # Reject large jumps caused by sleep/network interruptions. The worker sends
    # small progress deltas, so anything beyond 15 s is not a trustworthy sample.
    seconds = min(seconds, 15.0)
    init_spotify_stats()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE spotify_listening_totals
            SET total_seconds = total_seconds + ?, updated_at = strftime('%s', 'now')
            WHERE id = 1
            """,
            (seconds,),
        )
        conn.execute(
            """
            UPDATE spotify_songs
            SET listened_seconds = listened_seconds + ?, updated_at = strftime('%s', 'now')
            WHERE track_id = ?
            """,
            (seconds, track_id),
        )



def get_last_spotify_status_from_stats() -> dict[str, Any] | None:
    """Return the most recently recorded Spotify track for source switching."""
    init_spotify_stats()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT track_id, artist, title, artist_id, album, cover_url, genre,
                   duration_ms, updated_at
            FROM spotify_songs
            WHERE play_count > 0
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        return None
    return {
        "source": "spotify",
        "artist": row["artist"],
        "title": row["title"],
        "album": row["album"],
        "cover_url": row["cover_url"],
        "genre": row["genre"],
        "track_id": row["track_id"],
        "artist_id": row["artist_id"],
        "duration_ms": row["duration_ms"],
        "updated_at": int(row["updated_at"] or 0) * 1000,
    }


def get_spotify_songs_missing_genre(
    limit: int = 50,
    *,
    db_path: Path | str | None = None,
    retry_after_seconds: int = 0,
) -> list[dict[str, Any]]:
    """Return missing rows, optionally skipping recently attempted lookups."""
    init_spotify_stats(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.track_id, s.artist, s.title, s.artist_id
            FROM spotify_songs AS s
            LEFT JOIN spotify_genre_attempts AS a ON a.track_id = s.track_id
            WHERE (s.genre IS NULL OR TRIM(s.genre) = '')
              AND (a.last_attempt_at IS NULL OR a.last_attempt_at <= ?)
            ORDER BY s.updated_at DESC
            LIMIT ?
            """,
            (int(time.time()) - max(0, int(retry_after_seconds)), max(1, int(limit))),
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def claim_spotify_songs_missing_genre(
    *, limit: int = 8, db_path: Path | str | None = None,
    retry_after_seconds: int = 24 * 60 * 60,
) -> list[dict[str, Any]]:
    """Atomically claim missing genres across the web and Spotify processes.

    Claiming before the HTTP requests prevents two workers from fetching the
    same artist at the same time. Failed lookups become eligible again later.
    """
    init_spotify_stats(db_path)
    now = int(time.time())
    with get_connection(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT s.track_id, s.artist, s.title, s.artist_id
            FROM spotify_songs AS s
            LEFT JOIN spotify_genre_attempts AS a ON a.track_id = s.track_id
            WHERE (s.genre IS NULL OR TRIM(s.genre) = '')
              AND (a.last_attempt_at IS NULL OR a.last_attempt_at <= ?)
            ORDER BY s.updated_at DESC
            LIMIT ?
            """,
            (now - max(0, int(retry_after_seconds)), max(1, int(limit))),
        ).fetchall()
        conn.executemany(
            """INSERT INTO spotify_genre_attempts (track_id, last_attempt_at)
               VALUES (?, ?)
               ON CONFLICT(track_id) DO UPDATE SET last_attempt_at = excluded.last_attempt_at""",
            [(row["track_id"], now) for row in rows],
        )
    return [{key: row[key] for key in row.keys()} for row in rows]


def update_spotify_genre(
    track_id: str, genre: str | None, *, db_path: Path | str | None = None,
) -> None:
    clean = str(genre or "").strip()
    if not track_id or not clean:
        return
    init_spotify_stats(db_path)
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE spotify_songs
            SET genre = ?, updated_at = strftime('%s', 'now')
            WHERE track_id = ?
            """,
            (clean, track_id),
        )

def mark_spotify_genre_attempt(
    track_id: str, *, db_path: Path | str | None = None, attempted_at: int | None = None,
) -> None:
    """Persist a backfill attempt so failed lookups do not repeat on every visit."""
    if not track_id:
        return
    init_spotify_stats(db_path)
    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO spotify_genre_attempts (track_id, last_attempt_at)
               VALUES (?, ?)
               ON CONFLICT(track_id) DO UPDATE SET last_attempt_at = excluded.last_attempt_at""",
            (track_id, int(time.time()) if attempted_at is None else int(attempted_at)),
        )


def get_spotify_ranked_stats(limit: int = 10) -> dict[str, Any]:
    init_spotify_stats()
    limit = max(1, int(limit))
    with get_connection() as conn:
        songs = [
            {
                "title": row["title"],
                "artist": row["artist"],
                "album": row["album"],
                "count": int(row["play_count"] or 0),
                "genre": row["genre"],
                "cover_url": row["cover_url"],
                "duration_ms": row["duration_ms"],
                "spotify_track_id": row["track_id"],
            }
            for row in conn.execute(
                """
                SELECT track_id, artist, title, album, cover_url, genre,
                       duration_ms, play_count
                FROM spotify_songs
                ORDER BY play_count DESC, artist, title
                LIMIT ?
                """,
                (limit,),
            )
        ]
        artists = [
            {"name": row["artist"], "count": int(row["play_count"] or 0)}
            for row in conn.execute(
                """
                SELECT artist, play_count FROM spotify_artist_totals
                ORDER BY play_count DESC, artist LIMIT ?
                """,
                (limit,),
            )
        ]
        albums = [
            {
                "name": row["album"],
                "artist": row["artist"],
                "count": int(row["play_count"] or 0),
                "cover_url": row["cover_url"],
            }
            for row in conn.execute(
                """
                SELECT album, artist, cover_url, play_count FROM spotify_album_totals
                ORDER BY play_count DESC, album LIMIT ?
                """,
                (limit,),
            )
        ]
        genres = [
            {"name": row["genre"], "count": int(row["plays"] or 0)}
            for row in conn.execute(
                """
                SELECT genre, SUM(play_count) AS plays
                FROM spotify_songs
                WHERE genre IS NOT NULL AND genre != ''
                GROUP BY genre
                ORDER BY plays DESC, genre
                LIMIT ?
                """,
                (limit,),
            )
        ]
        total_row = conn.execute(
            "SELECT total_seconds FROM spotify_listening_totals WHERE id = 1"
        ).fetchone()
        coverage_row = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN genre IS NOT NULL AND genre != '' THEN 1 ELSE 0 END) AS with_genre
            FROM spotify_songs
            """
        ).fetchone()

    total_seconds = float((total_row or {"total_seconds": 0})["total_seconds"] or 0.0)
    return {
        "scope": "spotify",
        "top_songs": songs,
        "top_artists": artists,
        "top_albums": albums,
        "top_album_covers": albums,
        "top_genres": genres,
        "radar_genres": genres[:6],
        "total_minutes_listened": int(round(total_seconds / 60.0)),
        "album_count_unit": "play",
        "metadata_coverage": {
            "songs_total": int(coverage_row["total"] or 0) if coverage_row else 0,
            "songs_with_genre": int(coverage_row["with_genre"] or 0) if coverage_row else 0,
            "songs_with_shazam_id": 0,
        },
    }
