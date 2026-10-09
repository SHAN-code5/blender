# Changelog

## Unreleased

### Added

- Blender MCP Bridge 0.4.0: lights, cameras, and animation (30 tools).
  - `setup_lighting` places a rig sized to the subject and turned toward the
    scene camera (three-point, studio, outdoor with a gradient sky, dramatic),
    with a matching background that leaves HDRI worlds alone; `set_light`
    creates or changes single lights by power, color or color temperature,
    softness, and aim, optionally tracking an object.
  - `set_camera` frames objects from any side for the scene's aspect ratio, or
    places and aims a camera; lens, depth of field, and render resolution.
  - `insert_keyframes` (location, rotation with full turns kept, scale,
    visibility, light and camera values; bezier, linear, or constant),
    `clear_animation`, `set_timeline`, and `create_turntable` for seamless
    camera-orbit or spinning-object loops.
  - `render_animation` writes MP4 (H.264), GIF, or PNG frames and returns a
    contact sheet of frames; GIFs are encoded in pure Python because Blender
    has no imaging library, and `frame_step` drafts keep real-time playback.
  - Works with Blender 5.0's slotted actions as well as older versions, and a
    new `animation` guide.
- Blender MCP Bridge 0.3.0 (`blenderMcp/`), replacing the single-file add-on
  from the first version: an MCP server and Blender add-on that work with MCP
  SDK 1.x and 2.x, Python 3.10+, Blender 4.2+ as an extension and older
  versions as a legacy add-on, with the UI or headless.
  - 22 tools: scene and object inspection; create, modify, and delete objects;
    materials and modifiers; undo and redo; import and export across glTF,
    FBX, OBJ, STL, PLY, USD, Alembic, and .blend (with target size and
    placement); renders, multi-view renders, and viewport screenshots returned
    as images; Python execution; saving.
  - Asset libraries: search and import Poly Haven HDRIs, PBR texture sets, and
    models, Sketchfab models, and Poly Pizza models. Downloads run in the
    server with size limits, checksum checks, and path-traversal protection,
    are cached, and are sent over the bridge when Blender runs elsewhere.
    Attribution is stored on imported data as `mcp_*` custom properties.
  - AI generation with Tripo, Hyper3D Rodin, any REST service (the AI 3D
    Object Generator's provider code), or an offline mock, from text or a
    reference image. Long jobs continue in the background and are imported
    once through `get_generation_status`.
  - Built-in guides (tool and MCP resources) and a `build_scene` prompt.
  - All tools are async; blocking work runs in threads, and each bridge
    request uses its own connection, so concurrent calls never interfere.
  - Optional token authentication, a Blender-side switch for Python
    execution, and an opt-in safe mode that rejects risky scripts.
  - `blender-mcp-bridge` CLI: `serve` (stdio, SSE, streamable HTTP), `config`
    for Claude Desktop, Claude Code, Cursor, VS Code, and Windsurf,
    `install-addon`, `update`, `build-addon`, `headless`, and `doctor`;
    packaged with `pyproject.toml` for `uvx` and pip.
  - Works around problems found on real installations: viewport screenshots
    are drawn offscreen (window captures are black under Xvfb and some VMs),
    multi-view renders do not need numpy (missing from some distribution
    builds), Cycles previews skip denoising on builds without OpenImageDenoise,
    and GPU render engines are refused in background mode instead of crashing
    Blender.
  - Tests against real Blender 4.5, 5.0, and 5.2 (`tests/blenderBridgeAddon.py`,
    including a full asset and generation chain), a UI check on Blender 4.0
    under Xvfb (`checks/blenderMcpUi.py`), and a CI matrix over Linux,
    Windows, macOS, Python 3.10/3.13, and both MCP SDK versions.

### Changed

- Rewrote the README: it now introduces both tools and corrects the extension's
  panel and button names, importer operators, development setup, and
  packaging commands.
- Renamed the internal `*_phase3` UI and test modules to `*_workspace` and
  removed the remaining "Phase 3" wording from the code, docs, and changelog.
- Stopped tracking the build artifacts under `checks/`; the extension zip is
  produced by the packaging command and is now ignored by git.
- Renamed the test modules under `tests/` after the area they cover (for example
  `tests/storage.py` instead of `tests/test_storage.py`) and added `pytest.ini`
  so the suite is still collected.
- Removed underscores from the package and module names: the extension package
  is now `ai3dgenerator/` and modules use camelCase, such as
  `services/jobManager.py`, `providers/customRest.py`, and
  `ui/operatorsWorkspace.py`.
- Test functions use a `check` prefix instead of `test_`; `pytest.ini` now sets
  `python_functions = check*`.
- **Breaking:** the Blender extension id changed from `ai_3d_generator` to
  `ai3dgenerator`. Remove the previously installed copy before installing this
  build.

### Fixed

- Blender MCP Bridge renders no longer fail on Blender 5.0+ when the scene's
  output is set to video; the output format is switched and restored with its
  media type.
- Export no longer includes unrelated objects the user already had selected; the
  previous selection is restored after the export completes.
- Asset-library metadata is preserved when an asset file is temporarily
  unavailable (for example an unmounted drive) instead of being silently pruned
  by an unrelated write.
- `join_endpoint` preserves a configured base-URL path prefix when an endpoint
  uses a leading slash (such as the default `/generate` mapping).
- Centering a hierarchy no longer moves parented children twice, which distorted
  multi-object imports.
- Downloads whose URL has a non-asset suffix (for example `/download.php`) fall
  back to the response content type or the requested format instead of failing
  before the request.
- Dynamic provider and prompt-template enums use integer defaults so the
  extension registers on Blender 5.x.
- Failed and timed-out jobs keep the progress they actually reached instead of
  showing a full progress bar.
- The generation timer uses the clamped polling interval rather than the raw
  Scene value.
- History is written before best-effort import/library side effects, and
  library-recording errors are caught, so one failure cannot drop the history
  record.
- Deleting a missing batch job now reports an error instead of failing silently.
- Removed unused imports across the package and test suite.

### Fixed (code audit — generation, batch, library)

- Batch jobs use the shared `start_generation` helper directly instead of calling
  the `ai3d.generate` operator from a timer context, where the operator poll
  always failed; queued batch items now actually start and complete.
- Generation callbacks resolve the live `bpy.context` at callback time instead of
  closing over the operator context, which could be freed or point at a
  different scene when the timer fired.
- `remove_tiny_objects` no longer crashes: it dropped a duplicate `.length` call
  on an already-scalar value and touched removed objects afterwards. Tiny meshes
  are now actually removed and remaining passes only see live objects.
- A corrupt, unparseable, or unknown-schema `metadata.json` can no longer be
  silently rewritten to an empty library by the next add/update/remove; the
  mutation is refused and the on-disk file is left untouched.
- Library asset IDs now sanitize provider and job identifiers (colons, slashes,
  spaces) into the allowed `[A-Za-z0-9._-]` alphabet so recording a valid
  provider job cannot silently fail validation.
- `JobManager.start()` resets the previous job's handle, download, and output URL
  state, so a reused manager instance cannot inherit a stale completed job.
- A raising UI update/finish callback no longer flips an already-completed job
  back to failed; callbacks are isolated from the state machine.
- The preferences provider enum now uses an explicit integer default (the mock
  provider), consistent with the scene settings enum.

## 0.2.0 — Workspace

### Added

- Explicit provider capability declarations for generation modes, output
  formats, cancellation, image input, and optional provider features.
- Image-to-3D adapter contract for providers that can actually validate/upload
  a local reference image.
- Collapsible **AI 3D WORKSPACE** sidebar sections: CREATE, ADVANCED, BATCH,
  HISTORY, ASSET LIBRARY, EXPORT/IMPORT, and SETTINGS controls.
- Local prompt templates with explicit original/enhanced prompt preservation.
- Bounded batch queue with pause/resume, cancel, retry, delete, reorder, and
  sequential Blender timer orchestration.
- Atomic local JSON asset library with names, tags, collections, favorites,
  filtering, and generated-file copying.
- Standard-library image validation for PNG/JPG/JPEG/WEBP signatures, size,
  and local-file identity.
- Blender-native export dispatcher with format/extension/overwrite validation.
- Local thumbnail and optimization/LOD foundations.
- Automatic recording of successful generated downloads in the local library.

### Security and reliability

- Authenticated CustomREST downloads are restricted to the configured API host
  or subdomains; cross-host authenticated downloads require an explicit
  provider adapter or download allowlist policy.
- API keys remain in password-typed Add-on Preferences only. Custom headers and
  request payload templates are redacted from diagnostic dictionaries.
- Capability checks require the mode and feature flag to agree.
- Batch completion and failure are idempotent and cannot reprocess completed
  items.
- Thumbnail rendering restores scene camera/render state in `finally`.
- Registration and partial-registration teardown are guarded and rollback-safe.
- History rows carry extended request fields, while malformed or credential-like
  rows are ignored.

## 0.1.0

- Initial provider-independent text-to-3D MVP with MockProvider, configurable
  REST provider, bounded HTTP/download validation, timer-driven job manager,
  format-aware import, post-processing, history, preferences, and packaging.
