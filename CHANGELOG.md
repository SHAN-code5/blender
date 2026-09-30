# Changelog

## Unreleased

### Changed

- Renamed the internal `*_phase3` UI and test modules to `*_workspace` and
  removed the remaining "Phase 3" wording from the code, docs, and changelog.
- Stopped tracking the build artifacts under `checks/`; the extension zip is
  produced by the packaging command and is now ignored by git.
- Renamed the test modules under `tests/` after the area they cover (for example
  `tests/storage.py` instead of `tests/test_storage.py`) and added `pytest.ini`
  so the suite is still collected.

### Fixed

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
