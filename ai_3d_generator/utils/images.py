"""Image input validation and metadata helpers for image-to-3D adapters."""
from __future__ import annotations

from pathlib import Path

from ..core.errors import ValidationError

SUPPORTED_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_SIGNATURES = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
}


def validate_image_path(path: str | Path) -> str:
    """Validate a local reference image before any provider upload."""
    candidate = Path(path).expanduser()
    if not candidate.is_file():
        raise ValidationError("Reference image does not exist.")
    suffix = candidate.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_SUFFIXES:
        raise ValidationError("Reference image must be PNG, JPG, JPEG, or WEBP.")
    size = candidate.stat().st_size
    if size <= 0 or size > 25 * 1024 * 1024:
        raise ValidationError("Reference image must be between 1 byte and 25 MB.")
    with candidate.open("rb") as handle:
        header = handle.read(32)
    if suffix == ".webp":
        if not header.startswith(_SIGNATURES["image/webp"][0]) or header[8:12] != b"WEBP":
            raise ValidationError("Reference image content does not match its extension.")
    elif suffix == ".png":
        if not header.startswith(_SIGNATURES["image/png"][0]):
            raise ValidationError("Reference image content does not match its extension.")
    elif not header.startswith(_SIGNATURES["image/jpeg"][0]):
        raise ValidationError("Reference image content does not match its extension.")
    return str(candidate.resolve())


def image_mime_type(path: str | Path) -> str:
    """Return a safe MIME type for a validated reference image."""
    suffix = Path(path).suffix.lower()
    return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}[suffix]
