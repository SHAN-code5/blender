"""Phase 3 pure-Python foundation tests (RED phase)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ai_3d_generator.core.errors import ValidationError
from ai_3d_generator.core.capabilities import ProviderCapabilities
from ai_3d_generator.providers.base import Base3DProvider
from ai_3d_generator.providers.image_to_3d import ImageTo3DProvider
from ai_3d_generator.services.batch_service import BatchQueue
from ai_3d_generator.services.library_service import AssetLibrary
from ai_3d_generator.services.prompt_service import PromptTemplates, PromptEnhancer
from ai_3d_generator.services.export_service import validate_export_target
from ai_3d_generator.utils.hashing import content_hash
from ai_3d_generator.utils.images import validate_image_path


def test_provider_capabilities_are_explicit_and_serializable():
    capabilities = ProviderCapabilities(
        provider_id="mock",
        name="Mock Provider",
        supported_generation_modes=("text_to_3d",),
        supported_formats=("glb",),
        supports_text_to_3d=True,
    )
    assert capabilities.supports("text_to_3d")
    assert not capabilities.supports("image_to_3d")
    assert "api_key" not in capabilities.to_dict()


def test_image_path_accepts_supported_image_and_rejects_script(tmp_path: Path):
    png = tmp_path / "reference.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert validate_image_path(png) == str(png.resolve())
    bad = tmp_path / "reference.py"
    bad.write_text("print('unsafe')", encoding="utf-8")
    with pytest.raises(ValidationError):
        validate_image_path(bad)


def test_image_provider_does_not_claim_unsupported_capability():
    class Broken(ImageTo3DProvider):
        def create_generation_job(self, request):
            raise NotImplementedError

        def get_job_status(self, job_id, elapsed_seconds=0.0):
            raise NotImplementedError

    assert Broken().capabilities().supports_image_to_3d is False


def test_library_metadata_is_bounded_and_credential_free(tmp_path: Path):
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


def test_library_rejects_metadata_secret_fields(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    library.metadata_path.write_text(
        '{"schema_version": 1, "assets": [{"id":"a","prompt":"chair","provider":"mock","model":"m","created_at":"now","format":"glb","file":"chair.glb","thumbnail":"","tags":[],"favorite":false,"api_key":"secret","collection":""}]}',
        encoding="utf-8",
    )
    assert library.list_assets() == []


def test_library_copy_file_keeps_metadata_asset_after_source_moves(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    source = tmp_path / "source.glb"
    source.write_bytes(b"glb")
    entry = library.add_asset(asset_id="copy", prompt="chair", provider="mock", model="default", output_format="glb", file_path=source, copy_file=True)
    source.unlink()
    assert Path(entry.file).is_file()
    assert Path(entry.file).parent == library.assets_dir


def test_content_hash_is_stable_and_content_based(tmp_path: Path):
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"same bytes")
    second.write_bytes(b"same bytes")
    third = tmp_path / "third.bin"
    third.write_bytes(b"other bytes")
    assert content_hash(first) == content_hash(second)
    assert content_hash(first) != content_hash(third)


def test_batch_queue_is_pause_resume_cancel_and_retry_safe():
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


def test_batch_queue_does_not_repeat_queued_or_completed_jobs():
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


def test_batch_queue_reorders_and_deletes_only_non_running_jobs():
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


def test_prompt_templates_and_enhancer_are_explicit():
    templates = PromptTemplates.defaults()
    assert "realistic_prop" in templates
    result = PromptEnhancer(template_id="realistic_prop").enhance("make a tractor")
    assert result.original == "make a tractor"
    assert "tractor" in result.enhanced.lower()
    assert result.template_id == "realistic_prop"


def test_export_validation_refuses_overwrite_and_unsupported_format(tmp_path: Path):
    target = tmp_path / "asset.glb"
    with pytest.raises(ValidationError):
        validate_export_target(tmp_path / "asset.xyz", "xyz")
    target.write_bytes(b"existing")
    with pytest.raises(ValidationError):
        validate_export_target(target, "glb", overwrite=False)
    assert validate_export_target(target, "glb", overwrite=True) == target.resolve()
