"""Phase 3 storage and library migration tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from ai_3d_generator.core.errors import ValidationError
from ai_3d_generator.services.library_service import AssetLibrary, SCHEMA_VERSION


def test_library_migrates_future_schema_by_ignoring_unknown_rows(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    source = tmp_path / "asset.glb"
    source.write_bytes(b"fixture")
    library.metadata_path.write_text(
        '{"schema_version": 99, "assets": [{"id": "x", "prompt": "chair", "provider": "mock", "model": "m", "created_at": "now", "format": "glb", "file": "' + str(source) + '", "unknown": true}]}',
        encoding="utf-8",
    )
    assert library.list_assets() == []


def test_library_rejects_secret_like_metadata_and_invalid_tags(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    source = tmp_path / "asset.glb"
    source.write_bytes(b"fixture")
    with pytest.raises(ValidationError):
        library.add_asset(asset_id="a", prompt="chair", provider="mock", model="m", output_format="glb", file_path=source, tags=["x" * 1000])


def test_library_rejects_symlink_and_external_file(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    external = tmp_path / "external.glb"
    external.write_bytes(b"external")
    with pytest.raises(ValidationError):
        library.add_asset(asset_id="external", prompt="chair", provider="mock", model="m", output_format="glb", file_path=external, copy_file=False)

    stored = library.assets_dir / "stored.glb"
    stored.write_bytes(b"stored")
    library.add_asset(asset_id="stored", prompt="chair", provider="mock", model="m", output_format="glb", file_path=stored, copy_file=False)
    try:
        link = library.assets_dir / "link.glb"
        link.symlink_to(stored)
    except (OSError, NotImplementedError):
        return
    with pytest.raises(ValidationError):
        library.resolve_asset_path(str(link))


def test_library_copy_repairs_unknown_partial_destination(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    source = tmp_path / "source.glb"
    source.write_bytes(b"valid")
    destination = library.assets_dir / "same-id.glb"
    destination.write_bytes(b"partial")
    with pytest.raises(ValidationError):
        library.add_asset(asset_id="same-id", prompt="chair", provider="mock", model="m", output_format="glb", file_path=source, copy_file=True)
    assert destination.read_bytes() == b"partial"
    destination.unlink()
    entry = library.add_asset(asset_id="same-id", prompt="chair", provider="mock", model="m", output_format="glb", file_path=source, copy_file=True)
    assert entry.file == str(destination)
    assert destination.read_bytes() == b"valid"


def test_library_asset_id_cannot_escape_generated_directory(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    source = tmp_path / "source.glb"
    source.write_bytes(b"valid")
    with pytest.raises(ValidationError):
        library.add_asset(asset_id="../escape", prompt="chair", provider="mock", model="m", output_format="glb", file_path=source, copy_file=True)
    assert not (tmp_path / "escape.glb").exists()
    assert not (tmp_path.parent / "escape.glb").exists()


def test_library_metadata_schema_is_explicit(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    assert SCHEMA_VERSION == 1
    assert library.metadata_path.parent == tmp_path
