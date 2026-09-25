"""Persistent, bounded local history with no credentials."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

from ..core.constants import HISTORY_FILENAME, QUALITY_LEVELS, STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED, STATUS_TIMEOUT, SUPPORTED_FORMATS
from ..core.models import HistoryEntry
from ..utils.files import atomic_write_bytes, ensure_directory

MAX_HISTORY_ENTRIES = 100
_HISTORY_STATUSES = frozenset({STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED, STATUS_TIMEOUT, "queued", "processing", "submitting", "cancelling"})


def _is_valid_history_entry(entry: HistoryEntry, allowed_root: Path) -> bool:
    """Return whether one deserialized history row is safe for Blender."""
    required = (entry.job_id, entry.provider, entry.timestamp, entry.model, entry.quality, entry.output_format, entry.status)
    if any(not isinstance(value, str) or not value for value in required):
        return False
    if not isinstance(entry.prompt, str):
        return False
    if entry.quality not in QUALITY_LEVELS or entry.output_format.lower().lstrip(".") not in SUPPORTED_FORMATS:
        return False
    if entry.generation_mode not in {"text_to_3d", "image_to_3d"} or entry.status not in _HISTORY_STATUSES:
        return False
    if entry.generation_mode == "text_to_3d" and not entry.prompt:
        return False
    if isinstance(entry.polygon_target, bool) or not isinstance(entry.polygon_target, int) or not 100 <= entry.polygon_target <= 10_000_000:
        return False
    if isinstance(entry.texture_resolution, bool) or not isinstance(entry.texture_resolution, int) or not 256 <= entry.texture_resolution <= 8192:
        return False
    if any(not isinstance(getattr(entry, name), bool) for name in ("auto_uv", "generate_materials", "generate_textures", "prompt_enhanced")):
        return False
    if entry.file_path:
        try:
            Path(entry.file_path).expanduser().resolve().relative_to(allowed_root)
        except (OSError, RuntimeError, ValueError):
            return False
    return True


def read_history(path: Path) -> List[HistoryEntry]:
    """Read valid history entries, ignoring corrupt or unsupported rows."""
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    allowed_root = path.parent.resolve()
    entries: List[HistoryEntry] = []
    for row in value[-MAX_HISTORY_ENTRIES:]:
        if not isinstance(row, dict) or not isinstance(row.get("job_id"), str):
            continue
        if any(str(key).lower() in {"api_key", "authorization", "token", "secret", "password", "credential"} for key in row):
            continue
        try:
            entry = HistoryEntry.from_dict(row)
        except (TypeError, ValueError):
            continue
        legacy_defaults = {
            "timestamp": "migrated",
            "negative_prompt": "",
            "model": "default",
            "quality": "standard",
            "output_format": "glb",
            "file_path": "",
        }
        if not all((entry.job_id, entry.provider, entry.timestamp, entry.model, entry.quality, entry.output_format, entry.status)):
            if not all(key not in row for key in legacy_defaults):
                continue
            try:
                entry = HistoryEntry.from_dict({**legacy_defaults, **row})
            except (TypeError, ValueError):
                continue
        if not _is_valid_history_entry(entry, allowed_root):
            continue
        entries.append(entry)
    return entries


def visible_history_index_to_storage(rows: list, index: int) -> int:
    """Map a newest-20 UI index to the bounded persisted history index."""
    if not 0 <= index < min(20, len(rows)):
        raise IndexError(index)
    return max(0, len(rows) - 20) + index


def write_history(path: Path, entries: Iterable[HistoryEntry]) -> Path:
    """Atomically persist a bounded, JSON-serializable history."""
    path = Path(path)
    ensure_directory(path.parent)
    rows = [entry.to_dict() for entry in list(entries)[-MAX_HISTORY_ENTRIES:]]
    data = json.dumps(rows, indent=2, ensure_ascii=False).encode("utf-8")
    return atomic_write_bytes(path, data)


def make_entry(
    *,
    job_id: str,
    prompt: str,
    negative_prompt: str,
    provider: str,
    model: str,
    quality: str,
    output_format: str,
    file_path: str = "",
    status: str = "queued",
    error: str = "",
    generation_mode: str = "text_to_3d",
    image_path: str = "",
    style: str = "realistic",
    polygon_target: int = 50000,
    topology_preference: str = "balanced",
    texture_resolution: int = 2048,
    auto_uv: bool = False,
    generate_materials: bool = True,
    generate_textures: bool = True,
    prompt_enhanced: bool = False,
    original_prompt: str = "",
) -> HistoryEntry:
    """Create a history entry with an ISO timestamp and no secrets."""
    return HistoryEntry(
        job_id=job_id,
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        prompt=prompt,
        negative_prompt=negative_prompt,
        provider=provider,
        model=model,
        quality=quality,
        output_format=output_format,
        file_path=file_path,
        status=status,
        error=error,
        generation_mode=generation_mode,
        image_path=image_path,
        style=style,
        polygon_target=polygon_target,
        topology_preference=topology_preference,
        texture_resolution=texture_resolution,
        auto_uv=auto_uv,
        generate_materials=generate_materials,
        generate_textures=generate_textures,
        prompt_enhanced=prompt_enhanced,
        original_prompt=original_prompt,
    )


def default_history_path(cache_dir: Path) -> Path:
    return Path(cache_dir) / HISTORY_FILENAME
