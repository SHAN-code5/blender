"""MCP server exposing a running Blender session as tools.

Start it with ``blender-mcp-bridge`` (or ``python -m blenderMcp``); the Blender
side is the "Blender MCP Bridge" add-on or ``runHeadless.py``.

Every tool is async and runs blocking work (the bridge socket, downloads,
generation polling) in worker threads, so one slow call never stalls the others.
"""

import base64
import json
import time
from functools import partial
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

import anyio
from pydantic import BaseModel, Field

from . import __version__, compat, guides, net
from .assets import AssetError, FetchedAsset, PolyHaven, PolyPizza, Sketchfab
from .config import Settings, load_settings
from .connection import BlenderConnection, BlenderError
from .generation import GenerationError, Generator
from .safety import SafeModeError, validate_script
from .transfer import stage_files

INSTRUCTIONS = """\
Tools for building and inspecting the scene open in Blender.

Workflow:
1. Call get_bridge_status, then get_scene_info, to learn what exists.
2. Prefer the structured tools (create_object, modify_object, set_material,
   add_modifier, import_model, export_scene) over execute_blender_code; they
   validate input, work across Blender versions, and are one undo step each.
3. Light and frame it: setup_lighting (presets) or set_light, and set_camera
   (auto-frame from a side, or aim at a target).
4. Check your work visually with render_views (several angles in one image),
   render_image, or viewport_screenshot (UI only), and fix what looks wrong.
5. Animate with insert_keyframes or create_turntable, then render_animation
   (mp4, gif, or png frames; you get a contact sheet of frames back).
6. For realistic content: search_assets + import_asset (Poly Haven HDRIs,
   textures and models; Sketchfab; Poly Pizza) or generate_3d (AI models).
7. get_guide has short guides: workflow, modeling, materials, lighting,
   animation, python, assets. Read the relevant one before an unfamiliar task.

Conventions: meters, Z up, front view looks along +Y, rotations in degrees
(XYZ), colors '#RRGGBB' (sRGB) or [r, g, b] linear 0-1. Save with
save_blend_file before large or destructive changes.
"""

Vector3 = Annotated[List[float], Field(min_length=3, max_length=3)]
ObjectKind = Literal[
    "cube", "uv_sphere", "ico_sphere", "cylinder", "cone", "torus", "plane", "circle", "grid",
    "monkey", "light_point", "light_sun", "light_spot", "light_area", "camera", "empty", "text",
]
RenderView = Literal["camera", "front", "back", "left", "right", "top", "iso"]
RenderEngine = Literal["auto", "current", "cycles", "eevee", "workbench"]
AssetSource = Literal["polyhaven", "sketchfab", "polypizza"]
PolyHavenType = Literal["hdris", "textures", "models"]
GuideTopic = Literal["workflow", "modeling", "materials", "lighting", "animation", "python", "assets"]
GeneratorName = Literal["tripo", "hyper3d", "custom_api", "mock"]
LightKind = Literal["point", "sun", "spot", "area"]
LightingPreset = Literal["three_point", "studio", "outdoor", "dramatic"]
CameraView = Literal["front", "back", "left", "right", "top", "iso"]
Target = Union[str, Vector3]
Interpolation = Literal["bezier", "linear", "constant"]
TurntableMode = Literal["camera", "object"]
AnimationFormat = Literal["mp4", "gif", "png"]


class Keyframe(BaseModel):
    """What an object looks like at one frame; only the fields given are keyed."""

    frame: float
    location: Optional[Vector3] = None
    rotation_degrees: Optional[Vector3] = None
    scale: Optional[Vector3] = None
    visible: Optional[bool] = None
    data: Optional[Dict[str, Any]] = Field(
        None, description="Light/camera data properties, e.g. {'energy': 800} or {'lens': 35}")


def _drop_none(params: dict) -> dict:
    return {key: value for key, value in params.items() if value is not None}


def _image_result(result: dict, label: str) -> list:
    data = base64.b64decode(result.pop("image_base64"))
    return [compat.Image(data=data, format="png"), f"{label}: {json.dumps(result)}"]


def _message(exc: BaseException) -> str:
    user_message = getattr(exc, "user_message", None)
    if callable(user_message):
        detail = getattr(exc, "detail", None)
        return f"{user_message()} ({detail})" if detail else user_message()
    return str(exc)


ANTICIPATED = (BlenderError, AssetError, GenerationError, net.NetError, ValueError, OSError)


