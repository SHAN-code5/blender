"""Tests for the Blender MCP server package that need neither Blender nor a live MCP client."""
from __future__ import annotations

import json
import re
import socket
import socketserver
import threading
import zipfile
from pathlib import Path

import pytest

import blenderMcp
from blenderMcp import addonTools, clients, cli, connection as connection_module, safety
from blenderMcp.config import ConfigError, Settings, load_settings
from blenderMcp.connection import BlenderConnection, BlenderError, BlenderNotRunning

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# Fake Blender bridge
# --------------------------------------------------------------------------

class _FakeBridge(socketserver.StreamRequestHandler):
    """Answers like the add-on: echoes the request, or fails on demand."""

    def handle(self):
        for raw in self.rfile:
            request = json.loads(raw)
            kind = request["type"]
            if kind == "hang":
                continue
            if kind == "fail":
                response = {"id": request["id"], "status": "error", "code": "not_found", "message": "boom"}
            elif kind == "wrong_id":
                response = {"id": "someone-else", "status": "success", "result": None}
            elif kind == "garbage":
                self.wfile.write(b"not json\n")
                self.wfile.flush()
                continue
            else:
                response = {"id": request["id"], "status": "success", "result": {"echo": request}}
            self.wfile.write(json.dumps(response).encode() + b"\n")
            self.wfile.flush()


@pytest.fixture
def fake_bridge():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _FakeBridge)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------

def check_send_command_returns_result_and_sends_protocol_fields(fake_bridge):
    result = BlenderConnection(port=fake_bridge, token="s3cret").send_command("get_scene_info", {"a": 1}, timeout=7)
    request = result["echo"]
    assert request["type"] == "get_scene_info"
    assert request["params"] == {"a": 1}
    assert request["token"] == "s3cret"
    assert request["timeout"] == 7
    assert re.fullmatch(r"[0-9a-f]{32}", request["id"])


def check_token_is_omitted_when_unset(fake_bridge):
    assert "token" not in BlenderConnection(port=fake_bridge).send_command("ping")["echo"]


def check_error_status_raises_with_message_and_code(fake_bridge):
    with pytest.raises(BlenderError, match="boom") as info:
        BlenderConnection(port=fake_bridge).send_command("fail")
    assert info.value.code == "not_found"


def check_mismatched_response_id_is_rejected(fake_bridge):
    with pytest.raises(BlenderError, match="different request"):
        BlenderConnection(port=fake_bridge).send_command("wrong_id")


def check_invalid_json_response_raises(fake_bridge):
    connection = BlenderConnection(port=fake_bridge)
    with pytest.raises(BlenderError, match="connection failed"):
        connection.send_command("garbage")
    assert connection.send_command("ok")["echo"]["type"] == "ok"


def check_unreachable_blender_explains_how_to_start_it():
    with pytest.raises(BlenderNotRunning, match="Start the bridge in Blender") as info:
        BlenderConnection(port=_unused_port()).send_command("ping")
    assert info.value.code == "not_running"


def check_unanswered_command_times_out(fake_bridge, monkeypatch):
    monkeypatch.setattr(connection_module, "RESPONSE_GRACE", 0.2)
    with pytest.raises(BlenderError, match="did not answer") as info:
        BlenderConnection(port=fake_bridge).send_command("hang", timeout=0.1)
    assert info.value.code == "timeout"


def check_concurrent_commands_never_mix_responses(fake_bridge):
    connection = BlenderConnection(port=fake_bridge)
    results, errors = {}, []

    def worker(index):
        try:
            results[index] = connection.send_command("cmd", {"n": index})["echo"]["params"]["n"]
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(32)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert results == {i: i for i in range(32)}


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

class _Flags:
    def __init__(self, **values):
        self.__dict__.update(values)


def check_settings_defaults():
    assert load_settings(None, {}) == Settings()


def check_settings_env_and_aliases():
    settings = load_settings(None, {"BLENDER_HOST": "10.0.0.5", "BLENDER_PORT": "9999",
                                    "BLENDER_MCP_TOKEN": "t", "BLENDER_MCP_SAFE_MODE": "yes",
                                    "BLENDER_MCP_TIMEOUT": "12.5"})
    assert (settings.host, settings.port, settings.token, settings.safe_mode, settings.timeout) == (
        "10.0.0.5", 9999, "t", True, 12.5)
    # The BLENDER_MCP_* name wins over the alias.
    assert load_settings(None, {"BLENDER_PORT": "1111", "BLENDER_MCP_PORT": "2222"}).port == 2222


