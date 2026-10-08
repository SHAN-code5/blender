"""MCP server exposing a running Blender session as tools.

Run with:  python -m blenderMcp.server
Requires the "Blender MCP Bridge" add-on to be started inside Blender.
"""

from typing import Any, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .connection import BlenderConnection, BlenderError

mcp = MCPServer(
    "blender",
    instructions=(
        "Tools for inspecting and editing the open Blender scene. "
        "Start the 'Blender MCP Bridge' add-on in Blender before calling them."
    ),
)

_connection = BlenderConnection()


def _send(command: str, params: Optional[dict] = None) -> Any:
    try:
        return _connection.send_command(command, params)
    except BlenderError as exc:
        # ToolError reaches the model as a readable message instead of a generic crash.
        raise ToolError(str(exc)) from exc


@mcp.tool()
def get_scene_info() -> dict:
    """Return the active scene's name, frame range, and object count."""
    return _send("get_scene_info")


@mcp.tool()
def list_objects(object_type: Optional[str] = None) -> list:
    """List objects in the active scene.

    object_type optionally filters by Blender type, e.g. MESH, CAMERA, LIGHT, EMPTY.
    """
    return _send("list_objects", {"object_type": object_type})


@mcp.tool()
def get_object_info(name: str) -> dict:
    """Return transform, dimensions, parent, data block, and materials for one object."""
    return _send("get_object_info", {"name": name})


@mcp.tool()
def execute_blender_code(code: str) -> dict:
    """Run Python code inside Blender with `bpy` already imported.

    Anything printed is returned as `stdout`. Assign a value to a variable named
    `result` to have it returned as `result` (must be JSON-serializable, otherwise
    its repr is returned).

    This runs arbitrary Python in the Blender process. Only use it on scenes you trust.
    """
    return _send("execute_code", {"code": code})


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