def build_server(settings: Optional[Settings] = None, connection: Optional[BlenderConnection] = None,
                 generator: Optional[Generator] = None, asset_factories: Optional[Dict[str, Any]] = None):
    """Create the MCP server. ``connection``, ``generator`` and ``asset_factories`` are test seams."""
    settings = settings or Settings()
    connection = connection or BlenderConnection(
        settings.host, settings.port, settings.timeout, settings.token)
    cache_root = net.cache_dir(settings.cache_dir)
    generator = generator or Generator(cache_root, settings.tripo_api_key, settings.hyper3d_api_key,
                                       settings.generation_config)
    factories = {
        "polyhaven": lambda: PolyHaven(cache_root),
        "sketchfab": lambda: Sketchfab(settings.sketchfab_api_key, cache_root),
        "polypizza": lambda: PolyPizza(settings.polypizza_api_key, cache_root),
    }
    factories.update(asset_factories or {})
    server = compat.create_server("blender", INSTRUCTIONS)

    def send(command: str, params: Optional[dict] = None, timeout: Optional[float] = None) -> Any:
        """Blocking bridge call; raises BlenderError."""
        return connection.send_command(command, _drop_none(params or {}), timeout=timeout)

    async def offload(function, *args) -> Any:
        """Run blocking work in a thread and turn anticipated failures into readable tool errors."""
        try:
            return await anyio.to_thread.run_sync(partial(function, *args))
        except compat.ToolError:
            raise
        except ANTICIPATED as exc:
            raise compat.ToolError(_message(exc)) from exc
        except Exception as exc:
            if hasattr(exc, "user_message"):  # ai3dgenerator errors
                raise compat.ToolError(_message(exc)) from exc
            raise

    async def call(command: str, params: Optional[dict] = None, timeout: Optional[float] = None) -> Any:
        return await offload(send, command, params, timeout)

    def stage(root, files) -> Dict[Any, str]:
        return stage_files(lambda command, params: send(command, params, timeout=120), root, files)

    # ---- inspection --------------------------------------------------------

    @compat.tool(server, title="Bridge status", read_only=True, idempotent=True)
    async def get_bridge_status() -> dict:
        """Report whether Blender is reachable, its version, the bridge's capabilities, and which
        asset libraries and generators are configured."""
        status = {
            "mcp_server_version": __version__,
            "mcp_sdk_version": compat.sdk_version(),
            "safe_mode": settings.safe_mode,
            "blender_address": f"{settings.host}:{settings.port}",
            "asset_sources": {
                "polyhaven": "ready",
                "sketchfab": "ready" if settings.sketchfab_api_key else "search only (no API key)",
                "polypizza": "ready" if settings.polypizza_api_key else "needs BLENDER_MCP_POLYPIZZA_API_KEY",
            },
            "generators": generator.configured() + ["mock"],
        }
        try:
            status.update(await anyio.to_thread.run_sync(partial(send, "get_bridge_status", None, 10)))
            status["connected"] = True
        except BlenderError as exc:
            status.update(connected=False, error=str(exc))
        return status

    @compat.tool(server, title="Scene overview", read_only=True, idempotent=True)
    async def get_scene_info(
        limit: Annotated[int, Field(ge=1, le=5000, description="Maximum objects to list")] = 200,
    ) -> dict:
        """Summarize the active scene: render settings, camera, collections, and each object's
        transform, dimensions, parent, visibility, and materials."""
        return await call("get_scene_info", {"limit": limit})

    @compat.tool(server, title="List objects", read_only=True, idempotent=True)
    async def list_objects(
        object_type: Annotated[Optional[str], Field(description="Filter by type: MESH, CAMERA, LIGHT, EMPTY, CURVE, ...")] = None,
        collection: Annotated[Optional[str], Field(description="Only objects in this collection")] = None,
    ) -> list:
        """List object names, types, and locations, optionally filtered."""
        return await call("list_objects", {"object_type": object_type, "collection": collection})

    @compat.tool(server, title="Object details", read_only=True, idempotent=True)
    async def get_object_info(name: Annotated[str, Field(description="Exact object name")]) -> dict:
        """Detailed info for one object: world bounding box, mesh statistics, modifiers,
        constraints, children, custom properties, and light or camera settings."""
        return await call("get_object_info", {"name": name})

    # ---- building ----------------------------------------------------------

    @compat.tool(server, title="Create object")
    async def create_object(
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
        """Create a mesh primitive (with a UV map), light, camera, empty, or text object. The
        first camera in a scene becomes the scene camera automatically."""
        return await call("create_object", {
            "kind": kind, "name": name, "location": location, "rotation_degrees": rotation_degrees,
            "scale": scale, "size": size, "segments": segments, "collection": collection,
            "energy": energy, "lens": lens, "text": text, "make_active_camera": make_active_camera,
        })

    @compat.tool(server, title="Modify object", idempotent=True)
    async def modify_object(
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
        return await call("modify_object", {
            "name": name, "location": location, "rotation_degrees": rotation_degrees, "scale": scale,
            "dimensions": dimensions, "new_name": new_name, "visible": visible, "parent": parent,
        })

    @compat.tool(server, title="Delete objects", destructive=True)
    async def delete_objects(names: Annotated[List[str], Field(min_length=1)]) -> dict:
        """Delete objects by name. Reports which names were not found."""
        return await call("delete_objects", {"names": names})

    @compat.tool(server, title="Set material")
    async def set_material(
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
        return await call("set_material", {
            "object_name": object_name, "color": color, "metallic": metallic, "roughness": roughness,
            "emission_color": emission_color, "emission_strength": emission_strength, "alpha": alpha,
            "material_name": material_name, "append": append,
        })

    @compat.tool(server, title="Add modifier")
    async def add_modifier(
        object_name: str,
        modifier_type: Annotated[str, Field(description="Blender modifier type, e.g. SUBSURF, BEVEL, ARRAY, MIRROR, SOLIDIFY, BOOLEAN")],
        settings: Annotated[Optional[dict], Field(description="Modifier properties, e.g. {'levels': 2}; object/collection properties take names")] = None,
        name: Optional[str] = None,
    ) -> dict:
        """Add a modifier and set its properties. Unknown or invalid properties are reported
        back instead of failing the whole call."""
        return await call("add_modifier", {"object_name": object_name, "modifier_type": modifier_type,
                                           "settings": settings, "name": name})

    @compat.tool(server, title="Undo or redo", destructive=True)
    async def undo(
        steps: Annotated[int, Field(ge=1, le=100)] = 1,
        redo: Annotated[bool, Field(description="Redo instead of undo")] = False,
    ) -> dict:
        """Undo (or redo) the last changes, including each structured tool call. Needs Blender's
        UI; background mode keeps no undo history."""
        return await call("redo" if redo else "undo", {"steps": steps})

    # ---- files -------------------------------------------------------------

    @compat.tool(server, title="Import model", open_world=True)
    async def import_model(
        filepath: Annotated[str, Field(description="Path on the machine running Blender")],
        collection: Annotated[Optional[str], Field(description="Put imported objects in this collection")] = None,
        target_size: Annotated[Optional[float], Field(gt=0, description="Scale so the largest side is this many meters")] = None,
        location: Annotated[Optional[Vector3], Field(description="Where the model's bottom center goes")] = None,
        name: Annotated[Optional[str], Field(description="Rename the imported root object")] = None,
    ) -> dict:
        """Import a model file: .glb, .gltf, .fbx, .obj, .stl, .ply, .usd/.usda/.usdc/.usdz,
        .abc, .dae, .svg, or append all objects from a .blend. Picks the importer that exists
        in the running Blender version."""
        return await call("import_model", {"filepath": filepath, "collection": collection,
                                           "target_size": target_size, "location": location,
                                           "name": name}, timeout=600)

    @compat.tool(server, title="Export scene")
    async def export_scene(
        filepath: Annotated[str, Field(description="Output path; the extension picks the format")],
        object_names: Annotated[Optional[List[str]], Field(description="Export only these objects")] = None,
        selected_only: Optional[bool] = None,
        overwrite: Optional[bool] = None,
    ) -> dict:
        """Export to .glb, .gltf, .fbx, .obj, .stl, .ply, .usd/.usda/.usdc/.usdz, or .abc.
        The user's selection is restored afterwards."""
        return await call("export_scene", {"filepath": filepath, "object_names": object_names,
                                           "selected_only": selected_only, "overwrite": overwrite}, timeout=600)

    @compat.tool(server, title="Save .blend file", idempotent=True)
    async def save_blend_file(
        filepath: Annotated[Optional[str], Field(description="Save as this path; omit to save the current file")] = None,
        save_copy: Annotated[Optional[bool], Field(description="Write a copy without switching to it")] = None,
    ) -> dict:
        """Save the open Blender file."""
        return await call("save_blend_file", {"filepath": filepath, "copy": save_copy}, timeout=300)

    # ---- looking -----------------------------------------------------------

    @compat.tool(server, title="Render image", read_only=True)
    async def render_image(
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
        result = await call("render_image", {
            "view": view, "engine": engine, "width": width, "height": height, "samples": samples,
            "camera": camera, "filepath": filepath,
        }, timeout=timeout_seconds)
        return _image_result(result, "render")

    @compat.tool(server, title="Render several views", read_only=True)
    async def render_views(
        views: Annotated[Optional[List[RenderView]], Field(min_length=1, max_length=9, description="Default: front, right, top, iso")] = None,
        engine: RenderEngine = "auto",
        width: Annotated[int, Field(ge=16, le=2048, description="Width of each view")] = 384,
        samples: Annotated[Optional[int], Field(ge=1, le=4096, description="Cycles samples (default 8)")] = None,
        camera: Annotated[Optional[str], Field(description="Camera object for the 'camera' view")] = None,
        timeout_seconds: Annotated[float, Field(gt=0, le=3600)] = 300,
    ) -> list:
        """Render several auto-framed views into one image (left to right, top to bottom; the
        layout is returned with it). The quickest way to check proportions and placement."""
        result = await call("render_views", {"views": views, "engine": engine, "width": width,
                                             "samples": samples, "camera": camera}, timeout=timeout_seconds)
        return _image_result(result, "views")

    @compat.tool(server, title="Viewport screenshot", read_only=True)
    async def viewport_screenshot(
        max_size: Annotated[int, Field(ge=64, le=4096, description="Longest side in pixels")] = 1024,
    ) -> list:
        """Capture the 3D View exactly as the user sees it. Needs Blender's UI; in background
        mode use render_image."""
        return _image_result(await call("viewport_screenshot", {"max_size": max_size}), "viewport")

    # ---- lights, cameras, animation -----------------------------------------

    @compat.tool(server, title="Add or change a light", idempotent=True)
    async def set_light(
        name: Annotated[Optional[str], Field(description="Light to change; created when no object has this name")] = None,
        kind: Optional[LightKind] = None,
        energy: Annotated[Optional[float], Field(ge=0, description="Watts (point, spot, area) or W/m2 for sun (3-5 is daylight)")] = None,
        color: Annotated[Optional[Any], Field(description="'#RRGGBB' (sRGB) or [r, g, b] linear 0-1")] = None,
        temperature_kelvin: Annotated[Optional[float], Field(ge=1000, le=40000, description="Color as a temperature instead: 2700 warm bulb, 5500 daylight, 8000 shade")] = None,
        size: Annotated[Optional[float], Field(ge=0, description="Softness: area side or point/spot radius in meters; sun angle in degrees")] = None,
        spot_angle_degrees: Annotated[Optional[float], Field(ge=1, le=180)] = None,
        spot_blend: Annotated[Optional[float], Field(ge=0, le=1, description="Spot edge softness")] = None,
        location: Optional[Vector3] = None,
        rotation_degrees: Optional[Vector3] = None,
        target: Annotated[Optional[Target], Field(description="Aim at an object (its center) or an [x, y, z] point")] = None,
        track: Annotated[Optional[bool], Field(description="Keep aiming at the target object as things move (false removes)")] = None,
        collection: Optional[str] = None,
    ) -> dict:
        """Create a point, sun, spot, or area light, or change an existing one. Only the fields
        you pass change; target turns it toward an object or point."""
        return await call("set_light", {
            "name": name, "kind": kind, "energy": energy, "color": color,
            "temperature_kelvin": temperature_kelvin, "size": size, "spot_angle_degrees": spot_angle_degrees,
            "spot_blend": spot_blend, "location": location, "rotation_degrees": rotation_degrees,
            "target": target, "track": track, "collection": collection,
        })

    @compat.tool(server, title="Light the scene with a preset")
    async def setup_lighting(
        preset: LightingPreset = "three_point",
        target: Annotated[Optional[List[str]], Field(description="Objects to light; default: all visible geometry")] = None,
        strength: Annotated[float, Field(gt=0, le=100, description="Multiplies every light's power")] = 1.0,
        azimuth_degrees: Annotated[float, Field(ge=-360, le=360, description="Turn the rig around the subject")] = 0.0,
        world: Annotated[Optional[bool], Field(description="Also set a matching world background. Default: yes, unless the world is an HDRI or sky")] = None,
        replace: Annotated[bool, Field(description="Remove the lights of an earlier setup_lighting call")] = True,
    ) -> dict:
        """Place a lighting rig sized to the subject and oriented to the scene camera:
        three_point (key, fill, rim), studio (soft, bright, light-gray background), outdoor
        (sun and sky), or dramatic (warm hard key, cool rim, dark background). Lists the
        scene's other lights so you can hide them."""
        return await call("setup_lighting", {
            "preset": preset, "target": target, "strength": strength, "azimuth_degrees": azimuth_degrees,
            "world": world, "replace": replace,
        })

    @compat.tool(server, title="Add or aim a camera", idempotent=True)
    async def set_camera(
        name: Annotated[Optional[str], Field(description="Camera to change (default: the scene camera); created if missing")] = None,
        view: Annotated[Optional[CameraView], Field(description="Auto-place: frame `fit` (default: all visible geometry) from this side")] = None,
        fit: Annotated[Optional[List[str]], Field(description="Objects that must be in frame when view is given")] = None,
        location: Optional[Vector3] = None,
        target: Annotated[Optional[Target], Field(description="Look at an object (its center) or an [x, y, z] point")] = None,
        track: Annotated[Optional[bool], Field(description="Keep looking at the target object as things move (false removes)")] = None,
        rotation_degrees: Optional[Vector3] = None,
        lens: Annotated[Optional[float], Field(gt=0, description="Focal length in mm: 24 wide, 50 natural, 85+ portrait/product")] = None,
        margin: Annotated[float, Field(ge=1, le=10, description="Space around the framed objects")] = 1.1,
        focus: Annotated[Optional[Any], Field(description="Depth of field: object name or distance in meters to keep sharp")] = None,
        fstop: Annotated[Optional[float], Field(gt=0, le=128, description="Aperture: lower blurs more (1.4 strong, 8 subtle)")] = None,
        depth_of_field: Annotated[Optional[bool], Field(description="false turns depth of field off")] = None,
        resolution: Annotated[Optional[List[int]], Field(min_length=2, max_length=2, description="Scene render size [width, height]; sets the aspect ratio")] = None,
        make_active: Annotated[bool, Field(description="Make it the scene camera")] = True,
        collection: Optional[str] = None,
    ) -> dict:
        """Create or move a camera: auto-frame objects from a side (view + fit), or place it
        at a location looking at a target. Also sets lens, depth of field, and resolution."""
        return await call("set_camera", {
            "name": name, "view": view, "fit": fit, "location": location, "target": target, "track": track,
            "rotation_degrees": rotation_degrees, "lens": lens, "margin": margin, "focus": focus,
            "fstop": fstop, "depth_of_field": depth_of_field, "resolution": resolution,
            "make_active": make_active, "collection": collection,
        })

    @compat.tool(server, title="Animate with keyframes")
    async def insert_keyframes(
        object_name: str,
        keyframes: Annotated[List[Keyframe], Field(min_length=1, max_length=1000)],
        interpolation: Annotated[Interpolation, Field(description="bezier eases in and out, linear is constant speed, constant jumps")] = "bezier",
        extend_timeline: Annotated[bool, Field(description="Grow the scene frame range to include these frames")] = True,
    ) -> dict:
        """Keyframe an object's location, rotation, scale, visibility, or a property of its
        light/camera data (e.g. {"energy": 500}, {"lens": 35}) at given frames. Rotations keep
        full turns, so 0 -> 360 spins once."""
        frames = [key.model_dump(exclude_none=True) for key in keyframes]
        return await call("insert_keyframes", {"object_name": object_name, "keyframes": frames,
                                               "interpolation": interpolation,
                                               "extend_timeline": extend_timeline})

    @compat.tool(server, title="Remove animation", destructive=True)
    async def clear_animation(object_names: Annotated[List[str], Field(min_length=1)]) -> dict:
        """Delete all keyframes of these objects (and of their light/camera data)."""
        return await call("clear_animation", {"object_names": object_names})

    @compat.tool(server, title="Set the timeline", idempotent=True)
    async def set_timeline(
        frame_start: Annotated[Optional[int], Field(ge=0)] = None,
        frame_end: Annotated[Optional[int], Field(ge=0)] = None,
        fps: Annotated[Optional[int], Field(ge=1, le=240)] = None,
        frame_current: Optional[int] = None,
    ) -> dict:
        """Set the scene's frame range, frame rate, or current frame; returns the duration."""
        return await call("set_timeline", {"frame_start": frame_start, "frame_end": frame_end, "fps": fps,
                                           "frame_current": frame_current})

    @compat.tool(server, title="Create a turntable")
    async def create_turntable(
        target: Annotated[Optional[List[str]], Field(description="Objects to show; default: all visible geometry")] = None,
        mode: Annotated[TurntableMode, Field(description="camera orbits the subject; object spins one object in place")] = "camera",
        frames: Annotated[int, Field(ge=2, le=100000, description="Frames per full loop (120 = 5 s at 24 fps)")] = 120,
        turns: Annotated[float, Field(ge=-100, le=100, description="Turns per loop; negative goes clockwise")] = 1.0,
        frame_start: Annotated[int, Field(ge=0)] = 1,
        elevation_degrees: Annotated[float, Field(ge=-89, le=89, description="Camera height angle (camera mode)")] = 20.0,
        lens: Annotated[float, Field(gt=0, description="Camera focal length in mm (camera mode)")] = 50.0,
        margin: Annotated[float, Field(ge=1, le=10)] = 1.1,
    ) -> dict:
        """Make a seamless 360-degree loop: a camera orbiting the subject (made the scene
        camera) or one object spinning. Sets the frame range; then call render_animation."""
        return await call("create_turntable", {
            "target": target, "mode": mode, "frames": frames, "turns": turns, "frame_start": frame_start,
            "elevation_degrees": elevation_degrees, "lens": lens, "margin": margin,
        })

    @compat.tool(server, title="Render an animation", open_world=True)
    async def render_animation(
        format: Annotated[AnimationFormat, Field(description="mp4 video, gif (short loops, palette colors), or png frames")] = "mp4",
        filepath: Annotated[Optional[str], Field(description="Output file (folder for png); default: mcp_renders/ next to the .blend or in temp")] = None,
        frame_start: Optional[int] = None,
        frame_end: Optional[int] = None,
        frame_step: Annotated[int, Field(ge=1, le=1000, description="Render every Nth frame (playback keeps real time)")] = 1,
        fps: Annotated[Optional[int], Field(ge=1, le=240, description="Default: the scene's frame rate")] = None,
        width: Annotated[Optional[int], Field(ge=16, le=4096, description="Default: 960 (mp4/png) or 320 (gif)")] = None,
        height: Annotated[Optional[int], Field(ge=16, le=4096, description="Default: keeps the scene's aspect ratio")] = None,
        engine: RenderEngine = "auto",
        samples: Annotated[Optional[int], Field(ge=1, le=4096, description="Cycles samples per frame (default 16)")] = None,
        camera: Annotated[Optional[str], Field(description="Camera to render through (default: the scene camera)")] = None,
        preview_frames: Annotated[int, Field(ge=0, le=16, description="Frames shown back to you as one image (0 for none)")] = 6,
        timeout_seconds: Annotated[float, Field(gt=0, le=3600)] = 1800,
    ) -> list:
        """Render the frame range to a video, GIF, or PNG frames where Blender runs, and return
        a contact sheet of evenly spaced frames so you can check the motion. Blender is busy
        while it renders: keep sizes and samples small for previews."""
        result = await call("render_animation", {
            "format": format, "filepath": filepath, "frame_start": frame_start, "frame_end": frame_end,
            "frame_step": frame_step, "fps": fps, "width": width, "height": height, "engine": engine,
            "samples": samples, "camera": camera, "preview_frames": preview_frames,
        }, timeout=timeout_seconds)
        if "image_base64" in result:
            return _image_result(result, "animation")
        return [f"animation: {json.dumps(result)}"]

    # ---- python ------------------------------------------------------------

    @compat.tool(server, title="Run Blender Python", destructive=True, open_world=True)
    async def execute_blender_code(
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
        return await call("execute_code", {"code": code}, timeout=timeout_seconds)

    # ---- assets ------------------------------------------------------------

    @compat.tool(server, title="Search asset libraries", read_only=True, open_world=True)
    async def search_assets(
        source: AssetSource,
        query: Annotated[Optional[str], Field(description="What to look for, in plain words")] = None,
        asset_type: Annotated[Optional[PolyHavenType], Field(description="Poly Haven only: hdris, textures, or models")] = None,
        category: Annotated[Optional[str], Field(description="Poly Haven category path, Sketchfab category slug, or Poly Pizza category name")] = None,
        licence: Annotated[Optional[str], Field(description="Poly Pizza only: CC0 or CC-BY")] = None,
        animated: Annotated[Optional[bool], Field(description="Poly Pizza only: animated models only")] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        list_categories: Annotated[bool, Field(description="Poly Haven only: list category paths for asset_type instead")] = False,
    ) -> dict:
        """Search Poly Haven (free CC0 HDRIs, PBR textures, models), Sketchfab (downloadable
        models), or Poly Pizza (low-poly models). Then pass an id to import_asset."""

        def search() -> dict:
            library = factories[source]()
            if source == "polyhaven":
                if list_categories:
                    if not asset_type:
                        raise AssetError("list_categories needs asset_type (hdris, textures, or models)")
                    return library.categories(asset_type)
                return library.search(query, asset_type, category, limit)
            if source == "sketchfab":
                if not query:
                    raise AssetError("Sketchfab search needs a query")
                return library.search(query, limit, category)
            return library.search(query, category, licence, bool(animated), limit)

        return await offload(search)

    def import_fetched(fetched: FetchedAsset, options: Dict[str, Any]) -> Dict[str, Any]:
        props = fetched.custom_properties()
        if fetched.kind == "hdri":
            staged = stage(fetched.root, fetched.files)
            return send("set_world_hdri", {
                "filepath": staged[fetched.files[0]], "strength": options.get("hdri_strength"),
                "rotation_degrees": options.get("hdri_rotation"), "name": fetched.name,
                "custom_properties": props}, timeout=300)
        if fetched.kind == "texture":
            staged = stage(fetched.root, list(fetched.maps.values()))
            return send("create_pbr_material", {
                "name": options.get("name") or fetched.name,
                "maps": {role: staged[path] for role, path in fetched.maps.items()},
                "apply_to": options.get("apply_to"), "uv_scale": options.get("uv_scale"),
                "custom_properties": props}, timeout=300)
        staged = stage(fetched.root, fetched.files)
        params = {"filepath": staged[fetched.files[0]], "collection": options.get("collection"),
                  "target_size": options.get("target_size"), "location": options.get("location"),
                  "name": options.get("name"), "custom_properties": props}
        if fetched.blend_collections:
            params["blend_collection"] = fetched.blend_collections
        try:
            return send("import_model", params, timeout=600)
        except BlenderError as exc:
            if exc.code != "unreadable" or fetched.fallback is None:
                raise
        fallback = fetched.fallback()
        staged = stage(fallback.root, fallback.files)
        params["filepath"] = staged[fallback.files[0]]
        params.pop("blend_collection", None)
        result = send("import_model", params, timeout=600)
        result["note"] = "This Blender could not read the artist's .blend; imported the glTF version."
        return result

    @compat.tool(server, title="Import asset", open_world=True)
    async def import_asset(
        source: AssetSource,
        asset_id: Annotated[str, Field(description="The id from search_assets")],
        asset_type: Annotated[Optional[PolyHavenType], Field(description="Poly Haven only; looked up when omitted")] = None,
        resolution: Annotated[Optional[str], Field(description="Poly Haven only: 1k (default), 2k, 4k, 8k")] = None,
        file_format: Annotated[Optional[str], Field(description="Poly Haven only: hdr/exr for HDRIs, jpg/png/exr for textures")] = None,
        apply_to: Annotated[Optional[List[str]], Field(description="Textures: objects that get the new material")] = None,
        uv_scale: Annotated[Optional[float], Field(gt=0, description="Textures: repeat the texture this many times")] = None,
        target_size: Annotated[Optional[float], Field(gt=0, description="Models: largest side in meters")] = None,
        location: Annotated[Optional[Vector3], Field(description="Models: where the bottom center goes")] = None,
        collection: Annotated[Optional[str], Field(description="Models: collection for the imported objects")] = None,
        name: Annotated[Optional[str], Field(description="Name for the model root or material")] = None,
        hdri_strength: Annotated[Optional[float], Field(ge=0, description="HDRIs: brightness (default 1)")] = None,
        hdri_rotation: Annotated[Optional[float], Field(description="HDRIs: turn the environment, in degrees")] = None,
        ctx: compat.Context = None,
    ) -> dict:
        """Download an asset (cached) and bring it into Blender: an HDRI becomes the world
        lighting, a texture set becomes a PBR material on apply_to, a model is imported at
        target_size and location. Attribution is stored in custom properties (mcp_*)."""
        await compat.report_progress(ctx, 0.05, 1.0, f"Downloading {source} asset {asset_id}")

        def fetch() -> FetchedAsset:
            library = factories[source]()
            if source == "polyhaven":
                return library.fetch(asset_id, asset_type, resolution or "1k", file_format)
            return library.fetch(asset_id)

        fetched = await offload(fetch)
        await compat.report_progress(ctx, 0.6, 1.0, "Importing into Blender")
        options = {"apply_to": apply_to, "uv_scale": uv_scale, "target_size": target_size,
                   "location": location, "collection": collection, "name": name,
                   "hdri_strength": hdri_strength, "hdri_rotation": hdri_rotation}
        result = await offload(import_fetched, fetched, options)
        await compat.report_progress(ctx, 1.0, 1.0, "Imported")
        return {"source": source, "asset_id": asset_id, "kind": fetched.kind, "name": fetched.name,
                "attribution": fetched.attribution, "blender": result}

    # ---- generation --------------------------------------------------------

    def import_generated(job) -> Dict[str, Any]:
        with job.import_lock:  # import exactly once, even when status is polled concurrently
            if job.imported is None:
                path = generator.output_path(job)
                staged = stage(path.parent, [path])
                properties = {"mcp_source": job.provider, "mcp_prompt": job.prompt[:500]}
                job.imported = send("import_model", dict(job.import_options, filepath=staged[path],
                                                         custom_properties=properties), timeout=600)
            return job.imported

    async def wait_and_import(job, wait_seconds: float, ctx) -> Dict[str, Any]:
        deadline = time.monotonic() + wait_seconds
        while not job.finished and time.monotonic() < deadline:
            snapshot = job.manager.snapshot
            await compat.report_progress(ctx, snapshot.progress, 1.0, snapshot.message)
            await anyio.sleep(0.5)
        status = job.status()
        if not job.finished:
            status["next_step"] = (f"Still generating. Call get_generation_status(job_id='{job.job_id}') "
                                   "to keep waiting; the model is imported when it finishes.")
            return status
        if status["state"] != "completed":
            raise compat.ToolError(f"Generation {status['state']}: {status['error'] or status['message']}")
        status["imported"] = await offload(import_generated, job)
        return status

    @compat.tool(server, title="Generate a 3D model", open_world=True)
    async def generate_3d(
        prompt: Annotated[Optional[str], Field(description="What to generate, e.g. 'a weathered wooden treasure chest'")] = None,
        provider: Annotated[Optional[GeneratorName], Field(description="Default: the first configured of tripo, hyper3d, custom_api")] = None,
        image_path: Annotated[Optional[str], Field(description="Reference PNG/JPG/WEBP on the MCP server's machine (image-to-3D)")] = None,
        negative_prompt: Optional[str] = None,
        name: Annotated[Optional[str], Field(description="Name for the imported root object")] = None,
        target_size: Annotated[Optional[float], Field(gt=0, description="Largest side in meters")] = None,
        location: Annotated[Optional[Vector3], Field(description="Where the bottom center goes")] = None,
        collection: Optional[str] = None,
        wait_seconds: Annotated[float, Field(ge=0, le=600, description="How long to wait before returning a job id")] = 50,
        timeout_seconds: Annotated[float, Field(ge=10, le=7200, description="Give up on the job after this long")] = 900,
        ctx: compat.Context = None,
    ) -> dict:
        """Generate a 3D model from text (or a reference image) with Tripo, Hyper3D Rodin, or a
        custom REST service, then import it. If it takes longer than wait_seconds, a job_id is
        returned: call get_generation_status with it until the model is imported."""
        if not (prompt or "").strip() and not image_path:
            raise compat.ToolError("Pass a prompt, an image_path, or both.")
        options = _drop_none({"name": name, "target_size": target_size, "location": location,
                              "collection": collection})
        job = await offload(partial(generator.start, provider, prompt or "", image_path, negative_prompt,
                                    timeout_seconds, options))
        return await wait_and_import(job, wait_seconds, ctx)

    @compat.tool(server, title="Generation status", open_world=True)
    async def get_generation_status(
        job_id: str,
        wait_seconds: Annotated[float, Field(ge=0, le=600)] = 50,
        cancel: Annotated[bool, Field(description="Cancel the job instead of waiting")] = False,
        ctx: compat.Context = None,
    ) -> dict:
        """Check (and wait for) a generate_3d job; imports the model once it completes."""
        try:
            job = generator.get(job_id)
        except GenerationError as exc:
            raise compat.ToolError(str(exc)) from exc
        if cancel:
            job.manager.cancel()
            return job.status()
        return await wait_and_import(job, wait_seconds, ctx)

    # ---- guides ------------------------------------------------------------

    @compat.tool(server, title="Read a guide", read_only=True, idempotent=True)
    async def get_guide(topic: GuideTopic) -> str:
        """Short guide for working in Blender through these tools: workflow (start here),
        modeling, materials, lighting (and cameras/rendering), animation, python, assets."""
        return guides.load(topic)

    def guide_reader(topic: str):
        # Static resource URIs must map to functions without parameters (mcp 1.x checks this).
        def reader() -> str:
            return guides.load(topic)

        reader.__name__ = f"guide_{topic}"
        return reader

    for topic, description in guides.TOPICS.items():
        compat.resource(server, f"blender://guides/{topic}", name=f"guide_{topic}",
                        description=description)(guide_reader(topic))

    @compat.prompt(server, name="build_scene", description="Plan, build, and check a Blender scene from a description")
    def build_scene(description: str) -> str:
        return (
            f"Build this scene in Blender: {description}\n\n"
            "1. Call get_bridge_status and get_scene_info, and read get_guide('workflow').\n"
            "2. Write a short plan with real-world sizes for every object before building.\n"
            "3. Build with create_object, modify_object, set_material, and add_modifier. Use "
            "search_assets/import_asset for HDRI lighting, textures, and detailed models, or "
            "generate_3d for unique objects.\n"
            "4. Check the result with render_views, fix problems, and check again.\n"
            "5. Finish with render_image from the camera and summarize what you built."
        )

    return server


def main(settings: Optional[Settings] = None) -> None:
    settings = settings or load_settings()
    server = build_server(settings)
    compat.run(server, settings.transport, settings.http_host, settings.http_port)


if __name__ == "__main__":
    main()
