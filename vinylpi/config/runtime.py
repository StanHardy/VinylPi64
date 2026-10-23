from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterator

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from vinylpi.config.config_loader import CONFIG_DEFAULTS, deep_update, load_config
from vinylpi.paths import CONFIG_PATH, get_active_config_path
from vinylpi.core.display_layout import normalize_image_config

_LEGACY_CONFIG_PATH = CONFIG_PATH

_CACHE: dict[str, Any] = {"path": None, "mtime": None, "cfg": None, "ts": 0.0}
_CACHE_TTL_SECONDS = 0.5
_CACHE_LOCK = threading.RLock()
# The OS file lock also protects against other worker processes. This lock
# serializes threads in this process (and avoids Windows' bounded lock retries).
_WRITE_THREAD_LOCK = threading.Lock()


def _current_config_path() -> Path:
    # Preserve compatibility with tests/tools that monkey-patch CONFIG_PATH.
    if Path(CONFIG_PATH) != Path(_LEGACY_CONFIG_PATH):
        return Path(CONFIG_PATH)
    return get_active_config_path()


def clear_config_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.update({"path": None, "mtime": None, "cfg": None, "ts": 0.0})


def _file_version(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return None


def _update_cache(path: Path, cfg: Dict[str, Any]) -> None:
    with _CACHE_LOCK:
        _CACHE.update({
            "path": str(path),
            "mtime": _file_version(path),
            "cfg": deepcopy(cfg),
            "ts": time.monotonic(),
        })


def read_config(force: bool = False) -> Dict[str, Any]:
    path = _current_config_path()
    version = _file_version(path)
    with _CACHE_LOCK:
        if not force and _CACHE["cfg"] is not None:
            fresh_enough = time.monotonic() - float(_CACHE["ts"]) < _CACHE_TTL_SECONDS
            same_file = _CACHE["path"] == str(path) and _CACHE["mtime"] == version
            if fresh_enough and same_file:
                return deepcopy(_CACHE["cfg"])

        # os.replace makes reads safe even while another process is writing.
        cfg = load_config(path)
        # Do not cache a snapshot from before a concurrent file replacement.
        if _file_version(path) == version:
            _CACHE.update({
                "path": str(path), "mtime": version,
                "cfg": deepcopy(cfg), "ts": time.monotonic(),
            })
        return deepcopy(cfg)


@contextmanager
def _config_write_lock(path: Path) -> Iterator[None]:
    """Exclusive lock for the complete read/merge/replace transaction.

    The separate .lock file must persist: deleting it would allow a new writer
    to lock a different inode while existing processes still hold the old one.
    Each profile has its own independent lock file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with _WRITE_THREAD_LOCK:
        with lock_path.open("a+b") as handle:
            if os.name == "nt":
                # msvcrt locks byte ranges, and requires a byte to be present.
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def write_config(data: Dict[str, Any] | None) -> Dict[str, Any]:
    path = _current_config_path()
    with _config_write_lock(path):
        # Always reload from disk *after* acquiring the lock. Reading before
        # locking would lose changes made by another thread or worker process.
        new_cfg = load_config(path)
        if isinstance(data, dict):
            deep_update(new_cfg, data)
        new_cfg["image"] = normalize_image_config(new_cfg.get("image"))
        _atomic_write_json(path, new_cfg)
        _update_cache(path, new_cfg)
        return deepcopy(new_cfg)


def reset_config() -> Dict[str, Any]:
    path = _current_config_path()
    with _config_write_lock(path):
        cfg = deepcopy(CONFIG_DEFAULTS)
        _atomic_write_json(path, cfg)
        _update_cache(path, cfg)
        return deepcopy(cfg)


def _atomic_write_json(path: Path, obj: Dict[str, Any]) -> None:
    """Write a complete JSON file atomically with a unique same-dir temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            # Keep existing file permissions when replacing an existing config.
            try:
                os.chmod(temp_path, path.stat().st_mode & 0o777)
            except FileNotFoundError:
                pass
            json.dump(obj, handle, indent=4)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


_FALLBACK_PATH_FIELDS = {
    "normal": "image_path",
    "turn": "side_flip_image_path",
}


def normalize_fallback_kind(kind: str | None) -> str:
    value = str(kind or "normal").strip().casefold()
    if value not in _FALLBACK_PATH_FIELDS:
        raise ValueError("invalid fallback image kind")
    return value


def set_fallback_image_path(rel_path: str, *, kind: str = "normal") -> bool:
    selected_kind = normalize_fallback_kind(kind)
    # A partial update is essential: reading a whole snapshot here and writing
    # it back would overwrite unrelated settings changed in the meantime.
    write_config({"fallback": {_FALLBACK_PATH_FIELDS[selected_kind]: rel_path}})
    return True


def get_current_fallback_path(*, kind: str = "normal") -> str | None:
    selected_kind = normalize_fallback_kind(kind)
    try:
        cfg = read_config()
        return (cfg.get("fallback") or {}).get(_FALLBACK_PATH_FIELDS[selected_kind]) or None
    except Exception:
        return None
