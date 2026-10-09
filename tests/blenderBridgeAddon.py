"""Tests for the Blender MCP Bridge add-on against real Blender (the ``bpy`` wheel).

Skipped unless ``bpy`` is importable, e.g. ``pip install bpy==4.5.*`` on Python 3.11.
Commands run on the test's main thread, which is Blender's main thread here.
"""
from __future__ import annotations

import base64
import os
import threading
import time

import pytest

bpy = pytest.importorskip("bpy")

from blenderMcp.connection import BlenderConnection, BlenderError  # noqa: E402
from blenderMcp.runHeadless import load_addon  # noqa: E402

addon = load_addon()


@pytest.fixture(autouse=True)
def fresh_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon._state["allow_code"] = True
    yield
    addon.stop_server()


def run(command, **params):
    response = addon.run_command({"id": "t", "type": command, "params": params})
    assert response["id"] == "t"
    if response["status"] != "success":
        raise AssertionError("%s failed [%s]: %s" % (command, response.get("code"), response.get("message")))
    return response["result"]


def run_error(command, **params):
    response = addon.run_command({"id": "t", "type": command, "params": params})
    assert response["status"] == "error", response
    return response["code"], response["message"]


def check_bridge_status_reports_background_mode():
    status = run("get_bridge_status")
    assert status["background"] is True
    assert status["gpu_rendering"] is (os.environ.get(addon.GPU_ENV) == "1")
    assert status["blender_version_tuple"][0] >= 3


def check_every_object_kind_can_be_created():
    for kind in addon.OBJECT_KINDS:
        result = run("create_object", kind=kind, name="K_" + kind, location=[1, 2, 3], size=1.5)
        assert result["location"] == [1.0, 2.0, 3.0]
    types = {o.name: o.type for o in bpy.data.objects}
    assert types["K_uv_sphere"] == "MESH"
    assert types["K_light_sun"] == "LIGHT" and types["K_camera"] == "CAMERA"
    assert types["K_empty"] == "EMPTY" and types["K_text"] == "FONT"
    assert bpy.context.scene.camera.name == "K_camera"
    assert run("get_object_info", name="K_uv_sphere")["dimensions"] == [1.5, 1.5, 1.5]


def check_invalid_create_parameters_are_reported():
    assert run_error("create_object", kind="hexagon")[0] == "invalid_params"
    assert run_error("create_object", kind="cube", location=[1, 2])[0] == "invalid_params"
    assert run_error("create_object", kind="cube", size=-1)[0] == "invalid_params"


def check_modify_object_transform_dimensions_and_parent():
    run("create_object", kind="cube", name="Box")
    run("create_object", kind="empty", name="Pivot", location=[5, 0, 0])
    result = run("modify_object", name="Box", location=[0, 0, 1], rotation_degrees=[0, 0, 90],
                 dimensions=[4, 2, 1], new_name="Crate")
    assert result["name"] == "Crate"
    assert result["dimensions"] == [4.0, 2.0, 1.0]
    assert result["rotation_degrees"][2] == 90.0
    world_before = bpy.data.objects["Crate"].matrix_world.translation.copy()
    assert run("modify_object", name="Crate", parent="Pivot")["parent"] == "Pivot"
    assert (bpy.data.objects["Crate"].matrix_world.translation - world_before).length < 1e-5
    assert run("modify_object", name="Crate", parent="")["parent"] is None


def check_rotation_respects_quaternion_mode():
    run("create_object", kind="cone", name="Q")
    bpy.data.objects["Q"].rotation_mode = "QUATERNION"
    result = run("modify_object", name="Q", rotation_degrees=[0, 90, 0])
    assert bpy.data.objects["Q"].rotation_mode == "QUATERNION"
    assert abs(result["rotation_degrees"][1] - 90.0) < 1e-3


