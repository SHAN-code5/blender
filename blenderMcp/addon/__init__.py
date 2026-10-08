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
    "version": (0, 2, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > MCP",
    "description": "Let MCP clients such as Claude inspect and edit your scene",
    "category": "Interface",
}

import base64
import contextlib
import hmac
import io
import json
import math
import os
import queue
import secrets
import socket
import socketserver
import tempfile
import threading
import time
import traceback

import bpy  # must precede bmesh/mathutils when running as the standalone bpy module
import bmesh
from mathutils import Euler, Vector

BRIDGE_VERSION = "0.2.0"
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
    try:
        if kind == "cube":
            bmesh.ops.create_cube(bm, size=size)
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
            bmesh.ops.create_monkey(bm)
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
    for i in range(major_segments):
        current, following = rings[i], rings[(i + 1) % major_segments]
        for j in range(minor_segments):
            k = (j + 1) % minor_segments
            bm.faces.new((current[j], following[j], following[k], current[k]))


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


def cmd_import_model(params):
    path = _file_path(params.get("filepath"))
    if not os.path.isfile(path):
        raise CommandError("file not found: %s" % path, code="not_found")
    extension = os.path.splitext(path)[1].lower()
    collection_name = _string(params.get("collection"), "collection")
    before = set(bpy.data.objects)

    if extension == ".blend":
        if bpy.data.filepath and os.path.samefile(path, bpy.data.filepath):
            raise CommandError("cannot import the file that is currently open")
        with bpy.data.libraries.load(path, link=False) as (source, target):
            target.objects = list(source.objects)
        collection = _target_collection(collection_name)
        for obj in target.objects:
            if obj is not None and not obj.users_collection:
                collection.objects.link(obj)
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

    imported = sorted(o.name for o in set(bpy.data.objects) - before)
    _undo_push("import %s" % os.path.basename(path))
    return {"filepath": path, "imported_objects": imported}


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
    distance = radius / math.sin(data.angle / 2) * 1.1
    camera.location = center + Vector(direction).normalized() * distance
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    return camera


def _scene_has_light(scene):
    return any(o.type == "LIGHT" and o.visible_get() for o in scene.objects)


def cmd_render_image(params):
    scene = bpy.context.scene
    render = scene.render
    width = int(_number(params.get("width"), "width", 16, 4096, 640))
    height = int(_number(params.get("height"), "height", 16, 4096, round(width * 3 / 4)))
    samples = int(_number(params.get("samples"), "samples", 1, 4096, 16))
    view = (_string(params.get("view"), "view") or "camera").lower()
    camera_name = _string(params.get("camera"), "camera")
    output = _string(params.get("filepath"), "filepath")
    if view != "camera" and view not in VIEW_DIRECTIONS:
        raise CommandError("view must be camera or one of: %s" % ", ".join(VIEW_DIRECTIONS))
    engine = _resolve_engine(render, params.get("engine"))

    saved = {
        "engine": render.engine, "x": render.resolution_x, "y": render.resolution_y,
        "percentage": render.resolution_percentage, "filepath": render.filepath,
        "camera": scene.camera, "format": render.image_settings.file_format,
    }
    if hasattr(scene, "cycles"):
        saved["samples"] = scene.cycles.samples
    temporary = []
    if output:
        path = os.path.abspath(os.path.expanduser(output))
        os.makedirs(os.path.dirname(path), exist_ok=True)
    else:
        path = os.path.join(tempfile.mkdtemp(prefix="mcp_render_"), "render.png")
    try:
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
        if not _scene_has_light(scene) and engine != "BLENDER_WORKBENCH":
            sun = bpy.data.objects.new("MCP_PreviewSun", bpy.data.lights.new("MCP_PreviewSun", "SUN"))
            sun.data.energy = 3.0
            sun.rotation_euler = (math.radians(50), 0, math.radians(30))
            scene.collection.objects.link(sun)
            temporary.append(sun)

        render.engine = engine
        render.resolution_x, render.resolution_y, render.resolution_percentage = width, height, 100
        render.image_settings.file_format = "PNG"
        render.filepath = path
        if engine == "CYCLES" and hasattr(scene, "cycles"):
            scene.cycles.samples = samples
        with _ui_context():
            bpy.ops.render.render(write_still=True)
        if not os.path.exists(path):
            raise CommandError("render finished but wrote no image", code="failed")
        with open(path, "rb") as handle:
            data = handle.read()
    finally:
        render.engine = saved["engine"]
        render.resolution_x, render.resolution_y = saved["x"], saved["y"]
        render.resolution_percentage = saved["percentage"]
        render.filepath = saved["filepath"]
        render.image_settings.file_format = saved["format"]
        scene.camera = saved["camera"]
        if "samples" in saved:
            scene.cycles.samples = saved["samples"]
        for obj in temporary:
            obj_data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if obj_data is not None and obj_data.users == 0:
                if isinstance(obj_data, bpy.types.Camera):
                    bpy.data.cameras.remove(obj_data)
                elif isinstance(obj_data, bpy.types.Light):
                    bpy.data.lights.remove(obj_data)
        if not output and os.path.exists(path):
            os.remove(path)
            os.rmdir(os.path.dirname(path))
    return {"format": "png", "width": width, "height": height, "engine": engine, "view": view,
            "filepath": path if output else None, "image_base64": base64.b64encode(data).decode("ascii")}


def cmd_viewport_screenshot(params):
    if bpy.app.background:
        raise CommandError("viewport screenshots need the Blender UI; use render_image in background mode",
                           code="unsupported")
    max_size = int(_number(params.get("max_size"), "max_size", 64, 4096, 1024))
    window_manager = bpy.context.window_manager
    for window in window_manager.windows:
        area = next((a for a in window.screen.areas if a.type == "VIEW_3D"), None)
        if area is not None:
            break
    else:
        raise CommandError("no 3D View is open", code="not_found")
    region = next(r for r in area.regions if r.type == "WINDOW")
    folder = tempfile.mkdtemp(prefix="mcp_shot_")
    path = os.path.join(folder, "viewport.png")
    try:
        with bpy.context.temp_override(window=window, screen=window.screen, area=area, region=region):
            bpy.ops.screen.screenshot_area(filepath=path)
        image = bpy.data.images.load(path)
        try:
            width, height = image.size
            factor = min(1.0, float(max_size) / max(width, height))
            if factor < 1.0:
                width, height = max(1, int(width * factor)), max(1, int(height * factor))
                image.scale(width, height)
                image.save()
        finally:
            bpy.data.images.remove(image)
        with open(path, "rb") as handle:
            data = handle.read()
    finally:
        if os.path.exists(path):
            os.remove(path)
        os.rmdir(folder)
    return {"format": "png", "width": width, "height": height,
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
    "viewport_screenshot": cmd_viewport_screenshot,
    "execute_code": cmd_execute_code,
    "save_blend_file": cmd_save_blend_file,
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
