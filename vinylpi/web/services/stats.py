from __future__ import annotations

import io
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

from vinylpi.core.stats_db import get_ranked_stats
from vinylpi.paths import FONTS_DIR
from vinylpi.profiles import get_active_profile

_SHARE_CARD_SIZE = (1080, 1920)
_SHARE_CARD_MARGIN = 74
_SHARE_CARD_PANEL_RADIUS = 34
_SHARE_CARD_MAX_COVERS = 3
_FONT_CACHE: dict[tuple[str, int, bool, bool], ImageFont.FreeTypeFont] = {}


def _empty_stats_payload() -> dict[str, Any]:
    return {
        "top_songs": [],
        "top_artists": [],
        "top_albums": [],
        "top_album_covers": [],
        "top_genres": [],
        "radar_genres": [],
        "total_minutes_listened": 0,
        "pickup_usage_seconds": 0.0,
        "metadata_coverage": {
            "songs_total": 0,
            "songs_with_genre": 0,
            "songs_with_shazam_id": 0,
        },
    }


def get_top_stats(limit: int = 10) -> dict:
    try:
        return get_ranked_stats(limit=limit)
    except Exception as exc:
        print(f"Could not load stats from SQLite: {exc}")
        return _empty_stats_payload()


def get_share_card_payload(limit: int = 5, *, scope: str = "vinyl") -> dict[str, Any]:
    # Resolve all three sources through the same aggregation used on /stats.html.
    # Import here to avoid the stats_scoped -> stats circular import at module load.
    from vinylpi.web.services.stats_scoped import get_scoped_stats

    scope = scope if scope in {"vinyl", "spotify", "combined"} else "vinyl"
    stats = get_scoped_stats(scope=scope, limit=max(10, int(limit)))
    profile = get_active_profile() or {}
    genres = list(stats.get("top_genres") or [])[:5]
    return {
        "scope": scope,
        "profile_name": profile.get("name") or "Guest",
        "total_minutes_listened": int(stats.get("total_minutes_listened") or 0),
        "pickup_usage_seconds": float(stats.get("pickup_usage_seconds") or 0.0) if scope == "vinyl" else None,
        "top_artists": list(stats.get("top_artists") or [])[:5],
        "top_albums": list(stats.get("top_albums") or [])[:5],
        "top_album_covers": list(stats.get("top_album_covers") or [])[:_SHARE_CARD_MAX_COVERS],
        "top_genres": genres,
        "album_count_unit": stats.get("album_count_unit") or ("session" if scope == "vinyl" else "play"),
    }


def build_share_card_image(payload: dict[str, Any] | None = None, *, scope: str = "vinyl") -> bytes:
    data = payload if payload is not None else get_share_card_payload(limit=5, scope=scope)
    image = _create_share_card(data)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _font(size: int, *, bold: bool = False, pixel: bool = False) -> ImageFont.FreeTypeFont:
    if pixel:
        filename = "VCR_OSD_MONO_1.001.ttf"
    elif bold:
        filename = "DejaVuSans-Bold.ttf"
    else:
        filename = "DejaVuSans.ttf"

    cache_key = (filename, size, bold, pixel)
    if cache_key in _FONT_CACHE:
        return _FONT_CACHE[cache_key]

    try:
        if pixel:
            font = ImageFont.truetype(str(FONTS_DIR / filename), size)
        else:
            font = ImageFont.truetype(filename, size)
    except Exception:
        fallback_path = FONTS_DIR / ("VCR_OSD_MONO_1.001.ttf" if pixel else "vinylpixel.ttf")
        try:
            font = ImageFont.truetype(str(fallback_path), size)
        except Exception:
            font = ImageFont.load_default()

    _FONT_CACHE[cache_key] = font
    return font


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    value = str(text or "").strip() or "—"
    if _text_size(draw, value, font)[0] <= max_width:
        return value

    ellipsis = "…"
    trimmed = value
    while trimmed:
        trimmed = trimmed[:-1].rstrip()
        candidate = (trimmed + ellipsis).strip()
        if candidate and _text_size(draw, candidate, font)[0] <= max_width:
            return candidate
    return ellipsis


