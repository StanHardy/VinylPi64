"""Local Git-based VinylPi build identification (no GitHub API requests).

Git tags are the release version source of truth. Untagged code is explicitly
identified as a development build; local code edits do not masquerade as a
published release. Works with historical v0.8-style tags and future SemVer tags.
"""
from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path

from vinylpi.paths import BASE_DIR

_REPO_URL = "https://github.com/simontrost/VinylPi64"
_DESCRIBE = re.compile(r"^(v\d+\.\d+(?:\.\d+)?(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?)-(\d+)-g([0-9a-f]+)$")
_SHA = re.compile(r"^[0-9a-f]{7,40}$")


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


@lru_cache(maxsize=1)
def get_version_info() -> dict[str, object]:
    """Return version metadata for the local checkout; cached until restart.

    A release is *only* considered an exact tag when HEAD points at the tag
    and there are no modifications in vinylpi/. Changes in tracked runtime
    data, e.g. data/config.json, do not change the displayed code version.
    """
    return _version_for_root(BASE_DIR)


def _version_for_root(root: Path) -> dict[str, object]:
    describe = _git(root, "describe", "--tags", "--long", "--always", "--match", "v[0-9]*")
    if not describe:
        return {
            "display": "Unavailable",
            "status": "Unknown build",
            "detail": "Git version metadata is unavailable in this installation.",
            "commit": "",
            "tag": "",
            "tag_url": "",
            "commit_url": "",
        }

    # Inspect ONLY source paths: user-edited config.json is tracked in some
    # older checkouts, but it must not turn a tagged release into a dev build.
    code_status = _git(root, "status", "--porcelain", "--untracked-files=normal", "--", "vinylpi")
    dirty = bool(code_status) if code_status is not None else False

    match = _DESCRIBE.fullmatch(describe)
    if match:
        tag, count_text, short_sha = match.groups()
        count = int(count_text)
        is_release = count == 0 and not dirty
        detail_parts = []
        if count:
            detail_parts.append(f"{count} commit{'s' if count != 1 else ''} after {tag}")
        if dirty:
            detail_parts.append("Local source changes")
        if is_release:
            detail_parts.append("Tagged release")
        return {
            "display": tag if count == 0 else describe,
            "status": "Release" if is_release else "Development build",
            "detail": " · ".join(detail_parts),
            "commit": short_sha,
            "tag": tag,
            "tag_url": f"{_REPO_URL}/tree/{tag}",
            "commit_url": f"{_REPO_URL}/commit/{short_sha}",
        }

    # No matching tags in this clone (possibly a shallow clone): commit SHA
    # still provides a truthful, deterministic identifier.
    short_sha = describe if _SHA.fullmatch(describe) else _git(root, "rev-parse", "--short", "HEAD")
    if not short_sha or not _SHA.fullmatch(short_sha):
        return {
            "display": "Unavailable",
            "status": "Unknown build",
            "detail": "No matching Git version tag was found.",
            "commit": "",
            "tag": "",
            "tag_url": "",
            "commit_url": "",
        }
    return {
        "display": short_sha,
        "status": "Untagged build",
        "detail": "No release tag in this checkout" + (" · Local source changes" if dirty else ""),
        "commit": short_sha,
        "tag": "",
        "tag_url": "",
        "commit_url": f"{_REPO_URL}/commit/{short_sha}",
    }
