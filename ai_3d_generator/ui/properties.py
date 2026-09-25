"""Blender extension property groups and dynamic enum callbacks."""
from __future__ import annotations

from typing import Any

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import PropertyGroup

from ..core.constants import SUPPORTED_FORMATS
from ..providers.registry import PROVIDERS


def _provider_items(self: Any, context: Any) -> list[tuple[str, str, str]]:
    return [
        (key, cls.display_name, (cls.__doc__ or "3D generation provider").strip().splitlines()[0])
        for key, cls in PROVIDERS.items()
    ]


def _template_items(self: Any, context: Any) -> list[tuple[str, str, str]]:
    from ..services.prompt_service import PromptTemplates

    return [
        (template.id, template.name, template.description or template.name)
        for template in PromptTemplates.defaults().values()
    ]


class AI3D_GeneratedHistoryItem(PropertyGroup):
    job_id: StringProperty(name="Job ID")
    negative_prompt: StringProperty(name="Negative Prompt", default="")
    timestamp: StringProperty(name="Timestamp")
    prompt: StringProperty(name="Prompt")
    provider: StringProperty(name="Provider")
    model: StringProperty(name="Model")
    quality: StringProperty(name="Quality")
    output_format: StringProperty(name="Format", default="glb")
    file_path: StringProperty(name="File")
    status: StringProperty(name="Status")
    error: StringProperty(name="Error")
    generation_mode: StringProperty(name="Generation Mode", default="text_to_3d")
    image_path: StringProperty(name="Reference Image", default="")
    style: StringProperty(name="Style", default="realistic")
    polygon_target: IntProperty(name="Polygon Target", default=50000, min=100, max=10000000)
    topology_preference: StringProperty(name="Topology", default="balanced")
    texture_resolution: IntProperty(name="Texture Resolution", default=2048, min=256, max=8192)
    auto_uv: BoolProperty(name="Auto UV", default=False)
    generate_materials: BoolProperty(name="Generate Materials", default=True)
    generate_textures: BoolProperty(name="Generate Textures", default=True)
    prompt_enhanced: BoolProperty(name="Prompt Enhanced", default=False)
    original_prompt: StringProperty(name="Original Prompt", default="")


