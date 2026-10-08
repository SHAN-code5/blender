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
