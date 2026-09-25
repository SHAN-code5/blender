"""Filesystem path and safe file-output helpers."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Iterable, Optional

from ..core.errors import ValidationError

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(value: str, fallback: str = "asset") -> str:
    """Return a deterministic filename component without path separators."""
    text = str(value or "")
    if "/" in text or "\\" in text or ".." in text:
        raise ValidationError("Remote filename contains an unsafe path component.")
    text = _SAFE_NAME.sub("_", text).strip("._")
    return text[:100] or fallback


def ensure_directory(path: os.PathLike[str] | str) -> Path:
    """Create and return a directory path."""
    result = Path(path).expanduser()
    result.mkdir(parents=True, exist_ok=True)
    return result


def is_within(path: Path, root: Path) -> bool:
    """Return whether a resolved path is contained by a resolved root."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def safe_output_path(root: os.PathLike[str] | str, job_id: str, name: str, suffix: str) -> Path:
    """Build a path under root after sanitizing every untrusted component."""
    root_path = ensure_directory(root)
    safe_job = sanitize_filename(job_id, "job")
    safe_name = sanitize_filename(name, "asset")
    safe_suffix = "." + sanitize_filename(suffix.lstrip("."), "bin").lstrip(".")
    target = root_path / safe_job / f"{safe_name}{safe_suffix}"
    if not is_within(target, root_path):
        raise ValidationError("The output path is outside the configured cache directory.")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def atomic_write_bytes(path: Path, data: bytes) -> Path:
    """Write bytes atomically, preventing partial files from being imported."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".partial-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path


def cleanup_partial_downloads(root: os.PathLike[str] | str) -> None:
    """Remove stale partial files left by interrupted downloads."""
    root_path = Path(root)
    if not root_path.exists():
        return
    for path in root_path.rglob(".partial-*"):
        try:
            path.unlink()
        except OSError:
            pass


def format_content_disposition(value: str) -> Optional[str]:
    """Extract a conservative filename from a Content-Disposition header."""
    marker = "filename="
    index = value.lower().find(marker)
    if index < 0:
        return None
    tail = value[index + len(marker):]
    filename = tail.split(";", 1)[0].strip().strip('"')
    return sanitize_filename(filename, "asset")
