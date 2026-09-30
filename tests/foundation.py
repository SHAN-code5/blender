"""Pure-Python foundation tests for providers, services, and validation."""
from __future__ import annotations

from pathlib import Path

import pytest

from ai3dgenerator.core.errors import ValidationError
from ai3dgenerator.core.capabilities import ProviderCapabilities
from ai3dgenerator.providers.imageTo3d import ImageTo3DProvider
from ai3dgenerator.services.batchService import BatchQueue
from ai3dgenerator.services.libraryService import AssetLibrary
from ai3dgenerator.services.promptService import PromptTemplates, PromptEnhancer
from ai3dgenerator.services.exportService import validate_export_target
from ai3dgenerator.utils.hashing import content_hash
from ai3dgenerator.utils.images import validate_image_path


def checkProviderCapabilitiesAreExplicitAndSerializable():
    capabilities = ProviderCapabilities(
        provider_id="mock",
        name="Mock Provider",
        supported_generation_modes=("text_to_3d",),
        supported_formats=("glb",),
        supports_text_to_3d=True,
    )
    assert capabilities.supports("text_to_3d")
    assert not capabilities.supports("imageTo3d")
    assert "api_key" not in capabilities.to_dict()


def checkImagePathAcceptsSupportedImageAndRejectsScript(tmp_path: Path):
    png = tmp_path / "reference.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert validate_image_path(png) == str(png.resolve())
    bad = tmp_path / "reference.py"
    bad.write_text("print('unsafe')", encoding="utf-8")
    with pytest.raises(ValidationError):
        validate_image_path(bad)


def checkImageProviderDoesNotClaimUnsupportedCapability():
    class Broken(ImageTo3DProvider):
        def create_generation_job(self, request):
            raise NotImplementedError

        def get_job_status(self, job_id, elapsed_seconds=0.0):
            raise NotImplementedError

    assert Broken().capabilities().supports_imageTo3d is False


def checkLibraryMetadataIsBoundedAndCredentialFree(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    (tmp_path / "chair.glb").write_bytes(b"fixture")
    entry = library.add_asset(
        asset_id="asset-1",
        prompt="chair",
        provider="mock",
        model="default",
        output_format="glb",
        file_path=tmp_path / "chair.glb",
        copy_file=True,
        thumbnail_path="",
        tags=["chair", "prop"],
        favorite=True,
    )
    assert entry.id == "asset-1"
    assert entry.name == "chair"
    assert "api_key" not in library.metadata_path.read_text(encoding="utf-8")
    loaded = library.list_assets()
    assert loaded[0].favorite is True
    assert loaded[0].tags == ["chair", "prop"]


def checkLibraryRejectsMetadataSecretFields(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    library.metadata_path.write_text(
        '{"schema_version": 1, "assets": [{"id":"a","prompt":"chair","provider":"mock","model":"m","created_at":"now","format":"glb","file":"chair.glb","thumbnail":"","tags":[],"favorite":false,"api_key":"secret","collection":""}]}',
        encoding="utf-8",
    )
    assert library.list_assets() == []


def checkLibraryCopyFileKeepsMetadataAssetAfterSourceMoves(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    source = tmp_path / "source.glb"
    source.write_bytes(b"glb")
    entry = library.add_asset(asset_id="copy", prompt="chair", provider="mock", model="default", output_format="glb", file_path=source, copy_file=True)
    source.unlink()
    assert Path(entry.file).is_file()
    assert Path(entry.file).parent == library.assets_dir


def checkContentHashIsStableAndContentBased(tmp_path: Path):
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"same bytes")
    second.write_bytes(b"same bytes")
    third = tmp_path / "third.bin"
    third.write_bytes(b"other bytes")
    assert content_hash(first) == content_hash(second)
    assert content_hash(first) != content_hash(third)


def checkBatchQueueIsPauseResumeCancelAndRetrySafe():
    queue = BatchQueue(max_workers=2)
    queue.enqueue("a", "chair")
    queue.enqueue("b", "table")
    queue.enqueue("c", "pot")
    queue.pause()
    assert queue.claim_next() is None
    queue.resume()
    assert queue.claim_next() == "a"
    assert queue.claim_next() == "b"
    queue.cancel("a")
    assert queue.claim_next() == "c"
    queue.complete("c")
    queue.retry("a")
    assert queue.claim_next() == "a"


def checkBatchQueueDoesNotRepeatQueuedOrCompletedJobs():
    queue = BatchQueue(max_workers=1)
    queue.enqueue("a", "chair")
    assert queue.claim_next() == "a"
    queue.complete("a")
    assert queue.claim_next() is None
    queue.complete("a")
    assert queue.snapshot()[0].state == "completed"
    with pytest.raises(ValidationError):
        queue.fail("a", "late error")
    with pytest.raises(ValidationError):
        queue.retry("a")
    assert queue.pending_job_ids() == []


def checkBatchQueueReordersAndDeletesOnlyNonRunningJobs():
    queue = BatchQueue(max_workers=1)
    for key, prompt in (("a", "chair"), ("b", "table"), ("c", "pot")):
        queue.enqueue(key, prompt)
    assert queue.pending_job_ids() == ["a", "b", "c"]
    queue.reorder("c", 0)
    assert queue.pending_job_ids() == ["c", "a", "b"]
    assert queue.remove("a") is True
    assert queue.remove("a") is False
    assert queue.pending_job_ids() == ["c", "b"]
    assert queue.claim_next() == "c"
    with pytest.raises(ValidationError):
        queue.remove("c")


def checkPromptTemplatesAndEnhancerAreExplicit():
    templates = PromptTemplates.defaults()
    assert "realistic_prop" in templates
    result = PromptEnhancer(template_id="realistic_prop").enhance("make a tractor")
    assert result.original == "make a tractor"
    assert "tractor" in result.enhanced.lower()
    assert result.template_id == "realistic_prop"


def checkExportValidationRefusesOverwriteAndUnsupportedFormat(tmp_path: Path):
    target = tmp_path / "asset.glb"
    with pytest.raises(ValidationError):
        validate_export_target(tmp_path / "asset.xyz", "xyz")
    target.write_bytes(b"existing")
    with pytest.raises(ValidationError):
        validate_export_target(target, "glb", overwrite=False)
    assert validate_export_target(target, "glb", overwrite=True) == target.resolve()