def check_material_hex_colors_are_linearized():
    run("create_object", kind="uv_sphere", name="Ball")
    result = run("set_material", object_name="Ball", color="#ff8800", metallic=1, roughness=0.2,
                 emission_color="#0000ff")
    assert result["slots"] == ["Ball_Material"]
    material = bpy.data.materials["Ball_Material"]
    node = next(n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    r, g, b, a = node.inputs["Base Color"].default_value
    assert (round(r, 3), round(g, 3), b, a) == (1.0, 0.246, 0.0, 1.0)
    assert node.inputs["Metallic"].default_value == 1.0
    assert node.inputs["Emission Strength"].default_value == 1.0
    assert run_error("set_material", object_name="Ball", color="orange")[0] == "invalid_params"
    run("create_object", kind="empty", name="Nothing")
    assert run_error("set_material", object_name="Nothing", color="#ffffff")[0] == "invalid_params"


def check_modifier_settings_are_applied_or_reported():
    run("create_object", kind="cube", name="Base")
    run("create_object", kind="empty", name="MirrorAxis")
    bevel = run("add_modifier", object_name="Base", modifier_type="bevel",
                settings={"width": 0.1, "segments": 3, "nonsense": 1})
    assert bevel["applied"] == {"width": 0.1, "segments": 3}
    assert "nonsense" in bevel["rejected"]
    mirror = run("add_modifier", object_name="Base", modifier_type="MIRROR", settings={"mirror_object": "MirrorAxis"})
    assert mirror["applied"] == {"mirror_object": "MirrorAxis"}
    code, message = run_error("add_modifier", object_name="Base", modifier_type="EXPLODE_EVERYTHING")
    assert code == "invalid_params" and "SUBSURF" in message


@pytest.mark.parametrize("extension", [".glb", ".gltf", ".fbx", ".obj", ".stl", ".ply", ".usdc", ".abc"])
def check_export_import_round_trip(tmp_path, extension):
    run("create_object", kind="cube", name="Keep")
    run("create_object", kind="cone", name="Other")
    bpy.data.objects["Other"].select_set(True)
    result = run("export_scene", filepath=str(tmp_path / ("out" + extension)), object_names=["Keep"])
    assert result["exists"] and result["bytes"] > 0
    assert [o.name for o in bpy.context.view_layer.objects if o.select_get()] == ["Other"]
    imported = run("import_model", filepath=result["filepath"], collection="Imported")
    assert imported["imported_objects"]
    assert all(bpy.data.collections["Imported"] in bpy.data.objects[n].users_collection
               for n in imported["imported_objects"])


def check_import_errors_are_clear(tmp_path):
    assert run_error("import_model", filepath=str(tmp_path / "missing.glb"))[0] == "not_found"
    unknown = tmp_path / "model.xyz"
    unknown.write_text("x")
    assert "unsupported format" in run_error("import_model", filepath=str(unknown))[1]


def check_blend_save_copy_and_append(tmp_path):
    run("create_object", kind="monkey", name="Suzanne")
    saved = run("save_blend_file", filepath=str(tmp_path / "scene"), copy=True)
    assert saved["filepath"].endswith("scene.blend") and os.path.exists(saved["filepath"])
    assert bpy.data.filepath == ""
    appended = run("import_model", filepath=saved["filepath"])
    assert appended["imported_objects"] == ["Suzanne.001"]


def check_render_cleans_up_and_restores_settings():
    run("create_object", kind="torus", name="Donut")
    render = bpy.context.scene.render
    before = (render.engine, render.resolution_x, render.resolution_y, render.filepath)
    result = run("render_image", view="iso", width=64, samples=1)
    assert base64.b64decode(result["image_base64"]).startswith(b"\x89PNG")
    assert (result["width"], result["height"], result["engine"]) == (64, 48, "CYCLES")
    assert (render.engine, render.resolution_x, render.resolution_y, render.filepath) == before
    assert not [o.name for o in bpy.data.objects if o.name.startswith("MCP_")]
    assert not [c.name for c in bpy.data.cameras if c.name.startswith("MCP_")]
    assert bpy.context.scene.camera is None


def check_render_keeps_a_copy_in_a_new_folder(tmp_path):
    run("create_object", kind="cube", name="Box")
    target = tmp_path / "nested" / "shot.png"
    result = run("render_image", view="front", width=32, samples=1, filepath=str(target))
    assert result["filepath"] == str(target) and target.read_bytes().startswith(b"\x89PNG")


@pytest.mark.skipif(os.environ.get("BLENDER_MCP_GPU") == "1", reason="GPU rendering enabled")
def check_gpu_engines_are_refused_in_background_mode():
    assert run_error("render_image", engine="eevee")[0] == "gpu_unavailable"
    assert run_error("render_image", engine="workbench")[0] == "gpu_unavailable"
    assert run_error("viewport_screenshot")[0] == "unsupported"


def check_execute_code_and_disable_switch():
    result = run("execute_code", code="print('hi')\nresult = {'objects': len(D.objects)}")
    assert result == {"stdout": "hi\n", "result": {"objects": 0}}
    assert run("execute_code", code="result = object()")["result"].startswith("<object")
    code, message = run_error("execute_code", code="1/0")
    assert code == "exception" and "ZeroDivisionError" in message
    addon._state["allow_code"] = False
    assert run_error("execute_code", code="result = 1")[0] == "disabled"


def check_unknown_command():
    code, message = run_error("does_not_exist")
    assert code == "unknown_command" and "create_object" in message


def _serve_while(client, timeout=20):
    """Run client() in a thread while draining the bridge queue on this (main) thread."""
    outcome = {}

    def target():
        try:
            outcome["value"] = client()
        except Exception as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=target)
    thread.start()
    deadline = time.time() + timeout
    while thread.is_alive() and time.time() < deadline:
        addon.drain_queue()
        time.sleep(0.005)
    thread.join(1)
    return outcome


