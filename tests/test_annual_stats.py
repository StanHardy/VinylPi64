"""Annual migrations, calendar filtering, and immutable playback history."""
from __future__ import annotations

import tempfile
import unittest
import importlib.util
from pathlib import Path
from unittest.mock import patch

from vinylpi.core import annual_stats, database, spotify_stats, stats_db
from vinylpi.web.services.stats_scoped import get_scoped_stats


class AnnualStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'annual.db'
        self.db_patch = patch.object(database, 'get_active_db_path', return_value=self.path)
        self.spotify_patch = patch.object(spotify_stats, 'get_active_db_path', return_value=self.path)
        self.db_patch.start(); self.spotify_patch.start()
        database._INITIALIZED_PATHS.clear()
        spotify_stats._SPOTIFY_INITIALIZED_PATHS.clear()

    def tearDown(self):
        self.spotify_patch.stop(); self.db_patch.stop()
        database._INITIALIZED_PATHS.clear()
        spotify_stats._SPOTIFY_INITIALIZED_PATHS.clear()
        self.tmp.cleanup()

    def test_legacy_totals_seed_to_2026_once(self):
        # Simulate a database made by the previous schema, containing counters
        # but no annual history or migration marker.
        with database.get_connection() as conn:
            database._create_schema(conn)
            database._migrate_schema(conn)
            conn.execute("INSERT INTO songs (song_key,artist,title,album,play_count,genre) VALUES ('s','Band','Track','LP',3,'Rock')")
            conn.execute("INSERT INTO album_sessions(album,session_count) VALUES('LP',2)")
            conn.execute("UPDATE listening_totals SET total_seconds=600 WHERE id=1")
            conn.execute("UPDATE pickup_usage_totals SET total_seconds=3600 WHERE id=1")
        database.init_db()
        selected = annual_stats.get_yearly_ranked_stats('vinyl', 2026)
        self.assertEqual(selected['top_songs'][0]['count'], 3)
        self.assertEqual(selected['top_albums'][0]['count'], 2)
        self.assertEqual(selected['total_minutes_listened'], 10)
        self.assertEqual(selected['pickup_usage_seconds'], 3600)
        with database.get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM playback_events').fetchone()[0], 0)
        # Restart/init must not seed a second time.
        database._INITIALIZED_PATHS.clear()
        database.init_db()
        self.assertEqual(annual_stats.get_yearly_ranked_stats('vinyl', 2026)['top_songs'][0]['count'], 3)

    def test_spotify_legacy_also_seeds_2026_once(self):
        database.init_db()
        spotify_stats._create_spotify_schema(self.path)
        with database.get_connection() as conn:
            conn.execute("INSERT INTO spotify_songs(track_id,artist,title,album,play_count,genre) VALUES ('id1','Band','Tune','LP',4,'Indie')")
            conn.execute("INSERT INTO spotify_album_totals(album,play_count) VALUES ('LP',4)")
            conn.execute("UPDATE spotify_listening_totals SET total_seconds=1200 WHERE id=1")
        spotify_stats.init_spotify_stats()
        data = annual_stats.get_yearly_ranked_stats('spotify', 2026)
        self.assertEqual(data['top_songs'][0]['count'], 4)
        self.assertEqual(data['total_minutes_listened'], 20)
        spotify_stats._SPOTIFY_INITIALIZED_PATHS.clear()
        spotify_stats.init_spotify_stats()
        self.assertEqual(annual_stats.get_yearly_ranked_stats('spotify', 2026)['top_songs'][0]['count'], 4)

    def test_new_plays_go_to_correct_year_and_keep_lifetime_totals(self):
        database.init_db()
        spotify_stats.init_spotify_stats()
        with patch.object(annual_stats, '_year', return_value=2027):
            stats_db.update_song_stats('Band', 'Track', 'LP', genre='Rock')
            stats_db.increment_album_session('LP')
            stats_db.add_listening_seconds(240)
            stats_db.add_pickup_usage_seconds(120)
            spotify_stats.record_spotify_play(track_id='sp1', artist='Band', title='Track', album='LP', genre='Rock')
            spotify_stats.add_spotify_listening_seconds('sp1', 10)
        self.assertEqual(annual_stats.get_yearly_ranked_stats('vinyl', 2027)['top_songs'][0]['count'], 1)
        self.assertEqual(annual_stats.get_yearly_ranked_stats('spotify', 2027)['top_songs'][0]['count'], 1)
        combined = get_scoped_stats('combined', year=2027)
        self.assertEqual(combined['top_artists'][0]['count'], 2)
        self.assertEqual(combined['total_minutes_listened'], 4)
        self.assertEqual(annual_stats.get_yearly_ranked_stats('vinyl', 2026)['top_songs'], [])
        self.assertEqual(stats_db.get_ranked_stats()['top_songs'][0]['count'], 1)
        with database.get_connection() as conn:
            events = conn.execute('SELECT source,event_type FROM playback_events ORDER BY id').fetchall()
        self.assertEqual([(r['source'],r['event_type']) for r in events],
                         [('vinyl','play'), ('vinyl','album_session'), ('spotify','play')])

    def test_years_include_2026_and_other_recorded_years(self):
        database.init_db()
        with patch.object(annual_stats, '_year', return_value=2027):
            stats_db.update_song_stats('Band', 'Track')
        self.assertIn(2027, annual_stats.get_available_years())


if __name__ == '__main__':
    unittest.main()

@unittest.skipUnless(importlib.util.find_spec("flask"), "flask not installed")
class AnnualStatsApiTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        from vinylpi.web.routes.stats_api import stats_bp
        self.app = Flask(__name__)
        self.app.register_blueprint(stats_bp)
        self.client = self.app.test_client()

    def test_invalid_year_is_rejected(self):
        self.assertEqual(self.client.get('/api/stats?year=2026x').status_code, 400)
        self.assertEqual(self.client.get('/api/stats/share-card?year=1999').status_code, 400)

    def test_year_passes_through_to_stats(self):
        with patch('vinylpi.web.routes.stats_api.get_scoped_stats', return_value={'year': 2026}) as mocked:
            response = self.client.get('/api/stats?scope=spotify&year=2026')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['year'], 2026)
            mocked.assert_called_once_with(scope='spotify', limit=10, year=2026)

    def test_year_passes_through_to_share_card(self):
        with patch('vinylpi.web.routes.stats_api.build_share_card_image', return_value=b'png') as mocked:
            response = self.client.get('/api/stats/share-card?scope=combined&year=2026')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, 'image/png')
            self.assertIn('spin-report-combined-2026', response.headers.get('Content-Disposition', ''))
            mocked.assert_called_once_with(scope='combined', year=2026)
