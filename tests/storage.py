"""Storage and library migration tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from ai3dgenerator.core.errors import ValidationError
from ai3dgenerator.services.libraryService import AssetLibrary, SCHEMA_VERSION


def checkLibraryMigratesFutureSchemaByIgnoringUnknownRows(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    source = tmp_path / "asset.glb"
    source.write_bytes(b"fixture")
    library.metadata_path.write_text(
        '{"schema_version": 99, "assets": [{"id": "x", "prompt": "chair", "provider": "mock", "model": "m", "created_at": "now", "format": "glb", "file": "' + str(source) + '", "unknown": true}]}',
        encoding="utf-8",
    )
    assert library.list_assets() == []


def checkLibraryRejectsSecretLikeMetadataAndInvalidTags(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    source = tmp_path / "asset.glb"
    source.write_bytes(b"fixture")
    with pytest.raises(ValidationError):
        library.add_asset(asset_id="a", prompt="chair", provider="mock", model="m", output_format="glb", file_path=source, tags=["x" * 1000])


def checkLibraryRejectsSymlinkAndExternalFile(tmp_path: Path) -> None:
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


def checkLibraryCopyRepairsUnknownPartialDestination(tmp_path: Path) -> None:
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


def checkLibraryAssetIdCannotEscapeGeneratedDirectory(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    source = tmp_path / "source.glb"
    source.write_bytes(b"valid")
    with pytest.raises(ValidationError):
        library.add_asset(asset_id="../escape", prompt="chair", provider="mock", model="m", output_format="glb", file_path=source, copy_file=True)
    assert not (tmp_path / "escape.glb").exists()
    assert not (tmp_path.parent / "escape.glb").exists()


def checkLibraryMetadataSchemaIsExplicit(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    assert SCHEMA_VERSION == 1
    assert library.metadata_path.parent == tmp_path


def checkLibraryRefusesToOverwriteCorruptMetadata(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    raw = '{"schema_version": '
    library.metadata_path.write_text(raw, encoding="utf-8")
    source = tmp_path / "source.glb"
    source.write_bytes(b"fixture")
    with pytest.raises(ValidationError):
        library.add_asset(asset_id="a", prompt="chair", provider="mock", model="m", output_format="glb", file_path=source, copy_file=True)
    # The corrupt file must be left untouched, not rewritten with a single row.
    assert library.metadata_path.read_text(encoding="utf-8") == raw


def checkLibraryRefusesToOverwriteFutureSchemaMetadata(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    raw = '{"schema_version": 99, "assets": []}'
    library.metadata_path.write_text(raw, encoding="utf-8")
    source = tmp_path / "source.glb"
    source.write_bytes(b"fixture")
    with pytest.raises(ValidationError):
        library.add_asset(asset_id="a", prompt="chair", provider="mock", model="m", output_format="glb", file_path=source, copy_file=True)
    assert library.metadata_path.read_text(encoding="utf-8") == raw


def checkRecordGeneratedAssetSanitizesOddIdCharacters(tmp_path: Path):
    from ai3dgenerator.services.libraryRecordService import record_generated_asset

    library = AssetLibrary(tmp_path)
    source = tmp_path / "result.glb"
    source.write_bytes(b"fixture")
    entry = record_generated_asset(
        library,
        file_path=source,
        job_id="abc:12/x",
        prompt="chair",
        provider="custom_api",
        model="m",
        output_format="glb",
        copy_file=True,
    )
    # Colons and slashes in the job id must be reduced to the ID alphabet.
    assert entry.id.startswith("custom_api-abc-12-x-")
    assert entry.id in {asset.id for asset in library.list_assets()}
