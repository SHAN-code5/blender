"""Blender MCP Bridge.

A localhost socket server inside Blender that the Blender MCP server
(``blender-mcp-bridge``) talks to. Works as a Blender 4.2+ extension and as a
legacy add-on on Blender 3.0 - 4.1, in the UI and in background mode
(``blender -b --python runHeadless.py``).

Threading model: socket handler threads never touch ``bpy``. They queue each
request; the queue is drained on Blender's main thread by a ``bpy.app.timers``
callback (UI) or by the headless loop (background mode).

Protocol (one JSON object per line):
    request  {"id", "type", "params", "timeout", "token"}
    response {"id", "status": "success", "result"} or
             {"id", "status": "error", "message", "code"}
"""

bl_info = {
    "name": "Blender MCP Bridge",
    "author": "Blender MCP contributors",
    "version": (0, 3, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > MCP",
    "description": "Let MCP clients such as Claude inspect and edit your scene",
    "category": "Interface",
}

import base64
import contextlib
from array import array
import hmac
import io
import json
import math
import os
import queue
import re
import secrets
import shutil
import socket
import socketserver
import tempfile
import threading
import time
import traceback

import bpy  # must precede bmesh/mathutils when running as the standalone bpy module
import bmesh
from mathutils import Euler, Matrix, Vector

BRIDGE_VERSION = "0.3.0"
PROTOCOL_VERSION = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
DEFAULT_COMMAND_TIMEOUT = 30.0
MAX_COMMAND_TIMEOUT = 3600.0
MAX_REQUEST_BYTES = 16 * 1024 * 1024
IDLE_READ_TIMEOUT = 60.0
TIMER_INTERVAL = 0.02
DRAIN_BUDGET_SECONDS = 0.25
GPU_ENV = "BLENDER_MCP_GPU"
TOKEN_ENV = "BLENDER_MCP_TOKEN"
PACKAGE_SOURCE = "git+https://github.com/SHAN-code5/blender"

_ADDON_KEY = __package__ or __name__


class CommandError(Exception):
    """An anticipated failure reported back to the client."""

    def __init__(self, message, code="invalid_params"):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def _round(values, digits=4):
    return [round(float(v), digits) for v in values]


def _vector(value, size, name):
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise CommandError("%s must be a list of %d numbers" % (name, size))
    try:
        return [float(v) for v in value]
    except (TypeError, ValueError):
        raise CommandError("%s must contain only numbers" % name)


def _number(value, name, minimum=None, maximum=None, default=None):
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise CommandError("%s must be a number" % name)
    if minimum is not None and number < minimum:
        raise CommandError("%s must be >= %s" % (name, minimum))
    if maximum is not None and number > maximum:
        raise CommandError("%s must be <= %s" % (name, maximum))
    return number


def _string(value, name, required=False):
    if value is None or value == "":
        if required:
            raise CommandError("%s is required" % name)
        return None
    if not isinstance(value, str):
        raise CommandError("%s must be a string" % name)
    return value


def _get_object(name):
    name = _string(name, "name", required=True)
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise CommandError("object not found: %s" % name, code="not_found")
    return obj


def _json_default(value):
    try:
        return [float(v) for v in value]
    except Exception:
        return repr(value)


def _rotation_degrees(obj):
    return _round([math.degrees(a) for a in obj.matrix_basis.to_euler("XYZ")], 3)


def _set_rotation_degrees(obj, degrees):
    euler = Euler([math.radians(d) for d in degrees], "XYZ")
    mode = obj.rotation_mode
    if mode == "QUATERNION":
        obj.rotation_quaternion = euler.to_quaternion()
    elif mode == "AXIS_ANGLE":
        axis, angle = euler.to_quaternion().to_axis_angle()
        obj.rotation_axis_angle = [angle, axis.x, axis.y, axis.z]
    else:
        obj.rotation_euler = euler.to_matrix().to_euler(mode)


def _world_bbox(objects):
    points = []
    for obj in objects:
        matrix = obj.matrix_world
        points.extend(matrix @ Vector(corner) for corner in obj.bound_box)
    if not points:
        return None
    low = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return low, high


def _object_collections(obj):
    return [c.name for c in obj.users_collection]


def _target_collection(name):
    scene = bpy.context.scene
    if name:
        collection = bpy.data.collections.get(name)
        if collection is None:
            collection = bpy.data.collections.new(name)
            scene.collection.children.link(collection)
        return collection
    layer = getattr(bpy.context, "view_layer", None)
    active = getattr(layer, "active_layer_collection", None) if layer else None
    return active.collection if active is not None else scene.collection


def _operator(module, name):
    namespace = getattr(bpy.ops, module, None)
    if namespace is None or name not in dir(namespace):
        return None
    return getattr(namespace, name)


@contextlib.contextmanager
def _ui_context():
    """Give operators a window/area context when Blender has a UI."""
    if bpy.app.background or not hasattr(bpy.context, "temp_override"):
        yield
        return
    window_manager = bpy.context.window_manager
    window = bpy.context.window or (window_manager.windows[0] if window_manager.windows else None)
    if window is None:
        yield
        return
    area = next((a for a in window.screen.areas if a.type == "VIEW_3D"), None)
    region = next((r for r in area.regions if r.type == "WINDOW"), None) if area else None
    kwargs = {"window": window, "screen": window.screen}
    if area is not None:
        kwargs["area"] = area
    if region is not None:
        kwargs["region"] = region
    with bpy.context.temp_override(**kwargs):
        yield


def _undo_push(message):
    """Make each MCP change its own undo step (best effort; no-op headless)."""
    if bpy.app.background:
        return
    try:
        with _ui_context():
            bpy.ops.ed.undo_push(message="MCP: " + message)
    except Exception:
        pass


def _summary(obj):
    return {
        "name": obj.name,
        "type": obj.type,
        "location": _round(obj.location, 3),
        "rotation_degrees": _rotation_degrees(obj),
        "scale": _round(obj.scale, 3),
        "dimensions": _round(obj.dimensions, 3),
        "parent": obj.parent.name if obj.parent else None,
        "collections": _object_collections(obj),
        "visible": obj.visible_get(),
        "materials": [s.material.name for s in obj.material_slots if s.material],
    }


# --------------------------------------------------------------------------
# Commands (main thread only)
# --------------------------------------------------------------------------

def cmd_ping(params):
    return {"pong": True, "protocol": PROTOCOL_VERSION, "bridge_version": BRIDGE_VERSION}


def cmd_get_bridge_status(params):
    return {
        "bridge_version": BRIDGE_VERSION,
        "protocol": PROTOCOL_VERSION,
        "blender_version": bpy.app.version_string,
        "blender_version_tuple": list(bpy.app.version),
        "background": bool(bpy.app.background),
        "file": bpy.data.filepath or None,
        "unsaved_changes": bool(bpy.data.is_dirty),
        "code_execution_allowed": bool(_state["allow_code"]),
        "auth_required": bool(_state["token"]),
        "gpu_rendering": _gpu_rendering_available(),
        "address": "%s:%s" % (_state["host"], _state["port"]),
        "commands": sorted(COMMANDS),
    }


def cmd_get_scene_info(params):
    limit = int(_number(params.get("limit"), "limit", 1, 5000, 200))
    scene = bpy.context.scene
    objects = list(scene.objects)
    render = scene.render
    world = scene.world
    return {
        "scene": scene.name,
        "file": bpy.data.filepath or None,
        "frame": {"current": scene.frame_current, "start": scene.frame_start,
                  "end": scene.frame_end, "fps": render.fps},
        "units": {"system": scene.unit_settings.system, "scale": scene.unit_settings.scale_length},
        "render": {"engine": render.engine, "resolution": [render.resolution_x, render.resolution_y],
                   "percentage": render.resolution_percentage},
        "camera": scene.camera.name if scene.camera else None,
        "world": world.name if world else None,
        "collections": [
            {"name": c.name, "objects": len(c.objects), "children": [ch.name for ch in c.children]}
            for c in bpy.data.collections
        ],
        "object_count": len(objects),
        "objects": [_summary(o) for o in objects[:limit]],
        "truncated": len(objects) > limit,
    }


def cmd_list_objects(params):
    object_type = _string(params.get("object_type"), "object_type")
    collection = _string(params.get("collection"), "collection")
    objects = bpy.context.scene.objects
    if collection:
        source = bpy.data.collections.get(collection)
        if source is None:
            raise CommandError("collection not found: %s" % collection, code="not_found")
        objects = source.all_objects
    wanted = object_type.upper() if object_type else None
    return [
        {"name": o.name, "type": o.type, "location": _round(o.location, 3)}
        for o in objects if wanted is None or o.type == wanted
    ]


def cmd_get_object_info(params):
    obj = _get_object(params.get("name"))
    bpy.context.view_layer.update()
    info = _summary(obj)
    bbox = _world_bbox([obj])
    info["world_bounding_box"] = {"min": _round(bbox[0], 4), "max": _round(bbox[1], 4)} if bbox else None
    info["data"] = obj.data.name if obj.data else None
    info["modifiers"] = [{"name": m.name, "type": m.type} for m in obj.modifiers]
    info["constraints"] = [{"name": c.name, "type": c.type} for c in obj.constraints]
    info["children"] = [child.name for child in obj.children]
    info["animated"] = bool(obj.animation_data and obj.animation_data.action)
    info["custom_properties"] = {
        key: obj[key] for key in obj.keys()
        if not key.startswith("_") and isinstance(obj[key], (int, float, str, bool))
    }
    if obj.type == "MESH":
        mesh = obj.data
        info["mesh"] = {
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "faces": len(mesh.polygons),
            "triangles": sum(len(p.vertices) - 2 for p in mesh.polygons),
            "uv_layers": [uv.name for uv in mesh.uv_layers],
        }
    elif obj.type == "LIGHT":
        info["light"] = {"type": obj.data.type, "energy": obj.data.energy, "color": _round(obj.data.color, 3)}
    elif obj.type == "CAMERA":
        info["camera"] = {"type": obj.data.type, "lens": obj.data.lens,
                          "is_scene_camera": bpy.context.scene.camera == obj}
    return info


MESH_KINDS = ("cube", "uv_sphere", "ico_sphere", "cylinder", "cone", "torus", "plane",
              "circle", "grid", "monkey")
LIGHT_KINDS = {"light_point": "POINT", "light_sun": "SUN", "light_spot": "SPOT", "light_area": "AREA"}
DEFAULT_LIGHT_ENERGY = {"POINT": 1000.0, "SUN": 3.0, "SPOT": 1000.0, "AREA": 300.0}
OBJECT_KINDS = MESH_KINDS + tuple(LIGHT_KINDS) + ("camera", "empty", "text")


def _bmesh_call(func, bm, radius_args, **kwargs):
    """Call a bmesh.ops creator with radius args, falling back to Blender 2.9x diameter args."""
    try:
        return func(bm, **dict(kwargs, **radius_args))
    except TypeError:
        diameters = {k.replace("radius", "diameter"): v * 2 for k, v in radius_args.items()}
        return func(bm, **dict(kwargs, **diameters))


def _build_mesh(kind, size, segments):
    half = size / 2.0
    bm = bmesh.new()
    # bmesh's calc_uvs only fills an existing UV layer; it never creates one.
    bm.loops.layers.uv.new("UVMap")
    try:
        if kind == "cube":
            bmesh.ops.create_cube(bm, size=size, calc_uvs=True)
        elif kind == "uv_sphere":
            _bmesh_call(bmesh.ops.create_uvsphere, bm, {"radius": half},
                        u_segments=segments, v_segments=max(3, segments // 2), calc_uvs=True)
        elif kind == "ico_sphere":
            _bmesh_call(bmesh.ops.create_icosphere, bm, {"radius": half},
                        subdivisions=max(1, min(6, segments // 16 + 1)), calc_uvs=True)
        elif kind in ("cylinder", "cone"):
            radius2 = half if kind == "cylinder" else 0.0
            _bmesh_call(bmesh.ops.create_cone, bm, {"radius1": half, "radius2": radius2},
                        cap_ends=True, cap_tris=False, segments=segments, depth=size, calc_uvs=True)
        elif kind == "plane":
            bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=half, calc_uvs=True)
        elif kind == "grid":
            bmesh.ops.create_grid(bm, x_segments=segments, y_segments=segments, size=half, calc_uvs=True)
        elif kind == "circle":
            _bmesh_call(bmesh.ops.create_circle, bm, {"radius": half},
                        cap_ends=False, segments=segments, calc_uvs=True)
        elif kind == "monkey":
            bmesh.ops.create_monkey(bm, calc_uvs=True)
            bmesh.ops.scale(bm, vec=(half, half, half), verts=bm.verts)
        elif kind == "torus":
            _build_torus(bm, major=half * 0.75, minor=half * 0.25, major_segments=segments,
                         minor_segments=max(6, segments // 3))
        mesh = bpy.data.meshes.new(kind)
        bm.to_mesh(mesh)
    finally:
        bm.free()
    if kind in ("uv_sphere", "ico_sphere", "torus", "monkey"):
        for polygon in mesh.polygons:
            polygon.use_smooth = True
    return mesh


def _build_torus(bm, major, minor, major_segments, minor_segments):
    rings = []
    for i in range(major_segments):
        u = 2 * math.pi * i / major_segments
        ring = []
        for j in range(minor_segments):
            v = 2 * math.pi * j / minor_segments
            radius = major + minor * math.cos(v)
            ring.append(bm.verts.new((radius * math.cos(u), radius * math.sin(u), minor * math.sin(v))))
        rings.append(ring)
    uv_layer = bm.loops.layers.uv.active or bm.loops.layers.uv.new("UVMap")
    for i in range(major_segments):
        current, following = rings[i], rings[(i + 1) % major_segments]
        for j in range(minor_segments):
            k = (j + 1) % minor_segments
            face = bm.faces.new((current[j], following[j], following[k], current[k]))
            # Unwrap as a grid: u around the ring, v around the tube (seams at i=0, j=0).
            corners = ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1))
            for loop, (a, b) in zip(face.loops, corners):
                loop[uv_layer].uv = (a / float(major_segments), b / float(minor_segments))


def cmd_create_object(params):
    kind = _string(params.get("kind"), "kind", required=True).lower()
    if kind not in OBJECT_KINDS:
        raise CommandError("kind must be one of: %s" % ", ".join(OBJECT_KINDS))
    name = _string(params.get("name"), "name") or kind.replace("_", " ").title().replace(" ", "")
    size = _number(params.get("size"), "size", 0.0001, 100000, 2.0)
    segments = int(_number(params.get("segments"), "segments", 3, 256, 32))
    location = _vector(params.get("location"), 3, "location")
    rotation = _vector(params.get("rotation_degrees"), 3, "rotation_degrees")
    scale = _vector(params.get("scale"), 3, "scale")
    collection = _target_collection(_string(params.get("collection"), "collection"))
    scene = bpy.context.scene

    if kind in MESH_KINDS:
        data = _build_mesh(kind, size, segments)
        data.name = name
    elif kind in LIGHT_KINDS:
        light_type = LIGHT_KINDS[kind]
        data = bpy.data.lights.new(name, type=light_type)
        data.energy = _number(params.get("energy"), "energy", 0, 1e7, DEFAULT_LIGHT_ENERGY[light_type])
    elif kind == "camera":
        data = bpy.data.cameras.new(name)
        data.lens = _number(params.get("lens"), "lens", 1, 5000, data.lens)
    elif kind == "text":
        data = bpy.data.curves.new(name, type="FONT")
        data.body = _string(params.get("text"), "text") or name
        data.size = size / 2.0
    else:
        data = None

    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    if location:
        obj.location = location
    if rotation:
        _set_rotation_degrees(obj, rotation)
    if scale:
        obj.scale = scale
    if kind == "camera" and (scene.camera is None or params.get("make_active_camera")):
        scene.camera = obj
    _undo_push("create %s" % obj.name)
    return _summary(obj)


def cmd_modify_object(params):
    obj = _get_object(params.get("name"))
    location = _vector(params.get("location"), 3, "location")
    rotation = _vector(params.get("rotation_degrees"), 3, "rotation_degrees")
    scale = _vector(params.get("scale"), 3, "scale")
    dimensions = _vector(params.get("dimensions"), 3, "dimensions")
    new_name = _string(params.get("new_name"), "new_name")
    if location:
        obj.location = location
    if rotation:
        _set_rotation_degrees(obj, rotation)
    if scale:
        obj.scale = scale
    if dimensions:
        if obj.data is None:
            raise CommandError("dimensions can only be set on objects with geometry")
        # Bounding boxes of freshly created objects are only valid after an update.
        bpy.context.view_layer.update()
        obj.dimensions = dimensions
    if "visible" in params and params["visible"] is not None:
        hidden = not bool(params["visible"])
        obj.hide_set(hidden)
        obj.hide_render = hidden
    if "parent" in params and params["parent"] is not None:
        world = obj.matrix_world.copy()
        obj.parent = _get_object(params["parent"]) if params["parent"] else None
        obj.matrix_world = world
    if new_name:
        obj.name = new_name
    bpy.context.view_layer.update()
    _undo_push("modify %s" % obj.name)
    return _summary(obj)


def cmd_delete_objects(params):
    names = params.get("names")
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise CommandError("names must be a non-empty list of object names")
    deleted, missing = [], []
    for name in names:
        obj = bpy.data.objects.get(name)
        if obj is None:
            missing.append(name)
            continue
        bpy.data.objects.remove(obj, do_unlink=True)
        deleted.append(name)
    if deleted:
        _undo_push("delete %d objects" % len(deleted))
    return {"deleted": deleted, "missing": missing}


def _parse_color(value, name):
    """Lists are linear RGB(A) like Blender's own sockets; '#RRGGBB' strings are sRGB."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.lstrip("#")
        if len(text) not in (6, 8):
            raise CommandError("%s must be '#RRGGBB', '#RRGGBBAA', or a list of 3-4 numbers" % name)
        try:
            channels = [int(text[i:i + 2], 16) / 255.0 for i in range(0, len(text), 2)]
        except ValueError:
            raise CommandError("%s is not a valid hex color" % name)
        rgb = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels[:3]]
        return rgb + [channels[3] if len(channels) == 4 else 1.0]
    if not isinstance(value, (list, tuple)) or len(value) not in (3, 4):
        raise CommandError("%s must be '#RRGGBB' or a list of 3-4 numbers" % name)
    color = [float(c) for c in value]
    return color + [1.0] if len(color) == 3 else color


def _principled(material):
    if material.node_tree is None:
        material.use_nodes = True
    tree = material.node_tree
    node = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if node is None:
        node = tree.nodes.new("ShaderNodeBsdfPrincipled")
        output = next((n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
        if output is None:
            output = tree.nodes.new("ShaderNodeOutputMaterial")
        tree.links.new(node.outputs[0], output.inputs["Surface"])
    return node


def _set_input(node, names, value):
    for name in names:
        socket_ = node.inputs.get(name)
        if socket_ is not None:
            socket_.default_value = value
            return True
    return False


def cmd_set_material(params):
    obj = _get_object(params.get("object_name"))
    if obj.data is None or not hasattr(obj.data, "materials"):
        raise CommandError("object %s cannot hold materials" % obj.name)
    material_name = _string(params.get("material_name"), "material_name") or (obj.name + "_Material")
    color = _parse_color(params.get("color"), "color")
    emission = _parse_color(params.get("emission_color"), "emission_color")
    metallic = _number(params.get("metallic"), "metallic", 0, 1)
    roughness = _number(params.get("roughness"), "roughness", 0, 1)
    strength = _number(params.get("emission_strength"), "emission_strength", 0, 1e6)
    alpha = _number(params.get("alpha"), "alpha", 0, 1)

    material = bpy.data.materials.get(material_name) or bpy.data.materials.new(material_name)
    node = _principled(material)
    if color:
        _set_input(node, ["Base Color"], color)
        material.diffuse_color = color
    if metallic is not None:
        _set_input(node, ["Metallic"], metallic)
        material.metallic = metallic
    if roughness is not None:
        _set_input(node, ["Roughness"], roughness)
        material.roughness = roughness
    if emission:
        _set_input(node, ["Emission Color", "Emission"], emission)
        if strength is None:
            strength = 1.0
    if strength is not None:
        _set_input(node, ["Emission Strength"], strength)
    if alpha is not None:
        _set_input(node, ["Alpha"], alpha)

    if params.get("append"):
        obj.data.materials.append(material)
    elif obj.data.materials:
        obj.data.materials[0] = material
    else:
        obj.data.materials.append(material)
    _undo_push("material %s" % material.name)
    return {"object": obj.name, "material": material.name,
            "slots": [s.material.name if s.material else None for s in obj.material_slots]}


def _modifier_types():
    return [item.identifier for item in bpy.types.Modifier.bl_rna.properties["type"].enum_items]


def cmd_add_modifier(params):
    obj = _get_object(params.get("object_name"))
    modifier_type = _string(params.get("modifier_type"), "modifier_type", required=True).upper()
    settings = params.get("settings") or {}
    if not isinstance(settings, dict):
        raise CommandError("settings must be an object of property names to values")
    if modifier_type not in _modifier_types():
        raise CommandError("unknown modifier_type %s; valid types: %s"
                           % (modifier_type, ", ".join(_modifier_types())))
    name = _string(params.get("name"), "name") or modifier_type.title()
    try:
        modifier = obj.modifiers.new(name=name, type=modifier_type)
    except (TypeError, RuntimeError) as exc:
        raise CommandError("cannot add %s to %s: %s" % (modifier_type, obj.name, exc))
    if modifier is None:
        raise CommandError("%s modifiers are not supported on %s objects" % (modifier_type, obj.type))

    applied, rejected = {}, {}
    for key, value in settings.items():
        prop = modifier.bl_rna.properties.get(key)
        if prop is None or key == "rna_type":
            rejected[key] = "unknown property"
            continue
        try:
            if prop.type == "POINTER" and isinstance(value, str):
                source = bpy.data.collections if prop.fixed_type.identifier == "Collection" else bpy.data.objects
                target = source.get(value)
                if target is None:
                    raise ValueError("no datablock named %s" % value)
                value = target
            setattr(modifier, key, value)
            applied[key] = value if not hasattr(value, "name") else value.name
        except (TypeError, ValueError, AttributeError) as exc:
            rejected[key] = str(exc)
    _undo_push("modifier %s" % modifier.name)
    return {"object": obj.name, "modifier": modifier.name, "type": modifier.type,
            "applied": applied, "rejected": rejected}


IMPORTERS = {
    ".glb": [("import_scene", "gltf")],
    ".gltf": [("import_scene", "gltf")],
    ".fbx": [("import_scene", "fbx")],
    ".obj": [("wm", "obj_import"), ("import_scene", "obj")],
    ".stl": [("wm", "stl_import"), ("import_mesh", "stl")],
    ".ply": [("wm", "ply_import"), ("import_mesh", "ply")],
    ".usd": [("wm", "usd_import")],
    ".usda": [("wm", "usd_import")],
    ".usdc": [("wm", "usd_import")],
    ".usdz": [("wm", "usd_import")],
    ".abc": [("wm", "alembic_import")],
    ".dae": [("wm", "collada_import")],
    ".svg": [("import_curve", "svg")],
}

# (operator module, operator name, fixed kwargs, selection-only property)
EXPORTERS = {
    ".glb": [("export_scene", "gltf", {"export_format": "GLB"}, "use_selection")],
    ".gltf": [("export_scene", "gltf", {"export_format": "GLTF_SEPARATE"}, "use_selection")],
    ".fbx": [("export_scene", "fbx", {}, "use_selection")],
    ".obj": [("wm", "obj_export", {}, "export_selected_objects"),
             ("export_scene", "obj", {}, "use_selection")],
    ".stl": [("wm", "stl_export", {}, "export_selected_objects"),
             ("export_mesh", "stl", {}, "use_selection")],
    ".ply": [("wm", "ply_export", {}, "export_selected_objects"),
             ("export_mesh", "ply", {}, "use_selection")],
    ".usd": [("wm", "usd_export", {}, "selected_objects_only")],
    ".usda": [("wm", "usd_export", {}, "selected_objects_only")],
    ".usdc": [("wm", "usd_export", {}, "selected_objects_only")],
    ".usdz": [("wm", "usd_export", {}, "selected_objects_only")],
    ".abc": [("wm", "alembic_export", {}, "selected")],
}


def _file_path(value, name="filepath"):
    path = _string(value, name, required=True)
    return os.path.abspath(os.path.expanduser(path))


GEOMETRY_TYPES = ("MESH", "CURVE", "SURFACE", "META", "FONT", "VOLUME", "GPENCIL", "GREASEPENCIL",
                  "POINTCLOUD", "CURVES")


def _custom_properties(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CommandError("custom_properties must be an object of names to text or numbers")
    clean = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or len(key) > 63:
            raise CommandError("custom property names must be 1-63 characters")
        if not isinstance(item, (str, int, float, bool)):
            raise CommandError("custom property %s must be text, a number, or a boolean" % key)
        clean[key] = item
    return clean


def _fit_objects(objects, target_size, location):
    """Scale the hierarchy so its largest side is target_size and stand it on location."""
    bpy.context.view_layer.update()
    geometry = [o for o in objects if o.type in GEOMETRY_TYPES] or list(objects)
    bbox = _world_bbox(geometry)
    if bbox is None:
        return None
    low, high = bbox
    pivot = Vector(((low.x + high.x) / 2, (low.y + high.y) / 2, low.z))
    largest = max((high - low).to_tuple())
    factor = target_size / largest if target_size and largest > 1e-9 else 1.0
    destination = Vector(location) if location else pivot
    transform = Matrix.Translation(destination) @ Matrix.Scale(factor, 4) @ Matrix.Translation(-pivot)
    for root in [o for o in objects if o.parent not in objects]:
        root.matrix_world = transform @ root.matrix_world
    bpy.context.view_layer.update()
    return factor


def _append_blend(path, collection, candidates):
    """Append the first named collection present in a .blend, or every object when none is."""
    try:
        with bpy.data.libraries.load(path, link=False) as (source, target):
            wanted = next((name for name in candidates if name in source.collections), None)
            if wanted:
                target.collections = [wanted]
            else:
                target.objects = list(source.objects)
    except (OSError, RuntimeError, ValueError) as exc:
        # Typically a file saved by a newer Blender; callers may fall back to glTF.
        raise CommandError("could not read %s: %s" % (os.path.basename(path), exc), code="unreadable")
    for appended in getattr(target, "collections", []) or []:
        if appended is not None:
            collection.children.link(appended)
    for obj in getattr(target, "objects", []) or []:
        if obj is not None and not obj.users_collection:
            collection.objects.link(obj)


def cmd_import_model(params):
    path = _file_path(params.get("filepath"))
    if not os.path.isfile(path):
        raise CommandError("file not found: %s" % path, code="not_found")
    extension = os.path.splitext(path)[1].lower()
    collection_name = _string(params.get("collection"), "collection")
    target_size = _number(params.get("target_size"), "target_size", 0.0001, 100000)
    location = _vector(params.get("location"), 3, "location")
    properties = _custom_properties(params.get("custom_properties"))
    new_name = _string(params.get("name"), "name")
    before = set(bpy.data.objects)

    if extension == ".blend":
        if bpy.data.filepath and os.path.samefile(path, bpy.data.filepath):
            raise CommandError("cannot import the file that is currently open")
        candidates = params.get("blend_collection") or []
        if isinstance(candidates, str):
            candidates = [candidates]
        if not isinstance(candidates, list) or not all(isinstance(c, str) for c in candidates):
            raise CommandError("blend_collection must be a collection name or a list of names to try")
        _append_blend(path, _target_collection(collection_name), candidates)
    else:
        candidates = IMPORTERS.get(extension)
        if not candidates:
            raise CommandError("unsupported format %s; supported: %s"
                               % (extension, ", ".join(sorted(list(IMPORTERS) + [".blend"]))))
        operator = next((op for op in (_operator(m, n) for m, n in candidates) if op), None)
        if operator is None:
            raise CommandError("no %s importer is available in this Blender build" % extension,
                               code="unsupported")
        with _ui_context():
            result = operator(filepath=path)
        if "FINISHED" not in result:
            raise CommandError("import failed for %s" % path, code="failed")
        if collection_name:
            collection = _target_collection(collection_name)
            for obj in set(bpy.data.objects) - before:
                if collection not in obj.users_collection:
                    for old in list(obj.users_collection):
                        old.objects.unlink(obj)
                    collection.objects.link(obj)

    new_objects = set(bpy.data.objects) - before
    if not new_objects:
        raise CommandError("%s contained no objects to import" % os.path.basename(path), code="failed")
    scale = None
    if target_size or location:
        scale = _fit_objects(new_objects, target_size, location)
    for obj in new_objects:
        for key, value in properties.items():
            obj[key] = value
    roots = [o for o in new_objects if o.parent not in new_objects]
    if new_name and len(roots) == 1:
        roots[0].name = new_name
    _undo_push("import %s" % os.path.basename(path))
    return {"filepath": path, "imported_objects": sorted(o.name for o in new_objects),
            "root_objects": sorted(o.name for o in roots),
            "scale_applied": round(scale, 6) if scale is not None else None}


def cmd_export_scene(params):
    path = _file_path(params.get("filepath"))
    extension = os.path.splitext(path)[1].lower()
    candidates = EXPORTERS.get(extension)
    if not candidates:
        raise CommandError("unsupported format %s; supported: %s" % (extension, ", ".join(sorted(EXPORTERS))))
    found = next(((op, fixed, flag) for op, fixed, flag in
                  ((_operator(m, n), fixed, flag) for m, n, fixed, flag in candidates) if op), None)
    if found is None:
        raise CommandError("no %s exporter is available in this Blender build" % extension, code="unsupported")
    operator, fixed, selection_flag = found

    names = params.get("object_names")
    if names is not None and (not isinstance(names, list) or not all(isinstance(n, str) for n in names)):
        raise CommandError("object_names must be a list of object names")
    objects = [_get_object(n) for n in names] if names else None
    if os.path.exists(path) and not params.get("overwrite", True):
        raise CommandError("file exists: %s (pass overwrite=true)" % path, code="exists")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    view_layer = bpy.context.view_layer
    selectable = list(view_layer.objects)
    previous = [o for o in selectable if o.select_get()]
    previous_active = view_layer.objects.active
    try:
        if objects is not None:
            missing = [o.name for o in objects if o not in selectable]
            if missing:
                raise CommandError("objects not in the active view layer: %s" % ", ".join(missing))
            for obj in selectable:
                obj.select_set(obj in objects)
            view_layer.objects.active = objects[0]
        kwargs = dict(fixed, filepath=path)
        if objects is not None or params.get("selected_only"):
            kwargs[selection_flag] = True
        with _ui_context():
            result = operator(**kwargs)
    finally:
        for obj in selectable:
            obj.select_set(obj in previous)
        view_layer.objects.active = previous_active
    if "FINISHED" not in result:
        raise CommandError("export failed for %s" % path, code="failed")
    written = path
    if extension == ".gltf" and not os.path.exists(path):
        written = None
    return {"filepath": path, "exists": bool(written and os.path.exists(written)),
            "bytes": os.path.getsize(path) if os.path.exists(path) else None}


# ---- rendering ------------------------------------------------------------

VIEW_DIRECTIONS = {
    "front": (0.0, -1.0, 0.0),
    "back": (0.0, 1.0, 0.0),
    "left": (-1.0, 0.0, 0.0),
    "right": (1.0, 0.0, 0.0),
    "top": (0.0, -0.0001, 1.0),
    "iso": (1.0, -1.0, 0.8),
}
ENGINE_CANDIDATES = {
    "cycles": ["CYCLES"],
    "eevee": ["BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"],
    "workbench": ["BLENDER_WORKBENCH"],
}


def _gpu_rendering_available():
    # Without a GPU context, Workbench/EEVEE renders abort the whole Blender
    # process in background mode, so they must be opted into explicitly.
    return not bpy.app.background or os.environ.get(GPU_ENV) == "1"


def _resolve_engine(render, requested):
    requested = (requested or "auto").lower()
    if requested == "auto":
        requested = "workbench" if _gpu_rendering_available() else "cycles"
    if requested == "current":
        return render.engine
    if requested not in ENGINE_CANDIDATES:
        raise CommandError("engine must be auto, current, cycles, eevee, or workbench")
    if requested != "cycles" and not _gpu_rendering_available():
        raise CommandError(
            "%s needs a GPU, which is not available in background mode. Use engine='cycles', "
            "or set %s=1 when starting Blender on a machine with a GPU." % (requested, GPU_ENV),
            code="gpu_unavailable")
    original = render.engine
    for identifier in ENGINE_CANDIDATES[requested]:
        try:
            render.engine = identifier
            return identifier
        except TypeError:
            continue
        finally:
            render.engine = original
    raise CommandError("render engine %s is not available in this Blender build" % requested,
                       code="unsupported")


def _frame_camera(scene, direction):
    # Bounding boxes of objects created since the last depsgraph update are still empty.
    bpy.context.view_layer.update()
    targets = [o for o in scene.objects
               if o.type in ("MESH", "CURVE", "SURFACE", "META", "FONT", "VOLUME", "GPENCIL",
                             "GREASEPENCIL", "POINTCLOUD", "CURVES")
               and o.visible_get()]
    bbox = _world_bbox(targets)
    center = (bbox[0] + bbox[1]) / 2 if bbox else Vector((0, 0, 0))
    radius = max(((bbox[1] - bbox[0]).length / 2) if bbox else 1.0, 0.1)
    data = bpy.data.cameras.new("MCP_PreviewCamera")
    data.clip_end = max(1000.0, radius * 20)
    camera = bpy.data.objects.new("MCP_PreviewCamera", data)
    scene.collection.objects.link(camera)
    # data.angle spans the longer side of the image; fit the sphere into the shorter side.
    render = scene.render
    longer = float(max(render.resolution_x, render.resolution_y))
    shorter = float(min(render.resolution_x, render.resolution_y))
    half_angle = math.atan(math.tan(data.angle / 2) * shorter / longer)
    distance = radius / math.sin(half_angle) * 1.05
    camera.location = center + Vector(direction).normalized() * distance
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    return camera


def _scene_has_light(scene):
    return any(o.type == "LIGHT" and o.visible_get() for o in scene.objects)


def _remove_temporary(objects):
    for obj in objects:
        obj_data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if obj_data is not None and obj_data.users == 0:
            if isinstance(obj_data, bpy.types.Camera):
                bpy.data.cameras.remove(obj_data)
            elif isinstance(obj_data, bpy.types.Light):
                bpy.data.lights.remove(obj_data)


@contextlib.contextmanager
def _render_settings(scene, engine, width, height, samples):
    """Switch render settings for a preview and restore every one of them afterwards.

    Yields a list that collects temporary objects (cameras, a sun) to delete at the end.
    """
    render = scene.render
    saved = {
        "engine": render.engine, "x": render.resolution_x, "y": render.resolution_y,
        "percentage": render.resolution_percentage, "filepath": render.filepath,
        "camera": scene.camera, "format": render.image_settings.file_format,
    }
    if hasattr(scene, "cycles"):
        saved["samples"] = scene.cycles.samples
        saved["denoising"] = scene.cycles.use_denoising
    temporary = []
    try:
        if not _scene_has_light(scene) and engine != "BLENDER_WORKBENCH":
            sun = bpy.data.objects.new("MCP_PreviewSun", bpy.data.lights.new("MCP_PreviewSun", "SUN"))
            sun.data.energy = 3.0
            sun.rotation_euler = (math.radians(50), 0, math.radians(30))
            scene.collection.objects.link(sun)
            temporary.append(sun)
        render.engine = engine
        render.resolution_x, render.resolution_y, render.resolution_percentage = width, height, 100
        render.image_settings.file_format = "PNG"
        if engine == "CYCLES" and hasattr(scene, "cycles"):
            scene.cycles.samples = samples
        yield temporary
    finally:
        render.engine = saved["engine"]
        render.resolution_x, render.resolution_y = saved["x"], saved["y"]
        render.resolution_percentage = saved["percentage"]
        render.filepath = saved["filepath"]
        render.image_settings.file_format = saved["format"]
        scene.camera = saved["camera"]
        if "samples" in saved:
            scene.cycles.samples = saved["samples"]
            scene.cycles.use_denoising = saved["denoising"]
        _remove_temporary(temporary)


def _check_view(view):
    view = (view or "camera").lower()
    if view != "camera" and view not in VIEW_DIRECTIONS:
        raise CommandError("view must be camera or one of: %s" % ", ".join(VIEW_DIRECTIONS))
    return view


def _render_view(scene, view, camera_name, path, temporary):
    """Render one view to path; _render_settings must already be active."""
    if view == "camera":
        if camera_name:
            camera = _get_object(camera_name)
            if camera.type != "CAMERA":
                raise CommandError("%s is not a camera" % camera_name)
            scene.camera = camera
        elif scene.camera is None:
            temporary.append(_frame_camera(scene, VIEW_DIRECTIONS["iso"]))
            scene.camera = temporary[-1]
    else:
        temporary.append(_frame_camera(scene, VIEW_DIRECTIONS[view]))
        scene.camera = temporary[-1]
    scene.render.filepath = path
    try:
        with _ui_context():
            bpy.ops.render.render(write_still=True)
    except RuntimeError as exc:
        cycles = getattr(scene, "cycles", None)
        if "denois" not in str(exc).lower() or cycles is None or not cycles.use_denoising:
            raise
        # Some Linux distribution builds lack OpenImageDenoise; preview without denoising
        # (the user's setting is restored by _render_settings).
        cycles.use_denoising = False
        with _ui_context():
            bpy.ops.render.render(write_still=True)
    if not os.path.exists(path):
        raise CommandError("render finished but wrote no image", code="failed")


def _render_size(params, default_width, maximum=4096):
    width = int(_number(params.get("width"), "width", 16, maximum, default_width))
    height = int(_number(params.get("height"), "height", 16, maximum, round(width * 3 / 4)))
    return width, height


def cmd_render_image(params):
    scene = bpy.context.scene
    width, height = _render_size(params, 640)
    samples = int(_number(params.get("samples"), "samples", 1, 4096, 16))
    view = _check_view(_string(params.get("view"), "view"))
    camera_name = _string(params.get("camera"), "camera")
    output = _string(params.get("filepath"), "filepath")
    engine = _resolve_engine(scene.render, params.get("engine"))
    folder = None
    if output:
        path = os.path.abspath(os.path.expanduser(output))
        os.makedirs(os.path.dirname(path), exist_ok=True)
    else:
        folder = tempfile.mkdtemp(prefix="mcp_render_")
        path = os.path.join(folder, "render.png")
    try:
        with _render_settings(scene, engine, width, height, samples) as temporary:
            _render_view(scene, view, camera_name, path, temporary)
        with open(path, "rb") as handle:
            data = handle.read()
    finally:
        if folder:
            shutil.rmtree(folder, ignore_errors=True)
    return {"format": "png", "width": width, "height": height, "engine": engine, "view": view,
            "filepath": path if output else None, "image_base64": base64.b64encode(data).decode("ascii")}


def _compose_grid(paths, width, height, out_path, gap=4):
    """Tile PNGs left to right, top to bottom into one PNG. Returns the column count.

    Uses the standard library's array instead of numpy: distribution builds of
    Blender (for example Ubuntu's) run on a system Python without numpy.
    """
    count = len(paths)
    columns = int(math.ceil(math.sqrt(count)))
    rows = int(math.ceil(count / float(columns)))
    total_width = columns * width + (columns - 1) * gap
    total_height = rows * height + (rows - 1) * gap
    grid = array("f", (0.12, 0.12, 0.12, 1.0)) * (total_width * total_height)
    for index, path in enumerate(paths):
        image = bpy.data.images.load(path)
        try:
            tile_width, tile_height = image.size
            pixels = array("f", bytes(4 * tile_width * tile_height * 4))
            image.pixels.foreach_get(pixels)
        finally:
            bpy.data.images.remove(image)
        copy_rows, copy_columns = min(tile_height, height), min(tile_width, width)
        row, column = divmod(index, columns)
        # Blender stores image rows bottom-up, so row 0 of the grid is at the top.
        top = total_height - row * (height + gap) - copy_rows
        left = column * (width + gap)
        for y in range(copy_rows):
            source = y * tile_width * 4
            target = ((top + y) * total_width + left) * 4
            grid[target:target + copy_columns * 4] = pixels[source:source + copy_columns * 4]
    image = bpy.data.images.new("MCP_Views", total_width, total_height, alpha=True)
    try:
        image.pixels.foreach_set(grid)
        image.filepath_raw = out_path
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)
    return columns


def cmd_render_views(params):
    scene = bpy.context.scene
    views = params.get("views") or ["front", "right", "top", "iso"]
    if not isinstance(views, list) or not 1 <= len(views) <= 9 or not all(isinstance(v, str) for v in views):
        raise CommandError("views must be a list of 1-9 view names")
    views = [_check_view(v) for v in views]
    width, height = _render_size(params, 384, maximum=2048)
    samples = int(_number(params.get("samples"), "samples", 1, 4096, 8))
    camera_name = _string(params.get("camera"), "camera")
    engine = _resolve_engine(scene.render, params.get("engine"))
    folder = tempfile.mkdtemp(prefix="mcp_views_")
    try:
        paths = []
        with _render_settings(scene, engine, width, height, samples) as temporary:
            for index, view in enumerate(views):
                path = os.path.join(folder, "view_%d.png" % index)
                _render_view(scene, view, camera_name, path, temporary)
                paths.append(path)
        grid_path = os.path.join(folder, "views.png")
        columns = _compose_grid(paths, width, height, grid_path)
        with open(grid_path, "rb") as handle:
            data = handle.read()
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    layout = [views[start:start + columns] for start in range(0, len(views), columns)]
    return {"format": "png", "views": views, "layout": layout, "tile_size": [width, height],
            "engine": engine, "image_base64": base64.b64encode(data).decode("ascii")}


def _capture_view3d(window, area, region, width, height, path):
    """Draw the 3D View into an offscreen GPU buffer and save it as PNG.

    Unlike a window screenshot this does not read the window's front buffer, which
    comes back black under Xvfb, some VMs, and some remote desktops.
    """
    import gpu

    space = area.spaces.active
    offscreen = gpu.types.GPUOffScreen(width, height)
    try:
        with offscreen.bind():
            framebuffer = gpu.state.active_framebuffer_get()
            framebuffer.clear(color=(0.0, 0.0, 0.0, 0.0))
            arguments = (window.scene, window.view_layer, space, region,
                         space.region_3d.view_matrix, space.region_3d.window_matrix)
            try:
                offscreen.draw_view3d(*arguments, do_color_management=True)
            except TypeError:
                offscreen.draw_view3d(*arguments)
            buffer = framebuffer.read_color(0, 0, width, height, 4, 0, "FLOAT")
    finally:
        offscreen.free()
    buffer.dimensions = width * height * 4
    image = bpy.data.images.new("MCP_Viewport", width, height, alpha=True)
    try:
        try:
            image.pixels.foreach_set(buffer)
        except (TypeError, ValueError):
            image.pixels[:] = list(buffer)
        image.filepath_raw = path
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)


def _screenshot_area(window, area, region, path, max_size):
    """Fallback: Blender's own area screenshot, scaled down to max_size."""
    with bpy.context.temp_override(window=window, screen=window.screen, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=path)
    image = bpy.data.images.load(path)
    try:
        width, height = image.size
        factor = min(1.0, float(max_size) / max(width, height))
        if factor < 1.0:
            image.scale(max(1, int(width * factor)), max(1, int(height * factor)))
            image.save()
        return tuple(image.size)
    finally:
        bpy.data.images.remove(image)


def cmd_viewport_screenshot(params):
    if bpy.app.background:
        raise CommandError("viewport screenshots need the Blender UI; use render_image in background mode",
                           code="unsupported")
    max_size = int(_number(params.get("max_size"), "max_size", 64, 4096, 1024))
    candidates = [(window, area) for window in bpy.context.window_manager.windows
                  for area in window.screen.areas if area.type == "VIEW_3D"]
    if not candidates:
        raise CommandError("no 3D View is open", code="not_found")
    window, area = max(candidates, key=lambda pair: pair[1].width * pair[1].height)
    region = next(r for r in area.regions if r.type == "WINDOW")
    factor = min(1.0, float(max_size) / max(region.width, region.height))
    width, height = max(1, int(region.width * factor)), max(1, int(region.height * factor))
    folder = tempfile.mkdtemp(prefix="mcp_shot_")
    path = os.path.join(folder, "viewport.png")
    try:
        try:
            _capture_view3d(window, area, region, width, height, path)
            method = "offscreen"
        except Exception:
            width, height = _screenshot_area(window, area, region, path, max_size)
            method = "screenshot"
        with open(path, "rb") as handle:
            data = handle.read()
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return {"format": "png", "width": width, "height": height, "method": method,
            "image_base64": base64.b64encode(data).decode("ascii")}


def cmd_execute_code(params):
    if not _state["allow_code"]:
        raise CommandError("code execution is disabled in the Blender MCP Bridge preferences",
                           code="disabled")
    code = _string(params.get("code"), "code", required=True)
    namespace = {"bpy": bpy, "C": bpy.context, "D": bpy.data, "__name__": "__blender_mcp__"}
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(compile(code, "<blender-mcp>", "exec"), namespace)
    result = namespace.get("result")
    try:
        json.dumps(result)
    except (TypeError, ValueError):
        result = repr(result)
    _undo_push("run script")
    return {"stdout": output.getvalue(), "result": result}


def cmd_save_blend_file(params):
    path = _string(params.get("filepath"), "filepath")
    copy = bool(params.get("copy", False))
    if path:
        path = os.path.abspath(os.path.expanduser(path))
        if not path.lower().endswith(".blend"):
            path += ".blend"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with _ui_context():
            bpy.ops.wm.save_as_mainfile(filepath=path, copy=copy)
    elif bpy.data.filepath:
        path = bpy.data.filepath
        with _ui_context():
            bpy.ops.wm.save_mainfile()
    else:
        raise CommandError("this file has never been saved; pass a filepath")
    return {"filepath": path, "copy": copy}


def _node_tree(datablock):
    if datablock.node_tree is None:
        datablock.use_nodes = True
    return datablock.node_tree


def _existing_file(value, name="filepath"):
    path = _file_path(value, name)
    if not os.path.isfile(path):
        raise CommandError("file not found: %s" % path, code="not_found")
    return path


def _load_image(path):
    try:
        return bpy.data.images.load(path, check_existing=True)
    except RuntimeError as exc:
        raise CommandError("cannot load image %s: %s" % (path, exc), code="unreadable")


def cmd_set_world_hdri(params):
    path = _existing_file(params.get("filepath"))
    strength = _number(params.get("strength"), "strength", 0, 1e6, 1.0)
    rotation = _number(params.get("rotation_degrees"), "rotation_degrees", -36000, 36000, 0.0)
    name = _string(params.get("name"), "name") or os.path.splitext(os.path.basename(path))[0]
    properties = _custom_properties(params.get("custom_properties"))
    image = _load_image(path)
    if params.get("pack", True) and not image.packed_file:
        # Keeps the lighting when the .blend is reopened after caches are cleared.
        image.pack()

    world = bpy.data.worlds.new(name)
    tree = _node_tree(world)
    tree.nodes.clear()
    coords = tree.nodes.new("ShaderNodeTexCoord")
    mapping = tree.nodes.new("ShaderNodeMapping")
    environment = tree.nodes.new("ShaderNodeTexEnvironment")
    background = tree.nodes.new("ShaderNodeBackground")
    output = tree.nodes.new("ShaderNodeOutputWorld")
    for x, node in enumerate((coords, mapping, environment, background, output)):
        node.location = (x * 250 - 500, 0)
    environment.image = image
    mapping.inputs["Rotation"].default_value[2] = math.radians(rotation)
    background.inputs["Strength"].default_value = strength
    tree.links.new(coords.outputs["Generated"], mapping.inputs["Vector"])
    tree.links.new(mapping.outputs["Vector"], environment.inputs["Vector"])
    tree.links.new(environment.outputs["Color"], background.inputs["Color"])
    tree.links.new(background.outputs["Background"], output.inputs["Surface"])
    for key, value in properties.items():
        world[key] = value
    previous = bpy.context.scene.world
    if previous is not None:
        previous.use_fake_user = True  # otherwise it is dropped on save once unused
    bpy.context.scene.world = world
    _undo_push("world %s" % world.name)
    return {"world": world.name, "image": image.name, "packed": bool(image.packed_file),
            "strength": strength, "rotation_degrees": rotation,
            "previous_world": previous.name if previous is not None else None}


TEXTURE_ROLES = ("base_color", "roughness", "metallic", "normal", "displacement", "alpha", "emission", "ao")


def _set_colorspace(image, is_color):
    for name in (("sRGB",) if is_color else ("Non-Color", "Linear", "Raw")):
        try:
            image.colorspace_settings.name = name
            return
        except TypeError:
            continue


def cmd_create_pbr_material(params):
    name = _string(params.get("name"), "name", required=True)
    maps = params.get("maps")
    if not isinstance(maps, dict) or not maps:
        raise CommandError("maps must map texture roles (%s) to image paths" % ", ".join(TEXTURE_ROLES))
    unknown = [role for role in maps if role not in TEXTURE_ROLES]
    if unknown:
        raise CommandError("unknown texture roles: %s; valid: %s" % (", ".join(unknown), ", ".join(TEXTURE_ROLES)))
    paths = {role: _existing_file(path, role) for role, path in maps.items()}
    names = params.get("apply_to") or []
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise CommandError("apply_to must be a list of object names")
    objects = [_get_object(n) for n in names]
    for obj in objects:
        if obj.data is None or not hasattr(obj.data, "materials"):
            raise CommandError("object %s cannot hold materials" % obj.name)
    uv_scale = _number(params.get("uv_scale"), "uv_scale", 0.0001, 10000, 1.0)
    displacement_scale = _number(params.get("displacement_scale"), "displacement_scale", 0, 100, 0.05)
    properties = _custom_properties(params.get("custom_properties"))

    material = bpy.data.materials.new(name)
    principled = _principled(material)
    tree = material.node_tree
    output = next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL")
    coords = tree.nodes.new("ShaderNodeTexCoord")
    mapping = tree.nodes.new("ShaderNodeMapping")
    coords.location, mapping.location = (-1100, 0), (-900, 0)
    mapping.inputs["Scale"].default_value = (uv_scale, uv_scale, uv_scale)
    tree.links.new(coords.outputs["UV"], mapping.inputs["Vector"])

    # Image textures and tangent-space normal maps need a UV map. Without one on every
    # target, fall back to box projection from generated coordinates and skip normals.
    missing_uvs = [o.name for o in objects if o.type == "MESH" and not o.data.uv_layers]
    warnings = []
    if missing_uvs:
        warnings.append("no UV map on %s: used box projection and skipped the normal map"
                        % ", ".join(missing_uvs))
        tree.links.new(coords.outputs["Generated"], mapping.inputs["Vector"])

    used, ignored = [], []
    for row, role in enumerate(r for r in TEXTURE_ROLES if r in paths):
        if role == "ao" or (role == "normal" and missing_uvs):
            # Principled BSDF has no AO input; Blender computes occlusion itself.
            ignored.append(role)
            continue
        image = _load_image(paths[role])
        _set_colorspace(image, role in ("base_color", "emission"))
        texture = tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        texture.location = (-650, 300 - row * 280)
        if missing_uvs:
            texture.projection = "BOX"
            texture.projection_blend = 0.2
        tree.links.new(mapping.outputs["Vector"], texture.inputs["Vector"])
        if role == "normal":
            normal_map = tree.nodes.new("ShaderNodeNormalMap")
            normal_map.location = (-300, 300 - row * 280)
            tree.links.new(texture.outputs["Color"], normal_map.inputs["Color"])
            tree.links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
        elif role == "displacement":
            displacement = tree.nodes.new("ShaderNodeDisplacement")
            displacement.location = (-300, 300 - row * 280)
            displacement.inputs["Scale"].default_value = displacement_scale
            displacement.inputs["Midlevel"].default_value = 0.5
            tree.links.new(texture.outputs["Color"], displacement.inputs["Height"])
            tree.links.new(displacement.outputs["Displacement"], output.inputs["Displacement"])
        else:
            socket_names = {
                "base_color": ["Base Color"], "roughness": ["Roughness"], "metallic": ["Metallic"],
                "alpha": ["Alpha"], "emission": ["Emission Color", "Emission"],
            }[role]
            target = next((principled.inputs.get(n) for n in socket_names if principled.inputs.get(n)), None)
            if target is None:
                ignored.append(role)
                continue
            tree.links.new(texture.outputs["Color"], target)
        used.append(role)

    for key, value in properties.items():
        material[key] = value
    for obj in objects:
        if obj.data.materials:
            obj.data.materials[0] = material
        else:
            obj.data.materials.append(material)
    _undo_push("material %s" % material.name)
    return {"material": material.name, "maps": used, "ignored": ignored,
            "assigned_to": [o.name for o in objects], "warnings": warnings}


def _upload_root():
    return os.path.join(tempfile.gettempdir(), "blender_mcp_uploads")


def _safe_relpath(value):
    """A relative path that cannot leave its folder (no absolute paths, drives, or '..')."""
    if not isinstance(value, str) or not value or "\x00" in value or len(value) > 512:
        raise CommandError("relpath must be a non-empty relative path")
    parts = re.split(r"[\\/]+", value)
    if (value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value)
            or any(part in ("", ".", "..") for part in parts)):
        raise CommandError("unsafe relative path: %r" % value)
    return os.path.join(*parts)


def cmd_stat_file(params):
    path = _file_path(params.get("filepath"))
    exists = os.path.isfile(path)
    return {"filepath": path, "exists": exists, "size": os.path.getsize(path) if exists else None}


def cmd_receive_file(params):
    """Write one chunk of a file sent by the MCP server (for Blender on another machine)."""
    transfer = _string(params.get("transfer_id"), "transfer_id", required=True)
    if not re.match(r"^[0-9a-f]{8,64}$", transfer):
        raise CommandError("transfer_id must be 8-64 lowercase hex characters")
    relpath = _safe_relpath(params.get("relpath"))
    offset = int(_number(params.get("offset"), "offset", 0, 1 << 40, 0))
    try:
        data = base64.b64decode(params.get("data_base64") or "", validate=True)
    except (TypeError, ValueError):
        raise CommandError("data_base64 is not valid base64")
    root = os.path.join(_upload_root(), transfer)
    path = os.path.join(root, relpath)
    current = os.path.getsize(path) if os.path.exists(path) else 0
    if offset and offset != current:  # offset 0 always (re)starts the file
        raise CommandError("chunk for %s starts at %d but %d bytes are stored" % (relpath, offset, current),
                           code="out_of_order")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "ab" if offset else "wb") as handle:
        handle.write(data)
    return {"filepath": path, "size": os.path.getsize(path), "root": root}


def _history(params, operator_name):
    if bpy.app.background:
        raise CommandError("undo and redo need the Blender UI; background mode keeps no undo history",
                           code="unsupported")
    steps = int(_number(params.get("steps"), "steps", 1, 100, 1))
    done = 0
    with _ui_context():
        operator = getattr(bpy.ops.ed, operator_name)
        for _ in range(steps):
            if not operator.poll() or "FINISHED" not in operator():
                break
            done += 1
    return {"steps": done}


def cmd_undo(params):
    return _history(params, "undo")


def cmd_redo(params):
    return _history(params, "redo")


COMMANDS = {
    "ping": cmd_ping,
    "get_bridge_status": cmd_get_bridge_status,
    "get_scene_info": cmd_get_scene_info,
    "list_objects": cmd_list_objects,
    "get_object_info": cmd_get_object_info,
    "create_object": cmd_create_object,
    "modify_object": cmd_modify_object,
    "delete_objects": cmd_delete_objects,
    "set_material": cmd_set_material,
    "add_modifier": cmd_add_modifier,
    "import_model": cmd_import_model,
    "export_scene": cmd_export_scene,
    "render_image": cmd_render_image,
    "render_views": cmd_render_views,
    "viewport_screenshot": cmd_viewport_screenshot,
    "execute_code": cmd_execute_code,
    "save_blend_file": cmd_save_blend_file,
    "set_world_hdri": cmd_set_world_hdri,
    "create_pbr_material": cmd_create_pbr_material,
    "stat_file": cmd_stat_file,
    "receive_file": cmd_receive_file,
    "undo": cmd_undo,
    "redo": cmd_redo,
}


def run_command(request):
    """Execute one decoded request on the main thread and build its response."""
    request_id = request.get("id")
    command = request.get("type")
    handler = COMMANDS.get(command)
    if handler is None:
        return {"id": request_id, "status": "error", "code": "unknown_command",
                "message": "unknown command %r; available: %s" % (command, ", ".join(sorted(COMMANDS)))}
    params = request.get("params") or {}
    if not isinstance(params, dict):
        return {"id": request_id, "status": "error", "code": "invalid_params",
                "message": "params must be a JSON object"}
    try:
        return {"id": request_id, "status": "success", "result": handler(params)}
    except CommandError as exc:
        return {"id": request_id, "status": "error", "code": exc.code, "message": str(exc)}
    except Exception as exc:
        return {"id": request_id, "status": "error", "code": "exception",
                "message": "%s: %s" % (type(exc).__name__, exc), "traceback": traceback.format_exc()}


# --------------------------------------------------------------------------
# Socket server and main-thread queue
# --------------------------------------------------------------------------

_state = {"server": None, "thread": None, "host": DEFAULT_HOST, "port": DEFAULT_PORT,
          "token": "", "allow_code": True, "uses_timer": False}
_pending = queue.Queue()
_slot_lock = threading.Lock()


def drain_queue():
    """Run queued commands on the calling (main) thread. Returns the timer interval."""
    deadline = time.monotonic() + DRAIN_BUDGET_SECONDS
    while time.monotonic() < deadline:
        try:
            request, slot = _pending.get_nowait()
        except queue.Empty:
            break
        with _slot_lock:
            if slot["state"] == "cancelled":
                continue
            slot["state"] = "running"
        slot["response"] = run_command(request)
        slot["event"].set()
    return TIMER_INTERVAL


def _error(request_id, code, message):
    return {"id": request_id, "status": "error", "code": code, "message": message}


def _submit(raw):
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return _error(None, "invalid_request", "invalid JSON request: %s" % exc)
    if not isinstance(request, dict):
        return _error(None, "invalid_request", "request must be a JSON object")
    request_id = request.get("id")

    token = _state["token"]
    if token and not hmac.compare_digest(str(request.get("token") or ""), token):
        return _error(request_id, "unauthorized", "missing or wrong token; set BLENDER_MCP_TOKEN to the "
                      "token shown in the Blender MCP Bridge preferences")
    try:
        timeout = float(request.get("timeout") or DEFAULT_COMMAND_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_COMMAND_TIMEOUT
    timeout = min(max(timeout, 1.0), MAX_COMMAND_TIMEOUT)

    slot = {"event": threading.Event(), "response": None, "state": "pending"}
    _pending.put((request, slot))
    if slot["event"].wait(timeout):
        return slot["response"]
    with _slot_lock:
        if slot["state"] == "pending":
            slot["state"] = "cancelled"
            return _error(request_id, "timeout", "Blender was busy and did not start the command within "
                          "%.0fs; it was cancelled" % timeout)
    # Already running: wait for it to finish rather than leave its result unknown.
    slot["event"].wait()
    return slot["response"]


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(IDLE_READ_TIMEOUT)
        while True:
            try:
                raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            except (socket.timeout, OSError):
                return
            if not raw:
                return
            if len(raw) > MAX_REQUEST_BYTES and not raw.endswith(b"\n"):
                self._send(_error(None, "too_large", "request exceeds %d bytes" % MAX_REQUEST_BYTES))
                return
            if raw.strip():
                self._send(_submit(raw))

    def _send(self, response):
        payload = json.dumps(response, default=_json_default).encode("utf-8") + b"\n"
        try:
            self.wfile.write(payload)
            self.wfile.flush()
        except OSError:
            pass


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second Blender silently share the port.
    allow_reuse_address = os.name != "nt"


def is_running():
    return _state["server"] is not None


def start_server(host=None, port=None, token=None, allow_code=None, use_timer=True):
    """Start listening. Returns False when the bridge is already running."""
    if is_running():
        return False
    prefs = _preferences()
    host = host or (prefs.host if prefs else DEFAULT_HOST)
    port = int(port or (prefs.port if prefs else DEFAULT_PORT))
    if token is None:
        token = (prefs.token if prefs else "") or os.environ.get(TOKEN_ENV, "")
    if allow_code is None:
        allow_code = prefs.allow_code_execution if prefs else True

    server = _Server((host, port), _Handler)
    thread = threading.Thread(target=server.serve_forever, name="blender-mcp-bridge", daemon=True)
    thread.start()
    _state.update(server=server, thread=thread, host=host, port=server.server_address[1],
                  token=token or "", allow_code=bool(allow_code), uses_timer=use_timer)
    if use_timer and not bpy.app.timers.is_registered(drain_queue):
        bpy.app.timers.register(drain_queue, persistent=True)
    return True


def stop_server():
    server = _state["server"]
    if server is None:
        return False
    server.shutdown()
    server.server_close()
    if _state["thread"] is not None:
        _state["thread"].join(timeout=5)
    _state.update(server=None, thread=None)
    if bpy.app.timers.is_registered(drain_queue):
        bpy.app.timers.unregister(drain_queue)
    # Fail anything still queued instead of leaving clients waiting.
    while True:
        try:
            request, slot = _pending.get_nowait()
        except queue.Empty:
            break
        slot["response"] = _error(request.get("id"), "stopped", "the bridge was stopped")
        slot["event"].set()
    return True


def serve_blocking(host=None, port=None, token=None, allow_code=True, stop_event=None):
    """Run the bridge on the calling thread until stop_event is set or Ctrl+C (background mode)."""
    start_server(host=host, port=port, token=token, allow_code=allow_code, use_timer=False)
    print("Blender MCP Bridge %s listening on %s:%s (auth: %s). Press Ctrl+C to stop."
          % (BRIDGE_VERSION, _state["host"], _state["port"], "token" if _state["token"] else "none"),
          flush=True)
    try:
        while stop_event is None or not stop_event.is_set():
            drain_queue()
            time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        stop_server()


# --------------------------------------------------------------------------
# Blender UI
# --------------------------------------------------------------------------

def _preferences():
    try:
        return bpy.context.preferences.addons[_ADDON_KEY].preferences
    except (AttributeError, KeyError):
        return None


def client_config():
    """MCP client JSON for this Blender's bridge settings."""
    server = {"command": "uvx", "args": ["--from", PACKAGE_SOURCE, "blender-mcp-bridge"]}
    env = {}
    if _state["port"] != DEFAULT_PORT:
        env["BLENDER_MCP_PORT"] = str(_state["port"])
    if _state["token"]:
        env["BLENDER_MCP_TOKEN"] = _state["token"]
    if env:
        server["env"] = env
    return json.dumps({"mcpServers": {"blender": server}}, indent=2)


class BLENDERMCP_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = _ADDON_KEY

    host: bpy.props.StringProperty(
        name="Host", default=DEFAULT_HOST,
        description="Address to listen on. Keep 127.0.0.1 unless you understand the risk")
    port: bpy.props.IntProperty(name="Port", default=DEFAULT_PORT, min=1024, max=65535)
    token: bpy.props.StringProperty(
        name="Token", subtype="PASSWORD", default="",
        description="Optional shared secret. Clients must send it as BLENDER_MCP_TOKEN")
    auto_start: bpy.props.BoolProperty(
        name="Start automatically", default=False,
        description="Start the bridge when Blender starts")
    allow_code_execution: bpy.props.BoolProperty(
        name="Allow Python execution", default=True,
        description="Let clients run arbitrary Python through execute_blender_code")

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.prop(self, "host")
        row.prop(self, "port")
        row = layout.row(align=True)
        row.prop(self, "token")
        row.operator("blendermcp.generate_token", text="", icon="FILE_REFRESH")
        layout.prop(self, "auto_start")
        layout.prop(self, "allow_code_execution")
        layout.label(text="Changes apply the next time the bridge starts.", icon="INFO")


class BLENDERMCP_OT_start(bpy.types.Operator):
    bl_idname = "blendermcp.start"
    bl_label = "Start MCP Bridge"
    bl_description = "Start listening for MCP clients"

    def execute(self, context):
        try:
            started = start_server()
        except OSError as exc:
            self.report({"ERROR"}, "Could not listen on the configured port: %s" % exc)
            return {"CANCELLED"}
        if started:
            self.report({"INFO"}, "MCP bridge listening on %s:%s" % (_state["host"], _state["port"]))
        return {"FINISHED"}


class BLENDERMCP_OT_stop(bpy.types.Operator):
    bl_idname = "blendermcp.stop"
    bl_label = "Stop MCP Bridge"
    bl_description = "Stop listening for MCP clients"

    def execute(self, context):
        stop_server()
        self.report({"INFO"}, "MCP bridge stopped")
        return {"FINISHED"}


class BLENDERMCP_OT_copy_config(bpy.types.Operator):
    bl_idname = "blendermcp.copy_config"
    bl_label = "Copy Client Config"
    bl_description = "Copy MCP client JSON (Claude Desktop, Cursor, ...) to the clipboard"

    def execute(self, context):
        context.window_manager.clipboard = client_config()
        self.report({"INFO"}, "MCP client config copied to the clipboard")
        return {"FINISHED"}


class BLENDERMCP_OT_generate_token(bpy.types.Operator):
    bl_idname = "blendermcp.generate_token"
    bl_label = "Generate Token"
    bl_description = "Generate a random shared secret for client authentication"

    def execute(self, context):
        prefs = _preferences()
        if prefs is None:
            return {"CANCELLED"}
        prefs.token = secrets.token_urlsafe(24)
        self.report({"INFO"}, "New token generated; restart the bridge and update your client config")
        return {"FINISHED"}


class BLENDERMCP_PT_panel(bpy.types.Panel):
    bl_label = "MCP Bridge"
    bl_idname = "BLENDERMCP_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MCP"

    def draw(self, context):
        layout = self.layout
        if is_running():
            layout.label(text="Listening on %s:%s" % (_state["host"], _state["port"]), icon="CHECKMARK")
            layout.label(text="Auth: %s" % ("token" if _state["token"] else "none (localhost only)"))
            layout.label(text="Python execution: %s" % ("on" if _state["allow_code"] else "off"))
            layout.operator("blendermcp.stop", icon="PAUSE")
        else:
            layout.label(text="Stopped", icon="X")
            layout.operator("blendermcp.start", icon="PLAY")
        layout.operator("blendermcp.copy_config", icon="COPYDOWN")


CLASSES = (
    BLENDERMCP_AddonPreferences,
    BLENDERMCP_OT_start,
    BLENDERMCP_OT_stop,
    BLENDERMCP_OT_copy_config,
    BLENDERMCP_OT_generate_token,
    BLENDERMCP_PT_panel,
)


def _auto_start():
    prefs = _preferences()
    if prefs is not None and prefs.auto_start and not is_running():
        try:
            start_server()
        except OSError as exc:
            print("Blender MCP Bridge: auto-start failed: %s" % exc)
    return None


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    if not bpy.app.background:
        bpy.app.timers.register(_auto_start, first_interval=1.0)


def unregister():
    stop_server()
    if bpy.app.timers.is_registered(_auto_start):
        bpy.app.timers.unregister(_auto_start)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
