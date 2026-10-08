"""MCP server exposing a running Blender session as tools.

Start it with ``blender-mcp-bridge`` (or ``python -m blenderMcp``); the Blender
side is the "Blender MCP Bridge" add-on or ``runHeadless.py``.
"""

import base64
import json
from typing import Annotated, Any, List, Literal, Optional

from pydantic import Field

from . import __version__, compat
from .config import Settings, load_settings
from .connection import BlenderConnection, BlenderError
from .safety import SafeModeError, validate_script

INSTRUCTIONS = """\
Tools for building and inspecting the scene open in Blender.

Workflow:
1. Call get_scene_info first to learn what exists (names, transforms, materials).
2. Prefer the structured tools (create_object, modify_object, set_material,
   add_modifier, import_model, export_scene) over execute_blender_code; they
   validate input, work across Blender versions, and are one undo step each.
3. Check your work visually with render_image (any mode, auto-frames a view)
   or viewport_screenshot (only when Blender's UI is open).
4. Use execute_blender_code for anything else (geometry nodes, animation,
   rigging). Print values or assign `result` to read them back.

Conventions: units are meters, rotations are degrees (XYZ Euler), colors are
'#RRGGBB' (sRGB) or [r, g, b] linear floats 0-1. Save with save_blend_file
before large or destructive changes.
"""

Vector3 = Annotated[List[float], Field(min_length=3, max_length=3)]
ObjectKind = Literal[
    "cube", "uv_sphere", "ico_sphere", "cylinder", "cone", "torus", "plane", "circle", "grid",
    "monkey", "light_point", "light_sun", "light_spot", "light_area", "camera", "empty", "text",
]
RenderView = Literal["camera", "front", "back", "left", "right", "top", "iso"]
RenderEngine = Literal["auto", "current", "cycles", "eevee", "workbench"]


def _drop_none(params: dict) -> dict:
    return {key: value for key, value in params.items() if value is not None}


def _image_result(result: dict, label: str) -> list:
    data = base64.b64decode(result.pop("image_base64"))
    return [compat.Image(data=data, format="png"), f"{label}: {json.dumps(result)}"]


