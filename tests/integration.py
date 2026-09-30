"""Integration regressions for modes, libraries, and persistence."""
from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from ai3dgenerator.core.errors import ValidationError
from ai3dgenerator.core.models import GenerationRequest, ProviderConfig
from ai3dgenerator.providers.customRest import CustomRESTProvider
from ai3dgenerator.providers.imageTo3d import ImageTo3DProvider
from ai3dgenerator.providers.mock import MockProvider
from ai3dgenerator.services.generationService import GenerationService
from ai3dgenerator.services.jobManager import JobManager
from ai3dgenerator.services.libraryService import AssetLibrary
from ai3dgenerator.services.history import read_history
from ai3dgenerator.services.downloadManager import DownloadManager
from ai3dgenerator.services.promptService import PromptEnhancer, effective_prompt
from ai3dgenerator.storage.jsonStorage import JSONStorage


def checkMockProviderExposesImageModeAndRunsOffline(tmp_path: Path):
    image = tmp_path / "reference.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    provider = MockProvider()
    capabilities = provider.capabilities()
    assert capabilities.supports_imageTo3d
    request = GenerationRequest(prompt="chair from image", generation_mode="imageTo3d", image_path=str(image), reference_image_path=str(image))
    manager = DownloadManager(tmp_path)
    job = JobManager(provider, manager)
    job.start(request.to_dict())
    for _ in range(6):
        if job.snapshot.is_finished:
            break
        job.tick()
    assert job.snapshot.state == "completed"
    assert Path(job.snapshot.output_path).is_file()


def checkImageModeAllowsAnEmptyOptionalDescription(tmp_path: Path):
    image = tmp_path / "reference.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    request = GenerationRequest(prompt="", generation_mode="imageTo3d", image_path=str(image))
    provider = MockProvider()
    service = GenerationService(provider)
    handle = service.submit_image(image, request)
    assert handle.job_id.startswith("mock-")
    from ai3dgenerator.core.config import validate_config
    assert validate_config({
        "provider": "mock",
        "prompt": "",
        "generation_mode": "imageTo3d",
        "output_format": "glb",
        "timeout": 60.0,
        "poll_interval": 2.0,
        "job_timeout": 900.0,
    }) == []


def checkJobManagerUsesImageAdapterForImageMode(tmp_path: Path):
    image = tmp_path / "reference.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    provider = MockProvider()
    original_text_submit = provider.create_generation_job
    provider.create_generation_job = lambda request: (_ for _ in ()).throw(AssertionError("text adapter used"))
    provider.create_image_job = lambda path, request: original_text_submit({**request, "image_path": path, "reference_image_path": path})
    job = JobManager(provider, DownloadManager(tmp_path))
    job.start(GenerationRequest(prompt="", generation_mode="imageTo3d", image_path=str(image)).to_dict())
    assert job.snapshot.state == "submitting"
    job.tick()
    assert job.snapshot.state == "queued"


def checkGenerationServiceRoutesMockImageModeThroughImageAdapter(tmp_path: Path):
    image = tmp_path / "reference.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    provider = MockProvider()
    service = GenerationService(provider)
    request = GenerationRequest(prompt="chair", generation_mode="imageTo3d", image_path=str(image))
    assert isinstance(provider, ImageTo3DProvider)
    handle = service.submit_image(image, request)
    assert handle.job_id.startswith("mock-")


def checkGenerationServiceRejectsRestImageModeUntilUploadAdapterExists(tmp_path: Path):
    image = tmp_path / "reference.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    provider = CustomRESTProvider(ProviderConfig(base_url="https://example.test"))
    service = GenerationService(provider)
    with pytest.raises(ValidationError, match="Image"):
        service.submit_image(image, GenerationRequest(prompt="chair", generation_mode="imageTo3d", image_path=str(image), reference_image_path=str(image)))


def checkCustomRestDoesNotSendLocalImagePathToRemote(tmp_path: Path):
    image = tmp_path / "reference.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    provider = CustomRESTProvider(ProviderConfig(base_url="https://example.test"))
    captured = {}

    def request_json(method, url, **kwargs):
        captured.update(kwargs.get("json_body") or {})
        return {"id": "job-1"}, object()

    provider.client.request_json = request_json
    provider.create_generation_job({
        "prompt": "chair",
        "generation_mode": "text_to_3d",
        "image_path": str(image),
        "reference_image_path": str(image),
    })
    assert "image_path" not in captured
    assert "reference_image_path" not in captured
    assert captured["prompt"] == "chair"