def check_flags_override_environment():
    flags = _Flags(host="h", port=4321, timeout=None, token=None, safe_mode=False,
                   transport="streamable-http", http_host=None, http_port=9000)
    settings = load_settings(flags, {"BLENDER_MCP_PORT": "1234", "BLENDER_MCP_HOST": "env"})
    assert (settings.host, settings.port, settings.transport, settings.http_port) == ("h", 4321, "streamable-http", 9000)


@pytest.mark.parametrize("environ", [
    {"BLENDER_MCP_PORT": "70000"},
    {"BLENDER_MCP_PORT": "abc"},
    {"BLENDER_MCP_TIMEOUT": "-1"},
    {"BLENDER_MCP_SAFE_MODE": "maybe"},
    {"BLENDER_MCP_TRANSPORT": "carrier-pigeon"},
])
def check_invalid_settings_are_rejected(environ):
    with pytest.raises(ConfigError):
        load_settings(None, environ)


# --------------------------------------------------------------------------
# Safe mode
# --------------------------------------------------------------------------

ALLOWED_SCRIPTS = [
    "import bpy\nbpy.ops.mesh.primitive_cube_add(size=2)\nresult = len(bpy.data.objects)",
    "import math\nfor o in bpy.context.scene.objects:\n    o.rotation_euler.z += math.radians(10)",
    "import bmesh, mathutils\nbm = bmesh.new()\nbm.free()",
    "mat = getattr(bpy.data, 'materials')\nprint(len(mat))",
    "bpy.ops.wm.save_as_mainfile(filepath='/tmp/x.blend')",
    "from bpy.props import FloatProperty",
]

BLOCKED_SCRIPTS = {
    "import os": "os",
    "import subprocess as sp": "subprocess",
    "from shutil import rmtree": "shutil",
    "import urllib.request": "urllib",
    "open('/etc/passwd').read()": "open",
    "eval('1+1')": "eval",
    "__import__('os')": "__import__",
    "().__class__.__bases__[0].__subclasses__()": "__class__",
    "bpy.app.handlers.load_post.append(print)": "bpy.app.handlers",
    "import bpy as b\nb.app.timers.register(print)": "bpy.app.timers",
    "from bpy.app import handlers": "bpy.app.handlers",
    "import bpy.app.handlers": "bpy.app.handlers",
    "a = bpy.app\na.handlers.frame_change_pre.clear()": "bpy.app.handlers",
    "getattr(bpy.app, 'handlers')": "handlers",
    "name = 'handlers'\ngetattr(bpy.app, name)": "literal",
    "bpy.ops.wm.quit_blender()": "quit_blender",
    "bpy.data.texts['x'].as_module()": "as_module",
    "bpy.utils.register_class(Foo)": "register_class",
    "def broken(:\n  pass": "syntax error",
}


@pytest.mark.parametrize("script", ALLOWED_SCRIPTS)
def check_safe_mode_allows_normal_blender_work(script):
    assert safety.check_script(script) == []


@pytest.mark.parametrize("script,needle", list(BLOCKED_SCRIPTS.items()))
def check_safe_mode_blocks_risky_scripts(script, needle):
    violations = safety.check_script(script)
    assert violations, script
    assert any(needle in v.reason for v in violations), [str(v) for v in violations]


def check_safe_mode_error_lists_lines_and_alternatives():
    with pytest.raises(safety.SafeModeError) as info:
        safety.validate_script("x = 1\nimport os")
    assert "line 2" in str(info.value)
    assert "import_model" in str(info.value)


# --------------------------------------------------------------------------
# Client configuration
# --------------------------------------------------------------------------