def check_socket_round_trip_with_token():
    addon.start_server(host="127.0.0.1", port=0, token="tok", allow_code=True, use_timer=False)
    port = addon._state["port"]
    good = _serve_while(lambda: BlenderConnection(port=port, token="tok").send_command("ping"))
    assert good["value"]["pong"] is True
    bad = _serve_while(lambda: BlenderConnection(port=port, token="nope").send_command("ping"))
    assert isinstance(bad["error"], BlenderError) and bad["error"].code == "unauthorized"


def check_timed_out_commands_are_cancelled_not_run_later():
    addon.start_server(host="127.0.0.1", port=0, token="", allow_code=True, use_timer=False)
    port = addon._state["port"]
    with pytest.raises(BlenderError) as info:
        # Nothing drains the queue, so the bridge gives up after its 1 s minimum wait.
        BlenderConnection(port=port, timeout=1).send_command("create_object", {"kind": "cube", "name": "Late"})
    assert info.value.code == "timeout"
    addon.drain_queue()
    assert "Late" not in bpy.data.objects


# ---- 0.3.0 commands --------------------------------------------------------

def _image_file(path, color, file_format="PNG", float_buffer=False, size=(8, 8)):
    image = bpy.data.images.new(os.path.basename(path), size[0], size[1], float_buffer=float_buffer)
    image.pixels[:] = list(color) * (size[0] * size[1])
    image.filepath_raw = str(path)
    image.file_format = file_format
    image.save()
    bpy.data.images.remove(image)
    return str(path)


def _png_size(data):
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def check_every_primitive_has_a_uv_map():
    for kind in addon.MESH_KINDS:
        run("create_object", kind=kind, name="UV_" + kind)
    assert [o.name for o in bpy.data.objects if not o.data.uv_layers] == []


def check_world_hdri_keeps_previous_world(tmp_path):
    hdr = _image_file(tmp_path / "sky.hdr", (0.5, 0.7, 1.0, 1.0), "HDR", float_buffer=True)
    old = bpy.data.worlds.new("Old")
    bpy.context.scene.world = old
    result = run("set_world_hdri", filepath=hdr, strength=2, rotation_degrees=90,
                 custom_properties={"mcp_source": "polyhaven"})
    world = bpy.context.scene.world
    assert result["previous_world"] == "Old" and old.use_fake_user
    assert world.name == result["world"] and world["mcp_source"] == "polyhaven"
    assert result["packed"] is True
    background = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    assert background.inputs["Strength"].default_value == 2
    assert run_error("set_world_hdri", filepath=str(tmp_path / "missing.hdr"))[0] == "not_found"


def check_pbr_material_with_and_without_uvs(tmp_path):
    maps = {role: _image_file(tmp_path / (role + ".png"), color) for role, color in (
        ("base_color", (0.8, 0.3, 0.1, 1)), ("roughness", (0.5, 0.5, 0.5, 1)), ("normal", (0.5, 0.5, 1, 1)),
        ("displacement", (0.5, 0.5, 0.5, 1)), ("ao", (1, 1, 1, 1)))}
    run("create_object", kind="cube", name="Wall")
    result = run("create_pbr_material", name="Rock", maps=maps, apply_to=["Wall"], uv_scale=2,
                 custom_properties={"mcp_asset_id": "rock"})
    assert result["maps"] == ["base_color", "roughness", "normal", "displacement"]
    assert result["ignored"] == ["ao"] and result["warnings"] == []
    material = bpy.data.materials["Rock"]
    assert material["mcp_asset_id"] == "rock" and bpy.data.objects["Wall"].active_material == material
    spaces = {n.image.colorspace_settings.name for n in material.node_tree.nodes if n.type == "TEX_IMAGE"}
    assert spaces == {"sRGB", "Non-Color"}

    mesh = bpy.data.meshes.new("NoUV")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [], [(0, 1, 2)])
    bpy.context.scene.collection.objects.link(bpy.data.objects.new("NoUV", mesh))
    fallback = run("create_pbr_material", name="Rock2", maps=maps, apply_to=["NoUV"])
    assert "normal" in fallback["ignored"] and "box projection" in fallback["warnings"][0]
    assert run_error("create_pbr_material", name="X", maps={"glow": maps["ao"]})[0] == "invalid_params"


def check_file_transfer_commands(tmp_path):
    data = os.urandom(3000)
    first = run("receive_file", transfer_id="feedc0de", relpath="tex/a.bin", offset=0,
                data_base64=base64.b64encode(data[:1000]).decode())
    second = run("receive_file", transfer_id="feedc0de", relpath="tex/a.bin", offset=1000,
                 data_base64=base64.b64encode(data[1000:]).decode())
    with open(second["filepath"], "rb") as handle:
        assert handle.read() == data
    assert first["filepath"] == second["filepath"] and second["size"] == 3000
    assert run_error("receive_file", transfer_id="feedc0de", relpath="tex/a.bin", offset=5,
                     data_base64="")[0] == "out_of_order"
    restarted = run("receive_file", transfer_id="feedc0de", relpath="tex/a.bin", offset=0,
                    data_base64=base64.b64encode(b"new").decode())
    assert restarted["size"] == 3
    for bad in ("../x", "/etc/x", "C:/x", "a/../../x"):
        assert "unsafe" in run_error("receive_file", transfer_id="feedc0de", relpath=bad, offset=0,
                                     data_base64="")[1]
    assert run_error("receive_file", transfer_id="NOT-HEX", relpath="a", offset=0, data_base64="")[0] == "invalid_params"
    stat = run("stat_file", filepath=second["filepath"])
    assert stat == {"filepath": second["filepath"], "exists": True, "size": 3}
    assert run("stat_file", filepath=str(tmp_path / "none"))["exists"] is False


