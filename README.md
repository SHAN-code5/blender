# Blender MCP Bridge & AI 3D Object Generator

Two open-source Blender tools in one repository:

| Tool | What it does | Docs |
|---|---|---|
| **Blender MCP Bridge** (`blenderMcp/`) | Lets Claude and other [MCP](https://modelcontextprotocol.io) clients build, inspect, render, import, and export Blender scenes; pull HDRIs, textures, and models from Poly Haven, Sketchfab, and Poly Pizza; and generate models with Tripo or Hyper3D Rodin. Tested on Blender 4.0, 4.5, 5.0, and 5.2, with or without Blender's UI. | [blenderMcp/README.md](blenderMcp/README.md) |
| **AI 3D Object Generator** (`ai3dgenerator/`) | A Blender 4.5+ extension with a sidebar workspace for text-to-3D and image-to-3D generation through any REST provider, with history, an asset library, batch generation, and export. | [below](#ai-3d-object-generator) |

Both are MIT licensed and send no telemetry.

## Blender MCP Bridge: quick start

You need Blender and [uv](https://docs.astral.sh/uv/) (or Python 3.10+ with pip).

1. Install the Blender add-on into every Blender version on this computer:

   ```bash
   uvx --from git+https://github.com/SHAN-code5/blender blender-mcp-bridge install-addon
   ```

   In Blender: **Edit → Preferences → Add-ons**, enable **Blender MCP Bridge**, then in the 3D
   View press **N**, open the **MCP** tab, and click **Start MCP Bridge**.

2. Add the server to your MCP client, for example Claude Desktop:

   ```bash
   uvx --from git+https://github.com/SHAN-code5/blender blender-mcp-bridge config claude-desktop --write
   ```

   `config` also supports `claude-code`, `cursor`, `vscode`, `windsurf`, and `http`.

3. Restart the client and ask for a scene. Check the connection any time with
   `... blender-mcp-bridge doctor`.

Everything else (all 22 tools, headless Blender, API keys, security, compatibility,
troubleshooting) is in [blenderMcp/README.md](blenderMcp/README.md).

## AI 3D Object Generator

A Blender 4.5+ extension for creating, organizing, and importing 3D assets from text
prompts or validated local reference images. It is provider-independent: the UI, job
manager, downloader, importer, post-processing, library, and export layers do not depend
on a specific vendor. The same provider, job, and download code powers `generate_3d` in the
Blender MCP Bridge.

### Overview

The extension accepts a prompt such as:

> A realistic wooden Indian village chair with carved legs

It validates the request, submits it to the configured provider, polls the long-running
job, downloads a size-bounded local asset, validates its format, and optionally imports it
into the dedicated `AI3D_Generated` collection.

The **AI 3D** tab in the 3D View sidebar contains these panels:

- **CREATE**: text-to-3D and image-to-3D modes, provider and model selection, prompt
  templates, prompt enhancement, reference-image validation and preview, generation status,
  and cancellation.
- **ADVANCED**: topology, polygon target, texture resolution, UV, material, texture,
  smoothing, placement, and import options.
- **BATCH**: sequential, timer-driven batch generation with pause, resume, cancel, retry,
  deletion, and reordering.
- **HISTORY**: bounded local generation records with import, retry, delete, and clear,
  plus import, export, and cleanup of the last generated asset.
- **ASSET LIBRARY**: persistent asset records with names, tags, collections, favorites,
  filtering, re-import, regeneration, rename, and removal.
- **SETTINGS**: non-secret workspace settings and a shortcut to the add-on preferences,
  where provider URLs and credentials are configured.

Successful downloads are copied into the library's `generated/` folder and recorded
automatically. The library can be relocated from the ASSET LIBRARY panel.

### Features

- Provider registry with `custom_api` (configurable REST), `local_api`, and an offline
  `mock` provider.
- Configurable REST endpoints, authentication header and prefix, request payload, and dotted
  response paths.
- Draft, Standard, and High quality values passed through to providers.
- Timer-based polling with progress, cancellation, timeout, and safe terminal states.
- GLB, glTF, OBJ, FBX, and STL download detection and import.
- A dedicated generated collection, hierarchy preservation, and optional centering and
  scale normalization.
- Optional smooth shading and normal recalculation; imported textures are packed into the
  .blend so files stay portable.
- Size-bounded downloads, supported-format and integrity checks, path sanitization, and an
  optional download-host allowlist.
- Local JSON history with re-import and retry; secrets are never written to history.
- API keys stay in the password-typed preference (or an environment variable) and are not
  copied into Scene properties.
- Structured debug logging that redacts API keys and authorization headers.

### Requirements

- Blender 4.5 LTS or newer.
- For real text-to-3D generation, a provider that follows the async job protocol described
  under [Provider configuration](#provider-configuration). The `mock` provider is an
  offline fixture that always returns the packaged cube.
- For real image-to-3D generation, a provider-specific image adapter. The generic REST
  provider is text-only.
- No third-party Python packages are needed by the extension itself.

### Installation

1. Build the extension zip (see [Packaging](#packaging)).
2. In Blender: **Edit → Preferences → Get Extensions**, open the **⌄** menu in the top
   right, choose **Install from Disk**, and select `ai3dgenerator-<version>.zip`.
3. Make sure **AI 3D Object Generator** is enabled.

For development, point Blender at the `ai3dgenerator` folder, which holds the manifest and
`__init__.py`. Increment the version in `ai3dgenerator/blender_manifest.toml` for every
release and keep released zips unchanged.

### First setup

1. Open **Edit → Preferences → Add-ons → AI 3D Object Generator**.
2. Choose `mock` to try the offline path, or `custom_api` for a real service.
3. For `custom_api`, enter the API base URL and endpoint mappings.
4. In the 3D View sidebar, open the **AI 3D** tab and type a prompt in **CREATE**.
5. Click **GENERATE**.
6. Keep **Import Asset** enabled to place the result in `AI3D_Generated`.

Each request carries the current model, quality, format, style, polygon target, topology,
texture, UV, and material settings. Providers may ignore optional parameters; generation
does not fail just because a provider ignores one. Preferences are read at generation
time. The API key is read only into the in-memory request.

### Provider configuration

The `custom_api` provider expects this protocol:

```text
POST {base_url}{generate_path}
GET  {base_url}{status_path}             with {job_id}
POST {base_url}{cancel_path}             with {job_id}   (optional)
GET  the returned asset URL
```

Map the provider's JSON with dotted paths:

| Setting | Example |
|---|---|
| Generate endpoint | `/generate` |
| Status endpoint | `/jobs/{job_id}` |
| Cancel endpoint | `/jobs/{job_id}/cancel` |
| Job ID path | `data.id` |
| Status path | `data.status` |
| Progress path | `data.progress` |
| Output URL path | `data.result.download_url` |
| Error path | `data.error.message` |
| Auth header | `Authorization` |
| Auth prefix | `Bearer ` |

To support a new service natively, subclass `Base3DProvider`, register it in
`ai3dgenerator/providers/registry.py`, and it appears in the same UI.

### Usage

| Panel | Button | What it does |
|---|---|---|
| CREATE | **GENERATE** | Submits the current request |
| CREATE | **CANCEL JOB** | Requests cancellation; the local timer stops on its next tick |
| CREATE | **ENHANCE** | Rewrites the prompt; the original stays in history |
| BATCH | **START BATCH**, **PAUSE**, **RESUME**, **CANCEL BATCH**, **CLEAR QUEUE** | Controls the batch queue |
| HISTORY | **IMPORT LAST ASSET** | Imports the last downloaded file |
| HISTORY | **EXPORT LAST ASSET** | Exports it as GLB, glTF, OBJ, FBX, or STL; overwriting needs an explicit tick |
| HISTORY | **DELETE GENERATED OBJECTS** | Removes only objects created by this extension |
| SETTINGS | **OPEN PREFERENCES** | Opens the add-on preferences |
| SETTINGS | **OPEN ASSET FOLDER** | Opens the cache folder |

The default cache is an `ai3dgenerator` folder in the system temporary directory. Set a
persistent cache folder in the preferences if assets must survive operating-system
cleanup.

### Supported formats

| Format | Support | Importer |
|---|---|---|
| GLB | Yes | `bpy.ops.import_scene.gltf` |
| glTF | Yes | `bpy.ops.import_scene.gltf` |
| OBJ | Yes | `bpy.ops.wm.obj_import` |
| STL | Yes | `bpy.ops.wm.stl_import` |
| FBX | When Blender's FBX importer is enabled | `bpy.ops.import_scene.fbx` |

Only these extensions are accepted, and downloads are checked for integrity before Blender
opens them.

### Architecture

```text
Blender UI / operators / Scene properties / Preferences
        ↓
Generation request + provider capabilities
        ↓
Provider adapter (text or image contract)
        ↓
JobManager (submit → poll → download)
        ↓
DownloadManager + validation + host boundary
        ↓
History + local asset library
        ↓
ImportManager → PostProcessor → AI3D_Generated
        ↓
Batch controller / Blender-native export
```

The core, provider, and network modules are pure Python and testable without Blender.
`importManager.py`, `exportManager.py`, and `postProcessor.py` are the Blender-facing
modules. The timer callback is single-threaded by design so `bpy.data` is never touched
from a worker thread. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### Manual test plan

1. Install a freshly built zip and enable the extension.
2. Confirm the **AI 3D WORKSPACE** panel and its sub-panels appear in the **AI 3D** tab.
3. Run the `mock` provider in text mode with a simple prompt.
4. Check queued, processing, downloading, and completed states, the history entry, the
   generated collection, and the automatic library record.
5. Delete the transient cache and confirm the library copy survives.
6. Select a valid PNG/JPG/JPEG/WEBP, run `mock` in image mode, and check the fixture result.
7. Enhance a prompt, edit it, generate, and confirm the original prompt is kept in history.
8. Start a batch, pause and resume it, cancel it, then retry a failed or cancelled item.
9. Reorder queued jobs, delete a finished job, and clear the finished queue.
10. Try favorites, filtering, rename, re-import, regeneration, and removal in the library.
11. Import and export GLB, glTF, OBJ, and STL; FBX when its importer is enabled.
12. Confirm that an invalid API URL, an unsupported format or provider mode, a malformed
    image, and a truncated GLB are rejected.
13. Check the debug log and history for redacted credentials.
14. Restart Blender and confirm the preferences persist.

### Packaging

Blender's extension command is the source of truth:

```bash
BLENDER=/path/to/blender   # e.g. /Applications/Blender.app/Contents/MacOS/Blender
"$BLENDER" --command extension validate ai3dgenerator
"$BLENDER" --command extension build --source-dir ai3dgenerator --output-dir dist
"$BLENDER" --command extension validate dist/ai3dgenerator-<version>.zip
```

The manifest uses schema version `1.0.0` and requests only the `network` and `files`
permissions the extension needs.

### Troubleshooting

- **Empty sidebar:** enable the extension, confirm Blender is 4.5 or newer, and check the
  system console for registration errors.
- **Mock import fails:** the fixture ships in the package; rebuild it with
  `python ai3dgenerator/fixtures/buildFixture.py` if files were copied incompletely.
- **"Invalid job ID":** map the real response field (for example `data.id`). The extension
  never guesses another field, and it rejects IDs that try path traversal.
- **Output URL rejected:** only absolute HTTP(S) URLs are accepted. Serve local files over a
  local HTTP server or write a provider-specific adapter.
- **FBX import fails:** enable Blender's FBX importer or use GLB/glTF.
- **API key missing:** set it in the preferences or in the configured environment variable;
  logs never contain either value.
- **Cache disappears:** choose a persistent cache folder instead of the temporary folder.

### Security notes

- Remote responses are untrusted and parsed as data only.
- Only `http`/`https` URLs and supported asset extensions are accepted.
- Job IDs and remote filenames are sanitized; output paths stay inside the cache folder.
- Downloads are size-bounded and integrity-checked before import.
- An optional download-host allowlist supports strict egress control.
- No downloaded code is executed and no remote shell command is run.
- API keys, authorization headers, and credential-like fields are redacted from logs and
  omitted from history. The password-typed preference hides the key on screen but is not an
  encrypted keychain; use the environment-variable option to avoid storing it.
- Blender's importers run in-process and are not a sandbox; import only from trusted
  providers or use an isolated Blender process.

### Limitations

- `mock` is an offline fixture that returns the packaged cube; it performs no AI inference.
- The generic REST provider is text-only. Image-to-3D needs a provider-specific adapter.
  (The Blender MCP Bridge ships Tripo and Hyper3D Rodin adapters with image support.)
- `MaterialProvider`, `TextureProvider`, `VariationProvider`, and provider-backed prompt
  enhancement are extension points, not built-in services.
- Thumbnail and optimization services are foundations; automatic thumbnails and production
  LOD/retopology are not part of the default generation path.
- Real network generation depends on your provider and has not been tested against a live
  service in this repository.

## Repository layout

| Path | Contents |
|---|---|
| `blenderMcp/` | Blender MCP Bridge: MCP server, CLI, asset and generation clients, guides |
| `blenderMcp/addon/` | The Blender add-on the MCP server talks to (extension manifest included) |
| `ai3dgenerator/` | The AI 3D Object Generator extension |
| `tests/` | Test suite (pytest; modules are named by area, test functions start with `check`) |
| `checks/` | Scripts that drive a real Blender, such as the Blender UI check |
| `docs/` | Architecture notes for the extension |
| `pyproject.toml` | Packaging for the `blender-mcp-bridge` command |

## Development

```bash
git clone https://github.com/SHAN-code5/blender
cd blender
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e .                 # the Blender MCP Bridge and the mcp SDK, needed by its tests
pytest -q
```

Python 3.10 or newer is required. Without the `mcp` package, the MCP server tests are
skipped; the rest of the suite needs only pytest.

Tests against real Blender:

```bash
pip install "bpy==4.5.*"         # Blender as a Python module; 4.5 and 5.0 need Python 3.11, 5.1+ needs 3.13
pytest -q tests/blenderBridgeAddon.py

# Blender's UI (Linux, with Blender and Xvfb installed):
xvfb-run -a python checks/blenderMcpUi.py --blender blender
```

GitHub Actions runs the main suite; the MCP server tests on Linux, Windows, and macOS with
Python 3.10 and 3.13 and both MCP SDK major versions; the add-on tests on Blender 4.5, 5.0,
and 5.2; and the UI check on Ubuntu's Blender 4.0.

## License

[MIT](LICENSE)