class AI3D_Settings(PropertyGroup):
    """Transient UI state stored on Scene and mirrored by Preferences."""

    prompt: StringProperty(name="Prompt", default="", description="Describe the 3D object to generate")
    provider: EnumProperty(name="Provider", items=_provider_items, default="mock", description="Registered provider identifier")
    model: StringProperty(name="Model", default="default", description="Provider model identifier")
    negative_prompt: StringProperty(name="Negative Prompt", default="")
    quality: EnumProperty(name="Quality", items=[("draft", "Draft", "Fast preview"), ("standard", "Standard", "Balanced quality"), ("high", "High", "Highest supported quality")])
    output_format: EnumProperty(name="Output Format", items=[(fmt, fmt.upper(), f"Import {fmt.upper()}") for fmt in SUPPORTED_FORMATS])
    style: StringProperty(name="Style", default="realistic")
    polygon_target: IntProperty(name="Polygon Target", default=50000, min=100, max=10000000)
    generate_materials: BoolProperty(name="Generate Materials", default=True)
    generate_textures: BoolProperty(name="Generate Textures", default=True)
    topology_preference: EnumProperty(name="Topology", items=[("clean", "Clean", "Prefer clean topology"), ("balanced", "Balanced", "Balanced topology preference"), ("dense", "Dense", "Prefer dense topology")], default="balanced")
    texture_resolution: IntProperty(name="Texture Resolution", default=2048, min=256, max=8192)
    auto_uv: BoolProperty(name="Auto UV", default=False)
    generation_mode: EnumProperty(name="Generation Mode", items=[("text_to_3d", "Text → 3D", "Generate from a text prompt"), ("image_to_3d", "Image → 3D", "Generate from a reference image")], default="text_to_3d")
    image_path: StringProperty(name="Reference Image", default="", subtype='FILE_PATH')
    reference_image: PointerProperty(name="Reference Image Preview", type=bpy.types.Image)
    prompt_template: EnumProperty(name="Prompt Template", items=_template_items, default="realistic_prop")
    enhance_prompt: BoolProperty(name="Enhance Prompt", default=False)
    enhanced_prompt: StringProperty(name="Enhanced Prompt", default="")
    original_prompt: StringProperty(name="Original Prompt", default="")
    library_dir: StringProperty(name="AI3D Library Directory", default="", subtype='DIR_PATH')
    favorite_filter: BoolProperty(name="Favorites Only", default=False)
    library_filter: StringProperty(name="Library Filter", default="")
    batch_prompts: StringProperty(name="Batch Prompts", default="", description="One prompt per line")
    batch_running: BoolProperty(name="Batch Running", default=False)
    batch_paused: BoolProperty(name="Batch Paused", default=False)
    batch_concurrency: IntProperty(name="Batch Concurrency", default=1, min=1, max=8)
    prompt_enhanced: BoolProperty(name="Prompt Enhanced", default=False)
    auto_center: BoolProperty(name="Auto Center", default=True)
    auto_scale: BoolProperty(name="Auto Scale", default=True)
    smooth_shading: BoolProperty(name="Smooth Shading", default=False)
    generate_collision_mesh: BoolProperty(name="Generate Collision Mesh", default=False)
    delete_previous: BoolProperty(name="Delete Previous Generated Object", default=False)
    auto_import: BoolProperty(name="Import Asset", default=True)
    location_mode: EnumProperty(name="Location", items=[("CURSOR", "3D Cursor", "Place at the 3D cursor"), ("ORIGIN", "World Origin", "Place at the world origin"), ("COLLECTION", "Active Collection", "Place in the active collection")])
    status: StringProperty(name="Status", default="Ready")
    error_message: StringProperty(name="Error", default="")
    progress: FloatProperty(name="Progress", default=0.0, min=0.0, max=1.0)
    current_job_id: StringProperty(name="Current Job ID", default="")
    output_path: StringProperty(name="Output Path", default="")
    timer_running: BoolProperty(name="Timer Running", default=False)
    batch_current_job_id: StringProperty(name="Batch Current Job ID", default="")
    last_export_path: StringProperty(name="Last Export Path", default="", subtype='FILE_PATH')
    history: CollectionProperty(type=AI3D_GeneratedHistoryItem)
    history_index: IntProperty(name="History Index", default=-1)
    api_base_url: StringProperty(name="Runtime Provider URL", default="", options={'HIDDEN'})
    max_asset_size_mb: IntProperty(name="Max Asset Size MB", default=512, min=1, max=4096)
    poll_interval: FloatProperty(name="Polling Interval", default=2.0, min=0.5, max=120.0)
    timeout: FloatProperty(name="Request Timeout", default=60.0, min=1.0, max=600.0)
    job_timeout: FloatProperty(name="Job Timeout", default=900.0, min=10.0, max=86400.0)
    cache_dir: StringProperty(name="Cache Directory", default="", subtype='DIR_PATH')
    debug_mode: BoolProperty(name="Debug Mode", default=False)


classes = (AI3D_GeneratedHistoryItem, AI3D_Settings)
_REGISTERED = False
_SCENE_POINTER_OWNED = False


def register() -> None:
    global _REGISTERED, _SCENE_POINTER_OWNED
    if _REGISTERED:
        return
    registered: list[Any] = []
    installed_scene_pointer = False
    try:
        for cls in classes:
            if not getattr(cls, "is_registered", False):
                bpy.utils.register_class(cls)
                registered.append(cls)
        if not hasattr(bpy.types.Scene, "ai3d"):
            bpy.types.Scene.ai3d = PointerProperty(type=AI3D_Settings)
            installed_scene_pointer = True
    except Exception:
        _SCENE_POINTER_OWNED = False
        if installed_scene_pointer and hasattr(bpy.types.Scene, "ai3d"):
            del bpy.types.Scene.ai3d
        for cls in reversed(registered):
            _safe_unregister_class(cls)
        _REGISTERED = False
        raise
    _SCENE_POINTER_OWNED = installed_scene_pointer
    _REGISTERED = True


def _safe_unregister_class(cls: Any) -> None:
    if getattr(cls, "is_registered", False):
        bpy.utils.unregister_class(cls)


def unregister() -> None:
    global _REGISTERED, _SCENE_POINTER_OWNED
    if not _REGISTERED:
        return
    if _SCENE_POINTER_OWNED and hasattr(bpy.types.Scene, "ai3d"):
        del bpy.types.Scene.ai3d
    for cls in reversed(classes):
        _safe_unregister_class(cls)
    _SCENE_POINTER_OWNED = False
    _REGISTERED = False