def check_blend_import_picks_collection_and_fits(tmp_path):
    run("create_object", kind="monkey", name="Pot_LOD0", collection="pot_LOD0", size=4, location=[5, 5, 5])
    run("create_object", kind="cube", name="Pot_LOD1", collection="pot_LOD1")
    library = run("save_blend_file", filepath=str(tmp_path / "pot.blend"), copy=True)["filepath"]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    result = run("import_model", filepath=library, blend_collection=["pot_LOD0", "pot"], target_size=1,
                 location=[0, 0, 0], name="Pot", custom_properties={"mcp_asset_id": "pot"}, collection="Props")
    assert result["imported_objects"] == ["Pot"] and result["root_objects"] == ["Pot"]
    pot = bpy.data.objects["Pot"]
    bpy.context.view_layer.update()
    assert abs(max(pot.dimensions) - 1.0) < 1e-4
    low = min((pot.matrix_world @ addon.Vector(c)).z for c in pot.bound_box)
    assert abs(low) < 1e-4 and pot["mcp_asset_id"] == "pot"
    assert "Pot_LOD1" not in bpy.data.objects


def check_unreadable_blend_reports_its_own_code(tmp_path):
    broken = tmp_path / "future.blend"
    broken.write_bytes(b"BLENDER17-01v9900" + os.urandom(64))
    code, message = run_error("import_model", filepath=str(broken))
    assert code == "unreadable" and "future.blend" in message


def check_render_views_builds_a_grid():
    run("create_object", kind="cone", name="Cone")
    result = run("render_views", views=["front", "right", "top"], width=32, samples=1)
    data = base64.b64decode(result["image_base64"])
    assert data.startswith(b"\x89PNG")
    assert _png_size(data) == (2 * 32 + 4, 2 * 24 + 4)
    assert result["layout"] == [["front", "right"], ["top"]]
    assert not [o.name for o in bpy.data.objects if o.name.startswith("MCP_")]
    assert run_error("render_views", views=["sideways"])[0] == "invalid_params"


def check_undo_needs_the_ui():
    assert run_error("undo")[0] == "unsupported"
    assert "undo" in run("get_bridge_status")["commands"]


# ---- full chain: MCP tool -> download -> bridge -> Blender ------------------

from bridgeHelpers import http_api, is_error, run_tools, text_of  # noqa: E402,F401


