"""Compatibility layer over the MCP Python SDK.

mcp 1.x ships the high-level server as ``mcp.server.fastmcp.FastMCP``; mcp 2.x
renamed it to ``mcp.server.mcpserver.MCPServer`` and moved HTTP options from
server settings to ``run()`` keyword arguments. Everything else in this package
goes through the helpers here so both major versions work unchanged.
"""

import inspect
from importlib import metadata

try:  # mcp >= 2
    from mcp.server.mcpserver import Image, MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError

    SDK_MAJOR = 2
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server, Image
    from mcp.server.fastmcp.exceptions import ToolError

    SDK_MAJOR = 1

try:
    from mcp.types import ToolAnnotations
except ImportError:  # very old 1.x releases
    ToolAnnotations = None

TRANSPORTS = ("stdio", "sse", "streamable-http")

__all__ = ["Image", "ToolError", "SDK_MAJOR", "TRANSPORTS", "create_server", "sdk_version", "tool", "run"]


def sdk_version() -> str:
    try:
        return metadata.version("mcp")
    except metadata.PackageNotFoundError:
        return "unknown"


def create_server(name: str, instructions: str):
    return _Server(name, instructions=instructions)


def tool(server, *, title: str, read_only: bool = False, destructive: bool = False,
         idempotent: bool = False, open_world: bool = False):
    """Register a tool with whichever metadata the installed SDK understands."""
    accepted = inspect.signature(server.tool).parameters
    kwargs = {}
    if "title" in accepted:
        kwargs["title"] = title
    if "structured_output" in accepted:
        # Tools return plain dicts, lists, or images; let the SDK pick content types.
        kwargs["structured_output"] = False
    if ToolAnnotations is not None and "annotations" in accepted:
        # camelCase field names are accepted by both 1.x and 2.x models.
        kwargs["annotations"] = ToolAnnotations(
            title=title,
            readOnlyHint=read_only,
            destructiveHint=destructive,
            idempotentHint=idempotent,
            openWorldHint=open_world,
        )
    return server.tool(**kwargs)


def run(server, transport: str = "stdio", host: str = "127.0.0.1", port: int = 8000) -> None:
    if transport not in TRANSPORTS:
        raise ValueError(f"unknown transport {transport!r}; expected one of {', '.join(TRANSPORTS)}")
    if transport == "stdio":
        server.run("stdio")
    elif SDK_MAJOR >= 2:
        server.run(transport, host=host, port=port)
    else:
        server.settings.host = host
        server.settings.port = port
        server.run(transport)
