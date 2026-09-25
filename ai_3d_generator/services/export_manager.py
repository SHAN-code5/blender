"""Blender-native export dispatcher for generated assets."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ..core.errors import ExportError
from .export_service import validate_export_target


def export_objects(objects: Iterable[Any], filepath: str | Path, output_format: str, *, overwrite: bool = False) -> Path:
    """Export selected Blender objects without silently overwriting a file."""
    import bpy

    target = validate_export_target(filepath, output_format, overwrite)
    selected = [obj for obj in objects if getattr(obj, "name", "") in bpy.context.view_layer.objects]
    if not selected:
        raise ExportError("Select at least one object before exporting.")
    previous = bpy.context.view_layer.objects.active
    try:
        for obj in selected:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = selected[0]
        fmt = output_format.lower().lstrip(".")
        if fmt in {"glb", "gltf"}:
            bpy.ops.export_scene.gltf(filepath=str(target), export_format='GLB' if fmt == "glb" else 'GLTF_SEPARATE', use_selection=True)
        elif fmt == "obj":
            bpy.ops.wm.obj_export(filepath=str(target), export_selected_objects=True, forward_axis='NEGATIVE_Z', up_axis='Y')
        elif fmt == "stl":
            bpy.ops.wm.stl_export(filepath=str(target), export_selected_objects=True, apply_modifiers=True)
        elif fmt == "fbx":
            bpy.ops.export_scene.fbx(filepath=str(target), use_selection=True, path_mode='COPY', embed_textures=True)
        else:
            raise ExportError("Export format is not supported by this Blender build.")
    except ExportError:
        raise
    except Exception as exc:
        raise ExportError("Blender could not export the selected asset.", detail=str(exc)) from exc
    finally:
        for obj in selected:
            obj.select_set(False)
        bpy.context.view_layer.objects.active = previous
    if not target.is_file() or target.stat().st_size == 0:
        raise ExportError("Blender did not create a valid export file.")
    return target