def check_assets_and_generation_end_to_end(http_api, tmp_path):
    """Real downloads (from a local fake Poly Haven) and a mock generation, imported into real Blender."""
    pytest.importorskip("mcp")
    import json

    from blenderMcp.assets import PolyHaven
    from blenderMcp.config import Settings
    from blenderMcp.generation import Generator
    from blenderMcp.server import build_server

    def file_bytes(path):
        with open(path, "rb") as handle:
            return handle.read()

    hdr = file_bytes(_image_file(tmp_path / "sky.hdr", (0.6, 0.7, 1.0, 1.0), "HDR", float_buffer=True))
    diffuse = file_bytes(_image_file(tmp_path / "diff.png", (0.7, 0.4, 0.2, 1.0)))
    normal = file_bytes(_image_file(tmp_path / "nor.png", (0.5, 0.5, 1.0, 1.0)))
    run("create_object", kind="cone", name="crate_body", collection="crate_LOD0", size=3)
    run("create_object", kind="cube", name="crate_low", collection="crate_LOD1")
    blend = file_bytes(run("save_blend_file", filepath=str(tmp_path / "crate.blend"), copy=True)["filepath"])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    run("create_object", kind="plane", name="Floor", size=10)

    http_api.add("GET", "/info/sky", {"name": "Sky", "type": 0, "authors": {"Ann": "All"}})
    http_api.add("GET", "/files/sky", {"hdri": {"1k": {"hdr": http_api.add_file("/sky.hdr", hdr)}}})
    http_api.add("GET", "/info/planks", {"name": "Planks", "type": 1, "authors": {"Bo": "All"}})
    http_api.add("GET", "/files/planks", {
        "Diffuse": {"1k": {"jpg": http_api.add_file("/planks_diff.jpg", diffuse)}},
        "nor_gl": {"1k": {"jpg": http_api.add_file("/planks_nor.jpg", normal)}}})
    http_api.add("GET", "/info/crate", {"name": "Crate", "type": 2, "authors": {"Cy": "All"}})
    http_api.add("GET", "/files/crate", {"blend": {"1k": {"blend": dict(
        http_api.add_file("/crate.blend", blend),
        include={"textures/crate_diff.png": http_api.add_file("/crate_diff.png", diffuse)})}}})

    addon.start_server(host="127.0.0.1", port=0, token="", allow_code=True, use_timer=False)
    connection = BlenderConnection(port=addon._state["port"], timeout=60)
    server = build_server(Settings(cache_dir=str(tmp_path / "cache")), connection,
                          generator=Generator(tmp_path / "generated"),
                          asset_factories={"polyhaven": lambda: PolyHaven(tmp_path / "ph", api=http_api.base)})
    outcome = _serve_while(lambda: run_tools(server, [
        ("import_asset", {"source": "polyhaven", "asset_id": "sky", "hdri_strength": 1.5}),
        ("import_asset", {"source": "polyhaven", "asset_id": "planks", "apply_to": ["Floor"], "uv_scale": 4}),
        ("import_asset", {"source": "polyhaven", "asset_id": "crate", "target_size": 0.8, "location": [2, 0, 0]}),
        ("generate_3d", {"prompt": "a box", "provider": "mock", "name": "Generated", "target_size": 0.5,
                         "location": [-2, 0, 0], "wait_seconds": 30}),
    ]), timeout=120)
    assert "error" not in outcome, outcome.get("error")
    _, results = outcome["value"]
    for result in results:
        assert not is_error(result), text_of(result)

    world = bpy.context.scene.world
    assert world.name == "Sky" and world["mcp_license"] == "CC0" and world["mcp_author"] == "Ann"
    floor_material = bpy.data.objects["Floor"].active_material
    assert floor_material.name == "Planks" and floor_material["mcp_asset_id"] == "planks"
    crate = bpy.data.objects["crate_body"]
    bpy.context.view_layer.update()
    assert abs(max(crate.dimensions) - 0.8) < 1e-4 and "crate_low" not in bpy.data.objects
    assert crate["mcp_url"] == "https://polyhaven.com/a/crate"
    generated = bpy.data.objects["Generated"]
    assert generated["mcp_source"] == "mock" and abs(max(generated.dimensions) - 0.5) < 1e-4
    assert json.loads(text_of(results[3]))["state"] == "completed"


# ---- lights, cameras, animation ---------------------------------------------

def _in_view(camera_name, object_names):
    """True when every bounding-box corner of the objects projects inside the camera's frame."""
    from bpy_extras.object_utils import world_to_camera_view
    from mathutils import Vector

    scene = bpy.context.scene
    bpy.context.view_layer.update()
    camera = bpy.data.objects[camera_name]
    for name in object_names:
        obj = bpy.data.objects[name]
        for corner in obj.bound_box:
            x, y, depth = world_to_camera_view(scene, camera, obj.matrix_world @ Vector(corner))
            if not (0 <= x <= 1 and 0 <= y <= 1 and depth > 0):
                return False
    return True


def _forward(obj):
    from mathutils import Vector

    bpy.context.view_layer.update()
    return (obj.matrix_world.to_quaternion() @ Vector((0, 0, -1))).normalized()


def check_set_light_creates_aims_and_updates():
    run("create_object", kind="cube", name="Subject", location=[0, 0, 1])
    light = run("set_light", name="Key", kind="area", location=[4, -4, 5], target="Subject", energy=800,
                temperature_kelvin=3200, size=2)
    assert light["created"] is True and light["type"] == "LIGHT"
    assert light["light"]["type"] == "area" and light["light"]["energy"] == 800 and light["light"]["size"] == 2
    red, green, blue = light["light"]["color"]
    assert red == 1.0 and red > green > blue  # warm
    obj = bpy.data.objects["Key"]
    toward = (bpy.data.objects["Subject"].location - obj.location).normalized()
    assert _forward(obj).dot(toward) > 0.999

    changed = run("set_light", name="Key", energy=1200, color="#ffffff", track=True, target="Subject")
    assert changed["created"] is False and changed["light"]["energy"] == 1200
    assert changed["light"]["color"] == [1.0, 1.0, 1.0] and changed["light"]["tracking"] == "Subject"
    assert run("set_light", name="Key", track=False)["light"]["tracking"] is None
    spot = run("set_light", name="Key", kind="spot", spot_angle_degrees=30, spot_blend=0.5)
    assert spot["light"]["type"] == "spot" and spot["light"]["spot_angle_degrees"] == 30
    sun = run("set_light", kind="sun", rotation_degrees=[45, 0, 30], size=1)
    assert sun["name"] == "SunLight" and sun["light"]["angle_degrees"] == 1

    assert run_error("set_light", name="Subject", energy=1)[0] == "invalid_params"
    assert run_error("set_light", name="Key", color="#ff0000", temperature_kelvin=5000)[0] == "invalid_params"
    assert run_error("set_light", kind="point", spot_angle_degrees=20)[0] == "invalid_params"
    assert run_error("set_light", kind="laser")[0] == "invalid_params"
    assert run_error("set_light", kind="area", target="Ghost")[0] == "not_found"


