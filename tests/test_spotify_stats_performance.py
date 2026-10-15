"""Regressions for fast, local Spotify/Combined stats and bounded backfill."""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from vinylpi.core import spotify_genre_backfill, spotify_stats


class SpotifyStatsPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.first = Path(self.folder.name) / 'one.db'
        self.second = Path(self.folder.name) / 'two.db'

    def _add(self, path, track_id):
        with patch('vinylpi.core.database.get_active_db_path', return_value=path), \
             patch('vinylpi.core.spotify_stats.get_active_db_path', return_value=path):
            spotify_stats.record_spotify_play(
                track_id=track_id, artist='Test Artist', title='A Song', album='Album',
            )

    def test_ranked_stats_read_local_db_without_enrichment(self):
        self._add(self.first, 'song-one')
        with patch('vinylpi.core.database.get_active_db_path', return_value=self.first), \
             patch('vinylpi.core.spotify_stats.get_active_db_path', return_value=self.first), \
             patch('requests.get', side_effect=AssertionError('No HTTP in stats requests')):
            first = spotify_stats.get_spotify_ranked_stats()
            second = spotify_stats.get_spotify_ranked_stats()
        self.assertEqual(first['top_songs'][0]['count'], 1)
        self.assertEqual(second['top_songs'], first['top_songs'])
        self.assertEqual(first['scope'], 'spotify')

    def test_failed_genre_lookup_respects_persistent_retry_window(self):
        self._add(self.first, 'missing')
        spotify_stats.mark_spotify_genre_attempt('missing', db_path=self.first)
        self.assertEqual(
            spotify_stats.get_spotify_songs_missing_genre(db_path=self.first, retry_after_seconds=86400), []
        )
        spotify_stats.mark_spotify_genre_attempt('missing', db_path=self.first, attempted_at=int(time.time()) - 86401)
        self.assertEqual(
            len(spotify_stats.get_spotify_songs_missing_genre(db_path=self.first, retry_after_seconds=86400)), 1
        )

    def test_backfill_attempts_are_per_profile(self):
        self._add(self.first, 'song-one')
        self._add(self.second, 'song-two')
        spotify_stats.mark_spotify_genre_attempt('song-one', db_path=self.first)
        self.assertEqual(
            spotify_stats.get_spotify_songs_missing_genre(db_path=self.first, retry_after_seconds=86400), []
        )
        rows = spotify_stats.get_spotify_songs_missing_genre(db_path=self.second, retry_after_seconds=86400)
        self.assertEqual([row['track_id'] for row in rows], ['song-two'])

    def test_backfill_schedule_is_nonblocking_and_deduplicated(self):
        entered = threading.Event()
        release = threading.Event()
        def slow_backfill(path, *, limit):
            entered.set()
            release.wait(2)
        try:
            with patch.object(spotify_genre_backfill, '_enrich_missing_genres', side_effect=slow_backfill):
                start = time.monotonic()
                self.assertTrue(spotify_genre_backfill.request_spotify_genre_backfill(db_path=self.first))
                self.assertLess(time.monotonic() - start, 0.5)
                self.assertTrue(entered.wait(1))
                self.assertFalse(spotify_genre_backfill.request_spotify_genre_backfill(db_path=self.first))
        finally:
            release.set()

    def test_spotify_initialization_is_once_per_database(self):
        with patch.object(spotify_stats, '_create_spotify_schema', wraps=spotify_stats._create_spotify_schema) as schema:
            spotify_stats.init_spotify_stats(self.first)
            spotify_stats.init_spotify_stats(self.first)
            spotify_stats.init_spotify_stats(self.second)
            spotify_stats.init_spotify_stats(self.second)
        # Other tests may already have initialized these paths. Each unique
        # temporary path is only initialized once on its first invocation.
        self.assertEqual(schema.call_count, 2)


class SpotifyBackfillClaimTests(unittest.TestCase):
    def test_claiming_is_atomic_and_prevents_duplicate_attempts(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / 'profile.db'
            with patch('vinylpi.core.database.get_active_db_path', return_value=db_path), \
                 patch('vinylpi.core.spotify_stats.get_active_db_path', return_value=db_path):
                for idx in range(3):
                    spotify_stats.record_spotify_play(
                        track_id=f'item-{idx}', artist='Band', title=f'Song {idx}'
                    )
            first = spotify_stats.claim_spotify_songs_missing_genre(
                db_path=db_path, limit=2, retry_after_seconds=86400
            )
            second = spotify_stats.claim_spotify_songs_missing_genre(
                db_path=db_path, limit=2, retry_after_seconds=86400
            )
            self.assertEqual(len(first), 2)
            self.assertEqual(len(second), 1)
            self.assertFalse({row['track_id'] for row in first} & {row['track_id'] for row in second})
            self.assertEqual(
                spotify_stats.claim_spotify_songs_missing_genre(db_path=db_path, retry_after_seconds=86400), []
            )


class ScopedStatsServiceTests(unittest.TestCase):
    def test_spotify_stats_returns_local_data_and_schedules_enrichment(self):
        from vinylpi.web.services import stats_scoped
        payload = {'scope': 'spotify', 'top_songs': [], 'top_artists': [],
                   'top_albums': [], 'top_album_covers': [], 'top_genres': [],
                   'total_minutes_listened': 311}
        with patch.object(stats_scoped, 'get_spotify_ranked_stats', return_value=payload) as local, \
             patch.object(stats_scoped, 'request_spotify_genre_backfill') as background:
            result = stats_scoped.get_scoped_stats('spotify')
            self.assertEqual(result['total_minutes_listened'], 311)
            local.assert_called_once_with(limit=10)
            background.assert_called_once_with()

    def test_combined_stats_schedules_enrichment_without_waiting(self):
        from vinylpi.web.services import stats_scoped
        payload = {'top_songs': [], 'top_artists': [], 'top_albums': [],
                   'top_album_covers': [], 'top_genres': [], 'total_minutes_listened': 311}
        with patch.object(stats_scoped, 'get_spotify_ranked_stats', return_value=payload), \
             patch.object(stats_scoped, '_vinyl_stats', return_value={**payload, 'total_minutes_listened': 2267}), \
             patch.object(stats_scoped, 'request_spotify_genre_backfill') as background:
            result = stats_scoped.get_scoped_stats('combined')
            self.assertEqual(result['total_minutes_listened'], 2578)
            background.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
