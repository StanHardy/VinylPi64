"""Yearly listening aggregates and timestamped playback events.

Existing lifetime counters are assigned to 2026 *once* during migration.  They
are not fabricated into timestamped plays.  Future plays and sessions have real
UTC timestamps, while annual totals use the device's local calendar year.
"""
from __future__ import annotations

import time
from typing import Any

LEGACY_YEAR = 2026


def _year(ts: int | None = None) -> int:
    return time.localtime(int(ts if ts is not None else time.time())).tm_year


def create_tables(conn) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS yearly_plays (
        source TEXT NOT NULL CHECK(source IN ('vinyl', 'spotify')),
        year INTEGER NOT NULL,
        song_key TEXT NOT NULL,
        artist TEXT NOT NULL,
        title TEXT NOT NULL,
        album TEXT,
        count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (source, year, song_key)
    );
    CREATE TABLE IF NOT EXISTS yearly_albums (
        source TEXT NOT NULL CHECK(source IN ('vinyl', 'spotify')),
        year INTEGER NOT NULL,
        album TEXT NOT NULL,
        count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (source, year, album)
    );
    CREATE TABLE IF NOT EXISTS yearly_usage (
        source TEXT NOT NULL CHECK(source IN ('vinyl', 'spotify')),
        year INTEGER NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('listening', 'pickup')),
        seconds REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (source, year, kind)
    );
    CREATE TABLE IF NOT EXISTS playback_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        event_type TEXT NOT NULL,
        occurred_at INTEGER NOT NULL,
        song_key TEXT,
        artist TEXT,
        title TEXT,
        album TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_playback_events_year_time ON playback_events(source, occurred_at DESC);
    CREATE INDEX IF NOT EXISTS idx_yearly_plays_year ON yearly_plays(source, year, count DESC);
    """)


def seed_vinyl(conn) -> None:
    """One-time 2026 assignment of preexisting vinyl counters; idempotent."""
    if conn.execute("SELECT 1 FROM meta WHERE key='annual_seed_vinyl_v1'").fetchone():
        return
    conn.execute("""
        INSERT INTO yearly_plays (source, year, song_key, artist, title, album, count)
        SELECT 'vinyl', ?, song_key, artist, title, album, play_count FROM songs WHERE play_count > 0
    """, (LEGACY_YEAR,))
    conn.execute("""
        INSERT INTO yearly_albums (source, year, album, count)
        SELECT 'vinyl', ?, album, session_count FROM album_sessions WHERE session_count > 0
    """, (LEGACY_YEAR,))
    for kind, table in (("listening", "listening_totals"), ("pickup", "pickup_usage_totals")):
        conn.execute(f"""
            INSERT INTO yearly_usage (source, year, kind, seconds)
            SELECT 'vinyl', ?, ?, total_seconds FROM {table} WHERE id=1 AND total_seconds > 0
        """, (LEGACY_YEAR, kind))
    conn.execute("INSERT INTO meta (key,value) VALUES ('annual_seed_vinyl_v1','2026')")


def refresh_vinyl_baseline(conn) -> None:
    """Sync the once-imported legacy JSON with its 2026 annual baseline.

    Storage imports legacy JSON after initializing the database. Without this
    refresh the freshly seeded 2026 rows would still be empty. Never erase real
    timestamped plays when an import is run on a live installation.
    """
    if conn.execute("SELECT 1 FROM playback_events WHERE source='vinyl' LIMIT 1").fetchone():
        return
    for table in ('yearly_plays', 'yearly_albums', 'yearly_usage'):
        conn.execute(f"DELETE FROM {table} WHERE source='vinyl' AND year=?", (LEGACY_YEAR,))
    conn.execute("DELETE FROM meta WHERE key='annual_seed_vinyl_v1'")
    seed_vinyl(conn)


def seed_spotify(conn) -> None:
    if conn.execute("SELECT 1 FROM meta WHERE key='annual_seed_spotify_v1'").fetchone():
        return
    conn.execute("""
        INSERT INTO yearly_plays (source, year, song_key, artist, title, album, count)
        SELECT 'spotify', ?, track_id, artist, title, album, play_count
        FROM spotify_songs WHERE play_count > 0
    """, (LEGACY_YEAR,))
    conn.execute("""
        INSERT INTO yearly_albums (source, year, album, count)
        SELECT 'spotify', ?, album, play_count FROM spotify_album_totals WHERE play_count > 0
    """, (LEGACY_YEAR,))
    conn.execute("""
        INSERT INTO yearly_usage (source, year, kind, seconds)
        SELECT 'spotify', ?, 'listening', total_seconds
        FROM spotify_listening_totals WHERE id=1 AND total_seconds > 0
    """, (LEGACY_YEAR,))
    conn.execute("INSERT INTO meta (key,value) VALUES ('annual_seed_spotify_v1','2026')")


def log_play(conn, source: str, song_key: str, artist: str, title: str,
             album: str | None, *, occurred_at: int | None = None) -> None:
    timestamp = int(time.time() if occurred_at is None else occurred_at)
    conn.execute("""
        INSERT INTO yearly_plays (source, year, song_key, artist, title, album, count)
        VALUES (?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(source,year,song_key) DO UPDATE SET
            count = count + 1,
            album = COALESCE(NULLIF(excluded.album,''),yearly_plays.album)
    """, (source, _year(timestamp), song_key, artist, title, album))
    conn.execute("""
        INSERT INTO playback_events (source,event_type,occurred_at,song_key,artist,title,album)
        VALUES (?,'play',?,?,?,?,?)
    """, (source, timestamp, song_key, artist, title, album))


def log_album(conn, source: str, album: str, *, occurred_at: int | None = None) -> None:
    timestamp = int(time.time() if occurred_at is None else occurred_at)
    conn.execute("""
        INSERT INTO yearly_albums (source,year,album,count) VALUES (?,?,?,1)
        ON CONFLICT(source,year,album) DO UPDATE SET count=count+1
    """, (source, _year(timestamp), album))
    if source == 'vinyl':
        conn.execute("""
            INSERT INTO playback_events (source,event_type,occurred_at,album)
            VALUES ('vinyl','album_session',?,?)
        """, (timestamp, album))


def log_seconds(conn, source: str, kind: str, seconds: float, *, occurred_at: int | None = None) -> None:
    if not seconds:
        return
    conn.execute("""
        INSERT INTO yearly_usage (source,year,kind,seconds) VALUES (?,?,?,?)
        ON CONFLICT(source,year,kind) DO UPDATE SET seconds=seconds+excluded.seconds
    """, (source, _year(occurred_at), kind, float(seconds)))


def get_available_years() -> list[int]:
    from vinylpi.core.database import init_db, get_connection
    from vinylpi.core.spotify_stats import init_spotify_stats
    init_spotify_stats()
    with get_connection() as conn:
        years = [int(r[0]) for r in conn.execute("""
            SELECT year FROM yearly_plays UNION SELECT year FROM yearly_albums
            UNION SELECT year FROM yearly_usage ORDER BY year DESC
        """)]
    return sorted(set([_year(), *years]), reverse=True)


def get_yearly_ranked_stats(source: str, year: int, *, limit: int = 10) -> dict[str, Any]:
    from vinylpi.core.database import init_db, get_connection
    from vinylpi.core.spotify_stats import init_spotify_stats
    if source not in {'vinyl','spotify'}:
        raise ValueError('invalid source')
    if source == 'spotify':
        init_spotify_stats()
    else:
        init_db()
    limit = max(1, min(int(limit), 100))
    year = int(year)
    metadata_table = 'songs' if source == 'vinyl' else 'spotify_songs'
    metadata_key = 'song_key' if source == 'vinyl' else 'track_id'
    with get_connection() as conn:
        song_rows = conn.execute(f"""
            SELECT p.artist,p.title,COALESCE(m.album,p.album) AS album,
                   m.genre,m.cover_url,p.count
            FROM yearly_plays p LEFT JOIN {metadata_table} m ON m.{metadata_key}=p.song_key
            WHERE p.source=? AND p.year=?
            ORDER BY p.count DESC, p.artist COLLATE NOCASE, p.title COLLATE NOCASE LIMIT ?
        """, (source,year,limit)).fetchall()
        artists = conn.execute("""
            SELECT artist AS name,SUM(count) AS count FROM yearly_plays
            WHERE source=? AND year=? GROUP BY artist ORDER BY count DESC,name COLLATE NOCASE LIMIT ?
        """, (source,year,limit)).fetchall()
        album_rows = conn.execute("""
            SELECT album AS name,count FROM yearly_albums WHERE source=? AND year=?
            ORDER BY count DESC,album COLLATE NOCASE LIMIT ?
        """, (source,year,limit)).fetchall()
        genres = conn.execute(f"""
            SELECT m.genre AS name,SUM(p.count) AS count FROM yearly_plays p
            JOIN {metadata_table} m ON m.{metadata_key}=p.song_key
            WHERE p.source=? AND p.year=? AND TRIM(COALESCE(m.genre,''))!=''
            GROUP BY m.genre ORDER BY count DESC,name COLLATE NOCASE LIMIT ?
        """, (source,year,limit)).fetchall()
        covers = conn.execute(f"""
            SELECT y.album AS name,y.count,
              (SELECT s.cover_url FROM {metadata_table} s
               WHERE s.album=y.album AND COALESCE(s.cover_url,'')!=''
               ORDER BY s.play_count DESC LIMIT 1) AS cover_url
            FROM yearly_albums y WHERE y.source=? AND y.year=?
            ORDER BY y.count DESC,y.album COLLATE NOCASE LIMIT ?
        """, (source,year,limit)).fetchall()
        times = dict((r['kind'],float(r['seconds'])) for r in conn.execute("""
            SELECT kind,seconds FROM yearly_usage WHERE source=? AND year=?
        """, (source,year)))
        shazam_column = "SUM(CASE WHEN TRIM(COALESCE(m.shazam_track_id,''))!='' THEN 1 ELSE 0 END)" if source == 'vinyl' else '0'
        coverage = conn.execute(f"""
            SELECT COUNT(*) AS songs_total,
            SUM(CASE WHEN TRIM(COALESCE(m.genre,''))!='' THEN 1 ELSE 0 END) AS songs_with_genre,
            {shazam_column} AS songs_with_shazam_id
            FROM yearly_plays p LEFT JOIN {metadata_table} m ON m.{metadata_key}=p.song_key
            WHERE p.source=? AND p.year=?
        """, (source,year)).fetchone()
    top_songs = [dict(r) for r in song_rows]
    top_albums = [dict(r) for r in album_rows]
    if source == 'spotify':
        with get_connection() as conn:
            for row in top_albums:
                matching = conn.execute("SELECT artist,cover_url FROM spotify_album_totals WHERE album=?", (row['name'],)).fetchone()
                if matching:
                    row['artist'] = matching['artist']
                    row['cover_url'] = matching['cover_url']
    top_genres = [dict(r) for r in genres]
    return {
        'scope': source,
        'year': year,
        'top_songs': top_songs,
        'top_artists': [dict(r) for r in artists],
        'top_albums': top_albums,
        'top_album_covers': [dict(r) for r in covers if r['cover_url']],
        'top_genres': top_genres,
        'radar_genres': top_genres[:6],
        'total_minutes_listened': int(round(times.get('listening',0) / 60)),
        'pickup_usage_seconds': times.get('pickup',0),
        'album_count_unit': 'session' if source=='vinyl' else 'play',
        'metadata_coverage': {
            'songs_total': int(coverage['songs_total'] or 0),
            'songs_with_genre': int(coverage['songs_with_genre'] or 0),
            'songs_with_shazam_id': int(coverage['songs_with_shazam_id'] or 0),
        },
    }