def check_setup_lighting_presets_replace_and_keep_hdri_worlds(tmp_path):
    run("create_object", kind="monkey", name="Hero", location=[0, 0, 1])
    run("set_light", name="OldLamp", kind="point", location=[0, 0, 5])
    old_world = bpy.data.worlds.new("UserWorld")
    bpy.context.scene.world = old_world
    expected = {"three_point": 3, "studio": 4, "outdoor": 1, "dramatic": 2}
    previous = []
    for preset, count in expected.items():
        result = run("setup_lighting", preset=preset, target=["Hero"])
        assert len(result["lights"]) == count, preset
        assert sorted(result["removed"]) == sorted(previous)
        assert result["other_lights"] == ["OldLamp"] and result["world_replaced"] is True
        assert result["world"] == bpy.context.scene.world.name and bpy.context.scene.world.name.startswith("MCP ")
        for light in result["lights"]:
            obj = bpy.data.objects[light["name"]]
            if light["light"]["type"] != "sun":
                assert _forward(obj).dot((bpy.data.objects["Hero"].location - obj.location).normalized()) > 0.99
        previous = [light["name"] for light in result["lights"]]
    assert old_world.use_fake_user  # the user's world survives saving
    lights = [o.name for o in bpy.data.objects if o.type == "LIGHT"]
    assert sorted(lights) == sorted(previous + ["OldLamp"])

    hdr = _image_file(tmp_path / "sky.hdr", (0.5, 0.7, 1.0, 1.0), "HDR", float_buffer=True)
    hdri_world = run("set_world_hdri", filepath=hdr)["world"]
    kept = run("setup_lighting", preset="three_point", strength=2)
    assert kept["world_replaced"] is False and bpy.context.scene.world.name == hdri_world
    assert run("setup_lighting", preset="studio", world=True)["world_replaced"] is True
    assert run_error("setup_lighting", preset="disco")[0] == "invalid_params"


def check_setup_lighting_renders_lit_images():
    run("create_object", kind="uv_sphere", name="Ball", location=[0, 0, 1])
    run("create_object", kind="plane", name="Floor", size=8)
    run("set_camera", view="iso", fit=["Ball"])
    for preset in ("three_point", "outdoor"):
        run("setup_lighting", preset=preset, target=["Ball"])
        image = run("render_image", view="camera", engine="cycles", width=48, samples=2)
        assert base64.b64decode(image["image_base64"]).startswith(b"\x89PNG")


def check_set_camera_frames_aims_and_focuses():
    run("create_object", kind="cube", name="Crate", location=[2, 1, 0.5], size=1)
    run("create_object", kind="uv_sphere", name="Orb", location=[-1, 0, 2], size=0.5)
    framed = run("set_camera", view="front", fit=["Crate", "Orb"], lens=35, resolution=[640, 360])
    camera = bpy.context.scene.camera
    assert framed["created"] is True and framed["camera"]["is_scene_camera"] and camera.name == framed["name"]
    assert framed["camera"]["lens"] == 35 and framed["camera"]["resolution"] == [640, 360]
    assert camera.location.y < -1 and _in_view(camera.name, ["Crate", "Orb"])

    for view in ("top", "iso", "left"):
        run("set_camera", view=view)
        assert _in_view(camera.name, ["Crate", "Orb"]), view

    aimed = run("set_camera", location=[0, -10, 3], target="Orb", focus="Orb", fstop=1.8)
    assert aimed["created"] is False
    assert _forward(camera).dot((bpy.data.objects["Orb"].location - camera.location).normalized()) > 0.999
    assert aimed["camera"]["depth_of_field"] == {"enabled": True, "focus_object": "Orb",
                                                  "focus_distance": aimed["camera"]["depth_of_field"]["focus_distance"],
                                                  "fstop": 1.8}
    distance = run("set_camera", focus=4.5)["camera"]["depth_of_field"]
    assert distance["focus_object"] is None and distance["focus_distance"] == 4.5
    assert run("set_camera", depth_of_field=False)["camera"]["depth_of_field"]["enabled"] is False
    tracked = run("set_camera", name="Side", location=[8, 0, 2], target="Crate", track=True, make_active=False)
    assert tracked["camera"]["tracking"] == "Crate" and bpy.context.scene.camera == camera

    assert run_error("set_camera", view="front", location=[0, 0, 0])[0] == "invalid_params"
    assert run_error("set_camera", name="Crate")[0] == "invalid_params"
    assert run_error("set_camera", view="sideways")[0] == "invalid_params"
    assert run_error("set_camera", track=True)[0] == "invalid_params"