def _wrap_text_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    *,
    max_lines: int = 2,
) -> list[str]:
    value = str(text or "").strip() or "—"
    words = value.split()
    if not words:
        return ["—"]

    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if _text_size(draw, candidate, font)[0] <= max_width:
            current = candidate
            continue

        lines.append(current)
        current = word
        if len(lines) == max_lines - 1:
            break

    remaining_words = words[len(" ".join(lines + [current]).split()):]
    remainder = " ".join([current] + remaining_words).strip()
    if remainder:
        lines.append(_fit_text(draw, remainder, font, max_width))

    if len(lines) > max_lines:
        lines = lines[:max_lines]

    if len(lines) == 1 and _text_size(draw, lines[0], font)[0] > max_width:
        lines[0] = _fit_text(draw, lines[0], font, max_width)

    return lines[:max_lines]


# A record-sleeve-inspired layout: dark, quiet surfaces, hairline rules and
# precise columns. All album artwork stays inside fixed rectangular slots.
_INK = (13, 15, 21)
_PANEL = (23, 25, 33)
_SURFACE = (17, 19, 26)
_WHITE = (244, 245, 248)
_MUTED = (155, 161, 177)
_LINE = (59, 63, 77)


def _accent(scope: str) -> tuple[int, int, int]:
    return {
        "vinyl": (245, 197, 66),
        "spotify": (113, 226, 166),
        "combined": (225, 156, 241),
    }.get(scope, (245, 197, 66))


def _draw_background(size: tuple[int, int], accent: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGB", size, _INK)
    draw = ImageDraw.Draw(image)
    # A subtle vinyl-disc engraving in the upper right, without any gradient.
    cx, cy = 912, 235
    for radius in range(130, 550, 10):
        color = (30, 32, 42) if radius % 40 else (40, 43, 54)
        draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), outline=color, width=1)
    draw.ellipse((cx-56, cy-56, cx+56, cy+56), fill=(27, 29, 38), outline=(49, 52, 65), width=3)
    draw.ellipse((cx-8, cy-8, cx+8, cy+8), fill=accent)
    # A thin print registration line evokes a physical record sleeve.
    draw.rectangle((72, 41, 1008, 45), fill=accent)
    return image


def _panel(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int]) -> None:
    draw.rounded_rectangle(rect, radius=28, fill=_PANEL, outline=_LINE, width=2)


def _draw_right(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font: ImageFont.FreeTypeFont, fill) -> None:
    width, _ = _text_size(draw, text, font)
    draw.text((x-width, y), text, font=font, fill=fill)


def _duration(seconds: float) -> str:
    total_minutes = max(0, round(float(seconds or 0) / 60))
    hours, mins = divmod(total_minutes, 60)
    return f"{hours:,} h {mins:02d} min" if hours else f"{mins:,} min"


def _fetch_cover(url: str | None, size: tuple[int, int]) -> Image.Image | None:
    if not url or not str(url).startswith(("https://", "http://")):
        return None
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        # Album thumbnails never need multi-megabyte images.
        if len(response.content) > 5_000_000:
            return None
        with Image.open(io.BytesIO(response.content)) as source:
            cover = ImageOps.fit(
                ImageOps.exif_transpose(source).convert("RGB"), size,
                method=Image.Resampling.LANCZOS,
            )
            cover.load()
        return cover
    except Exception:
        return None


def _draw_empty_record(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], accent) -> None:
    left, top, right, bottom = rect
    draw.rounded_rectangle(rect, radius=12, fill=(37, 39, 49))
    cx, cy = (left+right)//2, (top+bottom)//2
    radius = (right-left)//2-13
    draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), fill=(15, 16, 22))
    for inner in range(20, radius, 12):
        draw.ellipse((cx-inner, cy-inner, cx+inner, cy+inner), outline=(55, 57, 67), width=2)
    draw.ellipse((cx-27, cy-27, cx+27, cy+27), fill=accent)
    draw.ellipse((cx-4, cy-4, cx+4, cy+4), fill=_INK)


