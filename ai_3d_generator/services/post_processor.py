"""Optional post-processing operations on imported generated objects."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

import bpy


@dataclass
class PostProcessOptions:
    smooth_shading: bool = False
    recalculate_normals: bool = True
    remove_tiny_objects: bool = False
    tiny_object_size: float = 0.001
    center_origin: bool = False
    apply_transforms: bool = False
    normalize_scale: bool = False
    decimate_ratio: float = 1.0
    voxel_remesh: float = 0.0
    cleanup_materials: bool = False


class PostProcessor:
    """Apply opt-in operations without destroying the imported hierarchy."""

    def __init__(self, context: Any = None) -> None:
        self.context = context or bpy.context

    def process(self, objects: Sequence[Any], options: Optional[PostProcessOptions] = None) -> str:
        options = options or PostProcessOptions()
        meshes = [obj for obj in objects if obj.type == 'MESH']
        if options.remove_tiny_objects:
            for obj in list(meshes):
                if _dimensions(obj).length <= options.tiny_object_size:
                    bpy.data.objects.remove(obj, do_unlink=True)
            meshes = [obj for obj in objects if obj.type == 'MESH']
        if options.smooth_shading:
            for obj in meshes:
                for polygon in obj.data.polygons:
                    polygon.use_smooth = True
        if options.recalculate_normals and meshes:
            _recalculate_normals(meshes)
        if options.center_origin and meshes:
            _center_group(meshes)
        if options.normalize_scale and meshes:
            _normalize_group(meshes)
        if options.apply_transforms and meshes:
            _apply_group(meshes)
        if options.decimate_ratio < 1.0 and meshes:
            for obj in meshes:
                modifier = obj.modifiers.new("AI3D Decimate", 'DECIMATE')
                modifier.ratio = max(0.01, min(1.0, options.decimate_ratio))
        if options.voxel_remesh > 0.0 and meshes:
            for obj in meshes:
                modifier = obj.modifiers.new("AI3D Voxel Remesh", 'REMESH')
                modifier.mode = 'VOXEL'
                modifier.voxel_size = options.voxel_remesh
        if options.cleanup_materials and meshes:
            _cleanup_materials(meshes)
        return "Post-processing applied."


def _dimensions(obj: Any) -> Any:
    from mathutils import Vector

    return Vector((obj.dimensions.x, obj.dimensions.y, obj.dimensions.z)).length


def _center_group(objects: Sequence[Any]) -> None:
    from mathutils import Vector

    points = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
    if not points:
        return
    center = sum(points, Vector((0.0, 0.0, 0.0))) / len(points)
    for obj in objects:
        obj.location -= center


def _normalize_group(objects: Sequence[Any]) -> None:
    from mathutils import Vector

    points = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
    if not points:
        return
    heights = [point.z for point in points]
    height = max(heights) - min(heights)
    if height <= 1e-6:
        return
    root = next((obj for obj in objects if obj.parent is None), objects[0])
    root.scale *= 2.0 / height


def _apply_group(objects: Sequence[Any]) -> None:
    active = bpy.context.view_layer.objects.active
    for obj in objects:
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        try:
            bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
        except Exception:
            pass
        obj.select_set(False)
    bpy.context.view_layer.objects.active = active


def _cleanup_materials(objects: Sequence[Any]) -> None:
    for obj in objects:
        for material in getattr(obj.data, "materials", ()):
            if material is not None and material.users == 0:
                bpy.data.materials.remove(material)


def _recalculate_normals(objects: Sequence[Any]) -> None:
    active = bpy.context.view_layer.objects.active
    for obj in objects:
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        try:
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.mesh.normals_make_consistent(inside=False)
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except Exception:
                pass
        obj.select_set(False)
    bpy.context.view_layer.objects.active = active
