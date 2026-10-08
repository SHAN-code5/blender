"""Compatibility layer over the MCP Python SDK.

mcp 1.x ships the high-level server as ``mcp.server.fastmcp.FastMCP``; mcp 2.x
renamed it to ``mcp.server.mcpserver.MCPServer`` and moved HTTP options from
server settings to ``run()`` keyword arguments. Everything else in this package
goes through the helpers here so both major versions work unchanged.
"""

import inspect
from importlib import metadata

try:  # mcp >= 2
    from mcp.server.mcpserver import Context, Image, MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError

    SDK_MAJOR = 2
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import Context, FastMCP as _Server, Image
    from mcp.server.fastmcp.exceptions import ToolError

    SDK_MAJOR = 1

try:
    from mcp.types import ToolAnnotations
except ImportError:  # very old 1.x releases
    ToolAnnotations = None

TRANSPORTS = ("stdio", "sse", "streamable-http")

__all__ = ["Context", "Image", "ToolError", "SDK_MAJOR", "TRANSPORTS", "create_server", "sdk_version", "tool",
           "resource", "prompt", "report_progress", "run"]


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


def resource(server, uri: str, *, name: str, description: str, mime_type: str = "text/markdown"):
    accepted = inspect.signature(server.resource).parameters
    options = {"name": name, "description": description, "mime_type": mime_type}
    return server.resource(uri, **{k: v for k, v in options.items() if k in accepted})


def prompt(server, *, name: str, description: str):
    return server.prompt(name=name, description=description)


async def report_progress(ctx, progress: float, total: float = 1.0, message: str = "") -> None:
    """Send a progress notification when the client asked for them; never fail the tool."""
    if ctx is None:
        return
    try:
        try:
            await ctx.report_progress(progress, total, message or None)
        except TypeError:  # mcp < 1.13 has no message argument
            await ctx.report_progress(progress, total)
    except Exception:
        pass


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