def check_client_configs_have_the_expected_shape():
    entry = json.loads(clients.config_for("claude-desktop"))["mcpServers"]["blender"]
    assert entry == {"command": "uvx", "args": ["--from", clients.PACKAGE_SOURCE, "blender-mcp-bridge"]}
    assert json.loads(clients.config_for("cursor")) == json.loads(clients.config_for("windsurf"))
    vscode = json.loads(clients.config_for("vscode", env={"BLENDER_MCP_PORT": "9877"}))["servers"]["blender"]
    assert vscode["type"] == "stdio" and vscode["env"] == {"BLENDER_MCP_PORT": "9877"}
    assert json.loads(clients.config_for("http"))["mcpServers"]["blender"] == {"url": "http://127.0.0.1:8000/mcp"}
    python_entry = json.loads(clients.config_for("cursor", launcher="python", python="/py"))["mcpServers"]["blender"]
    assert python_entry == {"command": "/py", "args": ["-m", "blenderMcp"]}


def check_claude_code_command_quotes_env_values(monkeypatch):
    monkeypatch.setattr(clients.sys, "platform", "linux")
    command = clients.config_for("claude-code", env={"BLENDER_MCP_TOKEN": "a b"})
    assert command.startswith("claude mcp add blender -e 'BLENDER_MCP_TOKEN=a b' -- uvx --from ")
    monkeypatch.setattr(clients.sys, "platform", "win32")
    command = clients.config_for("claude-code", env={"BLENDER_MCP_TOKEN": "a b"})
    assert command.startswith('claude mcp add blender -e "BLENDER_MCP_TOKEN=a b" -- uvx --from ')


def check_write_config_merges_and_backs_up(tmp_path):
    path = tmp_path / "claude_desktop_config.json"
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    backup = clients.write_config(path, "claude-desktop", env={"BLENDER_MCP_PORT": "9999"})
    data = json.loads(path.read_text())
    assert data["theme"] == "dark"
    assert data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["blender"]["env"] == {"BLENDER_MCP_PORT": "9999"}
    assert json.loads(backup.read_text())["mcpServers"] == {"other": {"command": "x"}}


def check_write_config_creates_missing_vscode_file(tmp_path):
    path = tmp_path / ".vscode" / "mcp.json"
    assert clients.write_config(path, "vscode") is None
    assert json.loads(path.read_text())["servers"]["blender"]["type"] == "stdio"


def check_default_config_paths_per_platform(tmp_path):
    home = tmp_path
    assert clients.default_config_path("claude-desktop", "darwin", home, {}) == (
        home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
    assert clients.default_config_path("claude-desktop", "win32", home, {"APPDATA": str(home / "AD")}) == (
        home / "AD" / "Claude" / "claude_desktop_config.json")
    assert clients.default_config_path("cursor", "linux", home, {}) == home / ".cursor" / "mcp.json"
    assert clients.default_config_path("claude-code", "linux", home, {}) is None


# --------------------------------------------------------------------------
# Add-on build and install
# --------------------------------------------------------------------------

def check_build_zips_layout(tmp_path):
    zips = addonTools.build_zips(tmp_path)
    with zipfile.ZipFile(zips["extension"]) as archive:
        assert sorted(archive.namelist()) == ["__init__.py", "blender_manifest.toml", "gifwriter.py"]
    with zipfile.ZipFile(zips["legacy"]) as archive:
        assert sorted(archive.namelist()) == ["blender_mcp_bridge/__init__.py", "blender_mcp_bridge/gifwriter.py"]


def check_addon_targets_per_platform(tmp_path):
    linux_root = tmp_path / ".config" / "blender"
    for version in ("4.10", "4.5", "3.6", "notes"):
        (linux_root / version).mkdir(parents=True)
    targets = addonTools.addon_targets(platform="linux", environ={}, home=tmp_path)
    assert [t.parts[-3] for t in targets] == ["3.6", "4.5", "4.10"]
    assert targets[0] == linux_root / "3.6" / "scripts" / "addons"

    mac = addonTools.addon_targets(version="5.0", platform="darwin", environ={}, home=tmp_path)
    assert mac == [tmp_path / "Library" / "Application Support" / "Blender" / "5.0" / "scripts" / "addons"]
    win = addonTools.addon_targets(version="4.2", platform="win32", environ={"APPDATA": str(tmp_path)}, home=tmp_path)
    assert win == [tmp_path / "Blender Foundation" / "Blender" / "4.2" / "scripts" / "addons"]
    custom = addonTools.addon_targets(platform="linux", environ={"BLENDER_USER_SCRIPTS": str(tmp_path / "s")})
    assert custom == [tmp_path / "s" / "addons"]


def check_install_addon_copies_the_package(tmp_path):
    installed = addonTools.install_addon(tmp_path / "addons")
    assert (installed / "__init__.py").read_text() == (addonTools.ADDON_SOURCE / "__init__.py").read_text()
    assert installed.name == "blender_mcp_bridge"


def check_versions_agree_everywhere():
    version = blenderMcp.__version__
    pyproject = (ROOT / "pyproject.toml").read_text()
    manifest = (addonTools.ADDON_SOURCE / "blender_manifest.toml").read_text()
    addon = (addonTools.ADDON_SOURCE / "__init__.py").read_text()
    assert re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1) == version
    assert re.search(r'^version = "([^"]+)"', manifest, re.M).group(1) == version
    assert re.search(r'^BRIDGE_VERSION = "([^"]+)"', addon, re.M).group(1) == version
    bl_info = re.search(r'"version": \((\d+), (\d+), (\d+)\)', addon).groups()
    assert ".".join(bl_info) == version
    assert re.search(r"^PROTOCOL_VERSION = (\d+)", addon, re.M).group(1) == str(blenderMcp.PROTOCOL_VERSION)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def check_cli_defaults_to_serve():
    assert cli._normalize([]) == ["serve"]
    assert cli._normalize(["--port", "1"]) == ["serve", "--port", "1"]
    assert cli._normalize(["config", "cursor"]) == ["config", "cursor"]


