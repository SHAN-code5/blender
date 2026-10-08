# Blender MCP

An MCP server that lets an MCP client (Claude Desktop, Claude Code, etc.) inspect
and edit the scene open in Blender.

It has two parts:

| Part | File | Runs in |
|---|---|---|
| MCP server | `blenderMcp/server.py` | Python process started by the MCP client |
| Blender add-on | `blenderMcp/blenderAddon.py` | Inside Blender, as a localhost socket server |

The two talk over `127.0.0.1:9876` with newline-delimited JSON. Blender commands
run on Blender's main thread, so `bpy` is never touched from a worker thread.

## Tools

| Tool | What it does |
|---|---|
| `get_scene_info` | Active scene name, frame range, object count |
| `list_objects(object_type?)` | Objects in the scene, optionally filtered (`MESH`, `CAMERA`, ...) |
| `get_object_info(name)` | Transform, dimensions, parent, data block, materials |
| `execute_blender_code(code)` | Run Python with `bpy` available; `print` output and a `result` variable are returned |

`execute_blender_code` runs arbitrary Python inside Blender. Use it only on
scenes and with clients you trust.

## Setup

### 1. Install the server dependency

Requires Python 3.10+ and `mcp` 2.x:

```bash
pip install -r blenderMcp/requirements.txt
```

### 2. Install the Blender add-on

Blender 4.5+:

1. **Edit → Preferences → Add-ons → Install from Disk**, pick `blenderMcp/blenderAddon.py`.
2. Enable **Blender MCP Bridge**.
3. Open the 3D View sidebar (`N`) → **MCP** tab → **Start MCP Bridge**.

The status line shows `running (127.0.0.1:9876)` when it is listening.

### 3. Register the server with your MCP client

From the repository root, the server is started with:

```bash
python -m blenderMcp.server
```

Claude Code (project-level `.mcp.json`):

```json
{
  "mcpServers": {
    "blender": {
      "command": "python",
      "args": ["-m", "blenderMcp.server"],
      "cwd": "/absolute/path/to/blender"
    }
  }
}
```

Claude Desktop (`claude_desktop_config.json`): same `command`, `args`, and `cwd` under
`mcpServers.blender`.

Use an absolute path to the interpreter if `python` is not the one where `mcp` is installed.

## Protocol

Request (one line):

```json
{"type": "list_objects", "params": {"object_type": "MESH"}}
```

Responses (one line):

```json
{"status": "success", "result": [...]}
{"status": "error", "message": "ValueError: object not found: Ghost", "traceback": "..."}
```

Supported `type` values: `get_scene_info`, `list_objects`, `get_object_info`, `execute_code`.

To add a tool:

1. Add a handler `_my_command(params)` in `blenderAddon.py` and register it in `COMMANDS`.
2. Add a `@mcp.tool()` function in `server.py` that calls `_send("my_command", {...})`.
3. Add a test in `tests/blenderBridge.py`.

## Security

- The add-on binds to `127.0.0.1` only. It is not reachable from other machines.
- There is no authentication. Any local process can connect to port 9876 and run
  code through `execute_code`. Stop the bridge when you are not using it.
- Blender must be started by you, and the bridge must be started from the panel.

## Tests

The protocol and tool-forwarding tests run without Blender:

```bash
pytest -q tests/blenderBridge.py
```
