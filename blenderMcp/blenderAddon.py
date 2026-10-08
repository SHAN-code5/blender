"""Blender MCP Bridge: a localhost socket server that runs commands inside Blender.

Install by zipping this file (or Install from Disk on the single .py file) and
enabling "Blender MCP Bridge". Then open the 3D View sidebar (N) > MCP and press
Start. The MCP server (python -m blenderMcp.server) connects to 127.0.0.1:9876.

Socket handler threads never touch bpy. They queue requests, and a
bpy.app.timers callback runs them on Blender's main thread.
"""

import contextlib
import io
import json
import queue
import socketserver
import threading
import traceback

import bpy

bl_info = {
    "name": "Blender MCP Bridge",
    "author": "AI 3D Object Generator project",
    "version": (0, 1, 0),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > MCP",
    "description": "Expose the open Blender session to the MCP server over localhost.",
    "category": "Interface",
}

HOST = "127.0.0.1"
PORT = 9876
REQUEST_WAIT_SECONDS = 25.0
TIMER_INTERVAL_SECONDS = 0.05

_pending = queue.Queue()
_server = None
_server_thread = None


# --- command implementations (main thread only) -----------------------------

def _get_scene_info(params):
    scene = bpy.context.scene
    return {
        "name": scene.name,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "frame_current": scene.frame_current,
        "object_count": len(scene.objects),
    }


def _list_objects(params):
    object_type = params.get("object_type")
    if object_type is not None:
        object_type = str(object_type).upper()
    objects = [
        {"name": obj.name, "type": obj.type, "location": list(obj.location)}
        for obj in bpy.context.scene.objects
        if object_type is None or obj.type == object_type
    ]
    return objects


def _get_object_info(params):
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("name must be a non-empty string")
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"object not found: {name}")
    return {
        "name": obj.name,
        "type": obj.type,
        "location": list(obj.location),
        "rotation_euler": list(obj.rotation_euler),
        "scale": list(obj.scale),
        "dimensions": list(obj.dimensions),
        "parent": obj.parent.name if obj.parent else None,
        "data": obj.data.name if obj.data else None,
        "materials": [slot.material.name for slot in obj.material_slots if slot.material],
        "visible": obj.visible_get(),
    }


def _execute_code(params):
    code = params.get("code")
    if not isinstance(code, str):
        raise ValueError("code must be a string")

    namespace = {"bpy": bpy, "__name__": "__blender_mcp__"}
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(compile(code, "<blender-mcp>", "exec"), namespace)

    result = namespace.get("result")
    try:
        json.dumps(result)
    except (TypeError, ValueError):
        result = repr(result)
    return {"stdout": output.getvalue(), "result": result}


COMMANDS = {
    "get_scene_info": _get_scene_info,
    "list_objects": _list_objects,
    "get_object_info": _get_object_info,
    "execute_code": _execute_code,
}


def _run_command(request):
    command = request.get("type")
    handler = COMMANDS.get(command)
    if handler is None:
        return {"status": "error", "message": f"unknown command: {command}"}
    params = request.get("params") or {}
    if not isinstance(params, dict):
        return {"status": "error", "message": "params must be a JSON object"}
    try:
        return {"status": "success", "result": handler(params)}
    except Exception as exc:
        return {
            "status": "error",
            "message": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }


# --- main-thread dispatch ---------------------------------------------------

def _drain_queue():
    while True:
        try:
            request, slot = _pending.get_nowait()
        except queue.Empty:
            break
        slot["response"] = _run_command(request)
        slot["event"].set()
    return TIMER_INTERVAL_SECONDS


def _submit(line):
    try:
        request = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return {"status": "error", "message": f"invalid JSON request: {exc}"}
    if not isinstance(request, dict):
        return {"status": "error", "message": "request must be a JSON object"}

    slot = {"event": threading.Event(), "response": None}
    _pending.put((request, slot))
    if not slot["event"].wait(REQUEST_WAIT_SECONDS):
        return {"status": "error", "message": "Blender did not process the command in time"}
    return slot["response"]


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        for raw in self.rfile:
            line = raw.strip()
            if not line:
                continue
            response = _submit(line)
            self.wfile.write(json.dumps(response, default=repr).encode("utf-8") + b"\n")
            self.wfile.flush()


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_server():
    global _server, _server_thread
    if _server is not None:
        return False
    _server = _Server((HOST, PORT), _Handler)
    _server_thread = threading.Thread(target=_server.serve_forever, name="blender-mcp", daemon=True)
    _server_thread.start()
    if not bpy.app.timers.is_registered(_drain_queue):
        bpy.app.timers.register(_drain_queue, persistent=True)
    return True


def stop_server():
    global _server, _server_thread
    if _server is None:
        return False
    _server.shutdown()
    _server.server_close()
    if _server_thread is not None:
        _server_thread.join(timeout=5)
    _server = None
    _server_thread = None
    if bpy.app.timers.is_registered(_drain_queue):
        bpy.app.timers.unregister(_drain_queue)
    return True


# --- Blender UI -------------------------------------------------------------

class BLENDERMCP_OT_start(bpy.types.Operator):
    bl_idname = "blendermcp.start"
    bl_label = "Start MCP Bridge"

    def execute(self, context):
        try:
            started = start_server()
        except OSError as exc:
            self.report({"ERROR"}, f"Could not bind {HOST}:{PORT}: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"MCP bridge listening on {HOST}:{PORT}" if started else "Already running")
        return {"FINISHED"}


class BLENDERMCP_OT_stop(bpy.types.Operator):
    bl_idname = "blendermcp.stop"
    bl_label = "Stop MCP Bridge"

    def execute(self, context):
        stop_server()
        self.report({"INFO"}, "MCP bridge stopped")
        return {"FINISHED"}


class BLENDERMCP_PT_panel(bpy.types.Panel):
    bl_label = "Blender MCP Bridge"
    bl_idname = "BLENDERMCP_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MCP"

    def draw(self, context):
        layout = self.layout
        running = _server is not None
        layout.label(text=f"Status: {'running' if running else 'stopped'} ({HOST}:{PORT})")
        row = layout.row()
        row.enabled = not running
        row.operator("blendermcp.start")
        row = layout.row()
        row.enabled = running
        row.operator("blendermcp.stop")


CLASSES = (BLENDERMCP_OT_start, BLENDERMCP_OT_stop, BLENDERMCP_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    stop_server()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
