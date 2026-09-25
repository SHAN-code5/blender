"""Pure-Python export target validation for the Blender export layer."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.constants import SUPPORTED_FORMATS
from ..core.errors import ValidationError
from ..utils.files import ensure_directory


def validate_export_target(path: str | Path, output_format: str, overwrite: bool = False) -> Path:
    """Validate format, extension, parent directory, and overwrite policy."""
    fmt = str(output_format or "").lower().lstrip(".")
    if fmt not in SUPPORTED_FORMATS:
        raise ValidationError("Export format is not supported.")
    target = Path(path).expanduser()
    if target.suffix.lower() != "." + fmt:
        raise ValidationError("Export filename extension does not match the selected format.")
    if not target.name or target.name in {".", ".."}:
        raise ValidationError("Export filename is invalid.")
    if target.exists() and not overwrite:
        raise ValidationError("Export target already exists. Choose another path or confirm overwrite.")
    if not str(target.name).strip():
        raise ValidationError("Export filename is invalid.")
    ensure_directory(target.parent)
    return target.resolve()
