# Phase 3 Changelog

## 0.2.0 — Phase 3 workspace

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
- History rows carry Phase 3 request fields, while malformed or credential-like
  rows are ignored.

## 0.1.0

- Initial provider-independent text-to-3D MVP with MockProvider, configurable
  REST provider, bounded HTTP/download validation, timer-driven job manager,
  format-aware import, post-processing, history, preferences, and packaging.
