# AI 3D Object Generator — development notes

The package source directory is `ai_3d_generator/`, not the repository root.
The manifest and `__init__.py` intentionally live in the same directory, as
required by Blender's extension package builder.

## Phase 3 architecture

```text
Blender UI / Scene properties / Add-on Preferences
        ↓
legacy coordinator (ui/operators.py)
        ↓
explicit provider capabilities + image adapter contract
        ↓
JobManager (submit → poll → download; bounded timer state machine)
        ↓
DownloadManager (allowlist, host-boundary, size/integrity checks)
        ↓
history + local asset library recording
        ↓
Blender importer → post-processing → AI3D_Generated collection
        ↓
batch controller / Blender-native export
```

The core, provider, utility, and non-Blender service modules do not import
`bpy`. Blender-only boundaries are isolated in the UI, import, post-processing,
export, thumbnail, and optimization modules. Worker code never touches
`bpy.data` or Blender operators; the timer callback remains on the main thread.

### Phase 3 additions

- `core/capabilities.py` is the source of truth for supported modes and formats.
  Mode checks require both the declared mode and its feature flag.
- `services/library_service.py` provides bounded atomic JSON metadata storage.
  Generated assets can be copied into the library's `generated/` directory so
  deleting a transient cache does not orphan a library record.
- `services/library_record_service.py` is called by the generation completion
  callback after a successful download.
- `services/batch_service.py` is a replaceable pure-Python queue; the Blender
  operator layer supplies sequential timer orchestration and user controls.
- Prompt enhancement is a local template tool. It never calls a model and never
  claims to generate 3D geometry.
- `providers/image_to_3d.py` is an adapter boundary. `MockProvider` implements
  it only as a deterministic offline fixture; `CustomRESTProvider` remains
  text-only until a real binary/multipart upload contract exists.
- `services/thumbnail_service.py` and `services/optimization_service.py` are
  local foundations. They are not automatically invoked by generation.

## Security boundaries

- API keys stay in `AddonPreferences` password fields and are read into runtime
  configuration only. They are not Scene properties, request dictionaries,
  history rows, or library metadata.
- Custom headers and request payload templates are redacted from diagnostic
  dictionaries; log output recursively redacts credential-like keys and URL
  credentials.
- Authenticated CustomREST downloads are restricted to the configured API host
  or its subdomains. Configure an explicit download allowlist for CDN hosts;
  arbitrary cross-host authenticated downloads are rejected.
- Remote images and 3D files are untrusted. File extensions, signatures, size,
  host, path containment, and cheap format checks run before Blender importers.
- Blender importers are in-process trust boundaries, not security sandboxes.

## Testing and release

The pure-Python suite is run without Blender. Release verification must be
performed after the final edit in this order:

```bash
python3 -m compileall -q ai_3d_generator tests
.venv/bin/pytest -q
blender --command extension validate ai_3d_generator
blender --command extension build --source-dir ai_3d_generator --output-dir checks --verbose
blender --command extension validate checks/ai_3d_generator-<version>.zip
blender --factory-startup -b --python checks/registration_probe.py
blender --factory-startup -b --python checks/registration_probe_twice.py
```

The checked-in historical `checks/ai_3d_generator-0.1.0.zip` is not a fresh
Phase 3 artifact and must not be used as release evidence.
