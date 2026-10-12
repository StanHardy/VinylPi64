from __future__ import annotations

import time
from io import BytesIO
from urllib.parse import quote
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from werkzeug.utils import secure_filename

from vinylpi.config.config_loader import CONFIG_DEFAULTS
from vinylpi.config.runtime import (
    get_current_fallback_path,
    normalize_fallback_kind,
    read_config,
    set_fallback_image_path,
    write_config,
)
from vinylpi.paths import (
    ALLOWED_EXT,
    ALLOWED_FONT_EXT,
    BASE_DIR,
    FONTS_DIR,
    UPLOAD_DIR,
    FALLBACK_NORMAL_DIR,
    FALLBACK_TURN_DIR,
)


def _safe_child_path(directory: Path, filename: str) -> Path | None:
    # Existing bundled fonts may legitimately contain spaces or non-ASCII
    # characters. Reject path traversal instead of requiring Werkzeug's
    # normalized upload filename to equal the on-disk name.
    raw_name = str(filename or "")
    if (
        not raw_name
        or "/" in raw_name
        or "\\" in raw_name
        or Path(raw_name).name != raw_name
        or raw_name in {".", ".."}
    ):
        return None

    candidate = (directory / raw_name).resolve()
    try:
        candidate.relative_to(directory.resolve())
    except ValueError:
        return None
    return candidate


def _safe_upload_path(filename: str) -> Path | None:
    """Support kind-specific uploads and legacy root files, without arbitrary paths."""
    value = str(filename or "")
    if "/" not in value:
        return _safe_child_path(UPLOAD_DIR, value)
    pieces = value.split("/")
    if len(pieces) != 2 or pieces[0] not in {"normal", "turn"}:
        return None
    directory = FALLBACK_NORMAL_DIR if pieces[0] == "normal" else FALLBACK_TURN_DIR
    return _safe_child_path(directory, pieces[1])


def _safe_font_path(filename: str) -> Path | None:
    return _safe_child_path(FONTS_DIR, filename)


def _legacy_fallback_kind(path: Path) -> str:
    """Classify existing root-level images without moving configured files."""
    name = path.stem.casefold()
    return "turn" if name.startswith(("turn_record", "side_flip", "sideflip")) else "normal"


def list_fallback_images(*, kind: str = "normal") -> list[dict]:
    selected_kind = normalize_fallback_kind(kind)
    current_path = get_current_fallback_path(kind=selected_kind)
    selected_dir = FALLBACK_TURN_DIR if selected_kind == "turn" else FALLBACK_NORMAL_DIR
    # Leave existing files in their original location so saved config paths work.
    # New uploads are strictly separated; legacy root images are shown by kind.
    candidates = list(selected_dir.glob("*"))
    candidates.extend(
        file for file in UPLOAD_DIR.glob("*")
        if file.is_file() and _legacy_fallback_kind(file) == selected_kind
    )
    files = []
    for path in sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True):
        if not path.is_file() or path.suffix.lower().lstrip(".") not in ALLOWED_EXT:
            continue
        relative_path = path.relative_to(BASE_DIR).as_posix()
        delete_ref = path.relative_to(UPLOAD_DIR).as_posix()
        files.append(
            {
                "filename": path.name,
                "delete_ref": delete_ref,
                "path": relative_path,
                "url": f"/uploads/{quote(delete_ref, safe='/')}",
                "is_current": relative_path == current_path,
                "kind": selected_kind,
            }
        )
    return files


def delete_fallback_image(filename: str) -> bool:
    path = _safe_upload_path(filename)
    if path is None or not path.is_file():
        return False

    relative_path = path.relative_to(BASE_DIR).as_posix()
    path.unlink()

    for kind in ("normal", "turn"):
        if get_current_fallback_path(kind=kind) == relative_path:
            set_fallback_image_path("", kind=kind)
    return True


