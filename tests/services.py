"""Tests for provider/job/download behavior that do not require Blender."""
from __future__ import annotations

from pathlib import Path

import pytest

from ai3dgenerator.core.errors import DownloadError
from ai3dgenerator.core.capabilities import ProviderCapabilities
from ai3dgenerator.core.models import ProviderConfig
from ai3dgenerator.providers.customRest import CustomRESTProvider
from ai3dgenerator.providers.mock import MockProvider
from ai3dgenerator.services.downloadManager import DownloadManager, detect_format
from ai3dgenerator.services.jobManager import JobManager


class FakeHttpResponse:
    def __init__(self, body: bytes, content_type: str = ""):
        self.body = body
        self.headers = {"Content-Type": content_type} if content_type else {}


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


def checkMockJobManagerDownloadsFixture(tmp_path):
    provider = MockProvider()
    manager = DownloadManager(tmp_path, allowed_hosts=[])

    class FixtureDownload(DownloadManager):
        def download(self, url, job_id, requested_format="", provider=None):
            # The manager is replaced to make the fixture path observable while
            # the real format checkS remain covered separately.
            return super().download(url, job_id, requested_format, provider)

    job = JobManager(provider, FixtureDownload(tmp_path), job_timeout=30)
    finished = []
    job.on_finished = lambda result, snapshot: finished.append(result)
    job.start({"prompt": "chair", "output_format": "glb", "quality": "draft"})
    for _ in range(6):
        if job.snapshot.is_finished:
            break
        job.tick()
    assert job.snapshot.state == "completed"
    assert job.snapshot.output_path.endswith(".glb")
    assert finished and finished[0].status == "completed"
    assert Path(job.snapshot.output_path).is_file()


def checkMockProviderCancelIsObservable():
    provider = MockProvider()
    job = provider.create_generation_job({"prompt": "chair"})
    provider.cancel_job(job.job_id)
    assert provider.get_job_status(job.job_id).state == "cancelled"


def checkDownloadManagerRejectsUnknownHostWhenAllowlistIsSet(tmp_path):
    manager = DownloadManager(tmp_path, allowed_hosts=["cdn.example.test"])
    with pytest.raises(DownloadError):
        manager.download("https://evil.example/a.glb", "job-1", "glb")


def checkDownloadManagerRejectsCrossHostAuthenticatedDownload(tmp_path):
    class Client:
        def request(self, method, url, **kwargs):
            raise AssertionError("network must not be reached")

    provider = CustomRESTProvider(ProviderConfig(base_url="https://api.example.test"), client=Client())
    manager = DownloadManager(tmp_path, client=Client())
    with pytest.raises(DownloadError, match="Authenticated asset downloads"):
        manager.download("https://cdn.example.test/a.glb", "job-1", "glb", provider)


def checkCustomRestCapabilitiesAreExplicit() -> None:
    capabilities = CustomRESTProvider().capabilities()
    assert capabilities.supports_generation_mode("text_to_3d")
    assert not capabilities.supports_generation_mode("imageTo3d")
    assert capabilities.supported_formats == ("glb",)
    assert capabilities.supports_cancellation is True


def checkCustomRestCapabilityReflectsDisabledCancelEndpoint() -> None:
    capabilities = CustomRESTProvider(ProviderConfig(cancel_path="")).capabilities()
    assert capabilities.supports_cancellation is False
    assert capabilities.supported_formats_for_mode("imageTo3d") == ()


def checkCustomRestDisabledCancelIsNotAdvertised() -> None:
    capabilities = CustomRESTProvider(ProviderConfig(cancel_path="")).capabilities()
    assert capabilities.supports_cancellation is False
    with pytest.raises(NotImplementedError):
        CustomRESTProvider(ProviderConfig(cancel_path="")).cancel_job("job-1")


def checkCapabilityFlagsCannotDisagreeWithModes() -> None:
    with pytest.raises(ValueError):
        ProviderCapabilities(
            provider_id="broken",
            name="Broken",
            supported_generation_modes=("imageTo3d",),
            supports_imageTo3d=False,
        )

    class Client:
        def request_json(self, method, url, **kwargs):
            assert method == "POST"
            assert url == "https://example.test/generate"
            assert kwargs["json_body"]["prompt"] == "chair"
            return {"data": {"id": "abc-1"}}, object()

        def request(self, method, url, **kwargs):
            return FakeHttpResponse(b"", "application/json")

    provider = CustomRESTProvider(
        ProviderConfig(base_url="https://example.test", model="m", job_id_path="data.id"),
        client=Client(),
    )
    handle = provider.create_generation_job({"prompt": "chair"})
    assert handle.job_id == "abc-1"


def checkDetectFormatPrefersUrlExtension():
    assert detect_format("https://cdn.example.test/download", "glb") == "glb"


def checkJobManagerStartResetsPreviousJob(tmp_path):
    provider = MockProvider()
    manager = DownloadManager(tmp_path)
    job = JobManager(provider, manager, job_timeout=30)
    job.start({"prompt": "chair", "output_format": "glb", "quality": "draft"})
    for _ in range(6):
        if job.snapshot.is_finished:
            break
        job.tick()
    assert job.snapshot.state == "completed"
    assert job.handle is not None
    assert job.downloaded is not None
    # A reused instance must not inherit the finished job's handle/output.
    job.start({"prompt": "chair", "output_format": "glb", "quality": "draft"})
    assert job.handle is None
    assert job.downloaded is None
    assert job.snapshot.job_id == ""


def checkRaisingUiUpdateCallbackDoesNotFlipCompletedToFailed(tmp_path):
    provider = MockProvider()

    def on_update(snapshot):
        raise RuntimeError("ui update boom")

    manager = DownloadManager(tmp_path)
    job = JobManager(provider, manager, job_timeout=30, on_update=on_update)
    job.start({"prompt": "chair", "output_format": "glb", "quality": "draft"})
    for _ in range(6):
        if job.snapshot.is_finished:
            break
        job.tick()
    assert job.snapshot.state == "completed"


def checkRaisingFinishedUiCallbackDoesNotFlipCompletedToFailed(tmp_path):
    provider = MockProvider()

    def on_finished(result, snapshot):
        raise RuntimeError("ui finished boom")

    manager = DownloadManager(tmp_path)
    job = JobManager(provider, manager, job_timeout=30, on_finished=on_finished)
    job.start({"prompt": "chair", "output_format": "glb", "quality": "draft"})
    for _ in range(6):
        if job.snapshot.is_finished:
            break
        job.tick()
    assert job.snapshot.state == "completed"
