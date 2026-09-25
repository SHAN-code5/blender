# AI 3D Object Generator

A production-oriented Blender 4.5+ extension for creating, organizing, and
importing 3D assets from text prompts or validated local reference images. The
project is provider-independent: the UI, job manager, downloader, importer,
post-processing, library, and export layers do not depend on a specific vendor.

## 1. Product overview

The extension accepts a prompt such as:

> A realistic wooden Indian village chair with carved legs

It validates the request, submits it to a configured provider, polls a
long-running job, downloads a bounded local asset, validates its format, and
optionally imports it into the dedicated `AI3D_Generated` collection.

The Phase 3 workspace provides:

- **CREATE** — text-to-3D and image-to-3D modes, provider/model selection,
  prompt templates, reference-image validation and preview, generation status,
  and cancellation.
- **ADVANCED** — topology, polygon target, texture resolution, UV, material,
  texture, smoothing, placement, and import options.
- **HISTORY** — bounded local generation records with import, retry, delete,
  clear, and generated-object cleanup controls.
- **ASSET LIBRARY** — persistent local asset records with names, tags,
  collections, favorites, filtering, re-import, regeneration, rename, and
  metadata removal.
- **BATCH** — sequential timer-driven batch generation with pause, resume,
  cancel, retry, deletion, and reordering.
- **EXPORT** — Blender-native GLB/glTF/OBJ/FBX/STL export with explicit
  overwrite control.
- **SETTINGS** — non-secret workspace settings plus a link to Add-on
  Preferences for provider configuration.

Successful generated downloads are copied into the configured library's
`generated/` directory and recorded automatically. The local library can be
relocated from the ASSET LIBRARY panel.

## 2. Features

- 3D View sidebar panel with Generate, Status, Quality, Advanced, Provider, and History sections.
- Dynamic provider registry with `custom_api`, `local_api`, and offline `mock` adapters.
- Configurable REST endpoints, authentication, request payload, and dotted response paths.
- Draft, Standard, and High quality values passed through to providers.
- Timer-based polling with progress, cancellation, timeout, and safe terminal states.
- GLB, glTF, OBJ, FBX, and STL download detection/import adapters.
- Dedicated generated collection, hierarchy preservation, optional centering and scale normalization.
- Optional smooth shading, normals, and portable packed textures.
- Bounded downloads, supported-format checks, path sanitization, and host allowlisting support.
- Local JSON history with re-import, retry, clear, and no secret storage.
- Preference values are used at generation time; API keys stay in the password-typed preferences field and are not copied into Scene properties.
- Mock provider with a packaged GLB fixture for offline UI testing.
- Structured debug logging with API key and authorization redaction.

## 3. Requirements

- Blender 4.5 LTS or newer.
- A configured provider implementing the documented async job protocol for real
  text-to-3D generation. `MockProvider` is an offline fixture and does not infer
  geometry from prompts.
- A provider-specific image adapter for real image-to-3D generation. The bundled
  Mock image path only returns the packaged cube.
- Python 3.9+ for the headless core test suite.
- No third-party Python runtime dependency is required by the extension itself.

## 4. Installation

### Developer installation

1. Open Blender's Extensions preferences.
2. Enable **Install from Disk**.
3. Select `checks/ai_3d_generator-0.2.0.zip` after the Phase 3 release build.
4. Enable **AI 3D Object Generator**.

For source development, use Blender's development add-on workflow and point it
at the `ai_3d_generator` package directory, which contains the manifest and
`__init__.py`.

### Production installation

Build the zip with the command below, then use **Install from Disk** in
Blender. Keep the zip immutable after release; increment the semantic version
in `ai_3d_generator/blender_manifest.toml` for every release.

## 5. First setup

1. Open **Edit → Preferences → Add-ons → AI 3D Object Generator**.
2. Select `mock` to verify the offline path, or `custom_api` for a real service.
3. Configure the API base URL and endpoint mappings.
4. Enter a prompt in the 3D View sidebar under **AI 3D**.
5. Press **GENERATE 3D**.
6. Enable **Import Asset** to place the result in `AI3D_Generated`.

The provider request uses the current model, quality, format, style, polygon
target, topology, texture, UV, material, and texture toggles. Providers may
ignore optional parameters; the extension does not fail solely because an
optional field is unsupported. Preferences are read at generation time, while
the user's current non-secret Scene controls remain authoritative. The
password-typed API key is read only into the in-memory provider request and is
not copied into Scene properties.

## 6. Provider configuration

The `custom_api` adapter expects this conceptual protocol:

```text
POST {base_url}{generate_path}
GET  {base_url}{status_path} with {job_id}
POST {base_url}{cancel_path} with {job_id}   (optional)
GET  the returned asset URL
```

Configure dotted mappings such as:

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

A real provider adapter should be added as a new subclass of
`Base3DProvider`, registered in `providers/registry.py`, and exposed through the
same UI. The existing generic adapter intentionally makes no vendor assumptions.

## 7. Usage

- **GENERATE 3D** submits the current request.
- **CANCEL** requests cancellation and stops the local timer on the next callback.
- **IMPORT LAST ASSET** imports the downloaded file.
- **DELETE GENERATED OBJECTS** removes only objects tagged by this extension.
- **SETTINGS** opens the extension preferences.
- **OPEN ASSET FOLDER** opens the local cache directory.

The default cache is a user-local `ai_3d_generator` directory in the system
temporary directory. Set a persistent cache directory in Preferences if assets
must survive operating-system cleanup.

## 8. Supported formats

| Format | First-class status | Import path |
|---|---|---|
| GLB | Yes | `bpy.ops.wm.gltf_import` or compatible installed importer |
| glTF | Yes | Blender glTF importer |
| OBJ | Yes | `bpy.ops.wm.obj_import` |
| STL | Yes | `bpy.ops.wm.stl_import` |
| FBX | Conditional | `bpy.ops.import_scene.fbx` when the importer is available |