def _draw_cover_gallery(draw: ImageDraw.ImageDraw, image: Image.Image, payload: dict[str, Any], accent) -> None:
    _panel(draw, (72, 712, 1008, 1026))
    draw.text((100, 738), "IN ROTATION", font=_font(23, bold=True), fill=_WHITE)
    _draw_right(draw, 979, 743, "TOP ALBUM ART", _font(17, pixel=True), accent)

    covers = list(payload.get("top_album_covers") or [])[:3]
    columns = ((94, 378), (398, 682), (702, 986))
    for index, (left, right) in enumerate(columns):
        draw.rounded_rectangle((left, 787, right, 1002), radius=17, fill=_SURFACE, outline=(44, 48, 61), width=1)
        cover_rect = (left + 62, 796, left + 222, 956)
        item = covers[index] if index < len(covers) else {}
        cover = _fetch_cover(item.get("cover_url"), (160, 160))
        if cover is None:
            _draw_empty_record(draw, cover_rect, accent)
        else:
            mask = Image.new("L", (160, 160))
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, 159, 159), radius=11, fill=255)
            image.paste(cover, (cover_rect[0], cover_rect[1]), mask)
        title = _fit_text(draw, str(item.get("name") or "—"), _font(18, bold=True), right-left-32)
        title_w, _ = _text_size(draw, title, _font(18, bold=True))
        draw.text((left + (right-left-title_w)//2, 963), title, font=_font(18, bold=True), fill=_WHITE)


def _draw_ranked(draw: ImageDraw.ImageDraw, label: str, items: list[dict[str, Any]], rect, accent, unit: str) -> None:
    _panel(draw, rect)
    left, top, right, bottom = rect
    draw.text((left+26, top+25), label, font=_font(27, bold=True), fill=_WHITE)
    _draw_right(draw, right-26, top+34, unit.upper(), _font(17, pixel=True), accent)
    for i in range(5):
        y = top + 88 + i*83
        draw.rounded_rectangle((left+18, y, right-18, y+73), radius=14, fill=_SURFACE)
        draw.text((left+29, y+21), f"{i+1:02d}", font=_font(19, bold=True), fill=accent)
        item = items[i] if i < len(items) else {}
        name = str(item.get("name") or "—")
        count = str(int(item.get("count") or 0)) if item else "—"
        count_font = _font(28, bold=True)
        count_w, _ = _text_size(draw, count, count_font)
        # Reserve the width of the numeric column even for four-digit counts.
        name_max = max(95, right-left-126-count_w)
        lines = _wrap_text_lines(draw, name, _font(20, bold=True), name_max, max_lines=2)
        if len(lines) > 1:
            for n, line in enumerate(lines):
                draw.text((left+69, y+11+n*25), line, font=_font(20, bold=True), fill=_WHITE)
        else:
            draw.text((left+69, y+23), lines[0], font=_font(20, bold=True), fill=_WHITE)
        _draw_right(draw, right-31, y+19, count, count_font, accent)


def _draw_genres(draw: ImageDraw.ImageDraw, payload: dict[str, Any], accent) -> None:
    _panel(draw, (72, 1632, 1008, 1848))
    draw.text((100, 1654), "TOP GENRES", font=_font(26, bold=True), fill=_WHITE)
    draw.text((792, 1662), "PLAYS", font=_font(17, pixel=True), fill=accent)
    genres = list(payload.get("top_genres") or [])[:5]
    if not genres and payload.get("top_genre"):
        genres = [{"name": payload["top_genre"], "count": 0}]
    if not genres:
        draw.text((104, 1733), "No genre data available yet.", font=_font(23), fill=_MUTED)
        return
    # Two columns: entries 1–3 on the left, 4–5 on the right.
    for i, entry in enumerate(genres):
        col = 0 if i < 3 else 1
        row = i if col == 0 else i-3
        x = 103 if col == 0 else 559
        y = 1704 + row*45
        draw.text((x, y), f"{i+1:02d}", font=_font(20, bold=True), fill=accent)
        name = _fit_text(draw, str(entry.get("name") or "Unknown"), _font(21, bold=True), 300)
        draw.text((x+44, y), name, font=_font(21, bold=True), fill=_WHITE)
        count = str(int(entry.get("count") or 0))
        _draw_right(draw, x+413, y, count, _font(21, bold=True), accent)


def _create_share_card(payload: dict[str, Any]) -> Image.Image:
    width, height = _SHARE_CARD_SIZE
    scope = str(payload.get("scope") or "vinyl").lower()
    if scope not in {"vinyl", "spotify", "combined"}:
        scope = "vinyl"
    accent = _accent(scope)
    image = _draw_background((width, height), accent)
    draw = ImageDraw.Draw(image)

    draw.text((72, 69), "VINYLPI  /  LISTENING LOG", font=_font(22, pixel=True), fill=accent)
    _draw_right(draw, 1008, 70, "01 / STATS", _font(17, pixel=True), _MUTED)
    draw.line((72, 119, 1008, 119), fill=_LINE, width=2)

    draw.text((72, 153), "On the Record.", font=_font(76, bold=True), fill=_WHITE)
    draw.text((76, 265), "A little history of what you listened to.", font=_font(26), fill=_MUTED)

    pill = scope.upper()
    label_font = _font(23, bold=True)
    pill_w, _ = _text_size(draw, pill, label_font)
    draw.rounded_rectangle((73, 325, 119+pill_w, 380), radius=15, fill=accent)
    draw.text((95, 338), pill, font=label_font, fill=_INK)
    profile = _fit_text(draw, str(payload.get("profile_name") or "Guest"), _font(24, bold=True), 430)
    _draw_right(draw, 1008, 339, f"{profile}", _font(24, bold=True), _WHITE)

    _panel(draw, (72, 409, 1008, 689))
    draw.text((105, 448), "TOTAL LISTENING TIME", font=_font(21, pixel=True), fill=accent)
    minutes = f"{int(payload.get('total_minutes_listened') or 0):,}"
    metric_font = _font(116 if len(minutes) <= 6 else 90, bold=True)
    metric = _fit_text(draw, minutes, metric_font, 514)
    draw.text((98, 491), metric, font=metric_font, fill=_WHITE)
    draw.text((106, 615), "minutes listened", font=_font(28), fill=_MUTED)
    draw.line((652, 453, 652, 647), fill=_LINE, width=2)

    if scope == "vinyl":
        draw.text((689, 488), "PICKUP USAGE", font=_font(19, pixel=True), fill=accent)
        draw.text((688, 540), _duration(payload.get("pickup_usage_seconds") or 0),
                  font=_font(30, bold=True), fill=_WHITE)
        draw.text((689, 598), "USB audio activity", font=_font(19), fill=_MUTED)
    else:
        draw.text((689, 488), "SOURCE", font=_font(19, pixel=True), fill=accent)
        source_name = "Spotify" if scope == "spotify" else "Vinyl + Spotify"
        draw.text((688, 540), source_name, font=_font(27, bold=True), fill=_WHITE)
        draw.text((689, 598), "Listening history", font=_font(19), fill=_MUTED)

    _draw_cover_gallery(draw, image, payload, accent)
    album_unit = str(payload.get("album_count_unit") or ("session" if scope == "vinyl" else "play"))
    album_unit_label = "SESSIONS" if album_unit == "session" else ("LISTENS" if album_unit == "listen" else "PLAYS")
    _draw_ranked(draw, "Top 5 artists", list(payload.get("top_artists") or []),
                 (72, 1052, 529, 1608), accent, "plays")
    _draw_ranked(draw, "Top 5 albums", list(payload.get("top_albums") or []),
                 (551, 1052, 1008, 1608), accent, album_unit_label)
    _draw_genres(draw, payload, accent)

    draw.line((72, 1880, 1008, 1880), fill=_LINE, width=2)
    draw.text((74, 1890), "VINYLPI64", font=_font(17, pixel=True), fill=accent)
    _draw_right(draw, 1006, 1890, "ON THE RECORD / " + scope.upper(), _font(16, pixel=True), _MUTED)
    return image
