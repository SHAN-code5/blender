# Blender MCP Bridge

Let Claude and other [MCP](https://modelcontextprotocol.io) clients build, inspect, render,
import, and export scenes in Blender, bring in HDRIs, textures, and models from free asset
libraries, and generate new models with AI.

- **22 structured tools**: create, modify, and delete objects; materials and modifiers;
  import and export glTF/GLB, FBX, OBJ, STL, PLY, USD, Alembic, and more; undo and redo;
  save. Each validates its input and picks the right Blender operator for the running
  version. Python is there when you need it.
- **Visual feedback**: `render_views` returns several auto-framed angles in one image,
  `render_image` renders through the camera, and `viewport_screenshot` shows exactly what
  the user sees. Previews work on headless servers too (Cycles on the CPU).
- **Assets**: search and import [Poly Haven](https://polyhaven.com) HDRIs, PBR textures, and
  models (free, CC0, no key), [Sketchfab](https://sketchfab.com) models, and
  [Poly Pizza](https://poly.pizza) low-poly models. Downloads run in the MCP server, so
  Blender never freezes, and they reach Blender even when it runs on another machine.
- **AI generation**: `generate_3d` with Tripo, Hyper3D Rodin, or any REST service, from text
  or a reference image. Long jobs return a job id instead of timing out.
- **Guides**: short built-in guides (workflow, modeling, materials, lighting, Blender Python,
  assets) the model reads before unfamiliar tasks.
- **Runs everywhere**: Blender 4.2+ as an extension and older versions as a legacy add-on,
  with the UI or headless; the server on Windows, macOS, and Linux with Python 3.10+ and MCP
  SDK 1.x or 2.x; any MCP client over stdio, SSE, or streamable HTTP.
- **Safe by default**: localhost only, optional token, a switch in Blender to turn off
  Python execution, an optional safe mode for scripts, checksummed and size-limited
  downloads. No telemetry.

## Contents

- [Quick start](#quick-start)
- [Client setup](#client-setup)
- [Tools](#tools)
- [Asset libraries](#asset-libraries)
- [AI model generation](#ai-model-generation)
- [Guides and prompts](#guides-and-prompts)
- [Headless Blender](#headless-blender)
- [Configuration](#configuration)
- [Updating](#updating)
- [Security](#security)
- [Compatibility](#compatibility)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

## Quick start

You need Blender and [uv](https://docs.astral.sh/uv/) for `uvx` (or Python 3.10+ with pip:
`pip install git+https://github.com/SHAN-code5/blender`, then run `blender-mcp-bridge`
instead of `uvx --from ... blender-mcp-bridge`).

**1. Install the Blender add-on.** Either let the CLI copy it into every Blender version it
finds:

```bash
uvx --from git+https://github.com/SHAN-code5/blender blender-mcp-bridge install-addon
```

then in Blender open **Edit → Preferences → Add-ons** and enable **Blender MCP Bridge**.

Or build zips and install one by hand:

```bash
uvx --from git+https://github.com/SHAN-code5/blender blender-mcp-bridge build-addon --out dist
```

- Blender 4.2 and newer: **Edit → Preferences → Get Extensions**, **⌄** menu (top right) →
  **Install from Disk**, choose `dist/blender_mcp_bridge-<version>.zip`.
- Older Blender: **Edit → Preferences → Add-ons → Install**, choose
  `dist/blender_mcp_bridge-<version>-legacy.zip`, then enable it.

**2. Start the bridge.** In the 3D View press **N**, open the **MCP** tab, and click **Start
MCP Bridge**. Tick **Start automatically** in the add-on preferences to skip this step next
time. No UI? See [Headless Blender](#headless-blender).

**3. Connect your MCP client** (details in [Client setup](#client-setup)):

```bash
uvx --from git+https://github.com/SHAN-code5/blender blender-mcp-bridge config claude-desktop --write
```

Restart the client and try:

> Build a low-poly campfire scene: logs, a ring of stones, an orange point light in the
> middle, a camera looking at it. Light it with a Poly Haven night HDRI, then show me four
> views.

Check the setup any time:

```bash
uvx --from git+https://github.com/SHAN-code5/blender blender-mcp-bridge doctor
```

## Client setup

The **Copy Client Config** button in Blender's MCP panel copies JSON for the running bridge,
including a non-default port or a token. From a terminal, `config` prints the setup for a
client and `--write` merges it into that client's config file, keeping the old file as
`.bak`:

| Client | Command | Writes to |
|---|---|---|
| Claude Desktop | `blender-mcp-bridge config claude-desktop --write` | macOS `~/Library/Application Support/Claude/claude_desktop_config.json`, Windows `%APPDATA%\Claude\claude_desktop_config.json` |
| Claude Code | `blender-mcp-bridge config claude-code` | prints a `claude mcp add ...` command to run |
| Cursor | `blender-mcp-bridge config cursor --write` | `~/.cursor/mcp.json` |
| VS Code | `blender-mcp-bridge config vscode --write` | `.vscode/mcp.json` in the current folder |
| Windsurf | `blender-mcp-bridge config windsurf --write` | `~/.codeium/windsurf/mcp_config.json` |
| HTTP clients | `blender-mcp-bridge config http` | prints a URL entry |

Prefix the commands with `uvx --from git+https://github.com/SHAN-code5/blender` when the
package is not installed. Add `--port 9877` or `--token ...` to include those settings,
`--path FILE` to write somewhere else, and `--launcher python` (this interpreter,
`python -m blenderMcp`) or `--launcher command` (`blender-mcp-bridge` on `PATH`) instead of
the default `uvx`.

The entry for Claude Desktop, Cursor, and Windsurf:

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

API keys for assets and generators go in an `env` block of the same entry (see
[Configuration](#configuration)).

On Windows, if the client cannot find `uvx`, put its full path (from `where uvx`) in
`command`.

Clients that connect over HTTP need the server running separately:

```bash
blender-mcp-bridge --transport streamable-http --http-port 8000   # http://127.0.0.1:8000/mcp
blender-mcp-bridge --transport sse                                # http://127.0.0.1:8000/sse
```

## Tools

| Group | Tool | What it does |
|---|---|---|
| Inspect | `get_bridge_status` | Connection, Blender and bridge versions, UI or background mode, configured asset sources and generators |
| | `get_scene_info` | Render settings, camera, collections, and every object's transform, size, parent, materials |
| | `list_objects` | Names, types, and locations, filtered by type or collection |
| | `get_object_info` | World bounding box, mesh statistics, modifiers, constraints, children, custom properties |
| Build | `create_object` | Cube, UV/ico sphere, cylinder, cone, torus, plane, circle, grid, monkey (all with UV maps), point/sun/spot/area light, camera, empty, text |
| | `modify_object` | Location, rotation (degrees), scale, exact dimensions, rename, visibility, parent |
| | `delete_objects` | Delete by name; reports names that were not found |
| | `set_material` | Principled BSDF color (`#RRGGBB` or linear RGB), metallic, roughness, emission, alpha |
| | `add_modifier` | Any modifier with its settings; object and collection settings take names |
| | `undo` | Undo or redo steps; every structured change is one step (UI mode) |
| Files | `import_model` | `.glb .gltf .fbx .obj .stl .ply .usd .usda .usdc .usdz .abc .dae .svg` or a `.blend`, optionally scaled to a size and placed |
| | `export_scene` | `.glb .gltf .fbx .obj .stl .ply .usd .usda .usdc .usdz .abc`, whole scene, selection, or named objects |
| | `save_blend_file` | Save, save as, or save a copy |
| Look | `render_image` | Render from the scene camera or an auto-framed front/back/left/right/top/iso view; returns the image |
| | `render_views` | Several auto-framed views in one image |
| | `viewport_screenshot` | The 3D View as the user sees it (UI mode) |
| Assets | `search_assets` | Search Poly Haven, Sketchfab, or Poly Pizza (or list Poly Haven categories) |
| | `import_asset` | Download and apply: HDRI → world lighting, texture set → PBR material, model → imported at a size and place |
| Generate | `generate_3d` | Text- or image-to-3D with Tripo, Hyper3D Rodin, a custom REST service, or `mock`; imports the result |
| | `get_generation_status` | Wait for (or cancel) a generation job; imports the model when it finishes |
| Python | `execute_blender_code` | Run Python with `bpy`, `C`, and `D`; returns printed output and a `result` variable |
| Guides | `get_guide` | Read a built-in guide |

Read-only tools carry MCP annotations, so clients that support them can approve those calls
automatically. File paths in `import_model`, `export_scene`, and `save_blend_file` refer to
the machine running Blender.

## Asset libraries

| Source | Content | Key | License |
|---|---|---|---|
| `polyhaven` | HDRIs, PBR texture sets, and models | none | CC0: no credit needed |
| `sketchfab` | User-uploaded models (downloadable ones) | `BLENDER_MCP_SKETCHFAB_API_KEY` to import; search works without | Per model; many need credit |
| `polypizza` | About 10,000 low-poly models | `BLENDER_MCP_POLYPIZZA_API_KEY` (free) | CC0 or CC-BY (credit required) |

Typical calls:

```text
search_assets(source="polyhaven", query="sunset beach", asset_type="hdris")
import_asset(source="polyhaven", asset_id="...", hdri_strength=1.2)

search_assets(source="polyhaven", query="worn wooden planks", asset_type="textures")
import_asset(source="polyhaven", asset_id="...", apply_to=["Floor"], uv_scale=4)

search_assets(source="polypizza", query="pine tree", licence="CC0")
import_asset(source="polypizza", asset_id="...", target_size=6, location=[3, 2, 0])
```

- **HDRIs** become a new world (the image is packed into the .blend). The previous world
  gets a fake user so it is not lost when saving.
- **Texture sets** become a Principled BSDF material with color, roughness, metallic,
  normal, and displacement maps, assigned to `apply_to`. `uv_scale` repeats the texture.
- **Models** are scaled so their largest side is `target_size` meters and stood with their
  bottom center at `location`. Poly Haven models come from the artist's .blend (just the
  full-detail level when the file has several), with the glTF version as a fallback when the
  running Blender cannot read the file.
- Every imported object, material, or world records `mcp_source`, `mcp_asset_id`,
  `mcp_url`, `mcp_license`, `mcp_author`, and `mcp_credit` as custom properties, so credits
  travel with the .blend. Show the credit to the user for CC-BY assets.
- Downloads are cached (see `BLENDER_MCP_CACHE`) and checked against the size and MD5
  checksum the library publishes. Archives and bundled texture paths cannot write outside
  the cache.
- When Blender runs on another machine or in a container, files are sent over the bridge
  connection in chunks automatically.

## AI model generation

| Provider | Setup | Text | Image |
|---|---|---|---|
| `tripo` | `BLENDER_MCP_TRIPO_API_KEY` ([Tripo platform](https://platform.tripo3d.ai)) | yes | yes |
| `hyper3d` | `BLENDER_MCP_HYPER3D_API_KEY` ([Hyper3D Rodin](https://hyper3d.ai)) | yes | yes |
| `custom_api` | `BLENDER_MCP_GENERATION_CONFIG` = path to a JSON provider config | yes | no |
| `mock` | nothing; always returns a cube, to test the pipeline | yes | yes |

Without `provider`, `generate_3d` uses the first configured of `tripo`, `hyper3d`, and
`custom_api`.

```text
generate_3d(prompt="a weathered wooden treasure chest", target_size=0.8, location=[0, 0, 0])
generate_3d(prompt="matching lid", image_path="/path/to/reference.png", provider="hyper3d")
```

Jobs run in the background on the MCP server. `generate_3d` waits up to `wait_seconds`
(default 50); if the job is still running it returns a `job_id` and the model calls
`get_generation_status(job_id)` until the model is imported (exactly once). Progress
notifications are sent while waiting. `image_path` is a PNG, JPG, or WEBP on the server's
machine.

`custom_api` reads a JSON file with the same fields as the AI 3D Object Generator's REST
provider:

```json
{
  "base_url": "https://generator.example.com",
  "api_key_env": "MY_GENERATOR_KEY",
  "generate_path": "/generate",
  "status_path": "/jobs/{job_id}",
  "job_id_path": "data.id",
  "status_path_value": "data.status",
  "progress_path": "data.progress",
  "output_url_path": "data.result.download_url",
  "error_path": "data.error.message"
}
```

Unknown keys are rejected with the list of valid ones.

## Guides and prompts

`get_guide(topic)` returns a short guide; the same text is exposed as MCP resources
`blender://guides/<topic>`:

| Topic | Covers |
|---|---|
| `workflow` | How to approach a task, units and axes, real-world sizes |
| `modeling` | Primitives, sizes, placement, and the most useful modifiers with their settings |
| `materials` | Values for common surfaces, color formats, PBR textures |
| `lighting` | Lighting, cameras, and preview rendering |
| `python` | Writing `execute_blender_code` scripts; API differences between Blender 3.x, 4.x, and 5.x |
| `assets` | Asset libraries and AI generation |

The `build_scene` prompt (shown as a slash command in some clients) walks the model through
planning, building, and checking a scene from a description.

## Headless Blender

Run Blender without its UI, on a server, in a container, or in CI:

```bash
blender-mcp-bridge headless scene.blend --blender /path/to/blender --port 9876
```

Options: `--token SECRET`, `--no-code` (disable Python execution), `--host`, and `--gpu`.
Without a GPU, EEVEE and Workbench renders crash Blender in background mode, so the bridge
renders with Cycles on the CPU there and refuses GPU engines; pass `--gpu` (or set
`BLENDER_MCP_GPU=1`) on machines that have one. Undo, redo, and viewport screenshots need
the UI.

The same runner can be started directly:
`blender -b scene.blend --python blenderMcp/runHeadless.py -- --port 9876`.

## Configuration

Flags beat environment variables, which beat the defaults.

| Flag | Environment variable | Default | Purpose |
|---|---|---|---|
| `--host` | `BLENDER_MCP_HOST` (or `BLENDER_HOST`) | `127.0.0.1` | Where the Blender bridge listens |
| `--port` | `BLENDER_MCP_PORT` (or `BLENDER_PORT`) | `9876` | Bridge port; must match the add-on preference |
| `--token` | `BLENDER_MCP_TOKEN` | none | Shared secret, if one is set in the add-on |
| `--timeout` | `BLENDER_MCP_TIMEOUT` | `30` | Seconds per command (renders, imports, scripts, and generation allow more) |
| `--safe-mode` | `BLENDER_MCP_SAFE_MODE=1` | off | Check scripts before they run |
| `--transport` | `BLENDER_MCP_TRANSPORT` | `stdio` | `stdio`, `sse`, or `streamable-http` |
| `--http-host`, `--http-port` | `BLENDER_MCP_HTTP_HOST`, `BLENDER_MCP_HTTP_PORT` | `127.0.0.1`, `8000` | HTTP transports only |
| | `BLENDER_MCP_CACHE` | per-user cache folder | Where downloads and generated models are kept |
| | `BLENDER_MCP_SKETCHFAB_API_KEY` | none | Sketchfab imports |
| | `BLENDER_MCP_POLYPIZZA_API_KEY` | none | Poly Pizza |
| | `BLENDER_MCP_TRIPO_API_KEY` | none | Tripo generation |
| | `BLENDER_MCP_HYPER3D_API_KEY` | none | Hyper3D Rodin generation |
| | `BLENDER_MCP_GENERATION_CONFIG` | none | JSON config for `custom_api` |

The `BLENDERMCP_SKETCHFAB_API_KEY`, `BLENDERMCP_POLYPIZZA_API_KEY`, and
`BLENDERMCP_HYPER3D_API_KEY` spellings are accepted too. The default cache is
`~/.cache/blender-mcp-bridge` on Linux, `~/Library/Caches/blender-mcp-bridge` on macOS, and
`%LOCALAPPDATA%\blender-mcp-bridge\cache` on Windows.

Example client entry with keys:

```json
{
  "mcpServers": {
    "blender": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/SHAN-code5/blender", "blender-mcp-bridge"],
      "env": {
        "BLENDER_MCP_SKETCHFAB_API_KEY": "...",
        "BLENDER_MCP_TRIPO_API_KEY": "..."
      }
    }
  }
}
```

In Blender, **Edit → Preferences → Add-ons → Blender MCP Bridge** sets the host, port,
token, **Start automatically**, and **Allow Python execution**.

To drive several Blender instances, give each bridge its own port and add one client entry
per port (`blender-mcp-bridge config cursor --port 9877`).

## Updating

```bash
uvx --from git+https://github.com/SHAN-code5/blender blender-mcp-bridge update
```

`update` refreshes every installed copy of the add-on (legacy add-on folders and 4.2+
extensions), keeps the previous file as `__init__.py.bak`, never downgrades a newer copy, and
`--dry-run` shows what it would change. Then restart Blender (or disable and re-enable the
add-on) and your MCP client. To make `uvx` fetch the newest server code, run it once with
`uvx --refresh --from git+https://github.com/SHAN-code5/blender blender-mcp-bridge --version`;
pip users run `pip install -U git+https://github.com/SHAN-code5/blender`. `doctor` warns when
the add-on and the server versions differ.

## Security

- The bridge listens on `127.0.0.1` by default and cannot be reached from other machines.
  For a remote Blender, prefer an SSH tunnel to changing the host.
- **Token:** click the refresh icon next to **Token** in the add-on preferences, restart the
  bridge, and give the same value to the server (`--token` or `BLENDER_MCP_TOKEN`; **Copy
  Client Config** includes it). Use it on shared machines, where any local user could
  otherwise connect.
- **Allow Python execution** (add-on preference) turns `execute_blender_code` off at the
  Blender end; the structured tools keep working.
- **Safe mode** (`--safe-mode`) rejects scripts that import `os`, `subprocess`, `socket` and
  similar modules, call `open`/`exec`/`eval`, register handlers, timers, or classes that
  outlive the script, or quit Blender. It guards against mistakes; it is not a sandbox,
  because Python can be obfuscated past any static check.
- API keys stay in the MCP server process; they are never sent to Blender or written to
  the scene.
- Downloads are limited in size, verified against published checksums, and confined to the
  cache folder; zip archives with unsafe paths are rejected.
- Blender's importers run in-process and are not a sandbox. Import files only from sources
  you trust, or use a throwaway Blender.

## Compatibility

| Component | Tested | Expected to work |
|---|---|---|
| Blender, background mode | 4.0 (Ubuntu package), 4.5 LTS, 5.0, 5.2 LTS | 4.2+ |
| Blender, UI mode | 4.0 (Ubuntu package, under Xvfb): panel, auto-start, undo/redo, viewport screenshot, Workbench render, import/export | all versions with a UI |
| Add-on install | Extension zip on 4.5 and 5.0; legacy zip on 4.0, 4.5, and 5.0 | Extension on 4.2+; legacy add-on from 3.0 (3.x untested) |
| MCP Python SDK | 1.10, 1.30, 2.2, 2.3 | `mcp>=1.10,<3` |
| Python (server) | 3.10, 3.11, 3.13 | 3.10+ |
| Server OS | Linux | Windows and macOS (covered by the CI matrix) |
| MCP transport | stdio (and in-memory sessions in tests) | SSE and streamable HTTP through the MCP SDK |

The asset libraries and generators are tested against local stand-ins that follow each
service's published API format (search, metadata, download, checksums, job polling). They
have not been run against the live services from this repository's test environment, so
report any mismatch you hit.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Could not reach the Blender bridge` | Start it in Blender (**N → MCP → Start MCP Bridge**) or run `blender-mcp-bridge headless`, and check the port matches |
| `missing or wrong token` | Use the token from the add-on preferences, or clear it there |
| `Blender did not answer within ...` | Blender is busy (rendering, a dialog, a long script). Retry or raise `timeout_seconds` |
| `eevee needs a GPU` | Headless without a GPU: use `engine="cycles"`, or start with `--gpu` if there is one |
| `Could not listen on the configured port` | Another Blender already uses it; pick another port |
| glTF import/export fails with `No module named 'numpy'` | Blender's own glTF add-on needs numpy. Official builds include it; on a distribution build (for example Ubuntu's `blender` package) install `python3-numpy`, and make sure no other `python3.x` (pyenv, uv, conda) comes first on `PATH`: such builds take their Python from it |
| Sketchfab import asks for a key | Set `BLENDER_MCP_SKETCHFAB_API_KEY` in the client entry's `env` |
| Poly Pizza download blocked | `static.poly.pizza` challenges datacenter and VPN addresses; retry from a home connection |
| `generate_3d` returned a `job_id` | Normal for long jobs: call `get_generation_status(job_id)` |
| Client shows no tools | Run `blender-mcp-bridge doctor`; restart the client after editing its config |

## Development

```bash
pip install -e . pytest
pytest -q tests/blenderBridge.py tests/blenderBridgeAssets.py   # server, CLI, assets, generation
pip install "bpy==4.5.*"                                       # Python 3.11 (5.1+ wheels need 3.13)
pytest -q tests/blenderBridgeAddon.py                           # the add-on against real Blender
xvfb-run -a python checks/blenderMcpUi.py --blender blender    # Blender's UI (Linux)
```

| Path | Role |
|---|---|
| `server.py` | MCP tools, resources, and the prompt |
| `compat.py` | MCP SDK 1.x / 2.x differences |
| `connection.py` | One TCP connection per request to the add-on |
| `transfer.py` | Sends files to a Blender on another machine |
| `assets.py`, `net.py` | Asset library clients and the HTTP helpers |
| `generation.py` | Tripo and Rodin providers and the background job runner |
| `guides/` | The built-in guides |
| `config.py`, `safety.py` | Settings and safe-mode checks |
| `cli.py`, `clients.py`, `addonTools.py` | `blender-mcp-bridge` subcommands |
| `addon/` | The Blender add-on and its extension manifest |
| `runHeadless.py` | Background-mode runner |

The server and the add-on speak one JSON object per line over TCP:

```json
{"id": "8c1e...", "type": "create_object", "params": {"kind": "cube"}, "timeout": 30, "token": "..."}
{"id": "8c1e...", "status": "success", "result": {"name": "Cube"}}
{"id": "8c1e...", "status": "error", "code": "not_found", "message": "object not found: Ghost"}
```

To add a tool: add a `cmd_*` function to `addon/__init__.py` and register it in `COMMANDS`,
add an `@compat.tool` function in `server.py`, and cover both in the tests.
