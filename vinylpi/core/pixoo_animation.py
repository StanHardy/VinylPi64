"""Render an on-device Pixoo marquee and expose it through the VinylPi web server.

The Pixoo fetches the finished GIF itself. This module only manages animation
files; it never relies on browser sessions or a public Internet service.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import re
import secrets
import socket
import tempfile
import time
from urllib.parse import urlsplit

from PIL import Image

from vinylpi.paths import DATA_DIR

GIF_DIR = DATA_DIR / "pixoo_animations"
_TOKEN = re.compile(r"^[0-9a-f]{32}$")
MAX_FRAMES = 360
MAX_FILES = 16
MAX_AGE_SECONDS = 24 * 60 * 60
WEB_PORT = 8080


def animation_file(token: str) -> Path | None:
    """Resolve only generated random filenames; never accept arbitrary paths."""
    if not _TOKEN.fullmatch(token):
        return None
    return GIF_DIR / f"{token}.gif"


def _prune_files(*, current: Path) -> None:
    """Keep the current animation and a small window of recent animations.

    Retain old files temporarily: the Pixoo can fetch a URL asynchronously,
    even after the HTTP command returns success.
    """
    now = time.time()
    try:
        files = sorted(GIF_DIR.glob("*.gif"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        # An older file may be removed by another worker during cleanup.
        return
    for index, path in enumerate(files):
        if path == current:
            continue
        try:
            if index >= MAX_FILES or now - path.stat().st_mtime > MAX_AGE_SECONDS:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def write_animation(resources: dict, speed_px_per_s: float, render_frame, cancelled) -> str | None:
    """Generate a finite seamless scrolling loop using the existing renderer.

    Long text is sampled across the complete scroll range instead of being
    truncated. An explicit upper bound protects memory and CPU on the Pi Zero.
    Returns a random download token, or None when cancelled.
    """
    canvas_size = int(resources["canvas_size"])
    widths = [int(line["width"]) for line in resources["lines"] if int(line["width"]) > canvas_size]
    if not widths:
        raise ValueError("Cannot create scrolling GIF without scrolling text")
    if canvas_size not in (16, 32, 64):
        raise ValueError("Pixoo GIF must be 16, 32 or 64 pixels square")

    total_ticks = max(widths) + canvas_size
    speed = max(1.0, min(200.0, float(speed_px_per_s)))
    # GIF timing uses hundredths of a second. Sample faster scroll speeds
    # instead of slowing the animation down with a 20 ms minimum delay.
    step = max(1, math.ceil(total_ticks / MAX_FRAMES), math.ceil(speed / 50.0))
    ticks = range(0, total_ticks, step)
    duration_ms = max(20, int(round((1000.0 * step / speed) / 10.0)) * 10)

    # GIF supports up to 256 colors. Share a single cover-based palette across
    # the complete animation to avoid distracting frame-to-frame color shifts.
    seed = resources["base_canvas"].convert("RGB").copy()
    seed.putpixel((0, 0), tuple(resources["text_color"]))
    palette = seed.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    frames = []
    for tick in ticks:
        if cancelled.is_set():
            return None
        frame = render_frame(resources, tick).convert("RGB")
        frames.append(frame.quantize(palette=palette, dither=Image.Dither.NONE))

    if cancelled.is_set():
        return None

    GIF_DIR.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    dest = GIF_DIR / f"{token}.gif"
    fd, tmp = tempfile.mkstemp(prefix=".pixoo-", suffix=".tmp", dir=GIF_DIR)
    os.close(fd)
    try:
        frames[0].save(
            tmp,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=duration_ms,
            loop=0,
            disposal=2,
            optimize=False,
        )
        if cancelled.is_set():
            return None
        os.chmod(tmp, 0o644)  # Web and recognizer may run as separate users.
        os.replace(tmp, dest)
    finally:
        Path(tmp).unlink(missing_ok=True)

    _prune_files(current=dest)
    return token


def build_animation_url(pixoo_ip: str, token: str) -> str:
    """Use the source LAN address that routes to this Pixoo, not localhost/Tailscale."""
    if animation_file(token) is None:
        raise ValueError("Invalid GIF token")

    override = os.environ.get("VINYLPI_PIXOO_GIF_BASE_URL", "").strip().rstrip("/")
    if override:
        parsed = urlsplit(override)
        if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("", "/"):
            raise ValueError("VINYLPI_PIXOO_GIF_BASE_URL must be an HTTP host and optional port")
        base = override
    else:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((pixoo_ip, 80))
            host = sock.getsockname()[0]
        if host in ("0.0.0.0", "127.0.0.1"):
            raise OSError("Cannot determine Pi LAN IP for Pixoo")
        base = f"http://{host}:{WEB_PORT}"

    url = f"{base}/api/pixoo/animation/{token}.gif"
    if len(url.encode("utf-8")) > 255:
        raise ValueError("Pixoo GIF URL exceeds firmware limit (255 bytes)")
    return url