def check_keyframes_keep_full_turns_and_set_interpolation():
    run("create_object", kind="cube", name="Spinner")
    result = run("insert_keyframes", object_name="Spinner", interpolation="linear", keyframes=[
        {"frame": 1, "location": [0, 0, 0], "rotation_degrees": [0, 0, 0]},
        {"frame": 48, "location": [4, 0, 0], "rotation_degrees": [0, 0, 360], "scale": [2, 2, 2]},
    ])
    assert result["channels"] == ["location", "rotation_euler", "scale"]
    assert result["animation_range"] == [1.0, 48.0] and result["interpolation"] == "LINEAR"
    scene = bpy.context.scene
    assert scene.frame_end >= 48
    obj = bpy.data.objects["Spinner"]
    scene.frame_set(48)
    assert abs(obj.rotation_euler.z - 6.283185) < 1e-4  # a full turn, not collapsed to 0
    scene.frame_set(24)
    assert abs(obj.location.x - 4 * 23 / 47.0) < 1e-3  # linear motion
    curves = addon._fcurves(obj)
    assert curves and all(p.interpolation == "LINEAR" for c in curves for p in c.keyframe_points)
    info = run("get_object_info", name="Spinner")
    assert info["animated"] is True and info["animation_range"] == [1.0, 48.0]

    run("set_light", name="Lamp", kind="point")
    fade = run("insert_keyframes", object_name="Lamp", interpolation="constant", keyframes=[
        {"frame": 1, "data": {"energy": 0}, "visible": True},
        {"frame": 10, "data": {"energy": 900, "color": "#ff0000"}, "visible": False},
    ])
    assert fade["channels"] == ["color", "energy", "hide_render", "hide_viewport"]
    scene.frame_set(10)
    lamp = bpy.data.objects["Lamp"]
    assert lamp.data.energy == 900 and lamp.hide_render is True
    scene.frame_set(5)
    assert lamp.data.energy == 0  # constant interpolation holds the value

    assert run_error("insert_keyframes", object_name="Spinner", keyframes=[{"frame": 3}])[0] == "invalid_params"
    assert run_error("insert_keyframes", object_name="Spinner", keyframes=[{"location": [0, 0, 0]}])[0] == "invalid_params"
    assert run_error("insert_keyframes", object_name="Lamp",
                     keyframes=[{"frame": 1, "data": {"no_such": 1}}])[0] == "invalid_params"
    assert run_error("insert_keyframes", object_name="Spinner", interpolation="wobbly",
                     keyframes=[{"frame": 1, "location": [0, 0, 0]}])[0] == "invalid_params"

    cleared = run("clear_animation", object_names=["Spinner", "Lamp"])
    assert cleared == {"cleared": ["Spinner", "Lamp"], "without_animation": []}
    assert addon._fcurves(obj) == [] and addon._fcurves(lamp.data) == []


def check_timeline_and_turntables():
    timeline = run("set_timeline", frame_start=10, frame_end=57, fps=30, frame_current=12)
    assert timeline == {"frame_start": 10, "frame_end": 57, "frame_current": 12, "fps": 30.0,
                        "frames": 48, "seconds": 1.6}
    assert run_error("set_timeline", frame_start=50, frame_end=20)[0] == "invalid_params"

    run("create_object", kind="monkey", name="Statue", location=[1, 2, 1])
    table = run("create_turntable", target=["Statue"], frames=60, frame_start=1, elevation_degrees=30)
    scene = bpy.context.scene
    camera, pivot = bpy.data.objects[table["camera"]], bpy.data.objects[table["spinning"]]
    assert scene.camera == camera and camera.parent == pivot
    assert (table["frame_start"], table["frame_end"], table["frames"]) == (1, 60, 60)
    for frame in (1, 15, 30, 45, 60):
        scene.frame_set(frame)
        assert _in_view(camera.name, ["Statue"]), frame
    scene.frame_set(61)
    assert abs(pivot.rotation_euler.z - 6.283185) < 1e-4
    scene.frame_set(31)
    assert abs(pivot.rotation_euler.z - 3.141593) < 1e-4  # constant speed
    again = run("create_turntable", target=["Statue"], frames=24)
    assert sorted(again["removed"]) == sorted([table["camera"], table["spinning"]])
    assert len([o for o in bpy.data.objects if o.get(addon.RIG_PROPERTY) == "turntable"]) == 2

    spin = run("create_turntable", target=["Statue"], mode="object", frames=40, turns=-2)
    statue = bpy.data.objects["Statue"]
    scene.frame_set(41)
    assert spin["spinning"] == "Statue" and abs(statue.rotation_euler.z + 4 * 3.141593) < 1e-4
    assert run_error("create_turntable", mode="object")[0] == "invalid_params"
    assert run_error("create_turntable", turns=0)[0] == "invalid_params"


def _gif_frames(data):
    assert data[:6] == b"GIF89a"
    return data.count(b"\x21\xf9\x04")


