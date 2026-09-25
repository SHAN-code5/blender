"""Blender import manager for supported 3D formats.

The importer deliberately preserves the imported hierarchy and collection
structure. It only moves/relinks objects after the format-specific operator has
completed, which avoids assuming that all providers return one mesh.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

import bpy

from ..core.constants import GENERATED_COLLECTION_NAME, SUPPORTED_FORMATS
from ..core.errors import ImportError_
from ..core.models import ImportResult
from ..utils.validation import validate_asset_format


@dataclass
class ImportOptions:
    location_mode: str = "CURSOR"
    auto_center: bool = True
    auto_scale: bool = True
    smooth_shading: bool = False
    recalculate_normals: bool = True
    apply_transforms: bool = False
    target_height: float = 2.0
    preserve_hierarchy: bool = True


class ImportManager:
    """Import a validated local asset into a dedicated generated collection."""

    def __init__(self, context: Any = None) -> None:
        self.context = context or bpy.context

    def import_asset(self, filepath: str, asset_format: str = "", options: Optional[ImportOptions] = None) -> ImportResult:
        """Import one asset, preserving objects, materials, and hierarchy."""
        path = Path(filepath).expanduser().resolve()
        if not path.is_file():
            raise ImportError_("The generated asset file no longer exists.")
        fmt = validate_asset_format(asset_format or path.suffix, SUPPORTED_FORMATS)
        options = options or ImportOptions()
        _ensure_object_mode()
        before = _object_snapshot(self.context)
        try:
            _run_import_operator(fmt, path)
        except Exception as exc:
            raise ImportError_("Blender could not import the generated asset.", detail=str(exc)) from exc
        new_objects = [obj for obj in self.context.scene.objects if obj.name not in before]
        if not new_objects:
            raise ImportError_("Blender imported the file but created no scene objects.")
        collection = _generated_collection(self.context)
        _relink_objects(new_objects, collection)
        root = _root_object(new_objects)
        if options.auto_center:
            _center_origin(new_objects, _target_location(options))
        if options.auto_scale:
            _normalize_scale(new_objects, options.target_height)
        if options.smooth_shading:
            _smooth_meshes(new_objects)
        if options.recalculate_normals:
            _recalculate_normals(new_objects)
        if options.apply_transforms:
            _apply_transforms(new_objects)
        _make_paths_portable(new_objects)
        _tag_objects(new_objects, path)
        materials = sorted({mat.name for obj in new_objects for mat in getattr(obj.data, "materials", ()) if mat is not None})
        return ImportResult(
            file_path=str(path),
            object_names=[obj.name for obj in new_objects],
            collection_name=collection.name,
            root_object=root.name if root else "",
            imported_count=len(new_objects),
            materials=materials,
        )


def _ensure_object_mode() -> None:
    obj = bpy.context.object
    if obj is not None and obj.mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass


def _object_snapshot(context: Any) -> set[str]:
    return {obj.name for obj in context.scene.objects}


def _run_import_operator(fmt: str, path: Path) -> None:
    """Use the installed Blender importer, with a compatibility fallback."""
    if fmt in {"glb", "gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif fmt == "obj":
        bpy.ops.wm.obj_import(filepath=str(path), forward_axis='NEGATIVE_Z', up_axis='Y')
    elif fmt == "stl":
        bpy.ops.wm.stl_import(filepath=str(path))
    elif fmt == "fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    else:
        raise ImportError_(f"Unsupported asset format: {fmt}")


def _generated_collection(context: Any) -> Any:
    collection = bpy.data.collections.get(GENERATED_COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(GENERATED_COLLECTION_NAME)
    if collection.name not in context.scene.collection.children:
        context.scene.collection.children.link(collection)
    return collection


def _relink_objects(objects: Sequence[Any], collection: Any) -> None:
    for obj in objects:
        for old in list(obj.users_collection):
            old.objects.unlink(obj)
        collection.objects.link(obj)


def _root_object(objects: Sequence[Any]) -> Optional[Any]:
    for obj in objects:
        if obj.parent is None:
            return obj
    return objects[0] if objects else None


def _target_location(options: ImportOptions) -> Any:
    if options.location_mode == "ORIGIN":
        return (0.0, 0.0, 0.0)
    if options.location_mode == "COLLECTION":
        collection = getattr(bpy.context, "collection", None)
        return getattr(collection, "location", (0.0, 0.0, 0.0))
    return tuple(bpy.context.scene.cursor.location)


def _center_origin(objects: Sequence[Any], target: Any) -> None:
    points = [obj.matrix_world @ _bounds_center(obj) for obj in objects if obj.type == 'MESH']
    if not points:
        return
    center = sum((_vector(point) for point in points), _vector((0.0, 0.0, 0.0))) / len(points)
    offset = _vector(target) - center
    for obj in objects:
        obj.location += offset


def _normalize_scale(objects: Sequence[Any], target_height: float) -> None:
    meshes = [obj for obj in objects if obj.type == 'MESH']
    if not meshes or target_height <= 0:
        return
    min_z = min((obj.matrix_world @ _vector(corner))[2] for obj in meshes for corner in obj.bound_box)
    max_z = max((obj.matrix_world @ _vector(corner))[2] for obj in meshes for corner in obj.bound_box)
    height = max_z - min_z
    if height <= 1e-6:
        return
    root = _root_object(objects)
    if root is not None:
        root.scale *= target_height / height


def _smooth_meshes(objects: Sequence[Any]) -> None:
    for obj in objects:
        if obj.type == 'MESH':
            for polygon in obj.data.polygons:
                polygon.use_smooth = True


def _recalculate_normals(objects: Sequence[Any]) -> None:
    active = bpy.context.view_layer.objects.active
    for obj in objects:
        if obj.type != 'MESH':
            continue
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


def _apply_transforms(objects: Sequence[Any]) -> None:
    for obj in objects:
        if obj.type != 'MESH':
            continue
        old_active = bpy.context.view_layer.objects.active
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        try:
            bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
        finally:
            obj.select_set(False)
            bpy.context.view_layer.objects.active = old_active


def _bounds_center(obj: Any) -> Any:
    corners = [_vector(corner) for corner in obj.bound_box]
    return sum(corners, _vector((0.0, 0.0, 0.0))) / len(corners)


def _tag_objects(objects: Sequence[Any], path: Path) -> None:
    for obj in objects:
        obj["ai3d_generated"] = True
        obj["ai3d_source_file"] = str(path)
        obj["ai3d_imported_at"] = str(int(os.path.getmtime(path) * 1000))


def _make_paths_portable(objects: Sequence[Any]) -> None:
    for obj in objects:
        for material in getattr(getattr(obj, "data", None), "materials", ()):
            if material is None:
                continue
            node_tree = getattr(material, "node_tree", None)
            for node in node_tree.nodes if node_tree else ():
                if node.bl_idname == "ShaderNodeTexImage" and node.image:
                    node.image.pack()


def _vector(value: Sequence[float]) -> Any:
    from mathutils import Vector

    return Vector(value)