def build_server(settings: Optional[Settings] = None, connection: Optional[BlenderConnection] = None):
    """Create the MCP server. ``connection`` can be replaced in tests."""
    settings = settings or Settings()
    connection = connection or BlenderConnection(
        settings.host, settings.port, settings.timeout, settings.token)
    server = compat.create_server("blender", INSTRUCTIONS)

    def call(command: str, params: Optional[dict] = None, timeout: Optional[float] = None) -> Any:
        try:
            return connection.send_command(command, _drop_none(params or {}), timeout=timeout)
        except BlenderError as exc:
            # ToolError reaches the model as a readable message instead of a generic crash.
            raise compat.ToolError(str(exc)) from exc

    @compat.tool(server, title="Bridge status", read_only=True, idempotent=True)
    def get_bridge_status() -> dict:
        """Report whether Blender is reachable, its version, and the bridge's capabilities."""
        status = {
            "mcp_server_version": __version__,
            "mcp_sdk_version": compat.sdk_version(),
            "safe_mode": settings.safe_mode,
            "blender_address": f"{settings.host}:{settings.port}",
        }
        try:
            status.update(connection.send_command("get_bridge_status", timeout=10))
            status["connected"] = True
        except BlenderError as exc:
            status.update(connected=False, error=str(exc))
        return status

    @compat.tool(server, title="Scene overview", read_only=True, idempotent=True)
    def get_scene_info(
        limit: Annotated[int, Field(ge=1, le=5000, description="Maximum objects to list")] = 200,
    ) -> dict:
        """Summarize the active scene: render settings, camera, collections, and each object's
        transform, dimensions, parent, visibility, and materials."""
        return call("get_scene_info", {"limit": limit})

    @compat.tool(server, title="List objects", read_only=True, idempotent=True)
    def list_objects(
        object_type: Annotated[Optional[str], Field(description="Filter by type: MESH, CAMERA, LIGHT, EMPTY, CURVE, ...")] = None,
        collection: Annotated[Optional[str], Field(description="Only objects in this collection")] = None,
    ) -> list:
        """List object names, types, and locations, optionally filtered."""
        return call("list_objects", {"object_type": object_type, "collection": collection})

    @compat.tool(server, title="Object details", read_only=True, idempotent=True)
    def get_object_info(name: Annotated[str, Field(description="Exact object name")]) -> dict:
        """Detailed info for one object: world bounding box, mesh statistics, modifiers,
        constraints, children, custom properties, and light or camera settings."""
        return call("get_object_info", {"name": name})

    @compat.tool(server, title="Create object")
    def create_object(
        kind: ObjectKind,
        name: Annotated[Optional[str], Field(description="Object name; Blender adds .001 if taken")] = None,
        location: Optional[Vector3] = None,
        rotation_degrees: Optional[Vector3] = None,
        scale: Optional[Vector3] = None,
        size: Annotated[Optional[float], Field(gt=0, description="Overall size in meters (default 2, like Blender)")] = None,
        segments: Annotated[Optional[int], Field(ge=3, le=256, description="Resolution for round shapes")] = None,
        collection: Annotated[Optional[str], Field(description="Collection to put it in; created if missing")] = None,
        energy: Annotated[Optional[float], Field(ge=0, description="Light power (W, or strength for sun)")] = None,
        lens: Annotated[Optional[float], Field(gt=0, description="Camera focal length in mm")] = None,
        text: Annotated[Optional[str], Field(description="Body for kind='text'")] = None,
        make_active_camera: Annotated[Optional[bool], Field(description="Make a new camera the scene camera")] = None,
    ) -> dict:
        """Create a mesh primitive, light, camera, empty, or text object. The first camera
        in a scene becomes the scene camera automatically."""
        return call("create_object", {
            "kind": kind, "name": name, "location": location, "rotation_degrees": rotation_degrees,
            "scale": scale, "size": size, "segments": segments, "collection": collection,
            "energy": energy, "lens": lens, "text": text, "make_active_camera": make_active_camera,
        })

    @compat.tool(server, title="Modify object", idempotent=True)
    def modify_object(
        name: str,
        location: Optional[Vector3] = None,
        rotation_degrees: Optional[Vector3] = None,
        scale: Optional[Vector3] = None,
        dimensions: Annotated[Optional[Vector3], Field(description="Target size in meters; adjusts scale")] = None,
        new_name: Optional[str] = None,
        visible: Optional[bool] = None,
        parent: Annotated[Optional[str], Field(description="Parent object name, or '' to clear; keeps world transform")] = None,
    ) -> dict:
        """Change an object's transform, size, name, visibility, or parent. Only the fields
        you pass are changed."""
        return call("modify_object", {
            "name": name, "location": location, "rotation_degrees": rotation_degrees, "scale": scale,
            "dimensions": dimensions, "new_name": new_name, "visible": visible, "parent": parent,
        })

    @compat.tool(server, title="Delete objects", destructive=True)
    def delete_objects(names: Annotated[List[str], Field(min_length=1)]) -> dict:
        """Delete objects by name. Reports which names were not found."""
        return call("delete_objects", {"names": names})

    @compat.tool(server, title="Set material")
    def set_material(
        object_name: str,
        color: Annotated[Optional[Any], Field(description="'#RRGGBB' (sRGB) or [r, g, b(, a)] linear 0-1")] = None,
        metallic: Annotated[Optional[float], Field(ge=0, le=1)] = None,
        roughness: Annotated[Optional[float], Field(ge=0, le=1)] = None,
        emission_color: Annotated[Optional[Any], Field(description="Glow color, same formats as color")] = None,
        emission_strength: Annotated[Optional[float], Field(ge=0)] = None,
        alpha: Annotated[Optional[float], Field(ge=0, le=1)] = None,
        material_name: Annotated[Optional[str], Field(description="Reuse or create this material")] = None,
        append: Annotated[Optional[bool], Field(description="Add a new slot instead of replacing slot 1")] = None,
    ) -> dict:
        """Create or update a Principled BSDF material and assign it to an object."""
        return call("set_material", {
            "object_name": object_name, "color": color, "metallic": metallic, "roughness": roughness,
            "emission_color": emission_color, "emission_strength": emission_strength, "alpha": alpha,
            "material_name": material_name, "append": append,
        })

    @compat.tool(server, title="Add modifier")
    def add_modifier(
        object_name: str,
        modifier_type: Annotated[str, Field(description="Blender modifier type, e.g. SUBSURF, BEVEL, ARRAY, MIRROR, SOLIDIFY, BOOLEAN")],
        settings: Annotated[Optional[dict], Field(description="Modifier properties, e.g. {'levels': 2}; object/collection properties take names")] = None,
        name: Optional[str] = None,
    ) -> dict:
        """Add a modifier and set its properties. Unknown or invalid properties are reported
        back instead of failing the whole call."""
        return call("add_modifier", {"object_name": object_name, "modifier_type": modifier_type,
                                     "settings": settings, "name": name})

    @compat.tool(server, title="Import model", open_world=True)
    def import_model(
        filepath: Annotated[str, Field(description="Path on the machine running Blender")],
        collection: Annotated[Optional[str], Field(description="Put imported objects in this collection")] = None,
    ) -> dict:
        """Import a model file: .glb, .gltf, .fbx, .obj, .stl, .ply, .usd/.usda/.usdc/.usdz,
        .abc, .dae, .svg, or append all objects from a .blend. Picks the importer that exists
        in the running Blender version."""
        return call("import_model", {"filepath": filepath, "collection": collection}, timeout=300)

    @compat.tool(server, title="Export scene")
    def export_scene(
        filepath: Annotated[str, Field(description="Output path; the extension picks the format")],
        object_names: Annotated[Optional[List[str]], Field(description="Export only these objects")] = None,
        selected_only: Optional[bool] = None,
        overwrite: Optional[bool] = None,
    ) -> dict:
        """Export to .glb, .gltf, .fbx, .obj, .stl, .ply, .usd/.usda/.usdc/.usdz, or .abc.
        The user's selection is restored afterwards."""
        return call("export_scene", {"filepath": filepath, "object_names": object_names,
                                     "selected_only": selected_only, "overwrite": overwrite}, timeout=300)

    @compat.tool(server, title="Render image", read_only=True)
    def render_image(
        view: RenderView = "camera",
        engine: RenderEngine = "auto",
        width: Annotated[int, Field(ge=16, le=4096)] = 640,
        height: Annotated[Optional[int], Field(ge=16, le=4096)] = None,
        samples: Annotated[Optional[int], Field(ge=1, le=4096, description="Cycles samples (default 16)")] = None,
        camera: Annotated[Optional[str], Field(description="Camera object for view='camera'")] = None,
        filepath: Annotated[Optional[str], Field(description="Also keep the PNG at this path")] = None,
        timeout_seconds: Annotated[float, Field(gt=0, le=3600)] = 180,
    ) -> list:
        """Render the scene and return the image so you can check your work. view='camera'
        uses the scene camera (or an auto-framed one if there is none); other views frame all
        visible geometry from that side. 'auto' picks Workbench with a UI and Cycles (CPU)
        in background mode. Scene render settings are restored afterwards."""
        result = call("render_image", {
            "view": view, "engine": engine, "width": width, "height": height, "samples": samples,
            "camera": camera, "filepath": filepath,
        }, timeout=timeout_seconds)
        return _image_result(result, "render")

    @compat.tool(server, title="Viewport screenshot", read_only=True)
    def viewport_screenshot(
        max_size: Annotated[int, Field(ge=64, le=4096, description="Longest side in pixels")] = 1024,
    ) -> list:
        """Capture the 3D View exactly as the user sees it. Needs Blender's UI; in background
        mode use render_image."""
        return _image_result(call("viewport_screenshot", {"max_size": max_size}), "viewport")

    @compat.tool(server, title="Run Blender Python", destructive=True, open_world=True)
    def execute_blender_code(
        code: Annotated[str, Field(description="Python with bpy, C (context), and D (data) available")],
        timeout_seconds: Annotated[float, Field(gt=0, le=3600)] = 60,
    ) -> dict:
        """Run Python inside Blender. Printed output comes back as `stdout`; assign a variable
        named `result` to return a value (JSON-serializable, otherwise its repr). Runs with full
        access to Blender; prefer the structured tools when they cover the task."""
        if settings.safe_mode:
            try:
                validate_script(code)
            except SafeModeError as exc:
                raise compat.ToolError(str(exc)) from exc
        return call("execute_code", {"code": code}, timeout=timeout_seconds)

    @compat.tool(server, title="Save .blend file", idempotent=True)
    def save_blend_file(
        filepath: Annotated[Optional[str], Field(description="Save as this path; omit to save the current file")] = None,
        save_copy: Annotated[Optional[bool], Field(description="Write a copy without switching to it")] = None,
    ) -> dict:
        """Save the open Blender file."""
        return call("save_blend_file", {"filepath": filepath, "copy": save_copy}, timeout=300)

    return server


def main(settings: Optional[Settings] = None) -> None:
    settings = settings or load_settings()
    server = build_server(settings)
    compat.run(server, settings.transport, settings.http_host, settings.http_port)


if __name__ == "__main__":
    main()