def check_render_animation_formats_and_restores_settings(tmp_path):
    run("create_object", kind="cube", name="Box")
    run("insert_keyframes", object_name="Box", keyframes=[
        {"frame": 1, "location": [-1, 0, 0]}, {"frame": 4, "location": [1, 0, 0]}])
    run("set_timeline", frame_start=1, frame_end=4, fps=12)
    scene = bpy.context.scene
    render = scene.render
    settings = render.image_settings
    if hasattr(settings, "media_type"):
        settings.media_type = "VIDEO"  # Blender 5.0+ refused PNG previews in such scenes
    settings.file_format = "FFMPEG"
    before = (render.engine, render.resolution_x, render.resolution_y, render.fps, settings.file_format,
              scene.frame_start, scene.frame_end, scene.frame_current, render.ffmpeg.format)

    gif = run("render_animation", format="gif", filepath=str(tmp_path / "loop"), width=40, samples=1,
              preview_frames=3)
    assert gif["filepath"] == str(tmp_path / "loop.gif") and gif["frames"] == 4 and gif["fps"] == 12
    assert _gif_frames((tmp_path / "loop.gif").read_bytes()) == 4
    assert gif["preview_frames"] == [1, 2, 4] or gif["preview_frames"] == [1, 3, 4]
    assert base64.b64decode(gif["image_base64"]).startswith(b"\x89PNG")

    frames = run("render_animation", format="png", filepath=str(tmp_path / "frames"), width=32, samples=1,
                 frame_step=2, preview_frames=0)
    assert sorted(os.listdir(tmp_path / "frames")) == ["frame_0001.png", "frame_0003.png"]
    assert frames["frames"] == 2 and "image_base64" not in frames and frames["seconds"] == 0.333

    video = run("render_animation", format="mp4", filepath=str(tmp_path / "clip.mp4"), width=33, height=25,
                samples=1, preview_frames=2)
    data = (tmp_path / "clip.mp4").read_bytes()
    assert video["size"] == [32, 24] and data[4:8] == b"ftyp" and video["bytes"] == len(data)
    assert video["preview_frames"] == [1, 4]

    still = run("render_image", view="iso", width=32, samples=1)
    assert base64.b64decode(still["image_base64"]).startswith(b"\x89PNG")
    after = (render.engine, render.resolution_x, render.resolution_y, render.fps, settings.file_format,
             scene.frame_start, scene.frame_end, scene.frame_current, render.ffmpeg.format)
    assert after == before
    assert not [o.name for o in bpy.data.objects if o.name.startswith("MCP_")]
    assert run_error("render_animation", format="webm")[0] == "invalid_params"
    assert run_error("render_animation", format="gif", frame_start=1, frame_end=400)[0] == "invalid_params"


def check_lights_camera_and_animation_through_mcp(tmp_path):
    """The MCP tools (pydantic arguments, keyframe models, image results) against real Blender."""
    pytest.importorskip("mcp")
    from blenderMcp.config import Settings
    from blenderMcp.server import build_server

    addon.start_server(host="127.0.0.1", port=0, token="", allow_code=True, use_timer=False)
    connection = BlenderConnection(port=addon._state["port"], timeout=60)
    server = build_server(Settings(cache_dir=str(tmp_path / "cache")), connection)
    outcome = _serve_while(lambda: run_tools(server, [
        ("create_object", {"kind": "monkey", "name": "Hero", "location": [0, 0, 1]}),
        ("set_camera", {"view": "front", "fit": ["Hero"], "resolution": [320, 240], "focus": "Hero"}),
        ("setup_lighting", {"preset": "dramatic"}),
        ("set_light", {"name": "Fill", "kind": "area", "location": [-3, -3, 2], "target": [0, 0, 1],
                       "temperature_kelvin": 6500}),
        ("insert_keyframes", {"object_name": "Hero", "interpolation": "linear", "keyframes": [
            {"frame": 1, "rotation_degrees": [0, 0, 0]}, {"frame": 6, "rotation_degrees": [0, 0, 180]}]}),
        ("set_timeline", {"frame_start": 1, "frame_end": 6, "fps": 6}),
        ("render_animation", {"format": "gif", "filepath": str(tmp_path / "hero.gif"), "width": 48,
                              "samples": 1, "preview_frames": 2}),
    ]), timeout=180)
    assert "error" not in outcome, outcome.get("error")
    _, results = outcome["value"]
    for result in results:
        assert not is_error(result), text_of(result)
    assert [block.type for block in results[-1].content] == ["image", "text"]
    assert (tmp_path / "hero.gif").read_bytes().count(b"\x21\xf9\x04") == 6
    hero = bpy.data.objects["Hero"]
    bpy.context.scene.frame_set(6)
    assert abs(hero.rotation_euler.z - 3.141593) < 1e-4
    assert bpy.context.scene.camera.data.dof.focus_object == hero
