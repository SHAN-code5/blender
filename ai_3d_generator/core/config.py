"""Configuration defaults and validation independent of Blender."""
from __future__ import annotations

from typing import Any, Dict, List
import math

from .constants import QUALITY_LEVELS, SUPPORTED_FORMATS
from .errors import ValidationError
from ..utils import validation


def default_config() -> Dict[str, Any]:
    """Return safe defaults used by preferences and tests."""
    return {
        "provider": "mock",
        "base_url": "http://127.0.0.1:8000",
        "api_key": "",
        "api_key_env": "",
        "model": "default",
        "quality": "standard",
        "output_format": "glb",
        "cache_dir": "",
        "timeout": 60.0,
        "poll_interval": 2.0,
        "job_timeout": 900.0,
        "debug": False,
        "auto_import": True,
        "auto_center": True,
        "auto_scale": True,
        "generate_materials": True,
        "generate_textures": True,
        "max_asset_size_mb": 512,
    }


def validate_config(config: Dict[str, Any]) -> List[str]:
    """Return human-readable configuration errors without raising."""
    errors: List[str] = []
    provider = str(config.get("provider", ""))
    if not provider:
        errors.append("Choose a generation provider.")
    generation_mode = str(config.get("generation_mode", "text_to_3d"))
    prompt = config.get("prompt", None)
    if generation_mode == "text_to_3d" and prompt is None:
        errors.append("Prompt cannot be empty.")
    elif generation_mode == "text_to_3d" and not str(prompt).strip():
        errors.append("Prompt cannot be empty.")
    if generation_mode not in {"text_to_3d", "image_to_3d"}:
        errors.append("Generation mode must be Text → 3D or Image → 3D.")
    quality = str(config.get("quality", "standard"))
    if quality not in QUALITY_LEVELS:
        errors.append("Quality must be Draft, Standard, or High.")
    output_format = str(config.get("output_format", "glb")).lower().lstrip(".")
    if output_format not in SUPPORTED_FORMATS:
        errors.append("Output format is not supported.")
    polygon_target = config.get("polygon_target", 50000)
    try:
        if not 100 <= int(polygon_target) <= 10_000_000:
            errors.append("Polygon target must be between 100 and 10,000,000.")
    except (TypeError, ValueError):
        errors.append("Polygon target must be a whole number.")
    try:
        value = float(config.get("max_asset_size_mb", 512))
        if not math.isfinite(value) or not 1 <= value <= 4096:
            errors.append("max_asset_size_mb must be between 1 and 4096.")
    except (TypeError, ValueError):
        errors.append("max_asset_size_mb must be a number.")
    for key, lower, upper in (("timeout", 0.1, 600.0), ("poll_interval", 0.1, 120.0), ("job_timeout", 1.0, 86400.0)):
        try:
            value = float(config.get(key, 0))
            if not (value == value and value != float("inf") and value != float("-inf") and lower <= value <= upper):
                errors.append(f"{key} must be between {lower:g} and {upper:g}.")
        except (TypeError, ValueError):
            errors.append(f"{key} must be a number.")
    if provider not in {"mock", "local_api", "custom_api"}:
        return
    if provider != "mock":
        try:
            validation.validate_http_url(str(config.get("base_url", "")), "API Base URL")
        except ValidationError as exc:
            errors.append(exc.user_message())
    return errors


def require_valid_config(config: Dict[str, Any]) -> None:
    """Raise one configuration error when validation fails."""
    errors = validate_config(config)
    if errors:
        raise ValidationError("; ".join(errors))
