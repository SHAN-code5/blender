"""Blender MCP: exposes an open Blender session to MCP clients."""

__version__ = "0.3.0"

# Version of the JSON line protocol spoken between the MCP server and the add-on.
# Bump when a request or response field changes meaning.
PROTOCOL_VERSION = 1
