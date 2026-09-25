"""Blender 4.5+ extension preference mapping.

Keeping this in the top-level package ensures the identifier is the extension
package (`bl_ext.<repo>.ai_3d_generator`), not the preferences submodule.
"""
from __future__ import annotations

from typing import Any

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import AddonPreferences

from .. import __package__ as base_package
from ..core.config import default_config
from ..core.constants import SUPPORTED_FORMATS
from ..providers.registry import PROVIDERS


def _provider_items(self: Any, context: Any) -> list[tuple[str, str, str]]:
    return [(key, cls.display_name, key) for key, cls in PROVIDERS.items()]


class AI3D_AddonPreferences(AddonPreferences):
    """Persistent provider and generation settings."""

    bl_idname = base_package

    provider: EnumProperty(name="Default Provider", items=_provider_items)
    api_base_url: StringProperty(name="API Base URL", default="http://127.0.0.1:8000")
    api_key: StringProperty(name="API Key", default="", subtype='PASSWORD')
    api_key_env: StringProperty(name="API Key Environment Variable", default="")
    requires_api_key: BoolProperty(name="Requires API Key", default=False)
    default_model: StringProperty(name="Default Model", default="default")
    default_quality: EnumProperty(name="Default Quality", items=[("draft", "Draft", ""), ("standard", "Standard", ""), ("high", "High", "")], default="standard")
    default_output_format: EnumProperty(name="Default Output Format", items=[(fmt, fmt.upper(), "") for fmt in SUPPORTED_FORMATS], default="glb")
    cache_dir: StringProperty(name="Cache Directory", default="", subtype='DIR_PATH')
    timeout: FloatProperty(name="Request Timeout", default=60.0, min=1.0, max=600.0)
    poll_interval: FloatProperty(name="Polling Interval", default=2.0, min=0.5, max=120.0)
    job_timeout: FloatProperty(name="Job Timeout", default=900.0, min=10.0, max=86400.0)
    debug_mode: BoolProperty(name="Debug Mode", default=False)
    auto_import: BoolProperty(name="Auto Import", default=True)
    auto_center: BoolProperty(name="Auto Center", default=True)
    auto_scale: BoolProperty(name="Auto Scale", default=True)
    generate_materials: BoolProperty(name="Generate Materials", default=True)
    generate_textures: BoolProperty(name="Generate Textures", default=True)
    generate_path: StringProperty(name="Generate Endpoint", default="/generate")
    status_path_template: StringProperty(name="Status Endpoint", default="/jobs/{job_id}")
    cancel_path_template: StringProperty(name="Cancel Endpoint", default="/jobs/{job_id}/cancel")
    auth_header: StringProperty(name="Auth Header", default="Authorization")
    auth_prefix: StringProperty(name="Auth Prefix", default="Bearer ")
    custom_headers: StringProperty(name="Custom Headers JSON", default="{}")
    request_payload: StringProperty(name="Request Payload JSON", default="{}")
    job_id_path: StringProperty(name="Job ID Path", default="id")
    status_path_value: StringProperty(name="Status Path", default="status")
    progress_path: StringProperty(name="Progress Path", default="progress")
    output_url_path: StringProperty(name="Output URL Path", default="download_url")
    error_path: StringProperty(name="Error Path", default="error")
    download_host_allowlist: StringProperty(name="Download Host Allowlist", default="", description="Optional comma-separated provider/CDN host names")
    max_asset_size_mb: IntProperty(name="Max Asset Size MB", default=512, min=1, max=4096)

    def draw(self, context: Any) -> None:
        layout = self.layout
        layout.label(text="Text-to-3D Provider Settings", icon='WORLD')
        column = layout.column(align=True)
        column.prop(self, "provider")
        column.prop(self, "api_base_url")
        column.prop(self, "api_key")
        column.prop(self, "api_key_env")
        column.prop(self, "requires_api_key")
        box = layout.box()
        box.label(text="Endpoint Mappings")
        for name in ("generate_path", "status_path_template", "cancel_path_template", "job_id_path", "status_path_value", "progress_path", "output_url_path", "error_path", "download_host_allowlist"):
            box.prop(self, name)
        box = layout.box()
        box.label(text="Request Settings")
        for name in ("auth_header", "auth_prefix", "custom_headers", "request_payload"):
            box.prop(self, name)
        box = layout.box()
        box.label(text="Generation Defaults")
        for name in ("default_model", "default_quality", "default_output_format", "cache_dir", "timeout", "poll_interval", "job_timeout", "max_asset_size_mb"):
            box.prop(self, name)
        box = layout.box()
        box.label(text="Post-Processing Defaults")
        for name in ("auto_import", "auto_center", "auto_scale", "generate_materials", "generate_textures", "debug_mode"):
            box.prop(self, name)
        if self.debug_mode:
            box.label(text="API keys and authorization headers are redacted", icon='LOCKED')

    def config_dict(self) -> dict[str, Any]:
        """Return non-secret runtime configuration; credential values never pass through."""
        values = default_config()
        values.update({
            "provider": self.provider,
            "base_url": self.api_base_url,
            "api_key_env": self.api_key_env,
            "requires_api_key": self.requires_api_key,
            "model": self.default_model,
            "quality": self.default_quality,
            "output_format": self.default_output_format,
            "cache_dir": self.cache_dir,
            "timeout": self.timeout,
            "poll_interval": self.poll_interval,
            "job_timeout": self.job_timeout,
            "debug": self.debug_mode,
            "auto_import": self.auto_import,
            "auto_center": self.auto_center,
            "auto_scale": self.auto_scale,
            "generate_materials": self.generate_materials,
            "generate_textures": self.generate_textures,
            "generate_path": self.generate_path,
            "status_path": self.status_path_template,
            "cancel_path": self.cancel_path_template,
            "auth_header": self.auth_header,
            "auth_prefix": self.auth_prefix,
            "headers": {"<redacted>": "<redacted>"},
            "request_payload": "<redacted>",
            "job_id_path": self.job_id_path,
            "status_path_value": self.status_path_value,
            "progress_path": self.progress_path,
            "output_url_path": self.output_url_path,
            "error_path": self.error_path,
            "download_host_allowlist": self.download_host_allowlist,
            "max_asset_size_mb": self.max_asset_size_mb,
        })
        return values


def _safe_unregister_class(cls: Any) -> None:
    if getattr(cls, "is_registered", False):
        bpy.utils.unregister_class(cls)


def register() -> None:
    if getattr(AI3D_AddonPreferences, "is_registered", False):
        return
    bpy.utils.register_class(AI3D_AddonPreferences)


def unregister() -> None:
    _safe_unregister_class(AI3D_AddonPreferences)