Only explicitly supported extensions are accepted. The downloader performs
cheap integrity checks before invoking Blender.

## 9. Architecture

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

Core/provider/network modules are pure Python and testable without Blender.
`import_manager.py` and `post_processor.py` are isolated Blender-facing modules.
The timer callback is single-threaded by design: it avoids touching `bpy.data`
from a worker thread. For very slow network calls, reduce the request timeout
or move the transport to a future thread/queue architecture with strict main-thread
handoff.

## 10. Development

```bash
cd ~/ai_3d_generator_extension
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
```

The test suite does not require Blender and uses a packaged GLB fixture for the
mock download path.

## 11. Testing

Automated tests cover:

- URL, job ID, path, image, and asset-format validation.
- Provider capability agreement and provider-independent mode routing.
- Mock queued/processing/completed/failed/cancelled transitions.
- History serialization, Phase 3 field preservation, and secret omission.
- Download allowlisting, authenticated host boundaries, format detection, and
  size/integrity checks.
- Job state transitions, batch idempotence, and terminal callbacks.
- Library migration, bounded tags, atomic persistence, favorites, and copied
  generated files.
- Export target, overwrite, and Blender-native export dispatch seams.

Manual Blender test plan:

1. Install the fresh Phase 3 zip and enable the extension.
2. Confirm the **AI 3D WORKSPACE** sidebar and all collapsible sections appear.
3. Run the Mock Provider in text mode with a simple prompt.
4. Verify queued, processing, downloading, completed, history, generated
   collection, and automatic library recording.
5. Verify the generated library file survives deletion of the transient cache.
6. Select a valid PNG/JPG/JPEG/WEBP, run Mock image mode, and verify the
   fixture result; the mock does not infer geometry from the image.
7. Enhance a prompt, edit the enhanced text, generate, and verify the original
   prompt remains traceable in history.
8. Start a batch, pause/resume, cancel it, then retry a failed/cancelled item.
9. Reorder queued jobs, delete a finished job, and clear the finished queue.
10. Test favorites, filtering, rename, re-import, regeneration, and metadata
    removal in the Asset Library.
11. Test GLB, glTF, OBJ, and STL import/export; test FBX when available.
12. Reject a deliberately invalid API URL, unsupported output format,
    unsupported provider mode, malformed image, and truncated GLB.
13. Inspect the debug log and history for redacted credentials.
14. Restart Blender and verify Add-on Preferences persist.

## 12. Packaging

Blender's extension command is the source of truth:

```bash
BLENDER=/Applications/Blender.app/Contents/MacOS/Blender
"$BLENDER" --command extension validate ai_3d_generator
"$BLENDER" --command extension build \
  --source-dir ai_3d_generator \
  --output-dir checks \
  --verbose
```

Validate the resulting archive as well:

```bash
"$BLENDER" --command extension validate checks/ai_3d_generator-0.2.0.zip
```

The manifest uses Blender's documented schema version `1.0.0` and requests only
`network` and `files` permissions because the extension performs those actions.

## 13. Troubleshooting

- **Blank sidebar:** enable the extension and confirm Blender 4.5+; inspect the
  Blender console for registration errors.
- **Mock fails to import:** the fixture is included in the package; rebuild it
  with `.venv/bin/python ai_3d_generator/fixtures/build_fixture.py` if source
  files were copied incompletely.
- **Provider says invalid job ID:** map the actual response field, for example
  `data.id`; the extension rejects path traversal rather than guessing.
- **Output URL rejected:** only absolute HTTP(S) URLs are accepted. Local file
  paths should be served by a local HTTP provider or downloaded by a provider-specific adapter.
- **FBX import fails:** enable the FBX importer or use GLB/GLTF.
- **API key missing:** set the key in Preferences or set the configured
  environment-variable name; logs never contain either value.
- **Cache disappears:** choose a persistent cache directory instead of the
  system temporary directory.

## 14. Security notes

- Remote responses are untrusted and parsed as data only.
- Only `http`/`https` URLs and supported asset extensions are accepted.
- Job IDs and remote filenames are sanitized; output paths are confined to the
  configured cache root.
- Downloads are size-bounded and integrity-checked before Blender import.
- Optional download-host allowlisting is available for deployments requiring
  strict egress control.
- No downloaded Python code is executed and no remote shell command is run.
- API keys, authorization headers, and credential-like fields are redacted from
  structured logs and omitted from history. Blender's password subtype masks the
  value in the UI but does not provide OS keychain encryption; use the environment
  variable option when a deployment requires avoiding credential persistence.
- Blender importers run in-process and are not a sandbox for malicious files;
  use only trusted providers or run untrusted imports in an isolated Blender
  process.

## 15. Release limitations

- `MockProvider` is an offline pipeline fixture returning the packaged cube; it
  performs no AI inference.
- `CustomRESTProvider` is provider-dependent and text-only. Image-to-3D remains
  unavailable until a real multipart/binary upload adapter is implemented.
- `MaterialProvider`, `TextureProvider`, `VariationProvider`, and provider-backed
  prompt enhancement are replaceable interfaces/architecture, not built-in
  commercial services.
- Thumbnail and optimization services are local foundations; automatic
  thumbnail generation and full production LOD/retopology are not integrated
  into the default generation path.
- Network generation is provider-dependent and was not live-tested in this
  workspace because no external provider credentials/contract were supplied.
- Remote files and images are untrusted; Blender importers run in-process and
  are not a security sandbox.
- Password-typed preferences mask display only; they are not an encrypted
  keychain.
