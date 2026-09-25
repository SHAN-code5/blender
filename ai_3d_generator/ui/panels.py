"""Blender 3D View workspace panels with honest Phase 3 sections."""
from __future__ import annotations

from typing import Any

import bpy
from bpy.types import Panel

from .runtime_phase3 import library_for_props
from .operators_phase3 import BATCH


class _SectionHeader:
    """Consistent section header helper using verified Blender icons."""

    @staticmethod
    def draw(layout: bpy.types.UILayout, title: str, icon: str) -> bpy.types.UILayout:
        box = layout.box()
        box.label(text=title, icon=icon)
        return box


def _library_entries(props: Any, context: Any = None) -> list[Any]:
    query = str(getattr(props, "library_filter", "") or "").strip().lower()
    favorites_only = bool(getattr(props, "favorite_filter", False))
    entries = library_for_props(props, context).list_assets()
    return [
        entry for entry in entries
        if (not favorites_only or entry.favorite)
        and (not query or query in (entry.name + " " + " ".join(entry.tags)).lower())
    ][:50]


class AI3D_PT_create(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI 3D"
    bl_parent_id = "AI3D_PT_main"
    bl_label = "CREATE"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        props = context.scene.ai3d
        layout.prop(props, "generation_mode", text="Mode")
        layout.prop(props, "prompt", text="Prompt")
        if props.generation_mode == "image_to_3d":
            row = layout.row(align=True)
            row.prop(props, "image_path", text="Reference")
            row.operator("ai3d.load_reference", text="", icon='FILE_IMAGE')
            if props.reference_image:
                preview = layout.row(align=True)
                preview.template_image(props.reference_image, compact=True)
        else:
            row = layout.row(align=True)
            row.prop(props, "prompt_template")
            row.operator("ai3d.enhance_prompt", text="ENHANCE", icon='TEXT')
        if props.prompt_enhanced:
            layout.prop(props, "enhanced_prompt", text="Enhanced")
            layout.label(text="Review or edit before generation", icon='INFO')
        layout.prop(props, "negative_prompt", text="Negative")
        row = layout.row(align=True)
        row.prop(props, "provider", text="Provider")
        row.prop(props, "model", text="Model")
        row = layout.row(align=True)
        row.prop(props, "quality")
        row.prop(props, "style")
        row.prop(props, "output_format")
        if props.generation_mode == "image_to_3d" and props.provider != "mock":
            layout.label(text="Selected provider has no verified image upload adapter", icon='INFO')
        if props.timer_running:
            layout.operator("ai3d.cancel", text="CANCEL JOB", icon='CANCEL')
        else:
            layout.operator("ai3d.generate", text="GENERATE", icon='SHADERFX')
        if props.batch_running:
            layout.label(text="Batch in progress", icon='TIME')
        layout.progress(factor=props.progress, text=f"{props.progress * 100:.0f}%")
        if props.status:
            layout.label(text=props.status)
        if props.error_message:
            layout.label(text=props.error_message, icon='ERROR')


class AI3D_PT_advanced(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI 3D"
    bl_parent_id = "AI3D_PT_main"
    bl_label = "ADVANCED"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        props = context.scene.ai3d
        layout.prop(props, "polygon_target")
        layout.prop(props, "topology_preference")
        layout.prop(props, "texture_resolution")
        layout.prop(props, "auto_uv")
        layout.prop(props, "generate_materials")
        layout.prop(props, "generate_textures")
        layout.prop(props, "auto_center")
        layout.prop(props, "auto_scale")
        layout.prop(props, "smooth_shading")
        layout.prop(props, "auto_import")
        layout.prop(props, "location_mode", text="Place At")
        layout.prop(props, "delete_previous")


class AI3D_PT_batch(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI 3D"
    bl_parent_id = "AI3D_PT_main"
    bl_label = "BATCH"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        props = context.scene.ai3d
        layout.prop(props, "batch_prompts", text="One prompt per line")
        if props.batch_running:
            row = layout.row(align=True)
            if props.batch_paused:
                row.operator("ai3d.batch_resume", text="RESUME", icon='PLAY')
            else:
                row.operator("ai3d.batch_pause", text="PAUSE", icon='PAUSE')
            layout.operator("ai3d.batch_cancel", text="CANCEL BATCH", icon='CANCEL')
            layout.label(text=f"Active: {props.batch_current_job_id or 'waiting'}")
        else:
            layout.operator("ai3d.add_batch", text="START BATCH", icon='PLAY')
        if BATCH.queue is not None:
            for item in BATCH.queue.snapshot():
                row = layout.row(align=True)
                row.label(text=f"{item.job_id}: {item.state}")
                if item.state in {"failed", "cancelled", "timeout"}:
                    op = row.operator("ai3d.batch_retry", text="", icon='FILE_REFRESH')
                    op.job_id = item.job_id
                elif item.state != "processing":
                    op = row.operator("ai3d.batch_delete", text="", icon='X')
                    op.job_id = item.job_id
                if item.job_id in BATCH.queue.pending_job_ids():
                    op = row.operator("ai3d.batch_move_up", text="", icon='TRIA_UP')
                    op.job_id = item.job_id
                    op = row.operator("ai3d.batch_move_down", text="", icon='TRIA_DOWN')
                    op.job_id = item.job_id
            if not BATCH.active:
                layout.operator("ai3d.batch_clear", text="CLEAR QUEUE", icon='TRASH')


class AI3D_PT_history(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI 3D"
    bl_parent_id = "AI3D_PT_main"
    bl_label = "HISTORY"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        props = context.scene.ai3d
        for index, item in enumerate(props.history):
            row = layout.row(align=True)
            label = item.prompt[:28] + ("…" if len(item.prompt) > 28 else "")
            row.label(text=f"{index + 1}. {label or 'Image reference'}")
            if item.file_path:
                op = row.operator("ai3d.import", text="", icon='IMPORT')
                op.filepath = item.file_path
            op = row.operator("ai3d.retry", text="", icon='FILE_REFRESH')
            op.index = index
            if len(props.history) > 1:
                op = row.operator("ai3d.delete_history_entry", text="", icon='X')
                op.index = index
        if props.history:
            layout.operator("ai3d.clear_history", text="CLEAR HISTORY", icon='TRASH')
        if props.output_path:
            layout.operator("ai3d.import", text="IMPORT LAST ASSET", icon='IMPORT').filepath = props.output_path
            layout.operator("ai3d.export_asset", text="EXPORT LAST ASSET", icon='EXPORT')
        layout.operator("ai3d.delete_generated", text="DELETE GENERATED OBJECTS", icon='TRASH')


class AI3D_PT_library(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI 3D"
    bl_parent_id = "AI3D_PT_main"
    bl_label = "ASSET LIBRARY"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        props = context.scene.ai3d
        layout.prop(props, "library_dir")
        layout.prop(props, "library_filter", text="Filter")
        layout.prop(props, "favorite_filter", text="Favorites only")
        try:
            entries = _library_entries(props, context)
        except Exception as exc:
            layout.label(text=f"Library unavailable: {exc}", icon='ERROR')
            return
        if not entries:
            layout.label(text="No matching assets", icon='INFO')
            return
        for entry in entries:
            row = layout.row(align=True)
            row.label(text=entry.name[:28] + ("…" if len(entry.name) > 28 else ""))
            op = row.operator("ai3d.favorite_asset", text="★" if entry.favorite else "☆", icon='HEART')
            op.asset_id = entry.id
            op = row.operator("ai3d.import_library_asset", text="", icon='IMPORT')
            op.asset_id = entry.id
            op = row.operator("ai3d.regenerate_library_asset", text="", icon='FILE_REFRESH')
            op.asset_id = entry.id
            op = row.operator("ai3d.rename_library_asset", text="", icon='FILE_TEXT')
            op.asset_id = entry.id
            op.name = entry.name
            op = row.operator("ai3d.delete_library_asset", text="", icon='X')
            op.asset_id = entry.id
            op.confirm = True


class AI3D_PT_settings(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI 3D"
    bl_parent_id = "AI3D_PT_main"
    bl_label = "SETTINGS"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        layout.label(text="Provider URL, credentials, and endpoints are managed in Preferences", icon='INFO')
        layout.operator("ai3d.open_settings", text="OPEN PREFERENCES", icon='PREFERENCES')
        layout.operator("ai3d.open_asset_folder", text="OPEN ASSET FOLDER", icon='FILE_FOLDER')
        layout.label(text="Unsupported provider modes stay unavailable", icon='INFO')


class AI3D_PT_main(Panel):
    bl_idname = "AI3D_PT_main"
    bl_label = "AI 3D WORKSPACE"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI 3D"

    def draw(self, context: bpy.types.Context) -> None:
        self.layout.label(text="Provider-independent asset workspace", icon='WORLD')


classes = (
    AI3D_PT_main,
    AI3D_PT_create,
    AI3D_PT_advanced,
    AI3D_PT_batch,
    AI3D_PT_history,
    AI3D_PT_library,
    AI3D_PT_settings,
)


def register() -> None:
    registered: list[Any] = []
    try:
        for cls in classes:
            if not getattr(cls, "is_registered", False):
                bpy.utils.register_class(cls)
                registered.append(cls)
    except Exception:
        for cls in reversed(registered):
            if getattr(cls, "is_registered", False):
                bpy.utils.unregister_class(cls)
        raise


def unregister() -> None:
    for cls in reversed(classes):
        if getattr(cls, "is_registered", False):
            bpy.utils.unregister_class(cls)