def check_cli_config_prints_json(capsys):
    assert cli.main(["config", "cursor", "--port", "9877"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mcpServers"]["blender"]["env"] == {"BLENDER_MCP_PORT": "9877"}


def check_cli_config_write(tmp_path, capsys):
    path = tmp_path / "mcp.json"
    assert cli.main(["config", "cursor", "--write", "--path", str(path)]) == 0
    assert "blender" in json.loads(path.read_text())["mcpServers"]


def check_cli_rejects_bad_settings(capsys):
    assert cli.main(["serve", "--port", "70000"]) == 2
    assert "between 1 and 65535" in capsys.readouterr().err


def check_cli_doctor_reports_missing_blender(capsys):
    assert cli.main(["doctor", "--port", str(_unused_port())]) == 1
    assert "NOT CONNECTED" in capsys.readouterr().out


def _hide_module(monkeypatch, name):
    """Make ``from blenderMcp import <name>`` fail as it does when the mcp SDK is missing."""
    import sys

    monkeypatch.delattr(blenderMcp, name, raising=False)
    monkeypatch.setitem(sys.modules, f"blenderMcp.{name}", None)


def check_cli_serve_explains_a_missing_sdk(monkeypatch, capsys):
    _hide_module(monkeypatch, "server")
    assert cli.main(["serve"]) == 1
    assert 'pip install "mcp>=1.10,<3"' in capsys.readouterr().err


def check_cli_doctor_works_without_the_sdk(monkeypatch, capsys):
    _hide_module(monkeypatch, "compat")
    assert cli.main(["doctor", "--port", str(_unused_port())]) == 1
    out = capsys.readouterr().out
    assert "MCP SDK: NOT INSTALLED" in out and "NOT CONNECTED" in out


def check_cli_build_and_install(tmp_path, capsys):
    assert cli.main(["build-addon", "--out", str(tmp_path / "dist")]) == 0
    assert cli.main(["install-addon", "--dest", str(tmp_path / "addons")]) == 0
    assert (tmp_path / "addons" / "blender_mcp_bridge" / "__init__.py").exists()


# --------------------------------------------------------------------------
# MCP server (needs the mcp SDK, v1 or v2)
# --------------------------------------------------------------------------

from bridgeHelpers import RecordingConnection, is_error as _is_error, run_tools as _run_tools  # noqa: E402

ALL_TOOLS = {
    "get_bridge_status", "get_scene_info", "list_objects", "get_object_info", "create_object",
    "modify_object", "delete_objects", "set_material", "add_modifier", "undo", "import_model",
    "export_scene", "save_blend_file", "render_image", "render_views", "viewport_screenshot",
    "execute_blender_code", "search_assets", "import_asset", "generate_3d", "get_generation_status",
    "get_guide", "set_light", "setup_lighting", "set_camera", "insert_keyframes", "clear_animation",
    "set_timeline", "create_turntable", "render_animation",
}


def _server(connection, **settings):
    from blenderMcp.server import build_server

    return build_server(Settings(**settings), connection)


def check_server_exposes_annotated_tools():
    pytest.importorskip("mcp")
    tools, _ = _run_tools(_server(RecordingConnection()), [])
    assert {tool.name for tool in tools} == ALL_TOOLS
    by_name = {tool.name: tool for tool in tools}
    annotations = by_name["delete_objects"].annotations
    assert getattr(annotations, "destructiveHint", None) or getattr(annotations, "destructive_hint", None)
    read_only = by_name["get_scene_info"].annotations
    assert getattr(read_only, "readOnlyHint", None) or getattr(read_only, "read_only_hint", None)
    schema = getattr(by_name["create_object"], "input_schema", None) or by_name["create_object"].inputSchema
    assert "torus" in json.dumps(schema)
    asset_schema = getattr(by_name["import_asset"], "input_schema", None) or by_name["import_asset"].inputSchema
    assert "ctx" not in asset_schema["properties"]


def check_server_forwards_tools_without_none_params():
    pytest.importorskip("mcp")
    connection = RecordingConnection()
    _, results = _run_tools(_server(connection), [
        ("create_object", {"kind": "cube", "location": [1, 2, 3]}),
        ("modify_object", {"name": "Cube", "parent": ""}),
        ("execute_blender_code", {"code": "result = 1", "timeout_seconds": 5}),
        ("save_blend_file", {"filepath": "/tmp/a.blend", "save_copy": True}),
        ("undo", {"steps": 2}),
        ("undo", {"redo": True}),
    ])
    assert not any(_is_error(r) for r in results)
    assert connection.calls == [
        ("create_object", {"kind": "cube", "location": [1.0, 2.0, 3.0]}, None),
        ("modify_object", {"name": "Cube", "parent": ""}, None),
        ("execute_code", {"code": "result = 1"}, 5.0),
        ("save_blend_file", {"filepath": "/tmp/a.blend", "copy": True}, 300),
        ("undo", {"steps": 2}, None),
        ("redo", {"steps": 1}, None),
    ]


def check_server_reports_blender_errors_readably():
    pytest.importorskip("mcp")
    connection = RecordingConnection(error=BlenderError("object not found: Ghost", code="not_found"))
    _, (result,) = _run_tools(_server(connection), [("get_object_info", {"name": "Ghost"})])
    assert _is_error(result)
    assert "object not found: Ghost" in result.content[0].text


def check_server_status_works_without_blender():
    pytest.importorskip("mcp")
    connection = RecordingConnection(error=BlenderNotRunning("not running", code="not_running"))
    _, (result,) = _run_tools(_server(connection, port=9999), [("get_bridge_status", {})])
    status = json.loads(result.content[0].text)
    assert status["connected"] is False and status["blender_address"] == "127.0.0.1:9999"
    assert status["asset_sources"]["polyhaven"] == "ready"
    assert "mock" in status["generators"]


def check_safe_mode_blocks_before_reaching_blender():
    pytest.importorskip("mcp")
    connection = RecordingConnection()
    _, (blocked, allowed) = _run_tools(_server(connection, safe_mode=True), [
        ("execute_blender_code", {"code": "import os\nos.remove('x')"}),
        ("execute_blender_code", {"code": "result = len(bpy.data.objects)"}),
    ])
    assert _is_error(blocked) and "Safe mode blocked" in blocked.content[0].text
    assert not _is_error(allowed)
    assert [c[0] for c in connection.calls] == ["execute_code"]


def check_render_tools_return_image_blocks():
    pytest.importorskip("mcp")
    import base64

    png = b"\x89PNG\r\n\x1a\nfake"
    encoded = base64.b64encode(png).decode()
    connection = RecordingConnection(results={
        "render_image": lambda p: {"format": "png", "width": 16, "height": 16, "engine": "CYCLES",
                                   "view": "iso", "filepath": None, "image_base64": encoded},
        "render_views": lambda p: {"format": "png", "views": ["front", "top"], "layout": [["front", "top"]],
                                   "tile_size": [8, 6], "engine": "CYCLES", "image_base64": encoded},
    })
    _, (single, grid) = _run_tools(_server(connection), [
        ("render_image", {"view": "iso"}),
        ("render_views", {"views": ["front", "top"], "width": 16}),
    ])
    for result in (single, grid):
        assert [block.type for block in result.content] == ["image", "text"]
        assert base64.b64decode(result.content[0].data) == png
    assert "layout" in grid.content[1].text
    assert connection.calls[1] == ("render_views", {"views": ["front", "top"], "engine": "auto", "width": 16}, 300)


def check_server_forwards_light_camera_and_animation_tools():
    pytest.importorskip("mcp")
    import base64

    connection = RecordingConnection(results={
        "render_animation": lambda p: {"format": "gif", "filepath": "/tmp/a.gif", "frames": 4,
                                       "image_base64": base64.b64encode(b"\x89PNG\r\n").decode()},
    })
    _, results = _run_tools(_server(connection), [
        ("set_light", {"kind": "area", "target": "Hero", "temperature_kelvin": 3200}),
        ("set_light", {"name": "Key", "target": [0, 0, 1]}),
        ("setup_lighting", {"preset": "studio"}),
        ("set_camera", {"view": "iso", "fit": ["Hero"], "resolution": [1920, 1080]}),
        ("insert_keyframes", {"object_name": "Hero", "keyframes": [
            {"frame": 1, "location": [0, 0, 0]}, {"frame": 24, "rotation_degrees": [0, 0, 360], "data": {"lens": 35}}]}),
        ("clear_animation", {"object_names": ["Hero"]}),
        ("set_timeline", {"frame_end": 48, "fps": 30}),
        ("create_turntable", {"frames": 96}),
        ("render_animation", {"format": "gif", "width": 64}),
    ])
    assert not any(_is_error(r) for r in results), [r.content[0].text for r in results if _is_error(r)]
    calls = {(name, json.dumps(params, sort_keys=True)): timeout for name, params, timeout in connection.calls}
    assert ("set_light", json.dumps({"kind": "area", "target": "Hero", "temperature_kelvin": 3200.0},
                                    sort_keys=True)) in calls
    assert connection.calls[1][1]["target"] == [0.0, 0.0, 1.0]
    assert connection.calls[2][1] == {"preset": "studio", "strength": 1.0, "azimuth_degrees": 0.0, "replace": True}
    assert connection.calls[3][1]["resolution"] == [1920, 1080] and connection.calls[3][1]["make_active"] is True
    assert connection.calls[4][1]["keyframes"] == [
        {"frame": 1.0, "location": [0.0, 0.0, 0.0]},
        {"frame": 24.0, "rotation_degrees": [0.0, 0.0, 360.0], "data": {"lens": 35}}]
    assert connection.calls[4][1]["interpolation"] == "bezier"
    assert connection.calls[6][1] == {"frame_end": 48, "fps": 30}
    assert connection.calls[7][1]["frames"] == 96 and connection.calls[7][1]["mode"] == "camera"
    assert connection.calls[8] == ("render_animation", {"format": "gif", "frame_step": 1, "width": 64, "engine": "auto",
                                                        "preview_frames": 6}, 1800)
    assert [block.type for block in results[8].content] == ["image", "text"]
    assert "/tmp/a.gif" in results[8].content[1].text


def check_render_animation_without_preview_returns_text():
    pytest.importorskip("mcp")
    connection = RecordingConnection(results={"render_animation": lambda p: {"format": "mp4", "frames": 2}})
    _, (result,) = _run_tools(_server(connection), [("render_animation", {"preview_frames": 0})])
    assert [block.type for block in result.content] == ["text"] and '"mp4"' in result.content[0].text


# --------------------------------------------------------------------------
# GIF writer (pure Python, runs inside Blender)
# --------------------------------------------------------------------------

def _gifwriter():
    import importlib.util

    spec = importlib.util.spec_from_file_location("gifwriter", ROOT / "blenderMcp" / "addon" / "gifwriter.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lzw_decode(data, min_code_size=8):
    """Reference GIF LZW decoder (follows the spec, independent of the encoder's code)."""
    clear, end = 1 << min_code_size, (1 << min_code_size) + 1
    position = 0
    size = min_code_size + 1
    table = [bytes([i]) for i in range(clear)] + [b"", b""]
    out, previous = bytearray(), None
    bits = int.from_bytes(data, "little")
    while position + size <= len(data) * 8:
        code = (bits >> position) & ((1 << size) - 1)
        position += size
        if code == clear:
            size, table, previous = min_code_size + 1, table[:end + 1], None
            continue
        if code == end:
            break
        if code < len(table):
            entry = table[code]
            if previous is not None:
                table.append(previous + entry[:1])
        else:
            entry = previous + previous[:1]
            table.append(entry)
        out += entry
        previous = entry
        if len(table) == (1 << size) and size < 12:
            size += 1
    return bytes(out)


def _gif_images(data):
    """Split a GIF into (width, height, delay, [frame index bytes]) using the block structure."""
    assert data[:6] == b"GIF89a" and data[-1:] == b"\x3b"
    width, height = int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    position, frames, delay = 13 + 768, [], None
    while data[position] != 0x3B:
        if data[position] == 0x21:  # extension: skip its sub-blocks
            if data[position + 1] == 0xF9:
                delay = int.from_bytes(data[position + 4:position + 6], "little")
            position += 2
            while data[position]:
                position += data[position] + 1
            position += 1
            continue
        assert data[position] == 0x2C
        position += 10
        min_code_size = data[position]
        position += 1
        payload = bytearray()
        while data[position]:
            payload += data[position + 1:position + 1 + data[position]]
            position += data[position] + 1
        position += 1
        frames.append(_lzw_decode(bytes(payload), min_code_size))
    return width, height, delay, frames


def check_gif_writer_round_trips_through_a_reference_decoder():
    import io
    import random

    gif = _gifwriter()
    rng = random.Random(7)
    width, height = 173, 97  # odd sizes, and enough noise to fill and reset the code table
    frames = [bytes(rng.randrange(216) for _ in range(width * height)),
              bytes((x // 3 + y) % 216 for y in range(height) for x in range(width)),
              bytes([5]) * (width * height)]
    buffer = io.BytesIO()
    writer = gif.GifWriter(buffer, width, height, 100.0 / 12)
    for frame in frames:
        writer.add_frame(frame)
    writer.close()
    decoded_width, decoded_height, delay, decoded = _gif_images(buffer.getvalue())
    assert (decoded_width, decoded_height, delay) == (width, height, 8)
    assert decoded == frames
    with pytest.raises(ValueError):
        writer.add_frame(b"\x00")


def check_gif_quantize_keeps_grays_neutral_and_reads_bmp():
    import struct

    gif = _gifwriter()
    palette = gif.palette()
    width, height = 6, 3
    pixels = [(200, 120, 40), (128, 128, 128), (255, 255, 255), (0, 0, 0), (90, 90, 90), (10, 200, 250)]

    def bmp(bits):
        channels = bits // 8
        stride = (width * channels + 3) & ~3
        rows = b"".join(
            b"".join(bytes((b, g, r) + ((255,) if channels == 4 else ())) for r, g, b in pixels).ljust(stride, b"\0")
            for _ in range(height))
        header = struct.pack("<2sIHHI", b"BM", 54 + len(rows), 0, 0, 54)
        info = struct.pack("<IiiHHIIiiII", 40, width, height, 1, bits, 0, len(rows), 2835, 2835, 0, 0)
        return header + info + rows

    for bits in (24, 32):
        read_width, read_height, rows = gif.read_bmp(bmp(bits))
        assert (read_width, read_height) == (width, height)
        indices = gif.quantize(rows)
        colors = [tuple(palette[3 * i:3 * i + 3]) for i in indices]
        for x in (1, 2, 3, 4):  # grays stay gray whatever the dither threshold
            assert all(len(set(colors[y * width + x])) == 1 for y in range(height))
        assert colors[2] == (255, 255, 255) and colors[3] == (0, 0, 0)
        red, green, blue = colors[0]
        assert red > green > blue
    with pytest.raises(ValueError):
        gif.read_bmp(b"PNG not a bitmap")
