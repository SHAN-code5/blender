"""Phase 3 UI runtime helpers: capabilities, image loading, and library access."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.capabilities import ProviderCapabilities
from ..core.errors import ValidationError
from ..providers.registry import get_provider_class
from ..services.library_service import AssetLibrary
from ..utils.images import validate_image_path


def provider_capabilities(provider_id: str, config: Any = None) -> ProviderCapabilities:
    """Instantiate a registered adapter and return explicit capabilities."""
    try:
        provider = get_provider_class(provider_id)(config)
    except Exception as exc:
        raise ValidationError(f"Could not load provider '{provider_id}'.") from exc
    hook = getattr(provider, "capabilities_for", None)
    return hook(config) if callable(hook) else provider.capabilities()


def load_reference_preview(image_path: str, context: Any) -> Any:
    """Validate and load a local image into bpy.data.images for UI preview."""
    from ..utils.images import image_mime_type, validate_image_path as _validate
    import bpy

    safe_path = _validate(image_path)
    image = bpy.data.images.load(safe_path, check_existing=True)
    image["ai3d_mime_type"] = image_mime_type(safe_path)
    return image


def library_for_props(props: Any, context: Any = None) -> AssetLibrary:
    configured = str(getattr(props, "library_dir", "") or "").strip()
    if configured:
        root = Path(configured).expanduser()
    else:
        from .runtime import _default_cache

        root = Path(_default_cache(props, context)) / "AI3D_Library"
    return AssetLibrary(root)
