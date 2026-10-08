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