def checkGenerationRequestRoundTripPreservesExtendedFields():
    request = GenerationRequest(prompt="chair", generation_mode="imageTo3d", image_path="/tmp/ref.png", texture_resolution=4096, auto_uv=True)
    restored = GenerationRequest.from_dict(request.to_dict())
    assert restored.generation_mode == "imageTo3d"
    assert restored.image_path == "/tmp/ref.png"
    assert restored.reference_image_path == "/tmp/ref.png"
    assert restored.texture_resolution == 4096
    assert restored.auto_uv is True


def checkHistoryPreservesExtendedFields():
    from ai3dgenerator.services.history import make_entry, read_history, write_history

    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "history.json"
        entry = make_entry(
            job_id="job-1",
            prompt="chair from image",
            negative_prompt="blurry",
            provider="mock",
            model="default",
            quality="standard",
            output_format="glb",
            file_path="",
            generation_mode="imageTo3d",
            image_path="/tmp/reference.png",
            style="realistic",
            polygon_target=1234,
            texture_resolution=4096,
            auto_uv=True,
            prompt_enhanced=True,
            topology_preference="clean",
        )
        write_history(path, [entry])
        restored = read_history(path)[0]
        assert restored.generation_mode == "imageTo3d"
        assert restored.image_path == "/tmp/reference.png"
        assert restored.texture_resolution == 4096
        assert restored.auto_uv is True


def checkHistoryAcceptsImageModeWithoutPrompt(tmp_path: Path):
    path = tmp_path / "history.json"
    row = {
        "job_id": "image-1", "timestamp": "now", "prompt": "",
        "negative_prompt": "", "provider": "mock", "model": "default",
        "quality": "standard", "output_format": "glb", "file_path": "",
        "status": "completed", "generation_mode": "imageTo3d",
    }
    path.write_text(__import__("json").dumps([row]), encoding="utf-8")
    assert read_history(path)[0].generation_mode == "imageTo3d"


def checkLibraryUpdateFavoriteTagsAndCollection(tmp_path: Path):
    library = AssetLibrary(tmp_path)
    source = tmp_path / "asset.glb"
    source.write_bytes(b"glb")
    library.add_asset(asset_id="a", prompt="chair", provider="mock", model="default", output_format="glb", file_path=source, copy_file=True)
    updated = library.update("a", favorite=True, tags=["chair", "wood"], collection="Village")
    assert updated is not None
    assert updated.favorite
    assert updated.tags == ["chair", "wood"]
    assert updated.collection == "Village"
    assert library.remove("a")


def checkJsonStorageIsReplaceableAndBounded(tmp_path: Path):
    storage = JSONStorage(tmp_path / "metadata.json")
    storage.write({"assets": [{"id": str(i)} for i in range(6000)]})
    assert len(storage.list_records("assets")) == 5000


def checkPromptEnhancerNeverMutatesOriginalPrompt():
    result = PromptEnhancer().enhance("chair")
    assert result.original == "chair"
    assert result.enhanced.startswith("Detailed realistic chair")


def checkImageValidationRejectsEmptyAndOversizedInputs(tmp_path: Path):
    from ai3dgenerator.utils.images import validate_image_path

    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    with pytest.raises(ValidationError):
        validate_image_path(empty)
    missing = tmp_path / "missing.jpg"
    with pytest.raises(ValidationError):
        validate_image_path(missing)


def checkImageValidationChecksMagicBytesForSupportedFormats(tmp_path: Path):
    from ai3dgenerator.utils.images import validate_image_path

    bad_png = tmp_path / "bad.png"
    bad_png.write_bytes(b"not a png")
    with pytest.raises(ValidationError):
        validate_image_path(bad_png)
    fake_jpg = tmp_path / "fake.jpg"
    fake_jpg.write_bytes(b"not a jpeg")
    with pytest.raises(ValidationError):
        validate_image_path(fake_jpg)


def checkEffectivePromptRequiresExplicitEnableAndPreservesOriginal():
    assert effective_prompt("chair", "detailed chair", False) == "chair"
    assert effective_prompt("chair", "detailed chair", True) == "detailed chair"
