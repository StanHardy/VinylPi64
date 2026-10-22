from __future__ import annotations

import io

from flask import Blueprint, jsonify, request, send_file

from vinylpi.web.services.stats import build_share_card_image
from vinylpi.web.services.stats_scoped import get_scoped_stats
from vinylpi.core.annual_stats import get_available_years

stats_bp = Blueprint("stats_api", __name__)


@stats_bp.get("/api/stats")
def api_stats():
    scope = request.args.get("scope", "vinyl").strip().lower()
    if scope not in {"vinyl", "spotify", "combined"}:
        return jsonify({"error":"invalid_scope"}), 400
    year = _parse_year(request.args.get('year'))
    if year is False:
        return jsonify({"error":"invalid_year"}), 400
    return jsonify(get_scoped_stats(scope=scope, limit=10, year=year) if year is not None
                   else get_scoped_stats(scope=scope, limit=10))


@stats_bp.get("/api/stats/share-card")
def api_stats_share_card():
    scope = request.args.get("scope", "vinyl").strip().lower()
    if scope not in {"vinyl", "spotify", "combined"}:
        return jsonify({"error": "invalid_scope"}), 400
    year = _parse_year(request.args.get('year'))
    if year is False:
        return jsonify({"error":"invalid_year"}), 400
    image_bytes = (build_share_card_image(scope=scope, year=year) if year is not None
                   else build_share_card_image(scope=scope))
    return send_file(
        io.BytesIO(image_bytes),
        mimetype="image/png",
        download_name=f"vinylpi-spin-report-{scope}-{year or 'all-time'}.png",
        as_attachment=False,
        max_age=0,
    )


def _parse_year(value: str | None) -> int | None | bool:
    if not value or value == 'all':
        return None
    if not value.isascii() or not value.isdecimal() or len(value) != 4:
        return False
    year = int(value)
    return year if 2000 <= year <= 2100 else False


@stats_bp.get('/api/stats/years')
def api_stats_years():
    return jsonify({'years': get_available_years()})


@stats_bp.get('/api/stats/events')
def api_stats_events():
    from vinylpi.core.database import get_connection
    from vinylpi.core.spotify_stats import init_spotify_stats
    init_spotify_stats()
    scope = request.args.get('scope', 'vinyl').strip().lower()
    year = _parse_year(request.args.get('year'))
    if scope not in {'vinyl','spotify','combined'} or year is False:
        return jsonify({'error':'invalid_filter'}), 400
    sql = 'SELECT source,event_type,occurred_at,artist,title,album FROM playback_events WHERE 1=1'
    args = []
    if scope != 'combined':
        sql += ' AND source=?';args.append(scope)
    if year is not None:
        import datetime
        start = int(datetime.datetime(year,1,1,tzinfo=datetime.timezone.utc).timestamp())
        end = int(datetime.datetime(year+1,1,1,tzinfo=datetime.timezone.utc).timestamp())
        # Filter exact year by local timezone in code; query a +/- one-day buffer.
        sql += ' AND occurred_at>=? AND occurred_at<?';args.extend([start-86400,end+86400])
    sql += ' ORDER BY occurred_at DESC LIMIT 1000'
    with get_connection() as conn:
        events = [dict(r) for r in conn.execute(sql,args)]
    if year is not None:
        import time
        events = [r for r in events if time.localtime(r['occurred_at']).tm_year == year]
    return jsonify({'events':events[:200], 'legacy_year':2026,
        'note':'Pre-migration aggregates were assigned to 2026 without individual timestamps.'})