def upload_fallback_image(file_storage, *, kind: str = "normal"):
    selected_kind = normalize_fallback_kind(kind)
    if not file_storage or not file_storage.filename:
        return None, "empty filename"

    original_name = secure_filename(file_storage.filename)
    if "." not in original_name:
        return None, "invalid file type"

    extension = original_name.rsplit(".", 1)[-1].lower()
    if extension not in ALLOWED_EXT:
        return None, "invalid file type"

    prefix = "turn_record" if selected_kind == "turn" else "fallback"
    filename = f"{prefix}_{time.time_ns()}.{extension}"
    directory = FALLBACK_TURN_DIR if selected_kind == "turn" else FALLBACK_NORMAL_DIR
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / filename
    file_storage.save(destination)

    relative_path = destination.relative_to(BASE_DIR).as_posix()
    return {
        "image_path": relative_path,
        "filename": filename,
        "kind": selected_kind,
    }, None


def get_current_font_path() -> str:
    cfg = read_config()
    return str((cfg.get("image") or {}).get("font_path") or "")


def list_fonts() -> list[dict]:
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    current_path = get_current_font_path()
    fonts = []

    for path in sorted(FONTS_DIR.glob("*"), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.suffix.lower().lstrip(".") not in ALLOWED_FONT_EXT:
            continue
        relative_path = path.relative_to(BASE_DIR).as_posix()
        fonts.append(
            {
                "filename": path.name,
                "name": path.stem.replace("_", " ").replace("-", " "),
                "path": relative_path,
                "preview_url": f"/api/font-preview/{path.name}",
                "is_current": relative_path == current_path,
                "deletable": path.name.startswith("upload_"),
            }
        )
    return fonts


def upload_font(file_storage):
    if not file_storage or not file_storage.filename:
        return None, "empty filename"

    original_name = secure_filename(file_storage.filename)
    if "." not in original_name:
        return None, "invalid file type"

    extension = original_name.rsplit(".", 1)[-1].lower()
    if extension not in ALLOWED_FONT_EXT:
        return None, "invalid file type"

    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(original_name).stem or "font"
    filename = f"upload_{stem}_{time.time_ns()}.{extension}"
    destination = FONTS_DIR / filename
    file_storage.save(destination)

    try:
        ImageFont.truetype(str(destination), 16)
    except Exception:
        destination.unlink(missing_ok=True)
        return None, "font could not be loaded"

    relative_path = destination.relative_to(BASE_DIR).as_posix()
    return {"font_path": relative_path, "filename": filename}, None


def delete_font(filename: str) -> bool:
    path = _safe_font_path(filename)
    if path is None or not path.is_file() or not path.name.startswith("upload_"):
        return False

    relative_path = path.relative_to(BASE_DIR).as_posix()
    path.unlink()

    if get_current_font_path() == relative_path:
        default_path = str((CONFIG_DEFAULTS.get("image") or {}).get("font_path") or "")
        write_config({"image": {"font_path": default_path}})
    return True


def build_font_preview(filename: str) -> BytesIO | None:
    path = _safe_font_path(filename)
    if path is None or not path.is_file() or path.suffix.lower().lstrip(".") not in ALLOWED_FONT_EXT:
        return None

    width, height = 540, 132
    image = Image.new("RGB", (width, height), (18, 18, 25))
    draw = ImageDraw.Draw(image)

    def load_preview_font(sizes: tuple[int, ...]):
        for size in sizes:
            try:
                return ImageFont.truetype(str(path), size)
            except Exception:
                continue
        return None

    # Some pixel/bitmap-flavoured TTFs only expose usable strikes at a subset
    # of sizes. The Pixoo renderer uses very small glyphs, so a gallery preview
    # should not fail merely because 30 px is unsupported.
    font_large = load_preview_font((30, 26, 24, 20, 18, 16, 14, 12, 10, 8, 6, 5))
    font_small = load_preview_font((18, 16, 14, 12, 10, 9, 8, 7, 6, 5))
    if font_large is None or font_small is None:
        return None

    accent = (245, 197, 66)
    text = (248, 246, 250)
    muted = (177, 171, 187)
    draw.text((22, 20), "VINYLPI64", font=font_large, fill=text)
    draw.text((22, 76), "NOW PLAYING  •  SIDE B  •  01:23", font=font_small, fill=accent)
    draw.line((22, 112, width - 22, 112), fill=(58, 54, 68), width=1)
    draw.text((width - 190, 115), path.stem[:22], font=ImageFont.load_default(), fill=muted)

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
