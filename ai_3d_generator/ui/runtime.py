"""Blender 4.5+ extension preferences and runtime configuration bridge."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict

from ..core.errors import ValidationError
from ..core.models import ProviderConfig
from ..utils.files import is_within


def _default_cache(props: Any, context: Any = None) -> str:
    configured = str(getattr(props, "cache_dir", "") or "").strip()
    if configured:
        return configured
    if context is None:
        try:
            import bpy
            context = bpy.context
        except (ImportError, AttributeError, RuntimeError):
            context = None
    if context is not None:
        try:
            prefs = get_preferences(context)
        except (AttributeError, TypeError, KeyError, RuntimeError):
            prefs = None
        configured = str(getattr(prefs, "cache_dir", "") or "").strip()
        if configured:
            return configured
    try:
        import bpy

        resource = bpy.utils.user_resource("CONFIG", path="ai_3d_generator", create=True)
        if resource:
            return str(resource)
    except (ImportError, AttributeError, TypeError, OSError, RuntimeError):
        pass
    return str(Path(tempfile.gettempdir()) / "ai_3d_generator")


_HANDLER_REGISTERED = False


def register() -> None:
    global _HANDLER_REGISTERED
    try:
        import bpy

        if not _HANDLER_REGISTERED:
            bpy.app.handlers.load_post.append(sync_scene_from_preferences_no_args)
            _HANDLER_REGISTERED = True
    except (ImportError, AttributeError, RuntimeError):
        pass


def unregister() -> None:
    global _HANDLER_REGISTERED
    try:
        import bpy

        if _HANDLER_REGISTERED and sync_scene_from_preferences_no_args in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.remove(sync_scene_from_preferences_no_args)
        _HANDLER_REGISTERED = False
    except (ImportError, AttributeError, RuntimeError):
        _HANDLER_REGISTERED = False


def _package_name() -> str:
    """Return the top-level extension package, including a bl_ext namespace."""
    return __package__.rsplit(".ui", 1)[0]


def get_preferences(context: Any) -> Any | None:
    """Return the registered AddonPreferences object when available."""
    try:
        addon = context.preferences.addons.get(_package_name())
        return addon.preferences if addon is not None else None
    except (AttributeError, TypeError, KeyError):
        return None


def _parse_json_object(value: str, label: str) -> Dict[str, Any]:
    if not str(value or "").strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{label} must be valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValidationError(f"{label} must be a JSON object.")
    return parsed


def parse_headers(value: str) -> Dict[str, str]:
    """Validate custom headers before they reach urllib."""
    parsed = _parse_json_object(value, "Custom headers")
    headers: Dict[str, str] = {}
    for key, item in parsed.items():
        name = str(key)
        text = str(item)
        if not name or any(ord(char) < 0x20 or ord(char) == 0x7F for char in name + text):
            raise ValidationError("Custom headers contain invalid characters.")
        if name.lower() in {"host", "content-length", "transfer-encoding", "connection", "authorization", "proxy-authorization", "cookie", "set-cookie"}:
            raise ValidationError(f"Header '{name}' is managed by the HTTP client.")
        headers[name] = text
    return headers


def parse_allowlist(value: str) -> list[str]:
    """Parse an optional comma-separated host allowlist."""
    hosts = []
    for raw in str(value or "").split(","):
        host = raw.strip().lower().rstrip(".")
        if not host:
            continue
        if "/" in host or "@" in host or any(char in host for char in "\\?#"):
            raise ValidationError("Download allowlist entries must be host names.")
        hosts.append(host)
    return sorted(set(hosts))


def provider_config_from_preferences(context: Any, props: Any) -> ProviderConfig:
    """Build a runtime provider config, reading credentials only in memory."""
    prefs = get_preferences(context)
    if prefs is None:
        return ProviderConfig(
            model=str(getattr(props, "model", "default") or "default"),
            base_url="http://127.0.0.1:8000" if str(getattr(props, "provider", "mock")) == "mock" else "",
        )
    return ProviderConfig(
        base_url=str(prefs.api_base_url or "").strip(),
        api_key=str(prefs.api_key or ""),
        api_key_env=str(prefs.api_key_env or "").strip(),
        requires_api_key=bool(prefs.requires_api_key),
        model=str(props.model or prefs.default_model or "default").strip(),
        timeout=float(prefs.timeout),
        generate_path=str(prefs.generate_path or "/generate").strip(),
        status_path=str(prefs.status_path_template or "/jobs/{job_id}").strip(),
        cancel_path=str(prefs.cancel_path_template or "/jobs/{job_id}/cancel").strip(),
        auth_header=str(prefs.auth_header or "Authorization").strip(),
        auth_prefix=str(prefs.auth_prefix or ""),
        headers=parse_headers(str(prefs.custom_headers or "{}")),
        request_payload=_parse_json_object(str(prefs.request_payload or "{}"), "Request payload"),
        job_id_path=str(prefs.job_id_path or "id").strip(),
        status_path_value=str(prefs.status_path_value or "status").strip(),
        progress_path=str(prefs.progress_path or "progress").strip(),
        output_url_path=str(prefs.output_url_path or "download_url").strip(),
        error_path=str(prefs.error_path or "error").strip(),
        max_asset_size_mb=int(prefs.max_asset_size_mb),
        poll_interval=float(prefs.poll_interval),
        job_timeout=float(prefs.job_timeout),
        download_host_allowlist=parse_allowlist(str(prefs.download_host_allowlist or "")),
    )


def sync_scene_from_preferences_no_args() -> None:
    try:
        import bpy
        sync_scene_from_preferences(bpy.context)
    except (ImportError, AttributeError, RuntimeError, TypeError):
        pass


def sync_scene_from_preferences(context: Any) -> None:
    """Initialize non-secret Scene controls from persisted AddonPreferences."""
    prefs = get_preferences(context)
    if prefs is None or not hasattr(context.scene, "ai3d"):
        return
    props = context.scene.ai3d
    props.provider = str(prefs.provider or "mock")
    props.model = str(props.model or prefs.default_model or "default")
    props.quality = str(prefs.default_quality or "standard")
    props.output_format = str(prefs.default_output_format or "glb")
    props.cache_dir = str(prefs.cache_dir or "")
    props.timeout = float(prefs.timeout)
    props.poll_interval = float(prefs.poll_interval)
    props.job_timeout = float(prefs.job_timeout)
    props.max_asset_size_mb = int(prefs.max_asset_size_mb)
    props.auto_import = bool(prefs.auto_import)
    props.auto_center = bool(prefs.auto_center)
    props.auto_scale = bool(prefs.auto_scale)
    props.generate_materials = bool(prefs.generate_materials)
    props.generate_textures = bool(prefs.generate_textures)
    props.debug_mode = bool(prefs.debug_mode)


def is_generated_path(context: Any, path: str) -> bool:
    """Return whether a path is inside the configured generated cache root."""
    props = context.scene.ai3d
    root = Path(_default_cache(props, context)).expanduser()
    return is_within(Path(path).expanduser(), root)
