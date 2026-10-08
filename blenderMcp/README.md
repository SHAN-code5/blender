# Blender MCP Bridge

Let Claude and other [MCP](https://modelcontextprotocol.io) clients build, inspect, render,
import, and export scenes in Blender.

- **Runs everywhere:** Blender 4.2+ as an extension, Blender 3.0–4.1 as a legacy add-on, with
  the UI or headless (`blender -b`). The server runs on Windows, macOS, and Linux with Python
  3.10+ and either major version of the MCP Python SDK (1.x or 2.x).
- **Works with any MCP client:** Claude Desktop, Claude Code, Cursor, VS Code, Windsurf, or any
  client that speaks stdio, SSE, or streamable HTTP. One command prints or writes the config.
- **Structured tools first:** create, modify, delete, material, modifier, import, export, render,
  and save tools validate their input and pick the right operator for the running Blender
  version. Arbitrary Python is still available when you need it.
- **Visual feedback without a GPU:** `render_image` auto-frames the scene from any side and
  returns the picture to the model, even on a headless server (Cycles on the CPU).
- **Safer by default:** localhost only, optional token authentication, a switch in Blender to
  turn off Python execution, and an optional safe mode for scripts. With the UI open, changes
  made through the structured tools are pushed as undo steps. No telemetry.

## Contents

- [Quick start](#quick-start)
- [Client setup](#client-setup)
- [Tools](#tools)
- [Headless Blender](#headless-blender)
- [Configuration](#configuration)
- [Security](#security)
- [Compatibility](#compatibility)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

## Quick start

You need [uv](https://docs.astral.sh/uv/) (for `uvx`) or Python 3.10+ with pip, and Blender.

**1. Install the Blender add-on.** Pick one:

- Let the CLI copy it into every Blender version it finds:

  ```bash
  uvx --from git+https://github.com/SHAN-code5/blender blender-mcp-bridge install-addon
  ```

  Then in Blender: **Edit → Preferences → Add-ons**, enable **Blender MCP Bridge**.

- Or build zips and install one from disk:

  ```bash
  uvx --from git+https://github.com/SHAN-code5/blender blender-mcp-bridge build-addon --out dist
  ```

  Blender 4.2+: **Edit → Preferences → Get Extensions → ⌄ → Install from Disk**, choose
  `dist/blender_mcp_bridge-<version>.zip`.
  Blender 3.0–4.1: **Edit → Preferences → Add-ons → Install**, choose
  `dist/blender_mcp_bridge-<version>-legacy.zip`.

**2. Start the bridge.** In the 3D View press **N**, open the **MCP** tab, click **Start MCP
Bridge**. Tick **Start automatically** in the add-on preferences to skip this next time.

**3. Add the server to your client** (see [Client setup](#client-setup)), restart the client,
and ask something like:

> Build a low-poly campfire scene with a few logs, rocks around it, an orange point light in the
> middle and a camera looking at it, then render it from the iso view.

Check the connection at any time:

```bash
uvx --from git+https://github.com/SHAN-code5/blender blender-mcp-bridge doctor
```

## Client setup

The **Copy Client Config** button in Blender's MCP panel copies the JSON for the running bridge,
including a non-default port or token. From a terminal, `config` prints it for each client and
`--write` merges it into the client's config file (the old file is kept as `.bak`):

| Client | Print | Write |
|---|---|---|
| Claude Desktop | `blender-mcp-bridge config claude-desktop` | `... config claude-desktop --write` |
| Claude Code | `blender-mcp-bridge config claude-code` (prints a `claude mcp add` command) | run the printed command |
| Cursor | `blender-mcp-bridge config cursor` | `... config cursor --write` (`~/.cursor/mcp.json`) |
| VS Code | `blender-mcp-bridge config vscode` | `... config vscode --write` (`.vscode/mcp.json`) |
| Windsurf | `blender-mcp-bridge config windsurf` | `... config windsurf --write` |
| HTTP clients | `blender-mcp-bridge config http` | — |

Prefix each command with `uvx --from git+https://github.com/SHAN-code5/blender` if the package
is not installed. Add `--port 9877` or `--token ...` to include those settings.

The generated entry for Claude Desktop, Cursor, and Windsurf looks like this:

```json
{
  "mcpServers": {
    "blender": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/SHAN-code5/blender", "blender-mcp-bridge"]
    }
  }
}
```

Other launchers:

- `--launcher python` uses the current interpreter (`python -m blenderMcp`) after
  `pip install git+https://github.com/SHAN-code5/blender`.
- `--launcher command` uses `blender-mcp-bridge` from `PATH` (`pipx install ...`).

On Windows, if the client cannot find `uvx`, put its full path (from `where uvx`) in `command`.

For clients that connect over HTTP, run the server yourself and point the client at the URL:

```bash
blender-mcp-bridge --transport streamable-http --http-port 8000   # http://127.0.0.1:8000/mcp
blender-mcp-bridge --transport sse                                # http://127.0.0.1:8000/sse
```

## Tools

| Tool | What it does |
|---|---|
| `get_bridge_status` | Connection check, Blender and bridge versions, background/UI mode, what is allowed |
| `get_scene_info` | Render settings, camera, collections, and every object's transform, size, parent, materials |
| `list_objects` | Names, types, and locations, filtered by type or collection |
| `get_object_info` | World bounding box, mesh statistics, modifiers, constraints, children, custom properties |
| `create_object` | Cube, UV/ico sphere, cylinder, cone, torus, plane, circle, grid, monkey, point/sun/spot/area light, camera, empty, text |
| `modify_object` | Location, rotation (degrees), scale, target dimensions, rename, visibility, parent |
| `delete_objects` | Delete by name; reports names that were not found |
| `set_material` | Principled BSDF color (`#RRGGBB` or linear RGB), metallic, roughness, emission, alpha |
| `add_modifier` | Any modifier type with properties; object and collection properties accept names |
| `import_model` | `.glb .gltf .fbx .obj .stl .ply .usd .usda .usdc .usdz .abc .dae .svg`, or append from `.blend` |
| `export_scene` | `.glb .gltf .fbx .obj .stl .ply .usd .usda .usdc .usdz .abc`, whole scene, selection, or named objects |
| `render_image` | Render from the scene camera or an auto-framed front/back/left/right/top/iso view; returns the image |
| `viewport_screenshot` | The 3D View exactly as the user sees it (UI mode only) |
| `execute_blender_code` | Run Python with `bpy`, `C`, and `D`; returns printed output and a `result` variable |
| `save_blend_file` | Save, save as, or save a copy |

Read-only tools are annotated as such, so clients that support MCP tool annotations can
approve them automatically. File paths refer to the machine running Blender.

## Headless Blender

Run Blender without a UI, on a server, in Docker, or in CI:

```bash
blender -b scene.blend --python blenderMcp/runHeadless.py -- --port 9876 [--token SECRET] [--no-code]
```

`blenderMcp/runHeadless.py` is in this repository (and in the installed package). Without a GPU,
EEVEE and Workbench renders crash Blender in background mode, so `render_image` uses Cycles on
the CPU there and refuses GPU engines. Set `BLENDER_MCP_GPU=1` when the machine has a GPU.

## Configuration

Flags beat environment variables, which beat the defaults.

| Flag | Environment variable | Default | Purpose |
|---|---|---|---|
| `--host` | `BLENDER_MCP_HOST` (or `BLENDER_HOST`) | `127.0.0.1` | Where the Blender bridge listens |
| `--port` | `BLENDER_MCP_PORT` (or `BLENDER_PORT`) | `9876` | Bridge port; match the add-on preference |
| `--token` | `BLENDER_MCP_TOKEN` | none | Shared secret, if set in the add-on |
| `--timeout` | `BLENDER_MCP_TIMEOUT` | `30` | Default seconds per command (renders, imports, and scripts allow more) |
| `--safe-mode` | `BLENDER_MCP_SAFE_MODE=1` | off | Check scripts before they run (see below) |
| `--transport` | `BLENDER_MCP_TRANSPORT` | `stdio` | `stdio`, `sse`, or `streamable-http` |
| `--http-host`, `--http-port` | `BLENDER_MCP_HTTP_HOST`, `BLENDER_MCP_HTTP_PORT` | `127.0.0.1`, `8000` | HTTP transports only |

To drive several Blender instances, give each bridge its own port in the add-on preferences and
add one client entry per port (`blender-mcp-bridge config cursor --port 9877`).

In Blender, **Edit → Preferences → Add-ons → Blender MCP Bridge** sets the host, port, token,
auto-start, and whether Python execution is allowed.

## Security

- The bridge listens on `127.0.0.1` by default and is unreachable from other machines. To reach
  a remote Blender, prefer an SSH tunnel to changing the host.
- **Token:** click the refresh icon next to **Token** in the add-on preferences to generate one,
  restart the bridge, and give the same value to the server (`--token` or `BLENDER_MCP_TOKEN`;
  **Copy Client Config** includes it). Requests without it are rejected. Use this on shared
  machines, where any local user could otherwise connect.
- **Allow Python execution** (add-on preference) turns `execute_blender_code` off at the Blender
  end; the structured tools keep working.
- **Safe mode** (`--safe-mode`) statically rejects scripts that import `os`, `subprocess`,
  `socket` and similar modules, call `open`/`exec`/`eval`, register handlers, timers, or classes
  that outlive the script, or quit Blender. It guards against mistakes; it is not a sandbox,
  because Python can be obfuscated past any static check. Run untrusted clients against a
  throwaway Blender.
- Blender's importers run in-process. Only import files you trust.

## Compatibility

| Component | Tested | Expected to work |
|---|---|---|
| Blender (add-on) | 4.5 LTS and 5.0 via the `bpy` wheels (background mode) | 4.2+ as an extension; 3.0–4.1 as a legacy add-on (untested) |
| Install methods | Extension zip and legacy zip, both on 4.5 and 5.0 | Copy via `install-addon` |
| MCP SDK | 1.10, 1.30, 2.2, 2.3 | `mcp>=1.10,<3` |
| Python (server) | 3.10, 3.11, 3.13 | 3.10+ |
| OS (server) | Linux | Windows and macOS (covered by the CI matrix, which runs on pull requests) |
| Blender UI features | Not covered by automated tests (`viewport_screenshot`, panel, undo steps) | Blender 3.2+ |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Could not reach the Blender bridge` | Start it in Blender (**N → MCP → Start MCP Bridge**) and check the port matches |
| `missing or wrong token` | Use the token from the add-on preferences, or clear it there |
| `Blender did not answer within ...` | Blender is busy (rendering, a modal dialog, a long script). Retry, or pass a longer timeout |
| `eevee needs a GPU` | Headless without a GPU: use `engine="cycles"`, or set `BLENDER_MCP_GPU=1` if there is one |
| `Could not listen on the configured port` | Another Blender already uses it; pick another port in the preferences |
| Client shows no tools | Run `blender-mcp-bridge doctor`, restart the client after editing its config |

## Development

```bash
pip install -e . pytest
pytest -q tests/blenderBridge.py                      # server, CLI, safe mode (no Blender needed)
pip install "bpy==4.5.*"                              # Python 3.11
pytest -q tests/blenderBridgeAddon.py                 # the add-on against real Blender
```

Layout:

| Path | Role |
|---|---|
| `server.py` | MCP tools |
| `compat.py` | MCP SDK 1.x / 2.x differences |
| `connection.py` | One TCP connection per request to the add-on |
| `config.py` | Flags and environment variables |
| `safety.py` | Safe-mode script checks |
| `cli.py`, `clients.py`, `addonTools.py` | `blender-mcp-bridge` subcommands |
| `addon/` | The Blender add-on and its extension manifest |
| `runHeadless.py` | Background-mode runner |

Protocol between server and add-on: one JSON object per line over TCP.

```json
{"id": "8c1e...", "type": "create_object", "params": {"kind": "cube"}, "timeout": 30, "token": "..."}
{"id": "8c1e...", "status": "success", "result": {"name": "Cube", "...": "..."}}
{"id": "8c1e...", "status": "error", "code": "not_found", "message": "object not found: Ghost"}
```

To add a tool, add a `cmd_*` function to `addon/__init__.py` and register it in `COMMANDS`, add
an `@compat.tool` function in `server.py`, and cover both in the tests.
