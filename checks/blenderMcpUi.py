"""End-to-end check of the Blender MCP Bridge add-on in Blender's UI (not background mode).

Run under a display, for example on Linux CI:

    xvfb-run -a python checks/blenderMcpUi.py --blender blender

It installs the add-on into isolated user folders the way a user would (extension
zip on Blender 4.2+, legacy zip before), starts the bridge from its operator,
opens the sidebar panel, and drives the bridge from outside: viewport screenshot,
undo/redo, Workbench render, multi-view render, import/export in UI context, and
panel drawing. A second launch checks "Start automatically". When the mcp
package is installed, it also runs tools through the real MCP server over stdio.
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blenderMcp.addonTools import build_zips  # noqa: E402
from blenderMcp.connection import BlenderConnection, BlenderError  # noqa: E402

INNER = textwrap.dedent('''
    import sys, bpy, addon_utils
    mode, extension_zip, legacy_zip, port = sys.argv[sys.argv.index("--") + 1:]
    key = "blender_mcp_bridge"
    if mode == "install":
        if bpy.app.version >= (4, 2, 0):
            bpy.ops.extensions.package_install_files(filepath=extension_zip, repo="user_default",
                                                     enable_on_install=True)
            key = "bl_ext.user_default.blender_mcp_bridge"
        else:
            bpy.ops.preferences.addon_install(filepath=legacy_zip)
            addon_utils.enable(key, default_set=True)
    else:
        key = next(k for k in bpy.context.preferences.addons.keys() if k.endswith("blender_mcp_bridge"))
    prefs = bpy.context.preferences.addons[key].preferences

    def show_panel():
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.spaces.active.show_region_ui = True
                    for region in area.regions:
                        if region.type == "UI" and hasattr(region, "active_panel_category"):
                            region.active_panel_category = "MCP"
                    area.tag_redraw()

    def start():
        show_panel()
        if mode == "install":
            prefs.port = int(port)
            prefs.auto_start = True
            bpy.ops.wm.save_userpref()
            bpy.ops.blendermcp.start()
        print("MCP_UI_READY", key, flush=True)
        return None

    bpy.app.timers.register(start, first_interval=1.0)
''')


class Blender:
    def __init__(self, executable, user_root, mode, zips, port):
        Path(user_root).mkdir(parents=True, exist_ok=True)
        script = Path(user_root) / "inner.py"
        script.write_text(INNER)
        env = dict(os.environ,
                   BLENDER_USER_CONFIG=str(Path(user_root) / "config"),
                   BLENDER_USER_SCRIPTS=str(Path(user_root) / "scripts"),
                   BLENDER_USER_EXTENSIONS=str(Path(user_root) / "extensions"))
        self.lines = []
        self.process = subprocess.Popen(
            # The user folders above start empty, so no --factory-startup is needed, and the
            # second launch must load the preferences the first one saved.
            [executable, "--python", str(script), "--",
             mode, str(zips["extension"]), str(zips["legacy"]), str(port)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.process.stdout:
            self.lines.append(line.rstrip())

    def wait_ready(self, timeout=90):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if any(line.startswith("MCP_UI_READY") for line in self.lines):
                return
            if self.process.poll() is not None:
                break
            time.sleep(0.2)
        raise RuntimeError("Blender did not become ready:\n" + "\n".join(self.lines[-40:]))

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(15)
        except subprocess.TimeoutExpired:
            self.process.kill()


def png_size(data):
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def png_lit_fraction(data, threshold=24):
    """Share of pixels brighter than threshold in an 8-bit RGB(A) PNG (stdlib-only decoder)."""
    import struct
    import zlib

    width, height = png_size(data)
    bit_depth, color_type = data[24], data[25]
    if bit_depth != 8 or color_type not in (2, 6):
        raise ValueError("expected an 8-bit RGB or RGBA PNG")
    channels = 4 if color_type == 6 else 3
    raw, offset = b"", 8
    while offset < len(data):
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        if kind == b"IDAT":
            raw += data[offset + 8:offset + 8 + length]
        offset += 12 + length
    pixels = zlib.decompress(raw)
    stride = width * channels
    previous = bytearray(stride)
    lit = 0
    for row in range(height):
        start = row * (stride + 1)
        kind, line = pixels[start], bytearray(pixels[start + 1:start + 1 + stride])
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up, up_left = previous[i], (previous[i - channels] if i >= channels else 0)
            if kind == 1:
                line[i] = (line[i] + left) & 0xFF
            elif kind == 2:
                line[i] = (line[i] + up) & 0xFF
            elif kind == 3:
                line[i] = (line[i] + (left + up) // 2) & 0xFF
            elif kind == 4:
                estimate = left + up - up_left
                pa, pb, pc = abs(estimate - left), abs(estimate - up), abs(estimate - up_left)
                line[i] = (line[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else up_left)) & 0xFF
        lit += sum(1 for i in range(0, stride, channels) if max(line[i:i + 3]) > threshold)
        previous = line
    return lit / float(width * height)


def wait_for_port(connection, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            return connection.send_command("ping", timeout=5)
        except BlenderError:
            time.sleep(0.5)
    raise RuntimeError("the bridge did not start listening")


def bridge_checks(connection, work, artifacts=None):
    results = []

    def keep(name, encoded):
        if artifacts:
            Path(artifacts).mkdir(parents=True, exist_ok=True)
            (Path(artifacts) / name).write_bytes(base64.b64decode(encoded))

    def check(name, condition, detail=""):
        results.append((name, bool(condition), detail))

    status = connection.send_command("get_bridge_status")
    check("UI mode", status["background"] is False, status["blender_version"])

    connection.send_command("create_object", {"kind": "cube", "name": "UiCube", "location": [0, 0, 1]})
    shot = connection.send_command("viewport_screenshot", {"max_size": 640})
    data = base64.b64decode(shot["image_base64"])
    keep("viewport.png", shot["image_base64"])
    width, height = png_size(data)
    lit = png_lit_fraction(data)
    check("viewport screenshot shows the scene", data.startswith(b"\x89PNG") and max(width, height) <= 640
          and lit > 0.5, f"{width}x{height}, {lit:.0%} lit, {shot['method']}")

    undone = connection.send_command("undo", {"steps": 1})
    names = [o["name"] for o in connection.send_command("list_objects")]
    check("undo removes the new cube", undone["steps"] == 1 and "UiCube" not in names, str(names))
    connection.send_command("redo", {"steps": 1})
    names = [o["name"] for o in connection.send_command("list_objects")]
    check("redo brings it back", "UiCube" in names, str(names))

    render = connection.send_command("render_image", {"width": 160, "view": "iso"}, timeout=120)
    keep("render_iso.png", render["image_base64"])
    render_png = base64.b64decode(render["image_base64"])
    check("auto engine render (Workbench)", render["engine"] == "BLENDER_WORKBENCH"
          and render_png.startswith(b"\x89PNG") and png_lit_fraction(render_png) > 0.5, render["engine"])
    views = connection.send_command("render_views", {"views": ["front", "top"], "width": 96}, timeout=120)
    keep("render_views.png", views["image_base64"])
    check("multi-view render", png_size(base64.b64decode(views["image_base64"])) == (2 * 96 + 4, 72), str(views["layout"]))

    exported = connection.send_command("export_scene", {"filepath": str(work / "ui.glb"), "object_names": ["UiCube"]})
    imported = connection.send_command("import_model", {"filepath": exported["filepath"], "target_size": 0.5,
                                                        "location": [3, 0, 0]})
    check("export and import in UI context", exported["exists"] and imported["imported_objects"],
          str(imported["imported_objects"]))

    panel = connection.send_command("execute_code", {"code": (
        "import bpy\n"
        "result = {'panel': hasattr(bpy.types, 'BLENDERMCP_PT_panel'),"
        " 'sidebar': any(a.spaces.active.show_region_ui for w in C.window_manager.windows"
        " for a in w.screen.areas if a.type == 'VIEW_3D')}")})
    check("panel registered and sidebar open", panel["result"] == {"panel": True, "sidebar": True}, str(panel["result"]))
    return results


def mcp_checks(port):
    """Drive the real MCP server over stdio against the UI Blender, if mcp is installed."""
    try:
        import anyio
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        return [("MCP stdio (skipped: mcp not installed)", True, "")]

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "blenderMcp", "--port", str(port)],
                                       cwd=str(ROOT), env=dict(os.environ, PYTHONPATH=str(ROOT)))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                status = await session.call_tool("get_bridge_status", {})
                shot = await session.call_tool("viewport_screenshot", {"max_size": 320})
                return json.loads(status.content[0].text), [block.type for block in shot.content]

    status, blocks = anyio.run(run)
    return [("MCP stdio status", status.get("connected") and status.get("background") is False, ""),
            ("MCP stdio screenshot", blocks == ["image", "text"], str(blocks))]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--blender", default="blender")
    parser.add_argument("--port", type=int, default=9931)
    parser.add_argument("--artifacts", help="folder to keep the screenshot and renders in")
    args = parser.parse_args()
    if not os.environ.get("DISPLAY") and sys.platform.startswith("linux"):
        sys.exit("No display: run this under xvfb-run.")

    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        zips = build_zips(temp / "dist")
        results = []
        connection = BlenderConnection(port=args.port, timeout=60)

        first = Blender(args.blender, temp / "user", "install", zips, args.port)
        try:
            first.wait_ready()
            wait_for_port(connection)
            results += bridge_checks(connection, temp, args.artifacts)
            results += mcp_checks(args.port)
            time.sleep(1.0)  # let the sidebar redraw a few times
        finally:
            first.stop()
        tracebacks = [line for line in first.lines if "Traceback" in line or "Error:" in line]
        results.append(("no Python errors in Blender's log (panel drawing included)", not tracebacks,
                        "\n".join(tracebacks[:5])))

        second = Blender(args.blender, temp / "user", "reuse", zips, args.port)
        try:
            second.wait_ready()
            pong = wait_for_port(connection)
            results.append(("'Start automatically' starts the bridge on launch", pong.get("pong") is True, ""))
        except RuntimeError as exc:
            results.append(("'Start automatically' starts the bridge on launch", False, str(exc)[-400:]))
        finally:
            second.stop()

    failed = [r for r in results if not r[1]]
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    print(f"{len(results) - len(failed)}/{len(results)} UI checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
